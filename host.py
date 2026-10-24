#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import json
import subprocess
import signal
import time
import html
import re
from datetime import datetime, timezone, timedelta
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    KeyboardButton,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

import warnings
warnings.filterwarnings("ignore", message="pkg_resources is deprecated")

# ==================== CONFIG ====================
TOKEN = "8896885648:AAFseDG0KW6aDh-IhTiuiuO-tqqgvSDCoaQ"
BASE_DIR = "users"
PENDING_DIR = "pending_uploads"
MAX_BOTS = 1
BOT_NAMES = ["bot.py"]
ADMIN_ID = 8927616504

MAX_FILE_SIZE = 10 * 1024 * 1024
REQUIRED_FILENAME = "bot.py"
MAX_DAILY_UPLOADS = 5

DAILY_UPLOAD_FILE = os.path.join(PENDING_DIR, "daily_uploads.json")

os.makedirs(BASE_DIR, exist_ok=True)
os.makedirs(PENDING_DIR, exist_ok=True)

PENDING_APPROVALS = {}
BANNED_USERS = set()
ADMIN_STATE = {}

# ==================== SECURITY ====================
def sanitize_filename(name: str) -> str:
    name = os.path.basename(name)
    name = re.sub(r'[^\w\.\-]', '_', name)
    if name.startswith('.'):
        name = 'file' + name
    return name[:100]

def is_safe_command(cmd: str) -> bool:
    cmd = cmd.strip()
    if not cmd:
        return False

    lower = cmd.lower()

    blocked_words = [
        'uninstall', 'remove', 'purge', 'delete', 'rmdir',
        'shutdown', 'reboot', 'poweroff', 'halt',
        'mkfs', 'kill', 'pkill', 'killall',
    ]
    for w in blocked_words:
        if re.search(r'\b' + re.escape(w) + r'\b', lower):
            return False

    if re.search(r'(^|\s)rm(\s|$)', lower):
        return False

    if re.search(r'(^|\s)dd(\s|$)', lower):
        return False

    if ';' in cmd or '\n' in cmd or '\r' in cmd or '`' in cmd:
        return False
    if '&&' in cmd or '||' in cmd:
        return False

    return True

def esc(text) -> str:
    return html.escape(str(text))

def is_process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False
    except Exception:
        return False

# ==================== IST TIME HELPERS ====================
IST_TZ = timezone(timedelta(hours=5, minutes=30))

def get_ist_time_str() -> str:
    try:
        return datetime.now(IST_TZ).strftime('%d-%m-%Y %H:%M:%S')
    except Exception:
        return datetime.now().strftime('%d-%m-%Y %H:%M:%S')

def get_ist_time_short() -> str:
    try:
        return datetime.now(IST_TZ).strftime('%H:%M:%S')
    except Exception:
        return datetime.now().strftime('%H:%M:%S')

# ==================== DAILY UPLOAD TRACKER ====================
def _load_daily_uploads() -> dict:
    try:
        if os.path.exists(DAILY_UPLOAD_FILE):
            with open(DAILY_UPLOAD_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}

def _save_daily_uploads(data: dict):
    try:
        with open(DAILY_UPLOAD_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:
        pass

def get_today_uploads(uid) -> int:
    data = _load_daily_uploads()
    today = datetime.now().strftime("%Y-%m-%d")
    entry = data.get(str(uid))
    if not entry or entry.get("date") != today:
        return 0
    return int(entry.get("count", 0))

def increment_daily_uploads(uid):
    data = _load_daily_uploads()
    today = datetime.now().strftime("%Y-%m-%d")
    entry = data.get(str(uid))
    if not entry or entry.get("date") != today:
        data[str(uid)] = {"date": today, "count": 1}
    else:
        data[str(uid)]["count"] = int(entry.get("count", 0)) + 1
    _save_daily_uploads(data)

# ==================== PENDING CHECK ====================
def has_pending_upload(uid) -> bool:
    for aid, info in PENDING_APPROVALS.items():
        if info.get("user_id") == uid:
            return True
    pending_user_dir = os.path.join(PENDING_DIR, str(uid))
    if os.path.exists(pending_user_dir):
        try:
            files = [f for f in os.listdir(pending_user_dir) if not f.startswith('.')]
            if files:
                return True
        except Exception:
            pass
    return False

# ==================== BOT STATUS ====================
def is_bot_running(uid, bot_name=None):
    user_dir = os.path.join(BASE_DIR, str(uid))
    if bot_name:
        pid_file = os.path.join(user_dir, f"{bot_name}.pid")
        if not os.path.exists(pid_file):
            return False
        try:
            with open(pid_file, "r") as f:
                pid_str = f.read().strip()
                if not pid_str:
                    os.remove(pid_file)
                    return False
                pid = int(pid_str)
            if is_process_alive(pid):
                return True
            else:
                try:
                    os.remove(pid_file)
                except:
                    pass
                return False
        except:
            try:
                os.remove(pid_file)
            except:
                pass
            return False
    else:
        for bot in BOT_NAMES:
            pid_file = os.path.join(user_dir, f"{bot}.pid")
            if os.path.exists(pid_file):
                try:
                    with open(pid_file, "r") as f:
                        pid_str = f.read().strip()
                        if not pid_str:
                            os.remove(pid_file)
                            continue
                        pid = int(pid_str)
                    if is_process_alive(pid):
                        return True, bot
                    else:
                        os.remove(pid_file)
                except:
                    if os.path.exists(pid_file):
                        try:
                            os.remove(pid_file)
                        except:
                            pass
        return False, None

# ==================== SEND OR EDIT HELPER ====================
async def send_or_edit(update: Update, context: ContextTypes.DEFAULT_TYPE,
                       text: str, reply_markup=None):
    query = getattr(update, "callback_query", None)

    if query:
        try:
            await query.edit_message_text(
                text=text, reply_markup=reply_markup, parse_mode="HTML"
            )
            return
        except Exception:
            pass
        try:
            await context.bot.send_message(
                chat_id=update.effective_user.id,
                text=text, reply_markup=reply_markup, parse_mode="HTML",
            )
            return
        except Exception:
            pass
    elif getattr(update, "message", None):
        try:
            await update.message.reply_text(
                text, reply_markup=reply_markup, parse_mode="HTML"
            )
            return
        except Exception:
            pass

    try:
        await context.bot.send_message(
            chat_id=update.effective_user.id,
            text=text, reply_markup=reply_markup, parse_mode="HTML",
        )
    except Exception:
        pass

# ==================== HELP TEXT ====================
HELP_TEXT = """
<b>🤖 ADVANCED HOSTING PANEL</b>

<b>📤 UPLOAD FILE:</b>
Send <code>bot.py</code> as document.
Admin approve karega → auto host.

<b>▶️ RUN BOT:</b>
Click "▶️ Run Bot" → requirements auto install

<b>⏸️ STOP BOT:</b>
Click "⏸️ Stop Bot" → force stop

<b>📦 MANUAL INSTALL:</b>
Click → run any install command
<code>pip install package</code>
<code>sudo apt install package</code>
<code>python3 script.py</code>

<b>📁 VIEW FILES:</b>
Click file → more options

<b>⚠️ VIEW ERRORS:</b>
Error log

<b>⚠️ RULES:</b>
- Only <b>bot.py</b> accepted
- Max 1 bot per user
- 3 uploads per day
- Admin approval zaroori
"""

# ==================== USER KEYBOARD ====================
KEYBOARD = ReplyKeyboardMarkup(
    [
        [KeyboardButton("📤 Upload File"),
         KeyboardButton("📁 View Files")],
        [KeyboardButton("▶️ Run Bot"),
         KeyboardButton("⏸️ Stop Bot")],
        [KeyboardButton("📦 Manual Install"),
         KeyboardButton("🗑️ Delete Files")],
        [KeyboardButton("⚠️ View Errors")],
    ],
    resize_keyboard=True,
    one_time_keyboard=False,
)

ALL_BUTTON_TEXTS = {
    "📤 Upload File", "📁 View Files",
    "▶️ Run Bot", "⏸️ Stop Bot",
    "📦 Manual Install",
    "🗑️ Delete Files",
    "⚠️ View Errors",
}

# ==================== ADMIN INLINE KEYBOARD ====================
def get_admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Status", callback_data="adm_status")],
        [InlineKeyboardButton("📢 Broadcast", callback_data="adm_broadcast")],
        [InlineKeyboardButton("👥 All Users", callback_data="adm_users"),
         InlineKeyboardButton("⏳ Pending", callback_data="adm_pending")],
        [InlineKeyboardButton("🚫 Ban User", callback_data="adm_ban"),
         InlineKeyboardButton("✅ Unban User", callback_data="adm_unban")],
        [InlineKeyboardButton("🔄 Refresh", callback_data="adm_refresh")],
    ])

def get_cancel_keyboard(action="adm_cancel"):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Cancel", callback_data=action)]
    ])

# ==================== START ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid in BANNED_USERS and uid != ADMIN_ID:
        await update.message.reply_text(
            "🚫 <b>You are banned!</b>\nContact admin.", parse_mode="HTML")
        return

    user = update.effective_user
    name = esc(user.first_name or "User")
    uploads_left = max(0, MAX_DAILY_UPLOADS - get_today_uploads(uid))
    welcome = (
        f"✨ <b>Welcome {name}!</b>\n\n"
        f"🆔 <b>Your ID:</b> <code>{uid}</code>\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🌟 <b>FEATURES:</b>\n"
        "• ⚡ Ultra Fast Bot Hosting\n"
        "• 🔐 Admin Approval System\n"
        "• 📦 Manual Package Install\n"
        "• 🎨 Premium Panel UI\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"⚠️ <b>Max Bots:</b> {MAX_BOTS} (<code>bot.py</code> only)\n"
        f"📤 <b>Uploads Left Today:</b> <code>{uploads_left}/{MAX_DAILY_UPLOADS}</code>"
    )
    if uid == ADMIN_ID:
        await update.message.reply_text(
            welcome + "\n\n👑 <b>Admin Panel:</b> Send /admin",
            reply_markup=KEYBOARD, parse_mode="HTML")
    else:
        await update.message.reply_text(welcome, reply_markup=KEYBOARD, parse_mode="HTML")

# ==================== HELP ====================
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT, reply_markup=KEYBOARD, parse_mode="HTML")

# ==================== FILE UPLOAD ====================
async def upload_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid in BANNED_USERS and uid != ADMIN_ID:
        await update.message.reply_text(
            "🚫 <b>You are banned!</b>\nContact admin.", parse_mode="HTML")
        return

    user = update.effective_user
    if not update.message.document:
        await update.message.reply_text(
            "📎 <b>Please send a file as document</b>",
            reply_markup=KEYBOARD, parse_mode="HTML")
        return

    doc = update.message.document
    fname = doc.file_name
    if not fname:
        await update.message.reply_text("❌ <b>Invalid file name</b>", parse_mode="HTML")
        return

    fname = sanitize_filename(fname)
    if fname.lower() != REQUIRED_FILENAME:
        await update.message.reply_text(
            f"❌ <b>Only <code>{REQUIRED_FILENAME}</code> accepted!</b>\n\n"
            f"Aapne bheja: <code>{esc(fname)}</code>\n"
            f"Apni file ka naam badal kar <code>{REQUIRED_FILENAME}</code> rakhein.",
            parse_mode="HTML")
        return

    if doc.file_size and doc.file_size > MAX_FILE_SIZE:
        await update.message.reply_text(
            f"❌ <b>File too large!</b> Max {MAX_FILE_SIZE // (1024*1024)} MB",
            parse_mode="HTML")
        return

    running, running_bot = is_bot_running(uid)
    if running:
        await update.message.reply_text(
            f"❌ <b>Bot {esc(running_bot)} is running!</b>\nStop it first.",
            reply_markup=KEYBOARD, parse_mode="HTML")
        return

    if has_pending_upload(uid):
        await update.message.reply_text(
            "⚠️ <b>You already have a pending upload!</b>\n\n"
            "Admin ke approval ka wait karein.\n"
            "Ek time par ek hi upload allowed hai.",
            reply_markup=KEYBOARD, parse_mode="HTML")
        return

    today_uploads = get_today_uploads(uid)
    if today_uploads >= MAX_DAILY_UPLOADS:
        await update.message.reply_text(
            f"🚫 <b>Daily upload limit reached!</b>\n\n"
            f"📤 Aaj ke uploads: <code>{today_uploads}/{MAX_DAILY_UPLOADS}</code>\n"
            "Kal phir try karein.",
            reply_markup=KEYBOARD, parse_mode="HTML")
        return

    pending_user_dir = os.path.join(PENDING_DIR, str(uid))
    os.makedirs(pending_user_dir, exist_ok=True)
    pending_path = os.path.join(pending_user_dir, fname)

    try:
        tg_file = await doc.get_file()
        await tg_file.download_to_drive(pending_path)
        increment_daily_uploads(uid)

        approval_id = f"{uid}_{int(time.time())}"
        admin_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ APPROVE", callback_data=f"approve_{approval_id}"),
             InlineKeyboardButton("❌ REJECT", callback_data=f"reject_{approval_id}")]
        ])

        admin_msg = (
            "🔔 <b>NEW UPLOAD REQUEST</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 <b>User:</b> {esc(user.first_name)} (@{esc(user.username or 'N/A')})\n"
            f"🆔 <b>User ID:</b> <code>{uid}</code>\n"
            f"📄 <b>File:</b> <code>{esc(fname)}</code>\n"
            f"📦 <b>Size:</b> {doc.file_size} bytes\n"
            f"🕐 <b>Time:</b> {get_ist_time_str()}\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Approve karo → user ka bot auto-host ho jayega."
        )

        try:
            admin_msg_obj = await context.bot.send_message(
                chat_id=ADMIN_ID, text=admin_msg,
                reply_markup=admin_keyboard, parse_mode="HTML")

            PENDING_APPROVALS[approval_id] = {
                "user_id": uid, "file_name": fname,
                "pending_path": pending_path,
                "message_id": admin_msg_obj.message_id,
                "timestamp": time.time(),
                "user_name": user.first_name, "username": user.username,
            }

            try:
                with open(pending_path, "rb") as fh:
                    await context.bot.send_document(
                        chat_id=ADMIN_ID, document=fh,
                        caption=f"📄 From <code>{uid}</code> — <code>{esc(fname)}</code>",
                        parse_mode="HTML")
            except:
                pass

            uploads_left = max(0, MAX_DAILY_UPLOADS - get_today_uploads(uid))
            await update.message.reply_text(
                "✅ <b>File uploaded successfully!</b>\n\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"📄 <b>File:</b> <code>{esc(fname)}</code>\n"
                f"📦 <b>Size:</b> {doc.file_size} bytes\n"
                f"📤 <b>Uploads Left Today:</b> <code>{uploads_left}/{MAX_DAILY_UPLOADS}</code>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "⏳ <b>Status:</b> Pending Admin Approval\n\n"
                "👑 Admin approve karega to aapka bot automatically host ho jayega.",
                reply_markup=KEYBOARD, parse_mode="HTML")
        except Exception as e:
            await update.message.reply_text(
                f"❌ <b>Failed to notify admin:</b> {esc(str(e))}", parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ <b>Upload failed:</b> {esc(str(e))}", parse_mode="HTML")

# ==================== APPROVE / REJECT ====================
async def handle_approval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if update.effective_user.id != ADMIN_ID:
        await query.answer("❌ Not authorized", show_alert=True)
        return

    data = query.data
    action, approval_id = data.split("_", 1)
    info = PENDING_APPROVALS.get(approval_id)
    if not info:
        await query.edit_message_text("⚠️ <b>Request expired or already handled.</b>", parse_mode="HTML")
        return

    uid = info["user_id"]
    fname = info["file_name"]
    pending_path = info["pending_path"]

    if action == "approve":
        user_dir = os.path.join(BASE_DIR, str(uid))
        os.makedirs(user_dir, exist_ok=True)
        final_path = os.path.join(user_dir, fname)
        try:
            if os.path.exists(final_path):
                os.remove(final_path)
            os.rename(pending_path, final_path)

            pending_user_dir = os.path.join(PENDING_DIR, str(uid))
            if os.path.exists(pending_user_dir):
                for f in os.listdir(pending_user_dir):
                    try:
                        os.remove(os.path.join(pending_user_dir, f))
                    except:
                        pass

            PENDING_APPROVALS.pop(approval_id, None)

            await query.edit_message_text(
                f"✅ <b>APPROVED</b>\n\n"
                f"👤 User: <code>{uid}</code>\n"
                f"📄 File: <code>{esc(fname)}</code>\n\n"
                f"User ko notification bhej di gayi hai.",
                parse_mode="HTML")

            try:
                user_kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("▶️ Run Bot Now", callback_data=f"run_{fname}")]
                ])
                await context.bot.send_message(
                    chat_id=uid,
                    text=("🎉 <b>FILE APPROVED!</b>\n\n"
                          "━━━━━━━━━━━━━━━━━━━━\n"
                          f"📄 <b>File:</b> <code>{esc(fname)}</code>\n"
                          "✅ <b>Status:</b> Approved by Admin\n"
                          "━━━━━━━━━━━━━━━━━━━━\n\n"
                          "Ab aap ▶️ Run Bot kar sakte hain!"),
                    reply_markup=user_kb, parse_mode="HTML")
            except:
                pass
        except Exception as e:
            await query.edit_message_text(f"❌ Approve failed: {esc(str(e))}", parse_mode="HTML")

    elif action == "reject":
        try:
            if os.path.exists(pending_path):
                os.remove(pending_path)
        except:
            pass
        PENDING_APPROVALS.pop(approval_id, None)
        await query.edit_message_text(
            f"❌ <b>REJECTED</b>\n\n"
            f"👤 User: <code>{uid}</code>\n"
            f"📄 File: <code>{esc(fname)}</code>",
            parse_mode="HTML")
        try:
            await context.bot.send_message(
                chat_id=uid,
                text=("❌ <b>FILE REJECTED</b>\n\n"
                      "━━━━━━━━━━━━━━━━━━━━\n"
                      f"📄 <b>File:</b> <code>{esc(fname)}</code>\n"
                      "━━━━━━━━━━━━━━━━━━━━\n\n"
                      "Aapka file admin ne reject kar diya.\n"
                      "Contact admin for details."),
                parse_mode="HTML")
        except:
            pass

# ==================== VIEW FILES ====================
async def view_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user_dir = os.path.join(BASE_DIR, str(uid))

    if not os.path.exists(user_dir):
        await update.message.reply_text("📁 <b>No files found</b>", reply_markup=KEYBOARD, parse_mode="HTML")
        return
    try:
        files = os.listdir(user_dir)
    except:
        await update.message.reply_text("❌ <b>Error accessing directory</b>", reply_markup=KEYBOARD, parse_mode="HTML")
        return
    if not files:
        await update.message.reply_text("📁 <b>Folder is empty</b>", reply_markup=KEYBOARD, parse_mode="HTML")
        return

    keyboard = []
    running, running_bot = is_bot_running(uid)
    if running:
        keyboard.append([InlineKeyboardButton(f"🟢 RUNNING: {running_bot}", callback_data="status_info")])
    else:
        keyboard.append([InlineKeyboardButton("🔴 NO BOT RUNNING", callback_data="status_info")])

    bot_count = 0
    for file in sorted(files):
        try:
            file_path = os.path.join(user_dir, file)
            if os.path.exists(file_path):
                file_size = os.path.getsize(file_path)
                size_kb = file_size / 1024
                if file in BOT_NAMES:
                    bot_count += 1
                    file_emoji = "🟢" if (running and file == running_bot) else "🤖"
                elif file == "requirements.txt":
                    file_emoji = "📦"
                elif file == "error.log":
                    file_emoji = "⚠️"
                elif file == "output.log":
                    file_emoji = "📋"
                else:
                    file_emoji = "📄"
                keyboard.append([InlineKeyboardButton(
                    f"{file_emoji} {file} ({size_kb:.1f} KB)",
                    callback_data=f"file_{file}")])
        except:
            continue

    keyboard.extend([
        [InlineKeyboardButton("🔄 Refresh", callback_data="refresh_files")],
        [InlineKeyboardButton("🗑️ Delete Selected", callback_data="show_delete_menu")],
    ])

    await update.message.reply_text(
        f"📁 <b>Your Files</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 User ID: <code>{uid}</code>\n"
        f"📊 Total Files: <code>{len(files)}</code>\n"
        f"🤖 Bots: <code>{bot_count}/{MAX_BOTS}</code>\n"
        f"<b>Status:</b> {'🟢 Running' if running else '🔴 Stopped'}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Click file names for options ↓",
        reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

# ==================== MANUAL INSTALL ====================
async def manual_install_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid in BANNED_USERS and uid != ADMIN_ID:
        await update.message.reply_text("🚫 <b>You are banned!</b>\nContact admin.", parse_mode="HTML")
        return
    running, running_bot = is_bot_running(uid)
    if running:
        await update.message.reply_text(
            f"❌ <b>Bot {esc(running_bot)} is running!</b>\nStop it first.",
            reply_markup=KEYBOARD, parse_mode="HTML")
        return
    context.user_data["awaiting_install"] = True
    await update.message.reply_text(
        "📦 <b>Manual Command Execution</b>\n\n"
        "<b>✅ Allowed (almost anything):</b>\n"
        "• <code>pip install package_name</code>\n"
        "• <code>pip3 install package1 package2</code>\n"
        "• <code>python3 -m pip install package_name</code>\n"
        "• <code>python3 script.py</code>\n"
        "• <code>python3 script.py arg1 arg2</code>\n"
        "• <code>sudo apt install package_name</code>\n"
        "• <code>sudo apt update</code>\n"
        "• <code>sudo apt upgrade</code>\n"
        "• <code>sudo pip install package_name</code>\n"
        "• <code>apt install package_name</code>\n\n"
        "❌ <b>Blocked (destructive):</b>\n"
        "• <code>uninstall</code>, <code>remove</code>, <code>purge</code>\n"
        "• <code>delete</code>, <code>rm</code>, <code>rmdir</code>\n"
        "• <code>shutdown</code>, <code>reboot</code>, <code>kill</code>\n"
        "• Command chaining (<code>; && ||</code>)\n\n"
        "❌ Send /cancel to abort\n"
        "💡 Koi bhi button click karne par auto-cancel ho jayega",
        parse_mode="HTML")

async def handle_manual_install(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user_dir = os.path.join(BASE_DIR, str(uid))
    os.makedirs(user_dir, exist_ok=True)

    if not context.user_data.get("awaiting_install"):
        return False

    command = update.message.text.strip()
    if command.lower() == "/cancel":
        context.user_data["awaiting_install"] = False
        await update.message.reply_text("❌ Installation cancelled", reply_markup=KEYBOARD)
        return True

    if not is_safe_command(command):
        await update.message.reply_text(
            "❌ <b>Unsafe or destructive command blocked!</b>\n\n"
            "✅ <b>Allowed:</b>\n"
            "<code>pip install package</code>\n"
            "<code>sudo apt install package</code>\n"
            "<code>python3 script.py</code>\n"
            "<code>sudo pip install package</code>\n\n"
            "❌ <b>Blocked:</b>\n"
            "uninstall, remove, purge, delete, rm, kill, reboot,\n"
            "shell chaining (<code>; && ||</code>)",
            parse_mode="HTML")
        return True

    await update.message.reply_text(
        f"⚙️ Executing: <code>{esc(command)}</code>\nPlease wait...", parse_mode="HTML")
    try:
        parts = command.split()
        result = subprocess.run(parts, cwd=user_dir, capture_output=True, text=True, timeout=300, shell=False)
        log_path = os.path.join(user_dir, "install.log")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n{'='*60}\nCommand: {command}\nReturn: {result.returncode}\n")
            f.write(f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}\n{'='*60}\n")
        if result.returncode == 0:
            out = result.stdout.strip() or "✅ Command executed successfully (no output)"
            await update.message.reply_text(
                f"✅ <b>Success!</b>\n\n<pre>{esc(out[:1500])}</pre>",
                parse_mode="HTML")
        else:
            err = result.stderr or result.stdout or "Unknown error"
            await update.message.reply_text(
                f"❌ <b>Failed!</b>\n\n<pre>{esc(err[:1500])}</pre>",
                parse_mode="HTML")
    except subprocess.TimeoutExpired:
        await update.message.reply_text("⏱️ Command timeout!")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {esc(str(e))}")
    context.user_data["awaiting_install"] = False
    return True

# ==================== DELETE MENU ====================
async def show_delete_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
    uid = update.effective_user.id
    user_dir = os.path.join(BASE_DIR, str(uid))

    running, running_bot = is_bot_running(uid)
    if running:
        text = f"❌ <b>Cannot delete while {esc(running_bot)} is running!</b>\nStop first."
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="refresh_files")]])
        if query:
            await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
        else:
            await update.message.reply_text(text, reply_markup=kb, parse_mode="HTML")
        return

    try:
        files = os.listdir(user_dir) if os.path.exists(user_dir) else []
        if not files:
            text = "📁 No files to delete"
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="refresh_files")]])
            if query:
                await query.edit_message_text(text, reply_markup=kb)
            else:
                await update.message.reply_text(text, reply_markup=kb)
            return

        keyboard = []
        for file in sorted(files):
            if os.path.isfile(os.path.join(user_dir, file)):
                keyboard.append([InlineKeyboardButton(f"🗑️ {file}", callback_data=f"delete_select_{file}")])
        keyboard.append([InlineKeyboardButton("🔙 Cancel", callback_data="refresh_files")])

        text = "🗑️ <b>Select file to delete:</b>"
        if query:
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        else:
            await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    except Exception as e:
        if query:
            await query.edit_message_text(f"❌ Error: {esc(str(e))}", parse_mode="HTML")

# ==================== ADMIN PANEL ====================
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid != ADMIN_ID:
        await update.message.reply_text("❌ Not authorized!", parse_mode="HTML")
        return
    ADMIN_STATE.pop(uid, None)
    await update.message.reply_text(
        "👑 <b>ADMIN PANEL</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Choose an option below 👇\n\n"
        "💡 Use <code>/cancel</code> to abort any action",
        reply_markup=get_admin_keyboard(), parse_mode="HTML")

async def admin_panel_refresh(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = update.effective_user.id
    if uid != ADMIN_ID:
        await query.answer("❌ Not authorized", show_alert=True)
        return
    ADMIN_STATE.pop(uid, None)
    try:
        await query.edit_message_text(
            "👑 <b>ADMIN PANEL</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Choose an option below 👇\n\n"
            "💡 Use <code>/cancel</code> to abort any action",
            reply_markup=get_admin_keyboard(), parse_mode="HTML")
    except:
        pass

# ==================== ADMIN STATUS ====================
async def admin_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid != ADMIN_ID:
        return

    total_users = 0
    total_bots_running = 0
    total_bots_stopped = 0
    total_files = 0

    try:
        if os.path.exists(BASE_DIR):
            for user_id in os.listdir(BASE_DIR):
                user_dir = os.path.join(BASE_DIR, user_id)
                if os.path.isdir(user_dir):
                    total_users += 1
                    files = os.listdir(user_dir)
                    total_files += len(files)
                    running, running_bot = is_bot_running(user_id)
                    if running:
                        total_bots_running += 1
                    for bot in BOT_NAMES:
                        if os.path.exists(os.path.join(user_dir, bot)):
                            if not (running and bot == running_bot):
                                total_bots_stopped += 1

        pending_count = len(PENDING_APPROVALS)
        daily_data = _load_daily_uploads()
        today = datetime.now().strftime("%Y-%m-%d")
        today_upload_total = sum(
            int(v.get("count", 0)) for v in daily_data.values() if v.get("date") == today
        )

        msg = (
            "📊 <b>SYSTEM STATUS</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"👥 <b>Total Users:</b> <code>{total_users}</code>\n"
            f"🟢 <b>Running Bots:</b> <code>{total_bots_running}</code>\n"
            f"🔴 <b>Stopped Bots:</b> <code>{total_bots_stopped}</code>\n"
            f"📄 <b>Total Files:</b> <code>{total_files}</code>\n"
            f"⏳ <b>Pending Approvals:</b> <code>{pending_count}</code>\n"
            f"🚫 <b>Banned Users:</b> <code>{len(BANNED_USERS)}</code>\n"
            f"📤 <b>Uploads Today:</b> <code>{today_upload_total}</code>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"📁 <b>Storage:</b> <code>{BASE_DIR}/</code>\n"
            f"📦 <b>Max Bots/User:</b> <code>{MAX_BOTS}</code>\n"
            f"📤 <b>Daily Upload Limit:</b> <code>{MAX_DAILY_UPLOADS}</code>\n"
            f"🕐 <b>Server Time (IST):</b> <code>{get_ist_time_str()}</code>"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Refresh", callback_data="adm_status")],
            [InlineKeyboardButton("🔙 Back", callback_data="adm_refresh")],
        ])
        await send_or_edit(update, context, msg, kb)
    except Exception as e:
        await send_or_edit(update, context, f"❌ Error: {esc(str(e))}")

# ==================== ADMIN ALL USERS ====================
async def admin_all_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid != ADMIN_ID:
        return

    if not os.path.exists(BASE_DIR):
        await send_or_edit(update, context, "📭 No users found.")
        return

    users = []
    for user_id in sorted(os.listdir(BASE_DIR), key=lambda x: int(x) if x.isdigit() else 0):
        user_dir = os.path.join(BASE_DIR, user_id)
        if os.path.isdir(user_dir):
            files = os.listdir(user_dir)
            running, _ = is_bot_running(user_id)
            status = "🟢" if running else "🔴"
            ban = "🚫" if user_id.isdigit() and int(user_id) in BANNED_USERS else ""
            users.append(f"{status}{ban} <code>{user_id}</code> — {len(files)} files")

    text = f"👥 <b>ALL USERS ({len(users)})</b>\n━━━━━━━━━━━━━━━━━━━━\n"
    text += "\n".join(users[:50]) if users else "No users yet."
    if len(users) > 50:
        text += f"\n\n... +{len(users)-50} more"

    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="adm_refresh")]])
    await send_or_edit(update, context, text, kb)

# ==================== ADMIN PENDING ====================
async def admin_pending_approvals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid != ADMIN_ID:
        return

    if not PENDING_APPROVALS:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="adm_refresh")]])
        await send_or_edit(update, context, "✅ No pending approvals.", kb)
        return

    text = "⏳ <b>Pending Approvals:</b>\n━━━━━━━━━━━━━━━━━━━━\n"
    for aid, info in PENDING_APPROVALS.items():
        try:
            t = datetime.fromtimestamp(info['timestamp'], IST_TZ).strftime('%H:%M:%S')
        except Exception:
            t = "N/A"
        text += (
            f"• User: <code>{info['user_id']}</code>\n"
            f"  File: <code>{esc(info['file_name'])}</code>\n"
            f"  Time: {t}\n\n"
        )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="adm_refresh")]])
    await send_or_edit(update, context, text, kb)

# ==================== ADMIN MULTI-STEP ====================
async def admin_start_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = update.effective_user.id
    if uid != ADMIN_ID:
        return
    ADMIN_STATE[uid] = "broadcast"
    await query.edit_message_text(
        "📢 <b>BROADCAST MODE</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "Apna message bhejein jo sabhi users ko bhejna hai.\n\n"
        "⚠️ HTML formatting supported.",
        reply_markup=get_cancel_keyboard("adm_cancel"), parse_mode="HTML")

async def admin_start_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = update.effective_user.id
    if uid != ADMIN_ID:
        return
    ADMIN_STATE[uid] = "ban_user"
    await query.edit_message_text(
        "🚫 <b>BAN USER</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "User ID bhejein jo ban karna hai:",
        reply_markup=get_cancel_keyboard("adm_cancel"), parse_mode="HTML")

async def admin_start_unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = update.effective_user.id
    if uid != ADMIN_ID:
        return
    if not BANNED_USERS:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="adm_refresh")]])
        await query.edit_message_text("✅ No banned users.", reply_markup=kb, parse_mode="HTML")
        return
    text = "✅ <b>UNBAN USER</b>\n━━━━━━━━━━━━━━━━━━━━\n\nBanned Users:\n"
    for b in BANNED_USERS:
        text += f"• <code>{b}</code>\n"
    text += "\nUser ID bhejein jo unban karna hai:"
    ADMIN_STATE[uid] = "unban_user"
    await query.edit_message_text(text, reply_markup=get_cancel_keyboard("adm_cancel"), parse_mode="HTML")

async def admin_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("❌ Cancelled")
    uid = update.effective_user.id
    ADMIN_STATE.pop(uid, None)
    try:
        await query.edit_message_text(
            "👑 <b>ADMIN PANEL</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Action cancelled.\n\n"
            "Choose an option below 👇",
            reply_markup=get_admin_keyboard(), parse_mode="HTML")
    except:
        pass

# ==================== ADMIN STATE HANDLER ====================
async def handle_admin_state(update: Update, context: ContextTypes.DEFAULT_TYPE, state: str) -> bool:
    uid = update.effective_user.id
    text = update.message.text.strip()

    if text.lower() == "/cancel":
        ADMIN_STATE.pop(uid, None)
        await update.message.reply_text(
            "❌ <b>Cancelled</b>", reply_markup=get_admin_keyboard(), parse_mode="HTML")
        return True

    if state == "broadcast":
        ADMIN_STATE.pop(uid, None)
        sent = failed = 0
        if os.path.exists(BASE_DIR):
            for user_dir in os.listdir(BASE_DIR):
                if os.path.isdir(os.path.join(BASE_DIR, user_dir)):
                    try:
                        await context.bot.send_message(
                            chat_id=int(user_dir),
                            text=f"📢 <b>ANNOUNCEMENT</b>\n\n{esc(text)}",
                            parse_mode="HTML")
                        sent += 1
                    except:
                        failed += 1
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Admin Panel", callback_data="adm_refresh")]])
        await update.message.reply_text(
            f"✅ <b>Broadcast Complete!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ Sent: <code>{sent}</code>\n"
            f"❌ Failed: <code>{failed}</code>",
            reply_markup=kb, parse_mode="HTML")
        return True

    if state == "ban_user":
        ADMIN_STATE.pop(uid, None)
        if not text.isdigit():
            await update.message.reply_text("❌ Invalid user ID!", reply_markup=get_admin_keyboard(), parse_mode="HTML")
            return True
        target = int(text)
        if target == ADMIN_ID:
            await update.message.reply_text("❌ Cannot ban admin!", parse_mode="HTML")
            return True
        BANNED_USERS.add(target)
        await update.message.reply_text(
            f"🚫 User <code>{target}</code> banned!",
            reply_markup=get_admin_keyboard(), parse_mode="HTML")
        try:
            await context.bot.send_message(
                chat_id=target,
                text="🚫 <b>You have been banned by admin!</b>\nContact admin for details.",
                parse_mode="HTML")
        except:
            pass
        return True

    if state == "unban_user":
        ADMIN_STATE.pop(uid, None)
        if not text.isdigit():
            await update.message.reply_text("❌ Invalid user ID!", reply_markup=get_admin_keyboard(), parse_mode="HTML")
            return True
        target = int(text)
        if target in BANNED_USERS:
            BANNED_USERS.discard(target)
            await update.message.reply_text(
                f"✅ User <code>{target}</code> unbanned!",
                reply_markup=get_admin_keyboard(), parse_mode="HTML")
            try:
                await context.bot.send_message(
                    chat_id=target,
                    text="✅ <b>You have been unbanned!</b>\nYou can use the bot again.",
                    parse_mode="HTML")
            except:
                pass
        else:
            await update.message.reply_text(
                f"❌ User <code>{target}</code> is not banned.",
                reply_markup=get_admin_keyboard(), parse_mode="HTML")
        return True
    return False

# ==================== ADMIN CALLBACKS ====================
async def handle_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = update.effective_user.id

    if uid != ADMIN_ID:
        await query.answer("❌ Not authorized", show_alert=True)
        return

    data = query.data

    try:
        await query.answer()
    except:
        pass

    if data == "adm_refresh":
        await admin_panel_refresh(update, context)
        return
    if data == "adm_cancel":
        await admin_cancel(update, context)
        return
    if data == "adm_status":
        await admin_status(update, context)
        return
    if data == "adm_broadcast":
        await admin_start_broadcast(update, context)
        return
    if data == "adm_users":
        await admin_all_users(update, context)
        return
    if data == "adm_pending":
        await admin_pending_approvals(update, context)
        return
    if data == "adm_ban":
        await admin_start_ban(update, context)
        return
    if data == "adm_unban":
        await admin_start_unban(update, context)
        return

# ==================== CALLBACK ROUTER ====================
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data

    if data.startswith("approve_") or data.startswith("reject_"):
        await handle_approval(update, context)
        return
    if data.startswith("adm_"):
        await handle_admin_callback(update, context)
        return

    await query.answer()
    uid = update.effective_user.id
    user_dir = os.path.join(BASE_DIR, str(uid))

    if data == "status_info":
        running, running_bot = is_bot_running(uid)
        status = f"🟢 Running: {running_bot}" if running else "🔴 No bot running"
        await query.edit_message_text(
            f"🤖 <b>Bot Status</b>\n\n{status}\nUser ID: <code>{uid}</code>",
            parse_mode="HTML")
        return
    elif data == "refresh_files":
        class FakeUpdate:
            def __init__(self, q):
                self.message = q.message
                self.effective_user = q.from_user
                self.callback_query = None
        await view_files(FakeUpdate(query), context)
        return
    elif data == "show_delete_menu":
        await show_delete_menu(update, context)
        return
    elif data == "cancel":
        await query.edit_message_text("❌ Cancelled", parse_mode="HTML")
        return
    elif data.startswith("delete_select_"):
        file_name = data[len("delete_select_"):]
        file_path = os.path.join(user_dir, file_name)
        running, running_bot = is_bot_running(uid)
        if running and file_name == running_bot:
            await query.edit_message_text(
                f"❌ Cannot delete <code>{esc(file_name)}</code> while running!",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Back", callback_data="show_delete_menu")]
                ]))
            return
        try:
            os.remove(file_path)
            await query.edit_message_text(
                f"✅ <code>{esc(file_name)}</code> deleted!",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🗑️ Delete Another", callback_data="show_delete_menu")],
                    [InlineKeyboardButton("🔙 Back", callback_data="refresh_files")],
                ]))
        except Exception as e:
            await query.edit_message_text(f"❌ Error: {esc(str(e))}", parse_mode="HTML")
        return
    elif data.startswith("file_"):
        file_name = data[len("file_"):]
        file_path = os.path.join(user_dir, file_name)
        if not os.path.exists(file_path):
            await query.edit_message_text("❌ File not found!", parse_mode="HTML")
            return
        file_size = os.path.getsize(file_path)
        keyboard = []
        if file_name in BOT_NAMES:
            running, running_bot = is_bot_running(uid)
            if running:
                if file_name == running_bot:
                    keyboard.append([InlineKeyboardButton("⏸️ Stop This Bot", callback_data=f"stop_{file_name}")])
                else:
                    keyboard.append([InlineKeyboardButton(f"⚠️ {running_bot} running", callback_data="status_info")])
            else:
                keyboard.append([InlineKeyboardButton("▶️ Run This Bot", callback_data=f"run_{file_name}")])
        vd = []
        if file_size < 50000:
            vd.append(InlineKeyboardButton("📄 View", callback_data=f"view_{file_name}"))
        vd.append(InlineKeyboardButton("🗑️ Delete", callback_data=f"delete_select_{file_name}"))
        keyboard.append(vd)
        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="refresh_files")])
        await query.edit_message_text(
            f"📄 <b>File: {esc(file_name)}</b>\n\n"
            f"Size: {file_size} bytes ({file_size/1024:.1f} KB)\n"
            f"Path: <code>{esc(file_path)}</code>",
            reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        return
    elif data.startswith("run_"):
        bot_name = data[len("run_"):]
        await run_specific_bot(update, context, bot_name)
        return
    elif data.startswith("stop_"):
        bot_name = data[len("stop_"):]
        await stop_specific_bot(update, context, bot_name)
        return
    elif data.startswith("view_"):
        file_name = data[len("view_"):]
        file_path = os.path.join(user_dir, file_name)
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read(2000)
            await query.edit_message_text(
                f"📄 <b>{esc(file_name)}</b>\n\n<pre>{esc(content)}</pre>",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Back", callback_data=f"file_{file_name}")]
                ]))
        except:
            await query.edit_message_text(
                f"Cannot preview {esc(file_name)}",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Back", callback_data=f"file_{file_name}")]
                ]))
        return

# ==================== RUN BOT ====================
async def run_specific_bot(update: Update, context: ContextTypes.DEFAULT_TYPE, bot_name):
    query = update.callback_query
    uid = update.effective_user.id
    user_dir = os.path.join(BASE_DIR, str(uid))
    bot_path = os.path.join(user_dir, bot_name)

    if not os.path.exists(bot_path):
        await query.edit_message_text(f"❌ <b>{esc(bot_name)} not found!</b>", parse_mode="HTML")
        return

    running, running_bot = is_bot_running(uid)
    if running:
        await query.edit_message_text(
            f"⚠️ <b>{esc(running_bot)} already running!</b>\nStop it first.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back", callback_data=f"file_{bot_name}")]
            ]))
        return

    req = os.path.join(user_dir, "requirements.txt")
    if os.path.exists(req):
        await query.edit_message_text("📦 Installing requirements...", parse_mode="HTML")
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "-r", "requirements.txt"],
                cwd=user_dir, capture_output=True, text=True, timeout=300)
            if result.returncode != 0:
                with open(os.path.join(user_dir, "error.log"), "a", encoding="utf-8") as f:
                    f.write(f"=== PIP ERROR ===\n{result.stderr}\n")
        except:
            pass

    log_path = os.path.join(user_dir, "output.log")
    error_log = os.path.join(user_dir, "error.log")
    try:
        with open(log_path, "a", encoding="utf-8") as out:
            out.write(f"\n{'='*60}\n")
            out.write(f"[{get_ist_time_str()}] Starting {bot_name}\n")
            out.write(f"{'='*60}\n\n")

        out = open(log_path, "a", encoding="utf-8")
        err = open(error_log, "a", encoding="utf-8")
        process = subprocess.Popen(
            [sys.executable, bot_name], cwd=user_dir,
            stdout=out, stderr=err, start_new_session=True)

        with open(os.path.join(user_dir, f"{bot_name}.pid"), "w") as f:
            f.write(str(process.pid))
        with open(os.path.join(user_dir, f"{bot_name}.start"), "w") as f:
            f.write(str(time.time()))

        try:
            out.close()
            err.close()
        except:
            pass

        await query.edit_message_text(
            f"✅ <b>{esc(bot_name)} started!</b>\n\n"
            f"📁 Directory: <code>{esc(user_dir)}</code>\n"
            f"⚙️ PID: <code>{process.pid}</code>",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back", callback_data="refresh_files")],
            ]), parse_mode="HTML")
    except Exception as e:
        await query.edit_message_text(f"❌ <b>Failed to start:</b> {esc(str(e))}", parse_mode="HTML")

# ==================== STOP BOT ====================
async def stop_specific_bot(update: Update, context: ContextTypes.DEFAULT_TYPE, bot_name):
    query = update.callback_query
    uid = update.effective_user.id
    user_dir = os.path.join(BASE_DIR, str(uid))
    pid_file = os.path.join(user_dir, f"{bot_name}.pid")
    start_file = os.path.join(user_dir, f"{bot_name}.start")

    if not os.path.exists(pid_file):
        await query.edit_message_text(f"❌ <b>{esc(bot_name)} not running!</b>", parse_mode="HTML")
        return
    try:
        with open(pid_file, "r") as f:
            pid = int(f.read().strip())
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except:
            try:
                os.kill(pid, signal.SIGKILL)
            except:
                pass
        if os.path.exists(pid_file):
            os.remove(pid_file)
        if os.path.exists(start_file):
            try:
                os.remove(start_file)
            except:
                pass
        with open(os.path.join(user_dir, "output.log"), "a", encoding="utf-8") as f:
            f.write(f"\n[{get_ist_time_str()}] {bot_name} stopped manually\n")
        await query.edit_message_text(
            f"⏸️ <b>{esc(bot_name)} stopped!</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back", callback_data="refresh_files")]
            ]))
    except Exception as e:
        await query.edit_message_text(f"⚠️ Error: {esc(str(e))}", parse_mode="HTML")

# ==================== RUN / STOP BUTTONS ====================
async def run_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user_dir = os.path.join(BASE_DIR, str(uid))
    running, running_bot = is_bot_running(uid)
    if running:
        await update.message.reply_text(
            f"⚠️ <b>{esc(running_bot)} already running!</b>\nUse ⏸️ Stop Bot first.",
            reply_markup=KEYBOARD, parse_mode="HTML")
        return
    available_bots = [b for b in BOT_NAMES if os.path.exists(os.path.join(user_dir, b))]
    if not available_bots:
        await update.message.reply_text(
            "❌ <b>No bot file found!</b>\nUpload bot.py first (admin approval ke baad).",
            reply_markup=KEYBOARD, parse_mode="HTML")
        return
    keyboard = [[InlineKeyboardButton(f"▶️ Run {b}", callback_data=f"run_{b}")] for b in available_bots]
    keyboard.append([InlineKeyboardButton("🔙 Cancel", callback_data="cancel")])
    await update.message.reply_text(
        "🤖 <b>Select bot to run:</b>",
        reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

async def stop_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    running, running_bot = is_bot_running(uid)
    if not running:
        await update.message.reply_text("❌ <b>No bot is running!</b>", reply_markup=KEYBOARD, parse_mode="HTML")
        return
    keyboard = [
        [InlineKeyboardButton(f"⏸️ Stop {running_bot}", callback_data=f"stop_{running_bot}")],
        [InlineKeyboardButton("🔙 Cancel", callback_data="cancel")],
    ]
    await update.message.reply_text(
        f"🤖 <b>Running:</b> <code>{esc(running_bot)}</code>\n\nClick to stop:",
        reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

# ==================== VIEW ERRORS ====================
async def view_errors(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user_dir = os.path.join(BASE_DIR, str(uid))
    err = os.path.join(user_dir, "error.log")
    if not os.path.exists(err):
        await update.message.reply_text("✅ <b>No errors found</b>", reply_markup=KEYBOARD, parse_mode="HTML")
        return
    try:
        with open(err, "r", encoding="utf-8", errors="ignore") as f:
            data = f.read()
        if not data.strip():
            await update.message.reply_text("✅ <b>Error log is empty</b>", reply_markup=KEYBOARD, parse_mode="HTML")
            return
        data = data[-3500:]
        await update.message.reply_text(
            f"⚠️ <b>Error Log</b>\n\n<pre>{esc(data)}</pre>",
            reply_markup=KEYBOARD, parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {esc(str(e))}", parse_mode="HTML")

# ==================== BROADCAST CMD ====================
async def admin_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Not authorized!", parse_mode="HTML")
        return
    if not context.args:
        await update.message.reply_text(
            "📢 <b>Broadcast</b>\n\n"
            "Usage: <code>/broadcast Your message</code>\n"
            "Or use Admin Panel → 📢 Broadcast",
            parse_mode="HTML")
        return
    msg = " ".join(context.args)
    sent = failed = 0
    if os.path.exists(BASE_DIR):
        for user_dir in os.listdir(BASE_DIR):
            if os.path.isdir(os.path.join(BASE_DIR, user_dir)):
                try:
                    await context.bot.send_message(
                        chat_id=int(user_dir),
                        text=f"📢 <b>ANNOUNCEMENT</b>\n\n{esc(msg)}",
                        parse_mode="HTML")
                    sent += 1
                except:
                    failed += 1
    await update.message.reply_text(
        f"✅ <b>Broadcast done!</b>\nSent: {sent}\nFailed: {failed}",
        parse_mode="HTML")

async def admin_pending_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if not PENDING_APPROVALS:
        await update.message.reply_text("✅ No pending approvals.", parse_mode="HTML")
        return
    text = "⏳ <b>Pending Approvals:</b>\n\n"
    for aid, info in PENDING_APPROVALS.items():
        text += f"• <code>{aid}</code> → User <code>{info['user_id']}</code> | {esc(info['file_name'])}\n"
    await update.message.reply_text(text, parse_mode="HTML")

# ==================== GLOBAL CANCEL ====================
async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    cleared = False
    if uid in ADMIN_STATE:
        ADMIN_STATE.pop(uid, None)
        cleared = True
    if context.user_data.get("awaiting_install"):
        context.user_data["awaiting_install"] = False
        cleared = True
    if cleared:
        if uid == ADMIN_ID:
            await update.message.reply_text(
                "❌ <b>Cancelled</b>",
                reply_markup=get_admin_keyboard(), parse_mode="HTML")
        else:
            await update.message.reply_text("❌ Cancelled", reply_markup=KEYBOARD, parse_mode="HTML")
    else:
        await update.message.reply_text("ℹ️ Nothing to cancel.", parse_mode="HTML")

# ==================== ROUTER ====================
async def router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    uid = update.effective_user.id
    text = update.message.text

    if uid in BANNED_USERS and uid != ADMIN_ID:
        await update.message.reply_text("🚫 <b>You are banned!</b>\nContact admin.", parse_mode="HTML")
        return

    if uid == ADMIN_ID and uid in ADMIN_STATE:
        if await handle_admin_state(update, context, ADMIN_STATE[uid]):
            return

    if context.user_data.get("awaiting_install"):
        if text in ALL_BUTTON_TEXTS:
            context.user_data["awaiting_install"] = False
            await update.message.reply_text("❌ Manual install auto-cancelled.\n", parse_mode="HTML")
        elif text.lower() == "/cancel":
            context.user_data["awaiting_install"] = False
            await update.message.reply_text("❌ Installation cancelled", reply_markup=KEYBOARD)
            return
        else:
            if await handle_manual_install(update, context):
                return

    if text == "📤 Upload File":
        uploads_left = max(0, MAX_DAILY_UPLOADS - get_today_uploads(uid))
        await update.message.reply_text(
            "📎 <b>Upload Instructions</b>\n\n"
            "• File ka naam <b>exactly</b> <code>bot.py</code> hona chahiye\n"
            "• Only <b>bot.py</b> accepted\n"
            "• Max 10 MB\n"
            "• Admin approval required\n"
            "• 1 pending at a time\n\n"
            f"📊 <b>Uploads Left Today:</b> <code>{uploads_left}/{MAX_DAILY_UPLOADS}</code>",
            reply_markup=KEYBOARD, parse_mode="HTML")
    elif text == "📁 View Files":
        await view_files(update, context)
    elif text == "▶️ Run Bot":
        await run_bot(update, context)
    elif text == "⏸️ Stop Bot":
        await stop_bot(update, context)
    elif text == "📦 Manual Install":
        await manual_install_prompt(update, context)
    elif text == "🗑️ Delete Files":
        await show_delete_menu(update, context)
    elif text == "⚠️ View Errors":
        await view_errors(update, context)
    else:
        await update.message.reply_text(
            "❓ <b>Unknown command</b>\nUse buttons below",
            reply_markup=KEYBOARD, parse_mode="HTML")

# ==================== CLEANUP ====================
def cleanup():
    import glob
    for pattern in ("*.pid", "*.start"):
        for f in glob.glob(os.path.join(BASE_DIR, "*", pattern)):
            try:
                os.remove(f)
            except:
                pass

# ==================== MAIN ====================
def main():
    import atexit
    atexit.register(cleanup)
    try:
        app = ApplicationBuilder().token(TOKEN).build()

        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("help", help_command))
        app.add_handler(CommandHandler("admin", admin_panel))
        app.add_handler(CommandHandler("cancel", cancel_command))
        app.add_handler(CommandHandler("broadcast", admin_broadcast))
        app.add_handler(CommandHandler("pending", admin_pending_cmd))

        app.add_handler(MessageHandler(filters.Document.FileExtension("py"), upload_file))
        app.add_handler(MessageHandler(filters.Document.ALL, lambda u, c: u.message.reply_text(
            "❌ <b>Only <code>bot.py</code> files allowed!</b>", parse_mode="HTML")))

        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, router))
        app.add_handler(CallbackQueryHandler(handle_callback))

        print("=" * 60)
        print("ADVANCED HOSTING PANEL — v9")
        print("=" * 60)
        print("• Manual Install: sudo / apt / pip / python3 ALL ALLOWED")
        print("• Blocked ONLY: uninstall, remove, purge, delete, rm, kill")
        print("• Admin Panel buttons working")
        print("• IST time working")
        print("=" * 60)
        print(f"Base: {os.path.abspath(BASE_DIR)}")
        print(f"Admin ID: {ADMIN_ID}")
        print("=" * 60)

        app.run_polling()
    except Exception as e:
        print(f"Failed: {e}")

if __name__ == "__main__":
    main()

# credit - @vishalcodeverse
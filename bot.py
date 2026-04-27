# -*- coding: utf-8 -*-

"""
ULTIMATE BOT MANAGER (v33.0 - Test Bot Lockdown Added)
Base: Original Expanded Code (bot (3).py) + v31.0 + v32.0 Fixes
Features Added/Fixed:
1. PARSE ERROR FIXED: Replaced Markdown with HTML for link generation & broadcasts.
2. ROBUST AUTO-KICK: Unconditional kick if user leaves Main Channel or blocks bot.
3. ID MATCHING FIXED: Strips '-100' for foolproof channel leave detection.
4. BACKGROUND SYNC ENHANCED: Runs every 10 mins (with flood protection).
5. /sync COMMAND: Admin can manually trigger background sync.
6. AUTO-BROADCAST: Includes @H4R_Contact_bot in the text (Fixed to Auto-Delete in 3 Hrs).
7. KICK LOGIC RESTORED: Added back detailed error catching and unban logic.
8. ROLE-BASED MENUS: Different Telegram Menu commands for Owner, Admins, and Users.
9. ChatMember.KICKED ERROR FIXED: Updated to match python-telegram-bot v20+.
10. PERMANENT /ban LOGIC: Bans forcefully remove users from channels and block re-entry.
11. TELEGRAM UNBAN FIXED: /unban now physically removes the user from the "Removed Users" list of all tracked channels so new invite links work perfectly.
12. AUTO-DELETE SECRETS: Invite Links and Joined/Welcome messages auto-delete after 60 seconds to keep chats clean.
13. DYNAMIC TEST BOT BUTTON: Added a "Test Bot" button in the main menu that strictly verifies channel membership before providing the link.
14. /settestbot COMMAND: Admins can dynamically update the Test Bot link from within Telegram.
15. /locktestbot COMMAND (NEW): Admins can lock/unlock the Test Bot button just like free and paid batches.
"""

import logging
import json
import os
import io
import asyncio
import time
import threading
import re
from pyrogram import Client
from pyrogram.errors import FloodWait
from datetime import datetime, timedelta
from telegram import (
    Update, ChatMember, InlineKeyboardButton, InlineKeyboardMarkup, 
    BotCommandScopeChat, ChatJoinRequest, BotCommand,
    InputMediaPhoto, InputMediaVideo, InputMediaDocument, InputMediaAudio, InputMediaAnimation
)
from telegram.constants import ChatType, ParseMode
from telegram.error import TelegramError, BadRequest, Forbidden, RetryAfter
from telegram.ext import (
    ApplicationBuilder, CommandHandler, ContextTypes, ChatMemberHandler, 
    CallbackQueryHandler, MessageHandler, filters, Application, ChatJoinRequestHandler,
    MessageReactionHandler
)

# --- 1. LOGGING & SETUP ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- 2. FLASK KEEPALIVE SERVER ---
try:
    from flask import Flask
    def _start_keepalive():
        port = int(os.environ.get("PORT", "7860"))
        app = Flask(__name__)
        
        @app.route('/')
        def index(): 
            return "Bot Running - v33.0 Test Bot Lockdown", 200
        
        def run():
            app.run(host="0.0.0.0", port=port, use_reloader=False)
        
        t = threading.Thread(target=run, daemon=True)
        t.start()
except ImportError:
    def _start_keepalive(): 
        pass

_start_keepalive()

# --- 3. CONFIGURATION & DEFAULTS ---
DEFAULTS = {
    "TOKEN": "", 
    "OWNER": 0,
    "SUPPORT": 0,
    "MAIN_CH": 0,
    "LOG_CH": 0
}

API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
SESSION_STRING = os.environ.get("SESSION_STRING", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", DEFAULTS["TOKEN"])
OWNER_ID = int(os.environ.get("OWNER_ID", DEFAULTS["OWNER"]))
SUPPORT_GROUP_ID = int(os.environ.get("SUPPORT_GROUP_ID", DEFAULTS["SUPPORT"]))
MANDATORY_CHANNEL_ID = int(os.environ.get("MANDATORY_CHANNEL_ID", DEFAULTS["MAIN_CH"]))
LOG_CHANNEL_ID = int(os.environ.get("LOG_CHANNEL_ID", DEFAULTS["LOG_CH"]))
MONGO_URL = os.environ.get("MONGO_URL", None) 

MANDATORY_CHANNEL_LINK = os.environ.get("MANDATORY_CHANNEL_LINK", "https://t.me/YourChannel")
DATA_FILE = os.environ.get("DATA_FILE", "bot_data.json")

# --- 4. DATABASE & MEMORY ---
DB = {
    "ADMIN_IDS": [],
    "FREE_CHANNELS": {},
    "PAID_CHANNELS": {},
    "ALL_CHATS": {},     
    "USER_DATA": {},     
    "BLOCKED_USERS": [],
    "USER_TOPICS": {}, 
    "PENDING_REQUESTS": {},
    "LINK_MAP": {},      
    "CUSTOM_WELCOMES": {}, 
    "NEW_USERS_ALLOWED": True, 
    "FREE_LOCKED": False,      
    "PAID_LOCKED": False,
    "TEST_BOT_LOCKED": False, # NEW CONFIG FOR LOCK
    "SCHEDULED_DELETES": [],
    "TEST_BOT_LINK": ""
}

# Runtime Memory
MESSAGE_MAP = {} 
ADMIN_WIZARD = {} 
BROADCAST_STATE = {} 
TOPIC_CREATION_LOCK = set()
SPAM_CACHE = {} 

data_lock = asyncio.Lock()
mongo_client = None
mongo_collection = None

if MONGO_URL:
    try:
        from pymongo import MongoClient
        import certifi
        mongo_client = MongoClient(MONGO_URL, tlsCAFile=certifi.where())
        mongo_db = mongo_client.get_database("telegram_bot_db")
        mongo_collection = mongo_db.get_collection("bot_settings")
        logger.info("✅ Connected to MongoDB Atlas")
    except Exception as e:
        logger.error(f"❌ MongoDB Connection Failed: {e}")
        MONGO_URL = None

# --- 5. PERSISTENCE FUNCTIONS ---
def load_data():
    global DB
    
    if MONGO_URL and mongo_collection is not None:
        try:
            data = mongo_collection.find_one({"_id": "main_settings"})
            if data and "data" in data:
                loaded = data["data"]
                
                if "ADMIN_IDS" in loaded: DB["ADMIN_IDS"] = [int(x) for x in loaded["ADMIN_IDS"] if str(x).isdigit()]
                if "BLOCKED_USERS" in loaded: DB["BLOCKED_USERS"] = loaded["BLOCKED_USERS"]
                if "LINK_MAP" in loaded: DB["LINK_MAP"] = loaded["LINK_MAP"]
                if "CUSTOM_WELCOMES" in loaded: DB["CUSTOM_WELCOMES"] = {int(k): v for k, v in loaded["CUSTOM_WELCOMES"].items()}
                if "NEW_USERS_ALLOWED" in loaded: DB["NEW_USERS_ALLOWED"] = loaded["NEW_USERS_ALLOWED"]
                if "FREE_LOCKED" in loaded: DB["FREE_LOCKED"] = loaded["FREE_LOCKED"]
                if "PAID_LOCKED" in loaded: DB["PAID_LOCKED"] = loaded["PAID_LOCKED"]
                if "TEST_BOT_LOCKED" in loaded: DB["TEST_BOT_LOCKED"] = loaded["TEST_BOT_LOCKED"]
                if "SCHEDULED_DELETES" in loaded: DB["SCHEDULED_DELETES"] = loaded["SCHEDULED_DELETES"]
                if "TEST_BOT_LINK" in loaded: DB["TEST_BOT_LINK"] = loaded["TEST_BOT_LINK"]
                
                for k in ["FREE_CHANNELS", "PAID_CHANNELS", "ALL_CHATS", "USER_TOPICS", "USER_DATA", "PENDING_REQUESTS"]:
                    if k in loaded: 
                        DB[k] = {int(i): v for i, v in loaded[k].items()}
                
                if OWNER_ID not in DB["ADMIN_IDS"]: 
                    DB["ADMIN_IDS"].append(OWNER_ID)
                    
                for cid, name in DB["FREE_CHANNELS"].items():
                    if cid not in DB["ALL_CHATS"]: 
                        DB["ALL_CHATS"][cid] = name
                        
                for cid, name in DB["PAID_CHANNELS"].items():
                    if cid not in DB["ALL_CHATS"]: 
                        DB["ALL_CHATS"][cid] = name
                        
                logger.info("✅ Database loaded from MongoDB.")
                return
        except Exception as e:
            logger.error(f"MongoDB Load Error: {e}")

    if not os.path.exists(DATA_FILE):
        save_data_sync()
        return

    try:
        with open(DATA_FILE, "r") as f:
            loaded = json.load(f)
            
            if "ADMIN_IDS" in loaded: DB["ADMIN_IDS"] = [int(x) for x in loaded["ADMIN_IDS"] if str(x).isdigit()]
            if "BLOCKED_USERS" in loaded: DB["BLOCKED_USERS"] = loaded["BLOCKED_USERS"]
            if "LINK_MAP" in loaded: DB["LINK_MAP"] = loaded["LINK_MAP"]
            if "CUSTOM_WELCOMES" in loaded: DB["CUSTOM_WELCOMES"] = {int(k): v for k, v in loaded["CUSTOM_WELCOMES"].items()}
            if "NEW_USERS_ALLOWED" in loaded: DB["NEW_USERS_ALLOWED"] = loaded["NEW_USERS_ALLOWED"]
            if "FREE_LOCKED" in loaded: DB["FREE_LOCKED"] = loaded["FREE_LOCKED"]
            if "PAID_LOCKED" in loaded: DB["PAID_LOCKED"] = loaded["PAID_LOCKED"]
            if "TEST_BOT_LOCKED" in loaded: DB["TEST_BOT_LOCKED"] = loaded["TEST_BOT_LOCKED"]
            if "SCHEDULED_DELETES" in loaded: DB["SCHEDULED_DELETES"] = loaded["SCHEDULED_DELETES"]
            if "TEST_BOT_LINK" in loaded: DB["TEST_BOT_LINK"] = loaded["TEST_BOT_LINK"]
            
            for k in ["FREE_CHANNELS", "PAID_CHANNELS", "ALL_CHATS", "USER_TOPICS", "USER_DATA", "PENDING_REQUESTS"]:
                if k in loaded: 
                    DB[k] = {int(i): v for i, v in loaded[k].items()}

            if OWNER_ID not in DB["ADMIN_IDS"]: 
                DB["ADMIN_IDS"].append(OWNER_ID)
                
            for cid, name in DB["FREE_CHANNELS"].items():
                if cid not in DB["ALL_CHATS"]: 
                    DB["ALL_CHATS"][cid] = name
                    
            for cid, name in DB["PAID_CHANNELS"].items():
                if cid not in DB["ALL_CHATS"]: 
                    DB["ALL_CHATS"][cid] = name
                    
            logger.info("Database loaded from Local File.")
    except Exception as e:
        logger.error(f"Local Load Error: {e}")

def save_data_sync():
    try:
        to_save = {
            "ADMIN_IDS": DB["ADMIN_IDS"],
            "BLOCKED_USERS": DB["BLOCKED_USERS"],
            "NEW_USERS_ALLOWED": DB.get("NEW_USERS_ALLOWED", True),
            "FREE_LOCKED": DB.get("FREE_LOCKED", False),
            "PAID_LOCKED": DB.get("PAID_LOCKED", False),
            "TEST_BOT_LOCKED": DB.get("TEST_BOT_LOCKED", False),
            "LINK_MAP": DB["LINK_MAP"],
            "CUSTOM_WELCOMES": {str(k): v for k, v in DB["CUSTOM_WELCOMES"].items()},
            "FREE_CHANNELS": {str(k): v for k, v in DB["FREE_CHANNELS"].items()},
            "PAID_CHANNELS": {str(k): v for k, v in DB["PAID_CHANNELS"].items()},
            "ALL_CHATS": {str(k): v for k, v in DB["ALL_CHATS"].items()},
            "USER_DATA": {str(k): v for k, v in DB["USER_DATA"].items()},
            "USER_TOPICS": {str(k): v for k, v in DB["USER_TOPICS"].items()},
            "PENDING_REQUESTS": {str(k): v for k, v in DB["PENDING_REQUESTS"].items()},
            "SCHEDULED_DELETES": DB.get("SCHEDULED_DELETES", []),
            "TEST_BOT_LINK": DB.get("TEST_BOT_LINK", "")
        }

        if MONGO_URL and mongo_collection is not None:
            try: 
                mongo_collection.replace_one(
                    {"_id": "main_settings"}, 
                    {"_id": "main_settings", "data": to_save}, 
                    upsert=True
                )
            except Exception as e:
                logger.error(f"MongoDB Save Error: {e}")
                
        with open(DATA_FILE, "w") as f: 
            json.dump(to_save, f, indent=4)
            
    except Exception as e:
        logger.error(f"Save Error: {e}")

async def save_data_async():
    async with data_lock: 
        await asyncio.to_thread(save_data_sync)

# --- 6. CORE HELPERS ---

async def execute_universal_kick(user_id, context, permanent_ban=False):
    """Kicks or Permanently Bans the user from ALL batches and blocks them."""
    mod = False
    
    # 1. Unconditionally remove from Free Batches
    for bid in list(DB["FREE_CHANNELS"].keys()):
        try:
            await context.bot.ban_chat_member(int(bid), user_id)
            if not permanent_ban:
                await context.bot.unban_chat_member(int(bid), user_id) # Just kick
        except Exception: pass

    # 2. Unconditionally remove from Paid Batches
    for bid in list(DB["PAID_CHANNELS"].keys()):
        try:
            await context.bot.ban_chat_member(int(bid), user_id)
            if not permanent_ban:
                await context.bot.unban_chat_member(int(bid), user_id) # Just kick
        except Exception: pass

    # 3. Block User in Bot
    if user_id not in DB["BLOCKED_USERS"]:
        DB["BLOCKED_USERS"].append(user_id)
        mod = True

    if mod:
        await save_data_async()

def is_admin(uid):
    if uid == OWNER_ID: 
        return True
    if str(uid) == str(OWNER_ID): 
        return True
    if uid in DB["ADMIN_IDS"]: 
        return True
        
    str_uid = str(uid)
    for admin_id in DB["ADMIN_IDS"]:
        if str(admin_id) == str_uid:
            return True
            
    return False

def check_spam(uid):
    now = time.time()
    last = SPAM_CACHE.get(uid, 0)
    SPAM_CACHE[uid] = now
    
    if now - last < 1.5: 
        return True
        
    return False

async def check_membership(user_id, context):
    if is_admin(user_id):
        return True
    if not MANDATORY_CHANNEL_ID: 
        return True
        
    try:
        m = await context.bot.get_chat_member(MANDATORY_CHANNEL_ID, user_id)
        if m.status in [ChatMember.MEMBER, ChatMember.ADMINISTRATOR, ChatMember.OWNER]:
            return True
        return False
    except Exception: 
        return False

async def is_already_in_channel(context, chat_id, user_id):
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        if member.status in [ChatMember.MEMBER, ChatMember.ADMINISTRATOR, ChatMember.OWNER]:
            return True
        return False
    except Exception: 
        return False

async def delete_later(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    try: 
        await context.bot.delete_message(chat_id=job.data['chat_id'], message_id=job.data['msg_id'])
    except Exception: 
        pass

async def schedule_delete(context, message, delay=1200):
    if message: 
        context.job_queue.run_once(delete_later, delay, data={'chat_id': message.chat.id, 'msg_id': message.message_id})

async def get_or_create_topic(user, context):
    if not SUPPORT_GROUP_ID: 
        return None
        
    if user.id in DB["USER_TOPICS"]: 
        return DB["USER_TOPICS"][user.id]
        
    if user.id in TOPIC_CREATION_LOCK:
        await asyncio.sleep(1) 
        if user.id in DB["USER_TOPICS"]: 
            return DB["USER_TOPICS"][user.id]
            
    TOPIC_CREATION_LOCK.add(user.id)
    
    try:
        name = f"{user.first_name[:20]} ({user.id})"
        topic = await context.bot.create_forum_topic(SUPPORT_GROUP_ID, name)
        DB["USER_TOPICS"][user.id] = topic.message_thread_id
        await save_data_async()
        
        group_id_str = str(SUPPORT_GROUP_ID).replace("-100", "")
        
        text = (
            f"👤 **NEW USER TICKET**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📛 **Name:** {user.full_name}\n"
            f"🆔 **ID:** `{user.id}`\n"
            f"🔗 **Username:** @{user.username or 'None'}\n"
            f"🌐 **Lang:** {user.language_code or 'N/A'}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📜 [Click to Check History](https://t.me/c/{group_id_str}?q={user.id})"
        )
        
        await context.bot.send_message(
            SUPPORT_GROUP_ID, 
            text, 
            message_thread_id=topic.message_thread_id, 
            parse_mode=ParseMode.MARKDOWN, 
            disable_web_page_preview=True
        )
        
        return topic.message_thread_id
        
    except Exception as e: 
        logger.error(f"Topic Creation Error: {e}")
        return None
    finally: 
        TOPIC_CREATION_LOCK.discard(user.id)

async def track_chats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.my_chat_member: 
        return
        
    chat = update.my_chat_member.chat
    status = update.my_chat_member.new_chat_member.status
    
    if chat.type == ChatType.PRIVATE:
        if status == ChatMember.BANNED:
            logger.info(f"🚫 User {chat.id} blocked the bot. Auto-kicking unconditionally.")
            await execute_universal_kick(chat.id, context)
        return
    
    if status in [ChatMember.MEMBER, ChatMember.ADMINISTRATOR]:
        if chat.id not in DB["ALL_CHATS"]:
            DB["ALL_CHATS"][chat.id] = chat.title or f"Chat {chat.id}"
            await save_data_async()
            
    elif status in [ChatMember.LEFT, ChatMember.BANNED]:
        if chat.id in DB["ALL_CHATS"]:
            if chat.id not in DB["FREE_CHANNELS"] and chat.id not in DB["PAID_CHANNELS"]:
                del DB["ALL_CHATS"][chat.id]
                await save_data_async()

# --- 7. NEW: ROLE BASED MENU COMMANDS ---
async def set_role_based_commands(user_id, context: ContextTypes.DEFAULT_TYPE):
    """Sets the Telegram menu dynamically based on whether the user is Owner, Admin, or Regular User"""
    try:
        user_cmds = [
            BotCommand("start", "Open Main Menu"),
            BotCommand("id", "Get Telegram ID"),
            BotCommand("myinfo", "Check Active Demos")
        ]
        
        admin_cmds = user_cmds + [
            BotCommand("stats", "Bot Statistics"),
            BotCommand("batchstats", "Batch Info"),
            BotCommand("find", "Find a User"),
            BotCommand("ban", "Ban & Remove User"),
            BotCommand("unban", "Unban User"),
            BotCommand("kick", "Kick User"),
            BotCommand("extend", "Extend Demo"),
            BotCommand("demo", "Approve Demo"),
            BotCommand("per", "Approve Perm"),
            BotCommand("broadcast", "Send Broadcast"),
            BotCommand("post", "Post Message"),
            BotCommand("setwelcome", "Set Welcome"),
            BotCommand("settestbot", "Set Test Bot Link"),
            BotCommand("sync", "Manual Sync")
        ]
        
        owner_cmds = admin_cmds + [
            BotCommand("addadmin", "Add New Admin"),
            BotCommand("deladmin", "Remove Admin"),
            BotCommand("allusers", "All Users List"),
            BotCommand("lockdown", "Toggle Lockdown"),
            BotCommand("lockfree", "Lock Free Batches"),
            BotCommand("lockpaid", "Lock Paid Batches"),
            BotCommand("locktestbot", "Lock Test Bot"),
            BotCommand("addbatch", "Add Batch"),
            BotCommand("delbatch", "Delete Batch"),
            BotCommand("backup", "Backup DB")
        ]
        
        if str(user_id) == str(OWNER_ID):
            await context.bot.set_my_commands(owner_cmds, scope=BotCommandScopeChat(user_id))
        elif is_admin(user_id):
            await context.bot.set_my_commands(admin_cmds, scope=BotCommandScopeChat(user_id))
        else:
            await context.bot.set_my_commands(user_cmds, scope=BotCommandScopeChat(user_id))
    except Exception as e:
        logger.error(f"Failed to set role commands for {user_id}: {e}")

# --- DELETE COMMAND (/del) ---
async def cmd_del_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): 
        return
        
    if not update.message.reply_to_message: 
        return
        
    key = (update.effective_chat.id, update.message.reply_to_message.message_id)
    if key in MESSAGE_MAP:
        target_chat, target_msg = MESSAGE_MAP[key]
        try:
            await context.bot.delete_message(target_chat, target_msg)
            await update.message.reply_to_message.delete()
            await update.message.delete()
            
            del MESSAGE_MAP[key]
            del MESSAGE_MAP[(target_chat, target_msg)]
        except Exception as e:
            msg = await update.message.reply_text(f"⚠️ Delete failed (> 48hrs old or not found). Error: {e}")
            await schedule_delete(context, msg)

# --- 8. COMMANDS ---

async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Ab ye command SIRF OWNER use kar payega
    if update.effective_user.id != OWNER_ID: 
        return
        
    if not SESSION_STRING or not API_ID:
        await update.message.reply_text("❌ **Userbot Config Missing!**\nPlease add API_ID, API_HASH, and SESSION_STRING.", parse_mode=ParseMode.MARKDOWN)
        return

    msg = await update.message.reply_text("⏳ **Super Exit /clear Start...**\n\n🛡️ *Render Free Server aur Telegram Limits ko bachane ke liye ye process SLOW rakha gaya hai. Kripya wait karein...*", parse_mode=ParseMode.MARKDOWN)

    # In-memory session jisse disk usage na badhe
    userbot = Client("clear_bot", api_id=API_ID, api_hash=API_HASH, session_string=SESSION_STRING, in_memory=True)
    
    try:
        await userbot.start()
        
        # 1. Fetch Mandatory Channel Users (SLOW & SAFE)
        await msg.edit_text("⏳ Mandatory Channel ke sabhi users ko fetch kiya ja raha hai... (Slow Process)")
        mandatory_users = set()
        
        try:
            async for member in userbot.get_chat_members(MANDATORY_CHANNEL_ID):
                if not member.user.is_bot and not member.user.is_deleted:
                    mandatory_users.add(member.user.id)
                # Chota delay taaki Userbot list nikalte time FloodWait na khaye
                await asyncio.sleep(0.01) 
        except FloodWait as e:
            await asyncio.sleep(e.value + 2) # Agar flood aaya toh wait karega
            
        # Txt file generate karna (Memory safe)
        file_content = "Mandatory Channel Users ID:\n" + "\n".join([str(uid) for uid in mandatory_users])
        f = io.BytesIO(file_content.encode("utf-8"))
        f.name = "mandatory_users.txt"
        
        await context.bot.send_document(
            chat_id=update.effective_chat.id, 
            document=f, 
            caption=f"✅ Mandatory channel se **{len(mandatory_users)}** users fetch hue hain."
        )

        # 2. Check and Kick from other Batches
        all_channels = list(DB["FREE_CHANNELS"].keys()) + list(DB["PAID_CHANNELS"].keys())
        removed_count = 0
        checked_users = 0
        
        for bid in all_channels:
            try:
                bname = DB["ALL_CHATS"].get(int(bid), f"Batch {bid}")
                await msg.edit_text(f"⏳ Checking Batch: **{bname}**...\nServer ko safe rakhne ke liye process slow chal raha hai.", parse_mode=ParseMode.MARKDOWN)
                
                async for member in userbot.get_chat_members(int(bid)):
                    uid = member.user.id
                    checked_users += 1
                    
                    if uid not in mandatory_users and uid != OWNER_ID and not member.user.is_bot:
                        try:
                            # User ko remove karna
                            await context.bot.ban_chat_member(int(bid), uid)
                            await context.bot.unban_chat_member(int(bid), uid)
                            removed_count += 1
                            
                            # ✨ SUPER SAFE DELAY FOR RENDER & TELEGRAM (1.5 Seconds) ✨
                            await asyncio.sleep(1.5)
                            
                        except RetryAfter as e:
                            # Agar Main Bot par FloodWait lag gaya
                            logger.warning(f"FloodWait hit for {e.retry_after} seconds. Waiting...")
                            await asyncio.sleep(e.retry_after + 2)
                        except Exception:
                            pass
                    
                    # Message har 50 users ke baad update hoga taaki message edit limit na lage
                    if checked_users % 50 == 0:
                        try:
                            await msg.edit_text(f"⏳ **Live Status:**\nBatch: {bname}\nChecked: `{checked_users}`\nRemoved: `{removed_count}`\n\n*Process is running safely...*", parse_mode=ParseMode.MARKDOWN)
                        except RetryAfter as e:
                            await asyncio.sleep(e.retry_after + 1)
                        except Exception:
                            pass
                            
            except FloodWait as e:
                # Agar Userbot par limit lagi
                await asyncio.sleep(e.value + 5)
            except Exception as e:
                logger.error(f"Batch {bid} error: {e}")
                
        await userbot.stop()

        # 3. Final Success Message
        await msg.edit_text(
            f"✅ **/clear Process Pura Hua!** (100% Safe Execution)\n\n"
            f"🛡️ **Total Mandatory Users:** `{len(mandatory_users)}`\n"
            f"🚪 **Dusre Batches se Remove Hue:** `{removed_count}`\n\n"
            f"*(Render server aur bot dono bina kisi crash ke safely run hue)*",
            parse_mode=ParseMode.MARKDOWN
        )

    except Exception as e:
        await msg.edit_text(f"❌ **Error occurred:** `{e}`\n(Process stopped to protect server)", parse_mode=ParseMode.MARKDOWN)
        try:
            await userbot.stop()
        except Exception:
            pass
            
async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    msg_obj = update.effective_message
    
    text = ""
    if chat.type == ChatType.PRIVATE:
        if user: 
            text = f"👤 **Your User ID:** `{user.id}`"
        else: 
            text = f"🆔 **Chat ID:** `{chat.id}`"
    else:
        text = f"🆔 **Chat ID:** `{chat.id}`"
        if msg_obj and msg_obj.is_topic_message and msg_obj.message_thread_id: 
            text += f"\n🧵 **Topic ID:** `{msg_obj.message_thread_id}`"
        if user: 
            text += f"\n👤 **User ID:** `{user.id}`"
            
    try:
        if msg_obj: 
            await msg_obj.reply_text(text, parse_mode=ParseMode.MARKDOWN)
        else: 
            await context.bot.send_message(chat.id, text, parse_mode=ParseMode.MARKDOWN)
    except Exception: 
        pass
        
    if user and is_admin(user.id): 
        await schedule_delete(context, msg_obj)

async def cmd_joinall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Sirf Owner use kar sakta hai
    if update.effective_user.id != OWNER_ID:
        return

    session_string = DB.get("SESSION_STRING")
    if not session_string or not API_ID:
        await update.message.reply_text("❌ **Error:** Pehle `/login` karke apna Userbot connect karein.")
        return

    msg = await update.message.reply_text("⏳ **Userbot ko channels me add kiya ja raha hai...**\nPyrogram connect ho raha hai...")

    # Pyrogram client start karke Userbot ka asli Telegram ID nikalenge
    client = Client("temp_bot", api_id=API_ID, api_hash=API_HASH, session_string=session_string, in_memory=True)
    try:
        await client.start()
        me = await client.get_me()
        userbot_id = me.id
        await client.stop()
    except Exception as e:
        await msg.edit_text(f"❌ **Userbot connect nahi ho paya:** `{e}`")
        return

    await msg.edit_text(f"⏳ **Account Found:** `{me.first_name}`\nAb isko sabhi channels me Admin banaya ja raha hai... Please wait.")

    # Sabhi channels ki ek list banayenge
    all_chats = []
    if MANDATORY_CHANNEL_ID:
        all_chats.append(MANDATORY_CHANNEL_ID)
    all_chats.extend(list(DB["FREE_CHANNELS"].keys()))
    all_chats.extend(list(DB["PAID_CHANNELS"].keys()))

    success = 0
    failed = 0

    for cid in all_chats:
        try:
            # Main Bot seedha Userbot ko admin bana dega (Auto-Join ho jayega)
            await context.bot.promote_chat_member(
                chat_id=int(cid),
                user_id=userbot_id,
                can_invite_users=True,  # Minimal admin permission
                can_manage_chat=True    # Channel/Group manage karne ki basic permission
            )
            success += 1
            # FloodWait se bachne ke liye chhota delay
            await asyncio.sleep(0.5) 
        except Exception as e:
            logger.error(f"Failed to make admin in {cid}: {e}")
            failed += 1

    await msg.edit_text(
        f"✅ **Auto-Join & Admin Process Pura Hua!**\n\n"
        f"👤 **Account:** `{me.first_name}`\n"
        f"✅ **Successfully Added & Made Admin:** `{success}` channels\n"
        f"❌ **Failed:** `{failed}` channels (Agar koi fail hua, toh check karein ki kya wahan Main Bot ko Add Admin ka right hai ya nahi).\n\n"
        f"🎉 **Ab aap aaram se `/clear` command use kar sakte hain!**",
        parse_mode=ParseMode.MARKDOWN
    )

async def cmd_add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: 
        return
        
    try:
        new_admin = int(context.args[0])
        if new_admin not in DB["ADMIN_IDS"]: 
            DB["ADMIN_IDS"].append(new_admin)
            await save_data_async()
            msg = await update.message.reply_text(f"✅ User {new_admin} is now Admin.")
        else: 
            msg = await update.message.reply_text("⚠️ Already Admin.")
    except Exception: 
        msg = await update.message.reply_text("Usage: /addadmin [user_id]")
        
    await schedule_delete(context, update.message)
    await schedule_delete(context, msg)

async def cmd_del_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: 
        return
        
    try:
        target = int(context.args[0])
        if target in DB["ADMIN_IDS"] and target != OWNER_ID: 
            DB["ADMIN_IDS"].remove(target)
            await save_data_async()
            msg = await update.message.reply_text(f"🗑 User {target} removed from Admin.")
        else: 
            msg = await update.message.reply_text("⚠️ Cannot remove.")
    except Exception: 
        msg = await update.message.reply_text("Usage: /deladmin [user_id]")
        
    await schedule_delete(context, update.message)
    await schedule_delete(context, msg)

async def cmd_backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: 
        return
        
    save_data_sync()
    if os.path.exists(DATA_FILE): 
        await update.message.reply_document(document=open(DATA_FILE, "rb"), caption="DB Backup")
    else: 
        msg = await update.message.reply_text("No DB file found locally.")
        await schedule_delete(context, msg)
        
    await schedule_delete(context, update.message)

async def cmd_all_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: 
        return
        
    msg = await update.message.reply_text("⏳ Generating report...")
    report = f"ALL USERS DUMP - {datetime.now()}\n" + "-" * 40 + "\nID | Name | Username\n"
    
    for uid, data in DB["USER_DATA"].items(): 
        report += f"{uid} | {data.get('name')} | @{data.get('username')}\n"
        
    f = io.BytesIO(report.encode("utf-8"))
    f.name = "all_users.txt"
    
    await update.message.reply_document(document=f, caption="✅ All Users List")
    await context.bot.delete_message(update.effective_chat.id, msg.message_id)
    await schedule_delete(context, update.message)

async def cmd_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): 
        return
        
    target = None
    if len(context.args) > 0:
        try: target = int(context.args[0])
        except ValueError: pass
    elif update.message.message_thread_id:
        for u, t in DB["USER_TOPICS"].items():
            if t == update.message.message_thread_id:
                target = int(u)
                break

    if not target:
        msg = await update.message.reply_text("Usage: `/ban [user_id]` OR send `/ban` directly in a user's support topic.", parse_mode=ParseMode.MARKDOWN)
        await schedule_delete(context, update.message)
        await schedule_delete(context, msg)
        return

    if target not in DB["BLOCKED_USERS"] and target != OWNER_ID: 
        msg = await update.message.reply_text(f"⏳ Process started... Kicking and Banning user `{target}` from all groups.", parse_mode=ParseMode.MARKDOWN)
        await execute_universal_kick(target, context, permanent_ban=True)
        await msg.edit_text(f"🚫 User `{target}` has been PERMANENTLY BANNED from the bot and removed from all tracked groups.", parse_mode=ParseMode.MARKDOWN)
        
        if target in DB["USER_TOPICS"]:
            topic_id = DB["USER_TOPICS"][target]
            try: await context.bot.send_message(SUPPORT_GROUP_ID, f"🚨 **ADMIN ALERT:** This user has been BANNED.", message_thread_id=topic_id, parse_mode=ParseMode.MARKDOWN)
            except Exception: pass
    else: 
        msg = await update.message.reply_text("⚠️ User is already blocked or is the Owner.")
        
    await schedule_delete(context, update.message)
    try: await schedule_delete(context, msg) 
    except Exception: pass

async def cmd_unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): 
        return
        
    target = None
    if len(context.args) > 0:
        try: target = int(context.args[0])
        except ValueError: pass
    elif update.message.message_thread_id:
        for u, t in DB["USER_TOPICS"].items():
            if t == update.message.message_thread_id:
                target = int(u)
                break

    if not target:
        msg = await update.message.reply_text("Usage: `/unban [user_id]` OR send `/unban` directly in a user's support topic.", parse_mode=ParseMode.MARKDOWN)
        await schedule_delete(context, update.message)
        await schedule_delete(context, msg)
        return

    if target in DB["BLOCKED_USERS"]: 
        # 1. Remove from Bot DB
        DB["BLOCKED_USERS"].remove(target)
        await save_data_async()
        
        # 2. Telegram API: Unban from ALL channels' "Removed Users" list so new links work
        unban_count = 0
        all_channels = list(DB["FREE_CHANNELS"].keys()) + list(DB["PAID_CHANNELS"].keys())
        if MANDATORY_CHANNEL_ID:
            all_channels.append(MANDATORY_CHANNEL_ID)
            
        for bid in all_channels:
            try:
                await context.bot.unban_chat_member(int(bid), target)
                unban_count += 1
            except Exception: 
                pass
                
        msg = await update.message.reply_text(f"✅ User `{target}` has been UNBLOCKED in the bot DB and removed from the Banned list of {unban_count} channels. They can now join again using new links.", parse_mode=ParseMode.MARKDOWN)
        
        if target in DB["USER_TOPICS"]:
            topic_id = DB["USER_TOPICS"][target]
            try: await context.bot.send_message(SUPPORT_GROUP_ID, f"🟢 **ADMIN ALERT:** This user has been UNBANNED and their Telegram group restrictions have been cleared.", message_thread_id=topic_id, parse_mode=ParseMode.MARKDOWN)
            except Exception: pass
    else: 
        msg = await update.message.reply_text("⚠️ User is not blocked.")
        
    await schedule_delete(context, update.message)
    try: await schedule_delete(context, msg) 
    except Exception: pass

async def cmd_find_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): 
        return
        
    try: 
        query = context.args[0].replace("@", "").lower()
    except Exception: 
        msg = await update.message.reply_text("Usage: /find [username]")
        await schedule_delete(context, msg)
        return
        
    found = []
    for uid, data in DB["USER_DATA"].items():
        if query in data.get("username", "").lower():
            found.append(f"🆔 `{uid}` | Name: {data.get('name')} | @{data.get('username', '')}")
            
    if found:
        msg = await update.message.reply_text("🔍 **Found:**\n\n" + "\n".join(found), parse_mode=ParseMode.MARKDOWN)
    else:
        msg = await update.message.reply_text("❌ Not found.", parse_mode=ParseMode.MARKDOWN)
        
    await schedule_delete(context, update.message)
    await schedule_delete(context, msg)

async def cmd_lockdown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): 
        return
        
    DB["NEW_USERS_ALLOWED"] = not DB.get("NEW_USERS_ALLOWED", True)
    await save_data_async()
    
    if DB["NEW_USERS_ALLOWED"]:
        msg = await update.message.reply_text("🔓 **Lockdown Lifted!**", parse_mode=ParseMode.MARKDOWN)
    else:
        msg = await update.message.reply_text("🔒 **Lockdown Enabled!**", parse_mode=ParseMode.MARKDOWN)
        
    await schedule_delete(context, update.message)
    await schedule_delete(context, msg)

async def cmd_lockfree(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): 
        return
        
    DB["FREE_LOCKED"] = not DB.get("FREE_LOCKED", False)
    await save_data_async()
    
    if DB["FREE_LOCKED"]:
        msg = await update.message.reply_text("Free Batches are now **LOCKED 🔒**.", parse_mode=ParseMode.MARKDOWN)
    else:
        msg = await update.message.reply_text("Free Batches are now **UNLOCKED 🔓**.", parse_mode=ParseMode.MARKDOWN)
        
    await schedule_delete(context, update.message)
    await schedule_delete(context, msg)

async def cmd_lockpaid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): 
        return
        
    DB["PAID_LOCKED"] = not DB.get("PAID_LOCKED", False)
    await save_data_async()
    
    if DB["PAID_LOCKED"]:
        msg = await update.message.reply_text("Paid Batches are now **LOCKED 🔐**.", parse_mode=ParseMode.MARKDOWN)
    else:
        msg = await update.message.reply_text("Paid Batches are now **UNLOCKED 🔓**.", parse_mode=ParseMode.MARKDOWN)
        
    await schedule_delete(context, update.message)
    await schedule_delete(context, msg)

# --- NEW COMMAND: LOCK TEST BOT ---
async def cmd_locktestbot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): 
        return
        
    DB["TEST_BOT_LOCKED"] = not DB.get("TEST_BOT_LOCKED", False)
    await save_data_async()
    
    if DB["TEST_BOT_LOCKED"]:
        msg = await update.message.reply_text("Test Bot access is now **LOCKED 🔒**.", parse_mode=ParseMode.MARKDOWN)
    else:
        msg = await update.message.reply_text("Test Bot access is now **UNLOCKED 🔓**.", parse_mode=ParseMode.MARKDOWN)
        
    await schedule_delete(context, update.message)
    await schedule_delete(context, msg)

async def cmd_batch_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): 
        return
        
    msg = await update.message.reply_text("⏳ Calculating...")
    text = "📊 **BATCH STATISTICS**\n\n"
    
    all_batches = {**DB["FREE_CHANNELS"], **DB["PAID_CHANNELS"]}
    
    for cid, name in all_batches.items():
        active_demos = 0
        for uid, d in DB["USER_DATA"].items():
            if "demos" in d and str(cid) in d["demos"]:
                demo_data = d["demos"][str(cid)]
                if isinstance(demo_data, dict):
                    exp = demo_data["expiry"]
                else:
                    exp = float(demo_data)
                
                if exp > time.time():
                    active_demos += 1
                    
        try: 
            count = await context.bot.get_chat_member_count(cid)
        except Exception: 
            count = "N/A"
            
        text += f"📂 **{name}**\n   • ID: `{cid}`\n   • Members: `{count}`\n   • Active Demos: `{active_demos}`\n\n"
        
    await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN)

async def cmd_set_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): 
        return
        
    try: 
        bid = int(context.args[0])
        msg_text = " ".join(context.args[1:])
        DB["CUSTOM_WELCOMES"][bid] = msg_text
        await save_data_async()
        await update.message.reply_text(f"✅ Set:\n{msg_text}")
    except Exception: 
        await update.message.reply_text("Usage: `/setwelcome <batch_id> <message>`")

async def cmd_set_testbot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): 
        return
        
    try: 
        link = context.args[0]
        DB["TEST_BOT_LINK"] = link
        await save_data_async()
        msg = await update.message.reply_text(f"✅ Test bot link has been successfully set to: {link}")
    except Exception: 
        msg = await update.message.reply_text("Usage: `/settestbot <link>`\nExample: `/settestbot https://t.me/MyTestBot`", parse_mode=ParseMode.MARKDOWN)
        
    await schedule_delete(context, update.message)
    await schedule_delete(context, msg)

async def cmd_extend_demo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): 
        return
        
    try: 
        uid = int(context.args[0])
        bid = str(context.args[1])
        hours = float(context.args[2])
    except Exception: 
        msg = await update.message.reply_text("Usage: /extend [uid] [bid] [hrs]")
        await schedule_delete(context, msg)
        return
        
    if uid in DB["USER_DATA"] and "demos" in DB["USER_DATA"][uid] and bid in DB["USER_DATA"][uid]["demos"]:
        demo_data = DB["USER_DATA"][uid]["demos"][bid]
        if isinstance(demo_data, dict):
            current_exp = demo_data["expiry"]
        else:
            current_exp = float(demo_data)
            
        new_exp = max(current_exp, time.time()) + (hours * 3600)
        DB["USER_DATA"][uid]["demos"][bid] = {"expiry": new_exp, "warned": False}
        await save_data_async()
        
        msg = await update.message.reply_text(f"✅ Extended {hours} hrs.")
        
        try: 
            await context.bot.send_message(uid, f"🎁 **Demo Extended!**") 
        except Exception: 
            pass
    else: 
        msg = await update.message.reply_text("❌ No demo found.")
        
    await schedule_delete(context, update.message)
    await schedule_delete(context, msg)

async def cmd_kick_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): 
        return
        
    try: 
        uid = int(context.args[0])
        bid = int(context.args[1])
    except Exception: 
        msg = await update.message.reply_text("Usage: /kick [uid] [bid]")
        await schedule_delete(context, msg)
        return
        
    try:
        await context.bot.ban_chat_member(bid, uid)
        await context.bot.unban_chat_member(bid, uid)
        msg = await update.message.reply_text(f"✅ Kicked.")
        
        if uid in DB["USER_DATA"] and "demos" in DB["USER_DATA"][uid] and str(bid) in DB["USER_DATA"][uid]["demos"]: 
            del DB["USER_DATA"][uid]["demos"][str(bid)]
            await save_data_async()
    except Exception as e: 
        msg = await update.message.reply_text(f"❌ Failed: {e}")
        
    await schedule_delete(context, update.message)
    await schedule_delete(context, msg)

async def cmd_myinfo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    data = DB["USER_DATA"].get(uid, {})
    txt = f"👤 **MY INFO**\n🆔 ID: `{uid}`\n"
    
    if "demos" in data and data["demos"]:
        txt += "\n⏱ **Active Demos:**\n"
        now = time.time()
        for bid, d_data in data["demos"].items():
            if isinstance(d_data, dict):
                expiry = d_data["expiry"]
            else:
                expiry = float(d_data)
                
            rem = expiry - now
            if rem > 0:
                txt += f"• **{DB['ALL_CHATS'].get(int(bid), f'Batch {bid}')}**: {int(rem/60)} mins\n" 
            else:
                txt += f"• **{DB['ALL_CHATS'].get(int(bid), 'Batch')}**: EXPIRED 🔴\n"
    else: 
        txt += "\nNo active demos."
        
    if update.callback_query: 
        await context.bot.send_message(uid, txt, parse_mode=ParseMode.MARKDOWN)
        try: 
            await update.callback_query.answer() 
        except Exception: 
            pass
    elif update.message: 
        await update.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)

async def cmd_approve_demo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): 
        return
        
    msg = update.message
    try: 
        m = re.search(r'(https?://t\.me/\+[a-zA-Z0-9_\-]+)', msg.text)
        if m:
            link = m.group(1)
        else:
            link = context.args[0].strip()
    except Exception: 
        await msg.reply_text("Usage: `/demo <link>`")
        return
        
    ld = DB["LINK_MAP"].get(link)
    target_uid = None
    batch_id = None
    
    if isinstance(ld, dict):
        target_uid = ld.get("u")
        batch_id = ld.get("b")
    elif isinstance(ld, int):
        batch_id = ld
        if msg.message_thread_id:
            for u, t in DB["USER_TOPICS"].items():
                if t == msg.message_thread_id:
                    target_uid = int(u)
                    break
                    
    if not target_uid or not batch_id: 
        await msg.reply_text("❌ Link/User not found.")
        return
        
    if batch_id in DB["USER_DATA"].get(target_uid, {}).get("demo_history", []): 
        await msg.reply_text("⚠️ Warning: ALREADY used demo.")
        
    try:
        await context.bot.approve_chat_join_request(batch_id, target_uid)
        
        if "USER_DATA" not in DB:
            DB["USER_DATA"] = {}
        if target_uid not in DB["USER_DATA"]:
            DB["USER_DATA"][target_uid] = {}
        if "demos" not in DB["USER_DATA"][target_uid]:
            DB["USER_DATA"][target_uid]["demos"] = {}
            
        DB["USER_DATA"][target_uid]["demos"][str(batch_id)] = {"expiry": time.time() + (3 * 3600), "warned": False}
        
        if "demo_history" not in DB["USER_DATA"][target_uid]:
            DB["USER_DATA"][target_uid]["demo_history"] = []
            
        if batch_id not in DB["USER_DATA"][target_uid]["demo_history"]: 
            DB["USER_DATA"][target_uid]["demo_history"].append(batch_id)
            
        await save_data_async()
        
        await msg.reply_text(f"✅ **APPROVED (DEMO)**\nUser `{target_uid}` -> Batch `{batch_id}` for 3 Hrs.")
        
        try: 
            batch_name = DB['ALL_CHATS'].get(batch_id, 'Batch')
            welcome_str = DB['CUSTOM_WELCOMES'].get(batch_id, '')
            welc_msg = await context.bot.send_message(
                target_uid, 
                f"✅ **Approved for 3hrs!**\nWelcome to {batch_name}.\n\n{welcome_str}", 
                parse_mode=ParseMode.MARKDOWN
            ) 
            await schedule_delete(context, welc_msg, delay=60)
        except Exception: 
            pass
            
    except Exception as e: 
        await msg.reply_text(f"❌ Error: {e}")

async def cmd_approve_perm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): 
        return
        
    msg = update.message
    try: 
        m = re.search(r'(https?://t\.me/\+[a-zA-Z0-9_\-]+)', msg.text)
        if m:
            link = m.group(1)
        else:
            link = context.args[0].strip()
    except Exception: 
        await msg.reply_text("Usage: `/per <link>`")
        return
        
    ld = DB["LINK_MAP"].get(link)
    target_uid = None
    batch_id = None
    
    if isinstance(ld, dict):
        target_uid = ld.get("u")
        batch_id = ld.get("b")
    elif isinstance(ld, int):
        batch_id = ld
        if msg.message_thread_id:
            for u, t in DB["USER_TOPICS"].items():
                if t == msg.message_thread_id:
                    target_uid = int(u)
                    break
                    
    if not target_uid or not batch_id: 
        await msg.reply_text("❌ Link/User not found.")
        return
        
    try:
        await context.bot.approve_chat_join_request(batch_id, target_uid)
        
        if "demos" in DB["USER_DATA"].get(target_uid, {}) and str(batch_id) in DB["USER_DATA"][target_uid]["demos"]: 
            del DB["USER_DATA"][target_uid]["demos"][str(batch_id)]
            await save_data_async()
            
        await msg.reply_text(f"✅ **APPROVED (PERM)**\nUser `{target_uid}` -> Batch `{batch_id}`")
        
        try: 
            batch_name = DB['ALL_CHATS'].get(batch_id, 'Batch')
            welcome_str = DB['CUSTOM_WELCOMES'].get(batch_id, '')
            welc_msg = await context.bot.send_message(
                target_uid, 
                f"✅ **Approved Permanent!**\nWelcome to {batch_name}.\n\n{welcome_str}", 
                parse_mode=ParseMode.MARKDOWN
            ) 
            await schedule_delete(context, welc_msg, delay=60)
        except Exception: 
            pass
            
    except Exception as e: 
        await msg.reply_text(f"❌ Error: {e}")

async def cmd_user_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): 
        return
        
    try: 
        target_id = int(context.args[0])
    except Exception: 
        msg = await update.message.reply_text("Usage: /user [id]")
        await schedule_delete(context, update.message)
        await schedule_delete(context, msg)
        return
        
    msg = await update.message.reply_text("🔍 Scanning batches...")
    info = DB["USER_DATA"].get(target_id, {})
    
    r = f"USER DETAILS: {target_id}\nName: {info.get('name', 'Unknown')}\n\n"
    if target_id in DB['BLOCKED_USERS']:
        r += "🚫 BLOCKED\n\n"
        
    r += "--- MEMBERSHIP ---\n"
    found = False
    
    all_chats = set(list(DB["ALL_CHATS"].keys()) + list(DB["FREE_CHANNELS"].keys()) + list(DB["PAID_CHANNELS"].keys()))
    for cid in all_chats:
        try:
            m = await context.bot.get_chat_member(cid, target_id)
            if m.status in [ChatMember.MEMBER, ChatMember.ADMINISTRATOR, ChatMember.OWNER, ChatMember.RESTRICTED]: 
                r += f"{DB['ALL_CHATS'].get(cid, cid)}: ✅\n"
                found = True
        except Exception: 
            pass
            
    if not found: 
        r += "Not found in any batch.\n"
        
    if "demo_history" in info: 
        r += "\n--- DEMO HISTORY ---\n"
        for h in info["demo_history"]:
            r += f"• {h}\n"
            
    f = io.BytesIO(r.encode("utf-8"))
    f.name = f"scan_{target_id}.txt"
    
    await update.message.reply_document(document=f, caption="🔍 Deep Scan")
    await context.bot.delete_message(update.effective_chat.id, msg.message_id)
    await schedule_delete(context, update.message)

async def cmd_batches(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): 
        return
        
    msg = await update.message.reply_text("⏳ Compiling...")
    r = "ALL BATCHES\n" + "="*30 + "\n"
    
    all_chats = set(list(DB["ALL_CHATS"].keys()) + list(DB["FREE_CHANNELS"].keys()) + list(DB["PAID_CHANNELS"].keys()))
    for cid in all_chats: 
        r += f"{cid} | {DB['ALL_CHATS'].get(cid, 'Unknown')}\n"
        
    f = io.BytesIO(r.encode("utf-8"))
    f.name = "batches.txt"
    
    await update.message.reply_document(document=f)
    await context.bot.delete_message(update.effective_chat.id, msg.message_id)
    await schedule_delete(context, update.message)

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): 
        return
        
    storage = 'MongoDB ☁️' if MONGO_URL else 'Local 📁'
    lockdown = '🔴 ON' if not DB.get('NEW_USERS_ALLOWED', True) else '🟢 OFF'
    freelocked = '🔴 YES' if DB.get('FREE_LOCKED', False) else '🟢 NO'
    paidlocked = '🔴 YES' if DB.get('PAID_LOCKED', False) else '🟢 NO'
    testbotlocked = '🔴 YES' if DB.get('TEST_BOT_LOCKED', False) else '🟢 NO'
    
    t = (
        f"📊 **Statistics**\n"
        f"💾 Storage: {storage}\n"
        f"🔒 Lockdown: {lockdown}\n"
        f"🔓 Free Locked: {freelocked}\n"
        f"🔐 Paid Locked: {paidlocked}\n"
        f"🤖 Test Bot Locked: {testbotlocked}\n\n"
        f"👥 Users: {len(DB['USER_DATA'])}\n"
        f"🆓 Free: {len(DB['FREE_CHANNELS'])}\n"
        f"💎 Paid: {len(DB['PAID_CHANNELS'])}\n"
        f"🚫 Blocked: {len(DB['BLOCKED_USERS'])}"
    )
    
    msg = await update.message.reply_text(t, parse_mode=ParseMode.MARKDOWN)
    await schedule_delete(context, update.message)
    await schedule_delete(context, msg)

async def cmd_delbatch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): 
        return
        
    try: 
        t = context.args[0].lower()
        cid = int(context.args[1])
    except Exception: 
        msg = await update.message.reply_text("Usage: /delbatch [free/paid] [id]")
        await schedule_delete(context, update.message)
        await schedule_delete(context, msg)
        return
        
    d = DB["FREE_CHANNELS"] if t == "free" else DB["PAID_CHANNELS"]
    
    if cid in d: 
        del d[cid]
        await save_data_async()
        msg = await update.message.reply_text("✅ Deleted")
    else: 
        msg = await update.message.reply_text("❌ Not found")
        
    await schedule_delete(context, update.message)
    await schedule_delete(context, msg)

async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    
    if uid in BROADCAST_STATE:
        del BROADCAST_STATE[uid]
    if uid in ADMIN_WIZARD:
        del ADMIN_WIZARD[uid]
        
    msg = await update.message.reply_text("❌ Cancelled")
    await schedule_delete(context, update.message)
    await schedule_delete(context, msg)

async def cmd_addbatch_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): 
        return
        
    ADMIN_WIZARD[update.effective_user.id] = {"step": "ask_type"}
    
    kb = [
        [InlineKeyboardButton("Free", callback_data="wiz_free"), 
         InlineKeyboardButton("Paid", callback_data="wiz_paid")]
    ]
    
    msg = await update.message.reply_text("🆕 **Add Batch Wizard**", reply_markup=InlineKeyboardMarkup(kb))
    await schedule_delete(context, update.message)
    await schedule_delete(context, msg)

async def wizard_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = q.from_user.id
    
    if uid not in ADMIN_WIZARD: 
        await q.answer("Expired")
        return
        
    if q.data in ["wiz_free", "wiz_paid"]: 
        ADMIN_WIZARD[uid]["type"] = q.data.split('_')[1]
        ADMIN_WIZARD[uid]["step"] = "ask_id"
        
        await q.edit_message_text(
            f"➡️ Send **Channel ID** for {q.data.split('_')[1].upper()}:", 
            parse_mode=ParseMode.MARKDOWN
        )

async def wizard_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user:
        return False
        
    uid = update.effective_user.id
    
    if uid not in ADMIN_WIZARD: 
        return False
        
    state = ADMIN_WIZARD[uid]
    
    if state["step"] == "ask_id":
        try:
            cid = int(update.message.text)
            chat_info = await context.bot.get_chat(cid)
            cname = chat_info.title or f"Batch {cid}"
            
            if state["type"] == "free":
                DB["FREE_CHANNELS"][cid] = cname
            else:
                DB["PAID_CHANNELS"][cid] = cname
                
            DB["ALL_CHATS"][cid] = cname
            await save_data_async()
            
            msg = await update.message.reply_text(f"✅ **Added!**\n{cname} ({cid})", parse_mode=ParseMode.MARKDOWN)
            
            if state["type"] == "free":
                b_count = 0
                await msg.reply_text("📢 Sending Auto-Broadcast to all tracked channels...", parse_mode=ParseMode.MARKDOWN)
                
                for t_cid in list(DB["ALL_CHATS"].keys()):
                    if t_cid != cid: 
                        try:
                            sent_msg = await context.bot.send_message(
                                t_cid,
                                f"🎉 <b>NEW FREE BATCH ADDED!</b> 🎉\n\n📛 <b>Name:</b> {cname}\n\n👉 Go to the Bot Menu to join now!\n\nBatch available on @H4R_Contact_bot",
                                parse_mode=ParseMode.HTML
                            )
                            DB.setdefault("SCHEDULED_DELETES", []).append({
                                "c": t_cid,
                                "m": sent_msg.message_id,
                                "t": time.time() + 10800 
                            })
                            b_count += 1
                        except Exception: 
                            pass
                
                await msg.reply_text(f"✅ Broadcast sent to {b_count} chats. It will auto-delete after 3 hours.")
                await save_data_async()

            del ADMIN_WIZARD[uid]
        except Exception: 
            msg = await update.message.reply_text("❌ Error. Ensure Bot is Admin and ID is valid.")
            
        await schedule_delete(context, update.message)
        await schedule_delete(context, msg)
        return True
        
    return False

async def cmd_broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): 
        return
        
    BROADCAST_STATE[update.effective_user.id] = {"type": "broadcast", "step": "wait_msg"}
    
    msg = await update.message.reply_text("📢 **Broadcast Mode**\nSend msg. /cancel to stop.")
    await schedule_delete(context, update.message)
    await schedule_delete(context, msg)

async def cmd_post_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): 
        return
        
    BROADCAST_STATE[update.effective_user.id] = {"type": "post", "step": "wait_msg"}
    
    msg = await update.message.reply_text("📝 **Post Mode**\nSend msg. /cancel to stop.")
    await schedule_delete(context, update.message)
    await schedule_delete(context, msg)

async def handle_broadcast_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user:
        return False
        
    uid = update.effective_user.id
    
    if uid not in BROADCAST_STATE: 
        return False
        
    state = BROADCAST_STATE[uid]
    
    if state["step"] == "wait_msg":
        state["content"] = update.message
        state["step"] = "confirm"
        
        kb = [
            [InlineKeyboardButton("✅ YES", callback_data="bc_yes"), 
             InlineKeyboardButton("❌ NO", callback_data="bc_no")]
        ]
        
        await update.message.reply_text("📢 **Confirm?**", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
        return True
        
    return False

async def broadcast_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = q.from_user.id
    
    if uid not in BROADCAST_STATE: 
        await q.answer("Expired")
        return
        
    if q.data == "bc_no": 
        del BROADCAST_STATE[uid]
        await q.edit_message_text("❌ Cancelled")
        return
        
    if q.data == "bc_yes":
        await q.edit_message_text("⏳ Processing...")
        count = 0
        
        if BROADCAST_STATE[uid]["type"] == "broadcast":
            targets = list(DB["USER_DATA"].keys())
        else:
            targets = list(DB["FREE_CHANNELS"].keys()) + list(DB["PAID_CHANNELS"].keys())
            
        for tid in targets:
            try: 
                await context.bot.copy_message(tid, uid, BROADCAST_STATE[uid]["content"].message_id)
                count += 1
                await asyncio.sleep(0.05)
            except Exception: 
                pass
                
        await context.bot.send_message(uid, f"✅ Done. Sent to {count}.")
        del BROADCAST_STATE[uid]

async def handle_reaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message_reaction: 
        return
        
    r = update.message_reaction
    
    if (r.chat.id, r.message_id) in MESSAGE_MAP:
        tc, tm = MESSAGE_MAP[(r.chat.id, r.message_id)]
        try: 
            await context.bot.set_message_reaction(tc, tm, reaction=r.new_reaction)
        except Exception: 
            pass

async def handle_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.edited_message: 
        return
        
    m = update.edited_message
    key = (m.chat.id, m.message_id)
    
    if key in MESSAGE_MAP:
        tc, tm = MESSAGE_MAP[key]
        try:
            if not m.photo and not m.video and not m.document and not m.audio and not m.animation:
                if m.text:
                    await context.bot.edit_message_text(m.text, chat_id=tc, message_id=tm, entities=m.entities)
            else:
                new_media = None
                if m.photo:
                    new_media = InputMediaPhoto(m.photo[-1].file_id, caption=m.caption, caption_entities=m.caption_entities)
                elif m.video:
                    new_media = InputMediaVideo(m.video.file_id, caption=m.caption, caption_entities=m.caption_entities)
                elif m.document:
                    new_media = InputMediaDocument(m.document.file_id, caption=m.caption, caption_entities=m.caption_entities)
                elif m.audio:
                    new_media = InputMediaAudio(m.audio.file_id, caption=m.caption, caption_entities=m.caption_entities)
                elif m.animation:
                    new_media = InputMediaAnimation(m.animation.file_id, caption=m.caption, caption_entities=m.caption_entities)
                
                if new_media:
                    await context.bot.edit_message_media(chat_id=tc, message_id=tm, media=new_media)
                elif m.caption is not None:
                    await context.bot.edit_message_caption(chat_id=tc, message_id=tm, caption=m.caption, caption_entities=m.caption_entities)
        except Exception as e: 
            logger.error(f"Edit Sync Error: {e}")

async def main_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    
    if not user:
        return
        
    if check_spam(user.id):
        return
        
    if chat.type in [ChatType.GROUP, ChatType.SUPERGROUP, ChatType.CHANNEL]:
        if chat.id not in DB["ALL_CHATS"]: 
            DB["ALL_CHATS"][chat.id] = chat.title or f"Chat {chat.id}"
            await save_data_async()
            
    if user.id not in DB["BLOCKED_USERS"]:
        if await wizard_message(update, context):
            return
            
        if await handle_broadcast_flow(update, context):
            return

    if chat.type == ChatType.PRIVATE:
        topic_id = await get_or_create_topic(user, context)
        if topic_id:
            reply_id = None
            if update.message.reply_to_message:
                reply_key = (chat.id, update.message.reply_to_message.message_id)
                if reply_key in MESSAGE_MAP:
                    _, reply_id = MESSAGE_MAP[reply_key]
                    
            try:
                sent = await context.bot.copy_message(
                    SUPPORT_GROUP_ID, 
                    chat.id, 
                    update.message.id, 
                    message_thread_id=topic_id, 
                    reply_to_message_id=reply_id
                )
                MESSAGE_MAP[(chat.id, update.message.id)] = (SUPPORT_GROUP_ID, sent.message_id)
                MESSAGE_MAP[(SUPPORT_GROUP_ID, sent.message_id)] = (chat.id, update.message.id)
            except Exception as e:
                if "thread not found" in str(e).lower():
                    if user.id in DB["USER_TOPICS"]:
                        del DB["USER_TOPICS"][user.id]
                    topic_id = await get_or_create_topic(user, context)
                    if topic_id:
                        try:
                            sent = await context.bot.copy_message(
                                SUPPORT_GROUP_ID, 
                                chat.id, 
                                update.message.id, 
                                message_thread_id=topic_id, 
                                reply_to_message_id=reply_id
                            )
                            MESSAGE_MAP[(chat.id, update.message.id)] = (SUPPORT_GROUP_ID, sent.message_id)
                            MESSAGE_MAP[(SUPPORT_GROUP_ID, sent.message_id)] = (chat.id, update.message.id)
                        except Exception: 
                            pass
                            
    elif chat.id == SUPPORT_GROUP_ID and update.message.message_thread_id:
        if update.message.from_user.id == context.bot.id: 
            return
            
        topic_id = update.message.message_thread_id
        target_uid = None
        for u, t in DB["USER_TOPICS"].items():
            if t == topic_id:
                target_uid = int(u)
                break
                
        if target_uid:
            reply_id = None
            if update.message.reply_to_message:
                reply_key = (SUPPORT_GROUP_ID, update.message.reply_to_message.message_id)
                if reply_key in MESSAGE_MAP:
                    _, reply_id = MESSAGE_MAP[reply_key]
                    
            try:
                sent = await context.bot.copy_message(
                    target_uid, 
                    chat.id, 
                    update.message.id, 
                    reply_to_message_id=reply_id
                )
                MESSAGE_MAP[(SUPPORT_GROUP_ID, update.message.id)] = (target_uid, sent.message_id)
                MESSAGE_MAP[(target_uid, sent.message_id)] = (SUPPORT_GROUP_ID, update.message.id)
            except Forbidden: 
                await context.bot.send_message(
                    SUPPORT_GROUP_ID, 
                    "❌ User has blocked the bot.", 
                    message_thread_id=update.message.message_thread_id
                )
            except Exception: 
                pass

async def on_chat_member_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    result = update.chat_member
    if not result: 
        return
        
    chat = result.chat
    user = result.new_chat_member.user
    status = result.new_chat_member.status
    
    chat_id_clean = str(chat.id).replace("-100", "")
    man_id_clean = str(MANDATORY_CHANNEL_ID).replace("-100", "")
    
    if chat_id_clean == man_id_clean and status in [ChatMember.LEFT, ChatMember.BANNED]:
        logger.info(f"🚪 User {user.id} left the Mandatory Channel. Auto-Kicking Unconditionally...")
        await execute_universal_kick(user.id, context, permanent_ban=True) 

async def handle_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    req = update.chat_join_request
    chat = req.chat
    user = req.from_user
    
    if user.id in DB["BLOCKED_USERS"]:
        try: 
            await context.bot.decline_chat_join_request(chat.id, user.id)
        except Exception: 
            pass
        return
    
    if chat.id in DB["FREE_CHANNELS"]:
        if await check_membership(user.id, context):
            try:
                await context.bot.approve_chat_join_request(chat.id, user.id)
                
                welcome_str = DB["CUSTOM_WELCOMES"].get(chat.id, f"✅ **Approved!**\nWelcome to {chat.title}")
                w_msg = await context.bot.send_message(user.id, welcome_str, parse_mode=ParseMode.MARKDOWN)
                await schedule_delete(context, w_msg, delay=60)
            except Exception: 
                pass
        else:
            try: 
                await context.bot.send_message(
                    user.id, 
                    f"⚠️ **Declined!**\nJoin Main:\n{MANDATORY_CHANNEL_LINK}", 
                    parse_mode=ParseMode.MARKDOWN
                )
                await context.bot.decline_chat_join_request(chat.id, user.id)
            except Exception: 
                pass
                
    elif chat.id in DB["PAID_CHANNELS"]:
        if req.invite_link and req.invite_link.invite_link in DB["LINK_MAP"]:
            try: 
                await context.bot.revoke_chat_invite_link(chat.id, req.invite_link.invite_link)
            except Exception: 
                pass

async def background_sync(context: ContextTypes.DEFAULT_TYPE):
    logger.info("🔄 Starting Background User Sync...")
    
    for uid in list(DB["USER_DATA"].keys()):
        user_id = int(uid)
        
        if user_id in DB["BLOCKED_USERS"]: continue
        if is_admin(user_id): continue
        
        try:
            m = await context.bot.get_chat_member(MANDATORY_CHANNEL_ID, user_id)
            status = m.status
        except BadRequest as e:
            if "User not found" in str(e) or "chat not found" in str(e) or "user not found" in str(e).lower():
                status = ChatMember.LEFT
            else:
                continue 
        except Exception:
            continue 
            
        if status in [ChatMember.LEFT, ChatMember.BANNED]:
            logger.info(f"🚫 Background Sync: Kicking {user_id} (Left Main Channel)")
            await execute_universal_kick(user_id, context, permanent_ban=True)
                
        await asyncio.sleep(0.5)
        
    logger.info("✅ Background Sync Complete.")

async def cmd_sync(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    msg = await update.message.reply_text("🔄 Background sync started manually. Check terminal logs.")
    context.job_queue.run_once(background_sync, 1)
    await schedule_delete(context, update.message)
    await schedule_delete(context, msg)

async def check_demos(context: ContextTypes.DEFAULT_TYPE):
    now = time.time()
    mod = False
    
    for uid, data in list(DB["USER_DATA"].items()):
        if not data.get("demos"): 
            continue
            
        for bid, d_data in data["demos"].copy().items():
            if isinstance(d_data, dict):
                expiry = d_data["expiry"]
                warned = d_data.get("warned", False)
            else:
                expiry = float(d_data)
                warned = False
                data["demos"][bid] = {"expiry": expiry, "warned": False}
                mod = True

            chat_id = int(bid)
            user_id = int(uid)
            
            if now > expiry:
                logger.info(f"⏳ Processing Demo Expiry: User {user_id} in Batch {chat_id}")
                
                try:
                    await context.bot.ban_chat_member(chat_id, user_id)
                    logger.info(f"✅ User {user_id} kicked from {chat_id}")
                    await context.bot.unban_chat_member(chat_id, user_id)
                    try: 
                        await context.bot.send_message(user_id, "⏰ **Demo Ended.**\nHope you enjoyed! Contact Admin for permanent access.")
                    except Exception: 
                        pass 

                except Exception as e: 
                    logger.error(f"❌ KICK FAILED for {user_id} in {chat_id}: {e}")
                    if LOG_CHANNEL_ID:
                        try:
                            err_msg = (
                                f"⚠️ **DEMO KICK FAILED**\n"
                                f"👤 User: `{user_id}`\n"
                                f"🆔 Batch: `{chat_id}`\n"
                                f"❓ Reason: `{e}`\n"
                                f"ℹ️ *Make sure Bot is Admin with Ban rights!*"
                            )
                            await context.bot.send_message(LOG_CHANNEL_ID, err_msg, parse_mode=ParseMode.MARKDOWN)
                        except Exception: pass
                    
                if bid in data["demos"]:
                    del data["demos"][bid]
                    mod = True
                    
            elif (expiry - now) <= 1800 and not warned:
                try: 
                    batch_name = DB['ALL_CHATS'].get(chat_id, 'Batch')
                    await context.bot.send_message(
                        user_id, 
                        f"⏳ **Reminder:** Demo for **{batch_name}** expires in <30 mins!"
                    )
                    data["demos"][bid]["warned"] = True
                    mod = True
                except Exception: 
                    pass
                    
    if "SCHEDULED_DELETES" in DB and DB["SCHEDULED_DELETES"]:
        surviving = []
        for item in DB["SCHEDULED_DELETES"]:
            if now > item["t"]:
                try:
                    await context.bot.delete_message(item["c"], item["m"])
                    logger.info(f"✅ Auto-deleted scheduled broadcast in chat {item['c']}")
                    mod = True
                except Exception as e: 
                    logger.error(f"❌ Failed to delete scheduled msg in {item['c']}: {e}")
                    mod = True 
            else:
                surviving.append(item)
                
        if len(surviving) != len(DB["SCHEDULED_DELETES"]):
            DB["SCHEDULED_DELETES"] = surviving
            mod = True

    if mod: 
        await save_data_async()

async def general_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = q.from_user.id
    data = q.data
    
    if uid in DB["BLOCKED_USERS"]: 
        await q.answer("🚫 You are banned from joining batches.", show_alert=True)
        return
        
    if check_spam(uid): 
        await q.answer("⏳ Wait...", show_alert=False)
        return
        
    if data.startswith("wiz_"): 
        await wizard_callback(update, context)
        return
        
    if data.startswith("bc_"): 
        await broadcast_callback(update, context)
        return

    # NEW: Test Bot Validation Logic & Lock Check
    if data == "test_bot":
        if DB.get("TEST_BOT_LOCKED", False):
            await q.answer("🔒 Locked by Admin.", show_alert=True)
            return
            
        if not await check_membership(uid, context):
            await q.answer("❌ Join Main Channel First!", show_alert=True)
            return
            
        test_link = DB.get("TEST_BOT_LINK")
        if not test_link:
            await q.answer("⚠️ Test Bot is not setup by Admin yet!", show_alert=True)
            return
            
        await q.answer("Verifying & Generating Link...")
        kb = [[InlineKeyboardButton("🔗 Open Test Bot", url=test_link)]]
        try:
            sent_msg = await context.bot.send_message(
                uid, 
                "🤖 **Test Bot Access Verification:**\n\nYou are verified! Click the button below to open the Test Bot.", 
                reply_markup=InlineKeyboardMarkup(kb), 
                parse_mode=ParseMode.MARKDOWN
            )
            await schedule_delete(context, sent_msg, delay=60)
        except Exception:
            pass
        return

    if data == "verify":
        if await check_membership(uid, context): 
            await q.answer("✅ Verified!")
            await show_user_menu(update)
        else: 
            if not DB.get("NEW_USERS_ALLOWED", True):
                await q.answer("⛔ Entry Closed.", show_alert=True)
            else:
                await q.answer("❌ Join Main Channel First!", show_alert=True)
                
    elif data == "u_main": 
        await q.answer()
        await show_user_menu(update)
        
    elif data == "u_free":
        if DB.get("FREE_LOCKED", False): 
            await q.answer("🔒 Locked.", show_alert=True)
            return
            
        if not DB["FREE_CHANNELS"]: 
            await q.answer("Empty", show_alert=True)
            return
            
        await q.answer()
        kb = []
        for i, n in DB["FREE_CHANNELS"].items():
            kb.append([InlineKeyboardButton(f"🔗 {n}", callback_data=f"get_f_{i}")])
        kb.append([InlineKeyboardButton("🔙 Back", callback_data="u_main")])
        
        try: 
            await q.edit_message_text("📂 **Free Batches:**", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
        except Exception: 
            pass
            
    elif data == "u_paid":
        if DB.get("PAID_LOCKED", False): 
            await q.answer("🔒 Locked.", show_alert=True)
            return
            
        if not DB["PAID_CHANNELS"]: 
            await q.answer("Empty", show_alert=True)
            return
            
        await q.answer()
        kb = []
        for i, n in DB["PAID_CHANNELS"].items():
            kb.append([InlineKeyboardButton(f"💎 {n}", callback_data=f"view_p_{i}")])
        kb.append([InlineKeyboardButton("🔙 Back", callback_data="u_main")])
        
        try: 
            await q.edit_message_text("💎 **Premium Batches:**", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
        except Exception: 
            pass
            
    elif data == "my_info": 
        await q.answer()
        await cmd_myinfo(update, context)
        return
    
    elif data.startswith("get_f_"):
        cid = int(data.split("_")[2])
        
        if await is_already_in_channel(context, cid, uid): 
            await q.answer("⚠️ Already Joined!", show_alert=True)
            return
            
        try:
            bname = DB["ALL_CHATS"].get(cid, f"Batch {cid}")
            l = await context.bot.create_chat_invite_link(
                cid, 
                creates_join_request=True, 
                name=f"Free-{uid}", 
                expire_date=int(time.time())+60
            )
            
            msg_text = (
                f"🔗 <b>Link:</b>\n\n"
                f"<b>{bname}</b>\n\n"
                f"{l.invite_link}\n\n"
                f"ℹ️ <i>Request auto-approved.</i>\n"
                f"⏳ <i>(Expires in 1 min)</i>"
            )
            sent_msg = await context.bot.send_message(uid, msg_text, parse_mode=ParseMode.HTML)
            await schedule_delete(context, sent_msg, delay=60)
            await q.answer("Sent to DM")
            
        except Exception as e: 
            await q.answer(f"Bot Error: {e}", show_alert=True)

    elif data.startswith("view_p_"):
        cid = int(data.split("_")[2])
        await q.answer()
        
        kb = [
            [InlineKeyboardButton("🔗 Request Access", callback_data=f"req_access_{cid}")], 
            [InlineKeyboardButton("🔙 Back", callback_data="u_paid")]
        ]
        
        try: 
            await q.edit_message_text(
                "💎 **Premium Access:**\nClick below.", 
                reply_markup=InlineKeyboardMarkup(kb), 
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception: 
            pass

    elif data.startswith("req_access_"):
        cid = int(data.split("_")[2])
        
        if not await check_membership(uid, context): 
            await q.answer("❌ Join Main First!", show_alert=True)
            return
            
        if await is_already_in_channel(context, cid, uid): 
            await q.answer("⚠️ Already joined!", show_alert=True)
            return
            
        await q.answer("🔄 Generating Link...")
        
        try:
            bname = DB["ALL_CHATS"].get(cid, f"Batch {cid}")
            l = await context.bot.create_chat_invite_link(
                cid, 
                creates_join_request=True, 
                name=f"Req-{uid}", 
                expire_date=int(time.time())+60
            )
            
            DB["LINK_MAP"][l.invite_link] = {"u": uid, "b": cid}
            await save_data_async()
            
            topic_id = await get_or_create_topic(update.effective_user, context)
            if topic_id:
                admin_msg = (
                    f"🔔 **NEW REQUEST**\n"
                    f"👤 User: {update.effective_user.mention_html()}\n"
                    f"📂 Batch: <b>{bname}</b>\n"
                    f"🔗 Link: {l.invite_link}\n\n"
                    f"👇 **Action:**\n"
                    f"/demo {l.invite_link}\n"
                    f"/per {l.invite_link}"
                )
                try: 
                    await context.bot.send_message(
                        SUPPORT_GROUP_ID, 
                        admin_msg, 
                        message_thread_id=topic_id, 
                        parse_mode=ParseMode.HTML
                    )
                except Exception: 
                    pass
                    
            user_msg = (
                f"✅ <b>Access Link Generated!</b>\n\n"
                f"<b>{bname}</b>\n\n"
                f"🔗 {l.invite_link}\n\n"
                f"ℹ️ <b>Sent to Admin.</b> Wait for approval.\n"
                f"⏳ <i>(Expires in 1 min)</i>"
            )
            user_msg_obj = await context.bot.send_message(uid, user_msg, parse_mode=ParseMode.HTML)
            await schedule_delete(context, user_msg_obj, delay=60)
            
        except Exception as e: 
            await context.bot.send_message(uid, f"❌ Error: {e}")

async def show_user_menu(update: Update):
    kb = [
        [InlineKeyboardButton("📂 Free Batches", callback_data="u_free"), 
         InlineKeyboardButton("💎 Paid Batches", callback_data="u_paid")],
        [InlineKeyboardButton("🤖 Test Bot", callback_data="test_bot")], 
        [InlineKeyboardButton("🆘 Support", url=f"tg://user?id={SUPPORT_GROUP_ID}")],
        [InlineKeyboardButton("ℹ️ My Info", callback_data="my_info")]
    ]
    txt = "👋 **Welcome!**"
    
    if update.callback_query: 
        try: 
            await update.callback_query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
        except Exception: 
            pass
    else: 
        await update.message.reply_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    await set_role_based_commands(user.id, context)
    
    if user.id not in DB["USER_DATA"]: 
        DB["USER_DATA"][user.id] = {"name": user.full_name, "username": user.username, "joined_at": time.time(), "demos": {}}
        await save_data_async()
        
    await get_or_create_topic(user, context)
    
    if user.id in DB["BLOCKED_USERS"]: 
        await update.message.reply_text("🚫 You are banned from joining batches, but you can leave a message for support here if needed.", parse_mode=ParseMode.MARKDOWN)
        return
        
    if is_admin(user.id): 
        admin_text = (
            f"👑 **WELCOME ADMIN!**\n"
            f"**🛠 Manage:** `/del`, `/find`, `/ban`, `/unban`, `/kick`, `/extend`, `/lockdown`, `/lockfree`, `/lockpaid`, `/sync`\n"
            f"**✅ Approve:** `/demo <link>`, `/per <link>`\n"
            f"**📊 Tools:** `/stats`, `/batchstats`\n"
            f"**📢 Broadcast:** `/broadcast`, `/post`, `/setwelcome`, `/settestbot`, `/locktestbot`"
        )
        await update.message.reply_text(admin_text, parse_mode=ParseMode.MARKDOWN)
        
    elif await check_membership(user.id, context): 
        await show_user_menu(update)
        
    else:
        if not DB.get("NEW_USERS_ALLOWED", True): 
            await update.message.reply_text("⛔ **Entry Closed!**", parse_mode=ParseMode.MARKDOWN)
            return
            
        kb = [
            [InlineKeyboardButton("📢 Join Channel", url=MANDATORY_CHANNEL_LINK)], 
            [InlineKeyboardButton("✅ Verified", callback_data="verify")]
        ]
        await update.message.reply_text("⚠️ **Join Main Channel First**", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

def main():
    load_data()
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler("del", cmd_del_msg))
    app.add_handler(MessageHandler(filters.Regex(r"^/id(@\w+)?$") & filters.ChatType.CHANNEL, cmd_id))
    
    app.add_handler(CommandHandler("addadmin", cmd_add_admin))
    app.add_handler(CommandHandler("deladmin", cmd_del_admin))
    app.add_handler(CommandHandler("backup", cmd_backup))
    app.add_handler(CommandHandler("allusers", cmd_all_users))
    
    app.add_handler(CommandHandler("ban", cmd_ban))
    app.add_handler(CommandHandler("unban", cmd_unban))
    app.add_handler(CommandHandler("find", cmd_find_user))
    app.add_handler(CommandHandler("extend", cmd_extend_demo))
    app.add_handler(CommandHandler("kick", cmd_kick_user))
    app.add_handler(CommandHandler("myinfo", cmd_myinfo))
    
    app.add_handler(CommandHandler("batchstats", cmd_batch_stats))
    app.add_handler(CommandHandler("setwelcome", cmd_set_welcome))
    app.add_handler(CommandHandler("settestbot", cmd_set_testbot))
    app.add_handler(CommandHandler("locktestbot", cmd_locktestbot)) # NEW COMMAND
    app.add_handler(CommandHandler("lockdown", cmd_lockdown))
    app.add_handler(CommandHandler("lockfree", cmd_lockfree))
    app.add_handler(CommandHandler("lockpaid", cmd_lockpaid))
    app.add_handler(CommandHandler("sync", cmd_sync))
    app.add_handler(CommandHandler("joinall", cmd_joinall))
    
    app.add_handler(CommandHandler("demo", cmd_approve_demo))
    app.add_handler(CommandHandler("per", cmd_approve_perm))
    
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("user", cmd_user_details))
    app.add_handler(CommandHandler("batches", cmd_batches))
    app.add_handler(CommandHandler("addbatch", cmd_addbatch_start))
    app.add_handler(CommandHandler("delbatch", cmd_delbatch))
    app.add_handler(CommandHandler("broadcast", cmd_broadcast_start))
    app.add_handler(CommandHandler("post", cmd_post_start))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("clear", cmd_clear))
    
    app.add_handler(CallbackQueryHandler(general_callback))
    app.add_handler(ChatJoinRequestHandler(handle_join_request))
    
    app.add_handler(ChatMemberHandler(on_chat_member_update, ChatMemberHandler.CHAT_MEMBER))
    app.add_handler(ChatMemberHandler(track_chats, ChatMemberHandler.MY_CHAT_MEMBER))
    
    app.add_handler(MessageReactionHandler(handle_reaction))
    app.add_handler(MessageHandler(filters.UpdateType.EDITED_MESSAGE, handle_edit))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, main_message_handler))
    
    if app.job_queue: 
        app.job_queue.run_repeating(check_demos, interval=60, first=10)
        app.job_queue.run_repeating(background_sync, interval=600, first=30)
    
    print("Bot v33.0 (Test Bot Lockdown Added) Started...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()

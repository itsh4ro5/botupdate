import os, json, asyncio, logging, time
from telegram import ChatMember
from telegram.constants import ParseMode

# --- LOGGING SETUP ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- CONFIGURATION & DEFAULTS ---
DEFAULTS = {"TOKEN": "", "OWNER": 0, "SUPPORT": 0, "MAIN_CH": 0, "LOG_CH": 0}

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

DEFAULT_CATEGORIES = ["Civil Engg.", "Electrical Engg.", "Mechanical Engg.", "Electronics Engg.", "CSE", "Competition (Math, Reas, GK/GS)", "Skill Improvement", "Other Batches"]

# --- DATABASE & MEMORY ---
DB = {
    "ADMIN_IDS": [], "FREE_CHANNELS": {}, "PAID_CHANNELS": {}, "ALL_CHATS": {},     
    "USER_DATA": {}, "BLOCKED_USERS": [], "USER_TOPICS": {}, "PENDING_REQUESTS": {},
    "LINK_MAP": {}, "CUSTOM_WELCOMES": {}, "NEW_USERS_ALLOWED": True, 
    "FREE_LOCKED": False, "PAID_LOCKED": False, "TEST_BOT_LOCKED": False, 
    "SCHEDULED_DELETES": [], "TEST_BOT_LINK": "", "BATCH_CATEGORIES": {},
    "CATEGORIES": DEFAULT_CATEGORIES.copy(), "MAINTENANCE_MODE": False
}

MESSAGE_MAP = {} 
ADMIN_WIZARD = {} 
BROADCAST_STATE = {} 
TOPIC_CREATION_LOCK = set()
SPAM_CACHE = {} 
data_lock = asyncio.Lock()
mongo_client = mongo_collection = None

# --- MONGODB SETUP ---
if MONGO_URL:
    try:
        from pymongo import MongoClient
        import certifi
        mongo_client = MongoClient(MONGO_URL, tlsCAFile=certifi.where())
        mongo_db = mongo_client.get_database("telegram_bot_db")
        mongo_collection = mongo_db.get_collection("bot_settings")
        logger.info("✅ Connected to MongoDB Atlas")
    except Exception as e:
        logger.error(f"❌ MongoDB Connection Failed: {e}"); MONGO_URL = None

# --- DATABASE FUNCTIONS ---
def load_data():
    global DB
    # ✅ FIX: Yahan sari nayi keys add kar di gayi hain, jisme USERBOT_SESSION aur USERBOT_PHONE bhi shamil hain
    keys_to_load = [
        "LINK_MAP", "NEW_USERS_ALLOWED", "FREE_LOCKED", "PAID_LOCKED", "TEST_BOT_LOCKED", 
        "SCHEDULED_DELETES", "TEST_BOT_LINK", "MAINTENANCE_MODE", "CATEGORIES", "BATCH_CATEGORIES", 
        "USERBOT_SESSION", "USERBOT_PHONE"
    ]
    
    if MONGO_URL and mongo_collection is not None:
        try:
            data = mongo_collection.find_one({"_id": "main_settings"})
            if data and "data" in data:
                loaded = data["data"]
                if "ADMIN_IDS" in loaded: DB["ADMIN_IDS"] = [int(x) for x in loaded["ADMIN_IDS"] if str(x).isdigit()]
                if "BLOCKED_USERS" in loaded: DB["BLOCKED_USERS"] = loaded["BLOCKED_USERS"]
                
                for k in keys_to_load:
                    if k in loaded: DB[k] = loaded[k]
                    
                for k in ["CUSTOM_WELCOMES", "FREE_CHANNELS", "PAID_CHANNELS", "ALL_CHATS", "USER_TOPICS", "USER_DATA", "PENDING_REQUESTS"]:
                    if k in loaded: DB[k] = {int(i): v for i, v in loaded[k].items()}
                if OWNER_ID not in DB["ADMIN_IDS"]: DB["ADMIN_IDS"].append(OWNER_ID)
                for cid, name in DB["FREE_CHANNELS"].items(): DB["ALL_CHATS"][cid] = name
                for cid, name in DB["PAID_CHANNELS"].items(): DB["ALL_CHATS"][cid] = name
                return
        except Exception as e: logger.error(f"MongoDB Load Error: {e}")

    if not os.path.exists(DATA_FILE): save_data_sync(); return
    try:
        with open(DATA_FILE, "r") as f:
            loaded = json.load(f)
            if "ADMIN_IDS" in loaded: DB["ADMIN_IDS"] = [int(x) for x in loaded["ADMIN_IDS"] if str(x).isdigit()]
            if "BLOCKED_USERS" in loaded: DB["BLOCKED_USERS"] = loaded["BLOCKED_USERS"]
            
            for k in keys_to_load:
                if k in loaded: DB[k] = loaded[k]
                
            for k in ["CUSTOM_WELCOMES", "FREE_CHANNELS", "PAID_CHANNELS", "ALL_CHATS", "USER_TOPICS", "USER_DATA", "PENDING_REQUESTS"]:
                if k in loaded: DB[k] = {int(i): v for i, v in loaded[k].items()}
            if OWNER_ID not in DB["ADMIN_IDS"]: DB["ADMIN_IDS"].append(OWNER_ID)
            for cid, name in DB["FREE_CHANNELS"].items(): DB["ALL_CHATS"][cid] = name
            for cid, name in DB["PAID_CHANNELS"].items(): DB["ALL_CHATS"][cid] = name
    except Exception as e: logger.error(f"Local Load Error: {e}")


def save_data_sync():
    try:
        to_save = {
            "ADMIN_IDS": DB["ADMIN_IDS"], "BLOCKED_USERS": DB["BLOCKED_USERS"],
            "NEW_USERS_ALLOWED": DB.get("NEW_USERS_ALLOWED", True), "FREE_LOCKED": DB.get("FREE_LOCKED", False),
            "PAID_LOCKED": DB.get("PAID_LOCKED", False), "TEST_BOT_LOCKED": DB.get("TEST_BOT_LOCKED", False),
            "LINK_MAP": DB["LINK_MAP"], "SCHEDULED_DELETES": DB.get("SCHEDULED_DELETES", []),
            "TEST_BOT_LINK": DB.get("TEST_BOT_LINK", ""), 
            "CATEGORIES": DB.get("CATEGORIES", DEFAULT_CATEGORIES),
            "BATCH_CATEGORIES": {str(k): v for k, v in DB.get("BATCH_CATEGORIES", {}).items()},
            "MAINTENANCE_MODE": DB.get("MAINTENANCE_MODE", False),
            
            # ✅ FIX: Yahan Database aur JSON file me Session ko permanent save karne ke liye commands lagaye gaye hain
            "USERBOT_SESSION": DB.get("USERBOT_SESSION"), 
            "USERBOT_PHONE": DB.get("USERBOT_PHONE"),
            
            "CUSTOM_WELCOMES": {str(k): v for k, v in DB["CUSTOM_WELCOMES"].items()},
            "FREE_CHANNELS": {str(k): v for k, v in DB["FREE_CHANNELS"].items()},
            "PAID_CHANNELS": {str(k): v for k, v in DB["PAID_CHANNELS"].items()},
            "ALL_CHATS": {str(k): v for k, v in DB["ALL_CHATS"].items()},
            "USER_DATA": {str(k): v for k, v in DB["USER_DATA"].items()},
            "USER_TOPICS": {str(k): v for k, v in DB["USER_TOPICS"].items()},
            "PENDING_REQUESTS": {str(k): v for k, v in DB["PENDING_REQUESTS"].items()}
        }
        if MONGO_URL and mongo_collection is not None:
            try: mongo_collection.replace_one({"_id": "main_settings"}, {"_id": "main_settings", "data": to_save}, upsert=True)
            except Exception as e: logger.error(f"MongoDB Save Error: {e}")
        with open(DATA_FILE, "w") as f: json.dump(to_save, f, indent=4)
    except Exception as e: logger.error(f"Save Error: {e}")

async def save_data_async():
    async with data_lock: await asyncio.to_thread(save_data_sync)

# --- CORE HELPERS ---
async def execute_universal_kick(user_id, context, permanent_ban=False):
    mod = False
    for bid in list(DB["FREE_CHANNELS"].keys()):
        try:
            await context.bot.ban_chat_member(int(bid), user_id)
            if not permanent_ban: await context.bot.unban_chat_member(int(bid), user_id)
        except Exception: pass
    for bid in list(DB["PAID_CHANNELS"].keys()):
        try:
            bid_str = str(bid); is_demo = False
            if user_id in DB["USER_DATA"] and "demos" in DB["USER_DATA"][user_id] and bid_str in DB["USER_DATA"][user_id]["demos"]: is_demo = True
            if permanent_ban:
                await context.bot.ban_chat_member(int(bid), user_id)
                if is_demo: del DB["USER_DATA"][user_id]["demos"][bid_str]; mod = True
            else:
                if is_demo:
                    await context.bot.ban_chat_member(int(bid), user_id); await context.bot.unban_chat_member(int(bid), user_id)
                    del DB["USER_DATA"][user_id]["demos"][bid_str]; mod = True
        except Exception: pass
    if user_id not in DB["BLOCKED_USERS"]: DB["BLOCKED_USERS"].append(user_id); mod = True
    if mod: await save_data_async()

def is_admin(uid):
    if str(uid) == str(OWNER_ID): return True
    if uid in DB["ADMIN_IDS"]: return True
    for admin_id in DB["ADMIN_IDS"]:
        if str(admin_id) == str(uid): return True
    return False

def check_spam(uid):
    now = time.time(); last = SPAM_CACHE.get(uid, 0)
    SPAM_CACHE[uid] = now
    return True if now - last < 1.5 else False

async def check_membership(user_id, context):
    if is_admin(user_id): return True
    if not MANDATORY_CHANNEL_ID: return True
    try:
        m = await context.bot.get_chat_member(MANDATORY_CHANNEL_ID, user_id)
        return m.status in [ChatMember.MEMBER, ChatMember.ADMINISTRATOR, ChatMember.OWNER]
    except Exception: return False

async def is_already_in_channel(context, chat_id, user_id):
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        return member.status in [ChatMember.MEMBER, ChatMember.ADMINISTRATOR, ChatMember.OWNER]
    except Exception: return False

async def delete_later(context):
    try: await context.bot.delete_message(chat_id=context.job.data['chat_id'], message_id=context.job.data['msg_id'])
    except Exception: pass

async def schedule_delete(context, message, delay=1200):
    if message: context.job_queue.run_once(delete_later, delay, data={'chat_id': message.chat.id, 'msg_id': message.message_id})

async def get_or_create_topic(user, context):
    if not SUPPORT_GROUP_ID: return None
    if user.id in DB["USER_TOPICS"]: return DB["USER_TOPICS"][user.id]
    if user.id in TOPIC_CREATION_LOCK:
        await asyncio.sleep(1) 
        if user.id in DB["USER_TOPICS"]: return DB["USER_TOPICS"][user.id]
    TOPIC_CREATION_LOCK.add(user.id)
    try:
        topic = await context.bot.create_forum_topic(SUPPORT_GROUP_ID, f"{user.first_name[:20]} ({user.id})")
        DB["USER_TOPICS"][user.id] = topic.message_thread_id; await save_data_async()
        group_id_str = str(SUPPORT_GROUP_ID).replace("-100", "")
        text = f"👤 **NEW USER TICKET**\n📛 {user.full_name}\n🆔 `{user.id}`\n📜 [Click to Check History](https://t.me/c/{group_id_str}?q={user.id})"
        await context.bot.send_message(SUPPORT_GROUP_ID, text, message_thread_id=topic.message_thread_id, parse_mode=ParseMode.MARKDOWN)
        return topic.message_thread_id
    except Exception: return None
    finally: TOPIC_CREATION_LOCK.discard(user.id)

import os, json, asyncio, logging, time
from pyrogram.enums import ChatMemberStatus, ParseMode
from pyrogram.errors import RPCError

# --- LOGGING SETUP ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- CONFIGURATION & DEFAULTS ---
DEFAULTS = {"TOKEN": "", "OWNER": 0, "SUPPORT": 0, "MAIN_CH": 0, "LOG_CH": 0}

def _safe_int(env_name, default=0):
    raw = os.environ.get(env_name, None)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return int(str(raw).strip())
    except (ValueError, TypeError):
        logger.warning(f"Env var {env_name}={repr(raw)} is not a valid integer. Using default {default}")
        return default

API_ID = _safe_int("API_ID", 0)
API_HASH = os.environ.get("API_HASH", "") or ""
SESSION_STRING = os.environ.get("SESSION_STRING", "") or ""
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", DEFAULTS["TOKEN"]) or DEFAULTS["TOKEN"]
OWNER_ID = _safe_int("OWNER_ID", DEFAULTS["OWNER"])
SUPPORT_GROUP_ID = _safe_int("SUPPORT_GROUP_ID", DEFAULTS["SUPPORT"])
MANDATORY_CHANNEL_ID = _safe_int("MANDATORY_CHANNEL_ID", DEFAULTS["MAIN_CH"])
LOG_CHANNEL_ID = _safe_int("LOG_CHANNEL_ID", DEFAULTS["LOG_CH"])
MONGO_URL = os.environ.get("MONGO_URL", None) or None

if not TELEGRAM_BOT_TOKEN:
    logger.warning("TELEGRAM_BOT_TOKEN is empty! Bot engine will not start until this Space secret is set. Web dashboard will still run.")
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

# --- MONGODB SETUP (WITH 5-SECOND TIMEOUT SAFETY) ---
if MONGO_URL:
    try:
        logger.info("⏳ Connecting to MongoDB Atlas...")
        from pymongo import MongoClient
        import certifi
        # 🔥 5-Second timeout lagaya hai taaki Hugging Face startup par hang na ho!
        mongo_client = MongoClient(
            MONGO_URL, 
            tlsCAFile=certifi.where(),
            serverSelectionTimeoutMS=5000,
            socketTimeoutMS=5000,
            connectTimeoutMS=5000
        )
        mongo_db = mongo_client.get_database("telegram_bot_db")
        mongo_collection = mongo_db.get_collection("bot_settings")
        logger.info("✅ Connected to MongoDB Atlas Successfully!")
    except Exception as e:
        logger.error(f"❌ MongoDB Connection Failed: {e}")
        MONGO_URL = None

# --- DATABASE FUNCTIONS ---
def load_data():
    global DB
    logger.info("🔄 Loading Data...")
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
                logger.info("✅ Data Loaded from MongoDB!")
                return
        except Exception as e: logger.error(f"❌ MongoDB Load Error: {e}")

    logger.info("⚠️ Falling back to Local JSON Data...")
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
            logger.info("✅ Data Loaded from Local JSON!")
    except Exception as e: logger.error(f"❌ Local Load Error: {e}")

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
            try: 
                mongo_collection.replace_one({"_id": "main_settings"}, {"_id": "main_settings", "data": to_save}, upsert=True)
            except Exception as e: logger.error(f"❌ MongoDB Save Error: {e}")
        with open(DATA_FILE, "w") as f: json.dump(to_save, f, indent=4)
    except Exception as e: logger.error(f"❌ Save Error: {e}")

async def _background_save():
    async with data_lock:
        try:
            logger.info("💾 Saving Data in Background...")
            await asyncio.to_thread(save_data_sync)
            logger.info("✅ Background Save Complete!")
        except Exception as e:
            logger.error(f"❌ Background Save Failed: {e}")

async def save_data_async():
    asyncio.create_task(_background_save())

# --- CORE HELPERS (100% PYROGRAM CONVERTED) ---
async def execute_universal_kick(user_id, client, permanent_ban=False):
    mod = False
    for bid in list(DB["FREE_CHANNELS"].keys()):
        try:
            await client.ban_chat_member(int(bid), user_id)
            if not permanent_ban: await client.unban_chat_member(int(bid), user_id)
        except Exception: pass
        
    for bid in list(DB["PAID_CHANNELS"].keys()):
        try:
            bid_str = str(bid); is_demo = False
            if user_id in DB["USER_DATA"] and "demos" in DB["USER_DATA"][user_id] and bid_str in DB["USER_DATA"][user_id]["demos"]: is_demo = True
            
            await client.ban_chat_member(int(bid), user_id)
            if not permanent_ban: await client.unban_chat_member(int(bid), user_id)
            
            if is_demo: 
                del DB["USER_DATA"][user_id]["demos"][bid_str]
                mod = True
        except Exception: pass
        
    if permanent_ban:
        if user_id not in DB["BLOCKED_USERS"]: 
            DB["BLOCKED_USERS"].append(user_id)
            mod = True
    else:
        user_key = user_id if user_id in DB["USER_DATA"] else (str(user_id) if str(user_id) in DB["USER_DATA"] else None)
        if user_key and DB["USER_DATA"].get(user_key, {}).get("tnc_accepted", False):
            DB["USER_DATA"][user_key]["tnc_accepted"] = False
            mod = True

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

async def check_membership(user_id, client):
    if is_admin(user_id): return True
    if not MANDATORY_CHANNEL_ID: return True
    try:
        m = await client.get_chat_member(int(MANDATORY_CHANNEL_ID), user_id)
        return m.status in [ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER, ChatMemberStatus.RESTRICTED]
    except Exception: return False

async def is_already_in_channel(client, chat_id, user_id):
    try:
        member = await client.get_chat_member(int(chat_id), user_id)
        return member.status in [ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER, ChatMemberStatus.RESTRICTED]
    except Exception: return False

# 🔥 PYROGRAM ASYNC DELAYED DELETE FUNCTION
async def _delayed_delete(client, chat_id, msg_id, delay):
    await asyncio.sleep(delay)
    try:
        await client.delete_messages(chat_id=int(chat_id), message_ids=int(msg_id))
    except Exception: 
        pass

async def schedule_delete(client, message, delay=1200):
    if message: 
        asyncio.create_task(_delayed_delete(client, message.chat.id, message.id, delay))

# =====================================================================
# 🧠 THE GENIUS HACK: HTTP PEER DISCOVERY (STANDARD LIBRARY VERSION)
# =====================================================================
import urllib.request
import json

async def force_peer_discovery(chat_id):
    """HACK: Sends an invisible message via HTTP API to force Telegram to push the Chat ID cache to Pyrogram!"""
    if not TELEGRAM_BOT_TOKEN: 
        return
    try:
        logger.info(f"🔄 Firing HTTP Ping to wake up Peer Cache for {chat_id}...")
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = json.dumps({"chat_id": chat_id, "text": "🔄 System Sync...", "disable_notification": True}).encode('utf-8')
        req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
        
        def make_req():
            res = urllib.request.urlopen(req, timeout=10)
            return json.loads(res.read().decode('utf-8'))
            
        resp = await asyncio.to_thread(make_req)
        if resp and resp.get("ok"):
            msg_id = resp['result']['message_id']
            del_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/deleteMessage"
            del_data = json.dumps({"chat_id": chat_id, "message_id": msg_id}).encode('utf-8')
            del_req = urllib.request.Request(del_url, data=del_data, headers={'Content-Type': 'application/json'})
            await asyncio.to_thread(urllib.request.urlopen, del_req, timeout=10)
            logger.info("✅ Peer Cache forcefully absorbed!")
    except Exception as e:
        logger.error(f"❌ HTTP Ping Exception: {repr(e)}")

# =====================================================================
# RAW API FORUM TOPIC ENGINE
# =====================================================================
async def get_or_create_topic(user, client, is_retry=False):
    if not SUPPORT_GROUP_ID: 
        return None
    if user.id in DB.get("USER_TOPICS", {}): 
        return DB["USER_TOPICS"][user.id]
    if user.id in TOPIC_CREATION_LOCK:
        await asyncio.sleep(1) 
        if user.id in DB.get("USER_TOPICS", {}): 
            return DB["USER_TOPICS"][user.id]
    TOPIC_CREATION_LOCK.add(user.id)
    try:
        from pyrogram.raw.functions.channels import CreateForumTopic
        
        peer = await client.resolve_peer(int(SUPPORT_GROUP_ID))
        r = await client.invoke(
            CreateForumTopic(
                channel=peer,
                title=f"{user.first_name[:20]} ({user.id})"
            )
        )
        
        topic_id = None
        for update in r.updates:
            if hasattr(update, "message") and hasattr(update.message, "id"):
                topic_id = update.message.id
                break
                
        if not topic_id:
            raise Exception("Topic ID fetch fail ho gaya.")

        DB.setdefault("USER_TOPICS", {})[user.id] = topic_id
        await save_data_async()
        
        group_id_str = str(SUPPORT_GROUP_ID).replace("-100", "")
        text = f"👤 **NEW USER TICKET**\n📛 {user.first_name}\n🆔 `{user.id}`\n📜 [Click to Check History](https://t.me/c/{group_id_str}?q={user.id})"
        await client.send_message(int(SUPPORT_GROUP_ID), text, reply_to_message_id=topic_id, parse_mode=ParseMode.MARKDOWN)
        return topic_id
    except Exception as e: 
        err_str = str(e).lower()
        if "peer" in err_str and "invalid" in err_str and not is_retry:
            logger.warning("Peer ID missing in Topic Engine. Triggering HTTP Hack...")
            await force_peer_discovery(SUPPORT_GROUP_ID)
            TOPIC_CREATION_LOCK.discard(user.id)
            return await get_or_create_topic(user, client, is_retry=True)
            
        logger.error(f"Topic Creation Error: {e}")
        if user.id in DB.get("USER_TOPICS", {}):
            del DB["USER_TOPICS"][user.id]
        raise e
    finally: 
        TOPIC_CREATION_LOCK.discard(user.id)

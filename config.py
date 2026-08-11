import os, json, asyncio, logging, time
from pyrogram.enums import ChatMemberStatus, ParseMode
from pyrogram.errors import RPCError, PeerIdInvalid, ChannelInvalid, ChannelPrivate

# --- LOGGING SETUP ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# =====================================================================
# ROOT-CAUSE PATCH: pyrogram 2.0.106
# =====================================================================
try:
    import pyrogram.utils as _pyro_utils
    _NEW_MIN_CHANNEL_ID = -1007852516352
    _NEW_MIN_CHAT_ID = -999999999999
    if getattr(_pyro_utils, "MIN_CHANNEL_ID", 0) > _NEW_MIN_CHANNEL_ID:
        _pyro_utils.MIN_CHANNEL_ID = _NEW_MIN_CHANNEL_ID
    if getattr(_pyro_utils, "MIN_CHAT_ID", 0) > _NEW_MIN_CHAT_ID:
        _pyro_utils.MIN_CHAT_ID = _NEW_MIN_CHAT_ID
    logger.info(
        f"  Patched pyrogram ID bounds (MIN_CHANNEL_ID={_pyro_utils.MIN_CHANNEL_ID}, "
        f"MIN_CHAT_ID={_pyro_utils.MIN_CHAT_ID})."
    )
except Exception as _patch_err:
    logger.warning(f"  Could not patch pyrogram ID bounds: {_patch_err}")

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
    "ADMIN_IDS": [], "FREE_CHANNELS": {}, "PAID_CHANNELS": {}, "SPECIAL_CHANNELS": {}, "ALL_CHATS": {},
    "USER_DATA": {}, "BLOCKED_USERS": [], "USER_TOPICS": {}, "PENDING_REQUESTS": {},
    "LINK_MAP": {}, "CUSTOM_WELCOMES": {}, "NEW_USERS_ALLOWED": True, 
    "FREE_LOCKED": False, "PAID_LOCKED": False, "TEST_BOT_LOCKED": False, 
    "SCHEDULED_DELETES": [], "TEST_BOT_LINK": "", "BATCH_CATEGORIES": {},
    "CATEGORIES": DEFAULT_CATEGORIES.copy(), "MAINTENANCE_MODE": False,
    "BATCH_COINS": {}
}

MESSAGE_MAP = {} 
ADMIN_WIZARD = {} 
BROADCAST_STATE = {} 
TOPIC_CREATION_LOCK = set()
SPAM_CACHE = {} 
data_lock = asyncio.Lock()
mongo_client = mongo_collection = None

# --- GLOBAL FLOODWAIT STATE ---
FLOOD_WAIT_UNTIL = 0

def get_flood_wait_status():
    remaining = FLOOD_WAIT_UNTIL - time.time()
    if remaining > 0:
        return True, int(remaining) + 1
    return False, 0

# --- MONGODB SETUP (WITH 5-SECOND TIMEOUT SAFETY) ---
if MONGO_URL:
    try:
        logger.info("  Connecting to MongoDB Atlas...")
        from pymongo import MongoClient
        import certifi
        mongo_client = MongoClient(
            MONGO_URL, 
            tlsCAFile=certifi.where(),
            serverSelectionTimeoutMS=5000,
            socketTimeoutMS=5000,
            connectTimeoutMS=5000
        )
        mongo_db = mongo_client.get_database("telegram_bot_db")
        mongo_collection = mongo_db.get_collection("bot_settings")
        logger.info("  Connected to MongoDB Atlas Successfully!")
    except Exception as e:
        logger.error(f"  MongoDB Connection Failed: {e}")
        MONGO_URL = None

# --- DATABASE FUNCTIONS ---
def load_data():
    global DB
    logger.info("  Loading Data...")
    keys_to_load = [
        "LINK_MAP", "NEW_USERS_ALLOWED", "FREE_LOCKED", "PAID_LOCKED", "TEST_BOT_LOCKED", 
        "SCHEDULED_DELETES", "TEST_BOT_LINK", "MAINTENANCE_MODE", "CATEGORIES", "BATCH_CATEGORIES", 
        "USERBOT_SESSION", "USERBOT_PHONE",
        "VIP_MATERIALS_LINK", "VIP_STICKER_ID", "VIP_STICKER_TYPE", "BATCH_COINS",
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
                
                # --- UPDATE: LOADING SPECIAL_CHANNELS ALONG WITH OTHERS ---
                for k in ["CUSTOM_WELCOMES", "FREE_CHANNELS", "PAID_CHANNELS", "SPECIAL_CHANNELS", "ALL_CHATS", "USER_TOPICS", "USER_DATA", "PENDING_REQUESTS"]:
                    if k in loaded: DB[k] = {int(i): v for i, v in loaded[k].items()}
                    
                if OWNER_ID not in DB["ADMIN_IDS"]: DB["ADMIN_IDS"].append(OWNER_ID)
                
                for cid, name in DB.get("FREE_CHANNELS", {}).items(): DB["ALL_CHATS"][cid] = name
                for cid, name in DB.get("PAID_CHANNELS", {}).items(): DB["ALL_CHATS"][cid] = name
                for cid, name in DB.get("SPECIAL_CHANNELS", {}).items(): DB["ALL_CHATS"][cid] = name
                
                logger.info("  Data Loaded from MongoDB!")
                return
        except Exception as e: logger.error(f"  MongoDB Load Error: {e}")
        
    logger.info("  Falling back to Local JSON Data...")
    if not os.path.exists(DATA_FILE): save_data_sync(); return
    try:
        with open(DATA_FILE, "r") as f:
            loaded = json.load(f)
            if "ADMIN_IDS" in loaded: DB["ADMIN_IDS"] = [int(x) for x in loaded["ADMIN_IDS"] if str(x).isdigit()]
            if "BLOCKED_USERS" in loaded: DB["BLOCKED_USERS"] = loaded["BLOCKED_USERS"]
            
            for k in keys_to_load:
                if k in loaded: DB[k] = loaded[k]
            
            # --- UPDATE: LOADING SPECIAL_CHANNELS ALONG WITH OTHERS ---
            for k in ["CUSTOM_WELCOMES", "FREE_CHANNELS", "PAID_CHANNELS", "SPECIAL_CHANNELS", "ALL_CHATS", "USER_TOPICS", "USER_DATA", "PENDING_REQUESTS"]:
                if k in loaded: DB[k] = {int(i): v for i, v in loaded[k].items()}
                
            if OWNER_ID not in DB["ADMIN_IDS"]: DB["ADMIN_IDS"].append(OWNER_ID)
            
            for cid, name in DB.get("FREE_CHANNELS", {}).items(): DB["ALL_CHATS"][cid] = name
            for cid, name in DB.get("PAID_CHANNELS", {}).items(): DB["ALL_CHATS"][cid] = name
            for cid, name in DB.get("SPECIAL_CHANNELS", {}).items(): DB["ALL_CHATS"][cid] = name
            
            logger.info("  Data Loaded from Local JSON!")
    except Exception as e: logger.error(f"  Local Load Error: {e}")

def save_data_sync():
    try:
        to_save = {
            "ADMIN_IDS": DB["ADMIN_IDS"], "BLOCKED_USERS": DB["BLOCKED_USERS"],
            "NEW_USERS_ALLOWED": DB.get("NEW_USERS_ALLOWED", True), "FREE_LOCKED": DB.get("FREE_LOCKED", False),
            "PAID_LOCKED": DB.get("PAID_LOCKED", False), "TEST_BOT_LOCKED": DB.get("TEST_BOT_LOCKED", False),
            "LINK_MAP": DB.get("LINK_MAP", {}), "SCHEDULED_DELETES": DB.get("SCHEDULED_DELETES", []),
            "TEST_BOT_LINK": DB.get("TEST_BOT_LINK", ""), 
            "CATEGORIES": DB.get("CATEGORIES", DEFAULT_CATEGORIES),
            "BATCH_CATEGORIES": {str(k): v for k, v in DB.get("BATCH_CATEGORIES", {}).items()},
            "MAINTENANCE_MODE": DB.get("MAINTENANCE_MODE", False),
            "USERBOT_SESSION": DB.get("USERBOT_SESSION"), 
            "USERBOT_PHONE": DB.get("USERBOT_PHONE"),
            "VIP_MATERIALS_LINK": DB.get("VIP_MATERIALS_LINK"),
            "VIP_STICKER_ID": DB.get("VIP_STICKER_ID"),
            "VIP_STICKER_TYPE": DB.get("VIP_STICKER_TYPE"),
            "BATCH_COINS": {str(k): v for k, v in DB.get("BATCH_COINS", {}).items()},
            "CUSTOM_WELCOMES": {str(k): v for k, v in DB.get("CUSTOM_WELCOMES", {}).items()},
            "FREE_CHANNELS": {str(k): v for k, v in DB.get("FREE_CHANNELS", {}).items()},
            "PAID_CHANNELS": {str(k): v for k, v in DB.get("PAID_CHANNELS", {}).items()},
            
            # --- UPDATE: SAVING SPECIAL CHANNELS ---
            "SPECIAL_CHANNELS": {str(k): v for k, v in DB.get("SPECIAL_CHANNELS", {}).items()},
            
            "ALL_CHATS": {str(k): v for k, v in DB.get("ALL_CHATS", {}).items()},
            "USER_DATA": {str(k): v for k, v in DB.get("USER_DATA", {}).items()},
            "USER_TOPICS": {str(k): v for k, v in DB.get("USER_TOPICS", {}).items()},
            "PENDING_REQUESTS": {str(k): v for k, v in DB.get("PENDING_REQUESTS", {}).items()}
        }
        if MONGO_URL and mongo_collection is not None:
            try: 
                mongo_collection.replace_one({"_id": "main_settings"}, {"_id": "main_settings", "data": to_save}, upsert=True)
            except Exception as e: logger.error(f"  MongoDB Save Error: {e}")
        with open(DATA_FILE, "w") as f: json.dump(to_save, f, indent=4)
    except Exception as e: logger.error(f"  Save Error: {e}")

async def _background_save():
    async with data_lock:
        try:
            logger.info("  Saving Data in Background...")
            await asyncio.to_thread(save_data_sync)
            logger.info("  Background Save Complete!")
        except Exception as e:
            logger.error(f"  Background Save Failed: {e}")

SAVE_DEBOUNCE_SECONDS = 4
_save_flush_task = None
_db_is_dirty = False

async def _debounced_flush():
    global _db_is_dirty
    try:
        while _db_is_dirty:
            _db_is_dirty = False
            await asyncio.sleep(SAVE_DEBOUNCE_SECONDS)
            await _background_save()
    except Exception as e:
        logger.error(f"  Debounced Save Failed: {e}")

async def save_data_async():
    global _save_flush_task, _db_is_dirty
    _db_is_dirty = True
    if _save_flush_task is None or _save_flush_task.done():
        _save_flush_task = asyncio.create_task(_debounced_flush())


# --- MEMBERSHIP CACHE ---
_MEMBERSHIP_CACHE = {} 
MEMBERSHIP_CACHE_TTL = 30  

async def get_membership_cached(client, chat_id, user_id, ttl=MEMBERSHIP_CACHE_TTL):
    key = (int(chat_id), int(user_id))
    now = time.time()
    cached = _MEMBERSHIP_CACHE.get(key)
    if cached and cached[1] > now:
        return cached[0]
    try:
        m = await client.get_chat_member(int(chat_id), int(user_id))
        result = m.status in [
            ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER, ChatMemberStatus.RESTRICTED,
        ]
    except Exception:
        result = False
        
    _MEMBERSHIP_CACHE[key] = (result, now + ttl)
    return result

def invalidate_membership_cache(user_id, chat_id=None):
    user_id = int(user_id)
    if chat_id is not None:
        _MEMBERSHIP_CACHE.pop((int(chat_id), user_id), None)
    else:
        for key in [k for k in _MEMBERSHIP_CACHE if k[1] == user_id]:
            del _MEMBERSHIP_CACHE[key]

# --- CORE HELPERS ---
async def execute_universal_kick(user_id, client, permanent_ban=False):
    from pyrogram.errors import FloodWait, UserNotParticipant
    from pyrogram.enums import ChatMemberStatus
    import asyncio
    
    mod = False
    target_uid = int(user_id)
    user_key = target_uid if target_uid in DB.get("USER_DATA", {}) else (str(target_uid) if str(target_uid) in DB.get("USER_DATA", {}) else None)

    # Semaphore limit taaki API block na ho
    sem = asyncio.Semaphore(4)

    # 💎 VIP FILTER LOGIC:
    if permanent_ban:
        # Agar Admin ne manually ban kiya hai, toh sab jagah se nikalo (Including Paid)
        all_channels = set(
            list(DB.get("FREE_CHANNELS", {}).keys()) +
            list(DB.get("PAID_CHANNELS", {}).keys()) +
            list(DB.get("SPECIAL_CHANNELS", {}).keys())
        )
    else:
        # Agar user ne rule toda hai (Leave/Block), toh Paid channel ko safe rakho
        all_channels = set(
            list(DB.get("FREE_CHANNELS", {}).keys()) +
            list(DB.get("SPECIAL_CHANNELS", {}).keys())
        )

    async def _smart_kick(bid):
        nonlocal mod
        async with sem:
            try:
                target_bid = int(bid)
                
                # Check karo ki kya user sach me is channel me hai?
                try:
                    member = await client.get_chat_member(target_bid, target_uid)
                    if member.status in [ChatMemberStatus.LEFT, ChatMemberStatus.BANNED]:
                        return 
                except UserNotParticipant:
                    return
                except Exception:
                    pass
                
                # User andar hai! Ab usko Kick karo.
                bid_str = str(bid)
                is_demo = user_key and bid_str in DB.get("USER_DATA", {}).get(user_key, {}).get("demos", {})
                
                await client.ban_chat_member(target_bid, target_uid)
                if not permanent_ban:
                    await asyncio.sleep(0.5) 
                    await client.unban_chat_member(target_bid, target_uid)
                    
                if is_demo:
                    del DB["USER_DATA"][user_key]["demos"][bid_str]
                    mod = True
                    
            except FloodWait as e:
                await asyncio.sleep(e.value + 1)
                try:
                    await client.ban_chat_member(target_bid, target_uid)
                    if not permanent_ban:
                        await client.unban_chat_member(target_bid, target_uid)
                except Exception:
                    pass
            except Exception as e:
                logger.error(f"  [SMART KICK FAIL] Batch {target_bid}: {e}")

    # Saare filtered channels ke liye smart kick chalayein
    if all_channels:
        await asyncio.gather(*[_smart_kick(bid) for bid in all_channels])
        
    invalidate_membership_cache(target_uid)
    
    if permanent_ban:
        if target_uid not in DB.get("BLOCKED_USERS", []): 
            DB.setdefault("BLOCKED_USERS", []).append(target_uid)
            mod = True
    else:
        if user_key and DB.get("USER_DATA", {}).get(user_key, {}).get("tnc_accepted", False):
            DB["USER_DATA"][user_key]["tnc_accepted"] = False
            mod = True
            
    if mod: await save_data_async()

def is_admin(uid):
    if str(uid) == str(OWNER_ID):
        return True
    uid_str = str(uid)
    for admin_id in DB.get("ADMIN_IDS", []):
        if admin_id == uid or str(admin_id) == uid_str:
            return True
    return False

def check_spam(uid):
    now = time.time(); last = SPAM_CACHE.get(uid, 0)
    SPAM_CACHE[uid] = now
    return True if now - last < 1.5 else False

async def check_membership(user_id, client):
    if is_admin(user_id): return True
    if not MANDATORY_CHANNEL_ID: return True
    return await get_membership_cached(client, MANDATORY_CHANNEL_ID, user_id)

async def is_already_in_channel(client, chat_id, user_id):
    return await get_membership_cached(client, chat_id, user_id)

async def _delayed_delete(client, chat_id, msg_id, delay):
    await asyncio.sleep(delay)
    try:
        await client.delete_messages(chat_id=int(chat_id), message_ids=int(msg_id))
    except Exception:
        pass

async def schedule_delete(client, message, delay=1200):
    if message: 
        asyncio.create_task(_delayed_delete(client, message.chat.id, message.id, delay))

async def refresh_peer_cache(client, chat_id):
    if not chat_id:
        return False
    try:
        await client.get_chat(int(chat_id))
        logger.info(f"  Peer cache refreshed natively for {chat_id}!")
        return True
    except Exception as e:
        logger.error(f"  Native peer refresh failed for {chat_id}: {e}")
        return False

async def get_or_create_topic(user, client, is_retry=False):
    if not SUPPORT_GROUP_ID:
        return None
            
    if user.id in DB.get("USER_TOPICS", {}):
        return DB["USER_TOPICS"][user.id]
            
    if user.id in TOPIC_CREATION_LOCK:
        for _ in range(10):
            await asyncio.sleep(0.5)
            if user.id in DB.get("USER_TOPICS", {}):
                return DB["USER_TOPICS"][user.id]
        return None
            
    TOPIC_CREATION_LOCK.add(user.id)
    
    try:
        title = f"{(user.first_name or 'User')[:20]} ({user.id})"
        topic_id = None
                
        try:
            topic = await client.create_forum_topic(chat_id=int(SUPPORT_GROUP_ID), title=title)
            topic_id = getattr(topic, "id", None) or getattr(topic, "message_thread_id", None)
        except AttributeError:
            from pyrogram.raw.functions.channels import CreateForumTopic
            peer = await client.resolve_peer(int(SUPPORT_GROUP_ID))
            r = await client.invoke(
                CreateForumTopic(channel=peer, title=title, random_id=client.rnd_id())
            )
            for update in r.updates:
                if hasattr(update, "message") and hasattr(update.message, "id"):
                    topic_id = update.message.id
                    break
                            
        if not topic_id:
            raise Exception("Topic ID could not be resolved from Telegram's response.")
                    
        DB.setdefault("USER_TOPICS", {})[user.id] = topic_id
        await save_data_async()
                
        group_id_str = str(SUPPORT_GROUP_ID).replace("-100", "")
        text = (
            f"🚨 **NEW USER TICKET**\n👤 {user.first_name}\n🆔 `{user.id}`\n"
            f"🔗 [Click to Check History](https://t.me/c/{group_id_str}?q={user.id})"
        )
                
        try:
            await client.send_message(
                int(SUPPORT_GROUP_ID), text, message_thread_id=topic_id, parse_mode=ParseMode.MARKDOWN
            )
        except TypeError:
            await client.send_message(
                int(SUPPORT_GROUP_ID), text, reply_to_message_id=topic_id, parse_mode=ParseMode.MARKDOWN
            )
                    
        return topic_id
            
    except (PeerIdInvalid, ChannelInvalid, ChannelPrivate) as e:
        if not is_retry:
            logger.warning(f"Peer cache miss for Support Group ({e}). Refreshing natively via get_chat()...")
            TOPIC_CREATION_LOCK.discard(user.id)
            await refresh_peer_cache(client, SUPPORT_GROUP_ID)
            return await get_or_create_topic(user, client, is_retry=True)
        logger.error(f"Topic Creation Error (peer still invalid after native refresh): {e}")
        raise
    except Exception as e:
        logger.error(f"Topic Creation Error: {e}")
        if user.id in DB.get("USER_TOPICS", {}):
            del DB["USER_TOPICS"][user.id]
        raise e
    finally:
        TOPIC_CREATION_LOCK.discard(user.id)

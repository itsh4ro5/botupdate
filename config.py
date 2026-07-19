import os, json, asyncio, logging, time
from pyrogram.enums import ChatMemberStatus, ParseMode
from pyrogram.errors import RPCError, PeerIdInvalid, ChannelInvalid, ChannelPrivate

# --- LOGGING SETUP ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# =====================================================================
# 🩹 ROOT-CAUSE PATCH: pyrogram 2.0.106 (the last release on PyPI —
# upstream is effectively unmaintained) hard-codes channel/chat ID
# bounds from ~2021 in pyrogram/utils.py:
#     MIN_CHANNEL_ID = -1002147483647
#     MIN_CHAT_ID    = -2147483647
# Telegram has since extended how negative new channel/supergroup IDs
# can get. A freshly-created group like your Support Group (e.g.
# -1003810420561) falls BELOW that hard-coded floor. Pyrogram's own
# utils.get_peer_type() rejects any ID outside these bounds and raises
# "Peer id invalid" *before a single byte goes over the MTProto socket*
# — no amount of peer-cache warm-up, get_chat() retries, or reconnects
# can work around a purely local, client-side range check. Widening the
# bound is the community-standard fix for this exact symptom
# (see pyrogram/pyrogram#1430, still unmerged upstream as of this build).
# This must run before any Client resolves a peer, so it lives at the
# very top of config.py, which every other module imports first.
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
        f"✅ Patched pyrogram ID bounds (MIN_CHANNEL_ID={_pyro_utils.MIN_CHANNEL_ID}, "
        f"MIN_CHAT_ID={_pyro_utils.MIN_CHAT_ID})."
    )
except Exception as _patch_err:
    logger.warning(f"⚠️ Could not patch pyrogram ID bounds: {_patch_err}")

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

# --- GLOBAL FLOODWAIT STATE ---
# UNIX timestamp until which Telegram has FloodWait-limited this bot.
# 0 means no active FloodWait. Set by bot.py whenever a FloodWait is
# caught, read by app.py to warn the dashboard/front-end.
FLOOD_WAIT_UNTIL = 0

def get_flood_wait_status():
    """Returns (is_active: bool, seconds_remaining: int) for the current FloodWait cooldown."""
    remaining = FLOOD_WAIT_UNTIL - time.time()
    if remaining > 0:
        return True, int(remaining) + 1
    return False, 0

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

# =====================================================================
# 🚀 PERFORMANCE: DEBOUNCED / BATCHED SAVE
# Previously, every save_data_async() call immediately spawned its own
# asyncio.create_task(_background_save()) — a FULL DB → JSON serialize
# (+ a full Mongo replace_one network round-trip) EACH time. Under bursty
# traffic (e.g. 50 users tapping buttons in the same second), that meant
# 50 separate full-DB writes queued back-to-back behind `data_lock`,
# hammering disk I/O and the Mongo connection for no benefit — the last
# write always wins anyway.
# Now: any call just flags the DB "dirty". At most ONE flush task is ever
# in flight; it waits SAVE_DEBOUNCE_SECONDS before actually writing, so
# every call that arrives inside that window gets coalesced into that
# single write. 100 calls in 3 seconds now costs exactly 1 write instead
# of 100 — this is the single biggest I/O reduction in this file.
# =====================================================================
SAVE_DEBOUNCE_SECONDS = 4
_save_flush_task = None

async def _debounced_flush():
    try:
        await asyncio.sleep(SAVE_DEBOUNCE_SECONDS)
        await _background_save()
    except Exception as e:
        logger.error(f"❌ Debounced Save Failed: {e}")

async def save_data_async():
    """
    Marks the DB dirty and ensures exactly one debounced flush is pending.
    Safe to call as often as you like — extra calls inside the debounce
    window are free (no new task, no new write).
    """
    global _save_flush_task
    # No `await` happens between this check and the create_task call, so
    # there's no window for two coroutines to both see "no task running"
    # and schedule two flushes — asyncio is single-threaded/cooperative,
    # so this is race-free without needing an extra lock.
    if _save_flush_task is None or _save_flush_task.done():
        _save_flush_task = asyncio.create_task(_debounced_flush())

# =====================================================================
# 🚀 PERFORMANCE: SHARED MEMBERSHIP TTL CACHE
# check_membership_pyro / is_already_in_channel_pyro (handlers.py) and
# the per-batch membership loop in app.py's /api/user endpoint all called
# client.get_chat_member() fresh, every single time — including cases
# where the SAME (chat, user) pair gets checked multiple times within the
# same page load (e.g. /api/user and /api/explore both re-check every
# batch back-to-back) or across rapid repeated button taps. Each of those
# is a real network round-trip to Telegram and counts against rate limits.
# This cache shares one short-lived result across every caller.
# =====================================================================
_MEMBERSHIP_CACHE = {}  # {(chat_id, user_id): (is_member: bool, expiry_ts: float)}
MEMBERSHIP_CACHE_TTL = 30  # seconds — short enough to stay accurate, long enough to kill duplicate bursts

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
    """Call right after a ban/kick/approve so a stale cached result isn't served."""
    user_id = int(user_id)
    if chat_id is not None:
        _MEMBERSHIP_CACHE.pop((int(chat_id), user_id), None)
    else:
        for key in [k for k in _MEMBERSHIP_CACHE if k[1] == user_id]:
            del _MEMBERSHIP_CACHE[key]

# --- CORE HELPERS (100% PYROGRAM CONVERTED) ---
async def execute_universal_kick(user_id, client, permanent_ban=False):
    mod = False

    async def _kick_free(bid):
        try:
            await client.ban_chat_member(int(bid), user_id)
            if not permanent_ban:
                await client.unban_chat_member(int(bid), user_id)
        except Exception:
            pass

    async def _kick_paid(bid):
        nonlocal mod
        try:
            bid_str = str(bid)
            is_demo = bid_str in DB["USER_DATA"].get(user_id, {}).get("demos", {})
            await client.ban_chat_member(int(bid), user_id)
            if not permanent_ban:
                await client.unban_chat_member(int(bid), user_id)
            if is_demo:
                del DB["USER_DATA"][user_id]["demos"][bid_str]
                mod = True
        except Exception:
            pass

    # 🚀 PERFORMANCE: this used to ban/unban across every free THEN every
    # paid channel one at a time (2N sequential Telegram round-trips for a
    # user in N+N channels). asyncio.gather fires them all concurrently —
    # total wait drops from O(N * latency) to roughly O(latency).
    await asyncio.gather(
        *[_kick_free(bid) for bid in list(DB["FREE_CHANNELS"].keys())],
        *[_kick_paid(bid) for bid in list(DB["PAID_CHANNELS"].keys())],
    )
    invalidate_membership_cache(user_id)  # drop any cached "still joined" entries

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
    # 🚀 PERFORMANCE: previously did `uid in DB["ADMIN_IDS"]` (one O(n) pass)
    # THEN a second O(n) loop doing str() comparisons as a fallback — i.e.
    # up to 2 full passes over the admin list on every single call, and
    # is_admin() is called on nearly every incoming message/command. Merged
    # into one pass that checks both forms per element.
    if str(uid) == str(OWNER_ID):
        return True
    uid_str = str(uid)
    for admin_id in DB["ADMIN_IDS"]:
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
# 🧠 NATIVE MTPROTO PEER CACHE WARM-UP (NO HTTP HACKS)
# =====================================================================
async def refresh_peer_cache(client, chat_id):
    """
    Forces Pyrogram to (re)learn a chat's access_hash purely over the
    existing MTProto socket — no HTTP Bot API calls involved.
    client.get_chat() internally resolves the peer and updates Pyrogram's
    local peer storage (SQLite session) as a side effect.
    """
    if not chat_id:
        return False
    try:
        await client.get_chat(int(chat_id))
        logger.info(f"✅ Peer cache refreshed natively for {chat_id}!")
        return True
    except Exception as e:
        logger.error(f"❌ Native peer refresh failed for {chat_id}: {e}")
        return False

# =====================================================================
# FORUM TOPIC ENGINE (NATIVE PYROGRAM 2.x API, WITH RAW-API FALLBACK)
# =====================================================================
async def get_or_create_topic(user, client, is_retry=False):
    if not SUPPORT_GROUP_ID:
        return None
    if user.id in DB.get("USER_TOPICS", {}):
        return DB["USER_TOPICS"][user.id]

    if user.id in TOPIC_CREATION_LOCK:
        # Another task is already creating this user's topic — wait for it
        for _ in range(10):
            await asyncio.sleep(0.5)
            if user.id in DB.get("USER_TOPICS", {}):
                return DB["USER_TOPICS"][user.id]
        return None

    TOPIC_CREATION_LOCK.add(user.id)
    try:
        title = f"{(user.first_name or 'User')[:20]} ({user.id})"
        topic_id = None

        # --- Preferred path: native Pyrogram 2.0.106 forum-topic method ---
        try:
            topic = await client.create_forum_topic(chat_id=int(SUPPORT_GROUP_ID), title=title)
            topic_id = getattr(topic, "id", None) or getattr(topic, "message_thread_id", None)
        except AttributeError:
            # Older Pyrogram build without create_forum_topic() — Raw API fallback
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
            f"👤 **NEW USER TICKET**\n📛 {user.first_name}\n🆔 `{user.id}`\n"
            f"📜 [Click to Check History](https://t.me/c/{group_id_str}?q={user.id})"
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

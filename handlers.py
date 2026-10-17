import asyncio
from datetime import datetime, timedelta
import io
import os
import re
import traceback
import asyncio
import time
import urllib.parse
import logging
from config import *
import richpyro as rp
import pyrogram
from pyrogram import Client, filters
from pyrogram.enums import ChatMemberStatus, ChatType, ParseMode
from pyrogram.errors import (
    ChatAdminRequired,
    FloodWait,
    InputUserDeactivated,
    PeerIdInvalid,
    RPCError,
    SessionPasswordNeeded,
    UserIsBlocked,
    UserNotParticipant,
)
from pyrogram.types import (
    BotCommand,
    BotCommandScopeChat,
    CallbackQuery,
    ChatJoinRequest,
    ChatMemberUpdated,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaAnimation,
    InputMediaAudio,
    InputMediaDocument,
    InputMediaPhoto,
    InputMediaVideo,
    Message,
)

logger = logging.getLogger(__name__)

# Custom Animated / Premium Emojis from @istevedogs Emoji Icons Pack
CUSTOM_EMOJIS = {
    "CHECK": "4967612238874542985",       # ✅
    "CHECK_2": "4967770182001885951",     # ✅
    "CHECK_GREEN": "4969981205396063191", # ✅
    "CROSS": "4970239229851337393",       # ✖️
    "CROSS_BOX": "4967749291280958323",   # ❎
    "ALERT": "4967800109334004515",       # ❗️
    "SIREN": "4969741112429249159",       # 🚨
    "STOP": "4967893421793477515",        # 🛑
    "QUESTION": "4967611285391803206",    # ❓
    "PLUS": "4967481744883188442",        # ➕
    "MINUS": "4967648879240544998",       # ➖
    "INFO": "4970095936857440997",        # ℹ️
    "BELL": "4967778303785042605",        # 🔔
    "SPARKLE": "4970033831630341184",     # ❇️
    "LIGHT": "4968003407315993509",       # 💡
    "LIGHTNING": "4967489802241835579",   # ⚡️
    "HEART": "4967994490963886991",       # ❤️
    "THUMB_UP": "4967792296788501647",    # 👍
    "BROADCAST": "4969807937825407652",   # 📢
    "ANNOUNCE": "4969904819402703699",    # 📣
    "DOWNLOAD": "4967594608033792786",    # 📥
    "STATS": "4967970726909838084",       # 📊
    "DIAMOND": "4968025655246586724",     # 💎
    "FOLDER": "4967570599166608213",      # 📂
    "GEAR": "4967490064234840998",       # ⚙️
    "GIFT": "4967740821605450859",        # 🎁
    "KEY": "4969770296732025426",         # 🔑
    "LOCK": "4970224115861422958",        # 🔒
    "PIN": "4970003148383978212",         # 📍
    "CLOCK": "4967710224258433643",       # 🕰
    "CROWN": "4969765374699504624",       # 👑
    "MEDAL": "4967587877820039770",       # 🥇
    "MAIL": "4967770306555937780",        # 📧
    "SHIELD": "4967861870963720868",      # 🛡
    "REFRESH": "4967823117473809209",     # 🔃
    "LINK": "4967826519087907994",        # 🔗
    "DOC": "4967625145251267473",         # 📄
    "BAN": "4967927656977793959",         # 🚫
}

def ce(name: str, fallback: str = ""):
    eid = CUSTOM_EMOJIS.get(name)
    if eid:
        return rp.custom_emoji(eid, fallback or "✨")
    return fallback

_SYNC_IN_PROGRESS = False
_BOT_SELF_ID = None
USER_FLOW_STATE = {}

# --- ANTI-SPAM CONFIG: Link generation cooldown & per-batch lock ---
GLOBAL_LINK_COOLDOWN = 15 * 60   # Rule A: 15 minutes between ANY link generation
ACTIVE_LINK_TTL = 60             # Rule B: matches the 60s invite-link expiry

def is_vip_user(uid) -> bool:
    """VIP = 25+ lifetime successful refers (tier flag set in process_successful_referral)."""
    key = uid if uid in DB["USER_DATA"] else str(uid)
    return DB["USER_DATA"].get(key, {}).get("tier") == "vip"

async def send_vip_treat(client: Client, uid: int):
    """VIPs ko action complete karne par ek premium sticker/GIF bhejta hai (agar admin ne /setvipsticker se set kiya hai)."""
    sticker_id = DB.get("VIP_STICKER_ID")
    if not sticker_id:
        return
    try:
        if DB.get("VIP_STICKER_TYPE") == "animation":
            await client.send_animation(uid, sticker_id)
        else:
            await client.send_sticker(uid, sticker_id)
    except Exception:
        pass

def get_cooldown_remaining(uid: int) -> int:
    """Rule A: returns remaining whole minutes of the global cooldown (0 = clear). VIPs bypass this entirely."""
    if is_vip_user(uid):
        return 0
    entry = DB.get("PENDING_REQUESTS", {}).get(str(uid))
    if not entry:
        return 0
    elapsed = time.time() - entry.get("last_link_ts", 0)
    remaining = GLOBAL_LINK_COOLDOWN - elapsed
    if remaining <= 0:
        return 0
    return max(1, int(remaining // 60) + (1 if remaining % 60 else 0))

def has_active_request(uid: int, cid: int) -> bool:
    """Rule B: True if this user still has a live pending link for this exact batch."""
    entry = DB.get("PENDING_REQUESTS", {}).get(str(uid))
    if not entry:
        return False
    exp = entry.get("active_batches", {}).get(str(cid))
    return bool(exp and time.time() < exp)

def register_link_request(uid: int, cid: int):
    """Call this the moment a link is successfully generated (free or paid)."""
    now = time.time()
    entry = DB["PENDING_REQUESTS"].setdefault(
        str(uid), {"last_link_ts": 0, "active_batches": {}}
    )
    entry["last_link_ts"] = now
    entry["active_batches"][str(cid)] = now + ACTIVE_LINK_TTL
    asyncio.create_task(save_data_async())

def clear_active_request(uid, cid):
    """Call this when an admin approves/rejects a request early, freeing the slot."""
    entry = DB.get("PENDING_REQUESTS", {}).get(str(uid))
    if entry and str(cid) in entry.get("active_batches", {}):
        del entry["active_batches"][str(cid)]

# --- HELPER: COMMAND ARGUMENTS EXTRACTOR ---
def get_args(message: Message):
    if message.command and len(message.command) > 1:
        return message.command[1:]
    elif message.text and not message.text.startswith('/'):
        return message.text.split(" ")
    return []

# --- HELPER: CRASH-SAFE SENDER CHECKS ---
def is_admin_msg(message: Message) -> bool:
    if message.from_user:
        return is_admin(message.from_user.id)
    return bool(message.sender_chat) and str(message.chat.id) == str(SUPPORT_GROUP_ID)

def is_owner_msg(message: Message) -> bool:
    return bool(message.from_user) and str(message.from_user.id) == str(OWNER_ID)

# --- HELPER: ROBUST MEMBERSHIP CHECKS FOR PYROGRAM ---
async def check_membership_pyro(uid: int, client: Client):
    if not MANDATORY_CHANNEL_ID:
        return True
    return await get_membership_cached(client, MANDATORY_CHANNEL_ID, uid)

async def is_already_in_channel_pyro(client: Client, cid: int, uid: int):
    return await get_membership_cached(client, cid, uid)

# --- MENU SETTERS ---
async def set_role_based_commands(user_id: int, client: Client):
    try:
        user_cmds = [
            BotCommand("start", "Open Main Menu"),
            BotCommand("id", "Get Telegram ID"),
            BotCommand("myinfo", "Check Active Demos"),
        ]
        if str(user_id) == str(OWNER_ID) or is_admin(user_id):
            admin_cmds = user_cmds + [
                BotCommand("stats", "Bot Statistics"),
                BotCommand("batchstats", "Batch Info"),
                BotCommand("genvoucher", "Generate Promo Code"),
                BotCommand("batchrequests", "View Batch Requests"),
            ]
            await client.set_bot_commands(
                admin_cmds, scope=BotCommandScopeChat(chat_id=user_id)
            )
        else:
            await client.set_bot_commands(
                user_cmds, scope=BotCommandScopeChat(chat_id=user_id)
            )
        try:
            from pyrogram.types import MenuButtonCommands
            await client.set_chat_menu_button(chat_id=user_id, menu_button=MenuButtonCommands())
        except Exception:
            pass
    except Exception:
        pass

async def process_successful_referral(client: Client, referee_id: int, referrer_id: int):
    # Strict key check (integer and string types handles both safely)
    referrer_key = str(referrer_id) if str(referrer_id) in DB["USER_DATA"] else (referrer_id if referrer_id in DB["USER_DATA"] else str(referrer_id))
    referee_key = str(referee_id) if str(referee_id) in DB["USER_DATA"] else (referee_id if referee_id in DB["USER_DATA"] else str(referee_id))
    
    if referrer_key not in DB["USER_DATA"]:
        return

    # Coins (referral_count) and Invites badhayein instantly
    DB["USER_DATA"][referrer_key]["referral_count"] = DB["USER_DATA"][referrer_key].get("referral_count", 0) + 1
    DB["USER_DATA"][referrer_key]["total_invited"] = DB["USER_DATA"][referrer_key].get("total_invited", 0) + 1

    # Har 5 refers par +1 bonus coin logic
    milestone_hit = DB["USER_DATA"][referrer_key]["total_invited"] % 5 == 0
    if milestone_hit:
        DB["USER_DATA"][referrer_key]["referral_count"] += 1

    total_now = DB["USER_DATA"][referrer_key]["total_invited"]
    newly_vip = total_now >= 25 and DB["USER_DATA"][referrer_key].get("tier") != "vip"
    if newly_vip:
        DB["USER_DATA"][referrer_key]["tier"] = "vip"

    if referee_key in DB["USER_DATA"]:
        DB["USER_DATA"][referee_key]["referred_by"] = referrer_id
        DB["USER_DATA"][referee_key].setdefault("welcome_bonus_claimed", False)

    await save_data_async()

    # --- NAYA LOGIC: SUPPORT GROUP TOPIC NOTIFICATION ---
    from config import SUPPORT_GROUP_ID, get_or_create_topic
    if SUPPORT_GROUP_ID:
        try:
            referrer_user = await client.get_users(int(referrer_id))
            topic_id = await get_or_create_topic(referrer_user, client)
            if topic_id:
                await client.send_message(
                    chat_id=int(SUPPORT_GROUP_ID),
                    text=f"🎉 <b>{total_now} refers successful by the user</b>",
                    message_thread_id=topic_id,
                    parse_mode=ParseMode.HTML
                )
        except Exception as e:
            logger.error(f"Support topic notification failed: {e}")

    # Welcome bonus check for the referrer if they were pending
    if not DB["USER_DATA"][referrer_key].get("welcome_bonus_claimed", True) and DB["USER_DATA"][referrer_key].get("referred_by"):
        DB["USER_DATA"][referrer_key]["referral_count"] = DB["USER_DATA"][referrer_key].get("referral_count", 0) + 1
        DB["USER_DATA"][referrer_key]["welcome_bonus_claimed"] = True
        await save_data_async()
        try:
            await client.send_message(
                referrer_id,
                "🔓 **WELCOME BONUS UNLOCKED!**\n\n"
                "Aapne apna pehla successful refer kar diya, isliye aapka pending **1 Coin Welcome Bonus** bhi ab wallet me add ho gaya hai! 🎉",
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception:
            pass

    # Referrer message
    try:
        await client.send_message(
            referrer_id,
            f"🎉 **REFERRAL SUCCESSFUL!**\n\n"
            f"Aapke bheje gaye link se ek naye user ne saare steps complete kar liye hain! Aapko **1 Coin** mil gaya hai. 🎁\n"
            f"💰 Total Balance: `{DB['USER_DATA'][referrer_key]['referral_count']}` Coins",
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception:
        pass

    if milestone_hit:
        try:
            await client.send_message(
                referrer_id,
                f"🏆 **MILESTONE BONUS!**\n\n"
                f"Aapne **{total_now} successful refers** poore kar liye hain — har 5 refers par 1 EXTRA Coin milta hai. "
                f"Aapke wallet me **+1 Bonus Coin** add ho gaya hai! 🎁",
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception:
            pass

    if newly_vip:
        try:
            await client.send_message(
                referrer_id,
                "👑 **VIP REFERRER UNLOCKED!**\n\n"
                "Aapne 25+ dosto ko refer kar diya hai! Aapko ab bot ke andar **👑 VIP Referrer** tag mil gaya hai — "
                "yeh 'My Info' aur 'Refer & Earn' section me dikhega.",
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception:
            pass

    if DB["USER_DATA"][referrer_key].get("tier") == "vip":
        await send_vip_treat(client, referrer_id)

    try:
        await client.send_message(
            referee_id,
            "👋 **WELCOME!**\n\n"
            "Aapne referral link se join kiya hai. Apna **1 Coin Welcome Bonus** paane ke liye, "
            "aapko bhi apna khud ka referral link kam se kam ek dost ko bhejna hoga aur unse join karwana hoga. 🎁\n"
            "Apna link paane ke liye 'Refer & Earn' button dabayein.",
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception:
        pass

# --- COMMANDS ---
async def cmd_add_admin(client: Client, message: Message):
    if not is_owner_msg(message):
        return
    args = get_args(message)
    if not args:
        return await message.reply_text("  Usage: /addadmin <user_id>")
    try:
        new_admin = int(args[0])
        if new_admin not in DB["ADMIN_IDS"]:
            DB["ADMIN_IDS"].append(new_admin)
            await save_data_async()
            await message.reply_text(f"  User `{new_admin}` is now Admin.", parse_mode=ParseMode.MARKDOWN)
        else:
            await message.reply_text("  User is already an Admin.")
    except Exception as e:
        await message.reply_text(f"  Error adding admin: {e}")

async def cmd_del_admin(client: Client, message: Message):
    if not is_owner_msg(message):
        return
    args = get_args(message)
    if not args:
        return await message.reply_text("  Usage: /deladmin <user_id>")
    try:
        target = int(args[0])
        if target in DB["ADMIN_IDS"]:
            DB["ADMIN_IDS"].remove(target)
            await save_data_async()
            await message.reply_text(f"  User `{target}` removed from Admins.", parse_mode=ParseMode.MARKDOWN)
        else:
            await message.reply_text("  User is not an Admin.")
    except Exception as e:
        await message.reply_text(f"  Error removing admin: {e}")

async def cmd_gendemo(client: Client, message: Message):
    if not is_admin_msg(message):
        return
    args = get_args(message)
    if len(args) < 2:
        return await message.reply_text("Usage: /gendemo <user_id> <batch_id>")
    
    try:
        target_uid = int(args[0])
        batch_id = int(args[1])
    except ValueError:
        return await message.reply_text("❌ Error: User ID and Batch ID numbers me hone chahiye.")

    try:
        # Generate link: 3 hours expiry, 1 use limit (Direct join, no request needed)
        expire_ts = time.time() + (3 * 3600)
        l = await client.create_chat_invite_link(
            chat_id=batch_id,
            creates_join_request=False,
            member_limit=1,
            expire_date=datetime.now() + timedelta(hours=3)
        )
        link = l.invite_link

        # Store the demo timer instantly so they get kicked if not upgraded
        target_key = str(target_uid) if str(target_uid) in DB.get("USER_DATA", {}) else target_uid
        DB.setdefault("USER_DATA", {}).setdefault(target_key, {}).setdefault("demos", {})[str(batch_id)] = {
            "expiry": expire_ts,
            "warned": False,
        }
        
        # Map the link so the /per command works perfectly
        DB.setdefault("LINK_MAP", {})[link] = {"u": target_uid, "b": batch_id, "ts": time.time()}
        await save_data_async()

        bname = DB.get("ALL_CHATS", {}).get(batch_id, f"Batch {batch_id}")
        
        # Message to Admin
        await message.reply_text(
            f"✅ **Auto-Demo Link Generated!**\n\n"
            f"📦 **Batch:** `{bname}`\n"
            f"👤 **User:** `{target_uid}`\n"
            f"⏱ **Validity:** `3 Hours`\n\n"
            f"🔗 **Link to share:**\n`{link}`\n\n"
            f"💡 *Give this to the student. They will join instantly for 3 hours.*\n"
            f"🔄 *To make it permanent later, just reply to this with:* `/per {link}`",
            parse_mode=ParseMode.MARKDOWN
        )
        
        # Optionally send it directly to the user
        try:
            await client.send_message(
                target_uid,
                f"🎁 **Admin granted you Demo Access!**\n\n"
                f"📦 **Batch:** `{bname}`\n"
                f"⏱ **Time Limit:** `3 Hours`\n\n"
                f"🔗 Click below to join instantly. **Link expires in 3 hours and works only once.**",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🚀 Join Demo Batch", url=link)]])
            )
        except Exception:
            pass # User might have bot blocked
            
    except Exception as e:
        await message.reply_text(f"❌ Failed to generate demo link: {e}")

async def cmd_deluser(client: Client, message: Message):
    if not is_admin_msg(message):
        return
    args = get_args(message)
    if not args or not args[0].strip().lstrip("-").isdigit():
        return await message.reply_text("❌ Error: Kripya valid numeric User ID bhejein.")

    target = int(args[0].strip())
    target_str = str(target)
    deleted = False

    # 1. Main USER_DATA delete
    if target in DB.get("USER_DATA", {}):
        del DB["USER_DATA"][target]
        deleted = True
    if target_str in DB.get("USER_DATA", {}):
        del DB["USER_DATA"][target_str]
        deleted = True

    # 2. Block List se delete
    if target in DB.get("BLOCKED_USERS", []):
        DB["BLOCKED_USERS"].remove(target)
        deleted = True
    if target_str in DB.get("BLOCKED_USERS", []):
        DB["BLOCKED_USERS"].remove(target_str)
        deleted = True

    # 3. Topics map delete
    if target in DB.get("USER_TOPICS", {}):
        del DB["USER_TOPICS"][target]
        deleted = True
    if target_str in DB.get("USER_TOPICS", {}):
        del DB["USER_TOPICS"][target_str]
        deleted = True

    # 4. Pending Requests & Active Links Delete
    if target_str in DB.get("PENDING_REQUESTS", {}):
        del DB["PENDING_REQUESTS"][target_str]
        deleted = True

    if deleted:
        await save_data_async()
        await message.reply_text(
            f"☢️ **HARD DELETE SUCCESSFUL!**\n\nUser `{target}` ka saara data bot ke database se poori tarah mita diya gaya hai.\nAb agar wo kisi ke refer link se dobara `/start` karega toh usko 100% fresh/new user mana jayega aur referrer ko coin milega.", 
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await message.reply_text(f"⚠️ User `{target}` database me pehle se exist nahi karta.")

async def cmd_storebatch(client: Client, message: Message):
    if not is_owner_msg(message):
        return
    
    args = get_args(message)
    if not args:
        return await message.reply_text("❌ Error: Valid Batch ID bhejein.")
    
    try:
        chat_id = int(args[0])
    except ValueError:
        return await message.reply_text("❌ Error: ID numbers me honi chahiye (jaise -10012345678).")

    # --- USERBOT SESSION FETCH KARNA ---
    from config import DB, API_ID, API_HASH
    session_string = DB.get("USERBOT_SESSION")
    
    if not session_string or not API_ID:
        return await message.reply_text(
            "❌ **Userbot Not Logged In!**\nPehle Owner dashboard se login karein taaki bot history scan kar sake.", 
            parse_mode=ParseMode.MARKDOWN
        )

    msg = await message.reply_text(f"⏳ **Starting Userbot to scan Batch `{chat_id}`...**", parse_mode=ParseMode.MARKDOWN)
    
    # --- USERBOT INITIALIZE KARNA ---
    userbot = Client(
        "store_bot",
        api_id=API_ID,
        api_hash=API_HASH,
        session_string=session_string,
        in_memory=True,
    )

    video_count = 0
    pdf_count = 0
    subjects_dict = {}
    channel_name = f"Batch {chat_id}"

    try:
        await userbot.start()
        
        # Channel ka asli naam Userbot se fetch karenge
        try:
            chat_info = await userbot.get_chat(chat_id)
            channel_name = chat_info.title
        except Exception:
            pass 

        await msg.edit_text(f"⏳ **Scanning '{channel_name}' via Userbot...**\nIsme thoda waqt lag sakta hai, kripya wait karein...", parse_mode=ParseMode.MARKDOWN)
        
        # Userbot ke through History scan karna
        async for m in userbot.get_chat_history(chat_id):
            caption = m.caption or ""
            
            # --- REGEX EXTRACTOR (Captions padhne ka logic) ---
            idx_match = re.search(r"Index:\s*(.*)", caption, re.IGNORECASE)
            title_match = re.search(r"Title:\s*(.*)", caption, re.IGNORECASE)
            sub_match = re.search(r"Subject:\s*(.*)", caption, re.IGNORECASE)

            vid_index = idx_match.group(1).strip() if idx_match else "999"
            vid_title = title_match.group(1).strip() if title_match else "Unknown Media"
            vid_subject = sub_match.group(1).strip() if sub_match else "Other Files"

            if vid_subject not in subjects_dict:
                subjects_dict[vid_subject] = []

            # 1. VIDEO SAVING
            if m.video:
                video_count += 1
                if vid_title == "Unknown Media":
                    vid_title = m.video.file_name or f"Video {m.id}"
                
                thumb_id = m.video.thumbs[0].file_id if m.video.thumbs else None
                
                subjects_dict[vid_subject].append({
                    "index": vid_index,
                    "msg_id": m.id,
                    "type": "video",
                    "title": str(vid_title),
                    "duration": m.video.duration,
                    "size": m.video.file_size,
                    "thumb_id": thumb_id
                })
            
            # 2. PDF SAVING
            elif m.document and m.document.mime_type == "application/pdf":
                pdf_count += 1
                if vid_title == "Unknown Media":
                    vid_title = m.document.file_name or f"PDF Note {m.id}"
                
                subjects_dict[vid_subject].append({
                    "index": vid_index,
                    "msg_id": m.id,
                    "type": "pdf",
                    "title": str(vid_title),
                    "size": m.document.file_size
                })
            
            # Update Live Status
            if (video_count + pdf_count) > 0 and (video_count + pdf_count) % 300 == 0:
                try:
                    await msg.edit_text(f"⏳ **Scanning '{channel_name}' via Userbot...**\n\nFound so far:\n🎥 Videos: `{video_count}`\n📄 PDFs: `{pdf_count}`")
                except: pass
                await asyncio.sleep(1.5)

        # Stop Userbot immediately after scanning
        await userbot.stop()

        # Indexing Sorting
        for sub in subjects_dict:
            subjects_dict[sub].sort(key=lambda x: x.get("index", "999"))

        # --- FIREBASE SAVE LOGIC ---
        import app as backend_app
        import time
        if getattr(backend_app, "db_fs", None):
            final_data = {
                "channel_name": channel_name,
                "chat_id": str(chat_id),
                "subjects": subjects_dict,
                "total_videos": video_count,
                "total_pdfs": pdf_count,
                "last_updated": time.time()
            }
            await asyncio.to_thread(
                backend_app.db_fs.collection('batch_contents').document(str(chat_id)).set, 
                final_data, 
                merge=True
            )
            firebase_status = "✅ Successfully saved to Firebase DB!"
        else:
            firebase_status = "ℹ️ Web database disabled (Bot-only mode)."

        await msg.edit_text(
            f"🎯 **Batch Scan Complete (Userbot)!**\n\n"
            f"📁 Channel: **{channel_name}**\n"
            f"🎥 Total Videos: `{video_count}`\n"
            f"📄 Total PDFs: `{pdf_count}`\n"
            f"📝 {firebase_status}",
            parse_mode=ParseMode.MARKDOWN
        )

    except Exception as e:
        traceback.print_exc()
        await msg.edit_text(f"❌ **Userbot Error during scan:** `{e}`", parse_mode=ParseMode.MARKDOWN)
        try:
            await userbot.stop()
        except:
            pass

async def cmd_userbotphone(client: Client, message: Message):
    if not is_owner_msg(message):
        return
    uid = message.from_user.id
    if not API_ID or API_ID == 0:
        return await message.reply_text(
            "⚠️ **API_ID Missing!** Kripya Cloud Dashboard me API_ID theek karein.",
            parse_mode=ParseMode.MARKDOWN,
        )
    
    # FIX: Don't cut the first part if it's not a command
    text = message.text or ""
    raw_phone = text.split(" ", 1)[-1] if text.startswith("/") else text
    phone = raw_phone.replace(" ", "").strip()
    
    msg = await message.reply_text("⏳ OTP request bhej raha hu, kripya wait karein...")
    temp_client = Client("temp_login", api_id=API_ID, api_hash=API_HASH, in_memory=True)
    await temp_client.connect()
    try:
        sent_code = await temp_client.send_code(phone)
        if not hasattr(client, "user_data_store"):
            client.user_data_store = {}
        client.user_data_store["login_client"] = temp_client
        client.user_data_store["login_phone"] = phone
        client.user_data_store["phone_code_hash"] = sent_code.phone_code_hash
        ADMIN_WIZARD[uid] = {"step": "call_cmd_userbototp"}
        await msg.edit_text(
            "✅ **OTP Bhej diya gaya hai!**\n\nKripya apna OTP yahan type karein.\n**DHYAN DEIN:** OTP space lagakar likhein (Example: `1 2 3 4 5`)",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        await temp_client.disconnect()
        await msg.edit_text(f"❌ Error: `{e}`", parse_mode=ParseMode.MARKDOWN)

async def cmd_userbototp(client: Client, message: Message):
    if not is_owner_msg(message):
        return
    uid = message.from_user.id
    
    # FIX: Safely extract OTP without losing the first digit
    text = message.text or ""
    raw_otp = text.split(" ", 1)[-1] if text.startswith("/") else text
    otp = raw_otp.replace(" ", "").replace("-", "").strip()
    
    user_store = getattr(client, "user_data_store", {})
    phone = user_store.get("login_phone")
    phone_code_hash = user_store.get("phone_code_hash")
    temp_client = user_store.get("login_client")
    
    if not phone or not phone_code_hash or not temp_client:
        return await message.reply_text("⚠️ Session expire ho gaya. Kripya wapas login par click karein.")
    
    msg = await message.reply_text("⏳ OTP Verify kar raha hu...")
    try:
        await temp_client.sign_in(phone, phone_code_hash, otp)
        session_string = await temp_client.export_session_string()
        from config import DB, save_data_async # Ensure safe import
        DB["USERBOT_SESSION"] = session_string
        DB["USERBOT_PHONE"] = phone
        await save_data_async()
        await temp_client.disconnect()
        user_store.pop("login_client", None)
        user_store.pop("login_phone", None)
        user_store.pop("phone_code_hash", None)
        await msg.edit_text(
            "🎉 **LOGIN SUCCESSFUL!**\n\nBina 2FA ke Session Database me save ho gaya hai.",
            parse_mode=ParseMode.MARKDOWN,
        )
    except SessionPasswordNeeded:
        ADMIN_WIZARD[uid] = {"step": "call_cmd_userbotpass"}
        await msg.edit_text(
            "🔒 **2-Step Verification Detected!**\n\nIs account me 2FA on hai. Kripya apna **2FA Password** type karein:",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        await temp_client.disconnect()
        user_store.pop("login_client", None)
        await msg.edit_text(f"❌ OTP Error: `{e}`", parse_mode=ParseMode.MARKDOWN)

async def cmd_userbotpass(client: Client, message: Message):
    if not is_owner_msg(message):
        return
    uid = message.from_user.id
    
    # FIX: Don't lose the first word of the password if it has spaces
    text = message.text or ""
    password = text.split(" ", 1)[-1] if text.startswith("/") else text
    password = password.strip()
    
    user_store = getattr(client, "user_data_store", {})
    phone = user_store.get("login_phone")
    temp_client = user_store.get("login_client")
    
    if not phone or not temp_client:
        return await message.reply_text("⚠️ Session expire ho gaya. Kripya wapas login par click karein.")
    
    msg = await message.reply_text("⏳ Password check kar raha hu...")
    try:
        await temp_client.check_password(password)
        session_string = await temp_client.export_session_string()
        from config import DB, save_data_async
        DB["USERBOT_SESSION"] = session_string
        DB["USERBOT_PHONE"] = phone
        await save_data_async()
        await temp_client.disconnect()
        user_store.pop("login_client", None)
        user_store.pop("login_phone", None)
        await msg.edit_text(
            "🎉 **LOGIN SUCCESSFUL!**\n\n2FA Password verified. Session save ho gaya hai.",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        await temp_client.disconnect()
        user_store.pop("login_client", None)
        await msg.edit_text(f"❌ Password Error: `{e}`", parse_mode=ParseMode.MARKDOWN)
async def cmd_del_msg(client: Client, message: Message):
    if not is_admin_msg(message):
        return

    # Mode 1: Reply to a specific message to delete it (and its bridged support topic message)
    if message.reply_to_message:
        key = (message.chat.id, message.reply_to_message.id)
        if key in MESSAGE_MAP:
            tc, tm = MESSAGE_MAP[key]
            try:
                await client.delete_messages(tc, tm)
                del MESSAGE_MAP[key]
                if (tc, tm) in MESSAGE_MAP:
                    del MESSAGE_MAP[(tc, tm)]
            except Exception:
                pass
        try:
            await client.delete_messages(message.chat.id, [message.reply_to_message.id, message.id])
        except Exception as e:
            logger.error(f"Delete failed: {e}")
            msg = await message.reply_text(f"  Delete failed: {e}")
            await schedule_delete(client, msg)
        return

    # Mode 2: Official Telegram Batch Delete /del <N> (up to 100 messages at once)
    args = get_args(message)
    if args and args[0].isdigit():
        count = min(int(args[0]), 100)
        msg_ids = [message.id]
        try:
            async for m in client.get_chat_history(message.chat.id, limit=count, offset_id=message.id):
                msg_ids.append(m.id)
            await client.delete_messages(message.chat.id, msg_ids)
        except Exception as e:
            logger.error(f"Bulk delete error: {e}")

async def cmd_emptybatch(client: Client, message: Message):
    if not is_admin_msg(message):
        return
    args = get_args(message)
    if not args:
        return
    try:
        cid = int(args[0])
    except ValueError:
        return await message.reply_text("  Error: Valid Batch ID bhejein.")
    session_string = DB.get("USERBOT_SESSION")
    if not session_string or not API_ID:
        return await message.reply_text(
            "  **Userbot Not Logged In!** Pehle Owner dashboard se login karein.",
            parse_mode=ParseMode.MARKDOWN,
        )
    msg = await message.reply_text(
        f"  **Emptying Batch `{cid}`...**\nUserbot start ho raha hai. Isme thoda time lag sakta hai, please wait.",
        parse_mode=ParseMode.MARKDOWN,
    )
    userbot = Client(
        "empty_bot",
        api_id=API_ID,
        api_hash=API_HASH,
        session_string=session_string,
        in_memory=True,
    )
    try:
        await userbot.start()
        try:
            await userbot.get_chat(cid)
        except Exception:
            await msg.edit_text(
                "  **Userbot Syncing...**\nChat list ko memory me load kar raha hu taaki ID mil sake. Isme 5-10 seconds lag sakte hain...",
                parse_mode=ParseMode.MARKDOWN,
            )
            async for dialog in userbot.get_dialogs():
                if dialog.chat.id == cid:
                    break
            await msg.edit_text(
                f"  **Emptying Batch `{cid}`...**\nSync complete! Ab members remove kar raha hu...",
                parse_mode=ParseMode.MARKDOWN,
            )
        removed_count = 0
        async for member in userbot.get_chat_members(cid):
            uid = member.user.id
            if uid != OWNER_ID and not member.user.is_bot and not is_admin(uid):
                try:
                    await client.ban_chat_member(cid, uid)
                    await client.unban_chat_member(cid, uid)
                    removed_count += 1
                    if uid in DB["USER_DATA"] and "demos" in DB["USER_DATA"][uid]:
                        if str(cid) in DB["USER_DATA"][uid]["demos"]:
                            del DB["USER_DATA"][uid]["demos"][str(cid)]
                    await asyncio.sleep(1.5)
                except FloodWait as e:
                    await asyncio.sleep(e.value + 1)
                except Exception:
                    pass
        await save_data_async()
        await userbot.stop()
        await msg.edit_text(
            f"  **Batch `{cid}` Pura Khali Ho Gaya!**\n\nTotal `{removed_count}` users ko remove kiya aur DB se clean kar diya.",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        await msg.edit_text(f"  Error: `{e}`", parse_mode=ParseMode.MARKDOWN)

async def cmd_sync(client: Client, message: Message):
    if not is_admin_msg(message):
        return
    msg = await message.reply_text("  Background sync started manually.")
    asyncio.create_task(background_sync(client))
    await schedule_delete(client, msg)

async def cmd_joinall(client: Client, message: Message):
    if not is_owner_msg(message):
        return
    session_string = DB.get("USERBOT_SESSION")
    if not session_string or not API_ID:
        return await message.reply_text(
            "  Error: Userbot not logged in. Owner dashboard se login karein."
        )
    msg = await message.reply_text("  Auto-joining userbot...")
    userbot = Client(
        "temp_bot",
        api_id=API_ID,
        api_hash=API_HASH,
        session_string=session_string,
        in_memory=True,
    )
    try:
        await userbot.start()
        userbot_id = (await userbot.get_me()).id
        await userbot.stop()
    except Exception as e:
        return await msg.edit_text(f"  Error: {e}")
    all_chats = (
        [MANDATORY_CHANNEL_ID]
        + list(DB.get("FREE_CHANNELS", {}).keys())
        + list(DB.get("PAID_CHANNELS", {}).keys())
        + list(DB.get("SPECIAL_CHANNELS", {}).keys())
    )
    success = failed = 0
    for cid in all_chats:
        try:
            await client.promote_chat_member(
                chat_id=int(cid),
                user_id=userbot_id,
                privileges=pyrogram.types.ChatPrivileges(
                    can_invite_users=True, can_manage_chat=True
                ),
            )
            success += 1
            await asyncio.sleep(0.5)
        except Exception:
            failed += 1
    await msg.edit_text(
        f"  **Auto-Join Process Pura Hua!**\nSuccess: `{success}`\nFailed: `{failed}`",
        parse_mode=ParseMode.MARKDOWN,
    )

async def cmd_lockpaid(client: Client, message: Message):
    if not is_admin_msg(message):
        return
    DB["PAID_LOCKED"] = not DB.get("PAID_LOCKED", False)
    await save_data_async()
    await message.reply_text(
        "Paid Batches **LOCKED  **." if DB["PAID_LOCKED"] else "Paid Batches **UNLOCKED  **.",
        parse_mode=ParseMode.MARKDOWN,
    )

async def cmd_id(client: Client, message: Message):
    chat, user = message.chat, message.from_user
    text = (
        f"  **Your User ID:** `{user.id}`"
        if chat.type == ChatType.PRIVATE and user
        else f"  **Chat ID:** `{chat.id}`"
    )
    if chat.type != ChatType.PRIVATE:
        if message.message_thread_id:
            text += f"\n  **Topic ID:** `{message.message_thread_id}`"
        if user:
            text += f"\n  **User ID:** `{user.id}`"
    try:
        await message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
    except Exception:
        pass
    if user and is_admin(user.id):
        await schedule_delete(client, message)

async def cmd_start(client: Client, message: Message):
    await start(client, message)

async def cmd_backup(client: Client, message: Message):
    if not is_owner_msg(message):
        return
    save_data_sync()
    if os.path.exists(DATA_FILE):
        await message.reply_document(document=DATA_FILE, caption="DB Backup")

async def cmd_all_users(client: Client, message: Message):
    if not is_owner_msg(message):
        return
    report = (
        f"ALL USERS DUMP - {datetime.now()}\n"
        + "-" * 40
        + "\nID | Name | Username\n"
    )
    for uid, data in DB["USER_DATA"].items():
        report += f"{uid} | {data.get('name')} | @{data.get('username')}\n"
    f = io.BytesIO(report.encode("utf-8"))
    f.name = "all_users.txt"
    f.seek(0)
    await message.reply_document(document=f, caption="  All Users List")

async def cmd_ban(client: Client, message: Message):
    if not is_admin_msg(message):
        return
    args = get_args(message)
    if not args:
        return
    try:
        target = int(args[0])
    except ValueError:
        return
    if target not in DB["BLOCKED_USERS"] and target != OWNER_ID:
        await execute_universal_kick(target, client, permanent_ban=True)
        await message.reply_text(f"  User `{target}` BANNED.", parse_mode=ParseMode.MARKDOWN)

async def cmd_unban(client: Client, message: Message):
    if not is_admin_msg(message):
        return
    args = get_args(message)
    target_id = args[0] if args else None
    if not target_id and message.text and len(message.text.split()) > 1:
        target_id = message.text.split()[1].strip()
    if not target_id:
        return await message.reply_text("  Error: Kripya ek User ID bhejein.")
    try:
        target = int(target_id)
    except (ValueError, TypeError):
        return await message.reply_text("  Error: Kripya ek valid Numeric User ID bhejein.")
    modified = False
    if target in DB.get("BLOCKED_USERS", []):
        DB["BLOCKED_USERS"].remove(target)
        modified = True
    if str(target) in DB.get("BLOCKED_USERS", []):
        DB["BLOCKED_USERS"].remove(str(target))
        modified = True
    user_key = target if target in DB["USER_DATA"] else (str(target) if str(target) in DB.get("USER_DATA", {}) else None)
    if user_key:
        DB["USER_DATA"][user_key]["tnc_accepted"] = False
        modified = True
    if modified:
        await save_data_async()
        db_msg = "  Database se unban kiya gaya aur Profile reset kar di gayi."
    else:
        db_msg = "  Database me pehle se unbanned tha."
    all_channels = list(
        set(
            list(DB.get("FREE_CHANNELS", {}).keys())
            + list(DB.get("PAID_CHANNELS", {}).keys())
            + list(DB.get("SPECIAL_CHANNELS", {}).keys())
            + ([MANDATORY_CHANNEL_ID] if MANDATORY_CHANNEL_ID else [])
        )
    )
    success_count = 0
    sem = asyncio.Semaphore(5)

    async def _unban_single(bid):
        nonlocal success_count
        async with sem:
            try:
                await client.unban_chat_member(int(bid), target)
                success_count += 1
            except Exception:
                pass

    if all_channels:
        await asyncio.gather(*[_unban_single(bid) for bid in all_channels], return_exceptions=True)

    await message.reply_text(
        f"  **User `{target}` Successfully Unbanned!**\n{db_msg}\n`{success_count}` channels/groups se unban request bheji gayi.",
        parse_mode=ParseMode.MARKDOWN,
    )

async def cmd_reset_user(client: Client, message: Message):
    if not is_admin_msg(message):
        return
    args = get_args(message)
    if not args:
        return
    target_uid = int(args[0])
    user_key = target_uid if target_uid in DB["USER_DATA"] else str(target_uid)
    if user_key in DB["USER_DATA"]:
        DB["USER_DATA"][user_key]["demos"] = {}
        DB["USER_DATA"][user_key]["demo_history"] = []
        DB["USER_DATA"][user_key]["unlocked_batches"] = []
        if target_uid in DB["BLOCKED_USERS"]:
            DB["BLOCKED_USERS"].remove(target_uid)
        await save_data_async()
        await message.reply_text(f"  User `{target_uid}` reset.", parse_mode=ParseMode.MARKDOWN)

async def cmd_genvoucher(client: Client, message: Message):
    if not is_admin_msg(message):
        return
    args = get_args(message)
    if len(args) < 3:
        return await message.reply_text(
            "🎟️ **Usage:** `/genvoucher <CODE> <POINTS> <MAX_USES>`\n"
            "Example: `/genvoucher DIWALI2026 5 100`",
            parse_mode=ParseMode.MARKDOWN
        )
    code = args[0].upper().strip()
    try:
        pts = float(args[1])
        max_uses = int(args[2])
    except ValueError:
        return await message.reply_text("❌ Error: Points aur Max Uses numeric hone chahiye.")

    DB.setdefault("VOUCHERS", {})[code] = {
        "points": pts,
        "max_uses": max_uses,
        "used_by": [],
        "created_at": time.time()
    }
    await save_data_async()
    copy_btn = make_copy_button(f"📋 Copy Code: {code}", code)
    kb = InlineKeyboardMarkup([[copy_btn]])
    await message.reply_text(
        f"✅ **Promo Voucher Created!**\n━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎟️ **Code:** `{code}`\n"
        f"💰 **Reward:** `{pts} Coins`\n"
        f"👥 **Total Uses:** `{max_uses}`\n\n"
        f"Yeh code users ke sath share karein!",
        reply_markup=kb,
        parse_mode=ParseMode.MARKDOWN
    )

def build_batch_requests_msg(page: int = 0):
    reqs = DB.get("BATCH_REQUESTS", {})
    statuses = DB.setdefault("BATCH_REQUEST_STATUS", {})

    if not reqs:
        return rp.message(
            rp.heading("📋 Batch Demands & Checklist"),
            rp.divider(),
            rp.quote(
                ce("INFO", "ℹ️"), " ", rp.bold("Abhi tak koi batch requests nahi aayi hain."), "\n\n",
                rp.italic("Jab users 'Request Batch' button se demand submit karenge, wo yahan checkbox checklist format me dikhengi.")
            ),
            rp.buttons(rp.btn("🔙 Back to Terminal", "dash_home", style=rp.Style.DANGER))
        )

    def _sort_key(item):
        bname, uids = item
        status = statuses.get(bname, "pending")
        status_rank = 0 if status != "done" else 1
        return (status_rank, -len(uids), bname.lower())

    sorted_items = sorted(reqs.items(), key=_sort_key)
    total = len(sorted_items)
    completed = sum(1 for b, _ in sorted_items if statuses.get(b) == "done")
    pending = total - completed
    pct = int((completed / total) * 100) if total > 0 else 0
    pbar = render_progress_bar(completed, total if total > 0 else 1, length=10)

    PER_PAGE = 5
    total_pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    start_idx = page * PER_PAGE
    end_idx = min(start_idx + PER_PAGE, total)
    page_items = sorted_items[start_idx:end_idx]

    body_elements = [
        rp.heading("📋 Batch Demands & Task Checklist"),
        rp.divider(),
        rp.para(
            ce("STATS", "📊"), " ", rp.bold("Task Progress: "),
            rp.code(f"[{pbar}] {completed}/{total} ({pct}%)")
        ),
        rp.para(
            rp.marked(f" 📦 Total: {total} | ⏳ Pending: {pending} | ✅ Uploaded: {completed} ")
        ),
        rp.divider(),
    ]

    for idx, (bname, uids) in enumerate(page_items, start=start_idx + 1):
        st = statuses.get(bname, "pending")
        is_done = (st == "done")
        badge = ce("CHECK", "✅") if is_done else ce("ALERT", "❗️")
        status_tag = rp.marked(" [☑ Uploaded] ") if is_done else rp.italic(" [☐ Pending] ")
        body_elements.append(
            rp.para(
                badge, " ", rp.bold(f"{idx}. {bname}"), "\n",
                "   ↳ ", rp.bold(f"👥 {len(uids)} Users"), " — ", status_tag
            )
        )

    body_elements.append(rp.divider())
    body_elements.append(
        rp.italic("💡 Niche buttons se checkbox toggle karein. Uploaded tick karte hi users ko auto-notification bhej di jayegi!")
    )

    btn_rows = []
    for sub_idx, (bname, uids) in enumerate(page_items):
        item_global_idx = start_idx + sub_idx
        st = statuses.get(bname, "pending")
        is_done = (st == "done")
        check_icon = "☑" if is_done else "☐"
        short_name = (bname[:18] + "..") if len(bname) > 20 else bname
        label = f"{check_icon} {short_name} ({'Done' if is_done else 'Upload'})"
        style = rp.Style.SUCCESS if is_done else rp.Style.DEFAULT
        btn_rows.append(rp.buttons(rp.btn(label, f"breq_t_{item_global_idx}_{page}", style=style)))

    nav_buttons = []
    if page > 0:
        nav_buttons.append(rp.btn("◀ Prev", f"breq_p_{page - 1}", style=rp.Style.DEFAULT))
    nav_buttons.append(rp.btn(f"📄 {page + 1}/{total_pages}", f"breq_p_{page}", style=rp.Style.DEFAULT))
    if page < total_pages - 1:
        nav_buttons.append(rp.btn("Next ▶", f"breq_p_{page + 1}", style=rp.Style.DEFAULT))

    btn_rows.append(rp.buttons(*nav_buttons))
    btn_rows.append(rp.buttons(
        rp.btn("🔄 Refresh", f"breq_p_{page}", style=rp.Style.DEFAULT),
        rp.btn("🔙 Back to Terminal", "dash_home", style=rp.Style.DANGER)
    ))

    return rp.message(*body_elements, *btn_rows)

async def cmd_batchrequests(client: Client, message: Message):
    if not is_admin_msg(message):
        return
    try:
        msg = build_batch_requests_msg(page=0)
        await rp.send(client, message.chat.id, msg)
    except Exception as e:
        logger.error(f"cmd_batchrequests error: {e}")
        reqs = DB.get("BATCH_REQUESTS", {})
        if not reqs:
            return await message.reply_text("📭 Abhi tak koi batch requests nahi aayi hain.")

        sorted_reqs = sorted(reqs.items(), key=lambda x: len(x[1]), reverse=True)
        txt = "🙋 **USER BATCH REQUESTS & DEMAND**\n━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        for idx, (bname, uids) in enumerate(sorted_reqs[:25], 1):
            txt += f"`{idx}.` **{bname}** — `{len(uids)} Users Requested`\n"
        txt += f"\n📊 Total Unique Requests: `{len(reqs)}`"
        await message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)

async def cmd_restorebatches(client: Client, message: Message):
    if not is_admin_msg(message):
        return

    backup_file = None
    cleanup_temp = False

    if message.document:
        if not (message.document.file_name or "").lower().endswith(".json"):
            return await message.reply_text("❌ Error: Kripya valid `.json` backup file bhejein.")
        dl_msg = await message.reply_text("⏳ Backup JSON download ho raha hai...")
        backup_file = await message.download()
        cleanup_temp = True
        try:
            await dl_msg.delete()
        except Exception:
            pass
    else:
        args = get_args(message)
        if args and os.path.exists(args[0]):
            backup_file = args[0]
        elif os.path.exists("backup.json"):
            backup_file = "backup.json"
        elif os.path.exists("old_backup.json"):
            backup_file = "old_backup.json"
        else:
            return await message.reply_text(
                "📥 **Usage: /restorebatches**\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "1️⃣ Backup JSON file ko **Document** ke taur par attach karein aur caption me `/restorebatches` likhein.\n"
                "2️⃣ Ya bot folder me `backup.json` rakh kar `/restorebatches` command chalayein.\n\n"
                "🛡️ **Safe Merge Guarantee:**\n"
                "• Jo batches aapne abhi manually add/update kiye hain, wo waise hi rahenge (kuch overwrite nahi hoga).\n"
                "• Sirf missing batches Userbot ke through channels me bot ko add & full admin promote karke restore honge!",
                parse_mode=ParseMode.MARKDOWN
            )

    status_msg = await message.reply_text(
        "🚀 **Batch Restoration Engine Started...**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "• Parsing backup JSON...\n"
        "• Checking existing database (skipping manual updates)...\n"
        "• Starting Userbot to add & promote bot in missing channels...",
        parse_mode=ParseMode.MARKDOWN
    )

    try:
        from restore_batches import run_batch_restore
        res = await run_batch_restore(backup_file)

        if not res.get("success"):
            return await status_msg.edit_text(
                f"❌ **Restoration Failed:**\n`{res.get('error')}`",
                parse_mode=ParseMode.MARKDOWN
            )

        restored_cnt = res.get("restored_count", 0)
        skipped_cnt = res.get("skipped_count", 0)
        failed_cnt = res.get("failed_count", 0)

        preview_lines = []
        for b in (res.get("restored") or [])[:8]:
            p_tag = "👑 Admin" if b.get("promoted") else "⚠️ Member"
            preview_lines.append(f"• `{b['title']}` ({b['type'].upper()}) -> {b['category']} [{p_tag}]")

        if len(res.get("restored", [])) > 8:
            preview_lines.append(f"• ...aur {len(res['restored']) - 8} batches aur.")

        preview_txt = "\n".join(preview_lines) if preview_lines else "Koi naya batch restore karne ki zaroorat nahi thi."

        report = (
            f"🎉 **BATCH RESTORATION COMPLETE!**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ **Restored & Connected:** `{restored_cnt}` Batches\n"
            f"⏩ **Skipped (Preserved):** `{skipped_cnt}` Batches *(Manual updates bilkul safe rahe)*\n"
            f"❌ **Failed:** `{failed_cnt}` Batches\n\n"
            f"📋 **Restored Preview:**\n{preview_txt}\n\n"
            f"💾 **MongoDB & Local DB 100% Synced!**"
        )
        await status_msg.edit_text(report, parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        logger.error(f"Error during cmd_restorebatches: {e}")
        await status_msg.edit_text(f"❌ **Error:** `{e}`", parse_mode=ParseMode.MARKDOWN)
    finally:
        if cleanup_temp and backup_file and os.path.exists(backup_file):
            try:
                os.remove(backup_file)
            except Exception:
                pass

async def cmd_list_vouchers(client: Client, message: Message):
    if not is_admin_msg(message):
        return
    vouchers = DB.get("VOUCHERS", {})
    if not vouchers:
        return await message.reply_text("📭 Abhi tak koi vouchers create nahi kiye gaye hain.")
    txt = "🎟️ **ACTIVE PROMO VOUCHERS**\n━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    for code, data in vouchers.items():
        used = len(data.get("used_by", []))
        max_u = data.get("max_uses", 1)
        pts = data.get("points", 0)
        txt += f"• `{code}`: `{pts} Coins` | `{used}/{max_u} Used`\n"
    await message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)

async def cmd_set_batch_price(client: Client, message: Message):
    if not is_admin_msg(message):
        return
    args = get_args(message)
    if len(args) < 2:
        return await message.reply_text(
            "💰 **Usage:** `/setprice <batch_id> <price>`\nExample: `/setprice -100123456789 ₹499`",
            parse_mode=ParseMode.MARKDOWN
        )
    raw_id = args[0].strip()
    price_val = " ".join(args[1:]).strip()
    try:
        cid = int(raw_id)
    except ValueError:
        return await message.reply_text("❌ Error: Batch ID numbers me honi chahiye.")
    
    cname = DB.get("ALL_CHATS", {}).get(cid) or DB.get("PAID_CHANNELS", {}).get(cid) or f"Batch {cid}"
    DB.setdefault("BATCH_PRICES", {})[str(cid)] = price_val
    await save_data_async()
    await message.reply_text(
        f"✅ **Batch Price Updated!**\n━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 **Batch:** `{cname}` (`{cid}`)\n"
        f"💰 **Price / Fee:** `{price_val}`",
        parse_mode=ParseMode.MARKDOWN
    )

async def cmd_set_request_note(client: Client, message: Message):
    if not is_admin_msg(message):
        return
    args = message.text.split(None, 1)
    if len(args) < 2 or not args[1].strip():
        current = DB.get("BATCH_REQUEST_NOTE", "Not set")
        return await message.reply_text(
            f"📝 **Current Batch Request Note:**\n\n{current}\n\n"
            f"📌 **To update, use:** `/setrequestnote <Your custom message>`",
            parse_mode=ParseMode.MARKDOWN
        )
    new_note = args[1].strip()
    DB["BATCH_REQUEST_NOTE"] = new_note
    await save_data_async()
    await message.reply_text(
        f"✅ **Batch Request Note Updated!**\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n{new_note}",
        parse_mode=ParseMode.MARKDOWN
    )

async def cmd_genvoucher_from_wizard(client: Client, message: Message):
    if not is_admin_msg(message):
        return
    parts = (message.text or "").strip().split()
    if len(parts) < 3:
        return await message.reply_text(
            "❌ Format invalid! Kripya is format me bhejein:\n`<CODE> <COINS> <MAX_USES>`\nExample: `DIWALI 5 100`",
            parse_mode=ParseMode.MARKDOWN
        )
    code = parts[0].upper().strip()
    try:
        pts = float(parts[1])
        max_uses = int(parts[2])
    except ValueError:
        return await message.reply_text("❌ Error: Coins aur Max Uses numeric hone chahiye.")

    DB.setdefault("VOUCHERS", {})[code] = {
        "points": pts,
        "max_uses": max_uses,
        "used_by": [],
        "created_at": time.time()
    }
    await save_data_async()
    copy_btn = make_copy_button(f"📋 Copy Code: {code}", code)
    kb = InlineKeyboardMarkup([[copy_btn]])
    await message.reply_text(
        f"✅ **Promo Voucher Created!**\n━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎟️ **Code:** `{code}`\n"
        f"💰 **Reward:** `{pts} Coins`\n"
        f"👥 **Total Uses:** `{max_uses}`\n\n"
        f"Yeh code users ke sath share karein!",
        reply_markup=kb,
        parse_mode=ParseMode.MARKDOWN
    )

async def cmd_find_user(client: Client, message: Message):
    if not is_admin_msg(message):
        return
    args = get_args(message)
    if not args:
        return
    query = args[0].replace("@", "").lower()
    found = []
    for uid, data in DB["USER_DATA"].items():
        if query in data.get("username", "").lower():
            found.append(f"  `{uid}` | @{data.get('username', '')}")
    await message.reply_text(
        "  **Found:**\n\n" + "\n".join(found) if found else "  Not found.",
        parse_mode=ParseMode.MARKDOWN,
    )

async def cmd_user_lookup(client: Client, message: Message):
    """🔍 Specific User Data — pura profile ek jagah: name, username, coins, joined channels, support topic link."""
    if not is_admin_msg(message):
        return
    args = get_args(message)
    if not args or not args[0].strip().lstrip("-").isdigit():
        return await message.reply_text("Usage: Sirf User ID bhejein (e.g. `123456789`)")

    target = int(args[0].strip())
    user_key = target if target in DB["USER_DATA"] else str(target)

    if user_key not in DB["USER_DATA"]:
        return await message.reply_text(f"❌ User `{target}` database me nahi mila.", parse_mode=ParseMode.MARKDOWN)

    rec = DB["USER_DATA"][user_key]
    msg = await message.reply_text("🔍 Fetching user data...")

    # --- Sab channels ka membership PARALLEL check karo (fast, sequential nahi) ---
    all_chats = list(DB.get("ALL_CHATS", {}).items())

    async def _check(cid, cname):
        return cname, await get_membership_cached(client, cid, target)

    results = await asyncio.gather(*[_check(cid, cname) for cid, cname in all_chats], return_exceptions=True)

    joined_lines, not_joined_lines = [], []
    for r in results:
        if isinstance(r, Exception):
            continue
        cname, joined = r
        (joined_lines if joined else not_joined_lines).append(f"{'✅' if joined else '❌'} {cname}")
    channels_txt = "\n".join(joined_lines + not_joined_lines) or "_Koi batch configured nahi hai._"

    # --- Support topic ka direct link ---
    topic_id = DB.get("USER_TOPICS", {}).get(target) or DB.get("USER_TOPICS", {}).get(str(target))
    if topic_id and SUPPORT_GROUP_ID:
        group_id_str = str(SUPPORT_GROUP_ID).replace("-100", "")
        topic_link = f"https://t.me/c/{group_id_str}/{topic_id}"
    else:
        topic_link = "_No support topic yet._"

    name = rec.get("name") or "N/A"
    username = f"@{rec.get('username')}" if rec.get("username") else "N/A"
    coins = rec.get("referral_count", 0)
    total_inv = rec.get("total_invited", 0)
    tier = "👑 VIP" if rec.get("tier") == "vip" else "Normal"
    tnc = "✅ Yes" if rec.get("tnc_accepted") else "❌ No"

    file_txt = (
        f"USER DATA LOOKUP\n"
        f"========================================\n"
        f"User ID        : {target}\n"
        f"Name            : {name}\n"
        f"Username        : {username}\n"
        f"Tag             : {tier}\n"
        f"TnC Accepted    : {tnc}\n"
        f"Coins           : {coins}\n"
        f"Total Referred  : {total_inv}\n"
        f"Support Topic   : {topic_link}\n"
        f"========================================\n"
        f"Channel Membership:\n{channels_txt}\n"
        f"========================================\n"
        f"Generated: {datetime.now()}\n"
    )

    f = io.BytesIO(file_txt.encode("utf-8"))
    f.name = f"user_{target}_data.txt"
    f.seek(0)

    kb = [[InlineKeyboardButton("🎁 Gift Coin to this User", callback_data=f"giftcoin_direct_{target}")]]
    await msg.delete()
    await message.reply_document(
        document=f,
        caption=f"🔍 **USER DATA — `{target}`**\n📛 {name} | {username}",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.MARKDOWN,
    )

async def cmd_addcat(client: Client, message: Message):
    if not is_admin_msg(message):
        return
    args = get_args(message)
    if not args:
        return
    new_cat = " ".join(args).strip()
    categories = DB.get("CATEGORIES", DEFAULT_CATEGORIES)
    if new_cat not in categories:
        DB["CATEGORIES"].append(new_cat)
        await save_data_async()
    await message.reply_text(f"  Added Category: {new_cat}")

async def cmd_setcategory(client: Client, message: Message):
    if not is_admin_msg(message):
        return
    raw_text = message.text or message.caption or ""
    ids = re.findall(r"-?\d+", raw_text)
    if not ids:
        return await message.reply_text("  Error: Koi valid ID nahi mili.")
    if not hasattr(client, "user_data_store"):
        client.user_data_store = {}
    client.user_data_store["setcat_ids"] = ids
    kb = [
        [InlineKeyboardButton(f"📁 {c}", callback_data=f"setextcat_{i}")]
        for i, c in enumerate(DB.get("CATEGORIES", DEFAULT_CATEGORIES))
    ]
    await message.reply_text(
        f"  **{len(ids)} Batches** detect hue hain.\nIn sabhi ke liye nayi category select karein:",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.MARKDOWN,
    )

async def cmd_delcat(client: Client, message: Message):
    if not is_admin_msg(message):
        return
    kb = [
        [InlineKeyboardButton(f"🗑️ Delete: {c}", callback_data=f"delcat_{i}")]
        for i, c in enumerate(DB.get("CATEGORIES", DEFAULT_CATEGORIES))
        if c != "Other Batches"
    ]
    kb.append([InlineKeyboardButton("❌ Cancel", callback_data="dash_home")])
    await message.reply_text(
        "  **Delete Category:**",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.MARKDOWN,
    )

async def cmd_batch_stats(client: Client, message: Message):
    if not is_admin_msg(message):
        return
    msg = await message.reply_text("  Calculating...")
    batches = {
        **DB.get("FREE_CHANNELS", {}),
        **DB.get("PAID_CHANNELS", {}),
        **DB.get("SPECIAL_CHANNELS", {})
    }
    async def _count(cid):
        try:
            return await client.get_chat_members_count(int(cid))
        except Exception:
            return "N/A"
    counts = await asyncio.gather(*[_count(cid) for cid in batches.keys()])
    text = "  **BATCH STATS**\n\n"
    for (cid, name), count in zip(batches.items(), counts):
        text += f"  **{name}** | ID: `{cid}` | Members: `{count}`\n"
    await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN)

async def cmd_set_welcome(client: Client, message: Message):
    if not is_admin_msg(message):
        return
    args = get_args(message)
    if len(args) < 2:
        return
    DB.setdefault("CUSTOM_WELCOMES", {})[int(args[0])] = " ".join(args[1:])
    await save_data_async()
    await message.reply_text("  Welcome Set.")

async def cmd_set_testbot(client: Client, message: Message):
    if not is_admin_msg(message):
        return
    args = get_args(message)
    if not args:
        return
    DB["TEST_BOT_LINK"] = args[0]
    await save_data_async()
    await message.reply_text("  Test bot link updated.")

async def cmd_set_vip_materials(client: Client, message: Message):
    """Usage: /setvipmaterials <link> — VIP-only 'VIP Course Materials' button ka target set karta hai."""
    if not is_admin_msg(message):
        return
    args = get_args(message)
    if not args:
        return await message.reply_text("Usage: /setvipmaterials <link>")
    DB["VIP_MATERIALS_LINK"] = args[0]
    await save_data_async()
    await message.reply_text("  👑 VIP Materials link updated.")

async def cmd_set_vip_sticker(client: Client, message: Message):
    """Reply to a sticker/animation with /setvipsticker — VIPs ko is par set treat milega har milestone/unlock par."""
    if not is_admin_msg(message):
        return
    if not message.reply_to_message or not (message.reply_to_message.sticker or message.reply_to_message.animation):
        return await message.reply_text("Kisi sticker ya GIF ko reply karke /setvipsticker bhejein.")
    file_id = (
        message.reply_to_message.sticker.file_id
        if message.reply_to_message.sticker
        else message.reply_to_message.animation.file_id
    )
    DB["VIP_STICKER_ID"] = file_id
    DB["VIP_STICKER_TYPE"] = "sticker" if message.reply_to_message.sticker else "animation"
    await save_data_async()
    await message.reply_text("  👑 VIP treat sticker/GIF set ho gaya.")

async def cmd_extend_demo(client: Client, message: Message):
    if not is_admin_msg(message):
        return
    args = get_args(message)
    if len(args) < 3:
        return
    uid, bid, hours = int(args[0]), str(args[1]), float(args[2])
    if uid in DB["USER_DATA"] and bid in DB["USER_DATA"].get(uid, {}).get("demos", {}):
        d = DB["USER_DATA"][uid]["demos"][bid]
        DB["USER_DATA"][uid]["demos"][bid] = {
            "expiry": max(
                (d["expiry"] if isinstance(d, dict) else float(d)), time.time()
            )
            + (hours * 3600),
            "warned": False,
        }
        await save_data_async()
        await message.reply_text("  Extended.")

async def cmd_kick_user(client: Client, message: Message):
    if not is_admin_msg(message):
        return
    args = get_args(message)
    if len(args) < 2:
        return await message.reply_text("  Usage: /kick <user_id> <batch_id>")
    uid, bid = int(args[0]), int(args[1])
    try:
        await client.ban_chat_member(bid, uid)
        await client.unban_chat_member(bid, uid)
        if uid in DB["USER_DATA"] and str(bid) in DB["USER_DATA"].get(uid, {}).get("demos", {}):
            del DB["USER_DATA"][uid]["demos"][str(bid)]
            await save_data_async()
        await message.reply_text("  Kicked successfully.")
    except Exception as e:
        await message.reply_text(f"  Kick failed: {e}")

async def cmd_myinfo(client: Client, message: Message):
    if not message.from_user:
        return
    uid = message.from_user.id
    user_key = uid if uid in DB["USER_DATA"] else str(uid)
    refer_points = DB["USER_DATA"].get(user_key, {}).get("referral_count", 0)
    total_invited = DB["USER_DATA"].get(user_key, {}).get("total_invited", 0)
    
    txt = (
        f"👤 **MY INFO**\n"
        f"🆔 **ID:** `{uid}`\n"
        f"👥 **Total Refers:** `{total_invited}`\n"
        f"🎁 **Available Coins:** `{refer_points}`\n"
    )
    await message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)

async def cmd_approve_demo(client: Client, message: Message):
    if not is_admin_msg(message):
        return
    args = get_args(message)
    link = None
    hours = 3.0
    if message.reply_to_message:
        replied = message.reply_to_message
        msg_text = replied.text or replied.caption or ""
        m = re.search(r"(https?://t\.me/(?:\+|joinchat/)[a-zA-Z0-9_\-]+)", msg_text)
        if m:
            link = m.group(1)
        if args:
            try:
                hours = float(args[0].lower().replace("h", ""))
            except Exception:
                pass

    if not link and args:
        for arg in args:
            if "t.me" in arg:
                link = arg.strip()
            elif "h" in arg.lower():
                try:
                    hours = float(arg.lower().replace("h", ""))
                except Exception:
                    pass

    if not link:
        return await message.reply_text("  Error: Link nahi mila.")

    ld = DB.get("LINK_MAP", {}).get(link)
    if not ld:
        return await message.reply_text("  Error: Ye link database me registered nahi hai.")

    target_uid = ld.get("u") if isinstance(ld, dict) else None
    batch_id = ld.get("b") if isinstance(ld, dict) else ld
    if not target_uid or not batch_id:
        return await message.reply_text("  Error: Invalid data in database for this link.")

    try:
        await client.approve_chat_join_request(batch_id, target_uid)
        invalidate_membership_cache(target_uid, batch_id)
        expiry_time = time.time() + (hours * 3600)
        DB["USER_DATA"].setdefault(target_uid, {}).setdefault("demos", {})[str(batch_id)] = {
            "expiry": expiry_time,
            "warned": False,
        }
        clear_active_request(target_uid, batch_id)
        DB.get("LINK_MAP", {}).pop(link, None)
        await save_data_async()
        await message.reply_text(
            f"  **APPROVED (DEMO)**\n  Time Given: `{hours} Hours`",
            parse_mode=ParseMode.MARKDOWN,
        )
        try:
            bname = DB["ALL_CHATS"].get(int(batch_id), f"Batch {batch_id}")
            user_msg = (
                f"  **Congratulations!**\n\n"
                f"Aapki request **{bname}** ke liye approve ho gayi hai.\n\n"
                f"  **Access Type:** Demo Trial\n"
                f"  **Duration:** `{hours} Hours`\n\n"
                f"Kripya diye gaye samay me batch access kar lein."
            )
            await client.send_message(target_uid, user_msg, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            logger.error(f"User demo notification failed: {e}")
    except Exception as e:
        await message.reply_text(f"  Approval failed: {e}")

async def cmd_approve_perm(client: Client, message: Message):
    if not is_admin_msg(message):
        return
    link = None
    args = get_args(message)
    if message.reply_to_message:
        replied = message.reply_to_message
        msg_text = replied.text or replied.caption or ""
        m = re.search(r"(https?://t\.me/(?:\+|joinchat/)[a-zA-Z0-9_\-]+)", msg_text)
        if m:
            link = m.group(1)

    if not link and args:
        link = args[0]

    if not link:
        return await message.reply_text("  Error: Link nahi mila. Kripya link provide karein.")

    ld = DB.get("LINK_MAP", {}).get(link)
    if not ld:
        return await message.reply_text("  Error: Ye link database me registered nahi hai.")

    target_uid = ld.get("u") if isinstance(ld, dict) else None
    batch_id = ld.get("b") if isinstance(ld, dict) else ld
    if not target_uid or not batch_id:
        return await message.reply_text("  Error: Invalid data in database for this link.")

    try:
        try:
            await client.approve_chat_join_request(batch_id, target_uid)
        except Exception:
            pass # Accept direct links that bypass join requests (like /gendemo)
            
        invalidate_membership_cache(target_uid, batch_id)
        
        # Remove Demo Timer -> Upgrade to Permanent
        target_key = str(target_uid) if str(target_uid) in DB.get("USER_DATA", {}) else target_uid
        if str(batch_id) in DB.get("USER_DATA", {}).get(target_key, {}).get("demos", {}):
            del DB["USER_DATA"][target_key]["demos"][str(batch_id)]
            
        clear_active_request(target_uid, batch_id)
        DB.get("LINK_MAP", {}).pop(link, None)
        await save_data_async()
        await message.reply_text("  **APPROVED (PERM)**\nUser now has lifetime permanent access.", parse_mode=ParseMode.MARKDOWN)
        try:
            bname = DB["ALL_CHATS"].get(int(batch_id), f"Batch {batch_id}")
            user_msg = (
                f"  **Congratulations!**\n\n"
                f"Aapki request **{bname}** ke liye approve ho gayi hai.\n\n"
                f"  **Access Type:** Lifetime Premium Access\n\n"
                f"Welcome to the premium community! Ab aap jab chahein apne batches section se isey access kar sakte hain."
            )
            await client.send_message(target_uid, user_msg, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            logger.error(f"User perm notification failed: {e}")
    except Exception as e:
        await message.reply_text(f"  Approval failed: {e}")

async def cmd_delbatch(client: Client, message: Message):
    if not is_admin_msg(message):
        return
    args = get_args(message)
    if len(args) < 2:
        return await message.reply_text("Usage: /delbatch <free/paid/special> <batch_id>")
    t, cid = args[0].lower(), int(args[1])
    
    if t == "free":
        d = DB.get("FREE_CHANNELS", {})
    elif t == "paid":
        d = DB.get("PAID_CHANNELS", {})
    elif t == "special":
        d = DB.get("SPECIAL_CHANNELS", {})
    else:
        return await message.reply_text("Error: Type must be free, paid, or special.")

    if cid in d:
        del d[cid]
        if cid in DB.get("ALL_CHATS", {}):
            del DB["ALL_CHATS"][cid]
        if str(cid) in DB.get("BATCH_CATEGORIES", {}):
            del DB["BATCH_CATEGORIES"][str(cid)]
        await save_data_async()
        await message.reply_text("  Batch poori tarah database se Delete ho gaya.")

async def cmd_addbatch_start(client: Client, message: Message):
    if not message.from_user or not is_admin(message.from_user.id):
        return
    ADMIN_WIZARD[message.from_user.id] = {"step": "ask_cat"}
    kb = []
    categories = DB.get("CATEGORIES", DEFAULT_CATEGORIES)
    for i in range(0, len(categories), 2):
        row = [InlineKeyboardButton(f"📁 {categories[i]}", callback_data=f"wcat_{i}")]
        if i + 1 < len(categories):
            row.append(
                InlineKeyboardButton(f"📁 {categories[i + 1]}", callback_data=f"wcat_{i+1}")
            )
        kb.append(row)
    await message.reply_text(
        "  **Add Batch Wizard**\nSelect Category:",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.MARKDOWN,
    )

async def cmd_broadcast_start(client: Client, message: Message):
    if not message.from_user:
        return
    BROADCAST_STATE[message.from_user.id] = {"type": "broadcast", "step": "wait_msg"}
    guide_text = (
        "📢 **Super Broadcast Engine**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Ab aap jo message bhejenge wo bot ke sabhi users ko deliver kiya jayega.\n\n"
        "💡 **Pro Tips for Modern Styling & Colors:**\n"
        "• **Colored Box (Green/Orange):** Message me ````diff```` code block use karein (`+ text` for green, `! text` for orange).\n"
        "• **Expandable Dropdown:** Text ko `**<blockquote expandable>Details</blockquote>**` me daalein.\n"
        "• **Media Supported:** Text ke alawa photo, video ya document bhi bhej sakte hain!\n\n"
        "👉 *Kripya apna broadcast message yahan bhejein:*"
    )
    await message.reply_text(guide_text, parse_mode=ParseMode.MARKDOWN)

async def cmd_post_start(client: Client, message: Message):
    if not message.from_user:
        return
    BROADCAST_STATE[message.from_user.id] = {"type": "post", "step": "wait_msg"}
    await message.reply_text("  Send message to post.")

async def cmd_user_details(client: Client, message: Message):
    if not is_admin_msg(message):
        return
    args = get_args(message)
    try:
        target_id = int(args[0])
    except Exception:
        return await message.reply_text("Usage: /user [id]")
    info = DB["USER_DATA"].get(target_id, {})
    r = (
        f"USER DETAILS: {target_id}\nName: {info.get('name', 'Unknown')}\n\n"
        + ("  BLOCKED\n\n" if target_id in DB.get("BLOCKED_USERS", []) else "")
        + "--- MEMBERSHIP ---\n"
    )
    all_target_cids = list(set(
        list(DB.get("ALL_CHATS", {}).keys())
        + list(DB.get("FREE_CHANNELS", {}).keys())
        + list(DB.get("PAID_CHANNELS", {}).keys())
        + list(DB.get("SPECIAL_CHANNELS", {}).keys())
    ))
    found = False
    sem = asyncio.Semaphore(5)

    async def _check_membership(cid):
        async with sem:
            try:
                m = await client.get_chat_member(int(cid), target_id)
                if m.status in [
                    ChatMemberStatus.MEMBER,
                    ChatMemberStatus.ADMINISTRATOR,
                    ChatMemberStatus.OWNER,
                    ChatMemberStatus.RESTRICTED,
                ]:
                    chat_name = DB.get('ALL_CHATS', {}).get(cid, cid)
                    return f"{chat_name}: Joined\n"
            except Exception:
                pass
            return None

    results = await asyncio.gather(*[_check_membership(cid) for cid in all_target_cids], return_exceptions=True)
    for res in results:
        if isinstance(res, str) and res:
            r += res
            found = True
    if not found:
        r += "Not found in any batch.\n"
    if "demo_history" in info:
        r += "\n--- DEMO HISTORY ---\n" + "\n".join([f"  {h}" for h in info["demo_history"]])
    f = io.BytesIO(r.encode("utf-8"))
    f.name = f"scan_{target_id}.txt"
    f.seek(0)
    await message.reply_document(document=f, caption="  Deep Scan")

async def cmd_batches(client: Client, message: Message):
    if not is_admin_msg(message):
        return
    r = (
        "ALL BATCHES\n"
        + "=" * 30
        + "\n"
        + "\n".join([
            f"{cid} | {DB['ALL_CHATS'].get(cid, 'Unknown')}"
            for cid in set(
                list(DB.get("ALL_CHATS", {}).keys())
                + list(DB.get("FREE_CHANNELS", {}).keys())
                + list(DB.get("PAID_CHANNELS", {}).keys())
                + list(DB.get("SPECIAL_CHANNELS", {}).keys())
            )
        ])
    )
    f = io.BytesIO(r.encode("utf-8"))
    f.name = "batches.txt"
    f.seek(0)
    await message.reply_document(document=f)

async def cmd_stats(client: Client, message: Message):
    if not is_admin_msg(message):
        return
    t = (
        f"  **Statistics**\n"
        f"  Storage: {'MongoDB  ' if MONGO_URL else 'Local  '}\n"
        f"  Lockdown: {'  ON' if not DB.get('NEW_USERS_ALLOWED', True) else '  OFF'}\n"
        f"  Free Locked: {'  YES' if DB.get('FREE_LOCKED', False) else '  NO'}\n"
        f"  Paid Locked: {'  YES' if DB.get('PAID_LOCKED', False) else '  NO'}\n"
        f"  Test Bot Locked: {'  YES' if DB.get('TEST_BOT_LOCKED', False) else '  NO'}\n\n"
        f"  Users: {len(DB.get('USER_DATA', {}))}\n"
        f"  Free: {len(DB.get('FREE_CHANNELS', {}))}\n"
        f"  Paid: {len(DB.get('PAID_CHANNELS', {}))}\n"
        f"  Special: {len(DB.get('SPECIAL_CHANNELS', {}))}\n"
        f"  Blocked: {len(DB.get('BLOCKED_USERS', []))}"
    )
    await message.reply_text(t, parse_mode=ParseMode.MARKDOWN)

async def cmd_cancel(client: Client, message: Message):
    if not message.from_user:
        return
    uid = message.from_user.id
    if uid in BROADCAST_STATE:
        del BROADCAST_STATE[uid]
    if uid in ADMIN_WIZARD:
        del ADMIN_WIZARD[uid]
    await message.reply_text("  Cancelled")

async def cmd_lockdown(client: Client, message: Message):
    if not is_admin_msg(message):
        return
    DB["NEW_USERS_ALLOWED"] = not DB.get("NEW_USERS_ALLOWED", True)
    await save_data_async()
    msg = await message.reply_text(
        "  **Lockdown Lifted!**" if DB["NEW_USERS_ALLOWED"] else "  **Lockdown Enabled!**",
        parse_mode=ParseMode.MARKDOWN,
    )
    await schedule_delete(client, msg)

async def cmd_lockfree(client: Client, message: Message):
    if not is_admin_msg(message):
        return
    DB["FREE_LOCKED"] = not DB.get("FREE_LOCKED", False)
    await save_data_async()
    await message.reply_text(
        "Free Batches **LOCKED  **." if DB["FREE_LOCKED"] else "Free Batches **UNLOCKED  **.",
        parse_mode=ParseMode.MARKDOWN,
    )

async def cmd_locktestbot(client: Client, message: Message):
    if not is_admin_msg(message):
        return
    DB["TEST_BOT_LOCKED"] = not DB.get("TEST_BOT_LOCKED", False)
    await save_data_async()
    await message.reply_text(
        "Test Bot **LOCKED  **." if DB["TEST_BOT_LOCKED"] else "Test Bot **UNLOCKED  **.",
        parse_mode=ParseMode.MARKDOWN,
    )

async def cmd_clear(client: Client, message: Message):
    if not is_owner_msg(message):
        return
    session_string = DB.get("USERBOT_SESSION")
    if not session_string or not API_ID:
        return await message.reply_text(
            "  **Userbot Not Logged In!** Dashboard se login karein.",
            parse_mode=ParseMode.MARKDOWN,
        )
    msg = await message.reply_text("  **Super Exit /clear Start...**", parse_mode=ParseMode.MARKDOWN)
    userbot = Client(
        "clear_bot",
        api_id=API_ID,
        api_hash=API_HASH,
        session_string=session_string,
        in_memory=True,
    )
    try:
        await userbot.start()
        await msg.edit_text(
            "  **Userbot Syncing...**\nSaare chats ko memory me load kar raha hu...",
            parse_mode=ParseMode.MARKDOWN,
        )
        async for _ in userbot.get_dialogs():
            pass
        await msg.edit_text(
            "  **Super Exit /clear Start...**\nSync complete! Ab removing process chalu hai...",
            parse_mode=ParseMode.MARKDOWN,
        )
        all_channels = (
            list(DB.get("FREE_CHANNELS", {}).keys())
            + list(DB.get("PAID_CHANNELS", {}).keys())
            + list(DB.get("SPECIAL_CHANNELS", {}).keys())
        )
        removed_count = checked_users = safe_users = 0
        membership_memo = {}
        for bid in all_channels:
            try:
                async for member in userbot.get_chat_members(int(bid)):
                    uid = member.user.id
                    checked_users += 1
                    if uid != OWNER_ID and not member.user.is_bot:
                        if uid not in membership_memo:
                            try:
                                await userbot.get_chat_member(int(MANDATORY_CHANNEL_ID), uid)
                                membership_memo[uid] = True
                            except Exception:
                                membership_memo[uid] = False

                        is_in_main = membership_memo[uid]
                        if is_in_main:
                            safe_users += 1
                        else:
                            try:
                                await client.ban_chat_member(int(bid), uid)
                                await client.unban_chat_member(int(bid), uid)
                                removed_count += 1
                                await asyncio.sleep(1.0)
                            except Exception:
                                pass
                    await asyncio.sleep(0.05)
            except Exception:
                pass
        await userbot.stop()
        await msg.edit_text(
            f"  **/clear Process Pura Hua!**\n\nChecked: `{checked_users}`\nSafe: `{safe_users}`\nRemoved: `{removed_count}`",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        await msg.edit_text(f"  Error: `{e}`", parse_mode=ParseMode.MARKDOWN)

async def cmd_maintenance(client: Client, message: Message):
    if not is_admin_msg(message):
        return
    DB["MAINTENANCE_MODE"] = not DB.get("MAINTENANCE_MODE", False)
    await save_data_async()
    msg = await message.reply_text(
        "  **Maintenance Mode Enabled!**\nNormal users ka support message ab aana band ho gaya hai."
        if DB["MAINTENANCE_MODE"]
        else "  **Maintenance Mode Disabled!**\nBot ab normally kaam kar raha hai.",
        parse_mode=ParseMode.MARKDOWN,
    )
    await schedule_delete(client, msg)

async def cmd_superfwd_start(client: Client, message: Message):
    source_id = message.text.strip()
    uid = message.from_user.id
    ADMIN_WIZARD[uid] = {"step": "superfwd_target", "source": source_id}
    await message.reply_text(
        f"✅ Source ID `{source_id}` saved.\n\n"
        "**Step 2/7:** Kahan forward karna hai? Us **Target Group/Channel ID** ko bhejein (e.g. `-10087654321`):",
        parse_mode=ParseMode.MARKDOWN
    )

async def cmd_cleanbatch(client: Client, message: Message):
    batch_id_str = message.text.strip()
    try:
        batch_id = int(batch_id_str)
    except ValueError:
        return await message.reply_text("❌ Error: Invalid Batch ID. Kripya numbers me ID bhejein.")

    await message.reply_text("🚀 **Anti-Leech Cleaner Started!**\nBackground process shuru ho gaya hai. Kripya wait karein...", parse_mode=ParseMode.MARKDOWN)
    asyncio.create_task(run_clean_unverified(client, message, batch_id))


async def run_clean_unverified(bot_client: Client, message: Message, batch_id: int):
    session_string = DB.get("USERBOT_SESSION")
    if not session_string or not API_ID:
        return await message.reply_text("❌ **Userbot Not Logged In!** Dashboard se login karein.", parse_mode=ParseMode.MARKDOWN)

    msg = await message.reply_text(f"⏳ **Anti-Leech Scanner Started** on Batch `{batch_id}`...")

    userbot = Client("clean_bot", api_id=API_ID, api_hash=API_HASH, session_string=session_string, in_memory=True)
    try:
        await userbot.start()
        
        mandatory_id = int(MANDATORY_CHANNEL_ID) if MANDATORY_CHANNEL_ID else None
        if not mandatory_id:
            await userbot.stop()
            return await msg.edit_text("❌ Error: `MANDATORY_CHANNEL_ID` set nahi hai. Bot verification check nahi kar sakta.")

        await msg.edit_text("✅ Userbot started! Scanning members... (Isme time lag sakta hai)")

        removed_count = 0
        safe_count = 0
        scanned_count = 0

        async for member in userbot.get_chat_members(batch_id):
            scanned_count += 1
            uid = member.user.id

            # Skip Bots, Admins, and Owner
            if member.user.is_bot or uid == OWNER_ID or is_admin(uid) or member.status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]:
                safe_count += 1
                continue

            # Check 1: User ne bot start kiya hai kya?
            has_started_bot = str(uid) in DB.get("USER_DATA", {}) or int(uid) in DB.get("USER_DATA", {})

            # Check 2: User Mandatory Channel me hai ya nahi?
            is_in_mandatory = False
            try:
                check_m = await bot_client.get_chat_member(mandatory_id, uid)
                if check_m.status in [ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER, ChatMemberStatus.RESTRICTED]:
                    is_in_mandatory = True
            except Exception:
                pass 

            # Agar bot start nahi kiya YA mandatory me nahi hai -> KICK
            if not has_started_bot or not is_in_mandatory:
                try:
                    await bot_client.ban_chat_member(batch_id, uid)
                    await bot_client.unban_chat_member(batch_id, uid)
                    
                    # Agar demos wagaira the toh clear kar do
                    if has_started_bot:
                        user_key = str(uid) if str(uid) in DB["USER_DATA"] else uid
                        if "demos" in DB["USER_DATA"][user_key] and str(batch_id) in DB["USER_DATA"][user_key]["demos"]:
                            del DB["USER_DATA"][user_key]["demos"][str(batch_id)]
                            
                    removed_count += 1
                    await asyncio.sleep(1.2) # Flood protection limit
                except FloodWait as fw:
                    await asyncio.sleep(fw.value + 1)
                    await bot_client.ban_chat_member(batch_id, uid)
                    await bot_client.unban_chat_member(batch_id, uid)
                    removed_count += 1
                except Exception:
                    pass
            else:
                safe_count += 1

            if scanned_count % 30 == 0:
                try:
                    await msg.edit_text(
                        f"⏳ **Scanning In Progress...**\n\n"
                        f"🔍 **Total Scanned:** `{scanned_count}`\n"
                        f"🛡️ **Safe Users:** `{safe_count}`\n"
                        f"👢 **Removed Leeches:** `{removed_count}`"
                    )
                except:
                    pass

        await save_data_async()
        await userbot.stop()
        
        await msg.edit_text(
            f"🎯 **Cleanup Successful!**\n\n"
            f"🔍 Total Scanned: `{scanned_count}`\n"
            f"🛡️ Safe Users: `{safe_count}`\n"
            f"👢 **Total Removed:** `{removed_count}`\n\n"
            f"Un sabhi users ko nikal diya gaya hai jo mandatory channel me nahi the ya jinhone bot register nahi kiya tha.",
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        await msg.edit_text(f"❌ **Error:** `{e}`")
        try:
            await userbot.stop()
        except:
            pass

async def run_advanced_caption_changer(bot_client: Client, message: Message, channel_id_str: str, start_msg_id: int, end_msg_id: int, mode: str, data_text: str):
    uid = message.from_user.id
    if not hasattr(bot_client, "cancel_tasks"): bot_client.cancel_tasks = set()
    bot_client.cancel_tasks.discard(uid)
    
    cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("🛑 Cancel Task", callback_data=f"cancel_task_{uid}")]])

    try:
        channel_id = int(channel_id_str)
    except ValueError:
        return await message.reply_text("❌ Error: Channel ID number me nahi hai.")

    session_string = DB.get("USERBOT_SESSION")
    if not session_string or not API_ID:
        return await message.reply_text("❌ **Userbot Not Logged In!** Dashboard se login karein.", parse_mode=ParseMode.MARKDOWN)

    msg = await message.reply_text(f"⏳ **Step 1:** Fetching messages super-fast from `{start_msg_id}` to `{end_msg_id}`...", reply_markup=cancel_kb)
    userbot = Client("advcap_bot", api_id=API_ID, api_hash=API_HASH, session_string=session_string, in_memory=True)
    
    try:
        await userbot.start()
        min_id = min(start_msg_id, end_msg_id)
        max_id = max(start_msg_id, end_msg_id)
        message_ids = list(range(min_id, max_id + 1))
        all_messages = []

        # Super-Fast Chunking (Avoids GetHistory FloodWait completely)
        for i in range(0, len(message_ids), 200):
            if uid in bot_client.cancel_tasks: break
            chunk = message_ids[i:i+200]
            try:
                msgs = await userbot.get_messages(channel_id, chunk)
                for m in msgs:
                    if m and not getattr(m, "empty", False):
                        all_messages.append(m)
                await asyncio.sleep(0.5)
            except Exception:
                pass
        
        if uid in bot_client.cancel_tasks:
            await userbot.stop()
            return await msg.edit_text("🛑 **Task Cancelled by User!** (Stopped during fetch)")

        total_msgs = len(all_messages)
        if total_msgs == 0:
            await userbot.stop()
            return await msg.edit_text(f"❌ Error: Range `{min_id}` - `{max_id}` me koi message nahi mila.")
            
        await msg.edit_text(f"✅ Total `{total_msgs}` messages loaded!\n⏳ **Step 2:** Advanced Changer Running...", reply_markup=cancel_kb)
        
        edited_count = 0
        checked_count = 0

        for m in all_messages:
            if uid in bot_client.cancel_tasks:
                await msg.edit_text("🛑 **Task Cancelled by User!** (Stopped in middle)")
                break

            checked_count += 1
            if m.caption or mode == "1":
                old_cap = m.caption or ""
                new_cap = old_cap
                
                if mode == "1":   # Full Replace
                    new_cap = data_text
                elif mode == "2": # Word Replace
                    if "|" in data_text:
                        old_word, new_word = data_text.split("|", 1)
                        new_cap = old_cap.replace(old_word.strip(), new_word.strip())
                elif mode == "3": # Remove Word
                    new_cap = old_cap.replace(data_text, "").strip()
                elif mode == "4": # Add Prefix
                    new_cap = f"{data_text}\n\n{old_cap}"
                elif mode == "5": # Add Suffix
                    new_cap = f"{old_cap}\n\n{data_text}"
                elif mode == "6": # Smart Auto Format
                    new_cap = smart_format_caption(old_cap)
                elif mode == "7": # Multi Replace & Delete
                    rules = data_text.split(",")
                    for rule in rules:
                        if "|" in rule:
                            old_w, new_w = rule.split("|", 1)
                            new_cap = new_cap.replace(old_w.strip(), new_w.strip())
                    new_cap = new_cap.strip()
                
                if new_cap != old_cap:
                    try:
                        await userbot.edit_message_caption(chat_id=channel_id, message_id=m.id, caption=new_cap)
                        edited_count += 1
                        await asyncio.sleep(2) 
                    except FloodWait as fw:
                        await asyncio.sleep(fw.value + 1)
                        await userbot.edit_message_caption(chat_id=channel_id, message_id=m.id, caption=new_cap)
                        edited_count += 1
                    except Exception:
                        pass
                        
            if checked_count % 30 == 0:
                try:
                    await msg.edit_text(
                        f"⏳ **Advanced Changer Running...**\n\n"
                        f"⚙️ **Mode Selected:** `{mode}`\n"
                        f"🔍 **Scanned:** `{checked_count}/{total_msgs}`\n"
                        f"✏️ **Successfully Edited:** `{edited_count}`",
                        reply_markup=cancel_kb
                    )
                except: pass

        await userbot.stop()
        if uid not in bot_client.cancel_tasks:
            await msg.edit_text(
                f"🎯 **Caption Changer Successful!**\n\n"
                f"📈 **Range:** `{min_id}` to `{max_id}`\n"
                f"✨ Total **{edited_count}** captions change ho gaye hain!",
                parse_mode=ParseMode.MARKDOWN
            )
    except Exception as err:
        import traceback
        traceback.print_exc()
        await msg.edit_text(f"❌ **Userbot Error:** `{err}`")
        try: await userbot.stop()
        except: pass

async def cmd_advcap_start(client: Client, message: Message):
    channel_id = message.text.strip()
    uid = message.from_user.id
    ADMIN_WIZARD[uid] = {"step": "advcap_start_msg", "channel_id": channel_id}
    await message.reply_text(
        f"✅ Channel ID `{channel_id}` saved.\n\n"
        "**Step 2/5:** Ab batayein kahan se **START** karna hai?\n"
        "Pehli message ka **Message ID** bhejein (Jaise: `1005`):",
        parse_mode=ParseMode.MARKDOWN
    )

def smart_format_caption(text):
    if not text: return text
    import re
    
    # 1. Clean _enc and unwanted spaces
    text = text.replace("_enc", "").replace("  ", " ")
    
    # 2. Remove tags like @Team_JeeX
    text = re.sub(r"@[a-zA-Z0-9_]+", "", text)
    
    # 3. Remove Extracted By line completely
    text = re.sub(r"Extracted By\s*[➤:]\s*.*", "", text, flags=re.IGNORECASE)
    
    # 4. Smart Extraction & Reordering
    if ("File Title :" in text or "Video Title :" in text) and "Batch Name :" in text:
        lines = text.split('\n')
        data_map = {}
        
        for line in lines:
            l_str = line.strip()
            if not l_str: continue
            if l_str.startswith("["): data_map['id'] = l_str
            elif "Title :" in l_str: data_map['title'] = l_str
            elif l_str.startswith("Batch Name"): data_map['batch'] = l_str
            elif l_str.startswith("Topic Name"): data_map['topic'] = l_str
        
        # Extract Subject (Reasoning By Sandeep Sir -> Reasoning)
        subject = ""
        title_val = data_map.get('title', '')
        sub_match = re.search(r":\s*(.*?)\s+By\s+", title_val, re.IGNORECASE)
        if sub_match:
            subject = f"Subject : {sub_match.group(1).strip()}"
            
        if all(k in data_map for k in ['id', 'title', 'batch', 'topic']):
            new_cap = f"{data_map['id']}\n{data_map['title']}\n{data_map['topic']}\n"
            if subject:
                new_cap += f"{subject}\n"
            new_cap += f"{data_map['batch']}"
            return new_cap.strip()
            
    return text.strip()

async def run_super_forwarder(bot_client: Client, message: Message, source_str: str, dest_str: str, start_msg_id: int, end_msg_id: int, topic_kw: str, remove_words: str, replace_words: str):
    uid = message.from_user.id
    if not hasattr(bot_client, "cancel_tasks"): bot_client.cancel_tasks = set()
    bot_client.cancel_tasks.discard(uid)

    from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    from pyrogram.errors import FloodWait
    import asyncio
    import re
    cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("🛑 Cancel Task", callback_data=f"cancel_task_{uid}")]])

    try:
        source_id = int(source_str)
        dest_id = int(dest_str)
    except ValueError:
        return await message.reply_text("❌ Error: IDs numbers me honi chahiye.")

    from config import DB, API_ID, API_HASH
    session_string = DB.get("USERBOT_SESSION")
    if not session_string or not API_ID:
        return await message.reply_text("❌ **Userbot Not Logged In!** Dashboard se login karein.")

    msg = await message.reply_text(f"⏳ **Step 1:** Checking Channel Access...", reply_markup=cancel_kb)

    from pyrogram import Client as PyroClient
    userbot = PyroClient("superfwd_bot", api_id=API_ID, api_hash=API_HASH, session_string=session_string, in_memory=True)
    
    try:
        await userbot.start()
        
        # STRICT CHANNEL ACCESS CHECK (Jisse pata chalega ki bot add hai ya nahi)
        try:
            await userbot.get_chat(source_id)
        except Exception as e:
            await userbot.stop()
            return await msg.edit_text(
                f"❌ **ACCESS DENIED!**\n\n"
                f"Userbot ko Source Channel (`{source_id}`) ka access nahi mil raha hai.\n\n"
                f"**Solution:** Jis number se Userbot login hai, kya wo is channel me join hai? Agar nahi, toh pehle usse add karein.\n"
                f"**System Error:** `{e}`"
            )

        await msg.edit_text(f"✅ Access Verified!\n⏳ Fetching messages super-fast...", reply_markup=cancel_kb)

        min_id = min(start_msg_id, end_msg_id)
        max_id = max(start_msg_id, end_msg_id)
        message_ids = list(range(min_id, max_id + 1))
        all_messages = []
        
        # Super-Fast Fetching with List Check
        for i in range(0, len(message_ids), 200):
            if uid in bot_client.cancel_tasks: break
            chunk = message_ids[i:i+200]
            try:
                msgs = await userbot.get_messages(source_id, chunk)
                if not isinstance(msgs, list): 
                    msgs = [msgs]
                for m in msgs:
                    if m and not getattr(m, "empty", False):
                        all_messages.append(m)
                await asyncio.sleep(0.5)
            except Exception as e:
                print(f"Fetch Error: {e}")

        if uid in bot_client.cancel_tasks:
            await userbot.stop()
            return await msg.edit_text("🛑 **Task Cancelled by User!**")

        total_msgs = len(all_messages)
        if total_msgs == 0:
            await userbot.stop()
            return await msg.edit_text(f"❌ Error: ID `{min_id}` se `{max_id}` tak koi media nahi mila, ya range khali hai.")
            
        await msg.edit_text(f"✅ Total `{total_msgs}` messages loaded!\n⏳ **Step 2:** Modifying & Forwarding...", reply_markup=cancel_kb)
        
        created_topics = {} 
        forwarded_count = 0
        checked_count = 0

        # /s ki jagah ab 0 (Zero) par skip hoga
        rem_list = [w.strip() for w in remove_words.split(",") if w.strip()] if remove_words != "0" else []
        rep_list = []
        if replace_words != "0":
            for r in replace_words.split(","):
                if "|" in r:
                    old_w, new_w = r.split("|", 1)
                    rep_list.append((old_w.strip(), new_w.strip()))

        for m in all_messages:
            if uid in bot_client.cancel_tasks:
                await msg.edit_text("🛑 **Task Cancelled by User!** (Stopped in middle)")
                break

            checked_count += 1
            if not (m.video or m.document):
                continue

            new_cap = m.caption or ""
            
            for w in rem_list:
                new_cap = new_cap.replace(w, "")
            
            for old_w, new_w in rep_list:
                new_cap = new_cap.replace(old_w, new_w)

            new_cap = new_cap.strip()
            topic_id = None
            
            # /s ki jagah ab 0 par skip hoga
            if topic_kw != "0":
                pattern = fr"{re.escape(topic_kw)}\s*:\s*(.*?)(?=\s+Batch:|\n|$)"
                t_match = re.search(pattern, new_cap, re.IGNORECASE)
                
                if t_match:
                    raw_topic_name = t_match.group(1).strip()
                    topic_key = raw_topic_name.lower() 
                    
                    if topic_key not in created_topics:
                        try:
                            new_topic = await bot_client.create_forum_topic(chat_id=dest_id, title=raw_topic_name)
                            created_topics[topic_key] = new_topic.id
                        except Exception:
                            try:
                                new_topic = await userbot.create_forum_topic(chat_id=dest_id, title=raw_topic_name)
                                created_topics[topic_key] = new_topic.id
                            except Exception:
                                pass
                    topic_id = created_topics.get(topic_key)
                
            try:
                await userbot.copy_message(
                    chat_id=dest_id, 
                    from_chat_id=source_id, 
                    message_id=m.id, 
                    message_thread_id=topic_id,
                    caption=new_cap
                )
                forwarded_count += 1
                await asyncio.sleep(1.5) 
            except FloodWait as fw:
                await asyncio.sleep(fw.value + 1)
                await userbot.copy_message(chat_id=dest_id, from_chat_id=source_id, message_id=m.id, message_thread_id=topic_id, caption=new_cap)
                forwarded_count += 1
            except Exception:
                pass
            
            if checked_count % 30 == 0:
                try:
                    topics_made = len(created_topics) if topic_kw != "0" else 0
                    await msg.edit_text(
                        f"⏳ **Super Forwarder Running...**\n\n"
                        f"📁 **Topics Created:** `{topics_made}`\n"
                        f"🔍 **Scanned:** `{checked_count}/{total_msgs}`\n"
                        f"✅ **Forwarded:** `{forwarded_count}`",
                        reply_markup=cancel_kb
                    )
                except:
                    pass

        await userbot.stop()
        if uid not in bot_client.cancel_tasks:
            await msg.edit_text(
                f"🎯 **Operation Successful Master!**\n\n"
                f"✨ **Topics Built:** `{len(created_topics) if topic_kw != '0' else 0}`\n"
                f"📦 **Total Forwarded & Edited:** `{forwarded_count}`",
                parse_mode=ParseMode.MARKDOWN
            )
    except Exception as err:
        import traceback
        traceback.print_exc()
        await msg.edit_text(f"❌ **Userbot Error:** `{err}`")
        try: await userbot.stop()
        except: pass
            
async def cmd_advcap_start(client: Client, message: Message):
    channel_id = message.text.strip()
    uid = message.from_user.id

async def run_advanced_caption_changer(bot_client: Client, message: Message, channel_id_str: str, start_msg_id: int, end_msg_id: int, mode: str, data_text: str):
    try:
        channel_id = int(channel_id_str)
    except ValueError:
        return await message.reply_text("❌ Error: Channel ID number me nahi hai.")

    session_string = DB.get("USERBOT_SESSION")
    if not session_string or not API_ID:
        return await message.reply_text("❌ **Userbot Not Logged In!** Dashboard se login karein.", parse_mode=ParseMode.MARKDOWN)

    msg = await message.reply_text(f"⏳ **Step 1:** Fetching messages from `{start_msg_id}` to `{end_msg_id}`...")
    userbot = Client("advcap_bot", api_id=API_ID, api_hash=API_HASH, session_string=session_string, in_memory=True)
    
    try:
        await userbot.start()
        min_id = min(start_msg_id, end_msg_id)
        max_id = max(start_msg_id, end_msg_id)

        all_messages = []
        async for m in userbot.get_chat_history(channel_id, offset_id=max_id + 1):
            if m.id < min_id: break
            if m.id <= max_id: all_messages.append(m)
        
        total_msgs = len(all_messages)
        if total_msgs == 0:
            await userbot.stop()
            return await msg.edit_text(f"❌ Error: Range `{min_id}` - `{max_id}` me koi message nahi mila.")
            
        await msg.edit_text(f"✅ Total `{total_msgs}` messages loaded!\n⏳ **Step 2:** Advanced Changer Running...")
        
        edited_count = 0
        checked_count = 0

        for m in all_messages:
            checked_count += 1
            if m.caption or mode == "1":
                old_cap = m.caption or ""
                new_cap = old_cap
                
                if mode == "1":   # Full Replace
                    new_cap = data_text
                elif mode == "2": # Word Replace
                    if "|" in data_text:
                        old_word, new_word = data_text.split("|", 1)
                        new_cap = old_cap.replace(old_word.strip(), new_word.strip())
                elif mode == "3": # Remove Word
                    new_cap = old_cap.replace(data_text, "").strip()
                elif mode == "4": # Add Prefix (Upar)
                    new_cap = f"{data_text}\n\n{old_cap}"
                elif mode == "5": # Add Suffix (Niche)
                    new_cap = f"{old_cap}\n\n{data_text}"
                elif mode == "6": # Smart Auto Format
                    new_cap = smart_format_caption(old_cap)
                
                if new_cap != old_cap:
                    try:
                        await userbot.edit_message_caption(
                            chat_id=channel_id,
                            message_id=m.id,
                            caption=new_cap
                        )
                        edited_count += 1
                        await asyncio.sleep(2)  # API flood se bachne ke liye
                    except FloodWait as fw:
                        await asyncio.sleep(fw.value + 1)
                        await userbot.edit_message_caption(chat_id=channel_id, message_id=m.id, caption=new_cap)
                        edited_count += 1
                    except Exception:
                        pass
                        
            if checked_count % 30 == 0:
                try:
                    await msg.edit_text(
                        f"⏳ **Advanced Changer Running...**\n\n"
                        f"⚙️ **Mode Selected:** `{mode}`\n"
                        f"🔍 **Scanned:** `{checked_count}/{total_msgs}`\n"
                        f"✏️ **Successfully Edited:** `{edited_count}`"
                    )
                except: pass

        await userbot.stop()
        await msg.edit_text(
            f"🎯 **Caption Changer Successful!**\n\n"
            f"📈 **Range:** `{min_id}` to `{max_id}`\n"
            f"✨ Total **{edited_count}** captions change ho gaye hain!",
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception as err:
        import traceback
        traceback.print_exc()
        await msg.edit_text(f"❌ **Userbot Error:** `{err}`")
        try:
            await userbot.stop()
        except:
            pass

# --- WIZARDS, MENUS & CALLBACKS ---
async def send_batch_update_post(client: Client, cid: int, cname: str, b_type: str, category: str, coin_cost: int = None, price: str = None):
    import config
    import os
    import urllib.parse
    update_channel = getattr(config, 'BATCH_UPDATE_CHANNEL_ID', 0)

    bot_username = getattr(config, 'BOT_USERNAME', None)
    if not bot_username:
        try:
            bot_username = (await client.get_me()).username
        except Exception:
            bot_username = "bot"

    deep_link = f"https://t.me/{bot_username}?start=batch_{cid}"
    share_text = f"🔥 Check out new batch: {cname} on our Bot!"
    share_url = f"https://t.me/share/url?url={urllib.parse.quote(deep_link)}&text={urllib.parse.quote(share_text)}"

    kb = [
        [InlineKeyboardButton("🚀 Get Direct Access via Bot", url=deep_link)],
        [InlineKeyboardButton("📢 Share with Friends", url=share_url)],
    ]

    type_badge = "FREE BATCH" if b_type.lower() == "free" else ("PAID BATCH" if b_type.lower() == "paid" else "SPECIAL BATCH")
    paid_price = price or DB.get("BATCH_PRICES", {}).get(str(cid))
    if b_type.lower() == 'special' and coin_cost:
        cost_line = f"\n💰 <b>Coin Cost:</b> <code>{coin_cost} Coins</code>"
    elif b_type.lower() == 'paid' and paid_price:
        cost_line = f"\n💰 <b>Price / Fee:</b> <code>{paid_price}</code>"
    else:
        cost_line = ""

    caption = (
        "🌟 ━━━━━━━━━━━━━━━━━━━━━ 🌟\n"
        "⚡ <b>NEW BATCH OFFICIALLY ADDED!</b> ⚡\n"
        "🌟 ━━━━━━━━━━━━━━━━━━━━━ 🌟\n\n"
        f"📦 <b>Batch Name:</b> <code>{cname}</code>\n"
        f"🏷️ <b>Access Level:</b> <code>{type_badge}</code>\n"
        f"📁 <b>Category:</b> <code>{category}</code>"
        f"{cost_line}\n\n"
        "```diff\n"
        "+ [STATUS: 100% VERIFIED & ACTIVE]\n"
        "+ [INSTANT BOT DELIVERY ENABLED]\n"
        "! [TAP BUTTON BELOW TO ENROLL]\n"
        "```\n\n"
        "📌 <b>Batch Features & Highlights:</b>\n"
        "➤ <b>Complete High-Quality Lectures</b> & Study Material\n"
        "➤ <b>Chapter-wise DPPs</b>, Hand-written Notes & Test Papers\n"
        "➤ <b>24x7 Discussion Community</b> & Doubt Sessions\n"
        "➤ <b>Direct instant access</b> without wait or verification delay!\n\n"
        "👉 <i>Neeche button par tap karke turant access unlock karein!</i>"
    )

    photo_path = None
    try:
        chat_info = await client.get_chat(cid)
        if chat_info.photo:
            photo_path = await client.download_media(chat_info.photo.big_file_id)
    except Exception:
        pass

    if update_channel:
        try:
            if photo_path:
                await client.send_photo(int(update_channel), photo=photo_path, caption=caption, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
            else:
                await client.send_message(int(update_channel), text=caption, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
        except Exception as e:
            logger.error(f"Failed to send to batch update channel: {e}")

    if photo_path and os.path.exists(photo_path):
        try:
            os.remove(photo_path)
        except Exception:
            pass

    # Auto-notify users who requested this batch
    reqs = DB.get("BATCH_REQUESTS", {})
    matched_uids = set()
    cname_lower = cname.lower()
    for req_text, uids in list(reqs.items()):
        if req_text.lower() in cname_lower or cname_lower in req_text.lower():
            for u in uids:
                matched_uids.add(int(u))

    for target_uid in matched_uids:
        try:
            notify_kb = [[InlineKeyboardButton("🚀 Open Your Requested Batch", url=deep_link)]]
            await client.send_message(
                target_uid,
                f"🎉 **Great News! Aapka Requested Batch Live Ho Chuka Hai!**\n━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📦 **Batch Name:** `{cname}`\n"
                f"🏷️ **Type:** `{type_badge}`\n\n"
                f"Aapne is batch ki demand ki thi. Ab yeh bot me successfully add ho gaya hai!\n"
                f"Neeche button se direct join karein:",
                reply_markup=InlineKeyboardMarkup(notify_kb),
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception:
            pass

async def process_updatepost(client: Client, message: Message):
    raw_id = message.text.strip()
    try:
        cid = int(raw_id)
    except ValueError:
        return await message.reply_text("❌ Error: Batch ID numbers me honi chahiye.", parse_mode=ParseMode.MARKDOWN)

    import config
    cname = config.DB.get("ALL_CHATS", {}).get(cid)
    if not cname:
        return await message.reply_text("❌ Error: Ye Batch ID database me nahi mili.", parse_mode=ParseMode.MARKDOWN)

    b_type = "unknown"
    if cid in config.DB.get("FREE_CHANNELS", {}):
        b_type = "free"
    elif cid in config.DB.get("PAID_CHANNELS", {}):
        b_type = "paid"
    elif cid in config.DB.get("SPECIAL_CHANNELS", {}):
        b_type = "special"

    category = config.DB.get("BATCH_CATEGORIES", {}).get(str(cid), "Other Batches")
    coin_cost = config.DB.get("BATCH_COINS", {}).get(str(cid)) if b_type == "special" else None

    msg = await message.reply_text(f"⏳ Generating update post for `{cname}`...", parse_mode=ParseMode.MARKDOWN)
    await send_batch_update_post(client, cid, cname, b_type, category, coin_cost)
    await msg.edit_text("✅ **Update post successfully sent to the channel!**", parse_mode=ParseMode.MARKDOWN)


async def wizard_callback(client: Client, q: CallbackQuery):
    uid = q.from_user.id
    if uid not in ADMIN_WIZARD:
        return await q.answer("Expired")
    if q.data.startswith("wcat_"):
        cat_idx = int(q.data.split("_")[1])
        ADMIN_WIZARD[uid]["category"] = DB.get("CATEGORIES", DEFAULT_CATEGORIES)[cat_idx]
        ADMIN_WIZARD[uid]["step"] = "ask_type"
        kb = [
            [
                InlineKeyboardButton("🆓 Free", callback_data="wiz_free"),
                InlineKeyboardButton("💵 Paid", callback_data="wiz_paid"),
                InlineKeyboardButton("✨ Special", callback_data="wiz_special"),
            ]
        ]
        return await q.edit_message_text(
            f"  Category: **{ADMIN_WIZARD[uid]['category']}**\n\n  **Step 2:** Select Batch Type:",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode=ParseMode.MARKDOWN,
        )
    if q.data in ["wiz_free", "wiz_paid", "wiz_special"]:
        if "category" not in ADMIN_WIZARD[uid]:
            return await q.answer("Start again")
        ADMIN_WIZARD[uid]["type"] = q.data.replace("wiz_", "")
        ADMIN_WIZARD[uid]["step"] = "ask_id"
        return await q.edit_message_text(
            f"  **Step 3:** Send **Channel ID** for {ADMIN_WIZARD[uid]['type'].upper()}:",
            parse_mode=ParseMode.MARKDOWN,
        )

    if q.data.startswith("wiz_dup_update_"):
        cid = int(q.data.replace("wiz_dup_update_", ""))
        state = ADMIN_WIZARD[uid]
        target_type = state.get("type", "free")
        target_cat = state.get("category", "Other Batches")
        
        # Clean from all collections first (both int and str keys)
        for col in ["FREE_CHANNELS", "PAID_CHANNELS", "SPECIAL_CHANNELS"]:
            DB.get(col, {}).pop(cid, None)
            DB.get(col, {}).pop(str(cid), None)

        try:
            cname = (await client.get_chat(cid)).title or state.get("existing_name") or f"Batch {cid}"
        except Exception:
            cname = state.get("existing_name") or f"Batch {cid}"

        if target_type == "free":
            DB.setdefault("FREE_CHANNELS", {})[cid] = cname
        elif target_type == "paid":
            DB.setdefault("PAID_CHANNELS", {})[cid] = cname
        elif target_type == "special":
            DB.setdefault("SPECIAL_CHANNELS", {})[cid] = cname

        DB.setdefault("ALL_CHATS", {})[cid] = cname
        DB.get("ALL_CHATS", {}).pop(str(cid), None)
        DB.setdefault("BATCH_CATEGORIES", {})[str(cid)] = target_cat
        await save_data_async()

        # Check if paid or special requires extra input
        if target_type == "paid":
            ADMIN_WIZARD[uid]["step"] = "ask_price"
            ADMIN_WIZARD[uid]["cid"] = cid
            ADMIN_WIZARD[uid]["cname"] = cname
            await q.edit_message_text(
                f"✅ **Batch Moved to PAID!**\n\n"
                f"💰 **Step 4:** '{cname}' ki Price / Fee kitni hai?\n(Example: `₹499`, `₹199 / Month`, etc.):",
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        elif target_type == "special":
            ADMIN_WIZARD[uid]["step"] = "ask_coin"
            ADMIN_WIZARD[uid]["cid"] = cid
            ADMIN_WIZARD[uid]["cname"] = cname
            await q.edit_message_text(
                f"✅ **Batch Moved to SPECIAL!**\n\n"
                f"  **Step 4:** '{cname}' ko unlock karne ke liye kitne Coins chahiye?\nSirf number bhejein (e.g. `3`):",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        ADMIN_WIZARD.pop(uid, None)
        await q.edit_message_text(
            f"✅ **Batch Successfully Updated & Cleaned!**\n━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📦 **Name:** `{cname}`\n"
            f"🆔 **ID:** `{cid}`\n"
            f"🏷️ **Type:** `{target_type.upper()}`\n"
            f"📂 **Category:** `{target_cat}`\n\n"
            f"✨ Purani duplicate entries safely remove kar di gayi hain!",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    if q.data == "wiz_dup_cancel":
        ADMIN_WIZARD.pop(uid, None)
        await q.answer("Cancelled! No duplicate created.")
        return await q.edit_message_text("❌ **Add Batch Cancelled.** Batch pehle jaisa hi surakshit hai.")

async def wizard_message(client: Client, message: Message):
    if not message.from_user or message.from_user.id not in ADMIN_WIZARD:
        return False
    uid, state = message.from_user.id, ADMIN_WIZARD[message.from_user.id]
    if state["step"] == "ask_id":
        try:
            raw_text = message.text.strip()
            cid = int(raw_text)
        except ValueError:
            await message.reply_text("❌ Error: Valid numeric Channel ID bhejein (jaise `-1001234567890`).")
            return True

        # --- DUPLICATE BATCH CHECK ---
        existing_type = None
        if cid in DB.get("FREE_CHANNELS", {}) or str(cid) in DB.get("FREE_CHANNELS", {}):
            existing_type = "free"
        elif cid in DB.get("PAID_CHANNELS", {}) or str(cid) in DB.get("PAID_CHANNELS", {}):
            existing_type = "paid"
        elif cid in DB.get("SPECIAL_CHANNELS", {}) or str(cid) in DB.get("SPECIAL_CHANNELS", {}):
            existing_type = "special"

        existing_cat = DB.get("BATCH_CATEGORIES", {}).get(str(cid)) or DB.get("BATCH_CATEGORIES", {}).get(cid, "Not Assigned")
        existing_name = (
            DB.get("ALL_CHATS", {}).get(cid)
            or DB.get("ALL_CHATS", {}).get(str(cid))
            or (DB.get(f"{existing_type.upper()}_CHANNELS", {}).get(cid) if existing_type else None)
            or f"Batch {cid}"
        )

        if existing_type:
            # Batch is already registered!
            ADMIN_WIZARD[uid]["step"] = "confirm_dup_update"
            ADMIN_WIZARD[uid]["cid"] = cid
            ADMIN_WIZARD[uid]["existing_name"] = existing_name
            ADMIN_WIZARD[uid]["existing_type"] = existing_type
            ADMIN_WIZARD[uid]["existing_cat"] = existing_cat

            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Yes, Update & Move Batch", callback_data=f"wiz_dup_update_{cid}")],
                [InlineKeyboardButton("❌ Cancel (Keep Existing)", callback_data="wiz_dup_cancel")]
            ])

            warn_text = (
                f"⚠️ **BATCH ALREADY REGISTERED / PEHLE SE EXIST KARTA HAI!**\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📦 **Batch Name:** `{existing_name}`\n"
                f"🆔 **Channel ID:** `{cid}`\n"
                f"🏷️ **Current Type:** `{existing_type.upper()}`\n"
                f"📂 **Current Category:** `{existing_cat}`\n\n"
                f"Aap ise dobara upload karne ki koshish kar rahe the:\n"
                f"➤ Target Type: **{state['type'].upper()}**\n"
                f"➤ Target Category: **{state['category']}**\n\n"
                f"💡 **Duplicate Rokne Ke Liye:**\n"
                f"• Agar category ya type change karna chahte hain toh **'Update & Move'** dabayein.\n"
                f"• Agar bhool se upload ho raha tha toh **'Cancel'** dabayein taaki duplicate batch na bane!"
            )
            await message.reply_text(warn_text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
            return True

        try:
            cname = (await client.get_chat(cid)).title or f"Batch {cid}"
            
            # Clean from all collections first to avoid duplicate keys
            for col in ["FREE_CHANNELS", "PAID_CHANNELS", "SPECIAL_CHANNELS"]:
                DB.get(col, {}).pop(cid, None)
                DB.get(col, {}).pop(str(cid), None)

            if state["type"] == "free":
                DB.setdefault("FREE_CHANNELS", {})[cid] = cname
            elif state["type"] == "paid":
                DB.setdefault("PAID_CHANNELS", {})[cid] = cname
            elif state["type"] == "special":
                DB.setdefault("SPECIAL_CHANNELS", {})[cid] = cname

            DB.setdefault("ALL_CHATS", {})[cid] = cname
            DB.get("ALL_CHATS", {}).pop(str(cid), None)
            DB.setdefault("BATCH_CATEGORIES", {})[str(cid)] = state["category"]
            await save_data_async()

            # Paid batches: ask price/fee so user sees exact cost without asking owner repeatedly
            if state["type"] == "paid":
                ADMIN_WIZARD[uid]["step"] = "ask_price"
                ADMIN_WIZARD[uid]["cid"] = cid
                ADMIN_WIZARD[uid]["cname"] = cname
                await message.reply_text(
                    f"💰 **Step 4:** '{cname}' ki Price / Fee kitni hai?\n(Example: `₹499`, `₹199 / Month`, etc.):",
                    parse_mode=ParseMode.MARKDOWN,
                )
                return True

            # Special batches: ab yahan ruk kar unlock-coin cost poochenge
            if state["type"] == "special":
                ADMIN_WIZARD[uid]["step"] = "ask_coin"
                ADMIN_WIZARD[uid]["cid"] = cid
                ADMIN_WIZARD[uid]["cname"] = cname
                await message.reply_text(
                    f"  **Step 4:** '{cname}' ko unlock karne ke liye kitne Coins chahiye?\nSirf number bhejein (e.g. `3`):",
                    parse_mode=ParseMode.MARKDOWN,
                )
                return True

            await message.reply_text(
                f"✅ **Added!**\n📛 Name: {cname} ({cid})\n🏷️ Type: {state['type'].upper()}\n📂 Category: {state['category']}",
                parse_mode=ParseMode.MARKDOWN,
            )
            
            if state["type"] == "free":
                await send_batch_update_post(client, cid, cname, state["type"], state["category"])

            if state["type"] == "free":
                b_count = 0
                await message.reply_text(
                    f"📡 Sending Auto-Broadcast for {state['type'].upper()} batch...", parse_mode=ParseMode.MARKDOWN
                )
                for t_cid in list(DB.get("ALL_CHATS", {}).keys()):
                    if t_cid != cid:
                        try:
                            sent_msg = await client.send_message(
                                int(t_cid),
                                f"🎉 <b>NEW FREE BATCH ADDED!</b>\n🆓 Name: {cname}\n📲 Join via Bot Menu!",
                                parse_mode=ParseMode.HTML,
                            )
                            DB.setdefault("SCHEDULED_DELETES", []).append({
                                "c": int(t_cid),
                                "m": sent_msg.id,
                                "t": time.time() + 10800,
                            })
                            b_count += 1
                            await asyncio.sleep(0.05)
                        except FloodWait as e:
                            await asyncio.sleep(e.value + 1)
                        except Exception:
                            pass
                await message.reply_text(f"✅ Broadcast sent to {b_count} chats.")
                await save_data_async()
            del ADMIN_WIZARD[uid]
        except Exception as e:
            await message.reply_text(f"  Error: Ensure valid ID ({e}).")
        return True

    elif state["step"] == "ask_price":
        try:
            price = message.text.strip()
            cid = state["cid"]
            cname = state["cname"]
            DB.setdefault("BATCH_PRICES", {})[str(cid)] = price
            await save_data_async()

            await message.reply_text(
                f"✅ **Added!**\n📛 Name: {cname} ({cid})\n🏷️ Type: PAID\n📂 Category: {state['category']}\n💰 Price / Fee: **{price}**",
                parse_mode=ParseMode.MARKDOWN,
            )

            await send_batch_update_post(client, cid, cname, "paid", state["category"], price=price)
            del ADMIN_WIZARD[uid]
        except Exception as e:
            await message.reply_text(f"  Error: {e}")
            if uid in ADMIN_WIZARD: del ADMIN_WIZARD[uid]
        return True

    elif state["step"] == "ask_coin":
        try:
            coin_cost = int(message.text.strip())
            if coin_cost < 1:
                raise ValueError("Coin cost 1 se kam nahi ho sakta")

            cid = state["cid"]
            cname = state["cname"]
            DB.setdefault("BATCH_COINS", {})[str(cid)] = coin_cost
            await save_data_async()

            await message.reply_text(
                f"✅ **Added!**\n📛 Name: {cname} ({cid})\n🏷️ Type: SPECIAL\n📂 Category: {state['category']}\n💰 Unlock Cost: **{coin_cost} Coin{'s' if coin_cost != 1 else ''}**",
                parse_mode=ParseMode.MARKDOWN,
            )

            await send_batch_update_post(client, cid, cname, "special", state["category"], coin_cost)

            b_count = 0
            await message.reply_text("📡 Sending Auto-Broadcast for SPECIAL batch...", parse_mode=ParseMode.MARKDOWN)
            for t_cid in list(DB.get("ALL_CHATS", {}).keys()):
                if t_cid != cid:
                    try:
                        sent_msg = await client.send_message(
                            int(t_cid),
                            f"✨ <b>NEW SPECIAL BATCH ADDED!</b>\n🌟 Name: {cname}\n🔓 Unlock via Referral in Bot Menu! (Cost: 💰 {coin_cost} Coin{'s' if coin_cost != 1 else ''})",
                            parse_mode=ParseMode.HTML,
                        )
                        DB.setdefault("SCHEDULED_DELETES", []).append({
                            "c": int(t_cid),
                            "m": sent_msg.id,
                            "t": time.time() + 10800,
                        })
                        b_count += 1
                        await asyncio.sleep(0.05)
                    except FloodWait as e:
                        await asyncio.sleep(e.value + 1)
                    except Exception:
                        pass
            await message.reply_text(f"✅ Broadcast sent to {b_count} chats.")
            await save_data_async()
            del ADMIN_WIZARD[uid]
        except ValueError:
            await message.reply_text("  Invalid input. Kripya sirf ek number bhejein (e.g. `3`).")
        except Exception as e:
            await message.reply_text(f"  Error: {e}")
            if uid in ADMIN_WIZARD: del ADMIN_WIZARD[uid]
        return True

    elif state["step"] == "giftcoin_uid":
        raw = message.text.strip()
        if not raw.lstrip("-").isdigit():
            await message.reply_text("❌ Sirf numeric User ID bhejein.")
            return True
        target = int(raw)
        target_key = target if target in DB["USER_DATA"] else str(target)
        if target_key not in DB["USER_DATA"]:
            await message.reply_text(f"❌ User `{target}` database me nahi mila.", parse_mode=ParseMode.MARKDOWN)
            del ADMIN_WIZARD[uid]
            return True
        ADMIN_WIZARD[uid] = {"step": "giftcoin_amount", "target": target}
        await message.reply_text(
            f"🎁 User `{target}` ko kitne Coins gift karne hain? (Number bhejein, e.g. `5`)",
            parse_mode=ParseMode.MARKDOWN,
        )
        return True

    elif state["step"] == "giftcoin_amount":
        try:
            amount = int(message.text.strip())
            if amount < 1:
                raise ValueError("Amount 1 se kam nahi ho sakta")

            target = state["target"]
            target_key = target if target in DB["USER_DATA"] else str(target)
            if target_key not in DB["USER_DATA"]:
                await message.reply_text("❌ User ab database me nahi mila.")
                del ADMIN_WIZARD[uid]
                return True

            DB["USER_DATA"][target_key]["referral_count"] = DB["USER_DATA"][target_key].get("referral_count", 0) + amount
            await save_data_async()

            await message.reply_text(
                f"✅ **Gift Sent!**\nUser `{target}` ko **{amount} Coin{'s' if amount != 1 else ''}** gift kar diye gaye.",
                parse_mode=ParseMode.MARKDOWN,
            )
            try:
                await client.send_message(
                    target,
                    f"🎁 **Owner gifted you {amount} coin{'s' if amount != 1 else ''}!**\n\nAapke wallet me ab total coins update ho gaye hain. Apne 'My Info' me check karein!",
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception:
                pass

            if DB["USER_DATA"][target_key].get("tier") == "vip":
                await send_vip_treat(client, target)

            del ADMIN_WIZARD[uid]
        except ValueError:
            await message.reply_text("❌ Invalid input. Kripya sirf ek number bhejein (e.g. `5`).")
        except Exception as e:
            await message.reply_text(f"❌ Error: {e}")
            if uid in ADMIN_WIZARD: del ADMIN_WIZARD[uid]
        return True

    elif state["step"] == "superfwd_target":
        ADMIN_WIZARD[uid]["step"] = "superfwd_start_id"
        ADMIN_WIZARD[uid]["dest"] = message.text.strip()
        await message.reply_text("**Step 3/7:** Kahan se shuru karna hai? **Start Message ID** bhejein (e.g. `1001`):", parse_mode=ParseMode.MARKDOWN)
        return True

    elif state["step"] == "superfwd_start_id":
        try:
            start_id = int(message.text.strip())
        except ValueError:
            return await message.reply_text("❌ Error: Message ID number me bhejein.")
        ADMIN_WIZARD[uid]["step"] = "superfwd_end_id"
        ADMIN_WIZARD[uid]["start_id"] = start_id
        await message.reply_text("**Step 4/7:** Kahan tak scan karna hai? **End Message ID** bhejein (e.g. `2050`):", parse_mode=ParseMode.MARKDOWN)
        return True

    elif state["step"] == "superfwd_end_id":
        try:
            end_id = int(message.text.strip())
        except ValueError:
            return await message.reply_text("❌ Error: Message ID number me bhejein.")
        ADMIN_WIZARD[uid]["step"] = "superfwd_topic_kw"
        ADMIN_WIZARD[uid]["end_id"] = end_id
        await message.reply_text("**Step 5/7:** Kya Naya Topic/Folder banana hai?\n\n- Agar HAAN: Topic ka Capturing word bhejein (e.g. `Subject` ya `Topic`)\n- Agar NAHI: Sirf `0` (Zero) bhejein (Direct forward hoga)", parse_mode=ParseMode.MARKDOWN)
        return True

    elif state["step"] == "superfwd_topic_kw":
        ADMIN_WIZARD[uid]["step"] = "superfwd_remove_kw"
        ADMIN_WIZARD[uid]["topic_kw"] = message.text.strip()
        await message.reply_text("**Step 6/7:** Kya caption se kuch DELETE karna hai?\n\n- Agar HAAN: Words ko comma `,` lagakar bhejein (e.g. `_enc, @Team_JeeX`)\n- Agar NAHI: Sirf `0` (Zero) bhejein", parse_mode=ParseMode.MARKDOWN)
        return True

    elif state["step"] == "superfwd_remove_kw":
        ADMIN_WIZARD[uid]["remove_words"] = message.text.strip()
        ADMIN_WIZARD[uid]["step"] = "superfwd_final"
        await message.reply_text("**Step 7/7:** Kya caption me kuch REPLACE karna hai?\n\n- Agar HAAN: Format me bhejein -> `Purana | Naya, Purana2 | Naya2`\n- Agar NAHI: Sirf `0` (Zero) bhejein", parse_mode=ParseMode.MARKDOWN)
        return True

    elif state["step"] == "superfwd_final":
        replace_words = message.text.strip()
        
        source = ADMIN_WIZARD[uid]["source"]
        dest = ADMIN_WIZARD[uid]["dest"]
        start_id = ADMIN_WIZARD[uid]["start_id"]
        end_id = ADMIN_WIZARD[uid]["end_id"]
        topic_kw = ADMIN_WIZARD[uid]["topic_kw"]
        remove_words = ADMIN_WIZARD[uid]["remove_words"]
        
        await message.reply_text("🚀 **Super Forwarder Started!**\nEditing & Forwarding everything in one go...", parse_mode=ParseMode.MARKDOWN)
        
        asyncio.create_task(run_super_forwarder(client, message, source, dest, start_id, end_id, topic_kw, remove_words, replace_words))
        del ADMIN_WIZARD[uid]
        return True
    elif state["step"] == "topicforward_caption":
        caption = message.text
        
        # Ye regex "Topic:" ke aage ka text extract karega, jab tak line break ya "Batch:" na aaye
        match = re.search(r"Topic:\s*(.*?)(?=\s+Batch:|\n|$)", caption, re.IGNORECASE)
        if not match:
            await message.reply_text("❌ Error: Is caption me `Topic:` format nahi mila. Kripya dhyan se phir se paste karein.")
            return True
        
        topic_name = match.group(1).strip()
        group_id = ADMIN_WIZARD[uid]["group_id"]
        channel_id = ADMIN_WIZARD[uid]["channel_id"]
        
        # Task start karo parameters ke sath
        asyncio.create_task(run_topic_forwarder(client, message, group_id, channel_id, keyword, start_msg_id, end_msg_id))
        del ADMIN_WIZARD[uid]
        return True

    elif state["step"] == "advcap_start_msg":
        try:
            start_msg_id = int(message.text.strip())
        except ValueError:
            await message.reply_text("❌ Error: Kripya sirf number bhejein (Message ID).")
            return True
            
        ADMIN_WIZARD[uid]["step"] = "advcap_end_msg"
        ADMIN_WIZARD[uid]["start_msg_id"] = start_msg_id
        
        await message.reply_text(
            f"✅ Start ID `{start_msg_id}` saved.\n\n"
            "**Step 3/5:** Ab kahan tak **END** karna hai?\n"
            "Aakhiri message ka **Message ID** bhejein (Jaise: `2050`):",
            parse_mode=ParseMode.MARKDOWN
        )
        return True

    elif state["step"] == "advcap_end_msg":
        try:
            end_msg_id = int(message.text.strip())
        except ValueError:
            await message.reply_text("❌ Error: Kripya sirf number bhejein (Message ID).")
            return True
            
        ADMIN_WIZARD[uid]["step"] = "advcap_mode"
        ADMIN_WIZARD[uid]["end_msg_id"] = end_msg_id
        
        menu_text = (
            f"✅ End ID `{end_msg_id}` saved.\n\n"
            "**Step 4/5:** Kya Action perform karna hai?\n"
            "Neeche diye gaye options me se ek **Number (1 se 6)** type karke bhejein:\n\n"
            "**1** ➡️ Pura Caption Naya Lagana hai (Full Replace)\n"
            "**2** ➡️ Koi specific Word/Line badalna hai (Word Replace)\n"
            "**3** ➡️ Koi specific Word/Link Delete karna hai (Remove Word)\n"
            "**4** ➡️ Caption ke Upar kuch add karna hai (Add Prefix)\n"
            "**5** ➡️ Caption ke Niche kuch add karna hai (Add Suffix)\n"
            "**6** ➡️ 🧠 **Smart Auto-Format** (Extract Subject, Clean Tags & Reorder)\n"
            "**7** ➡️ ⚡ **Multi-Task** (Replace & Delete ek sath karein)"
        )
        await message.reply_text(menu_text, parse_mode=ParseMode.MARKDOWN)
        return True

    elif state["step"] == "advcap_mode":
        mode = message.text.strip()
        if mode not in ["1", "2", "3", "4", "5", "6", "7"]:
            await message.reply_text("❌ Error: Sirf 1 se 7 ke beech ka number bhejein.")
            return True
            
        ADMIN_WIZARD[uid]["step"] = "advcap_data"
        ADMIN_WIZARD[uid]["mode"] = mode
        
        if mode == "1":
            prompt = "**Step 5/5:** Naya Pura Caption text bhejein:"
        elif mode == "2":
            prompt = "**Step 5/5:** Purana Word aur Naya Word bhejein `|` laga kar.\n*(Example: Purana | Naya)*:"
        elif mode == "3":
            prompt = "**Step 5/5:** Wo Word/Link bhejein jise poori tarah Delete karna hai:"
        elif mode == "4":
            prompt = "**Step 5/5:** Wo Text bhejein jise caption ke sabse **Upar** (Top) lagana hai:"
        elif mode == "5":
            prompt = "**Step 5/5:** Wo Text bhejein jise caption ke sabse **Niche** (Bottom) lagana hai:"
        elif mode == "6":
            prompt = "**Step 5/5:** Smart Auto-Format select kiya gaya hai. Iske liye input ki zarurat nahi, bas **'START'** likh kar bhej dein:"
        elif mode == "7":
            prompt = "**Step 5/5:** Replace aur Delete ek sath karne ke liye comma `,` lagakar bhejein.\n*(Agar sirf Delete karna hai toh `|` ke baad khali chhod dein)*\n\n**Format:** `Purana|Naya, HataoWord|, DeleteThis|`\n**Example:** `Testbook|Kamal, _enc|, @Team_JeeX|`"
            
        await message.reply_text(prompt, parse_mode=ParseMode.MARKDOWN)
        return True

    elif state["step"] == "advcap_data":
        data_text = message.text
        mode = ADMIN_WIZARD[uid]["mode"]
        channel_id = ADMIN_WIZARD[uid]["channel_id"]
        start_msg_id = ADMIN_WIZARD[uid]["start_msg_id"]
        end_msg_id = ADMIN_WIZARD[uid]["end_msg_id"]
        
        await message.reply_text(
            "🚀 **Advanced Caption Changer Started!**\nBot ab apna kaam kar raha hai...",
            parse_mode=ParseMode.MARKDOWN
        )
        
        asyncio.create_task(run_advanced_caption_changer(client, message, channel_id, start_msg_id, end_msg_id, mode, data_text))
        del ADMIN_WIZARD[uid]
        return True

    elif state["step"].startswith("call_cmd_"):
        cmd_name = state["step"].replace("call_cmd_", "")
        current_step = state["step"]
        try:
            cmds = {
                "addadmin": cmd_add_admin,
                "deladmin": cmd_del_admin,
                "ban": cmd_ban,
                "unban": cmd_unban,
                "kick": cmd_kick_user,
                "find": cmd_find_user,
                "resetuser": cmd_reset_user,
                "demo": cmd_approve_demo,
                "perm": cmd_approve_perm,
                "extend": cmd_extend_demo,
                "gendemo": cmd_gendemo,
                "settestbot": cmd_set_testbot,
                "setwelcome": cmd_set_welcome,
                "delbatch": cmd_delbatch,
                "addcat": cmd_addcat,
                "setcat": cmd_setcategory,
                "emptybatch": cmd_emptybatch,
                "userbotphone": cmd_userbotphone,
                "userbototp": cmd_userbototp,
                "userbotpass": cmd_userbotpass,
                "storebatch": cmd_storebatch,
                "userlookup": cmd_user_lookup,
                "superfwd": cmd_superfwd_start,
                "advcap": cmd_advcap_start,
                "deluser": cmd_deluser,
                "updatepost": process_updatepost,
                "genvoucher": cmd_genvoucher_from_wizard,
                "setreqnote": cmd_set_request_note,
                "setprice": cmd_set_batch_price,
            }
            if cmd_name in cmds:
                await cmds[cmd_name](client, message)
        except Exception:
            pass
        if uid in ADMIN_WIZARD and ADMIN_WIZARD[uid].get("step") == current_step:
            del ADMIN_WIZARD[uid]
        return True
    return False

async def handle_broadcast_flow(client: Client, message: Message):
    if not message.from_user or message.from_user.id not in BROADCAST_STATE:
        return False
    state = BROADCAST_STATE[message.from_user.id]
    if state["step"] == "wait_msg":
        state["content"] = message
        state["step"] = "confirm"
        kb = [
            [InlineKeyboardButton("🚀 Send Broadcast Now", callback_data="bc_yes")],
            [InlineKeyboardButton("❌ Cancel Broadcast", callback_data="bc_no")],
        ]
        await message.reply_text(
            "📢 **Broadcast Confirmation**\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\nKya aap sach me yeh message sabhi users ko broadcast karna chahte hain?",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode=ParseMode.MARKDOWN,
        )
        return True
    return False

async def broadcast_callback(client: Client, q: CallbackQuery):
    uid = q.from_user.id
    if uid not in BROADCAST_STATE:
        return await q.answer("Expired")
    if q.data == "bc_no":
        del BROADCAST_STATE[uid]
        return await q.edit_message_text("  Cancelled")
    if q.data == "bc_yes":
        await q.answer()
        await q.edit_message_text("⏳ **Processing Broadcast...**")
        
        count = 0
        blocked_count = 0
        is_user_broadcast = BROADCAST_STATE[uid]["type"] == "broadcast"
        blocked_to_kick = []

        targets = (
            list(DB.get("USER_DATA", {}).keys())
            if is_user_broadcast
            else list(DB.get("FREE_CHANNELS", {}).keys())
            + list(DB.get("PAID_CHANNELS", {}).keys())
            + list(DB.get("SPECIAL_CHANNELS", {}).keys())
        )

        total_targets = len(targets)

        for index, tid in enumerate(targets):
            try:
                await client.copy_message(
                    int(tid), uid, BROADCAST_STATE[uid]["content"].id
                )
                count += 1
                await asyncio.sleep(0.05)
            except FloodWait as e:
                await asyncio.sleep(e.value + 1)
                try:
                    await client.copy_message(int(tid), uid, BROADCAST_STATE[uid]["content"].id)
                    count += 1
                except Exception:
                    pass
            except Exception:
                # Agar user broadcast hai aur error aaya, matlab Bot Blocked ya Account Deleted
                if is_user_broadcast:
                    blocked_count += 1
                    blocked_to_kick.append(int(tid))

            # Live Progress Update (Har 50 users ke baad msg update hoga)
            if index > 0 and index % 50 == 0:
                try:
                    await q.edit_message_text(
                        f"📡 **Broadcasting In Progress...**\n\n"
                        f"📤 Sent: `{count} / {total_targets}`\n"
                        f"🚫 Blocked/Removed: `{blocked_count}`"
                    )
                except Exception:
                    pass

        # Final Success Message
        await q.edit_message_text(
            f"✅ **Broadcast Completed!**\n\n"
            f"📤 Successfully Sent: `{count}`\n"
            f"🚫 Blocked & Removed: `{blocked_count}`\n\n"
            f"💡 *Jin users ne bot ko block kiya tha, unhe sabhi premium/free batches se nikal diya gaya hai (Mandatory channel me abhi bhi hain).* ",
            parse_mode=ParseMode.MARKDOWN
        )

        del BROADCAST_STATE[uid]

        # Process blocked users in background with bounded concurrency
        if is_user_broadcast and blocked_to_kick:
            async def _kick_blocked_batch(uids):
                sem = asyncio.Semaphore(3)
                async def _kick_worker(u_id):
                    async with sem:
                        try:
                            from config import execute_universal_kick
                            await execute_universal_kick(u_id, client, permanent_ban=True)
                        except Exception:
                            pass
                        await asyncio.sleep(0.5)
                await asyncio.gather(*[_kick_worker(u) for u in uids], return_exceptions=True)

            asyncio.create_task(_kick_blocked_batch(blocked_to_kick))

async def general_callback(client: Client, q: CallbackQuery):
    uid = q.from_user.id
    data = q.data
    if q.message:
        q.message.from_user = q.from_user
    if uid in DB.get("BLOCKED_USERS", []):
        return await q.answer("  You are blocked by the admin.", show_alert=True)
    try:
        if data.startswith("wiz_") or data.startswith("wcat_"):
            return await wizard_callback(client, q)
        if data.startswith("bc_"):
            return await broadcast_callback(client, q)
        if data == "role_selector":
            if not is_admin(uid):
                return await q.answer("  Access Denied!", show_alert=True)
            await q.answer()
            return await show_role_selector_cb(client, q)
        if data == "goto_owner_panel":
            return await goto_owner_panel_cb(client, q)
        if data == "goto_admin_panel":
            return await goto_admin_panel_cb(client, q)
        if data == "goto_user_panel":
            return await goto_user_panel_cb(client, q)
        if data == "dash_home":
            if uid in ADMIN_WIZARD:
                del ADMIN_WIZARD[uid]
            await q.answer()
            await start_from_cb(client, q)
            
        elif data == "dash_locks":
            await q.answer()
            msg = build_security_panel_msg()
            await rp.edit(client, q.message.chat.id, q.message.id, msg)
        elif data.startswith("toggle_"):
            await q.answer()
            key = data.split("_")[1].upper()
            if key == "LOCKDOWN":
                DB["NEW_USERS_ALLOWED"] = not DB.get("NEW_USERS_ALLOWED", True)
            elif key == "MAINTENANCE":
                DB["MAINTENANCE_MODE"] = not DB.get("MAINTENANCE_MODE", False)
            elif key == "FREE":
                DB["FREE_LOCKED"] = not DB.get("FREE_LOCKED", False)
            elif key == "PAID":
                DB["PAID_LOCKED"] = not DB.get("PAID_LOCKED", False)
            elif key == "TESTBOT":
                DB["TEST_BOT_LOCKED"] = not DB.get("TEST_BOT_LOCKED", False)
            await save_data_async()
            msg = build_security_panel_msg()
            await rp.edit(client, q.message.chat.id, q.message.id, msg)
        elif data == "dash_db":
            await q.answer()
            msg = build_database_panel_msg()
            await rp.edit(client, q.message.chat.id, q.message.id, msg)
        elif data in ["dash_batches", "adash_batches"]:
            await q.answer()
            back_cb = "goto_admin_panel" if data.startswith("adash_") else "dash_home"
            msg = build_batches_panel_msg(back_cb=back_cb)
            await rp.edit(client, q.message.chat.id, q.message.id, msg)
        elif data == "dash_staff":
            await q.answer()
            msg = build_staff_panel_msg()
            await rp.edit(client, q.message.chat.id, q.message.id, msg)
        elif data in ["dash_comms", "adash_comms"]:
            await q.answer()
            back_cb = "goto_admin_panel" if data.startswith("adash_") else "dash_home"
            msg = build_comms_panel_msg(back_cb=back_cb)
            await rp.edit(client, q.message.chat.id, q.message.id, msg)
        elif data == "userbot_details":
            await q.answer()
            if uid != OWNER_ID and str(uid) != str(OWNER_ID):
                return await q.answer("⚠️ Access Denied! Owner only.", show_alert=True)
            session = DB.get("USERBOT_SESSION")
            phone = DB.get("USERBOT_PHONE", "Not Found")
            msg = build_userbot_panel_msg(session, phone)
            await rp.edit(client, q.message.chat.id, q.message.id, msg)
        elif data == "userbot_logout":
            if uid != OWNER_ID and str(uid) != str(OWNER_ID):
                return
            DB["USERBOT_SESSION"] = None
            DB["USERBOT_PHONE"] = None
            await save_data_async()
            if os.path.exists("temp_owner.session"):
                os.remove("temp_owner.session")
            await q.answer("✅ Session Deleted Successfully!", show_alert=True)
            logout_msg = rp.message(
                rp.heading("🤖 USERBOT CONTROL PANEL"),
                rp.para("⚠️ ", rp.bold("Userbot is now LOGGED OUT.")),
                rp.divider(),
                rp.expandable_quote("Userbot session successfully terminate ho gaya hai."),
                rp.buttons(
                    rp.btn("🔑 Login Again", "input_userbotphone", style=rp.Style.SUCCESS),
                    rp.btn("🔙 Back to Terminal", "dash_home", style=rp.Style.DANGER)
                )
            )
            await rp.edit(client, q.message.chat.id, q.message.id, logout_msg)
        elif data.startswith("input_"):
            await q.answer()
            cmd_name = data.split("_")[1]
            ADMIN_WIZARD[uid] = {"step": f"call_cmd_{cmd_name}"}
            prompts = {
                "addadmin": "Send User ID to make Admin:",
                "deladmin": "Send User ID to remove from Admin:",
                "ban": "Send User ID to Ban:",
                "unban": "Send User ID to Unban:",
                "kick": "Send User ID and Batch ID\nFormat: `uid bid`",
                "find": "Send Username to find:",
                "resetuser": "Send User ID to reset:",
                "demo": "Send Link and Time:\nFormat: `link 10h`",
                "perm": "Send Link to approve:",
                "extend": "Send User ID, Batch ID, Hours:\nFormat: `uid bid 24`",
                "gendemo": "Send User ID and Batch ID to generate a 3-hour Demo link:\nFormat: `uid bid`",
                "settestbot": "Send new Test Bot link:",
                "setwelcome": "Send Batch ID and Welcome Msg:\nFormat: `bid message`",
                "delbatch": "Send Type and ID:\nFormat: `free 123` or `paid 123` or `special 123`",
                "addcat": "Send Name for new Category:",
                "setcat": "Send Batch ID(s) (comma ya space lagakar):\nFormat: `-100x, -100y`",
                "emptybatch": "  **DHYAN DEIN!**\nSend Batch ID jisko poora khali (empty) karna hai:\nFormat: `-100123456789`",
                "advcap": "📝 **Advanced Caption Changer (Step 1/5)**\n\nUs **Channel ID** ko bhejein jiske captions edit karne hain (e.g. `-10012345678`):",
                "cleanbatch": "🛡️ **Clean Unverified Users (Anti-Leech)**\n\nUs **Batch/Channel ID** ko bhejein jise clean karna hai (e.g. `-100123456789`).\n\n*Note: Ye un sabhi users ko nikal dega jo Mandatory Channel me nahi hain ya jinhone bot start nahi kiya hai.*",
                "storebatch": "🗄️ **Store Batch Data**\n\nJis channel ka purana data (Videos/PDFs) Firebase me index karna hai, uska Chat ID bhejein:\nFormat: `-100123456789`",
                "updatepost": "📢 **Post Batch Update**\n\nJis purane batch ka update post channel me bhejna hai, uska **Batch ID** bhejein (e.g. `-100123456789`):",
                "superfwd": "🚀 **Super Forwarder (Step 1/7)**\n\nUs **Source Channel ID** ko bhejein jahan se files (content) uthani hain (e.g. `-10012345678`):",
                "userbotphone": "  **Apna Phone Number bhejein**\nCountry code ke sath (Jaise: `+919876543210`):",
                "userbototp": "  **OTP Bhejein**\n  *OTP spaces me bhejein!* Jaise: `1 2 3 4 5`:",
                "userbotpass": "  **2FA Password bhejein:**",
                "userlookup": "🔍 **Specific User Data**\n\nUser ka User ID bhejein:",
                "deluser": "☢️ **HARD DELETE USER**\n\nJis user ka data database se **poori tarah mitana hai**, uska **User ID** bhejein.\n*(Warning: Ye action undo nahi hoga)*:",
                "genvoucher": "🎟️ **GENERATE REDEEM CODE / VOUCHER**\n\nFormat: `<CODE> <COINS> <MAX_USES>`\n(Example: `DIWALI5 5 100` ya `GIFT10 10 50`):",
                "setreqnote": "📝 **SET BATCH REQUEST CUSTOM NOTE**\n\nNaya note message bhejein jo users ko batch demand karte waqt show hoga:\n(Example: `Admin abhi busy hain, kripya thoda dhairya rakhein...`):",
                "setprice": "💰 **SET PAID BATCH PRICE**\n\nFormat: `<BATCH_ID> <PRICE>`\n(Example: `-100123456789 ₹499`):",
            }
            await q.edit_message_text(
                f"  **INPUT REQUIRED FOR: {cmd_name.upper()}**\n\n{prompts.get(cmd_name, 'Send input:')}",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("❌ Cancel Input", callback_data="dash_home")
                ]]),
                parse_mode=ParseMode.MARKDOWN,
            )

        # =====================================================================
        # 🎁 GIFT COIN (Owner/Admin tool — 2-step wizard: ask UID, then amount)
        # =====================================================================
        elif data == "giftcoin_start":
            if not is_admin(uid):
                return await q.answer("❌ Sirf admins ke liye.", show_alert=True)
            await q.answer()
            ADMIN_WIZARD[uid] = {"step": "giftcoin_uid"}
            await q.edit_message_text(
                "🎁 **Gift Coin**\n\nJis user ko coins gift karne hain, uska User ID bhejein:",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="dash_home")]]),
                parse_mode=ParseMode.MARKDOWN,
            )

        elif data.startswith("giftcoin_direct_"):
            if not is_admin(uid):
                return await q.answer("❌ Sirf admins ke liye.", show_alert=True)
            target = int(data.replace("giftcoin_direct_", ""))
            target_key = target if target in DB["USER_DATA"] else str(target)
            if target_key not in DB["USER_DATA"]:
                return await q.answer("❌ User database me nahi mila.", show_alert=True)
            await q.answer()
            ADMIN_WIZARD[uid] = {"step": "giftcoin_amount", "target": target}
            await q.message.reply_text(
                f"🎁 User `{target}` ko kitne Coins gift karne hain? (Number bhejein, e.g. `5`)",
                parse_mode=ParseMode.MARKDOWN,
            )
        elif data == "dash_stats":
            await q.answer("Generating Stats...")
            await cmd_stats(client, q.message)
        elif data == "adash_users":
            await q.answer()
            msg = build_admin_users_panel_msg(back_cb="goto_admin_panel")
            await rp.edit(client, q.message.chat.id, q.message.id, msg)
        elif data == "adash_approvals":
            await q.answer()
            msg = build_admin_approvals_panel_msg(back_cb="goto_admin_panel")
            await rp.edit(client, q.message.chat.id, q.message.id, msg)
        elif data == "act_vouchers":
            await q.answer()
            await cmd_list_vouchers(client, q.message)
        elif data == "dash_commands":
            await q.answer()
            msg = build_commands_manual_msg()
            await rp.edit(client, q.message.chat.id, q.message.id, msg)

        # =====================================================================
        # 📥 RESTORE OLD BATCHES WIZARD & GUIDE
        # =====================================================================
        elif data == "act_restorebatches":
            if not is_admin(uid):
                return await q.answer("❌ Sirf admins ke liye.", show_alert=True)
            await q.answer()
            info_text = (
                "📥 **RESTORE OLD BATCHES FROM BACKUP**\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "Aap apne purane erased batches ko Userbot ke zariye bot me auto-add aur admin promote kar sakte hain.\n\n"
                "📌 **Step-by-Step Guide:**\n"
                "1️⃣ Apna saved MongoDB/JSON backup file chat me **Document (📎 File)** ke taur par send karein.\n"
                "2️⃣ File ke caption me `/restorebatches` likhein aur send karein.\n"
                "3️⃣ Bot automatic backup file download karke restore engine start kar dega!\n\n"
                "🛡️ **Safety Guarantee:**\n"
                "• Jo batches aapne abhi manually upload ya update kiye hain, wo **100% waise hi surakshit rahenge** (overwrite nahi honge).\n"
                "• Sirf missing batches Userbot ke through channels me join honge, bot ko add & all permissions ke sath promote karenge, aur unki original categories restore ho jayengi!"
            )
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Database Tools", callback_data="dash_db")]])
            await q.edit_message_text(info_text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

        # =====================================================================
        # 📋 BATCH REQUESTS DEMANDS & CHECKLIST TRACKER
        # =====================================================================
        elif data == "view_batch_requests":
            if not is_admin(uid):
                return await q.answer("❌ Sirf admins ke liye.", show_alert=True)
            await q.answer()
            msg = build_batch_requests_msg(page=0)
            await rp.edit(client, q.message.chat.id, q.message.id, msg)

        elif data.startswith("breq_p_"):
            if not is_admin(uid):
                return await q.answer("❌ Sirf admins ke liye.", show_alert=True)
            page = int(data.split("_")[2])
            await q.answer()
            msg = build_batch_requests_msg(page=page)
            await rp.edit(client, q.message.chat.id, q.message.id, msg)

        elif data.startswith("breq_t_"):
            if not is_admin(uid):
                return await q.answer("❌ Sirf admins ke liye.", show_alert=True)
            parts = data.split("_")
            item_global_idx = int(parts[2])
            page = int(parts[3])

            reqs = DB.get("BATCH_REQUESTS", {})
            statuses = DB.setdefault("BATCH_REQUEST_STATUS", {})

            def _sort_key(item):
                bname, uids = item
                status = statuses.get(bname, "pending")
                status_rank = 0 if status != "done" else 1
                return (status_rank, -len(uids), bname.lower())

            sorted_items = sorted(reqs.items(), key=_sort_key)
            if item_global_idx < 0 or item_global_idx >= len(sorted_items):
                return await q.answer("❌ Invalid batch index.", show_alert=True)

            bname, uids = sorted_items[item_global_idx]
            current_status = statuses.get(bname, "pending")

            if current_status == "done":
                # Toggle back to pending
                statuses[bname] = "pending"
                await save_data_async()
                await q.answer(f"Marked '{bname}' as Pending (☐)")
            else:
                # Mark done & notify requesting users
                statuses[bname] = "done"
                await save_data_async()

                notified_count = 0
                for target_uid in uids:
                    try:
                        notify_kb = InlineKeyboardMarkup([
                            [InlineKeyboardButton("📚 Open Batches Catalog", callback_data="all_batches_0")],
                            [InlineKeyboardButton("🏠 Main Menu", callback_data="u_main")]
                        ])
                        await client.send_message(
                            int(target_uid),
                            f"🎉 **Great News! Aapka Requested Batch Live Ho Chuka Hai!**\n"
                            f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                            f"📦 **Batch Name:** `{bname}`\n\n"
                            f"Aapne is batch ke liye demand request submit ki thi. Admin ne ise bot me successfully upload kar diya hai!\n\n"
                            f"Abhi access karke apni preparation continue karein! 🚀",
                            reply_markup=notify_kb,
                            parse_mode=ParseMode.MARKDOWN
                        )
                        notified_count += 1
                    except Exception as err:
                        logger.warning(f"Could not notify user {target_uid}: {err}")

                alert_txt = f"✅ '{bname}' marked as Uploaded (☑)!\n👥 {notified_count} user(s) notified."
                await q.answer(alert_txt, show_alert=True)

            msg = build_batch_requests_msg(page=page)
            await rp.edit(client, q.message.chat.id, q.message.id, msg)

        elif data == "act_adminlist":
            await q.answer("Fetching Admin List...")
            
            # Security check: Sirf owner ye list dekh sakta hai
            if uid != OWNER_ID and str(uid) != str(OWNER_ID):
                return await q.answer("⚠️ Access Denied! Owner Only.", show_alert=True)
                
            admin_ids = DB.get("ADMIN_IDS", [])
            lines = []
            if not admin_ids:
                lines.append("Koi admin assign nahi kiya gaya hai.")
            else:
                for aid in admin_ids:
                    user_key = aid if aid in DB.get("USER_DATA", {}) else (str(aid) if str(aid) in DB.get("USER_DATA", {}) else None)
                    user_info = DB.get("USER_DATA", {}).get(user_key, {}) if user_key else {}
                    name = user_info.get("name", "Unknown User")
                    username = user_info.get("username", "N/A")
                    role = "👑 Owner" if str(aid) == str(OWNER_ID) else "🧑‍💼 Admin"
                    lines.append(f"• {role}: {name} (ID: `{aid}`) @{username}")
            
            admin_msg = rp.message(
                rp.heading("📋 Bot Admins List"),
                rp.divider(),
                rp.expandable_quote("\n".join(lines)),
                rp.buttons(rp.btn("🔙 Back to Staff Menu", "dash_staff", style=rp.Style.DANGER))
            )
            await rp.edit(client, q.message.chat.id, q.message.id, admin_msg)
        elif data == "act_backup":
            await q.answer("Sending...")
            await cmd_backup(client, q.message)
        elif data == "act_sync":
            await q.answer("Sync Started!")
            await cmd_sync(client, q.message)
        elif data == "act_allusers":
            await q.answer("Generating...")
            await cmd_all_users(client, q.message)
        elif data == "act_batchstats":
            await q.answer("Calculating...")
            await cmd_batch_stats(client, q.message)
        elif data == "act_addbatch":
            await q.answer()
            await cmd_addbatch_start(client, q.message)
        elif data == "act_delcat":
            await q.answer()
            await cmd_delcat(client, q.message)
        elif data == "act_broadcast":
            await q.answer()
            await cmd_broadcast_start(client, q.message)
        elif data == "act_post":
            await q.answer()
            await cmd_post_start(client, q.message)

        # --- TERMS AND CONDITIONS & REFERRAL PROCESSOR ---
        elif data == "accept_tnc":
            await q.answer()
            user_key = uid if uid in DB["USER_DATA"] else str(uid)
            DB.setdefault("USER_DATA", {}).setdefault(user_key, {})["tnc_accepted"] = True
            await save_data_async()

            # Process pending referral if referee accepts TnC
            if "pending_referral" in DB["USER_DATA"][user_key]:
                referrer_id = DB["USER_DATA"][user_key].pop("pending_referral")
                await save_data_async()
                await process_successful_referral(client, uid, referrer_id)

            await show_user_menu_cb(client, q)

        elif data == "u_main":
            await q.answer()
            await show_user_menu_cb(client, q)

        # =====================================================================
        # 🎁 DAILY STREAK CLAIM SYSTEM
        # =====================================================================
        elif data == "claim_daily_streak":
            await q.answer()
            user_key = uid if uid in DB["USER_DATA"] else str(uid)
            user_info = DB["USER_DATA"].setdefault(user_key, {})
            now_dt = datetime.now()
            today_str = now_dt.strftime("%Y-%m-%d")
            yesterday_str = (now_dt - timedelta(days=1)).strftime("%Y-%m-%d")
            last_claim = user_info.get("last_streak_date")

            if last_claim == today_str:
                return await q.answer("⏳ Aapne aaj ka streak reward pehle hi claim kar liya hai! Agla reward kal milega.", show_alert=True)

            streak = user_info.get("streak", 0)
            if last_claim == yesterday_str:
                streak = (streak % 7) + 1
            else:
                streak = 1

            reward_coins = 0.25 if streak in [1, 2] else (0.50 if streak in [3, 4, 5, 6] else 1.0)
            user_info["streak"] = streak
            user_info["last_streak_date"] = today_str
            user_info["referral_count"] = round(user_info.get("referral_count", 0) + reward_coins, 2)
            await save_data_async()

            if q.message:
                await safe_reaction(client, q.message.chat.id, q.message.id, "🎉")

            streak_boxes = []
            for day in range(1, 8):
                if day < streak:
                    streak_boxes.append(f"D{day}: ✅")
                elif day == streak:
                    streak_boxes.append(f"D{day}: 🔥")
                else:
                    streak_boxes.append(f"D{day}: ⚪")

            bar_display = " | ".join(streak_boxes)
            kb = [
                [InlineKeyboardButton("🏆 View Leaderboard", callback_data="show_leaderboard")],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="u_main")]
            ]

            msg_text = (
                f"🔥 **Daily Streak Claimed!** 🔥\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🎉 **Badhai ho! Streak Active!**\n\n"
                f"⚡ **Current Streak:** `{streak} Days`\n"
                f"💰 **Reward Added:** `+{reward_coins} Coins`\n"
                f"💎 **Total Balance:** `{user_info['referral_count']} Coins`\n\n"
                f"📅 **7-Day Streak Tracker:**\n"
                f"`[{bar_display}]`\n\n"
                f"💡 *Roz bot open karke claim karein! Day 7 par milega Mega Bonus!*"
            )
            await q.edit_message_text(msg_text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

        # =====================================================================
        # 🏆 LIVE REFERRAL LEADERBOARD
        # =====================================================================
        elif data == "show_leaderboard":
            await q.answer()
            user_key = uid if uid in DB["USER_DATA"] else str(uid)
            ranked = []
            for u_k, u_d in DB.get("USER_DATA", {}).items():
                inv = u_d.get("total_invited", 0)
                if inv > 0:
                    ranked.append((u_k, inv, u_d.get("name") or "Telegram User"))
            ranked.sort(key=lambda x: x[1], reverse=True)

            medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
            lines = []
            top_10 = ranked[:10]
            for idx, (rk, count, uname) in enumerate(top_10):
                medal = medals[idx] if idx < len(medals) else f"`#{idx+1}`"
                crown = " 👑" if idx == 0 else ""
                lines.append(f"{medal} **{uname}** — `{count}` Invites{crown}")

            if not lines:
                lines.append("📭 Abhi tak kisi ne refer nahi kiya hai. Pehle banen!")

            user_rank_txt = "🎯 Your Rank: `Not Ranked (0 Invites)`"
            for idx, (rk, count, _) in enumerate(ranked):
                if str(rk) == str(user_key):
                    user_rank_txt = f"🎯 Your Rank: `#{idx + 1}` (`{count}` Invites)"
                    break

            kb = [
                [InlineKeyboardButton("🎁 Refer & Earn", callback_data="menu_refer")],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="u_main")]
            ]

            txt = (
                "🏆 **TOP 10 REFERRAL CHAMPIONS** 🏆\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                + "\n".join(lines)
                + "\n━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                + f"{user_rank_txt}\n\n"
                "✨ *Top referrers ko automatic VIP tier aur free batches ka access milta hai!*"
            )
            await q.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

        # =====================================================================
        # 🙋 REQUEST A BATCH
        # =====================================================================
        elif data == "req_batch_btn":
            await q.answer()
            USER_FLOW_STATE[uid] = "wait_batch_req"
            kb = [[InlineKeyboardButton("❌ Cancel", callback_data="cancel_user_flow")]]
            await q.edit_message_text(
                "🙋 **Request a New Batch / Course**\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "Aapko kaun sa batch chahiye? Kripya uska poora naam type karke message bhejein.\n\n"
                "*(Jaise: 'PW Yakeen NEET 2026', 'Khan Sir UPSC Foundation', 'Civil Booster')*\n\n"
                "Admin aapki demand review karke batch bot me add karenge! 🚀",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.MARKDOWN
            )

        # =====================================================================
        # 🎟️ REDEEM PROMO VOUCHER
        # =====================================================================
        elif data == "redeem_voucher_btn":
            await q.answer()
            USER_FLOW_STATE[uid] = "wait_voucher_code"
            kb = [[InlineKeyboardButton("❌ Cancel", callback_data="cancel_user_flow")]]
            await q.edit_message_text(
                "🎟️ **Redeem Promo Voucher**\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "Agar aapke paas koi Promo Code ya Gift Coupon hai, toh use yahan type karke message bhejein:\n\n"
                "💡 *Check our Updates Channel for surprise promo code drops!*",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.MARKDOWN
            )

        elif data == "cancel_user_flow":
            await q.answer("Cancelled")
            USER_FLOW_STATE.pop(uid, None)
            await show_user_menu_cb(client, q)

        # =====================================================================
        # 🔗 SHARE SPECIFIC BATCH & DEEP-LINK REDIRECT LOGIC
        # =====================================================================
        elif data.startswith("open_batch_"):
            cid = int(data.split("_")[2])
            user_key = uid if uid in DB["USER_DATA"] else str(uid)
            
            # Remove pending batch state so they don't get stuck in a loop
            if "pending_batch" in DB["USER_DATA"].get(user_key, {}):
                del DB["USER_DATA"][user_key]["pending_batch"]
                await save_data_async()
                
            # Smart Redirect to the correct batch panel
            if cid in DB.get("FREE_CHANNELS", {}) or str(cid) in DB.get("FREE_CHANNELS", {}):
                q.data = f"get_f_{cid}"
                return await general_callback(client, q)
            elif cid in DB.get("PAID_CHANNELS", {}) or str(cid) in DB.get("PAID_CHANNELS", {}):
                q.data = f"view_p_{cid}"
                return await general_callback(client, q)
            else:
                q.data = f"view_s_{cid}"
                return await general_callback(client, q)
                
        elif data == "clear_pending_batch":
            user_key = uid if uid in DB["USER_DATA"] else str(uid)
            if "pending_batch" in DB["USER_DATA"].get(user_key, {}):
                del DB["USER_DATA"][user_key]["pending_batch"]
                await save_data_async()
            await q.answer()
            await show_user_menu_cb(client, q)

        elif data.startswith("share_btn_"):
            await q.answer()
            cid = data.split("_")[2]
            bname = DB.get("ALL_CHATS", {}).get(int(cid)) or DB.get("ALL_CHATS", {}).get(cid) or f"Batch {cid}"
            
            import urllib.parse
            import config
            bot_username = getattr(config, 'BOT_USERNAME', 'H4R_Contact_bot')
            batch_link = f"https://t.me/{bot_username}?start=batch_{cid}_{uid}"
            
            share_msg = f"🔥 Join **{bname}** on Telegram!\nAccess complete lectures, PDFs, and premium notes here:\n{batch_link}"
            tg_url = f"https://t.me/share/url?url={urllib.parse.quote(batch_link)}&text={urllib.parse.quote(share_msg)}"
            wa_url = f"https://api.whatsapp.com/send?text={urllib.parse.quote(share_msg)}"

            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("✈️ Send to Telegram", url=tg_url)],
                [InlineKeyboardButton("💬 Send to WhatsApp", url=wa_url)],
                [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="u_main")]
            ])

            try:
                await q.edit_message_text(
                    f"🎯 **Share Link Ready for:**\n`{bname}`\n\n"
                    f"🔗 **Your Direct Share Link:**\n`{batch_link}`\n\n"
                    "💡 *Jab aapka dost is link par click karke bot start karega aur mandatory rules follow karega, toh usse direct yahi batch dikhega aur aapko +1 Referral Coin milega!*",
                    reply_markup=kb,
                    parse_mode=ParseMode.MARKDOWN,
                    disable_web_page_preview=True
                )
            except Exception:
                await client.send_message(uid, f"🎯 **Share Link Ready!**\n\n🔗 `{batch_link}`", reply_markup=kb)

        # --- MY INFO VIEW ---
        elif data == "my_info":
            await q.answer()
            user_key = uid if uid in DB["USER_DATA"] else str(uid)
            u_data = DB["USER_DATA"].get(user_key, {})
            refer_points = u_data.get("referral_count", 0)
            total_invited = u_data.get("total_invited", 0)
            streak = u_data.get("streak", 0)
            is_vip = u_data.get("tier") == "vip"

            progress_bar = render_progress_bar(total_invited, 25, length=10)

            if is_vip:
                txt = (
                    "👑 **[Elite Referrer Profile]** 👑\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🆔 **User ID:** `{uid}`\n"
                    f"🏷️ **Status:** `👑 VIP Referrer` 🌟\n"
                    f"🔥 **Daily Streak:** `{streak} Days`\n"
                    f"👥 **Total Refers:** `{total_invited}`\n"
                    f"💰 **Wallet Balance:** `{refer_points}` Coins\n\n"
                    "💎 *Enjoy zero cooldowns, exclusive VIP materials, and priority access.*"
                )
            else:
                txt = (
                    f"👤 **USER PROFILE & STATS**\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🆔 **User ID:** `{uid}`\n"
                    f"🔥 **Daily Streak:** `{streak} Days`\n"
                    f"👥 **Total Refers:** `{total_invited}`\n"
                    f"🎁 **Available Coins:** `{refer_points}`\n\n"
                    f"🎯 **VIP Tier Goal:** `{total_invited}/25`\n"
                    f"`[{progress_bar}]`\n\n"
                    f"💡 *Coins ka use karke aap koi bhi Special Batch ya Free Batch unlock kar sakte hain.*"
                )
            kb = [
                [InlineKeyboardButton("🔥 Claim Daily Streak", callback_data="claim_daily_streak")],
                [InlineKeyboardButton("🏆 Top Leaderboard", callback_data="show_leaderboard")],
                [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="u_main")]
            ]
            await q.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

        # --- HOME MENU: REFER & EARN LAYOUT ---
        elif data == "menu_refer":
            try:
                await q.answer()
                import urllib.parse
                import config
                bot_username = getattr(config, 'BOT_USERNAME', 'H4R_Contact_bot')
                ref_link = f"https://t.me/{bot_username}?start={uid}"

                share_text = "Join me on H4R — the smartest way to crack your exam!"
                tg_share_url = f"https://t.me/share/url?url={urllib.parse.quote(ref_link)}&text={urllib.parse.quote(share_text)}"
                wa_share_url = f"https://api.whatsapp.com/send?text={urllib.parse.quote(ref_link + ' ' + share_text)}"

                user_key = uid if uid in DB["USER_DATA"] else str(uid)
                u_data = DB["USER_DATA"].get(user_key, {})
                pts = u_data.get("referral_count", 0)
                total_inv = u_data.get("total_invited", 0)
                is_vip = u_data.get("tier") == "vip"

                progress_bar = render_progress_bar(total_inv, 25, length=10)

                refer_elements = [
                    rp.heading("🎁 Refer & Earn Rewards"),
                    rp.divider(),
                    rp.para(
                        ce("TROPHY", "🏆"), " ",
                        rp.marked(" Milestone Bonus: Har 5 refers par +1 EXTRA Coin! ")
                    ),
                    rp.para(
                        ce("CROWN", "👑"), " ",
                        rp.marked(" VIP Elite Tag: 25 refers cross karne par auto unlock! ")
                    ),
                    rp.divider(),
                    rp.para(
                        ce("CROWN", "👑"), " ", rp.bold("Your Tier: "),
                        rp.marked(" 👑 VIP Referrer 🌟 ") if is_vip else rp.italic("Standard Referrer")
                    ) if is_vip else rp.para(
                        ce("STATS", "📊"), " ", rp.bold("VIP Goal Progress: "),
                        rp.code(f"[{progress_bar}] {total_inv}/25")
                    ),
                    rp.para(
                        ce("CHECK", "✅"), " ", rp.bold("Total Referred: "), rp.code(f"{total_inv} Users"),
                        "  |  ",
                        ce("DIAMOND", "💎"), " ", rp.bold("Wallet Balance: "), rp.code(f"{pts} Coins")
                    ),
                    rp.blockquote(
                        ce("LINK", "🔗"), " ", rp.bold("Your Personal Referral Link:\n"),
                        rp.code(ref_link), "\n\n",
                        rp.italic("Niche diye gaye copy button ya sharing links se dosto ko invite karein!")
                    ),
                    rp.buttons(rp.copy_btn("📋 1-Tap Copy Invite Link", ref_link)),
                    rp.buttons(
                        rp.url_btn("✈️ Share on Telegram", tg_share_url),
                        rp.url_btn("💬 Share on WhatsApp", wa_share_url)
                    ),
                    rp.buttons(
                        rp.btn("🏆 Top 10 Leaderboard", "show_leaderboard", style=rp.Style.SUCCESS),
                        rp.btn("🏠 Main Menu", "u_main", style=rp.Style.DANGER)
                    )
                ]

                ref_msg = rp.message(*refer_elements)
                await rp.edit(client, q.message.chat.id, q.message.id, ref_msg)
            except Exception as e:
                logger.error(f"Refer & Earn Error: {e}")
                await q.answer("⚠️ Menu load hone me error aayi. Kripya wapas try karein.", show_alert=True)

        # =====================================================================
        # 💎 VIP-ONLY: EXCLUSIVE COURSE MATERIALS
        # =====================================================================
        elif data == "vip_materials":
            await q.answer()
            user_key = uid if uid in DB["USER_DATA"] else str(uid)
            if DB["USER_DATA"].get(user_key, {}).get("tier") != "vip":
                return await q.answer("👑 Yeh sirf VIP Referrers ke liye hai!", show_alert=True)

            materials_link = DB.get("VIP_MATERIALS_LINK")
            kb = [[InlineKeyboardButton("🔙 Back", callback_data="u_main")]]
            if materials_link:
                kb.insert(0, [InlineKeyboardButton("📂 Open Materials", url=materials_link)])
                txt = "💎 **VIP Course Materials**\n\nYeh sirf Elite Referrers ke liye hai. Neeche button se access karein."
            else:
                txt = "💎 **VIP Course Materials**\n\nAdmin abhi materials link update kar rahe hain. Jald hi yahan se access milega!"

            await q.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

        # =====================================================================
        # 🎁 VIP-ONLY: CLAIM MONTHLY BONUS
        # =====================================================================
        elif data == "vip_monthly_bonus":
            user_key = uid if uid in DB["USER_DATA"] else str(uid)
            if DB["USER_DATA"].get(user_key, {}).get("tier") != "vip":
                return await q.answer("👑 Yeh sirf VIP Referrers ke liye hai!", show_alert=True)

            last_claim = DB["USER_DATA"][user_key].get("last_monthly_bonus", 0)
            elapsed = time.time() - last_claim
            THIRTY_DAYS = 30 * 24 * 60 * 60

            if elapsed < THIRTY_DAYS:
                days_left = int((THIRTY_DAYS - elapsed) // 86400) + 1
                return await q.answer(f"⏳ Agla bonus {days_left} din me claim kar sakte hain!", show_alert=True)

            DB["USER_DATA"][user_key]["referral_count"] = DB["USER_DATA"][user_key].get("referral_count", 0) + 2
            DB["USER_DATA"][user_key]["last_monthly_bonus"] = time.time()
            await save_data_async()

            await q.answer("🎉 +2 Coins Claimed!", show_alert=True)
            await q.edit_message_text(
                "🎉 **MONTHLY BONUS CLAIMED!**\n\n💎 **+2 Coins** aapke wallet me add ho gaye hain, sirf VIP hone ke naate!",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Main Menu", callback_data="u_main")]]),
                parse_mode=ParseMode.MARKDOWN
            )
            await send_vip_treat(client, uid)

        # --- MANDATORY CHANNEL VERIFICATION & REFERRAL PROCESSOR ---
        elif data == "verify":
            # Belt-and-braces: cache turant clear karo taaki neeche wala check hamesha
            # live Telegram status dekhe, kabhi stale cached result nahi.
            invalidate_membership_cache(uid, MANDATORY_CHANNEL_ID)
            if await check_membership_pyro(uid, client):
                await q.answer("  Verification Successful!", show_alert=True)
                user_key = uid if uid in DB["USER_DATA"] else str(uid)
                if not DB["USER_DATA"].get(user_key, {}).get("tnc_accepted", False):
                    await show_tnc_menu_cb(client, q)
                else:
                    if "pending_referral" in DB["USER_DATA"][user_key]:
                        referrer_id = DB["USER_DATA"][user_key].pop("pending_referral")
                        await save_data_async()
                        await process_successful_referral(client, uid, referrer_id)
                    await start_from_cb(client, q)
            else:
                await q.answer(
                    "❌ Not joined yet! Tap 📢 Join Channel, join, then tap ✅ I've Joined again.\n"
                    "❌ Abhi tak join nahi kiya! 📢 Join Channel par tap karein, join karein, phir ✅ I've Joined dobara tap karein.",
                    show_alert=True,
                )

        elif data == "test_bot":
            if DB.get("TEST_BOT_LOCKED", False):
                return await q.answer("  Locked by Admin.", show_alert=True)
            if not await check_membership_pyro(uid, client):
                return await q.answer("  Join Main Channel First!", show_alert=True)
            if not DB.get("TEST_BOT_LINK"):
                return await q.answer("  Test Bot is not setup by Admin yet!", show_alert=True)
            await q.answer("Verifying & Generating Link...")
            kb = [[InlineKeyboardButton("🤖 Open Test Bot", url=DB.get("TEST_BOT_LINK"))]]
            try:
                sent_msg = await client.send_message(
                    uid,
                    "  **Test Bot Access Verification:**\n\nYou are verified! Click the button below to open the Test Series Website.",
                    reply_markup=InlineKeyboardMarkup(kb),
                    parse_mode=ParseMode.MARKDOWN,
                )
                await schedule_delete(client, sent_msg, delay=60)
            except Exception:
                pass

        # --- MY BATCHES LISTING ---
        elif data.startswith("my_batches_"):
            await q.answer()
            user_key = uid if uid in DB["USER_DATA"] else str(uid)
            if not DB["USER_DATA"].get(user_key, {}).get("tnc_accepted", False):
                return await show_tnc_menu_cb(client, q)

            await q.edit_message_text(
                "  **Aapke batches fetch kiye ja rahe hain... Please wait.**",
                parse_mode=ParseMode.MARKDOWN,
            )

            page = int(data.split("_")[-1])
            all_batches = {}
            for col in ["SPECIAL_CHANNELS", "PAID_CHANNELS", "FREE_CHANNELS"]:
                for c_k, c_v in DB.get(col, {}).items():
                    try:
                        n_k = int(str(c_k).strip())
                    except Exception:
                        n_k = c_k
                    if n_k not in all_batches:
                        all_batches[n_k] = c_v
            unlocked_ref_batches = DB["USER_DATA"].get(user_key, {}).get("unlocked_batches", [])

            async def check_member(cid, name):
                is_unlocked = int(cid) in unlocked_ref_batches or str(cid) in unlocked_ref_batches
                try:
                    m = await client.get_chat_member(int(cid), uid)
                    if m.status in [
                        ChatMemberStatus.MEMBER,
                        ChatMemberStatus.ADMINISTRATOR,
                        ChatMemberStatus.OWNER,
                        ChatMemberStatus.RESTRICTED,
                    ]:
                        return (cid, name, "Joined")
                except Exception:
                    pass

                if is_unlocked:
                    return (cid, name, "Referral Unlocked")

                return None

            results = await asyncio.gather(*[check_member(cid, name) for cid, name in all_batches.items()])
            joined_batches = [r for r in results if r is not None]

            if not joined_batches:
                return await q.edit_message_text(
                    "  Aap abhi kisi bhi batch me join nahi hain.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Menu", callback_data="u_main")]]),
                    parse_mode=ParseMode.MARKDOWN,
                )

            MAX_PER_PAGE = 8
            total_batches = len(joined_batches)
            start_idx = page * MAX_PER_PAGE
            end_idx = start_idx + MAX_PER_PAGE

            kb = []
            for cid, name, status_type in joined_batches[start_idx:end_idx]:
                clean_id = str(cid).replace('-100', '')
                if status_type == "Referral Unlocked":
                    button_text = f"✨ {name} [SPECIAL UNLOCKED]"
                else:
                    button_text = f"⚡ {name}"

                kb.append([InlineKeyboardButton(button_text, url=f"https://t.me/c/{clean_id}/1")])

            if page > 0:
                kb.append([InlineKeyboardButton("🔙 Previous Page", callback_data=f"my_batches_{page-1}")])
            if end_idx < total_batches:
                kb.append([InlineKeyboardButton("Next Page ➡️", callback_data=f"my_batches_{page+1}")])
            kb.append([InlineKeyboardButton("🏠 Main Menu", callback_data="u_main")])

            await q.edit_message_text(
                f"  **My Batches (Page {page+1})**\n\nYahan wo sabhi batches hain jisme aap join hain ya unlocked hain. Click karke access karein:",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.MARKDOWN,
            )

        elif data.startswith("all_batches_"):
            await q.answer()
            categories = DB.get("CATEGORIES", DEFAULT_CATEGORIES)
            cat_rows = []
            row = []
            for i, cat in enumerate(categories):
                row.append(rp.btn(f"📁 {cat}", f"showcat_{i}", style=rp.Style.DEFAULT))
                if len(row) == 2:
                    cat_rows.append(rp.buttons(*row))
                    row = []
            if row:
                cat_rows.append(rp.buttons(*row))

            msg = rp.message(
                rp.heading("📂 All Batches Catalog"),
                rp.para("Apni exam preparation ke liye neeche se ", rp.bold("Category"), " chunein:"),
                rp.divider(),
                *cat_rows,
                rp.buttons(
                    rp.btn("🏠 Main Menu", "u_main", style=rp.Style.DANGER)
                )
            )
            await rp.edit(client, q.message.chat.id, q.message.id, msg)

        # --- CATEGORY DETAILS WITH SPECIAL BATCHES BUTTON ---
        elif data.startswith("showcat_"):
            await q.answer()
            cat_idx = int(data.split("_")[1])
            cat_name = DB.get("CATEGORIES", DEFAULT_CATEGORIES)[cat_idx]

            msg = rp.message(
                rp.heading(f"📁 {cat_name}"),
                rp.para("Aapko kis type ke batch chahiye? Neeche category options chunein:"),
                rp.divider(),
                rp.expandable_quote([
                    rp.bold("ℹ️ Available Batch Options:\n\n"),
                    rp.bold("• "), "🆓 ", rp.bold("Free Batches"), " — ", rp.italic("100% Free study courses, direct access bina kisi cost ke\n\n"),
                    rp.bold("• "), "💰 ", rp.bold("Paid Batches"), " — ", rp.italic("Premium batches, full structured lectures aur study material\n\n"),
                    rp.bold("• "), "✨ ", rp.bold("Special Batches"), " — ", rp.italic("Referral coins se free me unlock hone wale courses\n\n"),
                    rp.bold("• "), "🎁 ", rp.bold("Refer & Earn"), " — ", rp.italic("Friends ko invite karein aur free coins earn karein")
                ]),
                rp.buttons(rp.btn("🆓 Free Batches", f"listcat_{cat_idx}_free_0", style=rp.Style.SUCCESS)),
                rp.buttons(rp.btn("💰 Paid Batches", f"listcat_{cat_idx}_paid_0", style=rp.Style.DEFAULT)),
                rp.buttons(rp.btn("✨ Special Batches", f"listcat_{cat_idx}_special_0", style=rp.Style.SUCCESS)),
                rp.buttons(rp.btn("🎁 Refer & Earn", "menu_refer", style=rp.Style.SUCCESS)),
                rp.buttons(
                    rp.btn("🔙 Back to Categories", "all_batches_0", style=rp.Style.DANGER),
                    rp.btn("🏠 Main Menu", "u_main", style=rp.Style.DANGER)
                )
            )
            await rp.edit(client, q.message.chat.id, q.message.id, msg)

        elif data.startswith("setextcat_"):
            await q.answer()
            cat_idx = int(data.split("_")[1])
            selected_cat = DB.get("CATEGORIES", DEFAULT_CATEGORIES)[cat_idx]
            user_store = getattr(client, "user_data_store", {})
            ids = user_store.get("setcat_ids", [])
            if not ids:
                await q.edit_message_text(
                    "  Session expired ya IDs nahi mili. Kripya wapas /start karke try karein."
                )
                return
            for cid in ids:
                DB.setdefault("BATCH_CATEGORIES", {})[str(cid)] = selected_cat
            await save_data_async()
            user_store.pop("setcat_ids", None)
            await q.edit_message_text(
                f"  **Success!**\n\nTotal `{len(ids)}` batches ko successfully **{selected_cat}** category me shift kar diya gaya hai!",
                parse_mode=ParseMode.MARKDOWN,
            )

        # --- BATCH LISTING (FREE, PAID, SPECIAL) ---
        elif data.startswith("listcat_"):
            parts = data.split("_")
            cat_idx, b_type, page = int(parts[1]), parts[2], int(parts[3])

            if b_type == "free" and DB.get("FREE_LOCKED", False):
                return await q.answer("Sorry, but at this moment the free batch is locked. When it will unlock I will inform you.", show_alert=True)
            if b_type == "paid" and DB.get("PAID_LOCKED", False):
                return await q.answer("Sorry, but at this moment the paid batch is locked. When it will unlock I will inform you.", show_alert=True)
            await q.answer()

            user_key = uid if uid in DB["USER_DATA"] else str(uid)
            if not DB["USER_DATA"].get(user_key, {}).get("tnc_accepted", False):
                return await show_tnc_menu_cb(client, q)

            cat_name = DB.get("CATEGORIES", DEFAULT_CATEGORIES)[cat_idx]
            if b_type == "free":
                source_dict = DB.get("FREE_CHANNELS", {})
                prefix, btn_style = "⚡", rp.Style.SUCCESS
            elif b_type == "paid":
                source_dict = DB.get("PAID_CHANNELS", {})
                prefix, btn_style = "👑", rp.Style.DEFAULT
            else:
                source_dict = DB.get("SPECIAL_CHANNELS", {})
                prefix, btn_style = "✨", rp.Style.SUCCESS

            filtered_batches = []
            seen_cids = set()
            for cid, name in source_dict.items():
                try:
                    norm_cid = int(str(cid).strip())
                except Exception:
                    norm_cid = cid
                if norm_cid in seen_cids:
                    continue
                seen_cids.add(norm_cid)
                if DB.get("BATCH_CATEGORIES", {}).get(str(norm_cid), "Other Batches") == cat_name:
                    filtered_batches.append((norm_cid, name))

            if not filtered_batches:
                empty_msg = rp.message(
                    rp.heading(f"📂 {cat_name} ({b_type.title()})"),
                    rp.divider(),
                    rp.para("⚠️ Is category (", rp.bold(cat_name), ") me abhi koi ", rp.bold(f"{b_type.title()} Batch"), " nahi hai."),
                    rp.buttons(
                        rp.btn("🔙 Back", f"showcat_{cat_idx}", style=rp.Style.DANGER),
                        rp.btn("🏠 Main Menu", "u_main", style=rp.Style.DANGER)
                    )
                )
                return await rp.edit(client, q.message.chat.id, q.message.id, empty_msg)

            MAX_PER_PAGE = 10
            total = len(filtered_batches)
            start_idx = page * MAX_PER_PAGE
            end_idx = start_idx + MAX_PER_PAGE

            batch_rows = []
            page_items = filtered_batches[start_idx:end_idx]

            # Single column grid: Har batch apni separate row me
            for cid, name in page_items:
                if b_type == "free":
                    cb_data = f"get_f_{cid}"
                elif b_type == "paid":
                    cb_data = f"view_p_{cid}"
                else:
                    cb_data = f"view_s_{cid}"
                batch_rows.append(rp.buttons(rp.btn(f"{prefix} {name}", cb_data, style=btn_style)))

            nav_btns = []
            if page > 0:
                nav_btns.append(rp.btn("🔙 Previous Page", f"listcat_{cat_idx}_{b_type}_{page-1}", style=rp.Style.DEFAULT))
            if end_idx < total:
                nav_btns.append(rp.btn("Next Page ➡️", f"listcat_{cat_idx}_{b_type}_{page+1}", style=rp.Style.DEFAULT))
            if nav_btns:
                batch_rows.append(rp.buttons(*nav_btns))

            batch_rows.append(rp.buttons(
                rp.btn("📂 Category Menu", f"showcat_{cat_idx}", style=rp.Style.DANGER),
                rp.btn("🏠 Main Menu", "u_main", style=rp.Style.DANGER)
            ))

            list_msg = rp.message(
                rp.heading(f"📂 {cat_name} ({b_type.title()})"),
                rp.para("Neeche diye gaye batches par click karein:"),
                rp.divider(),
                *batch_rows
            )
            await rp.edit(client, q.message.chat.id, q.message.id, list_msg)

        elif data.startswith("delcat_"):
            await q.answer()
            cat_idx = int(data.split("_")[1])
            categories = DB.get("CATEGORIES", DEFAULT_CATEGORIES)
            if cat_idx >= len(categories):
                return await q.answer("Invalid category", show_alert=True)
            deleted_cat = categories[cat_idx]
            if deleted_cat == "Other Batches":
                return await q.answer("  'Other Batches' ko delete nahi kiya ja sakta!", show_alert=True)
            DB["CATEGORIES"].remove(deleted_cat)
            shifted_count = 0
            if "BATCH_CATEGORIES" in DB:
                for cid, cat in DB["BATCH_CATEGORIES"].items():
                    if cat == deleted_cat:
                        DB["BATCH_CATEGORIES"][cid] = "Other Batches"
                        shifted_count += 1
            await save_data_async()
            await q.edit_message_text(
                f"  Category **{deleted_cat}** delete kar di gayi hai.\n\n Uske **{shifted_count} batches** automatically 'Other Batches' me shift ho gaye hain.",
                parse_mode=ParseMode.MARKDOWN,
            )

        elif data == "cancel_delcat":
            await q.answer()
            await q.edit_message_text("  Category deletion cancelled.")

        # NAYA CANCEL TASK HANDLER
        elif data.startswith("cancel_task_"):
            target_uid = int(data.split("_")[2])
            if not hasattr(client, "cancel_tasks"):
                client.cancel_tasks = set()
            client.cancel_tasks.add(target_uid)
            await q.answer("🛑 Cancelling Task... (Process ruk raha hai)", show_alert=True)
            try:
                await q.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass

        elif data.startswith("get_f_"):
            cid = int(data.split("_")[2])
            user_key = uid if uid in DB["USER_DATA"] else str(uid)
            user_rec = DB["USER_DATA"].setdefault(user_key, {})
            joined_free = user_rec.setdefault("free_batches_joined", [])
            FREE_LIMIT = 3

            already_used_slot = cid in joined_free or str(cid) in joined_free
            needs_unlock = (
                not is_vip_user(uid)
                and not user_rec.get("free_unlocked", False)
                and not already_used_slot
                and len(joined_free) >= FREE_LIMIT
            )

            if needs_unlock:
                pts = user_rec.get("referral_count", 0)
                await q.answer()
                if pts >= 1:
                    kb = [
                        [InlineKeyboardButton("🔓 Unlock All Free Batches (Cost: 1 Coin)", callback_data="unlock_all_free")],
                        [InlineKeyboardButton("🔙 Back", callback_data="u_main")],
                    ]
                    return await q.edit_message_text(
                        f"🔒 **Free Batches Locked**\n\nAapne apni **{FREE_LIMIT} free** batches use kar li hain.\nAapke paas **{pts} Coins** hain. **1 Coin** use karke saari (baaki) Free Batches hamesha ke liye unlock karein — dobara coin nahi lagega.",
                        reply_markup=InlineKeyboardMarkup(kb),
                        parse_mode=ParseMode.MARKDOWN
                    )
                else:
                    kb = [
                        [InlineKeyboardButton("🎁 Get Refer Link", callback_data="menu_refer")],
                        [InlineKeyboardButton("🔙 Back", callback_data="u_main")],
                    ]
                    return await q.edit_message_text(
                        f"🔒 **Free Batches Locked**\n\nAapne apni **{FREE_LIMIT} free** batches use kar li hain. Aage ke liye **1 Coin** chahiye.\nApne dosto ko refer karke coins earn karein!",
                        reply_markup=InlineKeyboardMarkup(kb),
                        parse_mode=ParseMode.MARKDOWN
                    )

            if await is_already_in_channel_pyro(client, cid, uid):
                return await q.answer("  Already Joined!", show_alert=True)
            cooldown_left = get_cooldown_remaining(uid)
            if cooldown_left > 0:
                return await q.answer("  Please wait 15 minutes before requesting another link.", show_alert=True)
            if has_active_request(uid, cid):
                return await q.answer("  You already have an active request/link for this batch!", show_alert=True)

            try:
                bname = DB["ALL_CHATS"].get(cid, f"Batch {cid}")
                l = await client.create_chat_invite_link(
                    cid,
                    creates_join_request=True,
                    name=f"Free-{uid}",
                    expire_date=datetime.now() + timedelta(seconds=60),
                )
                register_link_request(uid, cid)

                # Sirf pehli baar is batch ke liye slot count hota hai (free_unlocked hone ke baad tracking ki zaroorat nahi)
                if not user_rec.get("free_unlocked", False) and not already_used_slot:
                    joined_free.append(cid)
                    await save_data_async()

                kb = [
                    [InlineKeyboardButton("🚀 Join Batch", url=l.invite_link)],
                    [InlineKeyboardButton("🔗 Share Batch with Friends", callback_data=f"share_btn_{cid}")]
                ]
                sent_msg = await client.send_message(
                    uid,
                    f"  <b>Link Generated!</b>\n\n<b>{bname}</b>\n\n  <i>Request auto-approved.</i>\n  <i>(Expires in 1 min)</i>",
                    reply_markup=InlineKeyboardMarkup(kb),
                    parse_mode=ParseMode.HTML,
                )
                await schedule_delete(client, sent_msg, delay=60)
                await q.answer("Sent to DM")
            except Exception as e:
                await q.answer(f"Bot Error: {e}", show_alert=True)

        # =====================================================================
        # 🔓 UNLOCK ALL FREE BATCHES (ONE-TIME, DEDUCT 1 COIN — from batch 4 onward)
        # =====================================================================
        elif data == "unlock_all_free":
            user_key = uid if uid in DB["USER_DATA"] else str(uid)
            pts = DB["USER_DATA"].get(user_key, {}).get("referral_count", 0)

            if DB["USER_DATA"].get(user_key, {}).get("free_unlocked", False):
                await q.answer()
                return await q.edit_message_text(
                    "✅ Free Batches pehle se unlocked hain!",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Main Menu", callback_data="u_main")]]),
                    parse_mode=ParseMode.MARKDOWN
                )

            if pts < 1:
                return await q.answer("❌ Aapke paas enough coins nahi hain!", show_alert=True)

            DB["USER_DATA"][user_key]["referral_count"] -= 1
            DB["USER_DATA"][user_key]["free_unlocked"] = True
            await save_data_async()

            await q.answer("🎉 Free Batches Unlocked!", show_alert=True)
            await q.edit_message_text(
                "🎉 **Free Batches Unlocked!**\n\nAb aap kisi bhi Free Batch par click karke turant join kar sakte hain — dobara coin nahi lagega.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🌐 All Batches", callback_data="all_batches_0")],
                    [InlineKeyboardButton("🔙 Main Menu", callback_data="u_main")]
                ]),
                parse_mode=ParseMode.MARKDOWN
            )

        elif data.startswith("view_p_"):
            cid = int(data.split("_")[2])
            if DB.get("PAID_LOCKED", False):
                return await q.answer("Sorry, but at this moment the paid batch is locked. When it will unlock I will inform you.", show_alert=True)
            await q.answer()
            bname = DB.get("ALL_CHATS", {}).get(cid) or DB.get("PAID_CHANNELS", {}).get(cid) or f"Paid Batch {cid}"
            bcat = DB.get("BATCH_CATEGORIES", {}).get(str(cid), "Premium Batches")
            price = DB.get("BATCH_PRICES", {}).get(str(cid), "Affordable / Contact Admin")
            
            kb = [
                [InlineKeyboardButton("🔑 Request Access / Buy", callback_data=f"req_access_{cid}")],
                [InlineKeyboardButton("🔗 Share Batch with Friends", callback_data=f"share_btn_{cid}")],
                [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="u_main")],
            ]
            msg_text = (
                f"👑 **PREMIUM / PAID BATCH**\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📦 **Batch Name:** `{bname}`\n"
                f"📁 **Category:** `{bcat}`\n"
                f"💰 **Batch Price / Fee:** `{price}`\n\n"
                f"ℹ️ Is batch me high quality lectures, structured DPPs aur complete study notes shamil hain.\n"
                f"Access lene ke liye neeche **'🔑 Request Access / Buy'** button par tap karein!"
            )
            try:
                await q.edit_message_text(
                    msg_text,
                    reply_markup=InlineKeyboardMarkup(kb),
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception:
                pass

        # =====================================================================
        # 🌟 VIEW SPECIAL BATCH DETAILS & UNLOCK LOGIC
        # =====================================================================
        elif data.startswith("view_s_"):
            cid = int(data.split("_")[2])
            await q.answer()
            bname = DB.get("ALL_CHATS", {}).get(cid) or DB.get("SPECIAL_CHANNELS", {}).get(cid) or f"Special Batch {cid}"
            cost = DB.get("BATCH_COINS", {}).get(str(cid), 1)
            
            user_key = uid if uid in DB["USER_DATA"] else str(uid)
            unlocked_list = DB["USER_DATA"].get(user_key, {}).get("unlocked_batches", [])
            is_unlocked = cid in unlocked_list or str(cid) in unlocked_list
            pts = DB["USER_DATA"].get(user_key, {}).get("referral_count", 0)
            coin_word = "Coin" if cost == 1 else "Coins"

            kb = []
            if is_unlocked:
                clean_id = str(cid).replace('-100', '')
                kb.append([InlineKeyboardButton("🚀 Join Special Batch", url=f"https://t.me/c/{clean_id}/1")])
                status_str = "🎉 Unlocked!"
                desc = "Aapne is batch ko successfully unlock kar liya hai."
            else:
                if pts >= cost:
                    kb.append([InlineKeyboardButton(f"🔓 Unlock Batch (Cost: {cost} {coin_word})", callback_data=f"unlock_s_{cid}")])
                    status_str = "🔒 Locked"
                    desc = f"Aapke paas **{pts} Coins** hain. Aap {cost} {coin_word} use karke is batch ko unlock kar sakte hain."
                else:
                    kb.append([InlineKeyboardButton("🎁 Get Refer Link", callback_data="menu_refer")])
                    status_str = "🔒 Locked (Not Enough Coins)"
                    desc = f"Aapke paas enough coins nahi hain. Is batch ko unlock karne ke liye aapko **{cost} {coin_word}** chahiye.\nApne dosto ko refer karke coins earn karein!"

            kb.append([InlineKeyboardButton("🔗 Share Batch with Friends", callback_data=f"share_btn_{cid}")])
            kb.append([InlineKeyboardButton("🔙 Back", callback_data="u_main")])

            await q.edit_message_text(
                f"✨ **SPECIAL BATCH:** `{bname}`\n\n"
                f"**Status:** `{status_str}`\n\n{desc}",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.MARKDOWN
            )

        # =====================================================================
        # 🔓 UNLOCK SPECIAL BATCH (DEDUCT PER-BATCH COIN COST)
        # =====================================================================
        elif data.startswith("unlock_s_"):
            cid = int(data.split("_")[2])
            user_key = uid if uid in DB["USER_DATA"] else str(uid)
            cost = DB.get("BATCH_COINS", {}).get(str(cid), 1)
            pts = DB["USER_DATA"].get(user_key, {}).get("referral_count", 0)

            if pts < cost:
                return await q.answer("❌ Aapke paas enough coins nahi hain!", show_alert=True)

            # Deduct the batch's coin cost and add to unlocked list
            DB["USER_DATA"][user_key]["referral_count"] -= cost
            DB["USER_DATA"][user_key].setdefault("unlocked_batches", []).append(cid)
            await save_data_async()
            
            bname = DB.get("ALL_CHATS", {}).get(cid) or DB.get("SPECIAL_CHANNELS", {}).get(cid) or f"Special Batch {cid}"
            coin_word = "Coin" if cost == 1 else "Coins"

            try:
                # Generate unique 1-time invite link
                l = await client.create_chat_invite_link(
                    chat_id=cid,
                    creates_join_request=False,
                    name=f"Unlocked-{uid}",
                    member_limit=1
                )
                kb = [
                    [InlineKeyboardButton("🚀 Join Special Batch", url=l.invite_link)],
                    [InlineKeyboardButton("🔙 Main Menu", callback_data="u_main")]
                ]
                await q.edit_message_text(
                    f"🎉 **SUCCESS!**\n\nAapne **{cost} {coin_word}** use karke **{bname}** successfully unlock kar liya hai.\nNeeche diye gaye button par click karke direct join karein.",
                    reply_markup=InlineKeyboardMarkup(kb),
                    parse_mode=ParseMode.MARKDOWN
                )
                if DB["USER_DATA"].get(user_key, {}).get("tier") == "vip":
                    await send_vip_treat(client, uid)
            except Exception as e:
                logger.error(f"Unlock Special Batch Error: {e}")
                clean_id = str(cid).replace('-100', '')
                kb = [
                    [InlineKeyboardButton("🚀 Open Special Batch", url=f"https://t.me/c/{clean_id}/1")],
                    [InlineKeyboardButton("🔙 Main Menu", callback_data="u_main")]
                ]
                await q.edit_message_text(
                    f"🎉 **SUCCESS!**\n\nAapne **1 Coin** use karke **{bname}** successfully unlock kar liya hai.",
                    reply_markup=InlineKeyboardMarkup(kb),
                    parse_mode=ParseMode.MARKDOWN
                )

        elif data.startswith("req_access_"):
            cid = int(data.split("_")[2])
            if DB.get("PAID_LOCKED", False):
                return await q.answer("Sorry, but at this moment the paid batch is locked. When it will unlock I will inform you.", show_alert=True)
            if not await check_membership_pyro(uid, client):
                return await q.answer("  Join Main First!", show_alert=True)
            if await is_already_in_channel_pyro(client, cid, uid):
                return await q.answer("  Already joined!", show_alert=True)
            cooldown_left = get_cooldown_remaining(uid)
            if cooldown_left > 0:
                return await q.answer("  Please wait 15 minutes before requesting another link.", show_alert=True)
            if has_active_request(uid, cid):
                return await q.answer("  You already have an active request/link for this batch!", show_alert=True)
            await q.answer("  Generating Link...")

            try:
                bname = DB["ALL_CHATS"].get(cid, f"Batch {cid}")
                l = await client.create_chat_invite_link(
                    cid,
                    creates_join_request=True,
                    name=f"Req-{uid}",
                    expire_date=datetime.now() + timedelta(seconds=60),
                )
                DB.setdefault("LINK_MAP", {})[l.invite_link] = {"u": uid, "b": cid, "ts": time.time()}
                register_link_request(uid, cid)
                await save_data_async()

                topic_id = await get_or_create_topic(q.from_user, client)
                if topic_id:
                    user_link = f'<a href="tg://user?id={q.from_user.id}">{q.from_user.first_name or "User"}</a>'
                    notification_text = (
                        f"📩 <b>NEW REQUEST</b>\n"
                        f"👤 User: {user_link}\n"
                        f"📦 Batch: <b>{bname}</b>\n"
                        f"🔗 Link: {l.invite_link}\n\n"
                        f"⚡ <b>Action:</b>\n"
                        f"/demo {l.invite_link}\n"
                        f"/per {l.invite_link}"
                    )
                    try:
                        await client.send_message(
                            int(SUPPORT_GROUP_ID),
                            notification_text,
                            message_thread_id=topic_id,
                            parse_mode=ParseMode.HTML,
                        )
                    except TypeError:
                        await client.send_message(
                            int(SUPPORT_GROUP_ID),
                            notification_text,
                            reply_to_message_id=topic_id,
                            parse_mode=ParseMode.HTML,
                        )
                    except Exception as e:
                        logger.error(f"Admin notification failed: {e}")

                kb = [[InlineKeyboardButton("🔑 Request Access", url=l.invite_link)]]
                user_msg_obj = await client.send_message(
                    uid,
                    f"  <b>Access Link Generated!</b>\n\n<b>{bname}</b>\n\n  <b>Sent to Admin.</b> Wait for approval.\n  <i>(Expires in 1 min)</i>",
                    reply_markup=InlineKeyboardMarkup(kb),
                    parse_mode=ParseMode.HTML,
                )
                await schedule_delete(client, user_msg_obj, delay=60)
            except Exception as e:
                await client.send_message(uid, f"  Error: {e}")
    except Exception as e:
        logger.error(f"Callback Error: {e}")

# --- START, MENUS & CORE EVENTS ---
async def show_tnc_menu(client: Client, message: Message):
    import richpyro as rp
    tnc_msg = rp.message(
        rp.para("⚠️ ", rp.bold("STRICT WARNING & TERMS OF SERVICE")),
        rp.divider(),
        rp.para("🇬🇧 ", rp.bold("ENGLISH:")),
        rp.para("If you leave the Main Channel or block this bot, you will be ", rp.bold("INSTANTLY REMOVED"), " from ALL joined groups and channels."),
        rp.divider(),
        rp.para("🇮🇳 ", rp.bold("HINDI:")),
        rp.para("Agar aapne Main Channel ko chhoda (leave kiya) ya is bot ko block kiya, toh aapko sabhi groups aur channels se ", rp.bold("TURANT NIKAL"), " diya jayega."),
        rp.divider(),
        rp.para("✅ ", rp.italic("Click 'I Read & Accept' only if you agree to these terms.")),
        rp.buttons(rp.btn("✅ I Read & Accept", "accept_tnc", style=rp.Style.SUCCESS))
    )
    await rp.send(client, message.chat.id, tnc_msg)

async def show_tnc_menu_cb(client: Client, q: CallbackQuery):
    import richpyro as rp
    tnc_msg = rp.message(
        rp.para("⚠️ ", rp.bold("STRICT WARNING & TERMS OF SERVICE")),
        rp.divider(),
        rp.para("🇬🇧 ", rp.bold("ENGLISH:")),
        rp.para("If you leave the Main Channel or block this bot, you will be ", rp.bold("INSTANTLY REMOVED"), " from ALL joined groups and channels."),
        rp.divider(),
        rp.para("🇮🇳 ", rp.bold("HINDI:")),
        rp.para("Agar aapne Main Channel ko chhoda (leave kiya) ya is bot ko block kiya, toh aapko sabhi groups aur channels se ", rp.bold("TURANT NIKAL"), " diya jayega."),
        rp.divider(),
        rp.para("✅ ", rp.italic("Click 'I Read & Accept' only if you agree to these terms.")),
        rp.buttons(rp.btn("✅ I Read & Accept", "accept_tnc", style=rp.Style.SUCCESS))
    )
    await rp.edit(client, q.message.chat.id, q.message.id, tnc_msg)

def build_home_menu(user_key, user):
    """Normal users: standard half-width layout. VIPs: personalized greeting + exclusive full-width buttons."""
    
    # Check if user came from a Batch Share Link
    pending_batch = DB["USER_DATA"].get(user_key, {}).get("pending_batch")
    if pending_batch:
        bname = DB.get("ALL_CHATS", {}).get(int(pending_batch)) or DB.get("ALL_CHATS", {}).get(str(pending_batch)) or "Shared Batch"
        
        return rp.message(
            rp.heading("🎉 Invitation Received!"),
            rp.divider(),
            rp.para(
                "📌 ", rp.bold("Invitation Details:\n"),
                "➤ You have been invited by a friend to join this exclusive batch.\n",
                "➤ Click below to view details and join instantly."
            ),
            rp.buttons(
                rp.btn("🚀 Open Shared Batch", f"open_batch_{pending_batch}", style=rp.Style.SUCCESS),
                rp.btn("🏠 Main Menu", "clear_pending_batch", style=rp.Style.DANGER)
            )
        )

    vip = DB["USER_DATA"].get(user_key, {}).get("tier") == "vip"
    first_name = (user.first_name if user and user.first_name else "there")

    if vip:
        total_inv = DB["USER_DATA"].get(user_key, {}).get("total_invited", 0)
        pts = DB["USER_DATA"].get(user_key, {}).get("referral_count", 0)
        streak = DB["USER_DATA"].get(user_key, {}).get("streak", 0)
        return rp.message(
            rp.heading("👑 Elite VIP Dashboard"),
            rp.para("Welcome back, ", rp.bold(first_name), "!"),
            rp.divider(),
            rp.table(
                rows=[
                    [rp.table_cell("Wallet Balance", is_header=True), rp.table_cell("Total Referrals", is_header=True), rp.table_cell("Daily Streak", is_header=True)],
                    [rp.table_cell(f"💎 {pts} Coins"), rp.table_cell(f"👥 {total_inv}"), rp.table_cell(f"🔥 {streak} Days")]
                ],
                bordered=True, striped=True
            ),
            rp.expandable_quote([
                rp.bold("📌 VIP Privilege Pointers:\n\n"),
                rp.bold("• "), "📚 ", rp.bold("My Batches"), " — ", rp.italic("Aapke sabhi VIP aur unlocked batches\n\n"),
                rp.bold("• "), "🌐 ", rp.bold("All Batches"), " — ", rp.italic("Complete batches & engineering catalog\n\n"),
                rp.bold("• "), "🔥 ", rp.bold("Daily Streak"), " — ", rp.italic("Roz login karke bonus points claim karein\n\n"),
                rp.bold("• "), "🏆 ", rp.bold("Leaderboard"), " — ", rp.italic("Top students aur referral champions ranking\n\n"),
                rp.bold("• "), "💎 ", rp.bold("VIP Materials"), " — ", rp.italic("Exclusive study notes aur lectures\n\n"),
                rp.bold("• "), "🎁 ", rp.bold("Monthly Bonus"), " — ", rp.italic("Har mahine VIP bonus rewards\n\n"),
                rp.bold("• "), "🚀 ", rp.bold("Refer & Earn"), " — ", rp.italic("Friends ko invite karein aur free coins earn karein\n\n"),
                rp.bold("• "), "🎟️ ", rp.bold("Redeem Voucher"), " — ", rp.italic("Promo codes se free coins activate karein\n\n"),
                rp.bold("• "), "🙋 ", rp.bold("Request Batch"), " — ", rp.italic("Naye batch ki demand direct submit karein")
            ]),
            rp.buttons(
                rp.btn("👑 My Batches", "my_batches_0", style=rp.Style.SUCCESS),
                rp.btn("🌟 All Batches", "all_batches_0", style=rp.Style.DEFAULT)
            ),
            rp.buttons(
                rp.btn("🔥 Daily Streak", "claim_daily_streak", style=rp.Style.SUCCESS),
                rp.btn("🚀 Refer & Earn", "menu_refer", style=rp.Style.SUCCESS)
            ),
            rp.buttons(
                rp.btn("🏆 Leaderboard", "show_leaderboard", style=rp.Style.DEFAULT),
                rp.btn("🎟️ Redeem Code", "redeem_voucher_btn", style=rp.Style.DEFAULT)
            ),
            rp.buttons(
                rp.btn("💎 VIP Materials", "vip_materials", style=rp.Style.DEFAULT),
                rp.btn("🎁 Monthly Bonus", "vip_monthly_bonus", style=rp.Style.DEFAULT)
            ),
            rp.buttons(
                rp.btn("🙋 Request Batch", "req_batch_btn", style=rp.Style.DEFAULT),
                rp.btn("🤖 Test Bot", "test_bot", style=rp.Style.DEFAULT)
            ),
            rp.buttons(
                rp.btn("ℹ️ My Profile", "my_info", style=rp.Style.DEFAULT),
                rp.url_btn("📢 Updates", BATCH_UPDATE_CHANNEL_LINK, style=rp.Style.LINK)
            ),
            rp.buttons(
                rp.url_btn("🎥 Video Tutorial", "https://t.me/c/2836314734/1244", style=rp.Style.LINK)
            )
        )
    else:
        streak = DB["USER_DATA"].get(user_key, {}).get("streak", 0)
        return rp.message(
            rp.heading("🌟 Premium Study Hub"),
            rp.para("Welcome, ", rp.bold(first_name), "! Niche diye gaye options se apna section chunein:"),
            rp.divider(),
            rp.expandable_quote([
                rp.bold("📌 Quick Feature Pointers:\n\n"),
                rp.bold("• "), "📚 ", rp.bold("My Batches"), " — ", rp.italic("Aapke unlocked aur enrolled study courses\n\n"),
                rp.bold("• "), "🌐 ", rp.bold("All Batches"), " — ", rp.italic("Complete batches & engineering catalog\n\n"),
                rp.bold("• "), "🔥 ", rp.bold("Daily Streak"), " — ", rp.italic("Rozana login karke free coins claim karein\n\n"),
                rp.bold("• "), "🏆 ", rp.bold("Leaderboard"), " — ", rp.italic("Top students & referral rank dekhein\n\n"),
                rp.bold("• "), "🎁 ", rp.bold("Refer & Earn"), " — ", rp.italic("Dosto ko invite karein aur free courses payein\n\n"),
                rp.bold("• "), "🎟️ ", rp.bold("Redeem Voucher"), " — ", rp.italic("Promo codes se free coins activate karein\n\n"),
                rp.bold("• "), "🙋 ", rp.bold("Request Batch"), " — ", rp.italic("Apne manpasand batch ki request dalein\n\n"),
                rp.bold("• "), "🤖 ", rp.bold("Test Bot"), " — ", rp.italic("Practice test series solve karein\n\n"),
                rp.bold("• "), "ℹ️ ", rp.bold("Profile"), " — ", rp.italic("Wallet balance, streak & account details")
            ]),
            rp.buttons(
                rp.btn("📚 My Batches", "my_batches_0", style=rp.Style.SUCCESS),
                rp.btn("🌐 All Batches", "all_batches_0", style=rp.Style.DEFAULT)
            ),
            rp.buttons(
                rp.btn("🔥 Daily Streak", "claim_daily_streak", style=rp.Style.SUCCESS),
                rp.btn("🎁 Refer & Earn", "menu_refer", style=rp.Style.SUCCESS)
            ),
            rp.buttons(
                rp.btn("🏆 Leaderboard", "show_leaderboard", style=rp.Style.DEFAULT),
                rp.btn("🎟️ Redeem Code", "redeem_voucher_btn", style=rp.Style.DEFAULT)
            ),
            rp.buttons(
                rp.btn("🙋 Request Batch", "req_batch_btn", style=rp.Style.DEFAULT),
                rp.btn("🤖 Test Bot", "test_bot", style=rp.Style.DEFAULT)
            ),
            rp.buttons(
                rp.btn("ℹ️ My Profile", "my_info", style=rp.Style.DEFAULT),
                rp.url_btn("📢 Updates", BATCH_UPDATE_CHANNEL_LINK, style=rp.Style.LINK)
            ),
            rp.buttons(
                rp.url_btn("🎥 Video Tutorial", "https://t.me/c/2836314734/1244", style=rp.Style.LINK)
            )
        )

async def show_user_menu(client: Client, message: Message):
    user_key = message.from_user.id if message.from_user.id in DB["USER_DATA"] else str(message.from_user.id)
    msg = build_home_menu(user_key, message.from_user)
    await rp.send(client, message.chat.id, msg)

async def show_user_menu_cb(client: Client, q: CallbackQuery):
    user_key = q.from_user.id if q.from_user.id in DB["USER_DATA"] else str(q.from_user.id)
    msg = build_home_menu(user_key, q.from_user)
    await rp.edit(client, q.message.chat.id, q.message.id, msg)

# =====================================================================
# PANELS & MENUS (Richpyro Single Column Grid + Expandable Blockquotes)
# =====================================================================

def build_security_panel_msg():
    new_users = DB.get('NEW_USERS_ALLOWED', True)
    free_locked = DB.get('FREE_LOCKED', False)
    paid_locked = DB.get('PAID_LOCKED', False)
    testbot_locked = DB.get('TEST_BOT_LOCKED', False)
    maint_mode = DB.get('MAINTENANCE_MODE', False)

    lockdown_btn = rp.btn(
        f"🔐 Lockdown: {'🔴 ON' if not new_users else '🟢 OFF'}",
        "toggle_lockdown",
        style=rp.Style.DANGER if not new_users else rp.Style.SUCCESS
    )
    free_btn = rp.btn(
        f"🆓 Free: {'🔴 LOCK' if free_locked else '🟢 OPEN'}",
        "toggle_free",
        style=rp.Style.DANGER if free_locked else rp.Style.SUCCESS
    )
    paid_btn = rp.btn(
        f"💰 Paid: {'🔴 LOCK' if paid_locked else '🟢 OPEN'}",
        "toggle_paid",
        style=rp.Style.DANGER if paid_locked else rp.Style.SUCCESS
    )
    testbot_btn = rp.btn(
        f"🤖 Test Bot: {'🔴 LOCK' if testbot_locked else '🟢 OPEN'}",
        "toggle_testbot",
        style=rp.Style.DANGER if testbot_locked else rp.Style.SUCCESS
    )
    maint_btn = rp.btn(
        f"🛠️ Maint: {'🔴 ON' if maint_mode else '🟢 OFF'}",
        "toggle_maintenance",
        style=rp.Style.DANGER if maint_mode else rp.Style.SUCCESS
    )

    return rp.message(
        rp.heading("🔒 Security & Access Control"),
        rp.divider(),
        rp.expandable_quote([
            rp.bold("📌 Security Controls Pointers:\n\n"),
            rp.bold("• "), "🔒 ", rp.bold("Lockdown"), " — ", rp.italic("Naye users ko bot me aane se rokein ya allow karein\n\n"),
            rp.bold("• "), "🆓 ", rp.bold("Free Batches"), " — ", rp.italic("Free batches section ko instant lock/unlock karein\n\n"),
            rp.bold("• "), "💰 ", rp.bold("Paid Batches"), " — ", rp.italic("Paid courses catalog ko lock/unlock karein\n\n"),
            rp.bold("• "), "🤖 ", rp.bold("Test Bot"), " — ", rp.italic("Practice test bot access toggle karein\n\n"),
            rp.bold("• "), "🛠️ ", rp.bold("Maintenance"), " — ", rp.italic("Server update ke waqt users ko temporarily block karein")
        ]),
        rp.buttons(lockdown_btn, free_btn),
        rp.buttons(paid_btn, testbot_btn),
        rp.buttons(maint_btn),
        rp.buttons(rp.btn("🔙 Back to Terminal", "dash_home", style=rp.Style.DANGER))
    )

def build_database_panel_msg():
    return rp.message(
        rp.heading("💾 Database Management Tools"),
        rp.divider(),
        rp.expandable_quote([
            rp.bold("📌 Database Tools Pointers:\n\n"),
            rp.bold("• "), "📥 ", rp.bold("Download Backup"), " — ", rp.italic("Bot database ka poora JSON backup download karein\n\n"),
            rp.bold("• "), "🔄 ", rp.bold("Run Sync"), " — ", rp.italic("Storage aur MongoDB database sync karein\n\n"),
            rp.bold("• "), "👥 ", rp.bold("All Users List"), " — ", rp.italic("Sabhi registered users ki list export karein\n\n"),
            rp.bold("• "), "🔍 ", rp.bold("Specific User Data"), " — ", rp.italic("User ID ya username se complete details dekhein\n\n"),
            rp.bold("• "), "🎟️ ", rp.bold("Generate Redeem Code"), " — ", rp.italic("Referral coins/points ke naye promo voucher banayein\n\n"),
            rp.bold("• "), "📋 ", rp.bold("Active Vouchers"), " — ", rp.italic("Sabhi active redeem codes ki list dekhein\n\n"),
            rp.bold("• "), "🎁 ", rp.bold("Gift Coin"), " — ", rp.italic("User ke wallet me free coins credit karein\n\n"),
            rp.bold("• "), "🚫 ", rp.bold("Ban / Unban"), " — ", rp.italic("User access control manage karein\n\n"),
            rp.bold("• "), "📥 ", rp.bold("Restore Old Batches"), " — ", rp.italic("Old backup JSON se missing batches aur bot rights restore karein\n\n"),
            rp.bold("• "), "☢️ ", rp.bold("Hard Delete User"), " — ", rp.italic("User ka saara record database se delete karein")
        ]),
        rp.buttons(
            rp.btn("📥 Download Backup", "act_backup", style=rp.Style.DEFAULT),
            rp.btn("🔄 Run Sync", "act_sync", style=rp.Style.DEFAULT)
        ),
        rp.buttons(
            rp.btn("👥 All Users List", "act_allusers", style=rp.Style.DEFAULT),
            rp.btn("🔍 User Lookup", "input_userlookup", style=rp.Style.DEFAULT)
        ),
        rp.buttons(
            rp.btn("🎟️ Gen Voucher", "input_genvoucher", style=rp.Style.DEFAULT),
            rp.btn("📋 Active Vouchers", "act_vouchers", style=rp.Style.DEFAULT)
        ),
        rp.buttons(
            rp.btn("🎁 Gift Coin", "giftcoin_start", style=rp.Style.DEFAULT),
            rp.btn("☢️ Hard Delete", "input_deluser", style=rp.Style.DANGER)
        ),
        rp.buttons(
            rp.btn("🚫 Ban User", "input_ban", style=rp.Style.DANGER),
            rp.btn("✅ Unban User", "input_unban", style=rp.Style.SUCCESS)
        ),
        rp.buttons(
            rp.btn("📥 Restore Batches", "act_restorebatches", style=rp.Style.DEFAULT),
            rp.btn("📋 Demands Tracker", "view_batch_requests", style=rp.Style.SUCCESS)
        ),
        rp.buttons(rp.btn("🔙 Back to Terminal", "dash_home", style=rp.Style.DANGER))
    )

def build_batches_panel_msg(back_cb="dash_home"):
    back_text = "🔙 Back to Admin Panel" if back_cb == "goto_admin_panel" else "🔙 Back to Terminal"
    return rp.message(
        rp.heading("📦 Batches Management"),
        rp.divider(),
        rp.expandable_quote([
            rp.bold("📌 Batches Tools Pointers:\n\n"),
            rp.bold("• "), "➕ ", rp.bold("Add Batch"), " — ", rp.italic("Naya Free/Paid/Special batch link add karein\n\n"),
            rp.bold("• "), "🗑️ ", rp.bold("Delete Batch"), " — ", rp.italic("Existing batch catalog se remove karein\n\n"),
            rp.bold("• "), "📁 ", rp.bold("Categories"), " — ", rp.italic("Nayi category banayein ya remove karein\n\n"),
            rp.bold("• "), "🏷️ ", rp.bold("Set Category"), " — ", rp.italic("Batches ko unki specific category me assign karein\n\n"),
            rp.bold("• "), "🧹 ", rp.bold("Empty Batch"), " — ", rp.italic("Batch se un-approved users ko mass-kick karein\n\n"),
            rp.bold("• "), "📢 ", rp.bold("Post Update"), " — ", rp.italic("Batch channel me latest update notice post karein\n\n"),
            rp.bold("• "), "🚀 ", rp.bold("Super Forwarder"), " — ", rp.italic("Messages & lectures channel se auto copy karein\n\n"),
            rp.bold("• "), "🛡️ ", rp.bold("Clean Unverified"), " — ", rp.italic("Non-members ko batch se remove karein\n\n"),
            rp.bold("• "), "📝 ", rp.bold("Caption Changer"), " — ", rp.italic("Files ke captions automatically edit karein\n\n"),
            rp.bold("• "), "📊 ", rp.bold("Batch Stats"), " — ", rp.italic("Kisi batch ke total members aur active users dekhein")
        ]),
        rp.buttons(
            rp.btn("➕ Add Batch", "act_addbatch", style=rp.Style.SUCCESS),
            rp.btn("🗑️ Delete Batch", "input_delbatch", style=rp.Style.DANGER)
        ),
        rp.buttons(
            rp.btn("📁➕ Add Category", "input_addcat", style=rp.Style.DEFAULT),
            rp.btn("📁🗑️ Del Category", "act_delcat", style=rp.Style.DEFAULT)
        ),
        rp.buttons(
            rp.btn("🏷️ Set Category", "input_setcat", style=rp.Style.DEFAULT),
            rp.btn("🧹 Empty Batch", "input_emptybatch", style=rp.Style.DEFAULT)
        ),
        rp.buttons(
            rp.btn("📢 Post Update", "input_updatepost", style=rp.Style.DEFAULT),
            rp.btn("🚀 Super Forwarder", "input_superfwd", style=rp.Style.DEFAULT)
        ),
        rp.buttons(
            rp.btn("🛡️ Clean Unverified", "input_cleanbatch", style=rp.Style.DEFAULT),
            rp.btn("📝 Caption Changer", "input_advcap", style=rp.Style.DEFAULT)
        ),
        rp.buttons(
            rp.btn("📊 Batch Stats", "act_batchstats", style=rp.Style.DEFAULT),
            rp.btn("📋 Demands Checklist", "view_batch_requests", style=rp.Style.SUCCESS)
        ),
        rp.buttons(rp.btn(back_text, back_cb, style=rp.Style.DANGER))
    )

def build_staff_panel_msg(back_cb="dash_home"):
    back_text = "🔙 Back to Admin Panel" if back_cb == "goto_admin_panel" else "🔙 Back to Terminal"
    return rp.message(
        rp.heading("🧑‍💼 Staff & Admin Management"),
        rp.divider(),
        rp.expandable_quote([
            rp.bold("📌 Staff Tools Pointers:\n\n"),
            rp.bold("• "), "➕ ", rp.bold("Add Admin"), " — ", rp.italic("Kisi user ko bot me Admin rights grant karein\n\n"),
            rp.bold("• "), "➖ ", rp.bold("Remove Admin"), " — ", rp.italic("Kisi user se Admin permissions revoke karein\n\n"),
            rp.bold("• "), "📋 ", rp.bold("Admin List"), " — ", rp.italic("Current active admins aur unki IDs ki list dekhein")
        ]),
        rp.buttons(
            rp.btn("➕ Add Admin", "input_addadmin", style=rp.Style.SUCCESS),
            rp.btn("➖ Remove Admin", "input_deladmin", style=rp.Style.DANGER)
        ),
        rp.buttons(
            rp.btn("📋 Admin List", "act_adminlist", style=rp.Style.DEFAULT)
        ),
        rp.buttons(rp.btn(back_text, back_cb, style=rp.Style.DANGER))
    )

def build_comms_panel_msg(back_cb="dash_home"):
    back_text = "🔙 Back to Admin Panel" if back_cb == "goto_admin_panel" else "🔙 Back to Terminal"
    return rp.message(
        rp.heading("📢 Communications & Messaging"),
        rp.divider(),
        rp.expandable_quote([
            rp.bold("📌 Communications Pointers:\n\n"),
            rp.bold("• "), "📢 ", rp.bold("Broadcast"), " — ", rp.italic("Bot ke sabhi users ko mass message/media bhejein\n\n"),
            rp.bold("• "), "📝 ", rp.bold("Post Message"), " — ", rp.italic("Kisi specific channel ya group me message bhejein\n\n"),
            rp.bold("• "), "🤖 ", rp.bold("Set Test Bot"), " — ", rp.italic("Practice Test bot ka direct link configure karein\n\n"),
            rp.bold("• "), "👋 ", rp.bold("Set Welcome"), " — ", rp.italic("Kisi batch channel ke liye custom welcome message set karein")
        ]),
        rp.buttons(
            rp.btn("📢 Broadcast to All", "act_broadcast", style=rp.Style.DEFAULT),
            rp.btn("📝 Post Message", "act_post", style=rp.Style.DEFAULT)
        ),
        rp.buttons(
            rp.btn("🤖 Set Test Bot Link", "input_settestbot", style=rp.Style.DEFAULT),
            rp.btn("👋 Set Welcome Msg", "input_setwelcome", style=rp.Style.DEFAULT)
        ),
        rp.buttons(rp.btn(back_text, back_cb, style=rp.Style.DANGER))
    )

def build_userbot_panel_msg(session, phone):
    if session:
        status_text = "🟢 Active & Ready"
        quote_text = [
            rp.bold("📌 Userbot Status & Tools:\n\n"),
            rp.bold("• "), "📞 ", rp.bold("Logged In Number: "), rp.code(phone), "\n\n",
            rp.bold("• "), "⚡ ", rp.bold("Ready Commands: "), rp.italic("Userbot /emptybatch, /clear, /joinall execute karne ke liye ready hai.")
        ]
        action_btn = rp.buttons(rp.btn("🚪 Logout (Delete Session)", "userbot_logout", style=rp.Style.DANGER))
    else:
        status_text = "🔴 Not Logged In"
        quote_text = [
            rp.bold("📌 Userbot Status:\n\n"),
            rp.bold("• "), "⚠️ ", rp.bold("No Session: "), rp.italic("Koi active userbot session nahi mila.\n\n"),
            rp.bold("• "), "🔑 ", rp.bold("Login Required: "), rp.italic("Userbot mass cleanup features use karne ke liye login karein.")
        ]
        action_btn = rp.buttons(rp.btn("🔑 Login Now", "input_userbotphone", style=rp.Style.SUCCESS))

    return rp.message(
        rp.heading("🤖 USERBOT CONTROL PANEL"),
        rp.para("Status: ", rp.bold(status_text)),
        rp.divider(),
        rp.expandable_quote(quote_text),
        action_btn,
        rp.buttons(rp.btn("🔙 Back to Terminal", "dash_home", style=rp.Style.DANGER))
    )

def build_admin_users_panel_msg(back_cb="goto_admin_panel"):
    back_text = "🔙 Back to Admin Panel" if back_cb == "goto_admin_panel" else "🔙 Back to Terminal"
    return rp.message(
        rp.heading("👥 User Management"),
        rp.divider(),
        rp.expandable_quote([
            rp.bold("📌 User Tools Pointers:\n\n"),
            rp.bold("• "), "🚫 ", rp.bold("Ban / Unban"), " — ", rp.italic("User access control manage karein\n\n"),
            rp.bold("• "), "👢 ", rp.bold("Kick User"), " — ", rp.italic("Batch se member ko remove karein\n\n"),
            rp.bold("• "), "🔍 ", rp.bold("Find User"), " — ", rp.italic("Username ya ID se lookup karein\n\n"),
            rp.bold("• "), "♻️ ", rp.bold("Reset Data"), " — ", rp.italic("User stats aur demo reset karein")
        ]),
        rp.buttons(
            rp.btn("🚫 Ban User", "input_ban", style=rp.Style.DANGER),
            rp.btn("✅ Unban User", "input_unban", style=rp.Style.SUCCESS)
        ),
        rp.buttons(
            rp.btn("👢 Kick User", "input_kick", style=rp.Style.DANGER),
            rp.btn("🔍 Find User", "input_find", style=rp.Style.DEFAULT)
        ),
        rp.buttons(
            rp.btn("♻️ Reset User Data", "input_resetuser", style=rp.Style.DEFAULT)
        ),
        rp.buttons(rp.btn(back_text, back_cb, style=rp.Style.DANGER))
    )

def build_admin_approvals_panel_msg(back_cb="goto_admin_panel"):
    back_text = "🔙 Back to Admin Panel" if back_cb == "goto_admin_panel" else "🔙 Back to Terminal"
    return rp.message(
        rp.heading("✅ Access Approvals"),
        rp.divider(),
        rp.expandable_quote([
            rp.bold("📌 Approvals Guide Pointers:\n\n"),
            rp.bold("• "), "🕐 ", rp.bold("Approve Demo"), " — ", rp.italic("Demo access grant karein\n\n"),
            rp.bold("• "), "✅ ", rp.bold("Approve Perm"), " — ", rp.italic("Permanent access approve karein\n\n"),
            rp.bold("• "), "⏳ ", rp.bold("Extend Demo"), " — ", rp.italic("User ke time limit ko extend karein\n\n"),
            rp.bold("• "), "🔗 ", rp.bold("Generate Link"), " — ", rp.italic("3-hour demo invite link banayein")
        ]),
        rp.buttons(
            rp.btn("🕐 Approve Demo", "input_demo", style=rp.Style.SUCCESS),
            rp.btn("✅ Approve Perm", "input_perm", style=rp.Style.SUCCESS)
        ),
        rp.buttons(
            rp.btn("⏳ Extend Demo", "input_extend", style=rp.Style.DEFAULT),
            rp.btn("🔗 Gen Demo Link", "input_gendemo", style=rp.Style.DEFAULT)
        ),
        rp.buttons(rp.btn(back_text, back_cb, style=rp.Style.DANGER))
    )

def build_commands_manual_msg():
    return rp.message(
        rp.heading("📖 Master Commands & Manual"),
        rp.para("Neeche sabhi hidden aur terminal commands diye gaye hain. Expand karke details dekhein:"),
        rp.divider(),
        rp.expandable_quote([
            rp.bold("🤖 Userbot & Cleanup Commands:\n\n"),
            rp.bold("• "), "🌐 ", rp.code("/joinall"), " — Userbot sabhi channels auto-join karein\n\n",
            rp.bold("• "), "🧹 ", rp.code("/emptybatch <chat_id>"), " — Batch se un-approved mass-kick karein\n\n",
            rp.bold("• "), "🗑️ ", rp.code("/clear <chat_id>"), " — Specific chat ke messages delete karein\n\n",
            rp.bold("• "), "❌ ", rp.code("/del <N>"), " — Pichle N messages delete karein (Ex: ", rp.code("/del 10"), ")"
        ]),
        rp.expandable_quote([
            rp.bold("🔑 Access & Approvals Commands:\n\n"),
            rp.bold("• "), "⏳ ", rp.code("/demo <link> <time>"), " — Temporary demo approve karein\n\n",
            rp.bold("• "), "✅ ", rp.code("/perm <link>"), " — Permanent access approve karein\n\n",
            rp.bold("• "), "🔗 ", rp.code("/gendemo <uid> <bid>"), " — 3-hour demo invite link banayein\n\n",
            rp.bold("• "), "⏱️ ", rp.code("/extend <uid> <bid> <hours>"), " — Active demo extend karein\n\n",
            rp.bold("• "), "👢 ", rp.code("/kick <uid> <bid>"), " — User ko channel se remove karein"
        ]),
        rp.expandable_quote([
            rp.bold("💎 Batch & Config Commands:\n\n"),
            rp.bold("• "), "💰 ", rp.code("/setprice <bid> <price>"), " — Paid Batch price update karein\n\n",
            rp.bold("• "), "📝 ", rp.code("/setrequestnote <msg>"), " — Batch demand note customize karein\n\n",
            rp.bold("• "), "👋 ", rp.code("/setwelcome <bid> <msg>"), " — Custom welcome message set karein\n\n",
            rp.bold("• "), "🤖 ", rp.code("/settestbot <link>"), " — Test bot link update karein"
        ]),
        rp.expandable_quote([
            rp.bold("💾 Database & Promo Commands:\n\n"),
            rp.bold("• "), "🎟️ ", rp.code("/genvoucher <CODE> <PTS> <USES>"), " — Promo voucher banayein\n\n",
            rp.bold("• "), "📋 ", rp.code("/vouchers"), " — Active promo vouchers ki list dekhein\n\n",
            rp.bold("• "), "🙋 ", rp.code("/batchrequests"), " — Users ki batch demands dekhein\n\n",
            rp.bold("• "), "💾 ", rp.code("/backup"), " / ", rp.code("/sync"), " / ", rp.code("/allusers")
        ]),
        rp.buttons(rp.btn("🔙 Back to Terminal", "dash_home", style=rp.Style.DANGER))
    )

def build_owner_panel_kb():
    return rp.message(
        rp.heading("👑 SYSTEM MASTER TERMINAL"),
        rp.divider(),
        rp.expandable_quote([
            rp.bold("📌 System Master Modules Pointers:\n\n"),
            rp.bold("• "), "🔒 ", rp.bold("Security"), " — ", rp.italic("Lockdown, locks, maintenance mode\n\n"),
            rp.bold("• "), "💾 ", rp.bold("Database"), " — ", rp.italic("Backup, users, coins, vouchers\n\n"),
            rp.bold("• "), "📦 ", rp.bold("Batches"), " — ", rp.italic("Add/delete batches & categories\n\n"),
            rp.bold("• "), "🧑‍💼 ", rp.bold("Staff"), " — ", rp.italic("Admin permissions grant/revoke & staff list\n\n"),
            rp.bold("• "), "📢 ", rp.bold("Comms"), " — ", rp.italic("Global broadcast & batch notifications\n\n"),
            rp.bold("• "), "📊 ", rp.bold("Analytics"), " — ", rp.italic("Real-time performance & stats\n\n"),
            rp.bold("• "), "🤖 ", rp.bold("Userbot"), " — ", rp.italic("String session manager & status\n\n"),
            rp.bold("• "), "📖 ", rp.bold("Commands"), " — ", rp.italic("Hidden commands manual")
        ]),
        rp.buttons(
            rp.btn("🔒 Security & Locks", "dash_locks", style=rp.Style.DEFAULT),
            rp.btn("💾 Database Tools", "dash_db", style=rp.Style.DEFAULT)
        ),
        rp.buttons(
            rp.btn("📦 Batches & Cats", "dash_batches", style=rp.Style.DEFAULT),
            rp.btn("🧑‍💼 Staff & Admins", "dash_staff", style=rp.Style.DEFAULT)
        ),
        rp.buttons(
            rp.btn("📢 Communications", "dash_comms", style=rp.Style.DEFAULT),
            rp.btn("📊 Analytics & Stats", "dash_stats", style=rp.Style.DEFAULT)
        ),
        rp.buttons(
            rp.btn("🤖 Userbot Login", "userbot_details", style=rp.Style.DEFAULT),
            rp.btn("📖 All Commands", "dash_commands", style=rp.Style.DEFAULT)
        ),
        rp.buttons(
            rp.btn("🔄 Switch Panel", "role_selector", style=rp.Style.SUCCESS)
        )
    )

def build_admin_panel_kb():
    return rp.message(
        rp.heading("🛡 ADMINISTRATOR DASHBOARD"),
        rp.divider(),
        rp.expandable_quote([
            rp.bold("📌 Admin Tools Pointers:\n\n"),
            rp.bold("• "), "👥 ", rp.bold("Users"), " — ", rp.italic("Ban, Unban, Kick, Find & Reset user data\n\n"),
            rp.bold("• "), "✅ ", rp.bold("Approvals"), " — ", rp.italic("Demo aur Permanent access request approval\n\n"),
            rp.bold("• "), "📦 ", rp.bold("Batches"), " — ", rp.italic("Add/Delete batches & categories\n\n"),
            rp.bold("• "), "📢 ", rp.bold("Comms"), " — ", rp.italic("Broadcast announcements & custom posts")
        ]),
        rp.buttons(
            rp.btn("👥 Users Management", "adash_users", style=rp.Style.DEFAULT),
            rp.btn("✅ Approvals Center", "adash_approvals", style=rp.Style.DEFAULT)
        ),
        rp.buttons(
            rp.btn("📦 Batches Management", "adash_batches", style=rp.Style.DEFAULT),
            rp.btn("📢 Communications", "adash_comms", style=rp.Style.DEFAULT)
        ),
        rp.buttons(
            rp.btn("🔄 Switch Panel", "role_selector", style=rp.Style.SUCCESS)
        )
    )

def build_role_selector_kb(user_id):
    is_owner = str(user_id) == str(OWNER_ID)
    owner_btn = []
    if is_owner:
        owner_btn = [rp.buttons(rp.btn("👑 Owner Panel", "goto_owner_panel", style=rp.Style.DEFAULT))]
        
    return rp.message(
        rp.heading("🎛 Select Panel"),
        rp.para(rp.bold("Aap kis panel me jaana chahte hain?")),
        rp.divider(),
        *owner_btn,
        rp.buttons(
            rp.btn("🛡 Admin Panel", "goto_admin_panel", style=rp.Style.DEFAULT),
            rp.btn("👤 User Panel", "goto_user_panel", style=rp.Style.DEFAULT)
        )
    )

async def show_role_selector(client: Client, message: Message, user):
    msg = build_role_selector_kb(user.id)
    await client.send_rich_message(message.chat.id, rich_message=msg)

async def show_role_selector_cb(client: Client, q: CallbackQuery):
    msg = build_role_selector_kb(q.from_user.id)
    await rp.edit(client, q.message.chat.id, q.message.id, msg)

async def goto_owner_panel_cb(client: Client, q: CallbackQuery):
    if str(q.from_user.id) != str(OWNER_ID):
        return await q.answer("  Access Denied! Owner Only.", show_alert=True)
    await q.answer()
    msg = build_owner_panel_kb()
    await rp.edit(client, q.message.chat.id, q.message.id, msg)

async def goto_admin_panel_cb(client: Client, q: CallbackQuery):
    if not is_admin(q.from_user.id):
        return await q.answer("  Access Denied! Admins Only.", show_alert=True)
    await q.answer()
    msg = build_admin_panel_kb()
    await rp.edit(client, q.message.chat.id, q.message.id, msg)

async def goto_user_panel_cb(client: Client, q: CallbackQuery):
    await q.answer()
    await show_user_menu_cb(client, q)

async def start(client: Client, message: Message):
    user = message.from_user
    await safe_reaction(client, message.chat.id, message.id, "🎉")
    await set_role_based_commands(user.id, client)
    
    user_key = user.id if user.id in DB.get("USER_DATA", {}) else (str(user.id) if str(user.id) in DB.get("USER_DATA", {}) else user.id)
    
    # STRICT NEW USER CHECK FLAG
    is_new_user = False
    
    if user_key not in DB["USER_DATA"]:
        is_new_user = True
        DB["USER_DATA"][user_key] = {
            "name": f"{user.first_name or ''} {user.last_name or ''}".strip(),
            "username": user.username,
            "joined_at": time.time(),
            "demos": {},
            "tnc_accepted": False,
            "unlocked_batches": [],
            "referral_count": 0,
            "total_invited": 0
        }
        await save_data_async()

    DB["USER_DATA"][user_key].setdefault("unlocked_batches", [])

    # --- CHECK REFERRAL DEEP LINK PARAMETER ---
    args = get_args(message)
    target_batch_to_open = None
    
    if args:
        param = args[0].strip()
        referrer_id = None
        
        # Case A: Batch Share Link (batch_CID_UID)
        if param.startswith("batch_"):
            parts = param.split("_")
            if len(parts) >= 3:
                target_batch_to_open = parts[1]
                if parts[2].isdigit():
                    referrer_id = int(parts[2])
            elif len(parts) == 2 and parts[1].lstrip("-").isdigit():
                target_batch_to_open = parts[1]
                
        # Case B: Standard Referral
        elif param.isdigit():
            referrer_id = int(param)
        elif param.startswith("ref_"):
            parts = param.split("_")
            if len(parts) >= 2 and parts[-1].isdigit():
                referrer_id = int(parts[-1])
                
        # Save pending referral if it's a new user
        if is_new_user and referrer_id and str(referrer_id) != str(user.id):
            DB["USER_DATA"][user_key]["pending_referral"] = referrer_id
            
        # Save pending batch to show immediately after mandatory checks
        if target_batch_to_open:
            DB["USER_DATA"][user_key]["pending_batch"] = target_batch_to_open
            
        await save_data_async()

    await get_or_create_topic(user, client)

    import richpyro as rp
    # --- LOADING ANIMATION ---
    loading_msg = await rp.send(
        client, 
        message.chat.id, 
        rp.message(rp.para("⏳ ", rp.italic("Loading, please wait...")))
    )
    await asyncio.sleep(0.7)

    if str(user.id) == str(OWNER_ID) or is_admin(user.id):
        # Owner ko 3 panel (Owner/Admin/User) aur Admin ko 2 panel (Admin/User) dikhega
        msg = build_role_selector_kb(user.id)
        await rp.edit(client, loading_msg.chat.id, loading_msg.id, msg)
    elif await check_membership_pyro(user.id, client):
        if not DB["USER_DATA"].get(user_key, {}).get("tnc_accepted", False):
            tnc_msg = rp.message(
                rp.para("⚠️ ", rp.bold("STRICT WARNING & TERMS OF SERVICE")),
                rp.divider(),
                rp.para("🇬🇧 ", rp.bold("ENGLISH:")),
                rp.para("If you leave the Main Channel or block this bot, you will be ", rp.bold("INSTANTLY REMOVED"), " from ALL joined groups and channels."),
                rp.divider(),
                rp.para("🇮🇳 ", rp.bold("HINDI:")),
                rp.para("Agar aapne Main Channel ko chhoda (leave kiya) ya is bot ko block kiya, toh aapko sabhi groups aur channels se ", rp.bold("TURANT NIKAL"), " diya jayega."),
                rp.divider(),
                rp.para("✅ ", rp.italic("Click 'I Read & Accept' only if you agree to these terms.")),
                rp.buttons(rp.btn("✅ I Read & Accept", "accept_tnc", style=rp.Style.SUCCESS))
            )
            await rp.edit(client, message.chat.id, loading_msg.id, tnc_msg)
        else:
            if "pending_referral" in DB["USER_DATA"][user_key]:
                referrer_id = DB["USER_DATA"][user_key].pop("pending_referral")
                await save_data_async()
                await process_successful_referral(client, user.id, referrer_id)

            msg = build_home_menu(user_key, user)
            await rp.edit(client, loading_msg.chat.id, loading_msg.id, msg)
    else:
        if not DB.get("NEW_USERS_ALLOWED", True):
            return await loading_msg.edit_text("🚫 **Entry Closed!**", parse_mode=ParseMode.MARKDOWN)
        kb = [
            [InlineKeyboardButton("📢 Join Channel", url=MANDATORY_CHANNEL_LINK)],
            [InlineKeyboardButton("✅ I've Joined", callback_data="verify")],
        ]
        await loading_msg.edit_text(
            "📢 **Join Main Channel First / पहले Channel Join Karein**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "🇬🇧 **1️⃣** Tap **📢 Join Channel** below and join it.\n"
            "🇮🇳 **1️⃣** Neeche **📢 Join Channel** par tap karke channel join karein.\n\n"
            "🇬🇧 **2️⃣** Come back here and tap **✅ I've Joined** to unlock the bot.\n"
            "🇮🇳 **2️⃣** Wapas yahan aakar **✅ I've Joined** par tap karein aur bot unlock karein.\n\n"
            "💡 *This unlocks batches, free demos, and referral rewards.*",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode=ParseMode.MARKDOWN,
        )

async def start_from_cb(client: Client, q: CallbackQuery):
    user = q.from_user
    user_key = user.id if user.id in DB.get("USER_DATA", {}) else (str(user.id) if str(user.id) in DB.get("USER_DATA", {}) else user.id)
    await set_role_based_commands(user.id, client)
    
    if str(user.id) == str(OWNER_ID):
        msg = build_owner_panel_kb()
        await rp.edit(client, q.message.chat.id, q.message.id, msg)
    elif is_admin(user.id):
        msg = build_admin_panel_kb()
        await rp.edit(client, q.message.chat.id, q.message.id, msg)
    elif await check_membership_pyro(user.id, client):
        if not DB["USER_DATA"].get(user_key, {}).get("tnc_accepted", False):
            await show_tnc_menu_cb(client, q)
        else:
            if "pending_referral" in DB["USER_DATA"].get(user_key, {}):
                referrer_id = DB["USER_DATA"][user_key].pop("pending_referral")
                await save_data_async()
                await process_successful_referral(client, user.id, referrer_id)
            await show_user_menu_cb(client, q)
    else:
        if not DB.get("NEW_USERS_ALLOWED", True):
            return await q.edit_message_text("🚫 **Entry Closed!**", parse_mode=ParseMode.MARKDOWN)
        kb = [
            [InlineKeyboardButton("📢 Join Channel", url=MANDATORY_CHANNEL_LINK)],
            [InlineKeyboardButton("✅ I've Joined", callback_data="verify")],
        ]
        await q.edit_message_text(
            "📢 **Join Main Channel First / पहले Channel Join Karein**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "🇬🇧 **1️⃣** Tap **📢 Join Channel** below and join it.\n"
            "🇮🇳 **1️⃣** Neeche **📢 Join Channel** par tap karke channel join karein.\n\n"
            "🇬🇧 **2️⃣** Come back here and tap **✅ I've Joined** to unlock the bot.\n"
            "🇮🇳 **2️⃣** Wapas yahan aakar **✅ I've Joined** par tap karein aur bot unlock karein.\n\n"
            "💡 *This unlocks batches, free demos, and referral rewards.*",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode=ParseMode.MARKDOWN,
        )

async def delete_service_messages(client: Client, message: Message):
    try:
        await client.delete_messages(chat_id=message.chat.id, message_ids=message.id)
    except Exception as e:
        logger.error(f"Service message delete fail hua: {type(e).__name__} - {e}")

# =====================================================================
# LIVE EDITED MESSAGES SYNC ENGINE
# =====================================================================
async def handle_edit(client: Client, message: Message):
    key = (message.chat.id, message.id)
    if key in MESSAGE_MAP:
        tc, tm = MESSAGE_MAP[key]
        try:
            if message.text:
                await client.edit_message_text(tc, tm, message.text, entities=message.entities)
            elif message.caption is not None:
                await client.edit_message_caption(tc, tm, caption=message.caption, caption_entities=message.caption_entities)
        except Exception:
            pass

# =====================================================================
# LIVE DELETED MESSAGES SYNC ENGINE
# =====================================================================
async def handle_delete(client: Client, messages):
    try:
        for msg in messages if isinstance(messages, list) else [messages]:
            chat_id = getattr(msg, "chat", None)
            if not chat_id: continue
            key = (chat_id.id, msg.id)
            if key in MESSAGE_MAP:
                tc, tm = MESSAGE_MAP[key]
                await client.delete_messages(tc, tm)
                # Cleanup map
                del MESSAGE_MAP[key]
                if (tc, tm) in MESSAGE_MAP: del MESSAGE_MAP[(tc, tm)]
    except Exception:
        pass

# =====================================================================
# 2-WAY REACTION SYNC ENGINE (user DM <-> support topic, both directions)
# =====================================================================
async def handle_reaction(client: Client, update):
    try:
        chat = getattr(update, "chat", None)
        msg_id = getattr(update, "message_id", None)
        if not chat or not msg_id:
            return

        key = (chat.id, msg_id)
        if key not in MESSAGE_MAP:
            return
        target_chat, target_msg = MESSAGE_MAP[key]

        new_reactions = getattr(update, "new_reaction", None) or []

        if not new_reactions:
            # User/admin ne saari reactions hata di — dusri taraf bhi clear kar do
            try:
                await client.send_reaction(target_chat, target_msg)
            except Exception:
                pass
            return

        # Premium users ek se zyada reaction laga sakte hain — sabko map karo.
        # Unicode emoji -> str, custom emoji -> uska numeric ID (send_reaction dono support karta hai).
        emojis = []
        for r in new_reactions:
            emoji = getattr(r, "emoji", None)
            custom_id = getattr(r, "custom_emoji_id", None)
            if emoji:
                emojis.append(emoji)
            elif custom_id:
                emojis.append(custom_id)

        if not emojis:
            return

        await client.send_reaction(target_chat, target_msg, emoji=emojis)
    except Exception as e:
        logger.error(f"  Reaction sync failed: {e}")

async def handle_user_flow_messages(client: Client, message: Message) -> bool:
    if not message.from_user:
        return False
    uid = message.from_user.id
    state = USER_FLOW_STATE.get(uid)
    if not state:
        return False

    text = (message.text or "").strip()

    if state == "wait_batch_req":
        USER_FLOW_STATE.pop(uid, None)
        if not text:
            await message.reply_text("⚠️ Kripya valid text message me batch ka naam bhejein.")
            return True
        DB.setdefault("BATCH_REQUESTS", {}).setdefault(text, [])
        if uid not in DB["BATCH_REQUESTS"][text]:
            DB["BATCH_REQUESTS"][text].append(uid)
        DB.setdefault("BATCH_REQUEST_STATUS", {}).setdefault(text, "pending")
        await save_data_async()
        note = DB.get("BATCH_REQUEST_NOTE") or (
            "🙏 **Important Note from Admin:**\n"
            "Main abhi regular active nahi reh pata hu isliye batch upload/add hone me thoda waqt lag sakta hai. "
            "Aap bilkul chinta na karein aur thoda dhairya (patience) rakhein. "
            "Jaise hi yeh batch bot me add hoga, bot aapko direct notification bhej dega! Tab tak ke liye dhanyawad."
        )
        try:
            req_rcvd_msg = rp.message(
                rp.heading("✅ Batch Request Received!"),
                rp.divider(),
                rp.para(
                    ce("CHECK", "✅"), " ", rp.bold("Demand Registered: "), rp.code(text)
                ),
                rp.para(
                    rp.marked(" ⏳ Added to Admin Task Checklist ")
                ),
                rp.blockquote(
                    ce("INFO", "ℹ️"), " ", rp.bold("Note from Admin:\n"),
                    rp.italic(note)
                ),
                rp.buttons(rp.btn("🏠 Main Menu", "u_main", style=rp.Style.SUCCESS))
            )
            await rp.send(client, message.chat.id, req_rcvd_msg)
        except Exception:
            kb = [[InlineKeyboardButton("🏠 Main Menu", callback_data="u_main")]]
            await message.reply_text(
                f"✅ **Batch Request Received!**\n━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📦 **Requested Batch:** `{text}`\n\n"
                f"{note}",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.MARKDOWN
            )
        return True

    elif state == "wait_voucher_code":
        USER_FLOW_STATE.pop(uid, None)
        code = text.upper()
        vouchers = DB.get("VOUCHERS", {})
        if code not in vouchers:
            await safe_reaction(client, message.chat.id, message.id, "❌")
            kb = [
                [InlineKeyboardButton("🔄 Try Again", callback_data="redeem_voucher_btn")],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="u_main")]
            ]
            await message.reply_text(
                "❌ **Invalid Promo Code!**\n\nYeh voucher code exist nahi karta ya expire ho chuka hai.",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.MARKDOWN
            )
            return True

        v_data = vouchers[code]
        used_by = v_data.setdefault("used_by", [])
        if uid in used_by or str(uid) in used_by:
            await safe_reaction(client, message.chat.id, message.id, "⚠️")
            return await message.reply_text("⚠️ Aapne yeh voucher pehle hi redeem kar liya hai!")

        max_uses = v_data.get("max_uses", 1)
        if len(used_by) >= max_uses:
            await safe_reaction(client, message.chat.id, message.id, "⌛")
            return await message.reply_text("⌛ Yeh voucher fully redeemed ho chuka hai (maximum limit reached)!")

        pts = v_data.get("points", 0)
        used_by.append(uid)
        user_key = uid if uid in DB["USER_DATA"] else str(uid)
        user_info = DB["USER_DATA"].setdefault(user_key, {})
        user_info["referral_count"] = round(user_info.get("referral_count", 0) + pts, 2)
        await save_data_async()
        await safe_reaction(client, message.chat.id, message.id, "💎")

        kb = [[InlineKeyboardButton("🏠 Main Menu", callback_data="u_main")]]
        await message.reply_text(
            f"🎉 **Voucher Redeemed Successfully!**\n━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎟️ **Code:** `{code}`\n"
            f"💰 **Reward:** `+{pts} Coins` added to your wallet!\n\n"
            f"Ab aap in coins se batches unlock kar sakte hain.",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode=ParseMode.MARKDOWN
        )
        return True

    return False

# =====================================================================
# REGULAR MESSAGES & SUPPORT TICKETS ENGINE (BULLETPROOF 2-WAY)
# =====================================================================
async def main_message_handler(client: Client, message: Message, is_retry=False):
    user, chat = message.from_user, message.chat

    if user:
        if check_spam(user.id):
            return
        if user.id not in DB.get("BLOCKED_USERS", []):
            if await wizard_message(client, message):
                return
            if await handle_broadcast_flow(client, message):
                return
            if await handle_user_flow_messages(client, message):
                return
    elif chat.type == ChatType.PRIVATE:
        return

    # 1. USER -> ADMIN
    if chat.type == ChatType.PRIVATE:
        if DB.get("MAINTENANCE_MODE", False) and not is_admin(user.id):
            return await message.reply_text("🛠 **Under Maintenance.**")
        

        try:
            topic_id = await get_or_create_topic(user, client)
            if not topic_id:
                return await message.reply_text("  **Support Ticket Error:** Admin ne Support Group me Forum Topics enable nahi kiya hai.")
            
            reply_id = None
            if message.reply_to_message:
                reply_key = (chat.id, message.reply_to_message.id)
                if reply_key in MESSAGE_MAP:
                    _, reply_id = MESSAGE_MAP[reply_key]
            
            try:
                sent = await message.copy(
                    int(SUPPORT_GROUP_ID),
                    message_thread_id=topic_id,
                    reply_to_message_id=reply_id,
                )
            except TypeError:
                target_reply_id = reply_id if reply_id else topic_id
                sent = await message.copy(int(SUPPORT_GROUP_ID), reply_to_message_id=target_reply_id)
            except Exception as e:
                err_str = str(e).lower()
                if "reply" in err_str or "message_id_invalid" in err_str:
                    # Target reply message was deleted, try without reply_id
                    try:
                        sent = await message.copy(
                            int(SUPPORT_GROUP_ID),
                            message_thread_id=topic_id,
                        )
                    except TypeError:
                        sent = await message.copy(int(SUPPORT_GROUP_ID), reply_to_message_id=topic_id)
                else:
                    raise e
                    
            MESSAGE_MAP[(chat.id, message.id)] = (int(SUPPORT_GROUP_ID), sent.id)
            MESSAGE_MAP[(int(SUPPORT_GROUP_ID), sent.id)] = (chat.id, message.id)

            # --- PRIORITY SUPPORT: VIP messages seedha owner ke personal DM me #URGENT_VIP tag ke saath ---
            if is_vip_user(user.id):
                try:
                    uname = f"@{user.username}" if user.username else "no username"
                    await client.send_message(
                        int(OWNER_ID),
                        f"#URGENT_VIP\n👑 **VIP Referrer Message**\n👤 {user.first_name or ''} ({uname})\n🆔 `{user.id}`"
                    )
                    await message.copy(int(OWNER_ID))
                except Exception:
                    pass
        except Exception as e:
            err_str = str(e).lower()
            
            if isinstance(e, (PeerIdInvalid,)) or "peer" in err_str and "invalid" in err_str:
                if not is_retry:
                    try:
                        await message.reply_text("  Connection sync in progress... please wait a moment.")
                        import config
                        await config.refresh_peer_cache(client, SUPPORT_GROUP_ID)
                        return await main_message_handler(client, message, is_retry=True)
                    except Exception:
                        pass
                
            elif ("thread" in err_str or "topic" in err_str or "forum" in err_str) and not is_retry:
                if user.id in DB.get("USER_TOPICS", {}):
                    del DB["USER_TOPICS"][user.id]
                    await save_data_async()
                    return await main_message_handler(client, message, is_retry=True)
                    
            if not is_retry:
                await message.reply_text("  **Message deliver nahi ho paya!**\nAdmin ka Support Group ID theek se configured nahi hai.")

    # 2. ADMIN -> USER
    elif str(chat.id) == str(SUPPORT_GROUP_ID):
        global _BOT_SELF_ID
        if _BOT_SELF_ID is None:
            _BOT_SELF_ID = (await client.get_me()).id
        if user and user.id == _BOT_SELF_ID:
            return

        topic_id = (
            getattr(message, "message_thread_id", None)
            or getattr(message, "reply_to_top_message_id", None)
        )
        if not topic_id and message.reply_to_message:
            topic_id = (
                getattr(message.reply_to_message, "message_thread_id", None)
                or message.reply_to_message.id
            )

        target_uid = None
        if topic_id:
            for u, t in DB.get("USER_TOPICS", {}).items():
                if str(t) == str(topic_id):
                    target_uid = int(u)
                    break

        if not target_uid and message.reply_to_message:
            reply_key = (int(SUPPORT_GROUP_ID), message.reply_to_message.id)
            if reply_key in MESSAGE_MAP:
                mapped_chat_id, _ = MESSAGE_MAP[reply_key]
                target_uid = int(mapped_chat_id)

        if not target_uid:
            return

        reply_id = None
        if message.reply_to_message:
            reply_key = (int(SUPPORT_GROUP_ID), message.reply_to_message.id)
            if reply_key in MESSAGE_MAP:
                _, reply_id = MESSAGE_MAP[reply_key]
        try:
            try:
                sent = await message.copy(target_uid, reply_to_message_id=reply_id)
            except Exception:
                sent = await message.copy(target_uid)

            MESSAGE_MAP[(int(SUPPORT_GROUP_ID), message.id)] = (target_uid, sent.id)
            MESSAGE_MAP[(target_uid, sent.id)] = (int(SUPPORT_GROUP_ID), message.id)
        except Exception as e:
            await message.reply_text(f"  **User ko deliver nahi hua!** Error: `{e}`")

async def on_chat_member_update(client: Client, update: ChatMemberUpdated):
    logger.debug(f"Raw member-update event: chat {update.chat.id}")

    if not update.new_chat_member:
        return
        
    user = update.new_chat_member.user
    status = update.new_chat_member.status
    
    main_id = str(MANDATORY_CHANNEL_ID).replace("-100", "")
    current_id = str(update.chat.id).replace("-100", "")

    if current_id == main_id and status in [ChatMemberStatus.LEFT, ChatMemberStatus.BANNED]:
        if user:
            logger.info(f"🚨 RULE BROKEN! (User Left Channel). Universal Kick Started for User ID: {user.id}")
            # Yahan se kick function call hoga
            await execute_universal_kick(user.id, client)
        else:
            logger.warning("🚨 RULE BROKEN! Par User object nahi mila.")

    # --- INSTANT VERIFY FIX: user ne mandatory channel join kiya hi hai ---
    # 30-second membership cache ko turant invalidate karo taaki "Verified" button
    # stale/cached False na dekhe aur user ko wait na karna pade.
    elif current_id == main_id and status in [ChatMemberStatus.MEMBER, ChatMemberStatus.RESTRICTED, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]:
        if user:
            invalidate_membership_cache(user.id, MANDATORY_CHANNEL_ID)
            logger.info(f"✅ Membership cache invalidated instantly for User ID: {user.id} (joined mandatory channel)")

async def track_chats(client: Client, update: ChatMemberUpdated):
    chat = update.chat
    if not update.new_chat_member:
        return
    status = update.new_chat_member.status
    
    if chat.type == ChatType.PRIVATE and status == ChatMemberStatus.BANNED:
        logger.info(f"🚨 RULE BROKEN! (Bot Blocked). Universal Kick Started for User ID: {chat.id}")
        await execute_universal_kick(chat.id, client)

async def background_sync(client: Client):
    global SPAM_CACHE, _SYNC_IN_PROGRESS
    from pyrogram.errors import UserNotParticipant
    from pyrogram.enums import ChatMemberStatus
    
    SPAM_CACHE = {k: v for k, v in SPAM_CACHE.items() if time.time() - v < 2.0}
    if len(MESSAGE_MAP) > 5000:
        MESSAGE_MAP.clear()
        
    if _SYNC_IN_PROGRESS:
        return
        
    _SYNC_IN_PROGRESS = True
    try:
        # Sirf un users ko check karo jo blocked/admin nahi hain aur pehle channel join kar chuke hain
        user_ids = [
            int(uid) for uid in DB["USER_DATA"].keys()
            if int(uid) not in DB.get("BLOCKED_USERS", [])
            and not is_admin(int(uid))
            and DB["USER_DATA"].get(uid, {}).get("tnc_accepted", False)
        ]

        CHUNK_SIZE = 15   # ek saath kitne membership checks parallel chalein (flood-safe)
        CHUNK_DELAY = 1.0 # har chunk ke beech gap (Telegram rate limits ke andar rehne ke liye)
        kicked_any = False

        async def _check_one(user_id: int):
            nonlocal kicked_any
            try:
                m = await client.get_chat_member(int(MANDATORY_CHANNEL_ID), user_id)
                if m.status in [ChatMemberStatus.LEFT, ChatMemberStatus.BANNED]:
                    logger.info(f"🚨 AUTO-SCANNER: User {user_id} has LEFT. Kicking now!")
                    await execute_universal_kick(user_id, client)
                    kicked_any = True
            except UserNotParticipant:
                logger.info(f"🚨 AUTO-SCANNER: User {user_id} is MISSING. Kicking now!")
                await execute_universal_kick(user_id, client)
                kicked_any = True
            except Exception:
                pass

        for i in range(0, len(user_ids), CHUNK_SIZE):
            chunk = user_ids[i:i + CHUNK_SIZE]
            await asyncio.gather(*[_check_one(uid) for uid in chunk])
            await asyncio.sleep(CHUNK_DELAY)

        if kicked_any:
            await save_data_async()

    finally:
        _SYNC_IN_PROGRESS = False

async def handle_join_request(client: Client, req: ChatJoinRequest):
    chat = req.chat
    user = req.from_user
    if user.id in DB["BLOCKED_USERS"]:
        try:
            await client.decline_chat_join_request(chat.id, user.id)
        except Exception:
            pass
        return
    if chat.id in DB["FREE_CHANNELS"]:
        if await check_membership_pyro(user.id, client):
            try:
                await client.approve_chat_join_request(chat.id, user.id)
                invalidate_membership_cache(user.id, chat.id)
                welcome_str = DB["CUSTOM_WELCOMES"].get(
                    chat.id, f"  **Approved!**\nWelcome to {chat.title}"
                )
                w_msg = await client.send_message(
                    user.id, welcome_str, parse_mode=ParseMode.MARKDOWN
                )
                await schedule_delete(client, w_msg, delay=60)
            except Exception:
                pass
        else:
            try:
                await client.send_message(
                    user.id,
                    f"  **Declined!**\nJoin Main:\n{MANDATORY_CHANNEL_LINK}",
                    parse_mode=ParseMode.MARKDOWN,
                )
                await client.decline_chat_join_request(chat.id, user.id)
            except Exception:
                pass
    elif chat.id in DB["PAID_CHANNELS"]:
        if req.invite_link and req.invite_link.invite_link in DB["LINK_MAP"]:
            try:
                await client.revoke_chat_invite_link(
                    chat.id, req.invite_link.invite_link
                )
            except Exception:
                pass

async def check_demos(client: Client):
    now = time.time()
    mod = False
    for uid, data in list(DB["USER_DATA"].items()):
        if not data.get("demos"):
            continue
        for bid, d_data in data["demos"].copy().items():
            expiry = d_data["expiry"] if isinstance(d_data, dict) else float(d_data)
            warned = d_data.get("warned", False) if isinstance(d_data, dict) else False

            # 10-Minute Expiry Warning (between 1 and 10 minutes remaining)
            if 60 < expiry - now <= 10 * 60 and not warned:
                bname = DB.get("ALL_CHATS", {}).get(int(bid)) or DB.get("ALL_CHATS", {}).get(str(bid)) or "Batch"
                try:
                    await client.send_message(
                        int(uid),
                        f"⏳ **Demo Access Ending in 10 Minutes!**\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"📦 **Batch:** `{bname}`\n\n"
                        f"⚠️ Aapka is batch ka demo access agle **10 minutes** me expire ho jayega.\n\n"
                        f"Lagataar study jari rakhne ke liye dosto ko refer karein ya VIP access lein taaki lectures na ruken!",
                        parse_mode=ParseMode.MARKDOWN
                    )
                except Exception:
                    pass
                if isinstance(d_data, dict):
                    d_data["warned"] = True
                else:
                    data["demos"][bid] = {"expiry": expiry, "warned": True}
                mod = True

            elif now > expiry:
                try:
                    await client.ban_chat_member(int(bid), int(uid))
                    await client.unban_chat_member(int(bid), int(uid))
                    invalidate_membership_cache(uid, bid)
                except Exception:
                    pass
                bname = DB.get("ALL_CHATS", {}).get(int(bid)) or DB.get("ALL_CHATS", {}).get(str(bid)) or "Batch"
                try:
                    await client.send_message(
                        int(uid),
                        f"⌛ **Demo Access Expired**\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"📦 **Batch:** `{bname}`\n\n"
                        f"Aapka demo access period complete ho chuka hai.\n"
                        f"Aur access ke liye bot me `/start` karein aur coins claim karein!",
                        parse_mode=ParseMode.MARKDOWN
                    )
                except Exception:
                    pass
                if bid in data["demos"]:
                    del data["demos"][bid]
                    mod = True

    if DB.get("SCHEDULED_DELETES"):
        surviving = []
        for item in DB["SCHEDULED_DELETES"]:
            if now > item["t"]:
                try:
                    await client.delete_messages(chat_id=item["c"], message_ids=item["m"])
                except Exception as e:
                    logger.error(f"Failed to delete scheduled msg: {e}")
                mod = True
            else:
                surviving.append(item)

        if len(surviving) != len(DB.get("SCHEDULED_DELETES", [])):
            DB["SCHEDULED_DELETES"] = surviving
            mod = True

    if DB.get("LINK_MAP"):
        # Purge invite-links older than 24h. Missing "ts" (pre-upgrade entries)
        # are treated as timestamp 0, so they get swept out on the first run.
        LINK_MAP_TTL = 24 * 3600
        stale_links = [
            link for link, ld in DB["LINK_MAP"].items()
            if now - (ld.get("ts", 0) if isinstance(ld, dict) else 0) > LINK_MAP_TTL
        ]
        for link in stale_links:
            del DB["LINK_MAP"][link]
        if stale_links:
            mod = True
            logger.info(f"  LINK_MAP cleanup: removed {len(stale_links)} stale link(s) older than 24h.")

    if DB.get("PENDING_REQUESTS"):
        stale_users = []
        for uid_key, entry in DB["PENDING_REQUESTS"].items():
            active = entry.get("active_batches", {})
            expired_batches = [bid for bid, exp in active.items() if now > exp]
            for bid in expired_batches:
                del active[bid]
                mod = True
            if not active and now - entry.get("last_link_ts", 0) > GLOBAL_LINK_COOLDOWN:
                stale_users.append(uid_key)
        for uid_key in stale_users:
            del DB["PENDING_REQUESTS"][uid_key]
            mod = True

    if mod:
        await save_data_async()

async def auto_backup_db(client: Client):
    if LOG_CHANNEL_ID and os.path.exists(DATA_FILE):
        try:
            await client.send_document(
                int(LOG_CHANNEL_ID), document=DATA_FILE, caption="  Auto Backup"
            )
        except Exception:
            pass

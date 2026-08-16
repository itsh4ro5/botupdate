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

_SYNC_IN_PROGRESS = False
_BOT_SELF_ID = None

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
            ]
            await client.set_bot_commands(
                admin_cmds, scope=BotCommandScopeChat(chat_id=user_id)
            )
        else:
            await client.set_bot_commands(
                user_cmds, scope=BotCommandScopeChat(chat_id=user_id)
            )
    except Exception:
        pass

# =====================================================================
# REFERRAL POINTS & UNLOCK ENGINE
# =====================================================================
async def process_successful_referral(client: Client, referee_id: int, referrer_id: int):
    referrer_key = referrer_id if referrer_id in DB["USER_DATA"] else str(referrer_id)
    referee_key = referee_id if referee_id in DB["USER_DATA"] else str(referee_id)
    
    if referrer_key not in DB["USER_DATA"]:
        return

    # Referrer ko 1 Point aur +1 Invites count dein (yeh hamesha turant milta hai)
    DB["USER_DATA"][referrer_key]["referral_count"] = DB["USER_DATA"][referrer_key].get("referral_count", 0) + 1
    DB["USER_DATA"][referrer_key]["total_invited"] = DB["USER_DATA"][referrer_key].get("total_invited", 0) + 1

    # --- TIERED MILESTONE BONUS: har 5 successful refers par 1 EXTRA bonus coin ---
    milestone_hit = DB["USER_DATA"][referrer_key]["total_invited"] % 5 == 0
    if milestone_hit:
        DB["USER_DATA"][referrer_key]["referral_count"] += 1

    # --- 25+ REFERS VIP TAG (bot-internal, koi webpage badge nahi) ---
    total_now = DB["USER_DATA"][referrer_key]["total_invited"]
    newly_vip = total_now >= 25 and DB["USER_DATA"][referrer_key].get("tier") != "vip"
    if newly_vip:
        DB["USER_DATA"][referrer_key]["tier"] = "vip"

    # Referee ko ABHI point NAHI milega — sirf record hoga ki isse kisne refer kiya,
    # welcome bonus tab tak "locked" rahega jab tak yeh khud kisi ko refer na kare
    if referee_key in DB["USER_DATA"]:
        DB["USER_DATA"][referee_key]["referred_by"] = referrer_id
        DB["USER_DATA"][referee_key].setdefault("welcome_bonus_claimed", False)

    await save_data_async()

    # Agar referrer khud kisi se refer hua tha aur uska welcome bonus abhi tak locked tha,
    # to apna PEHLA successful refer karte hi wo bonus unlock ho jayega
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

    # Referrer notification
    try:
        await client.send_message(
            referrer_id,
            "🎉 **REFERRAL SUCCESSFUL!**\n\n"
            "Aapke bheje gaye link se ek naye user ne saare steps complete kar liye hain! Aapko **1 Coin** mil gaya hai. 🎁\n"
            "Ab aap is coin ka use karke koi bhi Special Batch unlock kar sakte hain.",
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

    # VIPs ko har successful refer par ek premium sticker/GIF treat milta hai (agar set hai)
    if DB["USER_DATA"][referrer_key].get("tier") == "vip":
        await send_vip_treat(client, referrer_id)

    # Referee notification — coin abhi nahi, sirf unlock karne ka raasta batao
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
        if backend_app.db_fs:
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
            firebase_status = "⚠️ Firebase is NOT connected! Data lost."

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
    if not is_admin_msg(message) or not message.reply_to_message:
        return
    key = (message.chat.id, message.reply_to_message.id)
    if key in MESSAGE_MAP:
        tc, tm = MESSAGE_MAP[key]
        try:
            await client.delete_messages(tc, tm)
            await message.reply_to_message.delete()
            await message.delete()
            del MESSAGE_MAP[key]
            if (tc, tm) in MESSAGE_MAP:
                del MESSAGE_MAP[(tc, tm)]
        except Exception as e:
            logger.error(f"Error deleting message: {e}")
            msg = await message.reply_text(f"  Delete failed: {e}")
            await schedule_delete(client, msg)
    else:
        try:
            await message.reply_to_message.delete()
            await message.delete()
        except Exception as e:
            logger.error(f"Delete failed: {e}")

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
    all_channels = (
        list(DB.get("FREE_CHANNELS", {}).keys())
        + list(DB.get("PAID_CHANNELS", {}).keys())
        + list(DB.get("SPECIAL_CHANNELS", {}).keys())
    )
    if MANDATORY_CHANNEL_ID:
        all_channels.append(MANDATORY_CHANNEL_ID)
    success_count = 0
    for bid in all_channels:
        try:
            await client.unban_chat_member(int(bid), target)
            success_count += 1
        except Exception:
            pass
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
        await client.approve_chat_join_request(batch_id, target_uid)
        invalidate_membership_cache(target_uid, batch_id)
        if str(batch_id) in DB["USER_DATA"].get(target_uid, {}).get("demos", {}):
            del DB["USER_DATA"][target_uid]["demos"][str(batch_id)]
        clear_active_request(target_uid, batch_id)
        await save_data_async()
        await message.reply_text("  **APPROVED (PERM)**", parse_mode=ParseMode.MARKDOWN)
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
    await message.reply_text("  Send message to broadcast.")

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
    found = False
    for cid in set(
        list(DB.get("ALL_CHATS", {}).keys())
        + list(DB.get("FREE_CHANNELS", {}).keys())
        + list(DB.get("PAID_CHANNELS", {}).keys())
        + list(DB.get("SPECIAL_CHANNELS", {}).keys())
    ):
        try:
            m = await client.get_chat_member(int(cid), target_id)
            if m.status in [
                ChatMemberStatus.MEMBER,
                ChatMemberStatus.ADMINISTRATOR,
                ChatMemberStatus.OWNER,
                ChatMemberStatus.RESTRICTED,
            ]:
                r += f"{DB['ALL_CHATS'].get(cid, cid)}: Joined\n"
                found = True
        except Exception:
            pass
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
        for bid in all_channels:
            try:
                async for member in userbot.get_chat_members(int(bid)):
                    uid = member.user.id
                    checked_users += 1
                    if uid != OWNER_ID and not member.user.is_bot:
                        is_in_main = False
                        try:
                            await userbot.get_chat_member(int(MANDATORY_CHANNEL_ID), uid)
                            is_in_main = True
                            safe_users += 1
                        except Exception:
                            pass
                        if not is_in_main:
                            try:
                                await client.ban_chat_member(int(bid), uid)
                                await client.unban_chat_member(int(bid), uid)
                                removed_count += 1
                                await asyncio.sleep(1.5)
                            except Exception:
                                pass
                    await asyncio.sleep(0.1)
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

async def wizard_message(client: Client, message: Message):
    if not message.from_user or message.from_user.id not in ADMIN_WIZARD:
        return False
    uid, state = message.from_user.id, ADMIN_WIZARD[message.from_user.id]
    if state["step"] == "ask_id":
        try:
            cid = int(message.text)
            cname = (await client.get_chat(cid)).title or f"Batch {cid}"
            
            if state["type"] == "free":
                DB.setdefault("FREE_CHANNELS", {})[cid] = cname
            elif state["type"] == "paid":
                DB.setdefault("PAID_CHANNELS", {})[cid] = cname
            elif state["type"] == "special":
                DB.setdefault("SPECIAL_CHANNELS", {})[cid] = cname

            DB.setdefault("ALL_CHATS", {})[cid] = cname
            DB.setdefault("BATCH_CATEGORIES", {})[str(cid)] = state["category"]
            await save_data_async()

            # Special batches: ab yahan ruk kar unlock-coin cost poochenge, baaki (free/paid) turant finish
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
        kb = [[
            InlineKeyboardButton("✅ YES", callback_data="bc_yes"),
            InlineKeyboardButton("❌ NO", callback_data="bc_no"),
        ]]
        await message.reply_text(
            "❓ **Confirm?**",
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
        await q.edit_message_text("  Processing...")
        count = 0
        targets = (
            list(DB.get("USER_DATA", {}).keys())
            if BROADCAST_STATE[uid]["type"] == "broadcast"
            else list(DB.get("FREE_CHANNELS", {}).keys())
            + list(DB.get("PAID_CHANNELS", {}).keys())
            + list(DB.get("SPECIAL_CHANNELS", {}).keys())
        )
        for tid in targets:
            try:
                await client.copy_message(
                    int(tid), uid, BROADCAST_STATE[uid]["content"].id
                )
                count += 1
                await asyncio.sleep(0.05)
            except FloodWait as e:
                await asyncio.sleep(e.value + 1)
            except Exception:
                pass
        await client.send_message(uid, f"  Done. Sent to {count}.")
        del BROADCAST_STATE[uid]

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
            kb = [
                [
                    InlineKeyboardButton(
                        f"🔐 System Lockdown: {'🔴 ON' if not DB.get('NEW_USERS_ALLOWED', True) else '🟢 OFF'}",
                        callback_data="toggle_lockdown",
                    )
                ],
                [
                    InlineKeyboardButton(
                        f"🆓 Free Batches: {'🔒 LOCKED' if DB.get('FREE_LOCKED', False) else '🔓 OPEN'}",
                        callback_data="toggle_free",
                    ),
                    InlineKeyboardButton(
                        f"💰 Paid Batches: {'🔒 LOCKED' if DB.get('PAID_LOCKED', False) else '🔓 OPEN'}",
                        callback_data="toggle_paid",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        f"🤖 Test Bot: {'🔒 LOCKED' if DB.get('TEST_BOT_LOCKED', False) else '🔓 OPEN'}",
                        callback_data="toggle_testbot",
                    )
                ],
                [
                    InlineKeyboardButton(
                        f"🛠️ Maintenance Mode: {'🔴 ON' if DB.get('MAINTENANCE_MODE', False) else '🟢 OFF'}",
                        callback_data="toggle_maintenance",
                    )
                ],
                [InlineKeyboardButton("🔙 Back to Terminal", callback_data="dash_home")],
            ]
            await q.edit_message_text(
                "🔒 **Security & Access Control**",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.MARKDOWN,
            )
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
            kb = [
                [
                    InlineKeyboardButton(
                        f"🔐 System Lockdown: {'🔴 ON' if not DB.get('NEW_USERS_ALLOWED', True) else '🟢 OFF'}",
                        callback_data="toggle_lockdown",
                    )
                ],
                [
                    InlineKeyboardButton(
                        f"🆓 Free Batches: {'🔒 LOCKED' if DB.get('FREE_LOCKED', False) else '🔓 OPEN'}",
                        callback_data="toggle_free",
                    ),
                    InlineKeyboardButton(
                        f"💰 Paid Batches: {'🔒 LOCKED' if DB.get('PAID_LOCKED', False) else '🔓 OPEN'}",
                        callback_data="toggle_paid",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        f"🤖 Test Bot: {'🔒 LOCKED' if DB.get('TEST_BOT_LOCKED', False) else '🔓 OPEN'}",
                        callback_data="toggle_testbot",
                    )
                ],
                [
                    InlineKeyboardButton(
                        f"🛠️ Maintenance Mode: {'🔴 ON' if DB.get('MAINTENANCE_MODE', False) else '🟢 OFF'}",
                        callback_data="toggle_maintenance",
                    )
                ],
                [InlineKeyboardButton("🔙 Back to Terminal", callback_data="dash_home")],
            ]
            await q.edit_message_text(
                "🔒 **Security & Access Control**",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.MARKDOWN,
            )
        elif data == "dash_db":
            await q.answer()
            kb = [
                [
                    InlineKeyboardButton("📥 Download Backup", callback_data="act_backup"),
                    InlineKeyboardButton("🔄 Run Sync", callback_data="act_sync"),
                ],
                [InlineKeyboardButton("👥 Download All Users List", callback_data="act_allusers")],
                [InlineKeyboardButton("🗄️ Store Batch Data (Scan)", callback_data="input_storebatch")],
                [InlineKeyboardButton("🔍 Specific User Data", callback_data="input_userlookup")],
                [
                    InlineKeyboardButton("🚫 Ban User", callback_data="input_ban"),
                    InlineKeyboardButton("✅ Unban User", callback_data="input_unban"),
                ],
                [InlineKeyboardButton("🎁 Gift Coin", callback_data="giftcoin_start")],
                [InlineKeyboardButton("🔙 Back", callback_data="dash_home")],
            ]
            await q.edit_message_text(
                "🛡️ **Database Tools**",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.MARKDOWN,
            )
        elif data in ["dash_batches", "adash_batches"]:
            await q.answer()
            kb = [
                [
                    InlineKeyboardButton("➕ Add Batch", callback_data="act_addbatch"),
                    InlineKeyboardButton("🗑️ Delete Batch", callback_data="input_delbatch"),
                ],
                [
                    InlineKeyboardButton("📁➕ Add Category", callback_data="input_addcat"),
                    InlineKeyboardButton("📁🗑️ Delete Category", callback_data="act_delcat"),
                ],
                [
                    InlineKeyboardButton("🏷️ Set Batch Category", callback_data="input_setcat"),
                    InlineKeyboardButton("🧹 Empty Batch", callback_data="input_emptybatch"),
                ],
                [
                    InlineKeyboardButton("🚀 Super Forwarder (All-in-One)", callback_data="input_superfwd"),
                    InlineKeyboardButton("🛡️ Clean Unverified", callback_data="input_cleanbatch")
                ],
                [InlineKeyboardButton("📝 Advanced Caption Changer", callback_data="input_advcap")],
                [InlineKeyboardButton("📊 Batch Stats", callback_data="act_batchstats")],
                [InlineKeyboardButton("🔙 Back", callback_data="dash_home")],
            ]
            await q.edit_message_text(
                "📦 **Batches Management**",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.MARKDOWN,
            )
        elif data == "dash_staff":
            await q.answer()
            kb = [
                [
                    InlineKeyboardButton("➕ Add Admin", callback_data="input_addadmin"),
                    InlineKeyboardButton("➖ Remove Admin", callback_data="input_deladmin"),
                ],
                # 👇 YEH NAYA BUTTON ADD KIYA GAYA HAI 👇
                [InlineKeyboardButton("📋 Admin List", callback_data="act_adminlist")],
                [InlineKeyboardButton("🔙 Back", callback_data="dash_home")],
            ]
            await q.edit_message_text(
                "🧑\u200d💼 **Staff Management**",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.MARKDOWN,
            )
        elif data in ["dash_comms", "adash_comms"]:
            await q.answer()
            kb = [
                [
                    InlineKeyboardButton("📢 Broadcast", callback_data="act_broadcast"),
                    InlineKeyboardButton("📝 Post Message", callback_data="act_post"),
                ],
                [
                    InlineKeyboardButton("🤖 Set Test Bot", callback_data="input_settestbot"),
                    InlineKeyboardButton("👋 Set Welcome", callback_data="input_setwelcome"),
                ],
                [InlineKeyboardButton("🔙 Back", callback_data="dash_home")],
            ]
            await q.edit_message_text(
                "📢 **Communications**",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.MARKDOWN,
            )
        elif data == "userbot_details":
            await q.answer()
            if uid != OWNER_ID:
                return await q.answer("  Access Denied! Owner only.", show_alert=True)
            session = DB.get("USERBOT_SESSION")
            phone = DB.get("USERBOT_PHONE", "Not Found")
            if session:
                connected_status = "  Active & Ready"
                text = (
                    f"  **USERBOT CONTROL PANEL**\n\n  **Status:** {connected_status}\n  **Logged in Number:** `{phone}`\n\n"
                    "*Userbot is fully linked and ready to execute /emptybatch, /clear, and /joinall commands.*"
                )
                kb = [
                    [InlineKeyboardButton("🚪 Logout (Delete Session)", callback_data="userbot_logout")],
                    [InlineKeyboardButton("🔙 Back", callback_data="dash_home")],
                ]
            else:
                text = (
                    "  **USERBOT CONTROL PANEL**\n\n  **Status:**   NOT LOGGED IN\n\n"
                    "*Koi active session nahi hai. Userbot features won't work. Kripya login karein.*"
                )
                kb = [
                    [InlineKeyboardButton("🔑 Login Now", callback_data="input_userbotphone")],
                    [InlineKeyboardButton("🔙 Back", callback_data="dash_home")],
                ]
            await q.edit_message_text(
                text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN
            )
        elif data == "userbot_logout":
            if uid != OWNER_ID:
                return
            DB["USERBOT_SESSION"] = None
            DB["USERBOT_PHONE"] = None
            await save_data_async()
            if os.path.exists("temp_owner.session"):
                os.remove("temp_owner.session")
            await q.answer("  Session Deleted Successfully!", show_alert=True)
            await q.edit_message_text(
                "  **Userbot is now LOGGED OUT.**",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Back", callback_data="dash_home")]
                ]),
            )
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
                "settestbot": "Send new Test Bot link:",
                "setwelcome": "Send Batch ID and Welcome Msg:\nFormat: `bid message`",
                "delbatch": "Send Type and ID:\nFormat: `free 123` or `paid 123` or `special 123`",
                "addcat": "Send Name for new Category:",
                "setcat": "Send Batch ID(s) (comma ya space lagakar):\nFormat: `-100x, -100y`",
                "emptybatch": "  **DHYAN DEIN!**\nSend Batch ID jisko poora khali (empty) karna hai:\nFormat: `-100123456789`",
                "advcap": "📝 **Advanced Caption Changer (Step 1/5)**\n\nUs **Channel ID** ko bhejein jiske captions edit karne hain (e.g. `-10012345678`):",
                "cleanbatch": "🛡️ **Clean Unverified Users (Anti-Leech)**\n\nUs **Batch/Channel ID** ko bhejein jise clean karna hai (e.g. `-100123456789`).\n\n*Note: Ye un sabhi users ko nikal dega jo Mandatory Channel me nahi hain ya jinhone bot start nahi kiya hai.*",
                "storebatch": "🗄️ **Store Batch Data**\n\nJis channel ka purana data (Videos/PDFs) Firebase me index karna hai, uska Chat ID bhejein:\nFormat: `-100123456789`",
                "superfwd": "🚀 **Super Forwarder (Step 1/7)**\n\nUs **Source Channel ID** ko bhejein jahan se files (content) uthani hain (e.g. `-10012345678`):",
                "userbotphone": "  **Apna Phone Number bhejein**\nCountry code ke sath (Jaise: `+919876543210`):",
                "userbototp": "  **OTP Bhejein**\n  *OTP spaces me bhejein!* Jaise: `1 2 3 4 5`:",
                "userbotpass": "  **2FA Password bhejein:**",
                "userlookup": "🔍 **Specific User Data**\n\nUser ka User ID bhejein:",
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
            kb = [
                [
                    InlineKeyboardButton("🚫 Ban", callback_data="input_ban"),
                    InlineKeyboardButton("✅ Unban", callback_data="input_unban"),
                ],
                [
                    InlineKeyboardButton("👢 Kick", callback_data="input_kick"),
                    InlineKeyboardButton("🔍 Find", callback_data="input_find"),
                ],
                [InlineKeyboardButton("♻️ Reset User Data", callback_data="input_resetuser")],
                [InlineKeyboardButton("🔙 Back", callback_data="dash_home")],
            ]
            await q.edit_message_text(
                "👥 **User Management**",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.MARKDOWN,
            )
        elif data == "adash_approvals":
            await q.answer()
            kb = [
                [
                    InlineKeyboardButton("🕐 Approve Demo", callback_data="input_demo"),
                    InlineKeyboardButton("✅ Approve Perm", callback_data="input_perm"),
                ],
                [InlineKeyboardButton("⏳ Extend Demo Time", callback_data="input_extend")],
                [InlineKeyboardButton("🔙 Back", callback_data="dash_home")],
            ]
            await q.edit_message_text(
                "✅ **Access Approvals**",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.MARKDOWN,
            )

        elif data == "act_adminlist":
            await q.answer("Fetching Admin List...")
            
            # Security check: Sirf owner ye list dekh sakta hai
            if uid != OWNER_ID and str(uid) != str(OWNER_ID):
                return await q.answer("  Access Denied! Owner Only.", show_alert=True)
                
            admin_ids = DB.get("ADMIN_IDS", [])
            text = "📋 **BOT ADMINS LIST**\n" + "—" * 20 + "\n\n"
            
            if not admin_ids:
                text += "  Koi admin assign nahi kiya gaya hai."
            else:
                # Har admin ki details Database se nikal kar print karega
                for aid in admin_ids:
                    user_key = aid if aid in DB.get("USER_DATA", {}) else (str(aid) if str(aid) in DB.get("USER_DATA", {}) else None)
                    user_info = DB.get("USER_DATA", {}).get(user_key, {}) if user_key else {}
                    
                    name = user_info.get("name", "Unknown User")
                    username = user_info.get("username", "N/A")
                    
                    # Owner aur Admin ko alag-alag dikhane ke liye
                    role = "  **Owner**" if str(aid) == str(OWNER_ID) else "  **Admin**"
                    
                    text += f"{role}\n  **Name:** {name}\n  **ID:** `{aid}`\n  **Username:** @{username}\n\n"
                    
            # Back button wapas Staff menu me le jayega
            kb = [[InlineKeyboardButton("🔙 Back", callback_data="dash_staff")]]
            
            await q.edit_message_text(
                text, 
                reply_markup=InlineKeyboardMarkup(kb), 
                parse_mode=ParseMode.MARKDOWN
            )
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

        # --- MY INFO VIEW ---
        elif data == "my_info":
            await q.answer()
            user_key = uid if uid in DB["USER_DATA"] else str(uid)
            refer_points = DB["USER_DATA"].get(user_key, {}).get("referral_count", 0)
            total_invited = DB["USER_DATA"].get(user_key, {}).get("total_invited", 0)
            is_vip = DB["USER_DATA"].get(user_key, {}).get("tier") == "vip"

            if is_vip:
                txt = (
                    "👑 **[Elite Referrer] — MY INFO** 👑\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🆔 **User ID:** `{uid}`\n"
                    f"🏷️ **Tag:** `👑 VIP Referrer`\n"
                    f"👥 **Total Refers:** `{total_invited}`\n"
                    f"💰 **Wallet:** `{refer_points}` Coins\n\n"
                    "💎 *Enjoy zero cooldowns, exclusive materials, and monthly bonuses — thank you for being Elite.*"
                )
            else:
                txt = (
                    f"👤 **MY INFO**\n"
                    f"🆔 **ID:** `{uid}`\n"
                    f"👥 **Total Refers:** `{total_invited}`\n"
                    f"🎁 **Available Coins:** `{refer_points}`\n\n"
                    f"💡 *In coins ka use karke aap koi bhi Special Batch ya saari Free Batches unlock kar sakte hain.*"
                )
            kb = [[InlineKeyboardButton("🔙 Back", callback_data="u_main")]]
            await q.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

        # --- HOME MENU: REFER & EARN LAYOUT ---
        elif data == "menu_refer":
            await q.answer()
            bot_username = "H4R_Contact_bot"
            ref_link = f"https://t.me/{bot_username}?start={uid}"
            share_text = f"🚀 Crack your exams with H4R Bot! Get free access to premium study materials and special batches.\n\nStart now: {ref_link}"
            share_url = f"https://t.me/share/url?url={urllib.parse.quote(ref_link)}&text={urllib.parse.quote(share_text)}"

            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("📤 Share to Chat", url=share_url)],
                [InlineKeyboardButton("🔙 Main Menu", callback_data="u_main")]
            ])

            user_key = uid if uid in DB["USER_DATA"] else str(uid)
            pts = DB["USER_DATA"].get(user_key, {}).get("referral_count", 0)
            total_inv = DB["USER_DATA"].get(user_key, {}).get("total_invited", 0)
            is_vip = DB["USER_DATA"].get(user_key, {}).get("tier") == "vip"

            text = (
                "🎁 **Refer & Earn Program**\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "Invite your friends and earn 1 coin on every successful refer!\n\n"
                "New users don't get a coin instantly — they unlock their own 1 coin welcome bonus only after THEY successfully refer someone too. Keep the chain going! 🔗\n\n"
                "🏆 **Milestone Bonus:** Every 5 successful refers = **+1 EXTRA Coin!**\n"
                "👑 **VIP Tag:** Cross 25 total refers to unlock the VIP Referrer tag!\n\n"
                + (f"👑 Your Tag: **VIP Referrer**\n" if is_vip else "")
                + f"👥 Total Referred Users: {total_inv}\n"
                f"💰 Total Earnings: {pts}\n\n"
                "🔗 Your Referral Link:\n"
                f"`{ref_link}`\n\n"
                "Click the button below to share directly with your friends! 👇"
            )

            await q.edit_message_text(
                text,
                reply_markup=kb,
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=True
            )

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
                    "  Abhi tak join nahi kiya hai. Kripya pehle channel join karein!",
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
                    "  **Test Bot Access Verification:**\n\nYou are verified! Click the button below to open the Test Bot.",
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
            all_batches = {
                **DB.get("FREE_CHANNELS", {}),
                **DB.get("PAID_CHANNELS", {}),
                **DB.get("SPECIAL_CHANNELS", {})
            }
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

            nav_buttons = []
            if page > 0:
                nav_buttons.append(InlineKeyboardButton("🔙 Back", callback_data=f"my_batches_{page-1}"))
            if end_idx < total_batches:
                nav_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"my_batches_{page+1}"))

            if nav_buttons:
                kb.append(nav_buttons)
            kb.append([InlineKeyboardButton("🏠 Main Menu", callback_data="u_main")])

            await q.edit_message_text(
                f"  **My Batches (Page {page+1})**\n\nYahan wo sabhi batches hain jisme aap join hain ya unlocked hain. Click karke access karein:",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.MARKDOWN,
            )

        elif data.startswith("all_batches_"):
            await q.answer()
            kb = [
                [InlineKeyboardButton(f"📁 {cat}", callback_data=f"showcat_{i}")]
                for i, cat in enumerate(DB.get("CATEGORIES", DEFAULT_CATEGORIES))
            ] + [
                [InlineKeyboardButton("🏠 Main Menu", callback_data="u_main")]
            ]
            await q.edit_message_text(
                "📂 **All Batches - Select Category:**",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.MARKDOWN,
            )

        # --- CATEGORY DETAILS WITH SPECIAL BATCHES BUTTON ---
        elif data.startswith("showcat_"):
            await q.answer()
            cat_idx = int(data.split("_")[1])
            kb = [
                [
                    InlineKeyboardButton("🆓 Free Batches", callback_data=f"listcat_{cat_idx}_free_0"),
                    InlineKeyboardButton("💰 Paid Batches", callback_data=f"listcat_{cat_idx}_paid_0"),
                ],
                [
                    InlineKeyboardButton("✨ Special Batches", callback_data=f"listcat_{cat_idx}_special_0"),
                ],
                [
                    InlineKeyboardButton("🔙 Back to Categories", callback_data="all_batches_0")
                ],
            ]
            await q.edit_message_text(
                f"  **Category: {DB.get('CATEGORIES', DEFAULT_CATEGORIES)[cat_idx]}**\n\nAapko kis type ke batch chahiye?",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.MARKDOWN,
            )

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
            elif b_type == "paid":
                source_dict = DB.get("PAID_CHANNELS", {})
            else:
                source_dict = DB.get("SPECIAL_CHANNELS", {})

            filtered_batches = [
                (cid, name)
                for cid, name in source_dict.items()
                if DB.get("BATCH_CATEGORIES", {}).get(str(cid), "Other Batches") == cat_name
            ]

            if not filtered_batches:
                return await q.edit_message_text(
                    f"  Is category ({cat_name}) me abhi koi {b_type.title()} batch nahi hai.",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("🔙 Back", callback_data=f"showcat_{cat_idx}")
                    ]]),
                    parse_mode=ParseMode.MARKDOWN,
                )

            MAX_PER_PAGE = 10
            total = len(filtered_batches)
            start_idx = page * MAX_PER_PAGE
            end_idx = start_idx + MAX_PER_PAGE

            kb = []
            for cid, name in filtered_batches[start_idx:end_idx]:
                if b_type == "free":
                    cb_data = f"get_f_{cid}"
                    prefix = "⚡"
                elif b_type == "paid":
                    cb_data = f"view_p_{cid}"
                    prefix = "👑"
                else:
                    cb_data = f"view_s_{cid}"
                    prefix = "✨"

                kb.append([InlineKeyboardButton(f"{prefix} {name}", callback_data=cb_data)])

            nav_buttons = []
            if page > 0:
                nav_buttons.append(
                    InlineKeyboardButton("🔙 Back", callback_data=f"listcat_{cat_idx}_{b_type}_{page-1}")
                )
            if end_idx < total:
                nav_buttons.append(
                    InlineKeyboardButton("Next ➡️", callback_data=f"listcat_{cat_idx}_{b_type}_{page+1}")
                )
            if nav_buttons:
                kb.append(nav_buttons)
            kb.append([InlineKeyboardButton("📂 Category Menu", callback_data=f"showcat_{cat_idx}")])

            await q.edit_message_text(
                f"  **{cat_name} ({b_type.title()})**\n\nNeeche diye gaye batches par click karein:",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.MARKDOWN,
            )

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

                kb = [[InlineKeyboardButton("🚀 Join Batch", url=l.invite_link)]]
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
            kb = [
                [InlineKeyboardButton("🔑 Request Access", callback_data=f"req_access_{cid}")],
                [InlineKeyboardButton("🔙 Back", callback_data="u_main")],
            ]
            try:
                await q.edit_message_text(
                    "  **Premium Access:**\nClick below.",
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
                DB.setdefault("LINK_MAP", {})[l.invite_link] = {"u": uid, "b": cid}
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
    kb = [[InlineKeyboardButton("✅ I Read & Accept", callback_data="accept_tnc")]]
    txt = (
        "  **STRICT WARNING & TERMS OF SERVICE**  \n\n"
        "  **ENGLISH:**\n"
        "If you leave the Main Channel or block this bot, you will be **INSTANTLY REMOVED** from ALL joined groups and channels.\n\n"
        "  **HINDI:**\n"
        "Agar aapne Main Channel ko chhoda (leave kiya) ya is bot ko block kiya, toh aapko sabhi groups aur channels se **TURANT NIKAL** diya jayega.\n\n"
        "  *Click 'I Read & Accept' only if you agree to these terms.*"
    )
    await message.reply_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

async def show_tnc_menu_cb(client: Client, q: CallbackQuery):
    kb = [[InlineKeyboardButton("✅ I Read & Accept", callback_data="accept_tnc")]]
    txt = (
        "  **STRICT WARNING & TERMS OF SERVICE**  \n\n"
        "  **ENGLISH:**\n"
        "If you leave the Main Channel or block this bot, you will be **INSTANTLY REMOVED** from ALL joined groups and channels.\n\n"
        "  **HINDI:**\n"
        "Agar aapne Main Channel ko chhoda (leave kiya) ya is bot ko block kiya, toh aapko sabhi groups aur channels se **TURANT NIKAL** diya jayega.\n\n"
        "  *Click 'I Read & Accept' only if you agree to these terms.*"
    )
    await q.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

def build_home_menu(user_key, user):
    """Normal users: standard half-width layout. VIPs: personalized greeting + exclusive full-width buttons."""
    vip = DB["USER_DATA"].get(user_key, {}).get("tier") == "vip"
    first_name = (user.first_name if user and user.first_name else "there")

    if vip:
        total_inv = DB["USER_DATA"].get(user_key, {}).get("total_invited", 0)
        pts = DB["USER_DATA"].get(user_key, {}).get("referral_count", 0)
        txt = (
            f"👑 **[Elite Referrer] {first_name}**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Welcome back! You have successfully referred `{total_inv}` students so far.\n"
            f"💰 **Wallet Balance:** `{pts}` Coins\n\n"
            "✨ *Your VIP dashboard is ready:*"
        )
        kb = [
            [InlineKeyboardButton("👑 My Batches (Elite Access)", callback_data="my_batches_0")],
            [InlineKeyboardButton("🌟 All Batches", callback_data="all_batches_0")],
            [InlineKeyboardButton("💎 VIP Course Materials", callback_data="vip_materials")],
            [InlineKeyboardButton("🎁 Claim Monthly Bonus", callback_data="vip_monthly_bonus")],
            [InlineKeyboardButton("🚀 Refer & Earn", callback_data="menu_refer")],
            [InlineKeyboardButton("🤖 Test Bot", callback_data="test_bot")],
            [InlineKeyboardButton("🎥 How to use the bot", url="https://t.me/c/2836314734/1244")],
            [InlineKeyboardButton("💎 My Info", callback_data="my_info")],
        ]
    else:
        txt = (
            "🌟 **Welcome to the Premium Hub!** 🌟\nYour centralized portal for exclusive communities.\n\n👇 *Select an option below:*"
        )
        kb = [
            [
                InlineKeyboardButton("📚 My Batches", callback_data="my_batches_0"),
                InlineKeyboardButton("🌐 All Batches", callback_data="all_batches_0"),
            ],
            [InlineKeyboardButton("🤖 Test Bot", callback_data="test_bot")],
            [InlineKeyboardButton("🎥 How to use the bot", url="https://t.me/c/2836314734/1244")],
            [InlineKeyboardButton("🎁 Refer & Earn", callback_data="menu_refer")],
            [InlineKeyboardButton("ℹ️ My Info", callback_data="my_info")],
        ]
    return txt, InlineKeyboardMarkup(kb)

async def show_user_menu(client: Client, message: Message):
    user_key = message.from_user.id if message.from_user.id in DB["USER_DATA"] else str(message.from_user.id)
    txt, kb = build_home_menu(user_key, message.from_user)
    await message.reply_text(txt, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

async def show_user_menu_cb(client: Client, q: CallbackQuery):
    user_key = q.from_user.id if q.from_user.id in DB["USER_DATA"] else str(q.from_user.id)
    txt, kb = build_home_menu(user_key, q.from_user)
    await q.edit_message_text(txt, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

# =====================================================================
# ROLE SWITCHER PANEL (Owner -> Owner/Admin/User, Admin -> Admin/User)
# =====================================================================
def build_owner_panel_kb():
    kb = [
        [
            InlineKeyboardButton("🔒 Security", callback_data="dash_locks"),
            InlineKeyboardButton("💾 Database", callback_data="dash_db"),
        ],
        [
            InlineKeyboardButton("📦 Batches", callback_data="dash_batches"),
            InlineKeyboardButton("🧑\u200d💼 Staff", callback_data="dash_staff"),
        ],
        [
            InlineKeyboardButton("📢 Comms", callback_data="dash_comms"),
            InlineKeyboardButton("📊 Analytics", callback_data="dash_stats"),
        ],
        [InlineKeyboardButton("🤖 Userbot Login & Stats", callback_data="userbot_details")],
        [InlineKeyboardButton("🔄 Switch Panel", callback_data="role_selector")],
    ]
    text = "👑 **SYSTEM MASTER TERMINAL**\n\nSelect a module below:"
    return text, InlineKeyboardMarkup(kb)

def build_admin_panel_kb():
    kb = [
        [
            InlineKeyboardButton("👥 Users", callback_data="adash_users"),
            InlineKeyboardButton("✅ Approvals", callback_data="adash_approvals"),
        ],
        [
            InlineKeyboardButton("📦 Batches", callback_data="adash_batches"),
            InlineKeyboardButton("📢 Comms", callback_data="adash_comms"),
        ],
        [InlineKeyboardButton("🔄 Switch Panel", callback_data="role_selector")],
    ]
    text = "🛡 **ADMINISTRATOR DASHBOARD**\n\nSelect an action below:"
    return text, InlineKeyboardMarkup(kb)

def build_role_selector_kb(user_id):
    is_owner = str(user_id) == str(OWNER_ID)
    kb = []
    if is_owner:
        kb.append([InlineKeyboardButton("👑 Owner Panel", callback_data="goto_owner_panel")])
    kb.append([InlineKeyboardButton("🛡 Admin Panel", callback_data="goto_admin_panel")])
    kb.append([InlineKeyboardButton("👤 User Panel", callback_data="goto_user_panel")])
    text = "🎛 **Select Panel**\n\nAap kis panel me jaana chahte hain?"
    return text, InlineKeyboardMarkup(kb)

async def show_role_selector(client: Client, message: Message, user):
    text, kb = build_role_selector_kb(user.id)
    await message.reply_text(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

async def show_role_selector_cb(client: Client, q: CallbackQuery):
    text, kb = build_role_selector_kb(q.from_user.id)
    await q.edit_message_text(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

async def goto_owner_panel_cb(client: Client, q: CallbackQuery):
    if str(q.from_user.id) != str(OWNER_ID):
        return await q.answer("  Access Denied! Owner Only.", show_alert=True)
    await q.answer()
    text, kb = build_owner_panel_kb()
    await q.edit_message_text(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

async def goto_admin_panel_cb(client: Client, q: CallbackQuery):
    if not is_admin(q.from_user.id):
        return await q.answer("  Access Denied! Admins Only.", show_alert=True)
    await q.answer()
    text, kb = build_admin_panel_kb()
    await q.edit_message_text(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

async def goto_user_panel_cb(client: Client, q: CallbackQuery):
    await q.answer()
    await show_user_menu_cb(client, q)

async def start(client: Client, message: Message):
    user = message.from_user
    await set_role_based_commands(user.id, client)
    
    user_key = user.id if user.id in DB.get("USER_DATA", {}) else (str(user.id) if str(user.id) in DB.get("USER_DATA", {}) else user.id)
    
    if user_key not in DB["USER_DATA"]:
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
    if args:
        ref_val = args[0]
        referrer_id = None
        
        if ref_val.isdigit():
            referrer_id = int(ref_val)
        elif ref_val.startswith("ref_"):
            parts = ref_val.split("_")
            if len(parts) >= 2 and parts[-1].isdigit():
                referrer_id = int(parts[-1])
                
        if referrer_id and str(referrer_id) != str(user.id):
            DB["USER_DATA"][user_key]["pending_referral"] = referrer_id
            await save_data_async()

    await get_or_create_topic(user, client)

    # --- LOADING ANIMATION ---
    loading_msg = await message.reply_text("⏳ **Loading, please wait...**", parse_mode=ParseMode.MARKDOWN)
    await asyncio.sleep(0.7)

    if str(user.id) == str(OWNER_ID) or is_admin(user.id):
        # Owner ko 3 panel (Owner/Admin/User) aur Admin ko 2 panel (Admin/User) dikhega
        text, kb = build_role_selector_kb(user.id)
        await loading_msg.edit_text(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
    elif await check_membership_pyro(user.id, client):
        if not DB["USER_DATA"].get(user_key, {}).get("tnc_accepted", False):
            tnc_kb = [[InlineKeyboardButton("✅ I Read & Accept", callback_data="accept_tnc")]]
            tnc_txt = (
                "⚠️ **STRICT WARNING & TERMS OF SERVICE**\n\n"
                "🇬🇧 **ENGLISH:**\n"
                "If you leave the Main Channel or block this bot, you will be **INSTANTLY REMOVED** from ALL joined groups and channels.\n\n"
                "🇮🇳 **HINDI:**\n"
                "Agar aapne Main Channel ko chhoda (leave kiya) ya is bot ko block kiya, toh aapko sabhi groups aur channels se **TURANT NIKAL** diya jayega.\n\n"
                "✅ *Click 'I Read & Accept' only if you agree to these terms.*"
            )
            await loading_msg.edit_text(tnc_txt, reply_markup=InlineKeyboardMarkup(tnc_kb), parse_mode=ParseMode.MARKDOWN)
        else:
            if "pending_referral" in DB["USER_DATA"][user_key]:
                referrer_id = DB["USER_DATA"][user_key].pop("pending_referral")
                await save_data_async()
                await process_successful_referral(client, user.id, referrer_id)

            txt, kb = build_home_menu(user_key, user)
            await loading_msg.edit_text(txt, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
    else:
        if not DB.get("NEW_USERS_ALLOWED", True):
            return await loading_msg.edit_text("🚫 **Entry Closed!**", parse_mode=ParseMode.MARKDOWN)
        kb = [
            [InlineKeyboardButton("📢 Join Channel", url=MANDATORY_CHANNEL_LINK)],
            [InlineKeyboardButton("✅ I've Joined", callback_data="verify")],
        ]
        await loading_msg.edit_text(
            "📢 **Join Main Channel First**\n\nTo access batches and unlock referral rewards, please join our mandatory channel first.",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode=ParseMode.MARKDOWN,
        )

async def start_from_cb(client: Client, q: CallbackQuery):
    user = q.from_user
    user_key = user.id if user.id in DB.get("USER_DATA", {}) else (str(user.id) if str(user.id) in DB.get("USER_DATA", {}) else user.id)
    await set_role_based_commands(user.id, client)
    
    if str(user.id) == str(OWNER_ID):
        text, kb = build_owner_panel_kb()
        await q.edit_message_text(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
    elif is_admin(user.id):
        text, kb = build_admin_panel_kb()
        await q.edit_message_text(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
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
            "📢 **Join Main Channel First**",
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
                
            elif ("reply" in err_str or "deleted" in err_str or "topic" in err_str) and not is_retry:
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

        async def _check_one(user_id: int):
            try:
                m = await client.get_chat_member(int(MANDATORY_CHANNEL_ID), user_id)
                if m.status in [ChatMemberStatus.LEFT, ChatMemberStatus.BANNED]:
                    logger.info(f"🚨 AUTO-SCANNER: User {user_id} has LEFT. Kicking now!")
                    await execute_universal_kick(user_id, client)
            except UserNotParticipant:
                logger.info(f"🚨 AUTO-SCANNER: User {user_id} is MISSING. Kicking now!")
                await execute_universal_kick(user_id, client)
            except Exception:
                pass

        for i in range(0, len(user_ids), CHUNK_SIZE):
            chunk = user_ids[i:i + CHUNK_SIZE]
            await asyncio.gather(*[_check_one(uid) for uid in chunk])
            await save_data_async()
            await asyncio.sleep(CHUNK_DELAY)

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
            if now > expiry:
                try:
                    await client.ban_chat_member(int(bid), int(uid))
                    await client.unban_chat_member(int(bid), int(uid))
                    invalidate_membership_cache(uid, bid)
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

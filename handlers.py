import asyncio
from datetime import datetime, timedelta
import io
import os
import re
import time
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

def get_cooldown_remaining(uid: int) -> int:
    """Rule A: returns remaining whole minutes of the global cooldown (0 = clear)."""
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
# REFERRAL FULFILLMENT ENGINE
# =====================================================================
async def handle_referral_redemption(client: Client, referee, referrer_id: int, batch_id: int):
    referee_id = referee.id
    if referee_id == referrer_id:
        try:
            await client.send_message(
                referee_id,
                "⚠️ **Aap khud ke referral link se batch unlock nahi kar sakte!**\nApne kisi dost ko link share karein.",
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception:
            pass
        return

    referrer_key = referrer_id if referrer_id in DB["USER_DATA"] else str(referrer_id)
    referee_key = referee_id if referee_id in DB["USER_DATA"] else str(referee_id)

    if referrer_key not in DB["USER_DATA"]:
        return

    DB["USER_DATA"][referrer_key].setdefault("unlocked_batches", [])
    DB["USER_DATA"][referee_key].setdefault("unlocked_batches", [])

    referrer_unlocked = DB["USER_DATA"][referrer_key]["unlocked_batches"]
    referee_unlocked = DB["USER_DATA"][referee_key]["unlocked_batches"]

    if batch_id in referee_unlocked or str(batch_id) in referee_unlocked:
        try:
            await client.send_message(
                referee_id,
                "ℹ️ **Aapne pehle se is Special Batch ka access unlock kiya hua hai!**",
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception:
            pass
        return

    referee_unlocked.append(batch_id)
    if batch_id not in referrer_unlocked and str(batch_id) not in referrer_unlocked:
        referrer_unlocked.append(batch_id)
        DB["USER_DATA"][referrer_key]["referral_count"] = DB["USER_DATA"][referrer_key].get("referral_count", 0) + 1

    await save_data_async()

    batch_name = DB.get("ALL_CHATS", {}).get(batch_id) or DB.get("SPECIAL_CHANNELS", {}).get(batch_id) or DB.get("PAID_CHANNELS", {}).get(batch_id) or f"Special Batch {batch_id}"

    try:
        link_referee = await client.create_chat_invite_link(
            chat_id=int(batch_id),
            creates_join_request=False,
            name=f"Ref-Referee-{referee_id}",
            member_limit=1
        )
        link_referrer = await client.create_chat_invite_link(
            chat_id=int(batch_id),
            creates_join_request=False,
            name=f"Ref-Referrer-{referrer_id}",
            member_limit=1
        )

        kb_referee = InlineKeyboardMarkup([[InlineKeyboardButton("🚀 Join Special Batch", url=link_referee.invite_link)]])
        await client.send_message(
            referee_id,
            f"🎉 **CONGRATULATIONS!**\n\nAapne saare steps (Mandatory channel & Terms) complete kar liye hain!\nAb aap **1 Special Batch ({batch_name})** join kar sakte hain.\n\nNeeche diye gaye button se batch join karein:",
            reply_markup=kb_referee,
            parse_mode=ParseMode.MARKDOWN
        )

        kb_referrer = InlineKeyboardMarkup([[InlineKeyboardButton("🎁 Claim Unlocked Batch", url=link_referrer.invite_link)]])
        await client.send_message(
            referrer_id,
            f"🔥 **REFERRAL SUCCESSFUL!**\n\n**{referee.first_name}** ne saare steps complete kar liye hain!\n\nAap dono ke liye **{batch_name}** unlock ho gaya hai. Join karne ke liye button par click karein:",
            reply_markup=kb_referrer,
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        logger.error(f"Referral Invite Link Error: {e}")
        clean_id = str(batch_id).replace("-100", "")
        direct_url = f"https://t.me/c/{clean_id}/1"
        kb_fallback = InlineKeyboardMarkup([[InlineKeyboardButton("🚀 Open Special Batch", url=direct_url)]])
        await client.send_message(
            referee_id,
            f"🎉 **CONGRATULATIONS!**\n\nAapne saare steps complete kar liye hain! Aapka **{batch_name}** unlock ho gaya hai.",
            reply_markup=kb_fallback,
            parse_mode=ParseMode.MARKDOWN
        )
        await client.send_message(
            referrer_id,
            f"🎉 **REFERRAL SUCCESSFUL!** **{referee.first_name}** ne join kiya! Aapka **{batch_name}** unlock ho gaya hai.",
            reply_markup=kb_fallback,
            parse_mode=ParseMode.MARKDOWN
        )

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

async def cmd_userbotphone(client: Client, message: Message):
    if not is_owner_msg(message):
        return
    uid = message.from_user.id
    if not API_ID or API_ID == 0:
        return await message.reply_text(
            "  **API_ID Missing!** Kripya Cloud Dashboard me API_ID theek karein.",
            parse_mode=ParseMode.MARKDOWN,
        )
    phone = message.text.split(" ", 1)[-1].replace(" ", "").strip()
    msg = await message.reply_text("  OTP request bhej raha hu, kripya wait karein...")
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
            "  **OTP Bhej diya gaya hai!**\n\nKripya apna OTP yahan type karein.\n**DHYAN DEIN:** OTP space lagakar likhein (Example: `1 2 3 4 5`)",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        await temp_client.disconnect()
        await msg.edit_text(f"  Error: `{e}`", parse_mode=ParseMode.MARKDOWN)

async def cmd_userbototp(client: Client, message: Message):
    if not is_owner_msg(message):
        return
    uid = message.from_user.id
    otp = message.text.split(" ", 1)[-1].replace(" ", "").replace("-", "").strip()
    user_store = getattr(client, "user_data_store", {})
    phone = user_store.get("login_phone")
    phone_code_hash = user_store.get("phone_code_hash")
    temp_client = user_store.get("login_client")
    if not phone or not phone_code_hash or not temp_client:
        return await message.reply_text("  Session expire ho gaya. Kripya wapas login par click karein.")
    msg = await message.reply_text("  OTP Verify kar raha hu...")
    try:
        await temp_client.sign_in(phone, phone_code_hash, otp)
        session_string = await temp_client.export_session_string()
        DB["USERBOT_SESSION"] = session_string
        DB["USERBOT_PHONE"] = phone
        await save_data_async()
        await temp_client.disconnect()
        user_store.pop("login_client", None)
        user_store.pop("login_phone", None)
        user_store.pop("phone_code_hash", None)
        await msg.edit_text(
            "  **LOGIN SUCCESSFUL!**\n\nBina 2FA ke Session Database me save ho gaya hai.",
            parse_mode=ParseMode.MARKDOWN,
        )
    except SessionPasswordNeeded:
        ADMIN_WIZARD[uid] = {"step": "call_cmd_userbotpass"}
        await msg.edit_text(
            "  **2-Step Verification Detected!**\n\nIs account me 2FA on hai. Kripya apna **2FA Password** type karein:",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        await temp_client.disconnect()
        user_store.pop("login_client", None)
        await msg.edit_text(f"  OTP Error: `{e}`", parse_mode=ParseMode.MARKDOWN)

async def cmd_userbotpass(client: Client, message: Message):
    if not is_owner_msg(message):
        return
    uid = message.from_user.id
    password = message.text.split(" ", 1)[-1].strip()
    user_store = getattr(client, "user_data_store", {})
    phone = user_store.get("login_phone")
    temp_client = user_store.get("login_client")
    if not phone or not temp_client:
        return await message.reply_text("  Session expire ho gaya. Kripya wapas login par click karein.")
    msg = await message.reply_text("  Password check kar raha hu...")
    try:
        await temp_client.check_password(password)
        session_string = await temp_client.export_session_string()
        DB["USERBOT_SESSION"] = session_string
        DB["USERBOT_PHONE"] = phone
        await save_data_async()
        await temp_client.disconnect()
        user_store.pop("login_client", None)
        user_store.pop("login_phone", None)
        await msg.edit_text(
            "  **LOGIN SUCCESSFUL!**\n\n2FA Password verified. Session save ho gaya hai.",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        await temp_client.disconnect()
        user_store.pop("login_client", None)
        await msg.edit_text(f"  Password Error: `{e}`", parse_mode=ParseMode.MARKDOWN)

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
        [InlineKeyboardButton(c, callback_data=f"setextcat_{i}")]
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
        [InlineKeyboardButton(f"  Delete: {c}", callback_data=f"delcat_{i}")]
        for i, c in enumerate(DB.get("CATEGORIES", DEFAULT_CATEGORIES))
        if c != "Other Batches"
    ]
    kb.append([InlineKeyboardButton("  Cancel", callback_data="dash_home")])
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
    
    txt = (
        f"👤 **MY INFO**\n"
        f"🆔 **ID:** `{uid}`\n"
        f"🎁 **Refer Points:** `{refer_points}`\n"
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
        row = [InlineKeyboardButton(categories[i], callback_data=f"wcat_{i}")]
        if i + 1 < len(categories):
            row.append(
                InlineKeyboardButton(categories[i + 1], callback_data=f"wcat_{i+1}")
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
                InlineKeyboardButton("Free", callback_data="wiz_free"),
                InlineKeyboardButton("Paid", callback_data="wiz_paid"),
                InlineKeyboardButton("Special", callback_data="wiz_special"),
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

            await message.reply_text(
                f"  **Added!**\nName: {cname} ({cid})\nType: {state['type'].upper()}\nCategory: {state['category']}",
                parse_mode=ParseMode.MARKDOWN,
            )

            if state["type"] in ["free", "special"]:
                b_count = 0
                msg_header = "<b>NEW FREE BATCH ADDED!</b>" if state["type"] == "free" else "<b>NEW SPECIAL BATCH ADDED!</b>"
                msg_body = "Join via Bot Menu!" if state["type"] == "free" else "Unlock via Referral in Bot Menu!"

                await message.reply_text(
                    f"  Sending Auto-Broadcast for {state['type'].upper()} batch...", parse_mode=ParseMode.MARKDOWN
                )
                for t_cid in list(DB.get("ALL_CHATS", {}).keys()):
                    if t_cid != cid:
                        try:
                            sent_msg = await client.send_message(
                                int(t_cid),
                                f"  {msg_header}\n  Name: {cname}\n  {msg_body}",
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
                await message.reply_text(f"  Broadcast sent to {b_count} chats.")
                await save_data_async()
            del ADMIN_WIZARD[uid]
        except Exception as e:
            await message.reply_text(f"  Error: Ensure valid ID ({e}).")
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
            InlineKeyboardButton("  YES", callback_data="bc_yes"),
            InlineKeyboardButton("  NO", callback_data="bc_no"),
        ]]
        await message.reply_text(
            "  **Confirm?**",
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
                        f"System Lockdown: {'  ON' if not DB.get('NEW_USERS_ALLOWED', True) else '  OFF'}",
                        callback_data="toggle_lockdown",
                    )
                ],
                [
                    InlineKeyboardButton(
                        f"Free Batches: {'  LOCKED' if DB.get('FREE_LOCKED', False) else '  OPEN'}",
                        callback_data="toggle_free",
                    ),
                    InlineKeyboardButton(
                        f"Paid Batches: {'  LOCKED' if DB.get('PAID_LOCKED', False) else '  OPEN'}",
                        callback_data="toggle_paid",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        f"Test Bot: {'  LOCKED' if DB.get('TEST_BOT_LOCKED', False) else '  OPEN'}",
                        callback_data="toggle_testbot",
                    )
                ],
                [
                    InlineKeyboardButton(
                        f"Maintenance Mode: {'  ON' if DB.get('MAINTENANCE_MODE', False) else '  OFF'}",
                        callback_data="toggle_maintenance",
                    )
                ],
                [InlineKeyboardButton("  Back to Terminal", callback_data="dash_home")],
            ]
            await q.edit_message_text(
                "  **Security & Access Control**",
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
                        f"System Lockdown: {'  ON' if not DB.get('NEW_USERS_ALLOWED', True) else '  OFF'}",
                        callback_data="toggle_lockdown",
                    )
                ],
                [
                    InlineKeyboardButton(
                        f"Free Batches: {'  LOCKED' if DB.get('FREE_LOCKED', False) else '  OPEN'}",
                        callback_data="toggle_free",
                    ),
                    InlineKeyboardButton(
                        f"Paid Batches: {'  LOCKED' if DB.get('PAID_LOCKED', False) else '  OPEN'}",
                        callback_data="toggle_paid",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        f"Test Bot: {'  LOCKED' if DB.get('TEST_BOT_LOCKED', False) else '  OPEN'}",
                        callback_data="toggle_testbot",
                    )
                ],
                [
                    InlineKeyboardButton(
                        f"Maintenance Mode: {'  ON' if DB.get('MAINTENANCE_MODE', False) else '  OFF'}",
                        callback_data="toggle_maintenance",
                    )
                ],
                [InlineKeyboardButton("  Back to Terminal", callback_data="dash_home")],
            ]
            await q.edit_message_text(
                "  **Security & Access Control**",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.MARKDOWN,
            )
        elif data == "dash_db":
            await q.answer()
            kb = [
                [
                    InlineKeyboardButton("  Download Backup", callback_data="act_backup"),
                    InlineKeyboardButton("  Run Sync", callback_data="act_sync"),
                ],
                [InlineKeyboardButton("  Download All Users List", callback_data="act_allusers")],
                [InlineKeyboardButton("  Back", callback_data="dash_home")],
            ]
            await q.edit_message_text(
                "  **Database Tools**",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.MARKDOWN,
            )
        elif data in ["dash_batches", "adash_batches"]:
            await q.answer()
            kb = [
                [
                    InlineKeyboardButton("  Add Batch", callback_data="act_addbatch"),
                    InlineKeyboardButton("  Delete Batch", callback_data="input_delbatch"),
                ],
                [
                    InlineKeyboardButton("  Add Category", callback_data="input_addcat"),
                    InlineKeyboardButton("  Delete Category", callback_data="act_delcat"),
                ],
                [
                    InlineKeyboardButton("  Set Batch Category", callback_data="input_setcat"),
                    InlineKeyboardButton("  Empty Batch", callback_data="input_emptybatch"),
                ],
                [InlineKeyboardButton("  Batch Stats", callback_data="act_batchstats")],
                [InlineKeyboardButton("  Back", callback_data="dash_home")],
            ]
            await q.edit_message_text(
                "  **Batches Management**",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.MARKDOWN,
            )
        elif data == "dash_staff":
            await q.answer()
            kb = [
                [
                    InlineKeyboardButton("  Add Admin", callback_data="input_addadmin"),
                    InlineKeyboardButton("  Remove Admin", callback_data="input_deladmin"),
                ],
                [InlineKeyboardButton("  Back", callback_data="dash_home")],
            ]
            await q.edit_message_text(
                "  **Staff Management**",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.MARKDOWN,
            )
        elif data in ["dash_comms", "adash_comms"]:
            await q.answer()
            kb = [
                [
                    InlineKeyboardButton("  Broadcast", callback_data="act_broadcast"),
                    InlineKeyboardButton("  Post Message", callback_data="act_post"),
                ],
                [
                    InlineKeyboardButton("  Set Test Bot", callback_data="input_settestbot"),
                    InlineKeyboardButton("  Set Welcome", callback_data="input_setwelcome"),
                ],
                [InlineKeyboardButton("  Back", callback_data="dash_home")],
            ]
            await q.edit_message_text(
                "  **Communications**",
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
                    [InlineKeyboardButton("  Logout (Delete Session)", callback_data="userbot_logout")],
                    [InlineKeyboardButton("  Back", callback_data="dash_home")],
                ]
            else:
                text = (
                    "  **USERBOT CONTROL PANEL**\n\n  **Status:**   NOT LOGGED IN\n\n"
                    "*Koi active session nahi hai. Userbot features won't work. Kripya login karein.*"
                )
                kb = [
                    [InlineKeyboardButton("  Login Now", callback_data="input_userbotphone")],
                    [InlineKeyboardButton("  Back", callback_data="dash_home")],
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
                    [InlineKeyboardButton("  Back", callback_data="dash_home")]
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
                "userbotphone": "  **Apna Phone Number bhejein**\nCountry code ke sath (Jaise: `+919876543210`):",
                "userbototp": "  **OTP Bhejein**\n  *OTP spaces me bhejein!* Jaise: `1 2 3 4 5`:",
                "userbotpass": "  **2FA Password bhejein:**",
            }
            await q.edit_message_text(
                f"  **INPUT REQUIRED FOR: {cmd_name.upper()}**\n\n{prompts.get(cmd_name, 'Send input:')}",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("  Cancel Input", callback_data="dash_home")
                ]]),
                parse_mode=ParseMode.MARKDOWN,
            )
        elif data == "dash_stats":
            await q.answer("Generating Stats...")
            await cmd_stats(client, q.message)
        elif data == "adash_users":
            await q.answer()
            kb = [
                [
                    InlineKeyboardButton("  Ban", callback_data="input_ban"),
                    InlineKeyboardButton("  Unban", callback_data="input_unban"),
                ],
                [
                    InlineKeyboardButton("  Kick", callback_data="input_kick"),
                    InlineKeyboardButton("  Find", callback_data="input_find"),
                ],
                [InlineKeyboardButton("  Reset User Data", callback_data="input_resetuser")],
                [InlineKeyboardButton("  Back", callback_data="dash_home")],
            ]
            await q.edit_message_text(
                "  **User Management**",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.MARKDOWN,
            )
        elif data == "adash_approvals":
            await q.answer()
            kb = [
                [
                    InlineKeyboardButton("  Approve Demo", callback_data="input_demo"),
                    InlineKeyboardButton("  Approve Perm", callback_data="input_perm"),
                ],
                [InlineKeyboardButton("  Extend Demo Time", callback_data="input_extend")],
                [InlineKeyboardButton("  Back", callback_data="dash_home")],
            ]
            await q.edit_message_text(
                "  **Access Approvals**",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.MARKDOWN,
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
                pref = DB["USER_DATA"][user_key].pop("pending_referral")
                await save_data_async()
                await handle_referral_redemption(client, q.from_user, pref["referrer_id"], pref["batch_id"])

            await show_user_menu_cb(client, q)

        elif data == "u_main":
            await q.answer()
            await show_user_menu_cb(client, q)

        # --- MY INFO VIEW ---
        elif data == "my_info":
            await q.answer()
            user_key = uid if uid in DB["USER_DATA"] else str(uid)
            refer_points = DB["USER_DATA"].get(user_key, {}).get("referral_count", 0)

            txt = (
                f"👤 **MY INFO**\n"
                f"🆔 **ID:** `{uid}`\n"
                f"🎁 **Refer Points:** `{refer_points}`\n\n"
                f"💡 *Aapke refer kiye gaye dost jab bot use karke Special Batch unlock karenge, tab aapke points badhenge.*"
            )
            kb = [[InlineKeyboardButton("🔙 Back", callback_data="u_main")]]
            await q.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

        # --- HOME MENU: REFER & JOIN LIST ---
        elif data == "menu_refer":
            await q.answer()
            special_batches = DB.get("SPECIAL_CHANNELS", {})
            if not special_batches:
                return await q.edit_message_text(
                    "❌ Abhi koi Special Batch available nahi hai jise aap refer kar sakein.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="u_main")]]),
                    parse_mode=ParseMode.MARKDOWN
                )

            kb = []
            for cid, name in special_batches.items():
                kb.append([InlineKeyboardButton(f"✨ {name}", callback_data=f"view_s_{cid}")])

            kb.append([InlineKeyboardButton("🔙 Main Menu", callback_data="u_main")])

            await q.edit_message_text(
                "🎁 **REFER & JOIN SPECIAL BATCHES**\n\n"
                "Neeche diye gaye kisi bhi Special Batch ko select karein aur apna Referral Link generate karein. "
                "Apne dosto ko invite karein aur dono ke liye batch FREE me unlock karein!",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.MARKDOWN
            )

        # --- MANDATORY CHANNEL VERIFICATION & REFERRAL PROCESSOR ---
        elif data == "verify":
            if await check_membership_pyro(uid, client):
                await q.answer("  Verification Successful!", show_alert=True)
                user_key = uid if uid in DB["USER_DATA"] else str(uid)
                if not DB["USER_DATA"].get(user_key, {}).get("tnc_accepted", False):
                    await show_tnc_menu_cb(client, q)
                else:
                    if "pending_referral" in DB["USER_DATA"][user_key]:
                        pref = DB["USER_DATA"][user_key].pop("pending_referral")
                        await save_data_async()
                        await handle_referral_redemption(client, q.from_user, pref["referrer_id"], pref["batch_id"])
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
            kb = [[InlineKeyboardButton("  Open Test Bot", url=DB.get("TEST_BOT_LINK"))]]
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

        # --- REFERRAL GENERATOR FOR BATCHES ---
        elif data.startswith("gen_ref_"):
            batch_id = int(data.split("_")[2])
            batch_name = DB.get("ALL_CHATS", {}).get(batch_id) or DB.get("SPECIAL_CHANNELS", {}).get(batch_id) or f"Batch {batch_id}"
            
            # 🔥 Hardcoded your exact bot username
            bot_username = "H4R_Contact_bot"
            
            ref_link = f"https://t.me/{bot_username}?start=ref_{batch_id}_{uid}"
            share_text = f"🎁 Get FREE access to {batch_name}!\n\nClick the link below to start the bot. We BOTH will instantly unlock lifetime access to this batch! 🚀"
            share_url = f"https://t.me/share/url?url={ref_link}&text={share_text}"

            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("📲 Share Link with Friend", url=share_url)],
                [InlineKeyboardButton("📋 Copy Link", callback_data=f"copy_ref_{batch_id}")],
                [InlineKeyboardButton("🔙 Back", callback_data="u_main")]
            ])

            await q.edit_message_text(
                f"🎯 **REFERRAL LINK FOR SPECIAL BATCH**\n\n"
                f"📌 **Batch:** `{batch_name}`\n\n"
                f"🔗 **Your Referral Link:**\n`{ref_link}`\n\n"
                f"💡 **Kaise kaam karta hai?**\n"
                f"1️⃣ Upar diye gaye **'Share Link'** button par click karke kisi dost ko bhejein.\n"
                f"2️⃣ Jaise hi wo mandatory channel join karke TnC accept karega, aap **dono** ke liye ye batch **FREE** me unlock ho jayega!",
                reply_markup=kb,
                parse_mode=ParseMode.MARKDOWN
            )

        elif data.startswith("copy_ref_"):
            batch_id = int(data.split("_")[2])
            
            # 🔥 Hardcoded your exact bot username here too
            bot_username = "H4R_Contact_bot"
            
            ref_link = f"https://t.me/{bot_username}?start=ref_{batch_id}_{uid}"
            await q.answer(f"Copied: {ref_link}", show_alert=True)

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
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("  Back to Menu", callback_data="u_main")]]),
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
                nav_buttons.append(InlineKeyboardButton("  Back", callback_data=f"my_batches_{page-1}"))
            if end_idx < total_batches:
                nav_buttons.append(InlineKeyboardButton("Next  ", callback_data=f"my_batches_{page+1}"))

            if nav_buttons:
                kb.append(nav_buttons)
            kb.append([InlineKeyboardButton("  Main Menu", callback_data="u_main")])

            await q.edit_message_text(
                f"  **My Batches (Page {page+1})**\n\nYahan wo sabhi batches hain jisme aap join hain ya unlocked hain. Click karke access karein:",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.MARKDOWN,
            )

        elif data.startswith("all_batches_"):
            await q.answer()
            kb = [
                [InlineKeyboardButton(cat, callback_data=f"showcat_{i}")]
                for i, cat in enumerate(DB.get("CATEGORIES", DEFAULT_CATEGORIES))
            ] + [
                [InlineKeyboardButton("  Main Menu", callback_data="u_main")]
            ]
            await q.edit_message_text(
                "  **All Batches - Select Category:**",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.MARKDOWN,
            )

        # --- CATEGORY DETAILS WITH SPECIAL BATCHES BUTTON ---
        elif data.startswith("showcat_"):
            await q.answer()
            cat_idx = int(data.split("_")[1])
            kb = [
                [
                    InlineKeyboardButton("  Free Batches", callback_data=f"listcat_{cat_idx}_free_0"),
                    InlineKeyboardButton("  Paid Batches", callback_data=f"listcat_{cat_idx}_paid_0"),
                ],
                [
                    InlineKeyboardButton("✨ Special Batches", callback_data=f"listcat_{cat_idx}_special_0"),
                ],
                [
                    InlineKeyboardButton("  Back to Categories", callback_data="all_batches_0")
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
                        InlineKeyboardButton("  Back", callback_data=f"showcat_{cat_idx}")
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
                    InlineKeyboardButton("  Back", callback_data=f"listcat_{cat_idx}_{b_type}_{page-1}")
                )
            if end_idx < total:
                nav_buttons.append(
                    InlineKeyboardButton("Next  ", callback_data=f"listcat_{cat_idx}_{b_type}_{page+1}")
                )
            if nav_buttons:
                kb.append(nav_buttons)
            kb.append([InlineKeyboardButton("  Category Menu", callback_data=f"showcat_{cat_idx}")])

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

        elif data.startswith("get_f_"):
            cid = int(data.split("_")[2])
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
                kb = [[InlineKeyboardButton("  Join Batch", url=l.invite_link)]]
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

        elif data.startswith("view_p_"):
            cid = int(data.split("_")[2])
            if DB.get("PAID_LOCKED", False):
                return await q.answer("Sorry, but at this moment the paid batch is locked. When it will unlock I will inform you.", show_alert=True)
            await q.answer()
            kb = [
                [InlineKeyboardButton("  Request Access", callback_data=f"req_access_{cid}")],
                [InlineKeyboardButton("  Back", callback_data="u_main")],
            ]
            try:
                await q.edit_message_text(
                    "  **Premium Access:**\nClick below.",
                    reply_markup=InlineKeyboardMarkup(kb),
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception:
                pass

        # --- VIEW SPECIAL BATCH DETAILS ---
        elif data.startswith("view_s_"):
            cid = int(data.split("_")[2])
            await q.answer()
            bname = DB.get("ALL_CHATS", {}).get(cid) or DB.get("SPECIAL_CHANNELS", {}).get(cid) or f"Special Batch {cid}"
            user_key = uid if uid in DB["USER_DATA"] else str(uid)
            unlocked_list = DB["USER_DATA"].get(user_key, {}).get("unlocked_batches", [])
            is_unlocked = cid in unlocked_list or str(cid) in unlocked_list

            kb = []
            if is_unlocked:
                clean_id = str(cid).replace('-100', '')
                kb.append([InlineKeyboardButton("🚀 Join Special Batch", url=f"https://t.me/c/{clean_id}/1")])
            else:
                kb.append([InlineKeyboardButton("🎁 Refer 1 Friend to Unlock FREE", callback_data=f"gen_ref_{cid}")])

            kb.append([InlineKeyboardButton("🔙 Back", callback_data="u_main")])

            status_str = "🎉 Unlocked!" if is_unlocked else "🔒 Locked (Requires 1 Referral)"
            await q.edit_message_text(
                f"✨ **SPECIAL BATCH:** `{bname}`\n\n"
                f"**Status:** `{status_str}`\n\n"
                f"Is batch ko unlock karne ke liye apne 1 dost ko bot par refer karein. Jaise hi wo mandatory channel join karke TnC accept karega, aap dono ka batch unlock ho jayega!",
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
                    notification_text = (
                        f"  <b>NEW REQUEST</b>\n"
                        f"  User: {q.from_user.mention}\n"
                        f"  Batch: <b>{bname}</b>\n"
                        f"  Link: {l.invite_link}\n\n"
                        f"  <b>Action:</b>\n"
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

                kb = [[InlineKeyboardButton("  Request Access", url=l.invite_link)]]
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
    kb = [[InlineKeyboardButton("  I Read & Accept", callback_data="accept_tnc")]]
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
    kb = [[InlineKeyboardButton("  I Read & Accept", callback_data="accept_tnc")]]
    txt = (
        "  **STRICT WARNING & TERMS OF SERVICE**  \n\n"
        "  **ENGLISH:**\n"
        "If you leave the Main Channel or block this bot, you will be **INSTANTLY REMOVED** from ALL joined groups and channels.\n\n"
        "  **HINDI:**\n"
        "Agar aapne Main Channel ko chhoda (leave kiya) ya is bot ko block kiya, toh aapko sabhi groups aur channels se **TURANT NIKAL** diya jayega.\n\n"
        "  *Click 'I Read & Accept' only if you agree to these terms.*"
    )
    await q.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

async def show_user_menu(client: Client, message: Message):
    kb = [
        [
            InlineKeyboardButton("📚 My Batches", callback_data="my_batches_0"),
            InlineKeyboardButton("🌐 All Batches", callback_data="all_batches_0"),
        ],
        [InlineKeyboardButton("🤖 Test Bot", callback_data="test_bot")],
        [InlineKeyboardButton("🎥 How to use the bot", url="https://t.me/c/2836314734/1244")],
        [InlineKeyboardButton("🎁 Refer & Join", callback_data="menu_refer")],
        [InlineKeyboardButton("ℹ️ My Info", callback_data="my_info")],
    ]
    txt = (
        "🌟 **Welcome to the Premium Hub!** 🌟\nYour centralized portal for exclusive communities.\n\n👇 *Select an option below:*"
    )
    await message.reply_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

async def show_user_menu_cb(client: Client, q: CallbackQuery):
    kb = [
        [
            InlineKeyboardButton("📚 My Batches", callback_data="my_batches_0"),
            InlineKeyboardButton("🌐 All Batches", callback_data="all_batches_0"),
        ],
        [InlineKeyboardButton("🤖 Test Bot", callback_data="test_bot")],
        [InlineKeyboardButton("🎥 How to use the bot", url="https://t.me/c/2836314734/1244")],
        [InlineKeyboardButton("🎁 Refer & Join", callback_data="menu_refer")],
        [InlineKeyboardButton("ℹ️ My Info", callback_data="my_info")],
    ]
    txt = (
        "🌟 **Welcome to the Premium Hub!** 🌟\nYour centralized portal for exclusive communities.\n\n👇 *Select an option below:*"
    )
    await q.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

async def start(client: Client, message: Message):
    user = message.from_user
    await set_role_based_commands(user.id, client)
    
    # 🔥 FIX: Properly handle Integer/String ID matching for new users
    user_key = user.id if user.id in DB.get("USER_DATA", {}) else (str(user.id) if str(user.id) in DB.get("USER_DATA", {}) else user.id)
    
    if user_key not in DB["USER_DATA"]:
        DB["USER_DATA"][user_key] = {
            "name": f"{user.first_name or ''} {user.last_name or ''}".strip(),
            "username": user.username,
            "joined_at": time.time(),
            "demos": {},
            "tnc_accepted": False,
            "unlocked_batches": [],
            "referral_count": 0
        }
        await save_data_async()

    DB["USER_DATA"][user_key].setdefault("unlocked_batches", [])

    # --- CHECK REFERRAL DEEP LINK PARAMETER ---
    args = get_args(message)
    if args and args[0].startswith("ref_"):
        try:
            parts = args[0].split("_")
            if len(parts) >= 3:
                batch_id = int(parts[1])
                referrer_id = int(parts[2])
                # Store referral as pending (User must join channel + accept TnC first)
                DB["USER_DATA"][user_key]["pending_referral"] = {
                    "batch_id": batch_id,
                    "referrer_id": referrer_id
                }
                await save_data_async()
        except Exception as e:
            logger.error(f"Referral parsing error: {e}")

    await get_or_create_topic(user, client)

    if str(user.id) == str(OWNER_ID):
        kb = [
            [
                InlineKeyboardButton("  Security", callback_data="dash_locks"),
                InlineKeyboardButton("  Database", callback_data="dash_db"),
            ],
            [
                InlineKeyboardButton("  Batches", callback_data="dash_batches"),
                InlineKeyboardButton("  Staff", callback_data="dash_staff"),
            ],
            [
                InlineKeyboardButton("  Comms", callback_data="dash_comms"),
                InlineKeyboardButton("  Analytics", callback_data="dash_stats"),
            ],
            [InlineKeyboardButton("  Userbot Login & Stats", callback_data="userbot_details")],
        ]
        text = "  **SYSTEM MASTER TERMINAL**\nSelect a module:"
        await message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
    elif is_admin(user.id):
        kb = [
            [
                InlineKeyboardButton("  Users", callback_data="adash_users"),
                InlineKeyboardButton("  Approvals", callback_data="adash_approvals"),
            ],
            [
                InlineKeyboardButton("  Batches", callback_data="adash_batches"),
                InlineKeyboardButton("  Comms", callback_data="adash_comms"),
            ],
        ]
        text = "  **ADMINISTRATOR DASHBOARD**\nSelect an action:"
        await message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
    elif await check_membership_pyro(user.id, client):
        if not DB["USER_DATA"].get(user_key, {}).get("tnc_accepted", False):
            await show_tnc_menu(client, message)
        else:
            # Check pending referral fulfillment
            if "pending_referral" in DB["USER_DATA"][user_key]:
                pref = DB["USER_DATA"][user_key].pop("pending_referral")
                await save_data_async()
                await handle_referral_redemption(client, user, pref["referrer_id"], pref["batch_id"])

            await show_user_menu(client, message)
    else:
        if not DB.get("NEW_USERS_ALLOWED", True):
            return await message.reply_text("  **Entry Closed!**", parse_mode=ParseMode.MARKDOWN)
        kb = [
            [InlineKeyboardButton("  Join Channel", url=MANDATORY_CHANNEL_LINK)],
            [InlineKeyboardButton("  Verified", callback_data="verify")],
        ]
        await message.reply_text(
            "  **Join Main Channel First**\n\nTo access batches and unlock referral rewards, please join our mandatory channel first.",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode=ParseMode.MARKDOWN,
        )

async def start_from_cb(client: Client, q: CallbackQuery):
    user = q.from_user
    user_key = user.id if user.id in DB["USER_DATA"] else str(user.id)
    await set_role_based_commands(user.id, client)
    if str(user.id) == str(OWNER_ID):
        kb = [
            [
                InlineKeyboardButton("  Security", callback_data="dash_locks"),
                InlineKeyboardButton("  Database", callback_data="dash_db"),
            ],
            [
                InlineKeyboardButton("  Batches", callback_data="dash_batches"),
                InlineKeyboardButton("  Staff", callback_data="dash_staff"),
            ],
            [
                InlineKeyboardButton("  Comms", callback_data="dash_comms"),
                InlineKeyboardButton("  Analytics", callback_data="dash_stats"),
            ],
            [InlineKeyboardButton("  Userbot Login & Stats", callback_data="userbot_details")],
        ]
        text = "  **SYSTEM MASTER TERMINAL**\nSelect a module:"
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
    elif is_admin(user.id):
        kb = [
            [
                InlineKeyboardButton("  Users", callback_data="adash_users"),
                InlineKeyboardButton("  Approvals", callback_data="adash_approvals"),
            ],
            [
                InlineKeyboardButton("  Batches", callback_data="adash_batches"),
                InlineKeyboardButton("  Comms", callback_data="adash_comms"),
            ],
        ]
        text = "  **ADMINISTRATOR DASHBOARD**\nSelect an action:"
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
    elif await check_membership_pyro(user.id, client):
        if not DB["USER_DATA"].get(user_key, {}).get("tnc_accepted", False):
            await show_tnc_menu_cb(client, q)
        else:
            if "pending_referral" in DB["USER_DATA"].get(user_key, {}):
                pref = DB["USER_DATA"][user_key].pop("pending_referral")
                await save_data_async()
                await handle_referral_redemption(client, user, pref["referrer_id"], pref["batch_id"])
            await show_user_menu_cb(client, q)
    else:
        if not DB.get("NEW_USERS_ALLOWED", True):
            return await q.edit_message_text("  **Entry Closed!**", parse_mode=ParseMode.MARKDOWN)
        kb = [
            [InlineKeyboardButton("  Join Channel", url=MANDATORY_CHANNEL_LINK)],
            [InlineKeyboardButton("  Verified", callback_data="verify")],
        ]
        await q.edit_message_text(
            "  **Join Main Channel First**",
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
# 2-WAY REACTION SYNC ENGINE
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
        emoji = None
        if new_reactions:
            emoji = getattr(new_reactions[0], "emoji", None)
            if not emoji:
                return

        await client.send_reaction(target_chat, target_msg, emoji=emoji)
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
            return await message.reply_text("  **Under Maintenance.**")
        
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
    if not update.new_chat_member:
        return
        
    user = update.new_chat_member.user
    status = update.new_chat_member.status
    
    if str(update.chat.id).replace("-100", "") == str(
        MANDATORY_CHANNEL_ID
    ).replace("-100", "") and status in [
        ChatMemberStatus.LEFT,
        ChatMemberStatus.BANNED,
    ]:
        if user:
            await execute_universal_kick(user.id, client)

async def track_chats(client: Client, update: ChatMemberUpdated):
    chat = update.chat
    
    if not update.new_chat_member:
        return
        
    status = update.new_chat_member.status
    if (
        chat.type == ChatType.PRIVATE and status == ChatMemberStatus.BANNED
    ):
        await execute_universal_kick(chat.id, client)

async def background_sync(client: Client):
    global SPAM_CACHE, _SYNC_IN_PROGRESS
    SPAM_CACHE = {k: v for k, v in SPAM_CACHE.items() if time.time() - v < 2.0}
    if len(MESSAGE_MAP) > 5000:
        MESSAGE_MAP.clear()

    if _SYNC_IN_PROGRESS:
        logger.warning("  Background sync already running. Skipping this cycle.")
        return

    _SYNC_IN_PROGRESS = True
    try:
        user_ids = list(DB["USER_DATA"].keys())
        for idx, uid in enumerate(user_ids):
            user_id = int(uid)
            if user_id in DB["BLOCKED_USERS"] or is_admin(user_id):
                continue
            try:
                m = await client.get_chat_member(int(MANDATORY_CHANNEL_ID), user_id)
                status = m.status
                if status == ChatMemberStatus.BANNED:
                    await execute_universal_kick(user_id, client)
                elif status == ChatMemberStatus.LEFT and DB["USER_DATA"].get(
                    uid, {}
                ).get("tnc_accepted", False):
                    await execute_universal_kick(user_id, client)
            except Exception:
                pass

            await asyncio.sleep(1.0)

            if idx > 0 and idx % 100 == 0:
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

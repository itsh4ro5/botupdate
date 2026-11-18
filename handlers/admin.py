# -*- coding: utf-8 -*-
import asyncio
from datetime import datetime, timedelta
import io
import os
import re
import traceback
import time
import urllib.parse
import logging
import json
import config
from config import *
import richpyro as rp
import pyrogram
from pyrogram import Client, filters
from pyrogram.enums import ButtonStyle, ChatMemberStatus, ChatType, ParseMode
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
    WebAppInfo,
)

from bot_api_bridge import (
    send_ephemeral_reply,
    send_html_widget,
    edit_widget_data,
    get_topic_deeplink,
    build_demands_console_card,
    build_giveaway_claim_card,
    build_interactive_broadcast_card,
    build_student_wallet_widget,
    build_batch_details_widget,
    build_leaderboard_widget,
    to_pyrogram_markup,
)

logger = logging.getLogger(__name__)
from .common import *
from .common import _SYNC_IN_PROGRESS, _BOT_SELF_ID, _patch_rich_text_classes

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


async def cmd_add_tester(client: Client, message: Message):
    if not is_owner_msg(message):
        return
    args = get_args(message)
    if not args:
        return await message.reply_text("🧪 Usage: /addtester <user_id>")
    try:
        new_tester = int(args[0])
        testers = DB.setdefault("TESTER_IDS", [])
        if new_tester not in testers:
            testers.append(new_tester)
            await save_data_async()
            await message.reply_text(f"✅ User `{new_tester}` added as Authorized Tester (Can bypass Maintenance Mode).", parse_mode=ParseMode.MARKDOWN)
        else:
            await message.reply_text(f"ℹ️ User `{new_tester}` is already an Authorized Tester.", parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await message.reply_text(f"❌ Error adding tester: {e}")


async def cmd_del_tester(client: Client, message: Message):
    if not is_owner_msg(message):
        return
    args = get_args(message)
    if not args:
        return await message.reply_text("🧪 Usage: /deltester <user_id>")
    try:
        target = int(args[0])
        testers = DB.setdefault("TESTER_IDS", [])
        if target in testers:
            testers.remove(target)
            await save_data_async()
            await message.reply_text(f"🗑️ User `{target}` removed from Authorized Testers.", parse_mode=ParseMode.MARKDOWN)
        else:
            await message.reply_text("ℹ️ User is not in Testers list.")
    except Exception as e:
        await message.reply_text(f"❌ Error removing tester: {e}")


async def cmd_testers(client: Client, message: Message):
    if not is_owner_msg(message):
        return
    testers = DB.get("TESTER_IDS", [])
    if not testers:
        return await message.reply_text("🧪 No authorized testers configured yet.\nUse `/addtester <user_id>` to add one.")
    t_list = "\n".join([f"• `{uid}`" for uid in testers])
    await message.reply_text(f"🧪 **Authorized Testers List ({len(testers)}):**\n\n{t_list}\n\n*Testers have full bypass access when Maintenance Mode is ON.*", parse_mode=ParseMode.MARKDOWN)


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
            "⚠️ Error: Userbot not logged in. Owner dashboard se login karein."
        )
    msg = await message.reply_text("⏳ **Auto-joining Userbot & Promoting...**")
    userbot = Client(
        "temp_join_bot",
        api_id=API_ID,
        api_hash=API_HASH,
        session_string=session_string,
        in_memory=True,
    )
    try:
        await userbot.start()
        userbot_me = await userbot.get_me()
        userbot_id = userbot_me.id
    except Exception as e:
        return await msg.edit_text(f"❌ Userbot connection failed: {e}")

    all_chats = (
        ([MANDATORY_CHANNEL_ID] if MANDATORY_CHANNEL_ID else [])
        + list(DB.get("FREE_CHANNELS", {}).keys())
        + list(DB.get("PAID_CHANNELS", {}).keys())
        + list(DB.get("SPECIAL_CHANNELS", {}).keys())
    )
    success = failed = 0
    full_privs = pyrogram.types.ChatPrivileges(
        can_manage_chat=True,
        can_post_messages=True,
        can_edit_messages=True,
        can_delete_messages=True,
        can_invite_users=True,
        can_restrict_members=True,
        can_pin_messages=True,
        can_promote_members=False
    )

    try:
        for cid in all_chats:
            try:
                chat_id_int = int(cid)
                # 1. Check if userbot already in channel
                is_member = False
                try:
                    m = await client.get_chat_member(chat_id_int, userbot_id)
                    is_member = m.status not in [ChatMemberStatus.LEFT, ChatMemberStatus.BANNED]
                except Exception:
                    is_member = False

                if not is_member:
                    # Create 1-time direct join link from bot and join via userbot
                    try:
                        inv = await client.create_chat_invite_link(chat_id_int, creates_join_request=False, member_limit=1)
                        await userbot.join_chat(inv.invite_link)
                    except Exception as join_err:
                        logger.warning(f"Userbot join failed for {cid}: {join_err}")

                # 2. Promote userbot with privileges
                await client.promote_chat_member(
                    chat_id=chat_id_int,
                    user_id=userbot_id,
                    privileges=full_privs
                )
                success += 1
                await asyncio.sleep(0.5)
            except FloodWait as fw:
                await asyncio.sleep(fw.value + 1)
            except Exception as e:
                logger.error(f"Failed to auto-join/promote in {cid}: {e}")
                failed += 1
    finally:
        try:
            await userbot.stop()
        except:
            pass

    await msg.edit_text(
        f"✅ **Auto-Join Process Complete!**\n━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"✨ **Success:** `{success}`\n"
        f"⚠️ **Failed:** `{failed}`",
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
        topic_link = f"https://t.me/c/{group_id_str}/{topic_id} | Bot Deep-Link: {get_topic_deeplink(topic_id)}"
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


async def cmd_kick_user(client: Client, message: Message):
    if not is_admin_msg(message):
        return
    args = get_args(message)
    if len(args) < 2:
        return await message.reply_text("Usage: /kick <user_id> <batch_id>", parse_mode=ParseMode.MARKDOWN)
    try:
        uid, bid = int(args[0]), int(args[1])
    except ValueError:
        return await message.reply_text("❌ Error: Valid User ID aur Batch ID bhejein.", parse_mode=ParseMode.MARKDOWN)
    try:
        await client.ban_chat_member(bid, uid)
        await client.unban_chat_member(bid, uid)
        user_key = uid if uid in DB["USER_DATA"] else (str(uid) if str(uid) in DB["USER_DATA"] else None)
        if user_key and str(bid) in DB["USER_DATA"].get(user_key, {}).get("demos", {}):
            del DB["USER_DATA"][user_key]["demos"][str(bid)]
            await save_data_async()
        await message.reply_text(f"✅ User `{uid}` kicked from batch `{bid}`.", parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await message.reply_text(f"❌ Kick failed: {e}")


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


async def auto_backup_db(client: Client):
    if LOG_CHANNEL_ID and os.path.exists(DATA_FILE):
        try:
            await client.send_document(
                int(LOG_CHANNEL_ID), document=DATA_FILE, caption="  Auto Backup"
            )
        except Exception:
            pass



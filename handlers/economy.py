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

    card = build_giveaway_claim_card(code, pts, max_uses, 0)
    pyro_kb = to_pyrogram_markup(card["reply_markup"])
    copy_btn = make_copy_button(f"📋 Copy: {code}", code)
    pyro_kb.inline_keyboard.append([
        copy_btn,
        InlineKeyboardButton("📢 Broadcast Giveaway", callback_data=f"bc_giveaway_{code}")
    ])

    try:
        widget_res = await send_html_widget(
            message.chat.id,
            card["text"],
            caption=f"🎁 <b>GIVEAWAY VOUCHER ACTIVE:</b> <code>{code}</code>",
            reply_markup=card["reply_markup"]
        )
        if not widget_res:
            await message.reply_text(card["text"], reply_markup=pyro_kb, parse_mode=ParseMode.HTML)
    except Exception:
        await message.reply_text(card["text"], reply_markup=pyro_kb, parse_mode=ParseMode.HTML)


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

    card = build_giveaway_claim_card(code, pts, max_uses, 0)
    pyro_kb = to_pyrogram_markup(card["reply_markup"])
    copy_btn = make_copy_button(f"📋 Copy: {code}", code)
    pyro_kb.inline_keyboard.append([
        copy_btn,
        InlineKeyboardButton("📢 Broadcast Giveaway", callback_data=f"bc_giveaway_{code}")
    ])

    try:
        widget_res = await send_html_widget(
            message.chat.id,
            card["text"],
            caption=f"🎁 <b>GIVEAWAY VOUCHER ACTIVE:</b> <code>{code}</code>",
            reply_markup=card["reply_markup"]
        )
        if not widget_res:
            await message.reply_text(card["text"], reply_markup=pyro_kb, parse_mode=ParseMode.HTML)
    except Exception:
        await message.reply_text(card["text"], reply_markup=pyro_kb, parse_mode=ParseMode.HTML)


async def cmd_myinfo(client: Client, message: Message):
    if not message.from_user:
        return
    uid = message.from_user.id
    user_key = uid if uid in DB["USER_DATA"] else str(uid)
    u_data = DB["USER_DATA"].get(user_key, {})
    widget = build_student_wallet_widget(uid, u_data)
    pm = to_pyrogram_markup(widget.get("reply_markup"))
    await message.reply_text(widget["text"], reply_markup=pm, parse_mode=ParseMode.HTML)


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



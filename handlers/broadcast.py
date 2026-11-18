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


async def get_batch_broadcast_kb(client: Client, cid: int, cname: str):
    import config
    import urllib.parse
    bot_username = getattr(config, 'BOT_USERNAME', None)
    if not bot_username:
        try:
            bot_username = (await client.get_me()).username
        except Exception:
            bot_username = "bot"
    deep_link = f"https://t.me/{bot_username}?start=batch_{cid}"
    share_text = f"🔥 Check out new batch: {cname} on our Bot!"
    share_url = f"https://t.me/share/url?url={urllib.parse.quote(deep_link)}&text={urllib.parse.quote(share_text)}"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 Get Access Here", url=deep_link, style=ButtonStyle.PRIMARY)],
        [InlineKeyboardButton("📢 Share Batch", url=share_url, style=ButtonStyle.SUCCESS)],
    ])


async def send_batch_update_post(client: Client, cid: int, cname: str, b_type: str, category: str, coin_cost: int = None, price: str = None):
    import config
    import os
    import urllib.parse
    update_channel = getattr(config, 'BATCH_UPDATE_CHANNEL_ID', 0) or config.DB.get('BATCH_UPDATE_CHANNEL_ID', 0) or -1004304703943

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
        [InlineKeyboardButton("🚀 Get Access Here", url=deep_link, style=ButtonStyle.PRIMARY)],
        [InlineKeyboardButton("📢 Share Batch", url=share_url, style=ButtonStyle.SUCCESS)],
    ]

    type_badge = b_type.upper()
    paid_price = price or DB.get("BATCH_PRICES", {}).get(str(cid))
    if b_type.lower() == 'special' and coin_cost:
        cost_line = f"\n💰 <b>Cost:</b> <code>{coin_cost} Coins</code>"
    elif b_type.lower() == 'paid' and paid_price:
        cost_line = f"\n💰 <b>Price:</b> <code>{paid_price}</code>"
    else:
        cost_line = ""

    caption = (
        "🎉 <b>NEW BATCH ADDED!</b>\n\n"
        f"📦 <b>Name:</b> <code>{cname}</code>\n"
        f"🏷️ <b>Type:</b> <code>{type_badge}</code>\n"
        f"📂 <b>Category:</b> <code>{category}</code>"
        f"{cost_line}\n\n"
        "👉 <i>Click the button below to get direct access via our Bot!</i>"
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
            notify_kb = [[InlineKeyboardButton("🚀 Open Your Requested Batch", url=deep_link, style=ButtonStyle.PRIMARY)]]
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



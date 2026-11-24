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

    # 1. Send to dedicated Update Channel (if configured)
    if update_channel:
        try:
            if photo_path and os.path.exists(photo_path):
                await client.send_photo(int(update_channel), photo=photo_path, caption=caption, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
            else:
                await client.send_message(int(update_channel), text=caption, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
        except Exception as e:
            logger.error(f"Failed to send to batch update channel: {e}")

    # 2. Send to all Supergroups' Announcements Topics (with 3-day auto-delete TTL)
    announcement_topics = DB.get("ANNOUNCEMENT_TOPICS", {})
    for g_cid_str, topic_id in list(announcement_topics.items()):
        try:
            g_cid = int(g_cid_str)
            if g_cid == cid:
                continue
            sent_ann = None
            if photo_path and os.path.exists(photo_path):
                sent_ann = await client.send_photo(
                    g_cid,
                    photo=photo_path,
                    caption=caption,
                    reply_markup=InlineKeyboardMarkup(kb),
                    parse_mode=ParseMode.HTML,
                    message_thread_id=int(topic_id)
                )
            else:
                sent_ann = await client.send_message(
                    g_cid,
                    text=caption,
                    reply_markup=InlineKeyboardMarkup(kb),
                    parse_mode=ParseMode.HTML,
                    message_thread_id=int(topic_id)
                )
            if sent_ann:
                DB.setdefault("SCHEDULED_DELETES", []).append({
                    "c": g_cid,
                    "m": sent_ann.id,
                    "t": time.time() + 259200,  # 3 days auto-delete
                })
            await asyncio.sleep(0.05)
        except Exception as e:
            logger.error(f"Failed to send announcement to group {g_cid_str} topic {topic_id}: {e}")

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
        if state["type"] == "post":
            kb = [
                [
                    InlineKeyboardButton("⏱️ 3 Hours", callback_data="post_ttl_3"),
                    InlineKeyboardButton("⏱️ 6 Hours", callback_data="post_ttl_6"),
                ],
                [
                    InlineKeyboardButton("⏱️ 12 Hours", callback_data="post_ttl_12"),
                    InlineKeyboardButton("⏱️ 24 Hours (1 Day)", callback_data="post_ttl_24"),
                ],
                [
                    InlineKeyboardButton("⏱️ 3 Days", callback_data="post_ttl_72"),
                    InlineKeyboardButton("⏱️ 7 Days (1 Wk)", callback_data="post_ttl_168"),
                ],
                [
                    InlineKeyboardButton("♾️ Never Delete (Permanent)", callback_data="post_ttl_0"),
                ],
                [
                    InlineKeyboardButton("❌ Cancel Post", callback_data="bc_no"),
                ],
            ]
            await message.reply_text(
                "📢 **Channel & Supergroup Post Configuration**\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "Yeh post sabhi channels aur supergroups ke **📢 Announcements** topic me publish hogi.\n\n"
                "⏳ **Auto-Delete Timer (Self-Destruct):**\n"
                "Aap is post ko kitne samay baad automatically delete karna chahte hain?",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.MARKDOWN,
            )
            return True
        else:
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
        return await q.edit_message_text("❌ Action Cancelled.")

    # ---------------------------------------------------------
    # DYNAMIC AUTO-DELETE CHANNELS & SUPERGROUPS POST FLOW
    # ---------------------------------------------------------
    if q.data.startswith("post_ttl_"):
        await q.answer()
        try:
            ttl_hours = int(q.data.split("_")[2])
        except Exception:
            ttl_hours = 0
        ttl_seconds = ttl_hours * 3600
        ttl_label = f"{ttl_hours} Hours" if ttl_hours in [3, 6, 12, 24] else (f"{ttl_hours // 24} Days" if ttl_hours > 24 else "Permanent (Never Delete)")

        await q.edit_message_text(f"⏳ **Publishing Post to Channels & Supergroups...**\n⏱️ Auto-Delete: `{ttl_label}`")

        # Collect unique target channels & supergroups
        target_dict = {}
        for col in ["FREE_CHANNELS", "PAID_CHANNELS", "SPECIAL_CHANNELS", "ALL_CHATS"]:
            for cid, name in DB.get(col, {}).items():
                try:
                    target_dict[int(cid)] = name
                except Exception:
                    pass

        targets = list(target_dict.keys())
        total_targets = len(targets)
        count = 0
        failed_count = 0
        scheduled_count = 0

        for index, tid in enumerate(targets):
            topic_id = DB.get("ANNOUNCEMENT_TOPICS", {}).get(str(tid))
            kwargs = {"message_thread_id": int(topic_id)} if topic_id else {}
            sent_msg = None
            try:
                sent_msg = await client.copy_message(
                    chat_id=int(tid),
                    from_chat_id=uid,
                    message_id=BROADCAST_STATE[uid]["content"].id,
                    **kwargs
                )
                count += 1
            except FloodWait as e:
                await asyncio.sleep(e.value + 1)
                try:
                    sent_msg = await client.copy_message(
                        chat_id=int(tid),
                        from_chat_id=uid,
                        message_id=BROADCAST_STATE[uid]["content"].id,
                        **kwargs
                    )
                    count += 1
                except Exception:
                    failed_count += 1
            except Exception as e:
                failed_count += 1
                logger.error(f"Post error in chat {tid}: {e}")

            if sent_msg and ttl_seconds > 0:
                DB.setdefault("SCHEDULED_DELETES", []).append({
                    "c": int(tid),
                    "m": sent_msg.id,
                    "t": time.time() + ttl_seconds,
                })
                scheduled_count += 1

            await asyncio.sleep(0.05)

            if index > 0 and index % 25 == 0:
                try:
                    await q.edit_message_text(
                        f"📡 **Posting In Progress...**\n\n"
                        f"📤 Sent: `{count} / {total_targets}`\n"
                        f"❌ Failed: `{failed_count}`\n"
                        f"⏱️ Self-Destruct: `{ttl_label}`"
                    )
                except Exception:
                    pass

        await save_data_async()
        del BROADCAST_STATE[uid]

        await q.edit_message_text(
            f"✅ **Post Successfully Published!**\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📤 **Sent To:** `{count} chats`\n"
            f"❌ **Failed:** `{failed_count}`\n"
            f"⏳ **Auto-Delete Timer:** `{ttl_label}`\n"
            f"🗑️ **Scheduled For Self-Destruct:** `{scheduled_count} messages`\n\n"
            f"💡 *Supergroups me post unke '📢 Announcements' topic me gayi hai, aur channels me direct broadcast hui hai.*",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # ---------------------------------------------------------
    # USER BROADCAST FLOW
    # ---------------------------------------------------------
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


def _check_admin_privilege(member, perm_name: str) -> bool:
    """Helper to check administrative permissions across Pyrogram versions."""
    if not member:
        return False
    if getattr(member, "status", None) == ChatMemberStatus.OWNER:
        return True
    if getattr(member, "status", None) != ChatMemberStatus.ADMINISTRATOR:
        return False
    privs = getattr(member, "privileges", None)
    if privs and hasattr(privs, perm_name):
        return bool(getattr(privs, perm_name))
    return bool(getattr(member, perm_name, False))


async def cmd_setup_announcements(client: Client, message: Message):
    """
    Audits all supergroups and channels, creates/pins '📢 Announcements' topic
    in all forum supergroups, identifies missing permissions, and generates a
    downloadable .txt report for manual permission approval.
    """
    if not message.from_user:
        return
    uid = message.from_user.id
    if not is_admin(uid):
        return await message.reply_text("❌ **Access Denied:** Yeh command keval Bot Admins/Owner ke liye hai.", parse_mode=ParseMode.MARKDOWN)

    status_msg = await message.reply_text(
        "🔍 **Supergroups & Channels Audit In Progress...**\n━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "⏳ Saare Supergroups aur Channels ko scan kiya ja raha hai...\n"
        "📢 Supergroups me '📢 Announcements' topic create aur pin kiya ja raha hai...\n"
        "📊 Detailed permissions report generate ho rahi hai...",
        parse_mode=ParseMode.MARKDOWN
    )

    try:
        me = await client.get_me()
        bot_id = me.id
        bot_username = me.username or "Bot"
    except Exception:
        bot_id = 0
        bot_username = "Bot"

    # Collect target chats
    all_targets = {}
    for col in ["FREE_CHANNELS", "PAID_CHANNELS", "SPECIAL_CHANNELS", "ALL_CHATS"]:
        for cid, name in DB.get(col, {}).items():
            try:
                all_targets[int(cid)] = name
            except Exception:
                pass

    supp_id = getattr(config, "SUPPORT_GROUP_ID", 0)
    if supp_id:
        try:
            all_targets[int(supp_id)] = all_targets.get(int(supp_id), "Support Group")
        except Exception:
            pass

    for cid in DB.get("ANNOUNCEMENT_TOPICS", {}).keys():
        try:
            all_targets[int(cid)] = all_targets.get(int(cid), f"Chat {cid}")
        except Exception:
            pass

    supergroups_audit = []
    channels_audit = []
    failed_chats = []

    topics_created = 0
    topics_existing = 0

    for cid, default_name in all_targets.items():
        try:
            chat = await client.get_chat(cid)
            chat_title = chat.title or default_name or str(cid)
            c_type = chat.type

            member = None
            try:
                member = await client.get_chat_member(cid, bot_id)
            except Exception as e:
                logger.warning(f"Could not get bot member info for chat {cid}: {e}")

            if c_type in [ChatType.SUPERGROUP, ChatType.GROUP]:
                is_forum = bool(getattr(chat, "is_forum", False))
                is_admin_user = getattr(member, "status", None) in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]
                
                can_manage_topics = _check_admin_privilege(member, "can_manage_topics")
                can_pin = _check_admin_privilege(member, "can_pin_messages")
                can_delete = _check_admin_privilege(member, "can_delete_messages")
                can_send = _check_admin_privilege(member, "can_send_messages") or is_admin_user

                missing_perms = []
                if not is_admin_user:
                    missing_perms.append("Make Bot Administrator")
                if not can_manage_topics:
                    missing_perms.append("Manage Topics (can_manage_topics)")
                if not can_pin:
                    missing_perms.append("Pin Messages (can_pin_messages)")
                if not can_delete:
                    missing_perms.append("Delete Messages (can_delete_messages)")
                if not can_send:
                    missing_perms.append("Send Messages (can_send_messages)")

                topic_status = "None"
                topic_id = DB.get("ANNOUNCEMENT_TOPICS", {}).get(str(cid))

                # If it's a forum and bot has manage_topics privilege, create or ensure topic
                if is_forum and can_manage_topics:
                    if not topic_id:
                        try:
                            topic = await client.create_forum_topic(chat_id=cid, title="📢 Announcements")
                            topic_id = getattr(topic, "id", None) or getattr(topic, "message_thread_id", None)
                        except AttributeError:
                            from pyrogram.raw.functions.channels import CreateForumTopic
                            peer = await client.resolve_peer(cid)
                            r = await client.invoke(
                                CreateForumTopic(channel=peer, title="📢 Announcements", random_id=client.rnd_id())
                            )
                            for update in r.updates:
                                if hasattr(update, "message") and hasattr(update.message, "id"):
                                    topic_id = update.message.id
                                    break
                        except Exception as e:
                            logger.error(f"Error creating topic in supergroup {cid}: {e}")

                        if topic_id:
                            DB.setdefault("ANNOUNCEMENT_TOPICS", {})[str(cid)] = int(topic_id)
                            topics_created += 1
                            topic_status = f"Created (ID: {topic_id})"

                            # Send and Pin Welcome Banner in the newly created topic
                            try:
                                banner = await client.send_message(
                                    chat_id=cid,
                                    message_thread_id=int(topic_id),
                                    text=(
                                        "📢 **Official Announcements Channel**\n"
                                        "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                                        "👋 **Welcome to the Official Announcements topic!**\n\n"
                                        "📌 Sabhi official batch updates, new releases, timetable alerts, "
                                        "aur important notices isi topic me aayenge.\n\n"
                                        "🔔 *Is topic ke notifications ON rakhein taaki koi important update miss na ho!*"
                                    ),
                                    parse_mode=ParseMode.MARKDOWN,
                                )
                                if can_pin:
                                    try:
                                        await client.pin_chat_message(chat_id=cid, message_id=banner.id)
                                    except Exception:
                                        pass
                            except Exception as e:
                                logger.error(f"Failed to send/pin banner in {cid} topic {topic_id}: {e}")
                        else:
                            topic_status = "Creation Failed"
                    else:
                        topics_existing += 1
                        topic_status = f"Active (ID: {topic_id})"
                else:
                    if topic_id:
                        topic_status = f"Previously Created (ID: {topic_id})"
                    else:
                        if not is_forum:
                            topic_status = "Topics Disabled in Group Settings"
                        else:
                            topic_status = "Missing 'Manage Topics' Permission"

                supergroups_audit.append({
                    "id": cid,
                    "title": chat_title,
                    "is_forum": is_forum,
                    "status": getattr(member, "status", "Unknown"),
                    "can_manage_topics": can_manage_topics,
                    "can_pin": can_pin,
                    "can_delete": can_delete,
                    "can_send": can_send,
                    "missing": missing_perms,
                    "topic_id": topic_id,
                    "topic_status": topic_status,
                })

            elif c_type == ChatType.CHANNEL:
                is_admin_user = getattr(member, "status", None) in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]
                can_post = _check_admin_privilege(member, "can_post_messages") or _check_admin_privilege(member, "can_send_messages")
                can_delete = _check_admin_privilege(member, "can_delete_messages")
                can_edit = _check_admin_privilege(member, "can_edit_messages")

                channel_missing = []
                if not is_admin_user:
                    channel_missing.append("Make Bot Administrator")
                if not can_post:
                    channel_missing.append("Post Messages (can_post_messages)")
                if not can_delete:
                    channel_missing.append("Delete Messages (can_delete_messages)")

                channels_audit.append({
                    "id": cid,
                    "title": chat_title,
                    "status": getattr(member, "status", "Unknown"),
                    "can_post": can_post,
                    "can_delete": can_delete,
                    "can_edit": can_edit,
                    "missing": channel_missing,
                })

            await asyncio.sleep(0.08)

        except Exception as e:
            failed_chats.append({"id": cid, "name": default_name, "error": str(e)})

    await save_data_async()

    # Build TXT Audit Report
    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    report_lines = [
        "=" * 80,
        "           TELEGRAM SUPERGROUPS & CHANNELS PERMISSIONS AUDIT REPORT",
        "=" * 80,
        f"Generated At: {now_str}",
        f"Bot Account:  @{bot_username} (ID: {bot_id})",
        f"Total Chats Scanned: {len(all_targets)}",
        f"Supergroups Audited: {len(supergroups_audit)}",
        f"Channels Audited:    {len(channels_audit)}",
        f"Announcement Topics: {topics_created} Created New, {topics_existing} Already Active",
        "=" * 80,
        "",
        "================================================================================",
        "1. SUPERGROUPS PERMISSIONS & FORUM TOPICS AUDIT",
        "================================================================================",
    ]

    sg_attention_count = 0
    for idx, sg in enumerate(supergroups_audit, start=1):
        has_issue = bool(sg["missing"] or not sg["is_forum"])
        if has_issue:
            sg_attention_count += 1
            status_flag = "⚠️ ACTION REQUIRED (Missing Permissions / Forum Disabled)"
        else:
            status_flag = "✅ FULLY CONFIGURED & READY"

        report_lines.append(f"\n[SUPERGROUP #{idx}] {sg['title']}")
        report_lines.append(f"  • Chat ID:        {sg['id']}")
        report_lines.append(f"  • Status Flag:    {status_flag}")
        report_lines.append(f"  • Forum Mode:     {'ENABLED ✅' if sg['is_forum'] else 'DISABLED ❌ (Topics off in group settings)'}")
        report_lines.append(f"  • Bot Role:       {str(sg['status']).replace('ChatMemberStatus.', '')}")
        report_lines.append(f"  • Announcement:   {sg['topic_status']}")
        
        report_lines.append("  • Current Permissions:")
        report_lines.append(f"      - Manage Topics:   {'✅ ALLOWED' if sg['can_manage_topics'] else '❌ MISSING'}")
        report_lines.append(f"      - Pin Messages:    {'✅ ALLOWED' if sg['can_pin'] else '❌ MISSING'}")
        report_lines.append(f"      - Delete Messages: {'✅ ALLOWED' if sg['can_delete'] else '❌ MISSING'}")
        report_lines.append(f"      - Send Messages:   {'✅ ALLOWED' if sg['can_send'] else '❌ MISSING'}")

        if sg["missing"]:
            report_lines.append("  • 🔧 Missing Permissions To Grant Manually in Telegram:")
            for m in sg["missing"]:
                report_lines.append(f"      👉 {m}")
            report_lines.append("  • How To Fix:")
            report_lines.append("      Telegram -> Open Group -> Edit (Pencil) -> Administrators -> Tap Bot")
            report_lines.append("      Enable the missing permissions listed above -> Save.")
        
        if not sg["is_forum"]:
            report_lines.append("  • 🔧 Forum Mode Fix:")
            report_lines.append("      Telegram -> Open Group -> Edit (Pencil) -> Topics -> Turn ON.")

        report_lines.append("-" * 80)

    report_lines.extend([
        "",
        "================================================================================",
        "2. BROADCAST CHANNELS AUDIT",
        "================================================================================",
        "(Note: Normal channels do not have topics. Batch updates and /post broadcasts",
        " are published directly to the channel root feed.)",
        "",
    ])

    for idx, ch in enumerate(channels_audit, start=1):
        ch_flag = "✅ ALL PERMISSIONS OK" if not ch["missing"] else "⚠️ ACTION REQUIRED"
        report_lines.append(f"[CHANNEL #{idx}] {ch['title']}")
        report_lines.append(f"  • Channel ID:     {ch['id']}")
        report_lines.append(f"  • Status Flag:    {ch_flag}")
        report_lines.append(f"  • Bot Role:       {str(ch['status']).replace('ChatMemberStatus.', '')}")
        report_lines.append(f"  • Post Messages:  {'✅ ALLOWED' if ch['can_post'] else '❌ MISSING'}")
        report_lines.append(f"  • Delete Message: {'✅ ALLOWED' if ch['can_delete'] else '❌ MISSING'}")
        if ch["missing"]:
            report_lines.append("  • 🔧 Missing Channel Permissions:")
            for m in ch["missing"]:
                report_lines.append(f"      👉 {m}")
        report_lines.append("-" * 80)

    if failed_chats:
        report_lines.extend([
            "",
            "================================================================================",
            "3. UNREACHABLE OR INACCESSIBLE CHATS",
            "================================================================================",
        ])
        for fc in failed_chats:
            report_lines.append(f"  • ID: {fc['id']} | Name: {fc['name']} | Error: {fc['error']}")

    report_lines.extend([
        "",
        "=" * 80,
        "SUMMARY & NEXT STEPS FOR ADMIN:",
        "1. Jo groups upar '⚠️ ACTION REQUIRED' mark hain, unme Telegram khol kar",
        "   bataye gaye missing permissions allow karein.",
        "2. Agar kisi group me 'Topics off' hai to Group Edit -> Topics ON karein.",
        "3. Permissions allow karne ke baad /setupannouncements wapas run karein,",
        "   bot bache hue supergroups me '📢 Announcements' topic turant create kar dega!",
        "=" * 80,
    ])

    report_content = "\n".join(report_lines)

    # Save to workspace file as well
    try:
        with open("supergroups_permissions_audit.txt", "w", encoding="utf-8") as f:
            f.write(report_content)
    except Exception:
        pass

    # Send document via Telegram
    doc_io = io.BytesIO(report_content.encode("utf-8"))
    doc_io.name = "supergroups_permissions_audit.txt"

    summary_caption = (
        "📊 **Supergroup & Channel Permissions Audit Report**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔍 **Scanned:** `{len(all_targets)} chats`\n"
        f"👥 **Supergroups:** `{len(supergroups_audit)}`\n"
        f"📢 **Announcements Topics Created:** `{topics_created}` new (`{topics_existing}` active)\n"
        f"⚠️ **Attention Needed:** `{sg_attention_count} groups` require permissions.\n\n"
        "📎 **Audit file attached:** Open `supergroups_permissions_audit.txt` to see exactly which permissions to grant!"
    )

    try:
        await client.send_document(
            chat_id=message.chat.id,
            document=doc_io,
            caption=summary_caption,
            parse_mode=ParseMode.MARKDOWN
        )
        await status_msg.delete()
    except Exception as e:
        await status_msg.edit_text(f"❌ Failed to send audit report document: {e}")




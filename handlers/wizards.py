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
from .broadcast import *
from .batches import *
from .menus import *
from .userbot import *

async def _check_and_setup_supergroup_announcements(client: Client, cid: int, cname: str = "") -> tuple[bool, str]:
    """
    If cid is a Supergroup with forum topics enabled:
    - Auto-registers in DB['SUPERGROUPS']
    - Auto-creates '📢 Announcements' topic if not present
    - Sends an official welcome announcement banner & pins it
    - Saves the topic ID into DB['ANNOUNCEMENT_TOPICS']
    Returns (success_bool, status_code).
    """
    try:
        from config import safe_create_forum_topic
        cid = int(cid)
        chat = None
        try:
            chat = await client.get_chat(cid)
        except Exception:
            pass

        if chat:
            if not cname:
                cname = chat.title or f"Chat {cid}"
            if chat.type == ChatType.CHANNEL:
                return False, "channel"
            DB.setdefault("SUPERGROUPS", {})[str(cid)] = cname
        else:
            if cname:
                DB.setdefault("SUPERGROUPS", {})[str(cid)] = cname

        existing_topic = DB.get("ANNOUNCEMENT_TOPICS", {}).get(str(cid))
        if existing_topic:
            return True, "already_exists"

        # Try creating forum topic via bot client
        topic_id = await safe_create_forum_topic(client, cid, "📢 Announcements")

        # Fallback to Userbot session if bot failed
        if not topic_id:
            try:
                from config import DB as cDB, API_ID, API_HASH
                session = cDB.get("USERBOT_SESSION") or getattr(config, "SESSION_STRING", None)
                api_id = API_ID or getattr(config, "API_ID", None)
                api_hash = API_HASH or getattr(config, "API_HASH", None)
                if session and api_id and api_hash:
                    from pyrogram import Client as PyroClient
                    async with PyroClient("temp_ann_wiz", api_id=int(api_id), api_hash=str(api_hash), session_string=str(session), in_memory=True) as ub:
                        topic_id = await safe_create_forum_topic(ub, cid, "📢 Announcements")
            except Exception as ube:
                logger.warning(f"Userbot fallback forum topic creation failed for {cid}: {ube}")

        if not topic_id:
            logger.warning(f"Could not create forum topic in {cid}. Supergroup may not have forum mode ON or missing manage_topics permission.")
            return False, "permission_error"

        DB.setdefault("ANNOUNCEMENT_TOPICS", {})[str(cid)] = int(topic_id)
        await save_data_async()

        try:
            banner_rich = rp.message(
                rp.heading("📢 Official Announcements Channel"),
                rp.divider(),
                rp.para("👋 ", rp.bold("Welcome to the Official Announcements topic!")),
                rp.para(
                    "📌 Sabhi official batch updates, new releases, timetable alerts, "
                    "aur important notices isi topic me aayenge."
                ),
                rp.divider(),
                rp.para(rp.italic("🔔 Is topic ke notifications ON rakhein taaki koi important update miss na ho!"))
            )
            banner = None
            try:
                banner = await client.send_rich_message(
                    chat_id=cid,
                    rich_message=banner_rich,
                    message_thread_id=int(topic_id)
                )
            except Exception:
                pass
            if not banner:
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
            if banner:
                try:
                    await client.pin_chat_message(chat_id=cid, message_id=banner.id)
                except Exception as pe:
                    logger.warning(f"Could not pin announcement banner in {cid}: {pe}")
        except Exception as se:
            logger.warning(f"Could not send announcement banner in {cid}: {se}")

        return True, "created"
    except Exception as e:
        logger.warning(f"Error checking supergroup announcement topic for {cid}: {e}")
        return False, str(e)


async def wizard_callback(client: Client, q: CallbackQuery):
    uid = q.from_user.id
    if uid not in ADMIN_WIZARD:
        return await q.answer("Wizard session expired.", show_alert=False)

    if q.data == "wiz_cancel":
        back_cb = ADMIN_WIZARD.get(uid, {}).get("back_cb", "dash_home" if is_owner(uid) else "goto_admin_panel")
        ADMIN_WIZARD.pop(uid, None)
        await q.answer("Add Batch Cancelled!")
        msg = build_batches_panel_msg(back_cb=back_cb)
        return await rp.edit(client, q.message.chat.id, q.message.id, msg)

    if q.data == "wiz_back_cat":
        ADMIN_WIZARD[uid]["step"] = "ask_cat"
        msg = build_addbatch_category_msg(uid)
        return await rp.edit(client, q.message.chat.id, q.message.id, msg)

    if q.data == "wiz_back_type":
        cat_idx = ADMIN_WIZARD.get(uid, {}).get("cat_idx", 0)
        selected_cat = ADMIN_WIZARD.get(uid, {}).get("category", DB.get("CATEGORIES", DEFAULT_CATEGORIES)[cat_idx])
        ADMIN_WIZARD[uid]["step"] = "ask_type"
        msg = rp.message(
            rp.heading("🏷️ Select Batch Type"),
            rp.para("Selected Category: ", rp.bold(f"📁 {selected_cat}")),
            rp.divider(),
            rp.para("Batch ka access level chunein:"),
            rp.buttons(
                rp.btn("🆓 Free Batch", "wiz_free", style=rp.Style.SUCCESS),
                rp.btn("💵 Paid Batch", "wiz_paid", style=rp.Style.DEFAULT),
                rp.btn("✨ Special Batch", "wiz_special", style=rp.Style.SUCCESS),
            ),
            rp.divider(),
            rp.buttons(
                rp.btn("🔙 Back to Categories", "wiz_back_cat", style=rp.Style.DEFAULT),
                rp.btn("❌ Cancel", "wiz_cancel", style=rp.Style.DANGER)
            )
        )
        return await rp.edit(client, q.message.chat.id, q.message.id, msg)

    if q.data.startswith("wcat_"):
        cat_idx = int(q.data.split("_")[1])
        selected_cat = DB.get("CATEGORIES", DEFAULT_CATEGORIES)[cat_idx]
        ADMIN_WIZARD[uid]["category"] = selected_cat
        ADMIN_WIZARD[uid]["cat_idx"] = cat_idx
        ADMIN_WIZARD[uid]["step"] = "ask_type"
        msg = rp.message(
            rp.heading("🏷️ Select Batch Type"),
            rp.para("Selected Category: ", rp.bold(f"📁 {selected_cat}")),
            rp.divider(),
            rp.para("Batch ka access level chunein:"),
            rp.buttons(
                rp.btn("🆓 Free Batch", "wiz_free", style=rp.Style.SUCCESS),
                rp.btn("💵 Paid Batch", "wiz_paid", style=rp.Style.DEFAULT),
                rp.btn("✨ Special Batch", "wiz_special", style=rp.Style.SUCCESS),
            ),
            rp.divider(),
            rp.buttons(
                rp.btn("🔙 Back to Categories", "wiz_back_cat", style=rp.Style.DEFAULT),
                rp.btn("❌ Cancel", "wiz_cancel", style=rp.Style.DANGER)
            )
        )
        return await rp.edit(client, q.message.chat.id, q.message.id, msg)

    if q.data in ["wiz_free", "wiz_paid", "wiz_special"]:
        if "category" not in ADMIN_WIZARD[uid]:
            return await q.answer("Start again")
        btype = q.data.replace("wiz_", "")
        ADMIN_WIZARD[uid]["type"] = btype
        ADMIN_WIZARD[uid]["step"] = "ask_id"
        ADMIN_WIZARD[uid]["msg_id"] = q.message.id
        ADMIN_WIZARD[uid]["chat_id"] = q.message.chat.id

        badge = "🆓 FREE" if btype == "free" else ("💵 PAID" if btype == "paid" else "✨ SPECIAL")
        msg = rp.message(
            rp.heading("🆔 Enter Channel / Group ID"),
            rp.para("Category: ", rp.bold(f"📁 {ADMIN_WIZARD[uid]['category']}")),
            rp.para("Batch Type: ", rp.bold(badge)),
            rp.divider(),
            rp.para("👉 Kripya us Channel ya Group ka ", rp.bold("Numeric ID"), " chat me bhejein:"),
            rp.expandable_quote([
                rp.bold("📌 Example:\n"),
                rp.code("-1002345678901"), "\n\n",
                rp.italic("Bot us channel/group me Admin hona chahiye taaki members manage ho sakein.")
            ]),
            rp.divider(),
            rp.buttons(
                rp.btn("🔙 Back to Types", "wiz_back_type", style=rp.Style.DEFAULT),
                rp.btn("❌ Cancel", "wiz_cancel", style=rp.Style.DANGER)
            )
        )
        return await rp.edit(client, q.message.chat.id, q.message.id, msg)

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
            cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel Wizard", callback_data="wiz_cancel")]])
            await q.edit_message_text(
                f"✅ **Batch Moved to PAID!**\n\n"
                f"💰 **Step 4:** '{cname}' ki Price / Fee kitni hai?\n(Example: `₹499`, `₹199 / Month`, etc.):\n\n*(Type /cancel to abort)*",
                reply_markup=cancel_kb,
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        elif target_type == "special":
            ADMIN_WIZARD[uid]["step"] = "ask_coin"
            ADMIN_WIZARD[uid]["cid"] = cid
            ADMIN_WIZARD[uid]["cname"] = cname
            cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel Wizard", callback_data="wiz_cancel")]])
            await q.edit_message_text(
                f"✅ **Batch Moved to SPECIAL!**\n\n"
                f"  **Step 4:** '{cname}' ko unlock karne ke liye kitne Coins chahiye?\nSirf number bhejein (e.g. `3`):\n\n*(Type /cancel to abort)*",
                reply_markup=cancel_kb,
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        sg_success, sg_code = await _check_and_setup_supergroup_announcements(client, cid, cname)
        sg_note = ""
        if sg_code == "created":
            sg_note = "\n📢 **Forum Supergroup:** `📢 Announcements` topic auto-created & pinned!"
        elif sg_code == "already_exists":
            sg_note = "\n📢 **Forum Supergroup:** `📢 Announcements` topic already linked."
        elif sg_code == "permission_error":
            sg_note = "\n⚠️ **Forum Supergroup:** Bot admin permission kam hai (Topic create nahi kar paya)."

        ADMIN_WIZARD.pop(uid, None)
        await q.edit_message_text(
            f"✅ **Batch Successfully Updated & Cleaned!**\n━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📦 **Name:** `{cname}`\n"
            f"🆔 **ID:** `{cid}`\n"
            f"🏷️ **Type:** `{target_type.upper()}`\n"
            f"📂 **Category:** `{target_cat}`{sg_note}\n\n"
            f"✨ Purani duplicate entries safely remove kar di gayi hain!",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    if q.data == "wiz_dup_cancel":
        back_cb = ADMIN_WIZARD.get(uid, {}).get("back_cb", "dash_home" if is_owner(uid) else "goto_admin_panel")
        ADMIN_WIZARD.pop(uid, None)
        await q.answer("Cancelled! No duplicate created.")
        msg = build_batches_panel_msg(back_cb=back_cb)
        return await rp.edit(client, q.message.chat.id, q.message.id, msg)


async def wizard_message(client: Client, message: Message):
    if not message.from_user or message.from_user.id not in ADMIN_WIZARD:
        return False
    uid, state = message.from_user.id, ADMIN_WIZARD[message.from_user.id]

    if message.text and message.text.strip().lower() in ["/cancel", "cancel"]:
        ADMIN_WIZARD.pop(uid, None)
        return await message.reply_text("❌ **Add Batch Cancelled.**", parse_mode=ParseMode.MARKDOWN)

    if state["step"] == "ask_id":
        try:
            raw_text = message.text.strip()
            cid = int(raw_text)
        except ValueError:
            cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel Wizard", callback_data="wiz_cancel")]])
            await message.reply_text(
                "❌ Error: Valid numeric Channel ID bhejein (jaise `-1001234567890`).\n\n*(Type /cancel to abort)*",
                reply_markup=cancel_kb,
                parse_mode=ParseMode.MARKDOWN
            )
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
                cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel Wizard", callback_data="wiz_cancel")]])
                await message.reply_text(
                    f"💰 **Step 4:** '{cname}' ki Price / Fee kitni hai?\n(Example: `₹499`, `₹199 / Month`, etc.):\n\n*(Type /cancel to abort)*",
                    reply_markup=cancel_kb,
                    parse_mode=ParseMode.MARKDOWN,
                )
                return True

            # Special batches: ab yahan ruk kar unlock-coin cost poochenge
            if state["type"] == "special":
                ADMIN_WIZARD[uid]["step"] = "ask_coin"
                ADMIN_WIZARD[uid]["cid"] = cid
                ADMIN_WIZARD[uid]["cname"] = cname
                cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel Wizard", callback_data="wiz_cancel")]])
                await message.reply_text(
                    f"🪙 **Step 4:** '{cname}' ko unlock karne ke liye kitne Coins chahiye?\nSirf number bhejein (e.g. `3`):\n\n*(Type /cancel to abort)*",
                    reply_markup=cancel_kb,
                    parse_mode=ParseMode.MARKDOWN,
                )
                return True

            sg_success, sg_code = await _check_and_setup_supergroup_announcements(client, cid, cname)
            sg_note = ""
            if sg_code == "created":
                sg_note = "\n📢 **Forum Supergroup:** `📢 Announcements` topic auto-created & pinned!"
            elif sg_code == "already_exists":
                sg_note = "\n📢 **Forum Supergroup:** `📢 Announcements` topic already linked."
            elif sg_code == "permission_error":
                sg_note = "\n⚠️ **Forum Supergroup:** Bot admin permission kam hai (Topic create nahi kar paya)."

            await message.reply_text(
                f"✅ **Added!**\n📛 Name: {cname} ({cid})\n🏷️ Type: {state['type'].upper()}\n📂 Category: {state['category']}{sg_note}",
                parse_mode=ParseMode.MARKDOWN,
            )
            
            if state["type"] == "free":
                await send_batch_update_post(client, cid, cname, state["type"], state["category"])

            if state["type"] == "free":
                b_count = 0
                await message.reply_text(
                    f"📡 Sending Auto-Broadcast for {state['type'].upper()} batch...", parse_mode=ParseMode.MARKDOWN
                )
                rich_msg, caption, kb = await build_batch_announcement_rich_post(
                    client, cid, cname, "free", state["category"]
                )
                for t_cid in list(DB.get("ALL_CHATS", {}).keys()):
                    if t_cid != cid:
                        try:
                            t_topic_id = DB.get("ANNOUNCEMENT_TOPICS", {}).get(str(t_cid))
                            if not t_topic_id and str(t_cid) in DB.get("SUPERGROUPS", {}):
                                await _check_and_setup_supergroup_announcements(client, int(t_cid))
                                t_topic_id = DB.get("ANNOUNCEMENT_TOPICS", {}).get(str(t_cid))
                            sent_msg = await send_rich_batch_post(
                                client,
                                int(t_cid),
                                rich_msg,
                                caption,
                                kb,
                                message_thread_id=int(t_topic_id) if t_topic_id else None
                            )
                            if sent_msg:
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

            sg_success, sg_code = await _check_and_setup_supergroup_announcements(client, cid, cname)
            sg_note = ""
            if sg_code == "created":
                sg_note = "\n📢 **Forum Supergroup:** `📢 Announcements` topic auto-created & pinned!"
            elif sg_code == "already_exists":
                sg_note = "\n📢 **Forum Supergroup:** `📢 Announcements` topic already linked."
            elif sg_code == "permission_error":
                sg_note = "\n⚠️ **Forum Supergroup:** Bot admin permission kam hai (Topic create nahi kar paya)."

            await message.reply_text(
                f"✅ **Added!**\n📛 Name: {cname} ({cid})\n🏷️ Type: PAID\n📂 Category: {state['category']}\n💰 Price / Fee: **{price}**{sg_note}",
                parse_mode=ParseMode.MARKDOWN,
            )

            await send_batch_update_post(client, cid, cname, "paid", state["category"], price=price)

            b_count = 0
            await message.reply_text("📡 Sending Auto-Broadcast for PAID batch...", parse_mode=ParseMode.MARKDOWN)
            rich_msg, caption, kb = await build_batch_announcement_rich_post(
                client, cid, cname, "paid", state["category"], price=price
            )
            for t_cid in list(DB.get("ALL_CHATS", {}).keys()):
                if t_cid != cid:
                    try:
                        t_topic_id = DB.get("ANNOUNCEMENT_TOPICS", {}).get(str(t_cid))
                        if not t_topic_id and str(t_cid) in DB.get("SUPERGROUPS", {}):
                            await _check_and_setup_supergroup_announcements(client, int(t_cid))
                            t_topic_id = DB.get("ANNOUNCEMENT_TOPICS", {}).get(str(t_cid))
                        sent_msg = await send_rich_batch_post(
                            client,
                            int(t_cid),
                            rich_msg,
                            caption,
                            kb,
                            message_thread_id=int(t_topic_id) if t_topic_id else None
                        )
                        if sent_msg:
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

            sg_success, sg_code = await _check_and_setup_supergroup_announcements(client, cid, cname)
            sg_note = ""
            if sg_code == "created":
                sg_note = "\n📢 **Forum Supergroup:** `📢 Announcements` topic auto-created & pinned!"
            elif sg_code == "already_exists":
                sg_note = "\n📢 **Forum Supergroup:** `📢 Announcements` topic already linked."
            elif sg_code == "permission_error":
                sg_note = "\n⚠️ **Forum Supergroup:** Bot admin permission kam hai (Topic create nahi kar paya)."

            await message.reply_text(
                f"✅ **Added!**\n📛 Name: {cname} ({cid})\n🏷️ Type: SPECIAL\n📂 Category: {state['category']}\n💰 Unlock Cost: **{coin_cost} Coin{'s' if coin_cost != 1 else ''}**{sg_note}",
                parse_mode=ParseMode.MARKDOWN,
            )

            await send_batch_update_post(client, cid, cname, "special", state["category"], coin_cost)

            b_count = 0
            await message.reply_text("📡 Sending Auto-Broadcast for SPECIAL batch...", parse_mode=ParseMode.MARKDOWN)
            rich_msg, caption, kb = await build_batch_announcement_rich_post(
                client, cid, cname, "special", state["category"], coin_cost=coin_cost
            )
            for t_cid in list(DB.get("ALL_CHATS", {}).keys()):
                if t_cid != cid:
                    try:
                        t_topic_id = DB.get("ANNOUNCEMENT_TOPICS", {}).get(str(t_cid))
                        if not t_topic_id and str(t_cid) in DB.get("SUPERGROUPS", {}):
                            await _check_and_setup_supergroup_announcements(client, int(t_cid))
                            t_topic_id = DB.get("ANNOUNCEMENT_TOPICS", {}).get(str(t_cid))
                        sent_msg = await send_rich_batch_post(
                            client,
                            int(t_cid),
                            rich_msg,
                            caption,
                            kb,
                            message_thread_id=int(t_topic_id) if t_topic_id else None
                        )
                        if sent_msg:
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

    elif state["step"] == "topicarr_source":
        try:
            source_id = int(message.text.strip())
        except ValueError:
            return await message.reply_text("❌ Error: Numeric Channel ID bhejein (jaise `-1001234567890`).\n\n*(Type /cancel to abort)*")
        ADMIN_WIZARD[uid]["step"] = "topicarr_dest"
        ADMIN_WIZARD[uid]["source_id"] = source_id
        cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel Wizard", callback_data="wiz_cancel")]])
        await message.reply_text(
            f"✅ **Source Channel ID:** `{source_id}`\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👉 **Step 2/4:** Ab **Destination Supergroup ID** bhejein jahan topics banakar files forward karni hain (e.g. `-1009876543210`):\n\n"
            f"*(Destination Supergroup me Topics mode ON hona chahiye aur Bot/Userbot ko Admin permissions honi chahiye)*",
            reply_markup=cancel_kb,
            parse_mode=ParseMode.MARKDOWN
        )
        return True

    elif state["step"] == "topicarr_dest":
        try:
            dest_id = int(message.text.strip())
        except ValueError:
            return await message.reply_text("❌ Error: Numeric Supergroup ID bhejein (jaise `-1009876543210`).\n\n*(Type /cancel to abort)*")
        ADMIN_WIZARD[uid]["step"] = "topicarr_tag"
        ADMIN_WIZARD[uid]["dest_id"] = dest_id
        cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel Wizard", callback_data="wiz_cancel")]])
        await message.reply_text(
            f"✅ **Destination Group ID:** `{dest_id}`\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👉 **Step 3/4:** Topic kis tag/prefix se extract karna hai?\n\n"
            f"• Agar caption me `Topic: Current Affairs` ya `Topic: Polity` likha hai, toh simply `Topic:` ya `Topic` bhejein.\n"
            f"• Default `Topic:` use karne ke liye `0` ya `Topic:` bhejein.\n\n"
            f"💡 *Bot caption se topic name automatically read karke usi naam ka topic destination supergroup me create karega.*",
            reply_markup=cancel_kb,
            parse_mode=ParseMode.MARKDOWN
        )
        return True

    elif state["step"] == "topicarr_tag":
        raw_tag = message.text.strip()
        topic_tag = "Topic:" if raw_tag in ["0", ""] else raw_tag
        ADMIN_WIZARD[uid]["step"] = "topicarr_range"
        ADMIN_WIZARD[uid]["topic_tag"] = topic_tag
        cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel Wizard", callback_data="wiz_cancel")]])
        await message.reply_text(
            f"🏷️ **Extraction Tag:** `{topic_tag}`\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👉 **Step 4/4:** Scan Range kya rakhni hai?\n\n"
            f"• **Poora Channel Scan:** `ALL` ya `0` bhejein (Bot automatically 1 se lekar latest message tak poora channel scan karega)\n"
            f"• **Custom Range:** `StartID EndID` bhejein (Example: `100 2500`)\n\n"
            f"*(Type /cancel to abort)*",
            reply_markup=cancel_kb,
            parse_mode=ParseMode.MARKDOWN
        )
        return True

    elif state["step"] == "topicarr_range":
        raw_range = message.text.strip().lower()
        start_id = 1
        end_id = 0
        if raw_range not in ["0", "all", ""]:
            parts = raw_range.split()
            try:
                if len(parts) >= 2:
                    start_id = int(parts[0])
                    end_id = int(parts[1])
                elif len(parts) == 1 and parts[0].isdigit():
                    start_id = int(parts[0])
            except ValueError:
                return await message.reply_text("❌ Error: Range numeric format me bhejein (e.g. `100 2000`) ya poora channel ke liye `ALL` bhejein.")

        source_id = ADMIN_WIZARD[uid]["source_id"]
        dest_id = ADMIN_WIZARD[uid]["dest_id"]
        topic_tag = ADMIN_WIZARD[uid]["topic_tag"]
        del ADMIN_WIZARD[uid]

        await message.reply_text(
            f"🚀 **Topic Arrangement Task Launched!**\n━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📥 **Source:** `{source_id}`\n"
            f"📤 **Destination:** `{dest_id}`\n"
            f"🏷️ **Extraction Tag:** `{topic_tag}`\n"
            f"🔢 **Range:** `{'ALL (1 to Latest)' if end_id == 0 else f'{start_id} to {end_id}'}`\n"
            f"🤖 **Engine:** User Session Account (Direct Copy without forward tag)\n"
            f"🏷️ **Caption:** Original Caption & Watermark Preserved ✅\n\n"
            f"⏳ Background process shuru ho raha hai...",
            parse_mode=ParseMode.MARKDOWN
        )

        from .userbot import run_topic_arrangement_engine
        asyncio.create_task(run_topic_arrangement_engine(
            client, message, str(source_id), str(dest_id), topic_tag, start_id, end_id, clean_caption=False
        ))
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
            import sys
            H = sys.modules.get("handlers")
            if not H:
                import handlers as H
            cmds = {
                "addadmin": getattr(H, "cmd_add_admin", None),
                "deladmin": getattr(H, "cmd_del_admin", None),
                "addtester": getattr(H, "cmd_add_tester", None),
                "deltester": getattr(H, "cmd_del_tester", None),
                "ban": getattr(H, "cmd_ban", None),
                "unban": getattr(H, "cmd_unban", None),
                "kick": getattr(H, "cmd_kick_user", None),
                "find": getattr(H, "cmd_find_user", None),
                "resetuser": getattr(H, "cmd_reset_user", None),
                "demo": getattr(H, "cmd_approve_demo", None),
                "per": getattr(H, "cmd_approve_perm", None),
                "perm": getattr(H, "cmd_approve_perm", None),
                "extend": getattr(H, "cmd_extend_demo", None),
                "gendemo": getattr(H, "cmd_gendemo", None),
                "settestbot": getattr(H, "cmd_set_testbot", None),
                "setwelcome": getattr(H, "cmd_set_welcome", None),
                "delbatch": getattr(H, "cmd_delbatch", None),
                "addcat": getattr(H, "cmd_addcat", None),
                "setcat": getattr(H, "cmd_setcategory", None),
                "emptybatch": getattr(H, "cmd_emptybatch", None),
                "userbotphone": getattr(H, "cmd_userbotphone", None),
                "userbototp": getattr(H, "cmd_userbototp", None),
                "userbotpass": getattr(H, "cmd_userbotpass", None),
                "userbotsession": getattr(H, "cmd_userbotsession", None),
                "storebatch": getattr(H, "cmd_storebatch", None),
                "userlookup": getattr(H, "cmd_user_lookup", None),
                "superfwd": getattr(H, "cmd_superfwd_start", None),
                "advcap": getattr(H, "cmd_advcap_start", None),
                "deluser": getattr(H, "cmd_deluser", None),
                "updatepost": getattr(H, "process_updatepost", None),
                "cleanbatch": getattr(H, "cmd_cleanbatch", None),
                "genvoucher": getattr(H, "cmd_genvoucher_from_wizard", None),
                "setreqnote": getattr(H, "cmd_set_request_note", None),
                "setprice": getattr(H, "cmd_set_batch_price", None),
            }
            handler = cmds.get(cmd_name) or getattr(H, f"cmd_{cmd_name}", None) or getattr(H, cmd_name, None)
            if callable(handler):
                await handler(client, message)
            else:
                logger.error(f"No handler found for wizard command {cmd_name}")
                await message.reply_text(f"❌ Command handler '{cmd_name}' nahi mila.")
        except Exception as e:
            logger.error(f"Error executing wizard command {cmd_name}: {e}")
            try:
                await message.reply_text(f"❌ Error: {e}")
            except Exception:
                pass
        if uid in ADMIN_WIZARD and ADMIN_WIZARD[uid].get("step") == current_step:
            del ADMIN_WIZARD[uid]
        return True
    return False



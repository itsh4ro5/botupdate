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
from .wizards import *

async def delete_service_messages(client: Client, message: Message):
    try:
        await client.delete_messages(chat_id=message.chat.id, message_ids=message.id)
    except Exception as e:
        logger.error(f"Service message delete fail hua: {type(e).__name__} - {e}")


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
        if not text:
            await message.reply_text("⚠️ Kripya valid text message me batch ka naam bhejein.\n*(Agar cancel karna ho toh /cancel likhein)*")
            return True
        USER_FLOW_STATE.pop(uid, None)
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
                rp.expandable_quote([
                    ce("INFO", "ℹ️"), " ", rp.bold("Note from Admin:\n"),
                    rp.italic(note)
                ]),
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
        if not text:
            await message.reply_text("⚠️ Kripya text message me voucher code bhejein.\n*(Agar cancel karna ho toh /cancel likhein)*")
            return True
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

    elif state == "wait_giveaway_batch":
        if not text:
            await message.reply_text("⚠️ Kripya valid text message me batch ka naam bhejein.\n*(Agar cancel karna ho toh /cancel likhein)*")
            return True
        USER_FLOW_STATE.pop(uid, None)
        user_key = str(uid) if str(uid) in DB.get("USER_DATA", {}) else uid
        user_coins = DB.get("USER_DATA", {}).get(user_key, {}).get("referral_count", 0)
        req_coins = DB.get("GIVEAWAY_CONFIG", {}).get("required_coins", 3)

        if user_coins < req_coins:
            kb = [
                [InlineKeyboardButton("🚀 Refer Friends (+1 Coin)", callback_data="menu_refer")],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="u_main")]
            ]
            await message.reply_text(
                f"❌ **Coins Insufficient!**\n\n"
                f"Giveaway me register karne ke liye kam se kam `{req_coins} Coins` chahiye.\n"
                f"Aapke paas abhi sirf `{user_coins} Coins` hain.\n\n"
                f"Dosto ko refer karke coins collect karein!",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.MARKDOWN
            )
            return True

        # Deduct coins & register
        DB["USER_DATA"][user_key]["referral_count"] -= req_coins
        DB.setdefault("GIVEAWAY_PARTICIPANTS", {})[str(uid)] = {
            "name": message.from_user.first_name or "Student",
            "username": message.from_user.username or "",
            "batch": text,
            "votes": 0,
            "voters": [],
            "time": time.time()
        }
        await save_data_async()

        bot_info = getattr(client, "me", None)
        bot_uname = bot_info.username if bot_info and bot_info.username else "H4R_Contact_bot"
        vote_link = f"https://t.me/{bot_uname}?start=vote_{uid}"
        share_url = f"https://t.me/share/url?url={urllib.parse.quote(vote_link)}&text={urllib.parse.quote(f'🔥 Bhaiyon, mujhe Free Batch Giveaway me vote karein! Mera target batch: {text}. Ek vote se mera batch unlock hoga! 🗳️')}"

        try:
            reg_msg = rp.message(
                rp.heading("🎉 Giveaway Registration Confirmed!"),
                rp.divider(),
                rp.marked(f" 🎯 Target Batch: {text} | 🎟️ Entry Paid: {req_coins} Coins "),
                rp.divider(),
                rp.expandable_quote([
                    rp.bold("📢 Ab Aage Kya Karna Hai?\n\n"),
                    "1. Aapka candidate profile successfully create ho gaya hai!\n",
                    "2. Neeche diye gaye ", rp.bold("Share & Rally Votes"), " button se apna link WhatsApp & Telegram study groups me bhejein.\n",
                    "3. Har user jo aapke link se aayega, aapko ", rp.bold("+1 Vote"), " dega!\n",
                    "4. Sabse zyada votes wale student ko unka manpasand batch bilkul ", rp.bold("FREE"), " milega! 🏆"
                ]),
                rp.para("🔗 ", rp.bold("Your Personal Voting Link:\n"), rp.code(vote_link)),
                rp.divider(),
                rp.buttons(
                    rp.url_btn("📤 Share & Rally Votes", share_url, style=rp.Style.LINK),
                    rp.btn("🏆 Live Leaderboard", "giveaway_leaderboard", style=rp.Style.SUCCESS)
                ),
                rp.buttons(rp.btn("🏠 Main Menu", "u_main", style=rp.Style.DANGER))
            )
            await rp.send(client, message.chat.id, reg_msg)
        except Exception:
            kb = [
                [InlineKeyboardButton("📤 Share & Rally Votes", url=share_url)],
                [InlineKeyboardButton("🏆 Live Leaderboard", callback_data="giveaway_leaderboard")],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="u_main")]
            ]
            await message.reply_text(
                f"🎉 **Giveaway Registration Confirmed!**\n━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🎯 **Target Batch:** `{text}`\n"
                f"🎟️ **Entry Fee Paid:** `{req_coins} Coins`\n\n"
                f"🔗 **Your Voting Link:**\n`{vote_link}`\n\n"
                f"Is link ko apne friends aur college groups me share karein!",
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

    elif state == "wait_search_breq":
        USER_FLOW_STATE.pop(uid, None)
        query = text.lower().strip()
        reqs = DB.get("BATCH_REQUESTS", {})
        statuses = DB.setdefault("BATCH_REQUEST_STATUS", {})
        matches = []
        user_db = DB.get("USER_DATA", {})
        for bname, uids in reqs.items():
            if query in bname.lower():
                matches.append((bname, uids))
            elif query in [str(u) for u in uids]:
                matches.append((bname, uids))
            else:
                for u in uids:
                    u_info = user_db.get(u) or user_db.get(str(u), {})
                    if query in (u_info.get("name") or "").lower() or query in (u_info.get("username") or "").lower():
                        matches.append((bname, uids))
                        break

        if not matches:
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔍 Search Again", callback_data="breq_search_prompt")],
                [InlineKeyboardButton("🔙 Back to Requests", callback_data="breq_p_0_pending")]
            ])
            await message.reply_text(f"❌ '{text}' ke liye koi matching batch request nahi mili.", reply_markup=kb)
            return True

        card = build_demands_console_card(matches, page=0, tab="all")
        pyro_kb = to_pyrogram_markup(card["reply_markup"])
        await message.reply_text(f"🔍 <b>Search Results for:</b> <code>{text}</code> (<b>{len(matches)}</b> found)\n\n" + card["text"], reply_markup=pyro_kb, parse_mode=ParseMode.HTML)
        return True

    is_link_state = (isinstance(state, dict) and state.get("action") == "wait_breq_link") or (isinstance(state, str) and state.startswith("wait_breq_link:"))
    is_bc_state = (isinstance(state, dict) and state.get("action") == "wait_breq_broadcast") or (isinstance(state, str) and state.startswith("wait_breq_broadcast:"))

    if is_link_state:
        if isinstance(state, dict):
            bname = state.get("bname", "")
            page = state.get("page", 0)
            tab = state.get("tab", "pending")
        else:
            parts = state.split(":", 3)
            bname = parts[1]
            page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
            tab = parts[3] if len(parts) > 3 else "pending"
        USER_FLOW_STATE.pop(uid, None)

        all_chats = DB.get("ALL_CHATS", {})
        matched_cid = None
        clean_text = text.strip()
        for cid, cname in all_chats.items():
            if clean_text == str(cid) or clean_text.lower() in str(cname).lower():
                matched_cid = cid
                break

        if not matched_cid and clean_text.startswith("-100"):
            try:
                matched_cid = int(clean_text)
            except ValueError:
                pass

        if not matched_cid:
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Request", callback_data=f"breq_p_{page}_{tab}")]])
            await message.reply_text(
                f"❌ '{text}' namak koi valid batch channel nahi mila.\n"
                f"Kripya sahi Channel ID (jaise -100...) ya exact Channel Name bhejein.",
                reply_markup=kb
            )
            return True

        channel_name = all_chats.get(matched_cid) or all_chats.get(str(matched_cid)) or f"Channel {matched_cid}"
        DB.setdefault("BATCH_REQUEST_STATUS", {})[bname] = "done"
        DB.setdefault("BATCH_REQUEST_LINKS", {})[bname] = matched_cid
        await save_data_async()

        uids = DB.get("BATCH_REQUESTS", {}).get(bname, [])
        notified = 0
        for target_uid in uids:
            try:
                notify_kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🚀 Open & Join Batch", callback_data=f"gen_link_{matched_cid}")],
                    [InlineKeyboardButton("📚 Browse All Batches", callback_data="all_batches_0")],
                    [InlineKeyboardButton("🏠 Main Menu", callback_data="u_main")]
                ])
                await client.send_message(
                    int(target_uid),
                    f"🎉 **Great News! Aapka Requested Batch Bot Me Add Ho Chuka Hai!**\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"📦 **Requested Batch:** `{bname}`\n"
                    f"📁 **Live Batch Channel:** `{channel_name}`\n\n"
                    f"Admin ne is batch ko live upload kar diya hai. Niche button dabakar instant batch me join karein!\n\n"
                    f"Happy Learning! 🚀",
                    reply_markup=notify_kb,
                    parse_mode=ParseMode.MARKDOWN
                )
                notified += 1
            except Exception as e:
                logger.warning(f"Failed to notify requester {target_uid}: {e}")

        back_kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Requests", callback_data=f"breq_p_{page}_{tab}")]])
        await message.reply_text(
            f"✅ **Batch Successfully Linked & Fulfilled!**\n━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📦 **Request:** `{bname}`\n"
            f"📁 **Linked Channel:** `{channel_name}` (`{matched_cid}`)\n"
            f"👥 **Students Notified:** `{notified}/{len(uids)} Students`\n\n"
            f"Status has been marked as **Live (☑)**.",
            reply_markup=back_kb,
            parse_mode=ParseMode.MARKDOWN
        )
        return True

    elif is_bc_state:
        if isinstance(state, dict):
            bname = state.get("bname", "")
            page = state.get("page", 0)
            tab = state.get("tab", "pending")
        else:
            parts = state.split(":", 3)
            bname = parts[1]
            page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
            tab = parts[3] if len(parts) > 3 else "pending"
        USER_FLOW_STATE.pop(uid, None)

        uids = DB.get("BATCH_REQUESTS", {}).get(bname, [])
        sent = 0
        for target_uid in uids:
            try:
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("📚 All Batches", callback_data="all_batches_0")],
                    [InlineKeyboardButton("🏠 Main Menu", callback_data="u_main")]
                ])
                await client.send_message(
                    int(target_uid),
                    f"📢 **ADMIN UPDATE ON YOUR REQUESTED BATCH**\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"📦 **Batch:** `{bname}`\n\n"
                    f"{text}\n\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"*(Aapne is batch ki demand submit ki thi isliye yeh update bheja gaya)*",
                    reply_markup=kb,
                    parse_mode=ParseMode.MARKDOWN
                )
                sent += 1
            except Exception as err:
                logger.warning(f"Could not send broadcast to requester {target_uid}: {err}")

        back_kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Request", callback_data=f"breq_p_{page}_{tab}")]])
        await message.reply_text(
            f"✅ **Broadcast Sent!**\n\nDelivered update to `{sent}/{len(uids)}` waiting students for `{bname}`.",
            reply_markup=back_kb,
            parse_mode=ParseMode.MARKDOWN
        )
        return True

    return False


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

        text_query = (message.text or message.caption or "").strip()
        matching_batches = []
        if text_query and len(text_query) >= 2 and not text_query.startswith("/"):
            q_low = text_query.lower()
            all_chats = DB.get("ALL_CHATS", {})
            for b_cid, b_cname in all_chats.items():
                if q_low in str(b_cname).lower():
                    b_type = "free"
                    if str(b_cid) in DB.get("PAID_CHANNELS", {}) or b_cid in DB.get("PAID_CHANNELS", {}):
                        b_type = "paid"
                    elif str(b_cid) in DB.get("SPECIAL_CHANNELS", {}) or b_cid in DB.get("SPECIAL_CHANNELS", {}):
                        b_type = "special"
                    matching_batches.append((b_cid, b_cname, b_type))

        delivered_to_support = False
        try:
            topic_id = await get_or_create_topic(user, client)
            if topic_id:
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
                delivered_to_support = True

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

        # Always respond back to the student / user so the bot is never silent
        if matching_batches:
            kb_rows = []
            for b_cid, b_cname, b_type in matching_batches[:5]:
                btn_text = f"📦 {str(b_cname)[:30]} ({b_type.upper()})"
                kb_rows.append([InlineKeyboardButton(btn_text, callback_data=f"binfo_{b_cid}")])
            kb_rows.append([
                InlineKeyboardButton("📚 All Batches", callback_data="u_batches"),
                InlineKeyboardButton("🏠 Main Menu", callback_data="u_main")
            ])
            status_note = "\n\n*(Aapka message Support Team ko bhi deliver ho gaya hai ✅)*" if delivered_to_support else ""
            await message.reply_text(
                f"🔍 **Search Results for:** `{text_query}` (Found {len(matching_batches)}):\n━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Aapke query se milte-julte batches niche diye gaye hain:\n\n"
                f"Batch par click karke direct access karein:{status_note}",
                reply_markup=InlineKeyboardMarkup(kb_rows),
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            kb = [
                [
                    InlineKeyboardButton("📚 Browse Batches", callback_data="u_batches"),
                    InlineKeyboardButton("📝 Request a Batch", callback_data="btn_req_batch")
                ],
                [
                    InlineKeyboardButton("🎁 Free Giveaway", callback_data="gw_hub"),
                    InlineKeyboardButton("🏠 Main Menu", callback_data="u_main")
                ]
            ]
            deliv_text = (
                "📩 **Aapka message Support Team ko deliver ho gaya hai!**\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "Admin jald hi is topic me aapko reply karenge.\n"
                "Tab tak aap niche diye gaye buttons se batches explore ya request kar sakte hain:"
                if delivered_to_support else
                "👋 **Hello!** Niche diye gaye menu se batches explore karein ya /start dabayein:"
            )
            await message.reply_text(
                deliv_text,
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.MARKDOWN
            )

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

        # Fallback 1: Check command arguments (e.g. /info 123456789 or /file 123456789)
        raw_cmd_text = (message.text or message.caption or "").strip()
        cmd_parts = raw_cmd_text.split()
        cmd_name = cmd_parts[0].lstrip("/!.").lower() if cmd_parts else ""
        if not target_uid and len(cmd_parts) > 1 and cmd_parts[1].isdigit():
            target_uid = int(cmd_parts[1])

        # Fallback 2: Check reply_to_message for user ID patterns (tg://user?id= or 🆔)
        if not target_uid and message.reply_to_message:
            rep_text = message.reply_to_message.text or message.reply_to_message.caption or ""
            id_match = re.search(r"tg://user\?id=(\d+)", rep_text)
            if not id_match:
                id_match = re.search(r"🆔\s*`?(\d+)`?", rep_text)
            if not id_match:
                id_match = re.search(r"User ID[:\s]+`?(\d+)`?", rep_text, re.IGNORECASE)
            if id_match:
                target_uid = int(id_match.group(1))

        # Fallback 3: If message is a slash command in topic but target_uid couldn't be resolved:
        if raw_cmd_text.startswith(("/", "!", ".")) and not target_uid:
            if cmd_name in ["demo"]:
                from .batches import cmd_approve_demo
                return await cmd_approve_demo(client, message)
            elif cmd_name in ["per", "perm"]:
                from .batches import cmd_approve_perm
                return await cmd_approve_perm(client, message)
            elif cmd_name in ["extend"]:
                from .batches import cmd_extend_demo
                return await cmd_extend_demo(client, message)
            elif cmd_name in ["gendemo"]:
                from .batches import cmd_gendemo
                return await cmd_gendemo(client, message)
            elif cmd_name in ["batchrequests", "requestbatches"]:
                from .batches import cmd_batchrequests
                return await cmd_batchrequests(client, message)
            elif cmd_name in ["vouchers"]:
                from .economy import cmd_list_vouchers
                return await cmd_list_vouchers(client, message)
            elif cmd_name in ["genvoucher"]:
                from .economy import cmd_genvoucher
                return await cmd_genvoucher(client, message)
            elif cmd_name in ["help", "start"]:
                from .menus import cmd_start
                return await cmd_start(client, message)
            else:
                await message.reply_text(
                    "⚠️ **Student ID auto-detect nahi ho payi.**\n"
                    "Kripya ID ke sath command dein: `/info <user_id>` ya `/file <user_id>`",
                    parse_mode=ParseMode.MARKDOWN
                )
                return

        if not target_uid:
            return

        # --- ADMIN TOPIC COMMAND INTERCEPTOR ---
        if raw_cmd_text.startswith(("/", "!", ".")):
            # Commands in this topic should NEVER be forwarded to the student!
            cmd_parts = raw_cmd_text.split()
            cmd_name = cmd_parts[0].lstrip("/!.").lower()

            if cmd_name in ["info", "user", "userinfo", "status", "details", "check", "profile"]:
                loading_msg = await message.reply_text("🔍 _Fetching student profile & batch membership..._", parse_mode=ParseMode.MARKDOWN)
                try:
                    from config import build_user_topic_info
                    summary_text, kb, _ = await build_user_topic_info(client, target_uid)
                    await loading_msg.edit_text(summary_text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
                except Exception as err:
                    await loading_msg.edit_text(f"❌ Error fetching student details: `{err}`")
                return

            elif cmd_name in ["txt", "file", "dump", "fulldetails", "fullinfo", "download"]:
                loading_msg = await message.reply_text("📄 _Generating complete student audit file (.txt)..._", parse_mode=ParseMode.MARKDOWN)
                try:
                    from config import generate_user_txt_file
                    f = await generate_user_txt_file(client, target_uid)
                    await loading_msg.delete()
                    user_key = target_uid if target_uid in DB.get("USER_DATA", {}) else str(target_uid)
                    u_name = DB.get("USER_DATA", {}).get(user_key, {}).get("name") or f"Student {target_uid}"
                    safe_u_name = str(u_name).replace("[", "").replace("]", "").strip()
                    if not safe_u_name or safe_u_name in [".", "..", "-", "_"] or len(safe_u_name) < 2:
                        safe_u_name = f"Student {target_uid}"
                    await client.send_document(
                        chat_id=int(SUPPORT_GROUP_ID),
                        document=f,
                        caption=f"📄 **FULL AUDIT FILE FOR STUDENT:** [{safe_u_name}](tg://user?id={target_uid}) (`{target_uid}`)",
                        message_thread_id=topic_id,
                        parse_mode=ParseMode.MARKDOWN
                    )
                except Exception as err:
                    await loading_msg.edit_text(f"❌ Error generating audit file: `{err}`")
                return

            elif cmd_name in ["giftcoin", "addcoins", "coins", "givecoins"]:
                if len(cmd_parts) < 2 or not cmd_parts[1].isdigit():
                    return await message.reply_text(
                        "⚠️ **Usage:** `/giftcoin <amount>` (e.g. `/giftcoin 5`)",
                        parse_mode=ParseMode.MARKDOWN
                    )
                amount = int(cmd_parts[1])
                user_key = target_uid if target_uid in DB.get("USER_DATA", {}) else str(target_uid)
                if user_key not in DB.get("USER_DATA", {}):
                    DB.setdefault("USER_DATA", {})[user_key] = {"name": f"User {target_uid}", "referral_count": 0}
                DB["USER_DATA"][user_key]["referral_count"] = DB["USER_DATA"][user_key].get("referral_count", 0) + amount
                await save_data_async()
                new_bal = DB["USER_DATA"][user_key]["referral_count"]
                
                try:
                    await client.send_message(
                        target_uid,
                        f"🎉 **Great News!** Admin ne aapko **+{amount} Study Coins** gift kiye hain! 💎\n"
                        f"Aapka naya coin balance: `{new_bal} Coins`"
                    )
                except Exception:
                    pass

                await message.reply_text(
                    f"✅ **Success!** Successfully gifted **+{amount} Coins** to student `{target_uid}`.\n"
                    f"💎 New Balance: `{new_bal} Coins`",
                    parse_mode=ParseMode.MARKDOWN
                )
                return

            elif cmd_name in ["demo"]:
                from .batches import cmd_approve_demo
                return await cmd_approve_demo(client, message)

            elif cmd_name in ["per", "perm"]:
                from .batches import cmd_approve_perm
                return await cmd_approve_perm(client, message)

            elif cmd_name in ["extend"]:
                from .batches import cmd_extend_demo
                return await cmd_extend_demo(client, message)

            elif cmd_name in ["gendemo"]:
                from .batches import cmd_gendemo
                return await cmd_gendemo(client, message)

            elif cmd_name in ["kick"]:
                from .admin import cmd_kick_user
                return await cmd_kick_user(client, message)

            elif cmd_name in ["ban", "block"]:
                if target_uid not in DB.get("BLOCKED_USERS", []):
                    DB.setdefault("BLOCKED_USERS", []).append(target_uid)
                    await save_data_async()
                await message.reply_text(
                    f"🚫 **Student Blocked!** User `{target_uid}` has been banned from the bot.",
                    parse_mode=ParseMode.MARKDOWN
                )
                return

            elif cmd_name in ["unban", "unblock"]:
                blocked = DB.get("BLOCKED_USERS", [])
                if target_uid in blocked:
                    blocked.remove(target_uid)
                if str(target_uid) in blocked:
                    blocked.remove(str(target_uid))
                await save_data_async()
                await message.reply_text(
                    f"🟢 **Student Unblocked!** User `{target_uid}` has been unbanned.",
                    parse_mode=ParseMode.MARKDOWN
                )
                return

            else:
                import handlers as H
                cmd_func = getattr(H, f"cmd_{cmd_name}", None)
                if cmd_func and callable(cmd_func):
                    return await cmd_func(client, message)

                user_key = target_uid if target_uid in DB.get("USER_DATA", {}) else str(target_uid)
                u_name = DB.get("USER_DATA", {}).get(user_key, {}).get("name") or f"Student {target_uid}"
                safe_u_name = str(u_name).replace("[", "").replace("]", "").strip()
                if not safe_u_name or safe_u_name in [".", "..", "-", "_"] or len(safe_u_name) < 2:
                    safe_u_name = f"Student {target_uid}"
                await message.reply_text(
                    f"🛠️ **TOPIC COMMANDS FOR [{safe_u_name}](tg://user?id={target_uid}) (`{target_uid}`):**\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"• `/demo <link> [hours]` - Grant temporary demo access (default: 3h)\n"
                    f"• `/per <link>` or `/perm` - Grant permanent lifetime batch access\n"
                    f"• `/extend [hours]` - Extend current active demo\n"
                    f"• `/info` or `/status` - View student profile snapshot (coins, referrals, batches)\n"
                    f"• `/file` or `/txt` - Download complete student audit report (.txt)\n"
                    f"• `/giftcoin <amount>` - Gift study coins directly to this student\n"
                    f"• `/ban` / `/unban` - Block or unblock student\n\n"
                    f"_(Note: Commands in this topic are kept private and never forwarded to the student.)_",
                    parse_mode=ParseMode.MARKDOWN
                )
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

    # Auto-capture any group or supergroup where bot or user is updated
    if update.chat and update.chat.type in [ChatType.SUPERGROUP, ChatType.GROUP]:
        DB.setdefault("SUPERGROUPS", {})[str(update.chat.id)] = update.chat.title or str(update.chat.id)

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
    if chat and chat.type in [ChatType.SUPERGROUP, ChatType.GROUP]:
        DB.setdefault("SUPERGROUPS", {})[str(chat.id)] = chat.title or str(chat.id)
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

    # Check if user has permanent access or active demo or unlocked batch in DB
    target_key = str(user.id) if str(user.id) in DB.get("USER_DATA", {}) else user.id
    u_info = DB.get("USER_DATA", {}).get(target_key, {})

    perm_batches = [int(x) for x in u_info.get("permanent_batches", []) if str(x).lstrip('-').isdigit()]
    unlocked_batches = [int(x) for x in u_info.get("unlocked_batches", []) if str(x).lstrip('-').isdigit()]

    chat_id_int = int(chat.id)
    chat_id_str = str(chat.id)

    has_perm = (
        chat_id_int in perm_batches or 
        chat_id_str in [str(x) for x in u_info.get("permanent_batches", [])] or
        chat_id_int in unlocked_batches or
        chat_id_str in [str(x) for x in u_info.get("unlocked_batches", [])]
    )

    demo_dict = u_info.get("demos", {})
    demo_info = demo_dict.get(chat_id_str) or demo_dict.get(chat_id_int)
    has_demo = False
    if demo_info:
        exp = demo_info.get("expiry") if isinstance(demo_info, dict) else float(demo_info)
        if exp > time.time():
            has_demo = True

    # If user is already permanently approved or on active demo, AUTO-APPROVE immediately!
    if has_perm or has_demo:
        try:
            await client.approve_chat_join_request(chat.id, user.id)
            invalidate_membership_cache(user.id, chat.id)
            welcome_str = DB.get("CUSTOM_WELCOMES", {}).get(
                chat.id, f"🎉 **Approved!**\nWelcome to {chat.title}!"
            )
            w_msg = await client.send_message(
                user.id, welcome_str, parse_mode=ParseMode.MARKDOWN
            )
            await schedule_delete(client, w_msg, delay=120)
            return
        except Exception as e:
            logger.error(f"Error auto-approving permitted join request for {user.id} in {chat.id}: {e}")
            return

    # Free Channels handling
    if chat.id in DB.get("FREE_CHANNELS", {}) or str(chat.id) in DB.get("FREE_CHANNELS", {}):
        if DB.get("FREE_LOCKED", False) or not DB.get("NEW_USERS_ALLOWED", True):
            try:
                await client.send_message(user.id, "⚠️ **Notice:** Free Batches ya Bot Entry currently locked hai.")
                await client.decline_chat_join_request(chat.id, user.id)
            except Exception:
                pass
            return

        user_rec = DB.get("USER_DATA", {}).get(target_key, {})
        joined_free = user_rec.get("free_batches_joined", [])
        if not is_vip_user(user.id) and not user_rec.get("free_unlocked", False) and len(joined_free) >= 3 and (chat_id_int not in joined_free and chat_id_str not in joined_free):
            try:
                await client.send_message(user.id, "🔒 **Free Limit Reached!**\nAapne 3 free batches use kar li hain. Agli batch ke liye bot me `/start` karke 1 Coin se unlock karein.")
                await client.decline_chat_join_request(chat.id, user.id)
            except Exception:
                pass
            return

        if await check_membership_pyro(user.id, client):
            try:
                await client.approve_chat_join_request(chat.id, user.id)
                invalidate_membership_cache(user.id, chat.id)
                if chat_id_int not in joined_free and chat_id_str not in joined_free:
                    DB.setdefault("USER_DATA", {}).setdefault(target_key, {}).setdefault("free_batches_joined", []).append(chat_id_int)
                    await save_data_async()
                welcome_str = DB.get("CUSTOM_WELCOMES", {}).get(
                    chat.id, f"🎉 **Approved!**\nWelcome to {chat.title}!"
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
                    f"⚠️ **Join Main Channel First!**\n{MANDATORY_CHANNEL_LINK}",
                    parse_mode=ParseMode.MARKDOWN,
                )
                await client.decline_chat_join_request(chat.id, user.id)
            except Exception:
                pass
    else:
        # Paid or Special batch channel join request
        # Keep request pending for Admin approval via /per or /demo
        logger.info(f"Pending join request from user {user.id} for batch chat {chat.id} ({chat.title})")



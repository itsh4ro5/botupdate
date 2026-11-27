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
    MessageNotModified,
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
    build_batch_request_detail_card,
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
from .economy import *
from .admin import *
from .batches import *
from .userbot import *
from .broadcast import *
from .wizards import *
from .menus import *
from .support import *

async def general_callback(client: Client, q: CallbackQuery):
    import config
    uid = q.from_user.id
    data = q.data
    if q.message:
        q.message.from_user = q.from_user
    if uid in DB.get("BLOCKED_USERS", []):
        return await q.answer("  You are blocked by the admin.", show_alert=True)
    try:
        if data.startswith("wiz_") or data.startswith("wcat_"):
            return await wizard_callback(client, q)
        if data.startswith("bc_") or data.startswith("post_ttl_"):
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
        elif data == "start_topic_arranger":
            await q.answer()
            if not is_admin(uid):
                return await q.answer("❌ Only Admins can use this tool.", show_alert=True)
            session_string = DB.get("USERBOT_SESSION") or getattr(config, "SESSION_STRING", None)
            if not session_string or not getattr(config, "API_ID", None):
                return await q.answer("❌ Userbot Not Logged In! Pehle Owner Panel se Userbot login karein.", show_alert=True)
            
            ADMIN_WIZARD[uid] = {
                "step": "topicarr_source",
                "back_cb": "dash_db"
            }
            msg = rp.message(
                rp.heading("🗂️ Topic Arrangement Tool"),
                rp.divider(),
                rp.expandable_quote([
                    rp.bold("📌 Tool Overview:\n\n"),
                    "Yeh tool Source Channel ke sabhi contents ko unke caption me diye gaye Topic name ke hisab se Destination Supergroup me naye Topics banakar auto-sort karega.\n\n",
                    "• ", rp.bold("Forward Tag:"), " Completely Removed (Direct Copy) ❌\n",
                    "• ", rp.bold("Engine:"), " User Session Account (Not Bot) ✅\n",
                    "• ", rp.bold("Auto Topic Creator:"), " Har unique topic ka dedicated forum topic banega ✅\n",
                    "• ", rp.bold("Original Caption:"), " Watermark aur caption 100% intact rahega ✅"
                ]),
                rp.divider(),
                rp.para("👉 ", rp.bold("Step 1/4:"), " Kripya ", rp.bold("Source Channel ID"), " bhejein (jahan se files leni hain):"),
                rp.expandable_quote([
                    rp.bold("📌 Example:\n"),
                    rp.code("-1002345678901"), "\n\n",
                    rp.italic("Bot aur Userbot dono is channel me member/admin hone chahiye.")
                ]),
                rp.divider(),
                rp.buttons(rp.btn("❌ Cancel", "wiz_cancel", style=rp.Style.DANGER))
            )
            await rp.edit(client, q.message.chat.id, q.message.id, msg)
        elif data == "dash_testers":
            await q.answer()
            if not is_owner(uid):
                return await q.answer("❌ Only Owner can manage testers.", show_alert=True)
            msg = build_testers_panel_msg()
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
                    rp.btn("🔑 Login (Phone + OTP)", "input_userbotphone", style=rp.Style.SUCCESS),
                    rp.btn("📝 Paste Session String", "input_userbotsession", style=rp.Style.DEFAULT),
                    rp.btn("🔙 Back to Terminal", "dash_home", style=rp.Style.DANGER)
                )
            )
            await rp.edit(client, q.message.chat.id, q.message.id, logout_msg)
        elif data == "userbot_resend_sms":
            if uid != OWNER_ID and str(uid) != str(OWNER_ID):
                return await q.answer("❌ Owner only.", show_alert=True)
            user_store = getattr(client, "user_data_store", {})
            phone = user_store.get("login_phone")
            phone_code_hash = user_store.get("phone_code_hash")
            temp_client = user_store.get("login_client")
            if not phone or not phone_code_hash or not temp_client:
                return await q.answer("⚠️ Login session expire ho gaya. Wapas login start karein.", show_alert=True)
            try:
                new_sent = await temp_client.resend_code(phone, phone_code_hash)
                user_store["phone_code_hash"] = new_sent.phone_code_hash
                await q.answer("📩 Telegram ko SMS bhejne ki request bhej di gayi hai! SMS check karein.", show_alert=True)
            except FloodWait as fw:
                await q.answer(f"⏳ Telegram rate limit: Kripya {fw.value}s wait karein.", show_alert=True)
            except Exception as ex:
                await q.answer(f"⚠️ Resend failed: {ex}", show_alert=True)
        elif data.startswith("input_"):
            await q.answer()
            cmd_name = data.split("_")[1]
            ADMIN_WIZARD[uid] = {"step": f"call_cmd_{cmd_name}"}
            prompts = {
                "addadmin": "Send User ID to make Admin:",
                "deladmin": "Send User ID to remove from Admin:",
                "addtester": "🧪 Send User ID to add as Authorized Tester (Bypasses Maintenance Mode):",
                "deltester": "🧪 Send User ID to remove from Authorized Testers:",
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
                "userbotsession": "📝 **PASTE PYROGRAM SESSION STRING**\n\nApna Pyrogram Session String yahan paste karein:\n*(Example: `BQG...`)*\n\n*(Agar aapke paas session string nahi hai toh aap 'Login via Phone & OTP' use karein)*",
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

        elif data.startswith("t_user_txt_"):
            if not is_admin(uid):
                return await q.answer("❌ Sirf admins ke liye.", show_alert=True)
            target = int(data.replace("t_user_txt_", ""))
            await q.answer("📄 Generating full details .txt file...", show_alert=False)
            try:
                from config import generate_user_txt_file
                f = await generate_user_txt_file(client, target)
                topic_id = getattr(q.message, "message_thread_id", None) or getattr(q.message, "reply_to_top_message_id", None)
                user_key = target if target in DB.get("USER_DATA", {}) else str(target)
                u_name = DB.get("USER_DATA", {}).get(user_key, {}).get("name") or f"Student {target}"
                safe_name = str(u_name).replace("[", "").replace("]", "")
                await client.send_document(
                    chat_id=q.message.chat.id,
                    document=f,
                    caption=f"📄 **FULL AUDIT FILE FOR STUDENT:** [{safe_name}](tg://user?id={target}) (`{target}`)",
                    message_thread_id=topic_id,
                    parse_mode=ParseMode.MARKDOWN
                )
            except Exception as err:
                await q.message.reply_text(f"❌ Error generating audit file: `{err}`")

        elif data.startswith("t_block_"):
            if not is_admin(uid):
                return await q.answer("❌ Sirf admins ke liye.", show_alert=True)
            target = int(data.replace("t_block_", ""))
            if target not in DB.get("BLOCKED_USERS", []):
                DB.setdefault("BLOCKED_USERS", []).append(target)
                await save_data_async()
            await q.answer(f"🚫 User {target} blocked!", show_alert=True)
            try:
                from config import build_user_topic_info
                summary_text, kb, _ = await build_user_topic_info(client, target)
                await q.edit_message_text(summary_text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
            except Exception:
                pass

        elif data.startswith("t_unblock_"):
            if not is_admin(uid):
                return await q.answer("❌ Sirf admins ke liye.", show_alert=True)
            target = int(data.replace("t_unblock_", ""))
            blocked = DB.get("BLOCKED_USERS", [])
            if target in blocked:
                blocked.remove(target)
            if str(target) in blocked:
                blocked.remove(str(target))
            await save_data_async()
            await q.answer(f"🟢 User {target} unblocked!", show_alert=True)
            try:
                from config import build_user_topic_info
                summary_text, kb, _ = await build_user_topic_info(client, target)
                await q.edit_message_text(summary_text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
            except Exception:
                pass
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
        # 📋 BATCH REQUESTS DEMANDS & CHECKLIST TRACKER (Bot API 10.4 Console)
        # =====================================================================
        elif data in ["view_batch_requests", "act_requestbatches", "requestbatches"]:
            if not is_admin(uid):
                return await send_ephemeral_reply(q.id, "❌ Sirf admins ke liye.", chat_id=q.message.chat.id)
            reqs = DB.get("BATCH_REQUESTS", {})
            statuses = DB.setdefault("BATCH_REQUEST_STATUS", {})
            if not reqs:
                await send_ephemeral_reply(q.id, "📭 Abhi tak koi batch requests nahi aayi hain.", chat_id=q.message.chat.id)
                msg = build_batch_requests_msg(page=0)
                return await rp.edit(client, q.message.chat.id, q.message.id, msg)

            def _sort_key(item):
                bname, uids = item
                status = statuses.get(bname, "pending")
                status_rank = 0 if status != "done" else 1
                return (status_rank, -len(uids), bname.lower())

            sorted_items = sorted(reqs.items(), key=_sort_key)
            card = build_demands_console_card(sorted_items, page=0)
            pyro_kb = to_pyrogram_markup(card["reply_markup"])
            try:
                await q.edit_message_text(card["text"], reply_markup=pyro_kb, parse_mode=ParseMode.HTML)
            except Exception:
                msg = build_batch_requests_msg(page=0)
                await rp.edit(client, q.message.chat.id, q.message.id, msg)

        # =====================================================================
        # 📋 BATCH REQUESTS ADVANCED MANAGEMENT SUITE (TABS, DETAIL, ACTIONS)
        # =====================================================================
        elif data.startswith("breq_tab_"):
            if not is_admin(uid):
                return await q.answer("❌ Sirf admins ke liye.", show_alert=True)
            parts = data.split("_")
            new_tab = parts[2]
            new_page = int(parts[3]) if len(parts) > 3 else 0

            reqs = DB.get("BATCH_REQUESTS", {})
            statuses = DB.setdefault("BATCH_REQUEST_STATUS", {})

            def _sort_key(item):
                bname, uids = item
                status = statuses.get(bname, "pending")
                status_rank = 0 if status != "done" else 1
                return (status_rank, -len(uids), bname.lower())

            sorted_items = sorted(reqs.items(), key=_sort_key)
            card = build_demands_console_card(sorted_items, page=new_page, tab=new_tab)
            pyro_kb = to_pyrogram_markup(card["reply_markup"])
            await q.answer()
            try:
                await q.edit_message_text(card["text"], reply_markup=pyro_kb, parse_mode=ParseMode.HTML)
            except Exception:
                pass

        elif data.startswith("breq_p_"):
            if not is_admin(uid):
                return await q.answer("❌ Sirf admins ke liye.", show_alert=True)
            parts = data.split("_")
            page = int(parts[2])
            tab = parts[3] if len(parts) > 3 else "pending"

            reqs = DB.get("BATCH_REQUESTS", {})
            statuses = DB.setdefault("BATCH_REQUEST_STATUS", {})

            def _sort_key(item):
                bname, uids = item
                status = statuses.get(bname, "pending")
                status_rank = 0 if status != "done" else 1
                return (status_rank, -len(uids), bname.lower())

            sorted_items = sorted(reqs.items(), key=_sort_key)
            card = build_demands_console_card(sorted_items, page=page, tab=tab)
            pyro_kb = to_pyrogram_markup(card["reply_markup"])
            await q.answer()
            try:
                await q.edit_message_text(card["text"], reply_markup=pyro_kb, parse_mode=ParseMode.HTML)
            except Exception:
                pass

        elif data.startswith("breq_view_"):
            # Drill-down: View dedicated batch request details & actions
            if not is_admin(uid):
                return await q.answer("❌ Sirf admins ke liye.", show_alert=True)
            parts = data.split("_")
            item_global_idx = int(parts[2])
            page = int(parts[3])
            tab = parts[4] if len(parts) > 4 else "pending"

            reqs = DB.get("BATCH_REQUESTS", {})
            statuses = DB.setdefault("BATCH_REQUEST_STATUS", {})

            def _sort_key(item):
                bname, uids = item
                status = statuses.get(bname, "pending")
                status_rank = 0 if status != "done" else 1
                return (status_rank, -len(uids), bname.lower())

            sorted_items = sorted(reqs.items(), key=_sort_key)
            if tab == "pending":
                filtered = [it for it in sorted_items if statuses.get(it[0], "pending") != "done"]
            elif tab == "done":
                filtered = [it for it in sorted_items if statuses.get(it[0], "pending") == "done"]
            else:
                filtered = sorted_items

            if item_global_idx < 0 or item_global_idx >= len(filtered):
                return await q.answer("❌ Invalid request index.", show_alert=True)

            bname, _ = filtered[item_global_idx]
            card = build_batch_request_detail_card(bname, item_idx=item_global_idx, page=page, tab=tab)
            pyro_kb = to_pyrogram_markup(card["reply_markup"])
            await q.answer()
            try:
                await q.edit_message_text(card["text"], reply_markup=pyro_kb, parse_mode=ParseMode.HTML)
            except Exception:
                pass

        elif data.startswith("breq_det_toggle_"):
            if not is_admin(uid):
                return await q.answer("❌ Sirf admins ke liye.", show_alert=True)
            parts = data.split("_")
            idx_or_enc = parts[3]
            page = int(parts[4])
            tab = parts[5] if len(parts) > 5 else "pending"

            reqs = DB.get("BATCH_REQUESTS", {})
            statuses = DB.setdefault("BATCH_REQUEST_STATUS", {})

            def _sort_key(item):
                bn, u = item
                status = statuses.get(bn, "pending")
                status_rank = 0 if status != "done" else 1
                return (status_rank, -len(u), bn.lower())

            sorted_items = sorted(reqs.items(), key=_sort_key)
            if tab == "pending":
                filtered = [it for it in sorted_items if statuses.get(it[0], "pending") != "done"]
            elif tab == "done":
                filtered = [it for it in sorted_items if statuses.get(it[0], "pending") == "done"]
            else:
                filtered = sorted_items

            if idx_or_enc.isdigit():
                item_idx = int(idx_or_enc)
                if 0 <= item_idx < len(filtered):
                    bname = filtered[item_idx][0]
                else:
                    return await q.answer("❌ Request list updated. Kripya refresh karein.", show_alert=True)
            else:
                bname = urllib.parse.unquote(idx_or_enc)
                item_idx = 0

            uids = reqs.get(bname, [])
            current_status = statuses.get(bname, "pending")
            if current_status == "done":
                statuses[bname] = "pending"
                await save_data_async()
                await q.answer(f"⏳ Marked '{bname[:30]}' as Pending (☐)")
            else:
                statuses[bname] = "done"
                await save_data_async()

                # Notify students
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
                            f"Aapne is batch ke liye demand request submit ki thi. Admin ne ise bot me successfully live kar diya hai!\n\n"
                            f"Abhi access karke apni preparation continue karein! 🚀",
                            reply_markup=notify_kb,
                            parse_mode=ParseMode.MARKDOWN
                        )
                        notified_count += 1
                    except Exception as err:
                        logger.warning(f"Could not notify user {target_uid}: {err}")

                await q.answer(f"✅ Live marked & {notified_count} student(s) notified!", show_alert=True)

            card = build_batch_request_detail_card(bname, item_idx=item_idx, page=page, tab=tab)
            pyro_kb = to_pyrogram_markup(card["reply_markup"])
            try:
                await q.edit_message_text(card["text"], reply_markup=pyro_kb, parse_mode=ParseMode.HTML)
            except Exception:
                pass

        elif data.startswith("breq_det_del_"):
            if not is_admin(uid):
                return await q.answer("❌ Sirf admins ke liye.", show_alert=True)
            parts = data.split("_")
            idx_or_enc = parts[3]
            page = int(parts[4])
            tab = parts[5] if len(parts) > 5 else "pending"

            reqs = DB.get("BATCH_REQUESTS", {})
            statuses = DB.setdefault("BATCH_REQUEST_STATUS", {})

            def _sort_key(item):
                bn, u = item
                status = statuses.get(bn, "pending")
                status_rank = 0 if status != "done" else 1
                return (status_rank, -len(u), bn.lower())

            sorted_items = sorted(reqs.items(), key=_sort_key)
            if tab == "pending":
                filtered = [it for it in sorted_items if statuses.get(it[0], "pending") != "done"]
            elif tab == "done":
                filtered = [it for it in sorted_items if statuses.get(it[0], "pending") == "done"]
            else:
                filtered = sorted_items

            if idx_or_enc.isdigit():
                item_idx = int(idx_or_enc)
                if 0 <= item_idx < len(filtered):
                    bname = filtered[item_idx][0]
                else:
                    return await q.answer("❌ Request list updated. Kripya refresh karein.", show_alert=True)
            else:
                bname = urllib.parse.unquote(idx_or_enc)
                item_idx = 0

            if bname in reqs:
                del reqs[bname]
            if bname in statuses:
                del statuses[bname]
            await save_data_async()

            await q.answer(f"🗑️ Request '{bname[:30]}' deleted!", show_alert=True)

            sorted_items = sorted(reqs.items(), key=_sort_key)
            card = build_demands_console_card(sorted_items, page=page, tab=tab)
            pyro_kb = to_pyrogram_markup(card["reply_markup"])
            try:
                await q.edit_message_text(card["text"], reply_markup=pyro_kb, parse_mode=ParseMode.HTML)
            except Exception:
                pass

        elif data.startswith("breq_det_link_"):
            if not is_admin(uid):
                return await q.answer("❌ Sirf admins ke liye.", show_alert=True)
            parts = data.split("_")
            idx_or_enc = parts[3]
            page = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else 0
            tab = parts[5] if len(parts) > 5 else "pending"

            reqs = DB.get("BATCH_REQUESTS", {})
            statuses = DB.setdefault("BATCH_REQUEST_STATUS", {})

            def _sort_key(item):
                bn, u = item
                status = statuses.get(bn, "pending")
                status_rank = 0 if status != "done" else 1
                return (status_rank, -len(u), bn.lower())

            sorted_items = sorted(reqs.items(), key=_sort_key)
            if tab == "pending":
                filtered = [it for it in sorted_items if statuses.get(it[0], "pending") != "done"]
            elif tab == "done":
                filtered = [it for it in sorted_items if statuses.get(it[0], "pending") == "done"]
            else:
                filtered = sorted_items

            if idx_or_enc.isdigit():
                item_idx = int(idx_or_enc)
                if 0 <= item_idx < len(filtered):
                    bname = filtered[item_idx][0]
                else:
                    return await q.answer("❌ Request list updated. Kripya refresh karein.", show_alert=True)
            else:
                bname = urllib.parse.unquote(idx_or_enc)

            USER_FLOW_STATE[uid] = {"action": "wait_breq_link", "bname": bname, "page": page, "tab": tab}
            await q.answer()
            cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data=f"breq_p_{page}_{tab}")]])
            await q.message.reply_text(
                f"🔗 **LINK BATCH TO CHANNEL**\n━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📦 **Request:** `{bname}`\n\n"
                f"Kripya bot ke us **Channel ka Numeric ID** bhejein (jaise `-100...`) ya Exact Channel Name jisme yeh batch upload hai.\n\n"
                f"Jaise hi aap ID bhejenge, students ko direct batch join button ke saath notification chala jayega!",
                reply_markup=cancel_kb,
                parse_mode=ParseMode.MARKDOWN
            )

        elif data.startswith("breq_det_msg_"):
            if not is_admin(uid):
                return await q.answer("❌ Sirf admins ke liye.", show_alert=True)
            parts = data.split("_")
            idx_or_enc = parts[3]
            page = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else 0
            tab = parts[5] if len(parts) > 5 else "pending"

            reqs = DB.get("BATCH_REQUESTS", {})
            statuses = DB.setdefault("BATCH_REQUEST_STATUS", {})

            def _sort_key(item):
                bn, u = item
                status = statuses.get(bn, "pending")
                status_rank = 0 if status != "done" else 1
                return (status_rank, -len(u), bn.lower())

            sorted_items = sorted(reqs.items(), key=_sort_key)
            if tab == "pending":
                filtered = [it for it in sorted_items if statuses.get(it[0], "pending") != "done"]
            elif tab == "done":
                filtered = [it for it in sorted_items if statuses.get(it[0], "pending") == "done"]
            else:
                filtered = sorted_items

            if idx_or_enc.isdigit():
                item_idx = int(idx_or_enc)
                if 0 <= item_idx < len(filtered):
                    bname = filtered[item_idx][0]
                else:
                    return await q.answer("❌ Request list updated. Kripya refresh karein.", show_alert=True)
            else:
                bname = urllib.parse.unquote(idx_or_enc)

            uids = DB.get("BATCH_REQUESTS", {}).get(bname, [])
            USER_FLOW_STATE[uid] = {"action": "wait_breq_broadcast", "bname": bname, "page": page, "tab": tab}
            await q.answer()
            cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data=f"breq_p_{page}_{tab}")]])
            await q.message.reply_text(
                f"📢 **BROADCAST TO REQUESTERS**\n━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📦 **Batch:** `{bname}`\n"
                f"👥 **Recipients:** `{len(uids)} Students`\n\n"
                f"Kripya apna text/update message bhejein jo aap in sabhi students ko bhejna chahte hain.\n"
                f"*(Cancel karne ke liye /cancel likhein)*",
                reply_markup=cancel_kb,
                parse_mode=ParseMode.MARKDOWN
            )

        elif data == "breq_search_prompt":
            if not is_admin(uid):
                return await q.answer("❌ Sirf admins ke liye.", show_alert=True)
            USER_FLOW_STATE[uid] = "wait_search_breq"
            await q.answer()
            cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Requests", callback_data="breq_p_0_pending")]])
            await q.message.reply_text(
                "🔍 **SEARCH BATCH DEMANDS**\n━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "Kripya koi bhi **Keyword** (e.g. `ALP`, `Science`, `GATE`, `Drishti`) ya **Student ID** type karke bhejein:\n\n"
                "*(Cancel karne ke liye /cancel likhein)*",
                reply_markup=cancel_kb,
                parse_mode=ParseMode.MARKDOWN
            )

        elif data.startswith("breq_delm_"):
            if not is_admin(uid):
                return await q.answer("❌ Sirf admins ke liye.", show_alert=True)
            parts = data.split("_")
            page = int(parts[2])
            tab = parts[3] if len(parts) > 3 else "pending"
            reqs = DB.get("BATCH_REQUESTS", {})
            statuses = DB.setdefault("BATCH_REQUEST_STATUS", {})

            def _sort_key(item):
                bname, uids = item
                status = statuses.get(bname, "pending")
                status_rank = 0 if status != "done" else 1
                return (status_rank, -len(uids), bname.lower())

            sorted_items = sorted(reqs.items(), key=_sort_key)
            card = build_demands_console_card(sorted_items, page=page, mode="delete", tab=tab)
            pyro_kb = to_pyrogram_markup(card["reply_markup"])
            await q.answer()
            try:
                await q.edit_message_text(card["text"], reply_markup=pyro_kb, parse_mode=ParseMode.HTML)
            except Exception:
                pass

        elif data.startswith("breq_del_"):
            if not is_admin(uid):
                return await q.answer("❌ Sirf admins ke liye.", show_alert=True)
            parts = data.split("_")
            item_global_idx = int(parts[2])
            page = int(parts[3])
            tab = parts[4] if len(parts) > 4 else "pending"

            reqs = DB.get("BATCH_REQUESTS", {})
            statuses = DB.setdefault("BATCH_REQUEST_STATUS", {})

            def _sort_key(item):
                bname, uids = item
                status = statuses.get(bname, "pending")
                status_rank = 0 if status != "done" else 1
                return (status_rank, -len(uids), bname.lower())

            sorted_items = sorted(reqs.items(), key=_sort_key)
            if tab == "pending":
                filtered = [it for it in sorted_items if statuses.get(it[0], "pending") != "done"]
            elif tab == "done":
                filtered = [it for it in sorted_items if statuses.get(it[0], "pending") == "done"]
            else:
                filtered = sorted_items

            if item_global_idx >= 0 and item_global_idx < len(filtered):
                bname, _ = filtered[item_global_idx]
                if bname in reqs:
                    del reqs[bname]
                if bname in statuses:
                    del statuses[bname]
                await save_data_async()
                await q.answer(f"🗑️ Deleted: {bname[:25]}", show_alert=True)

            sorted_items = sorted(reqs.items(), key=_sort_key)
            card = build_demands_console_card(sorted_items, page=page, mode="delete", tab=tab)
            pyro_kb = to_pyrogram_markup(card["reply_markup"])
            try:
                await q.edit_message_text(card["text"], reply_markup=pyro_kb, parse_mode=ParseMode.HTML)
            except Exception:
                pass

        elif data == "breq_clean_done":
            if not is_admin(uid):
                return await q.answer("❌ Sirf admins ke liye.", show_alert=True)

            reqs = DB.get("BATCH_REQUESTS", {})
            statuses = DB.setdefault("BATCH_REQUEST_STATUS", {})

            done_keys = [k for k, v in statuses.items() if v == "done"]
            cleaned_count = 0
            for k in done_keys:
                if k in reqs:
                    del reqs[k]
                    cleaned_count += 1
                if k in statuses:
                    del statuses[k]

            await save_data_async()
            await q.answer(f"🧹 {cleaned_count} uploaded batch requests clean kar diye gaye!", show_alert=True)

            def _sort_key(item):
                bname, uids = item
                status = statuses.get(bname, "pending")
                status_rank = 0 if status != "done" else 1
                return (status_rank, -len(uids), bname.lower())

            sorted_items = sorted(reqs.items(), key=_sort_key)
            card = build_demands_console_card(sorted_items, page=0, mode="normal", tab="pending")
            pyro_kb = to_pyrogram_markup(card["reply_markup"])
            try:
                await q.edit_message_text(card["text"], reply_markup=pyro_kb, parse_mode=ParseMode.HTML)
            except Exception:
                pass

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
            back_cb = "dash_home" if is_owner(q.from_user.id) else "goto_admin_panel"
            await cmd_addbatch_start(client, q.message, user_id=q.from_user.id, callback_query=q, back_cb=back_cb)
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

            if "pending_vote" in DB["USER_DATA"][user_key]:
                cand_id = DB["USER_DATA"][user_key].pop("pending_vote")
                await save_data_async()
                from .economy import cast_giveaway_vote
                await cast_giveaway_vote(client, q.from_user, cand_id)

            if "pending_demand" in DB["USER_DATA"][user_key]:
                d_idx = DB["USER_DATA"][user_key].pop("pending_demand")
                await save_data_async()
                bname = DB.get("DEMAND_SLUGS", {}).get(str(d_idx))
                if bname:
                    from .batches import cast_batch_demand_vote
                    await cast_batch_demand_vote(client, uid, bname)

            await show_user_menu_cb(client, q)

        elif data == "u_main":
            await q.answer()
            await show_user_menu_cb(client, q)

        # =====================================================================
        # 🎁 DAILY STREAK CLAIM SYSTEM (In-Chat Live Streak & Wallet)
        # =====================================================================
        elif data in ("claim_daily_streak", "wallet_claim_streak"):
            user_key = uid if uid in DB["USER_DATA"] else str(uid)
            user_info = DB["USER_DATA"].setdefault(user_key, {})
            now_dt = datetime.now()
            today_str = now_dt.strftime("%Y-%m-%d")
            yesterday_str = (now_dt - timedelta(days=1)).strftime("%Y-%m-%d")
            last_claim = user_info.get("last_streak_date")

            if last_claim == today_str:
                return await q.answer("⏳ Aapne aaj ka streak reward pehle hi claim kar liya hai! Agla reward kal milega.", show_alert=True)

            await q.answer("🎉 Badhai ho! Daily Streak Claimed!", show_alert=False)
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
                [InlineKeyboardButton("💳 View Student Wallet", callback_data="my_info"), InlineKeyboardButton("🏆 View Leaderboard", callback_data="show_leaderboard")],
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
        # 🏆 LIVE REFERRAL LEADERBOARD & PAGINATION
        # =====================================================================
        elif data == "show_leaderboard":
            await q.answer()
            widget = build_leaderboard_widget(uid, page=0)
            pm = to_pyrogram_markup(widget.get("reply_markup"))
            try:
                await q.edit_message_text(widget["text"], reply_markup=pm, parse_mode=ParseMode.HTML)
            except MessageNotModified:
                pass
            return

        elif data.startswith("lb_page_"):
            try:
                page_idx = int(data.split("_")[2])
            except Exception:
                page_idx = 0
            widget = build_leaderboard_widget(uid, page=page_idx)
            pm = to_pyrogram_markup(widget.get("reply_markup"))
            try:
                await q.edit_message_text(widget["text"], reply_markup=pm, parse_mode=ParseMode.HTML)
                await q.answer()
            except MessageNotModified:
                await q.answer("✅ Updated to current live rankings!", show_alert=False)
            except Exception as e:
                logger.error(f"Error updating leaderboard: {e}")
                await q.answer()
            return

        elif data == "view_giveaways_ticker":
            vouchers = DB.get("VOUCHERS", {})
            if not vouchers:
                return await send_ephemeral_reply(q.id, "📭 Abhi koi active giveaway nahi hai. Jaldi hi naye giveaway aayenge!", chat_id=q.message.chat.id)

            active_v_lines = []
            for code, v_data in list(vouchers.items())[-8:]:
                max_u = v_data.get("max_uses", 1)
                used_c = len(v_data.get("used_by", []))
                rem = max(0, max_u - used_c)
                pts = v_data.get("points", 0)
                status_icon = "🔥" if rem > 0 else "❌"
                active_v_lines.append(f"{status_icon} <code>{code}</code>: <b>+{pts} Coins</b> ({rem}/{max_u} slots left)")

            summary = "🎁 <b>LIVE GIVEAWAYS & PROMO TICKER</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n" + "\n".join(active_v_lines) + "\n\n<i>Promo codes claim karne ke liye 'Redeem Code' use karein!</i>"
            await send_ephemeral_reply(q.id, summary, chat_id=q.message.chat.id)

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
        # 🎁 1-CLICK GIVEAWAY CLAIMS & TICKER (Bot API 10.4)
        # =====================================================================
        elif data.startswith("claim_voucher_"):
            code = data.replace("claim_voucher_", "").strip().upper()
            vouchers = DB.get("VOUCHERS", {})
            if code not in vouchers:
                return await send_ephemeral_reply(q.id, "❌ Yeh voucher expire ho chuka hai ya exist nahi karta.", chat_id=q.message.chat.id)

            v_data = vouchers[code]
            used_by = v_data.setdefault("used_by", [])
            max_uses = v_data.get("max_uses", 1)
            pts = v_data.get("points", 0)

            if uid in used_by or str(uid) in used_by:
                return await send_ephemeral_reply(q.id, f"⚠️ Aapne voucher '{code}' pehle hi claim kar liya hai!", chat_id=q.message.chat.id)

            if len(used_by) >= max_uses:
                return await send_ephemeral_reply(q.id, f"⌛ Giveaway is full! All {max_uses} slots are already claimed.", chat_id=q.message.chat.id)

            used_by.append(uid)
            user_key = uid if uid in DB["USER_DATA"] else str(uid)
            user_info = DB["USER_DATA"].setdefault(user_key, {})
            user_info["referral_count"] = round(user_info.get("referral_count", 0) + pts, 2)
            await save_data_async()

            await send_ephemeral_reply(q.id, f"🎉 Mubarak ho! +{pts} Coins successfully aapke wallet me add ho gaye! 💎", chat_id=q.message.chat.id)

            # Live update card counter
            try:
                card = build_giveaway_claim_card(code, pts, max_uses, len(used_by))
                pyro_kb = to_pyrogram_markup(card["reply_markup"])
                await q.edit_message_text(card["text"], reply_markup=pyro_kb, parse_mode=ParseMode.HTML)
            except Exception as e:
                logger.debug(f"Live update card error: {e}")

        elif data.startswith("claimers_voucher_"):
            code = data.replace("claimers_voucher_", "").strip().upper()
            vouchers = DB.get("VOUCHERS", {})
            if code not in vouchers:
                return await send_ephemeral_reply(q.id, "❌ Voucher nahi mila.", chat_id=q.message.chat.id)
            v_data = vouchers[code]
            used_by = v_data.get("used_by", [])
            max_uses = v_data.get("max_uses", 1)
            pts = v_data.get("points", 0)

            if not used_by:
                return await send_ephemeral_reply(q.id, f"🎟️ <b>VOUCHER:</b> <code>{code}</code>\nAbhi tak kisi ne claim nahi kiya hai! (0/{max_uses} slots claimed)", chat_id=q.message.chat.id)

            claimer_lines = []
            for idx, c_uid in enumerate(used_by[:15], 1):
                u_info = DB.get("USER_DATA", {}).get(c_uid) or DB.get("USER_DATA", {}).get(str(c_uid), {})
                uname = u_info.get("name") or f"User {c_uid}"
                claimer_lines.append(f"{idx}. {uname} (+{pts} Coins)")

            summary = f"👥 <b>GIVEAWAY CLAIMERS LIST ({code})</b>\nClaimed: <b>{len(used_by)}/{max_uses}</b>\n\n" + "\n".join(claimer_lines)
            if len(used_by) > 15:
                summary += f"\n<i>...and {len(used_by) - 15} more students</i>"
            await send_ephemeral_reply(q.id, summary, chat_id=q.message.chat.id)

        elif data.startswith("copy_voucher_"):
            code = data.replace("copy_voucher_", "").strip().upper()
            await send_ephemeral_reply(q.id, f"🎟️ <b>Voucher Code:</b> <code>{code}</code>\nTap the code above to copy it!", chat_id=q.message.chat.id)

        elif data.startswith("bc_giveaway_"):
            if not is_admin(uid):
                return await send_ephemeral_reply(q.id, "❌ Sirf admins ke liye.", chat_id=q.message.chat.id)
            code = data.replace("bc_giveaway_", "").strip().upper()
            vouchers = DB.get("VOUCHERS", {})
            if code not in vouchers:
                return await send_ephemeral_reply(q.id, "❌ Voucher nahi mila.", chat_id=q.message.chat.id)
            v_data = vouchers[code]
            pts = v_data.get("points", 0)
            max_uses = v_data.get("max_uses", 1)
            used_count = len(v_data.get("used_by", []))

            await send_ephemeral_reply(q.id, f"🚀 Broadcasting Giveaway '{code}' to all active users...", chat_id=q.message.chat.id)
            
            card = build_giveaway_claim_card(code, pts, max_uses, used_count)
            pyro_kb = to_pyrogram_markup(card["reply_markup"])

            targets = list(DB.get("USER_DATA", {}).keys())
            sent_count = 0
            for t_uid in targets:
                try:
                    await client.send_message(int(t_uid), card["text"], reply_markup=pyro_kb, parse_mode=ParseMode.HTML)
                    sent_count += 1
                    await asyncio.sleep(0.04)
                except FloodWait as fw:
                    await asyncio.sleep(fw.value + 1)
                except Exception:
                    pass

            await send_ephemeral_reply(q.id, f"✅ Giveaway '{code}' successfully broadcasted to {sent_count} users! 🎉", chat_id=q.message.chat.id)

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

        # --- MY INFO / LIVE STUDENT WALLET VIEW ---
        elif data == "my_info":
            await q.answer()
            user_key = uid if uid in DB["USER_DATA"] else str(uid)
            u_data = DB["USER_DATA"].get(user_key, {})
            widget = build_student_wallet_widget(uid, u_data)
            pm = to_pyrogram_markup(widget.get("reply_markup"))
            try:
                await q.edit_message_text(widget["text"], reply_markup=pm, parse_mode=ParseMode.HTML)
            except MessageNotModified:
                pass
            return

        elif data.startswith("wallet_refresh_"):
            user_key = uid if uid in DB["USER_DATA"] else str(uid)
            u_data = DB["USER_DATA"].get(user_key, {})
            widget = build_student_wallet_widget(uid, u_data)
            pm = to_pyrogram_markup(widget.get("reply_markup"))
            try:
                await q.edit_message_text(widget["text"], reply_markup=pm, parse_mode=ParseMode.HTML)
                await q.answer("🔄 Live Wallet Refreshed!", show_alert=False)
            except MessageNotModified:
                await q.answer("✅ Wallet is already up-to-date!", show_alert=False)
            except Exception as e:
                logger.error(f"Error refreshing wallet: {e}")
                await q.answer()
            return

        # --- LIVE BATCH INSPECTOR WIDGET ---
        elif data.startswith("batch_inspect_"):
            try:
                cid = int(data.split("_")[2])
            except Exception:
                return await q.answer("❌ Invalid batch ID.", show_alert=True)
            cid_str = str(cid)
            has_access = (
                cid_str in DB.get("FREE_CHANNELS", {})
                or cid in DB.get("FREE_CHANNELS", {})
                or str(uid) in [str(x) for x in DB.get("PAID_USERS", {}).get(cid_str, [])]
                or int(uid) in DB.get("ADMINS", [])
            )
            widget = build_batch_details_widget(cid, uid, user_has_access=has_access)
            pm = to_pyrogram_markup(widget.get("reply_markup"))
            try:
                await q.edit_message_text(widget["text"], reply_markup=pm, parse_mode=ParseMode.HTML)
                await q.answer()
            except MessageNotModified:
                await q.answer("✅ Batch details up-to-date!", show_alert=False)
            except Exception as e:
                logger.error(f"Error inspecting batch: {e}")
                await q.answer()
            return

        # --- DEMO REQUEST FOR PAID BATCH ---
        elif data.startswith("req_demo_"):
            try:
                cid = int(data.split("_")[2])
            except Exception:
                cid = data.split("_")[2]
            bname = DB.get("ALL_CHATS", {}).get(cid) or DB.get("PAID_CHANNELS", {}).get(cid) or f"Batch {cid}"
            await q.answer("🎁 Demo Request Sent to Support Team!", show_alert=True)
            topic_id = await get_or_create_topic(q.from_user, client)
            if topic_id:
                try:
                    await client.send_message(
                        SUPPORT_GROUP_ID,
                        f"🎁 <b>DEMO / PREVIEW REQUEST</b>\n"
                        f"👤 Student: <code>{q.from_user.id}</code> (@{q.from_user.username or 'NoUser'})\n"
                        f"📦 Batch: <b>{bname}</b> (<code>{cid}</code>)\n"
                        f"<i>Student requested demo/sample lectures for this batch.</i>",
                        reply_to_message_id=topic_id,
                        parse_mode=ParseMode.HTML
                    )
                except Exception as e:
                    logger.error(f"Error sending demo request to topic: {e}")
            return

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
                    rp.expandable_quote([
                        ce("LINK", "🔗"), " ", rp.bold("Your Personal Referral Link:\n"),
                        rp.code(ref_link), "\n\n",
                        rp.italic("Niche diye gaye copy button ya sharing links se dosto ko invite karein!")
                    ]),
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

                try:
                    ref_msg = rp.message(*refer_elements)
                    await rp.edit(client, q.message.chat.id, q.message.id, ref_msg)
                except Exception as e_rich:
                    if "MESSAGE_NOT_MODIFIED" in str(e_rich):
                        return
                    logger.warning(f"Rich edit failed for Refer & Earn ({e_rich}), falling back to markdown")
                    fallback_text = (
                        "🎁 **Refer & Earn Rewards**\n"
                        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        "🏆 Milestone Bonus: Har 5 refers par +1 EXTRA Coin!\n"
                        "👑 VIP Elite Tag: 25 refers cross karne par auto unlock!\n"
                        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"👑 **Your Tier:** {'👑 VIP Referrer 🌟' if is_vip else 'Standard Referrer'}\n"
                        f"{('📊 **VIP Goal Progress:** `[' + progress_bar + f'] {total_inv}/25`') if not is_vip else ''}\n"
                        f"✅ **Total Referred:** `{total_inv} Users` | 💎 **Wallet Balance:** `{pts} Coins`\n\n"
                        f"🔗 **Your Personal Referral Link:**\n`{ref_link}`\n\n"
                        "_Niche diye gaye buttons se dosto ko invite karein:_"
                    )
                    kb = [
                        [InlineKeyboardButton("✈️ Share on Telegram", url=tg_share_url), InlineKeyboardButton("💬 Share on WhatsApp", url=wa_share_url)],
                        [InlineKeyboardButton("🏆 Top 10 Leaderboard", callback_data="show_leaderboard"), InlineKeyboardButton("🏠 Main Menu", callback_data="u_main")]
                    ]
                    await q.edit_message_text(fallback_text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
            except Exception as e:
                if "MESSAGE_NOT_MODIFIED" in str(e):
                    return
                logger.error(f"Refer & Earn Error: {e}")
                await q.answer("⚠️ Menu load hone me error aayi. Kripya wapas try karein.", show_alert=True)

        # =====================================================================
        # 🎁 GIVEAWAY & BATCH DEMANDS VIRAL SUITE CALLBACKS
        # =====================================================================
        elif data == "menu_giveaway":
            await q.answer()
            from .economy import build_giveaway_hub_msg
            bot_info = getattr(client, "me", None)
            bot_uname = bot_info.username if bot_info and bot_info.username else "H4R_Contact_bot"
            msg = build_giveaway_hub_msg(uid, bot_uname)
            await rp.edit(client, q.message.chat.id, q.message.id, msg)

        elif data == "giveaway_enter_prompt":
            await q.answer()
            user_key = str(uid) if str(uid) in DB.get("USER_DATA", {}) else uid
            user_coins = DB.get("USER_DATA", {}).get(user_key, {}).get("referral_count", 0)
            req_coins = DB.get("GIVEAWAY_CONFIG", {}).get("required_coins", 3)
            if user_coins < req_coins:
                return await q.answer(f"❌ Kam se kam {req_coins} Coins chahiye! Pehle refer karein.", show_alert=True)
            
            USER_FLOW_STATE[uid] = "wait_giveaway_batch"
            prompt_msg = rp.message(
                rp.heading("🎟️ Giveaway Entry Registration"),
                rp.divider(),
                rp.para("👉 ", rp.bold("Aapko Giveaway jeetne par konsa Batch chahiye?")),
                rp.expandable_quote([
                    rp.bold("📌 Example:\n"),
                    rp.code("SSC JE 2026 Foundation Batch C - Civil"), "\n\n",
                    rp.italic("Apne manpasand batch ka naam likh kar chat me bhejein.\n3 Coins aapki entry fee ke roop me use honge aur aapka Candidate Profile create ho jayega!")
                ]),
                rp.divider(),
                rp.buttons(rp.btn("❌ Cancel Entry", "menu_giveaway", style=rp.Style.DANGER))
            )
            await rp.edit(client, q.message.chat.id, q.message.id, prompt_msg)

        elif data == "giveaway_leaderboard" or data.startswith("gw_lb_page_"):
            await q.answer()
            from .economy import build_giveaway_leaderboard_msg
            page = 0
            if data.startswith("gw_lb_page_"):
                try:
                    page = int(data.split("_")[-1])
                except Exception:
                    page = 0
            msg = build_giveaway_leaderboard_msg(page=page, current_uid=uid)
            await rp.edit(client, q.message.chat.id, q.message.id, msg)

        elif data == "menu_batch_demands" or data.startswith("demands_page_"):
            await q.answer()
            from .batches import build_student_batch_demands_msg
            page = 0
            if data.startswith("demands_page_"):
                try:
                    page = int(data.split("_")[-1])
                except Exception:
                    page = 0
            bot_info = getattr(client, "me", None)
            bot_uname = bot_info.username if bot_info and bot_info.username else "H4R_Contact_bot"
            msg = build_student_batch_demands_msg(uid, bot_uname, page=page)
            await rp.edit(client, q.message.chat.id, q.message.id, msg)

        elif data.startswith("votedmd_"):
            d_idx = data.split("_")[1]
            bname = DB.get("DEMAND_SLUGS", {}).get(str(d_idx))
            if not bname:
                return await q.answer("❌ Batch demand not found.", show_alert=True)
            from .batches import cast_batch_demand_vote, build_student_batch_demands_msg
            ok, text = await cast_batch_demand_vote(client, uid, bname)
            await q.answer(text, show_alert=True)
            bot_info = getattr(client, "me", None)
            bot_uname = bot_info.username if bot_info and bot_info.username else "H4R_Contact_bot"
            msg = build_student_batch_demands_msg(uid, bot_uname, page=0)
            await rp.edit(client, q.message.chat.id, q.message.id, msg)

        elif data == "dash_giveaway":
            await q.answer()
            if not is_admin(uid):
                return await q.answer("❌ Sirf Admins ke liye.", show_alert=True)
            from .economy import build_admin_giveaway_panel_msg
            msg = build_admin_giveaway_panel_msg()
            await rp.edit(client, q.message.chat.id, q.message.id, msg)

        elif data == "gw_toggle_status":
            await q.answer()
            if not is_admin(uid):
                return await q.answer("❌ Sirf Admins ke liye.", show_alert=True)
            cfg = DB.setdefault("GIVEAWAY_CONFIG", {"active": True, "title": "🎉 Free Batch Mega Giveaway", "required_coins": 3})
            cfg["active"] = not cfg.get("active", True)
            await save_data_async()
            await q.answer(f"✅ Giveaway is now {'ACTIVE' if cfg['active'] else 'PAUSED'}!", show_alert=True)
            from .economy import build_admin_giveaway_panel_msg
            msg = build_admin_giveaway_panel_msg()
            await rp.edit(client, q.message.chat.id, q.message.id, msg)

        elif data == "gw_declare_winner":
            await q.answer()
            if not is_admin(uid):
                return await q.answer("❌ Sirf Admins ke liye.", show_alert=True)
            participants = DB.get("GIVEAWAY_PARTICIPANTS", {})
            if not participants:
                return await q.answer("❌ Abhi tak koi contestants nahi hain!", show_alert=True)
            sorted_p = sorted(participants.items(), key=lambda x: x[1].get("votes", 0), reverse=True)
            winner_id, winner_data = sorted_p[0]
            w_name = winner_data.get("name", "Student")
            w_batch = winner_data.get("batch", "Target Batch")
            w_votes = winner_data.get("votes", 0)

            try:
                await client.send_message(
                    int(winner_id),
                    f"🏆 **CONGRATULATIONS! YOU WON THE FREE BATCH GIVEAWAY!**\n━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"Aapne sabse zyada **{w_votes} Votes** jeetkar Free Batch Contest jeet liya hai! 🎉\n"
                    f"🎯 **Your Batch:** `{w_batch}`\n\n"
                    f"Admin aapke topic me free batch access turant approve kar rahe hain! 🚀",
                    parse_mode=ParseMode.MARKDOWN
                )
            except Exception:
                pass

            await q.answer(f"👑 Winner Declared:\n{w_name} ({w_votes} Votes)\nBatch: {w_batch}", show_alert=True)
            from .economy import build_admin_giveaway_panel_msg
            msg = build_admin_giveaway_panel_msg()
            await rp.edit(client, q.message.chat.id, q.message.id, msg)

        elif data == "gw_reset_confirm":
            await q.answer()
            if not is_admin(uid):
                return await q.answer("❌ Sirf Admins ke liye.", show_alert=True)
            DB["GIVEAWAY_PARTICIPANTS"] = {}
            DB["GIVEAWAY_VOTES"] = {}
            await save_data_async()
            await q.answer("🗑️ Giveaway contestants and votes reset successfully!", show_alert=True)
            from .economy import build_admin_giveaway_panel_msg
            msg = build_admin_giveaway_panel_msg()
            await rp.edit(client, q.message.chat.id, q.message.id, msg)

        # --- HOME MENU: VACANCIES & RECRUITMENT RADAR ---
        elif data == "menu_vacancies":
            try:
                await q.answer()
                url = getattr(config, "WEB_APP_URL", "") or "https://itsh4r01-user-manager.hf.space"
                vac_app_url = f"{url}?uid={uid}&tab=vacancies"
                vacs = DB.get("EXAM_VACANCIES", config.DEFAULT_VACANCIES)

                txt = (
                    "📢 **2026 Active Government Exam Vacancies & Alerts**\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    "⚡ *Live recruitment radar for competitive aspirants:*\n\n"
                )

                kb = []
                for idx, v in enumerate(vacs[:4], 1):
                    txt += (
                        f"**{idx}. {v.get('title')}**\n"
                        f"🏛️ *Org:* `{v.get('org')}` | 👥 *Posts:* `{v.get('total_posts')}`\n"
                        f"📅 *Last Date:* **{v.get('last_date')}**\n\n"
                    )
                    if v.get("apply_link"):
                        kb.append([InlineKeyboardButton(f"🚀 Apply: {v.get('org')}", url=v.get("apply_link"))])

                kb.append([InlineKeyboardButton("📱 Open Full Vacancy Radar (Mini App)", web_app=WebAppInfo(url=vac_app_url))])
                kb.append([InlineKeyboardButton("🏠 Main Menu", callback_data="u_main")])

                await q.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
            except Exception as e_vac:
                if "MESSAGE_NOT_MODIFIED" not in str(e_vac):
                    logger.error(f"Menu Vacancies Error: {e_vac}")
                    await q.answer("⚠️ Could not load vacancies.", show_alert=True)

        # --- HOME MENU: EXAM PRACTICE QUIZZES ---
        elif data == "menu_quizzes":
            try:
                await q.answer()
                url = getattr(config, "WEB_APP_URL", "") or "https://itsh4r01-user-manager.hf.space"
                quiz_app_url = f"{url}?uid={uid}&tab=study"

                txt = (
                    "🧠 **Multi-Subject Exam Practice Quiz Arena**\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    "🎯 *Available Subject Arenas:*\n"
                    "• 🏛️ **Indian Polity** (Articles, Writs, Amendments)\n"
                    "• 📜 **Modern History** (Freedom Struggle & Movements)\n"
                    "• 🌍 **Geography** (Rivers, Passes, National Parks)\n"
                    "• 📈 **Indian Economy** (RBI, Inflation, Budget)\n"
                    "• 📐 **CSAT Math & Aptitude** (Speed Math, Ratios)\n"
                    "• 🧠 **Logical Reasoning** (Coding, Puzzles)\n"
                    "• 🧬 **General Science** (Physics, Bio, Space Tech)\n"
                    "• 📰 **Current Affairs** (High-Yield 2026 Milestones)\n\n"
                    "🎁 **Daily Challenge:** Score ≥3 to claim **+1.0 Study Coins**!\n\n"
                    "👇 *Tap below to launch the interactive Quiz Arena:* "
                )
                kb = [
                    [InlineKeyboardButton("🧠 Launch Multi-Subject Quiz Arena", web_app=WebAppInfo(url=quiz_app_url))],
                    [InlineKeyboardButton("🏆 Top Leaderboard", callback_data="show_leaderboard")],
                    [InlineKeyboardButton("🏠 Main Menu", callback_data="u_main")]
                ]
                await q.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
            except Exception as e_quiz:
                if "MESSAGE_NOT_MODIFIED" not in str(e_quiz):
                    logger.error(f"Menu Quizzes Error: {e_quiz}")
                    await q.answer("⚠️ Could not load quiz arena.", show_alert=True)

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
                    if "pending_vote" in DB["USER_DATA"][user_key]:
                        cand_id = DB["USER_DATA"][user_key].pop("pending_vote")
                        await save_data_async()
                        from .economy import cast_giveaway_vote
                        await cast_giveaway_vote(client, q.from_user, cand_id)
                    if "pending_demand" in DB["USER_DATA"][user_key]:
                        d_idx = DB["USER_DATA"][user_key].pop("pending_demand")
                        await save_data_async()
                        bname = DB.get("DEMAND_SLUGS", {}).get(str(d_idx))
                        if bname:
                            from .batches import cast_batch_demand_vote
                            await cast_batch_demand_vote(client, uid, bname)
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
            u_data = DB["USER_DATA"].get(user_key, {})
            unlocked_ref_batches = u_data.get("unlocked_batches", [])
            perm_batches = u_data.get("permanent_batches", [])
            demos_batches = list(u_data.get("demos", {}).keys())
            free_joined = u_data.get("free_batches_joined", [])
            combined_unlocked = set(
                [int(x) for x in unlocked_ref_batches if str(x).lstrip('-').isdigit()] +
                [int(x) for x in perm_batches if str(x).lstrip('-').isdigit()] +
                [int(x) for x in demos_batches if str(x).lstrip('-').isdigit()]
            )

            # Prioritize candidate batches to check: unlocked/demos/free_joined + free channels
            candidate_cids = set(combined_unlocked)
            for f_cid in free_joined:
                try: candidate_cids.add(int(str(f_cid).strip()))
                except: pass
            for f_cid in DB.get("FREE_CHANNELS", {}).keys():
                try: candidate_cids.add(int(str(f_cid).strip()))
                except: pass

            batches_to_check = {cid: all_batches[cid] for cid in candidate_cids if cid in all_batches}
            sem = asyncio.Semaphore(5)

            async def check_member(cid, name):
                async with sem:
                    try:
                        cid_int = int(cid)
                    except Exception:
                        cid_int = None
                    is_unlocked = cid_int in combined_unlocked or str(cid) in [str(x) for x in combined_unlocked]
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
                        return (cid, name, "Unlocked")

                    return None

            results = await asyncio.gather(*[check_member(cid, name) for cid, name in batches_to_check.items()])
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
                if status_type == "Unlocked":
                    button_text = f"✨ {name} [UNLOCKED]"
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

        elif data.startswith("get_f_") or data.startswith("gen_link_"):
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
                    [InlineKeyboardButton("🚀 Join Batch", url=l.invite_link, style=ButtonStyle.PRIMARY)],
                    [InlineKeyboardButton("🔗 Share Batch with Friends", callback_data=f"share_btn_{cid}", style=ButtonStyle.SUCCESS)]
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
                    expire_date=datetime.now() + timedelta(hours=24),
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

                kb = [[InlineKeyboardButton("🔑 Send Join Request", url=l.invite_link)]]
                user_msg_obj = await client.send_message(
                    uid,
                    f"📨 <b>Access Request Created!</b>\n\n"
                    f"📦 <b>{bname}</b>\n\n"
                    f"👉 <b>Important:</b> Neeche diye gaye button par click karke channel join request submit karein.\n"
                    f"Admin ke approve karte hi aap channel me successfully add ho jayenge!",
                    reply_markup=InlineKeyboardMarkup(kb),
                    parse_mode=ParseMode.HTML,
                )
                await schedule_delete(client, user_msg_obj, delay=1800)
            except Exception as e:
                await client.send_message(uid, f"  Error: {e}")
    except Exception as e:
        if "MESSAGE_NOT_MODIFIED" in str(e):
            return
        logger.error(f"Callback Error: {e}")



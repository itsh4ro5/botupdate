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


def build_batch_requests_msg(page: int = 0):
    reqs = DB.get("BATCH_REQUESTS", {})
    statuses = DB.setdefault("BATCH_REQUEST_STATUS", {})

    if not reqs:
        return rp.message(
            rp.heading("📋 Batch Demands & Checklist"),
            rp.divider(),
            rp.expandable_quote([
                ce("INFO", "ℹ️"), " ", rp.bold("Abhi tak koi batch requests nahi aayi hain."), "\n\n",
                rp.italic("Jab users 'Request Batch' button se demand submit karenge, wo yahan checkbox checklist format me dikhengi.")
            ]),
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
        badge = ce("CHECK", "✅") if is_done else ce("ALERT", "🔴")
        status_tag = rp.marked(" [☑ Live] ") if is_done else rp.italic(" [☐ Pending] ")

        names = []
        user_db = DB.get("USER_DATA", {})
        for u in uids[:2]:
            u_info = user_db.get(u) or user_db.get(str(u), {})
            nm = u_info.get("name")
            un = u_info.get("username")
            if nm and un:
                names.append(f"{nm} (@{un})")
            elif nm:
                names.append(nm)
            elif un:
                names.append(f"@{un}")
            else:
                names.append(f"ID {u}")
        user_str = ", ".join(names) or f"{len(uids)} Students"
        if len(uids) > 2:
            user_str += f" (+{len(uids) - 2} more)"

        body_elements.append(
            rp.para(
                badge, " ", rp.bold(f"{idx}. {bname}"), "\n",
                "   ↳ ", rp.bold(f"👥 Demand ({len(uids)}): "), user_str, " — ", status_tag
            )
        )

    body_elements.append(rp.divider())
    body_elements.append(
        rp.italic("💡 Niche button se 1-click me batch Live karein. Students ko auto-notification chali jayegi!")
    )

    btn_rows = []
    for sub_idx, (bname, uids) in enumerate(page_items):
        item_global_idx = start_idx + sub_idx
        st = statuses.get(bname, "pending")
        is_done = (st == "done")
        check_icon = "☑" if is_done else "☐"
        icon_txt = "Live:" if is_done else "Upload:"
        short_name = (bname[:26] + "..") if len(bname) > 28 else bname
        label = f"{item_global_idx + 1}. {check_icon} {icon_txt} {short_name} ({len(uids)})"
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
        rp.btn("🗑️ Delete Mode", f"breq_delm_{page}", style=rp.Style.DANGER),
        rp.btn("🔙 Back to Terminal", "dash_home", style=rp.Style.DEFAULT)
    ))

    return rp.message(*body_elements, *btn_rows)


async def cmd_batchrequests(client: Client, message: Message):
    if not is_admin_msg(message):
        return
    try:
        reqs = DB.get("BATCH_REQUESTS", {})
        statuses = DB.setdefault("BATCH_REQUEST_STATUS", {})
        if not reqs:
            return await message.reply_text("📭 Abhi tak koi batch requests nahi aayi hain.")

        def _sort_key(item):
            bname, uids = item
            status = statuses.get(bname, "pending")
            status_rank = 0 if status != "done" else 1
            return (status_rank, -len(uids), bname.lower())

        sorted_items = sorted(reqs.items(), key=_sort_key)
        card = build_demands_console_card(sorted_items, page=0, tab="pending")
        pyro_kb = to_pyrogram_markup(card["reply_markup"])
        await message.reply_text(card["text"], reply_markup=pyro_kb, parse_mode=ParseMode.HTML)
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
    doc = message.document or (message.reply_to_message.document if message.reply_to_message else None)
    if not doc:
        return await message.reply_text(
            "📥 **RESTORE BATCHES GUIDE**\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Kripya apna MongoDB/JSON backup file chat me **Document (📎 File)** ke roop me bhejein aur caption me `/restorebatches` likhein.\n"
            "Ya fir kisi pehle se bheje gaye backup file par reply karke `/restorebatches` bhejein."
        )
    status_msg = await message.reply_text("⏳ **Downloading and inspecting backup file...**")
    temp_path = f"temp_restore_{message.id}.json"
    try:
        await message.download(file_name=temp_path) if message.document else await message.reply_to_message.download(file_name=temp_path)
        import restore_batches
        await status_msg.edit_text("⏳ **Restoring missing batches & syncing permissions...**")
        res = await restore_batches.run_batch_restore(temp_path)
        if res.get("success"):
            txt = (
                f"✅ **BATCH RESTORATION COMPLETE!**\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📦 **Restored Batches:** `{res.get('restored_count', 0)}`\n"
                f"⏩ **Skipped (Already Active):** `{res.get('skipped_count', 0)}`\n"
                f"❌ **Failed:** `{res.get('failed_count', 0)}`\n\n"
                f"✨ All missing batches restored into categories and database synced successfully!"
            )
        else:
            txt = f"❌ **Restoration Failed:** `{res.get('error')}`"
        await status_msg.edit_text(txt)
    except Exception as e:
        await status_msg.edit_text(f"❌ **Error during restore:** `{e}`")
    finally:
        if os.path.exists(temp_path):
            try: os.remove(temp_path)
            except: pass


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


async def cmd_extend_demo(client: Client, message: Message):
    if not is_admin_msg(message):
        return
    args = get_args(message)
    uid = None
    bid = None
    hours = None

    # Check topic resolution if in SUPPORT_GROUP_ID
    if message.chat and str(message.chat.id) == str(getattr(config, "SUPPORT_GROUP_ID", 0)):
        topic_id = getattr(message, "message_thread_id", None) or getattr(message, "reply_to_top_message_id", None)
        if not topic_id and message.reply_to_message:
            topic_id = getattr(message.reply_to_message, "message_thread_id", None) or message.reply_to_message.id
        if topic_id:
            for u, t in DB.get("USER_TOPICS", {}).items():
                if str(t) == str(topic_id):
                    uid = int(u)
                    break

    if len(args) == 1:
        try:
            hours = float(args[0].lower().replace("h", ""))
        except ValueError:
            pass
        if uid:
            user_key = uid if uid in DB["USER_DATA"] else (str(uid) if str(uid) in DB["USER_DATA"] else None)
            demos = DB.get("USER_DATA", {}).get(user_key, {}).get("demos", {}) if user_key else {}
            if demos:
                bid = list(demos.keys())[-1]
    elif len(args) == 2:
        try:
            if str(args[0]).startswith("-100"):
                bid = str(args[0])
                hours = float(args[1].lower().replace("h", ""))
            elif uid:
                bid = str(args[0])
                hours = float(args[1].lower().replace("h", ""))
            else:
                uid = int(args[0])
                hours = float(args[1].lower().replace("h", ""))
                user_key = uid if uid in DB["USER_DATA"] else (str(uid) if str(uid) in DB["USER_DATA"] else None)
                demos = DB.get("USER_DATA", {}).get(user_key, {}).get("demos", {}) if user_key else {}
                if demos:
                    bid = list(demos.keys())[-1]
        except ValueError:
            pass
    elif len(args) >= 3:
        try:
            uid, bid, hours = int(args[0]), str(args[1]), float(args[2].lower().replace("h", ""))
        except ValueError:
            pass

    if not uid or not bid or hours is None:
        return await message.reply_text("Usage: `/extend [hours]` (topic ke andar) ya `/extend <user_id> <batch_id> <hours>`", parse_mode=ParseMode.MARKDOWN)

    user_key = uid if uid in DB["USER_DATA"] else (str(uid) if str(uid) in DB["USER_DATA"] else None)
    if user_key and str(bid) in DB["USER_DATA"].get(user_key, {}).get("demos", {}):
        d = DB["USER_DATA"][user_key]["demos"][str(bid)]
        DB["USER_DATA"][user_key]["demos"][str(bid)] = {
            "expiry": max(
                (d["expiry"] if isinstance(d, dict) else float(d)), time.time()
            )
            + (hours * 3600),
            "warned": False,
        }
        await save_data_async()
        await message.reply_text(f"✅ Demo extended by `{hours} Hours` for user `{uid}` in batch `{bid}`.", parse_mode=ParseMode.MARKDOWN)
    else:
        await message.reply_text(f"⚠️ User `{uid}` ka batch `{bid}` me koi active demo nahi mila.", parse_mode=ParseMode.MARKDOWN)


def resolve_link_and_batch(message: Message, args: list):
    """
    Intelligently extracts link, target_uid, and batch_id from:
    1. Command text & arguments
    2. Replied message (text & caption)
    3. LINK_MAP (exact + invite hash match)
    4. Support Forum Topic ID -> Student mapping
    5. Active user requests in PENDING_REQUESTS
    6. Active user demos in USER_DATA
    """
    link = None
    target_uid = None
    batch_id = None
    hours = None

    full_texts = []
    if getattr(message, "text", None): full_texts.append(message.text)
    if getattr(message, "caption", None): full_texts.append(message.caption)
    if getattr(message, "reply_to_message", None):
        if getattr(message.reply_to_message, "text", None): full_texts.append(message.reply_to_message.text)
        if getattr(message.reply_to_message, "caption", None): full_texts.append(message.reply_to_message.caption)

    # 1. Look for invite links in full_texts and args
    for txt in full_texts:
        m = re.search(r"(https?://t\.me/(?:\+|joinchat/)[a-zA-Z0-9_\-]+)", txt)
        if m:
            link = m.group(1).strip().rstrip(".,;!/")
            break

    if not link:
        for arg in args:
            if "t.me" in arg or arg.startswith("+"):
                m = re.search(r"(https?://t\.me/(?:\+|joinchat/)[a-zA-Z0-9_\-]+)", arg)
                if m:
                    link = m.group(1).strip().rstrip(".,;!/")
                    break
                else:
                    link = arg.strip().rstrip(".,;!/")
                    break

    # 2. Extract hours if present in args
    for arg in args:
        a_clean = arg.lower().replace("h", "").strip()
        if a_clean.replace(".", "", 1).isdigit():
            val = float(a_clean)
            if 0 < val <= 720:
                hours = val
                break

    # 3. Resolve from LINK_MAP using link
    if link:
        ld = DB.get("LINK_MAP", {}).get(link)
        if not ld:
            clean_link = link.strip().rstrip("/")
            link_hash = clean_link.split("/")[-1].lstrip("+")
            for map_link, map_data in DB.get("LINK_MAP", {}).items():
                if map_link.strip().rstrip("/") == clean_link:
                    ld = map_data
                    link = map_link
                    break
                map_hash = map_link.strip().rstrip("/").split("/")[-1].lstrip("+")
                if link_hash and (link_hash == map_hash or link_hash in map_link or map_hash in clean_link):
                    ld = map_data
                    link = map_link
                    break
        if ld:
            target_uid = ld.get("u") if isinstance(ld, dict) else None
            batch_id = ld.get("b") if isinstance(ld, dict) else ld

    # 4. Resolve target_uid from Support Forum Topic
    if not target_uid and message.chat and str(message.chat.id) == str(getattr(config, "SUPPORT_GROUP_ID", 0)):
        topic_id = getattr(message, "message_thread_id", None) or getattr(message, "reply_to_top_message_id", None)
        if not topic_id and message.reply_to_message:
            topic_id = getattr(message.reply_to_message, "message_thread_id", None) or message.reply_to_message.id
        if topic_id:
            for u, t in DB.get("USER_TOPICS", {}).items():
                if str(t) == str(topic_id):
                    target_uid = int(u)
                    break

    # 5. Fallback user_id from replied message
    if not target_uid and message.reply_to_message:
        rep_txt = message.reply_to_message.text or message.reply_to_message.caption or ""
        m_u = re.search(r"tg://user\?id=(\d+)", rep_txt) or re.search(r"🆔\s*`?(\d+)`?", rep_txt) or re.search(r"User ID[:\s]+`?(\d+)`?", rep_txt, re.IGNORECASE)
        if m_u:
            target_uid = int(m_u.group(1))

    # 6. If target_uid is known but batch_id is not:
    if target_uid and not batch_id:
        for map_link, map_data in reversed(list(DB.get("LINK_MAP", {}).items())):
            if isinstance(map_data, dict) and str(map_data.get("u")) == str(target_uid):
                batch_id = map_data.get("b")
                if not link: link = map_link
                break

    if target_uid and not batch_id:
        p_batches = DB.get("PENDING_REQUESTS", {}).get(str(target_uid), {}).get("active_batches", {})
        if p_batches:
            batch_id = int(list(p_batches.keys())[-1])

    if target_uid and not batch_id:
        u_demos = DB.get("USER_DATA", {}).get(str(target_uid), {}).get("demos", {})
        if u_demos:
            batch_id = int(list(u_demos.keys())[-1])

    if not batch_id:
        for arg in args:
            if str(arg) in DB.get("ALL_CHATS", {}) or str(arg).startswith("-100"):
                try:
                    batch_id = int(arg)
                    break
                except ValueError:
                    pass

    if not batch_id and message.reply_to_message:
        rep_txt = message.reply_to_message.text or message.reply_to_message.caption or ""
        m_bid = re.search(r"\(?(-100\d+)\)?", rep_txt)
        if m_bid:
            try:
                batch_id = int(m_bid.group(1))
            except ValueError:
                pass
        if not batch_id:
            for cid, cname in DB.get("ALL_CHATS", {}).items():
                if cname and str(cname).lower() in rep_txt.lower():
                    batch_id = int(cid)
                    break

    # 7. Check positional args: /demo <user_id> <batch_id> or /per <user_id> <batch_id>
    if (not target_uid or not batch_id) and len(args) >= 2:
        try:
            a0, a1 = int(args[0]), int(args[1])
            if str(a0).startswith("-100"):
                batch_id, target_uid = a0, a1
            elif str(a1).startswith("-100"):
                target_uid, batch_id = a0, a1
            else:
                target_uid, batch_id = a0, a1
        except ValueError:
            pass

    return link, target_uid, batch_id, hours


async def cmd_approve_demo(client: Client, message: Message):
    if not is_admin_msg(message):
        return
    args = get_args(message)
    link, target_uid, batch_id, parsed_hours = resolve_link_and_batch(message, args)
    hours = parsed_hours if parsed_hours is not None else 3.0

    if not target_uid or not batch_id:
        return await message.reply_text(
            "❌ **Error: Request link ya Batch ID nahi mila.**\n\n"
            "💡 **Usage:**\n"
            "• `/demo <link> [hours]` (e.g. `/demo https://t.me/+xyz 6h`)\n"
            "• Ya topic ke request message par reply karke: `/demo`\n"
            "• Ya direct: `/demo <batch_id> [hours]` ya `/demo <user_id> <batch_id> [hours]`",
            parse_mode=ParseMode.MARKDOWN
        )

    batch_id = int(batch_id)
    target_uid = int(target_uid)

    try:
        approved_via_req = False
        try:
            await client.approve_chat_join_request(batch_id, target_uid)
            approved_via_req = True
        except Exception:
            pass

        try:
            await client.add_chat_members(batch_id, target_uid)
        except Exception:
            pass

        direct_link = None
        try:
            l_obj = await client.create_chat_invite_link(
                chat_id=batch_id,
                creates_join_request=False,
                name=f"Demo-{target_uid}",
                member_limit=1,
                expire_date=datetime.now() + timedelta(hours=int(hours) + 1),
            )
            direct_link = l_obj.invite_link
        except Exception as e_link:
            logger.error(f"Failed to create demo direct link: {e_link}")

        invalidate_membership_cache(target_uid, batch_id)
        expiry_time = time.time() + (hours * 3600)
        target_key = str(target_uid) if str(target_uid) in DB.get("USER_DATA", {}) else target_uid
        DB.setdefault("USER_DATA", {}).setdefault(target_key, {}).setdefault("demos", {})[str(batch_id)] = {
            "expiry": expiry_time,
            "warned": False,
        }
        clear_active_request(target_uid, batch_id)
        if link:
            DB.setdefault("LINK_MAP", {})[link] = {
                "u": target_uid,
                "b": batch_id,
                "demo": True,
                "ts": time.time()
            }
        await save_data_async()

        bname = DB.get("ALL_CHATS", {}).get(batch_id, f"Batch {batch_id}")
        kb = []
        if direct_link:
            kb.append([InlineKeyboardButton("🚀 Join Demo Batch", url=direct_link)])
        kb.append([InlineKeyboardButton("🏠 Main Menu", callback_data="u_main")])

        user_msg = (
            f"🎉 <b>Congratulations!</b>\n\n"
            f"Aapki request <b>{bname}</b> ke liye approve ho gayi hai.\n\n"
            f"🏷️ <b>Access Type:</b> Demo Trial\n"
            f"⏳ <b>Duration:</b> <code>{hours} Hours</code>\n\n"
            f"👉 <b>Neeche diye gaye button par click karke turant join karein:</b>"
        )
        try:
            await client.send_message(target_uid, user_msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
        except Exception as e_user:
            logger.error(f"User demo notification failed: {e_user}")

        admin_resp = (
            f"✅ <b>APPROVED (DEMO)</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 <b>User:</b> <code>{target_uid}</code>\n"
            f"📦 <b>Batch:</b> <b>{bname}</b> (<code>{batch_id}</code>)\n"
            f"⏳ <b>Duration:</b> <code>{hours} Hours</code>\n"
            f"⚡ <b>Join Request:</b> {'Approved (Auto-Joined)' if approved_via_req else 'Direct Link Generated'}\n"
            f"🔗 <b>Direct Link:</b> {direct_link or 'N/A'}\n\n"
            f"✨ User ko message aur direct Join Button deliver kar diya gaya hai!\n"
            f"🔄 <i>To make it permanent later, reply with:</i> <code>/per {link or ''}</code>"
        )
        await message.reply_text(admin_resp, parse_mode=ParseMode.HTML)
    except Exception as e:
        await message.reply_text(f"❌ Approval failed: {e}")


async def cmd_approve_perm(client: Client, message: Message):
    if not is_admin_msg(message):
        return
    args = get_args(message)
    link, target_uid, batch_id, _ = resolve_link_and_batch(message, args)

    if not target_uid or not batch_id:
        return await message.reply_text(
            "❌ **Error: Request link ya Batch ID nahi mila.**\n\n"
            "💡 **Usage:**\n"
            "• `/per <link>` ya `/perm <link>`\n"
            "• Ya topic ke request message par reply karke: `/per`\n"
            "• Ya direct: `/per <batch_id>` ya `/per <user_id> <batch_id>`",
            parse_mode=ParseMode.MARKDOWN
        )

    batch_id = int(batch_id)
    target_uid = int(target_uid)

    try:
        approved_via_join_request = False
        try:
            await client.approve_chat_join_request(batch_id, target_uid)
            approved_via_join_request = True
        except Exception as e_req:
            logger.info(f"approve_chat_join_request notice: {e_req}")

        try:
            await client.add_chat_members(batch_id, target_uid)
        except Exception:
            pass

        direct_join_link = None
        try:
            l_obj = await client.create_chat_invite_link(
                chat_id=batch_id,
                creates_join_request=False,
                name=f"Perm-{target_uid}",
                member_limit=1,
            )
            direct_join_link = l_obj.invite_link
        except Exception as e_link:
            logger.error(f"Failed to create direct perm link: {e_link}")

        invalidate_membership_cache(target_uid, batch_id)

        target_key = str(target_uid) if str(target_uid) in DB.get("USER_DATA", {}) else target_uid
        u_info = DB.setdefault("USER_DATA", {}).setdefault(target_key, {})
        u_info.setdefault("unlocked_batches", [])
        if batch_id not in u_info["unlocked_batches"]:
            u_info["unlocked_batches"].append(batch_id)
        u_info.setdefault("permanent_batches", [])
        if batch_id not in u_info["permanent_batches"]:
            u_info["permanent_batches"].append(batch_id)

        # Remove demo timer if any
        if "demos" in u_info:
            u_info["demos"].pop(str(batch_id), None)
            u_info["demos"].pop(int(batch_id), None)

        clear_active_request(target_uid, batch_id)
        if link:
            DB.get("LINK_MAP", {}).pop(link, None)
        await save_data_async()

        bname = DB.get("ALL_CHATS", {}).get(batch_id, f"Batch {batch_id}")

        kb = []
        if direct_join_link:
            kb.append([InlineKeyboardButton("🚀 Join Batch Now", url=direct_join_link)])
        kb.append([InlineKeyboardButton("🏠 Main Menu", callback_data="u_main")])

        user_msg = (
            f"🎉 <b>Congratulations!</b>\n\n"
            f"Aapki request <b>{bname}</b> ke liye approve ho gayi hai.\n\n"
            f"🏷️ <b>Access Type:</b> Lifetime Permanent Access\n\n"
            f"👉 <b>Neeche diye gaye button par click karke turant channel / group join karein:</b>\n"
            f"<i>(Yeh link sirf aapke liye 1-time valid hai)</i>"
        )
        try:
            await client.send_message(target_uid, user_msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
        except Exception as e_user:
            logger.error(f"User perm notification failed: {e_user}")

        admin_resp = (
            f"✅ <b>APPROVED (PERMANENT)</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 <b>User:</b> <code>{target_uid}</code>\n"
            f"📦 <b>Batch:</b> <b>{bname}</b> (<code>{batch_id}</code>)\n"
            f"🏷️ <b>Access:</b> <code>Lifetime Permanent</code>\n"
            f"⚡ <b>Join Request:</b> {'Approved (Auto-Joined)' if approved_via_join_request else 'Direct Link Generated'}\n"
            f"🔗 <b>Direct Link:</b> {direct_join_link or 'N/A'}\n\n"
            f"✨ User ko confirmation message aur direct Join Button deliver kar diya gaya hai!"
        )
        await message.reply_text(admin_resp, parse_mode=ParseMode.HTML)

    except Exception as e:
        await message.reply_text(f"❌ Approval failed: {e}")


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


def build_addbatch_category_msg(uid: int = None):
    categories = DB.get("CATEGORIES", DEFAULT_CATEGORIES)
    cat_rows = []
    row = []
    for i, cat in enumerate(categories):
        row.append(rp.btn(f"📁 {cat}", f"wcat_{i}", style=rp.Style.DEFAULT))
        if len(row) == 2:
            cat_rows.append(rp.buttons(*row))
            row = []
    if row:
        cat_rows.append(rp.buttons(*row))

    return rp.message(
        rp.heading("➕ Add Batch Wizard"),
        rp.para("Naya batch catalog me add karne ke liye pehle uski ", rp.bold("Category"), " chunein:"),
        rp.divider(),
        *cat_rows,
        rp.divider(),
        rp.buttons(
            rp.btn("❌ Cancel / Back", "wiz_cancel", style=rp.Style.DANGER)
        )
    )


async def cmd_addbatch_start(client: Client, message: Message, user_id: int = None, callback_query: CallbackQuery = None, back_cb: str = None):
    uid = user_id or (callback_query.from_user.id if callback_query and callback_query.from_user else None) or (message.from_user.id if message and message.from_user else None)
    if not uid or not is_admin(uid):
        return
    
    if not back_cb:
        back_cb = "dash_home" if is_owner(uid) else "goto_admin_panel"

    ADMIN_WIZARD[uid] = {
        "step": "ask_cat",
        "back_cb": back_cb
    }
    
    msg = build_addbatch_category_msg(uid)
    if callback_query and callback_query.message:
        await rp.edit(client, callback_query.message.chat.id, callback_query.message.id, msg)
    else:
        await rp.send(client, message.chat.id, msg)


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


# =====================================================================
# 🗳️ STUDENT BATCH DEMANDS & VIRAL VOTING CAMPAIGN
# =====================================================================

def build_student_batch_demands_msg(user_id: int, bot_username: str = "H4R_Contact_bot", page: int = 0):
    """
    Builds the interactive student-facing Batch Demands & Voting Campaign message.
    Students can vote on upcoming batches and share campaigns to rally votes.
    """
    reqs = DB.get("BATCH_REQUESTS", {})
    statuses = DB.setdefault("BATCH_REQUEST_STATUS", {})
    target_votes = DB.get("BATCH_DEMAND_TARGET", 30)

    # Filter only pending / active requests
    active_demands = [(bname, uids) for bname, uids in reqs.items() if statuses.get(bname) != "done"]
    active_demands.sort(key=lambda x: len(x[1]), reverse=True)
    total = len(active_demands)

    if total == 0:
        return rp.message(
            rp.heading("🗳️ Batch Demands & Voting Campaign"),
            rp.divider(),
            rp.expandable_quote([
                rp.bold("ℹ️ No Active Demands Right Now:\n\n"),
                "Abhi koi pending batch demand list me nahi hai. Aap pehle student ban kar naye batch ki demand submit karein!\n",
                f"Jaise hi kisi batch ko ", rp.bold(f"{target_votes} Votes"), " milenge, use sabse pehle channel me upload kiya jayega! 🚀"
            ]),
            rp.divider(),
            rp.buttons(
                rp.btn("➕ Request New Batch", "req_batch_btn", style=rp.Style.SUCCESS),
                rp.btn("🏠 Main Menu", "u_main", style=rp.Style.DANGER)
            )
        )

    # Store slug/index lookup for safe deep-linking
    slugs_map = DB.setdefault("DEMAND_SLUGS", {})
    for idx, (bname, _) in enumerate(active_demands, start=1):
        slugs_map[str(idx)] = bname

    PER_PAGE = 4
    total_pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    start_idx = page * PER_PAGE
    end_idx = min(start_idx + PER_PAGE, total)
    page_items = active_demands[start_idx:end_idx]

    body_elements = [
        rp.heading("🗳️ Batch Demands & Voting Campaign"),
        rp.divider(),
        rp.para(
            "📢 ", rp.bold("Target: "), f"Har batch ko upload hone ke liye ", rp.bold(f"{target_votes} Votes"), " chahiye!\n",
            rp.italic("Apne manpasand batch ko vote karein aur link apne college groups me share karke votes badhayein.")
        ),
        rp.marked(f" 📦 Active Demands: {total} | 🎯 Target: {target_votes} Votes | 📄 Page {page + 1}/{total_pages} "),
        rp.divider(),
    ]

    action_buttons = []
    for rel_idx, (bname, uids) in enumerate(page_items):
        global_idx = start_idx + rel_idx + 1
        voters_count = len(uids)
        pct = min(100, int((voters_count / target_votes) * 100))
        pbar = render_progress_bar(voters_count, target_votes, length=8)
        has_voted = user_id in uids
        voted_badge = "✅ Voted" if has_voted else "🗳️ Vote"
        
        deep_link = f"https://t.me/{bot_username}?start=demand_{global_idx}"
        needed = max(0, target_votes - voters_count)
        share_text = f"🔥 Bhaiyon, '{bname}' batch ke liye bas {needed} votes baaki hain! Is link pe click karke vote karo taaki jaldi upload ho: {deep_link}"
        share_url = f"https://t.me/share/url?url={urllib.parse.quote(deep_link)}&text={urllib.parse.quote(share_text)}"

        body_elements.append(
            rp.para(
                rp.bold(f"{global_idx}. {bname}\n"),
                rp.code(f"[{pbar}] {voters_count}/{target_votes} Votes ({pct}%)"),
                f" — ", rp.bold("Goal Reached! 🚀" if voters_count >= target_votes else f"{needed} more needed")
            )
        )

        row = []
        if not has_voted:
            row.append(rp.btn(f"🗳️ Vote #{global_idx}", f"votedmd_{global_idx}", style=rp.Style.SUCCESS))
        else:
            row.append(rp.btn(f"✅ Voted ({voters_count})", f"votedmd_{global_idx}", style=rp.Style.PRIMARY))
        
        row.append(rp.url_btn(f"📤 Share #{global_idx}", share_url, style=rp.Style.LINK))
        action_buttons.append(rp.buttons(*row))

    nav_btns = []
    if page > 0:
        nav_btns.append(rp.btn("⬅️ Prev", f"demands_page_{page - 1}", style=rp.Style.DEFAULT))
    if page < total_pages - 1:
        nav_btns.append(rp.btn("Next ➡️", f"demands_page_{page + 1}", style=rp.Style.DEFAULT))

    body_elements.append(rp.divider())
    body_elements.extend(action_buttons)
    if nav_btns:
        body_elements.append(rp.buttons(*nav_btns))

    body_elements.append(
        rp.buttons(
            rp.btn("➕ Request New Batch", "req_batch_btn", style=rp.Style.SUCCESS),
            rp.btn("🏠 Main Menu", "u_main", style=rp.Style.DANGER)
        )
    )
    return rp.message(*body_elements)


async def cast_batch_demand_vote(client: Client, user_id: int, bname: str):
    """Adds a vote for a batch demand and notifies admins if goal reached."""
    reqs = DB.setdefault("BATCH_REQUESTS", {})
    reqs.setdefault(bname, [])
    
    if user_id in reqs[bname]:
        return False, f"⚠️ Aap pehle hi **{bname}** ke liye vote kar chuke hain!"
    
    reqs[bname].append(user_id)
    await save_data_async()
    
    voters = len(reqs[bname])
    target = DB.get("BATCH_DEMAND_TARGET", 30)
    
    # Target reached alert
    if voters == target:
        from config import SUPPORT_GROUP_ID
        if SUPPORT_GROUP_ID:
            try:
                await client.send_message(
                    chat_id=int(SUPPORT_GROUP_ID),
                    text=f"🚨 <b>BATCH DEMAND GOAL REACHED!</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                         f"📦 Batch: <b>{bname}</b>\n"
                         f"🎯 Target: <b>{voters}/{target} Votes Reached!</b>\n"
                         f"Priority upload required!",
                    parse_mode=ParseMode.HTML
                )
            except Exception:
                pass

    return True, f"✅ Aapka vote **{bname}** ke liye count ho gaya!\n📊 Total Votes: **{voters}/{target}**"


async def cmd_demands(client: Client, message: Message):
    """Command /demands handler"""
    if not message.from_user:
        return
    bot_info = getattr(client, "me", None)
    bot_uname = bot_info.username if bot_info and bot_info.username else "H4R_Contact_bot"
    msg = build_student_batch_demands_msg(message.from_user.id, bot_uname, page=0)
    await rp.send(client, message.chat.id, msg)




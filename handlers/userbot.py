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
    if not is_admin_msg(message):
        return

    # Mode 1: Reply to a specific message to delete it (and its bridged support topic message)
    if message.reply_to_message:
        key = (message.chat.id, message.reply_to_message.id)
        if key in MESSAGE_MAP:
            tc, tm = MESSAGE_MAP[key]
            try:
                await client.delete_messages(tc, tm)
                del MESSAGE_MAP[key]
                if (tc, tm) in MESSAGE_MAP:
                    del MESSAGE_MAP[(tc, tm)]
            except Exception:
                pass
        try:
            await client.delete_messages(message.chat.id, [message.reply_to_message.id, message.id])
        except Exception as e:
            logger.error(f"Delete failed: {e}")
            msg = await message.reply_text(f"  Delete failed: {e}")
            await schedule_delete(client, msg)
        return

    # Mode 2: Official Telegram Batch Delete /del <N> (up to 100 messages at once)
    args = get_args(message)
    if args and args[0].isdigit():
        count = min(int(args[0]), 100)
        msg_ids = [message.id]
        try:
            async for m in client.get_chat_history(message.chat.id, limit=count, offset_id=message.id):
                msg_ids.append(m.id)
            await client.delete_messages(message.chat.id, msg_ids)
        except Exception as e:
            logger.error(f"Bulk delete error: {e}")


async def cmd_superfwd_start(client: Client, message: Message):
    source_id = message.text.strip()
    uid = message.from_user.id
    ADMIN_WIZARD[uid] = {"step": "superfwd_target", "source": source_id}
    await message.reply_text(
        f"✅ Source ID `{source_id}` saved.\n\n"
        "**Step 2/7:** Kahan forward karna hai? Us **Target Group/Channel ID** ko bhejein (e.g. `-10087654321`):",
        parse_mode=ParseMode.MARKDOWN
    )


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
                        new_topic_id = await safe_create_forum_topic(bot_client, dest_id, raw_topic_name)
                        if not new_topic_id:
                            new_topic_id = await safe_create_forum_topic(userbot, dest_id, raw_topic_name)
                        if new_topic_id:
                            created_topics[topic_key] = new_topic_id
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


async def run_topic_arrangement_engine(
    bot_client: Client,
    message: Message,
    source_str: str,
    dest_str: str,
    topic_tag: str = "Topic:",
    start_msg_id: int = 1,
    end_msg_id: int = 0,
    clean_caption: bool = False
):
    uid = message.from_user.id
    if not hasattr(bot_client, "cancel_tasks"):
        bot_client.cancel_tasks = set()
    bot_client.cancel_tasks.discard(uid)

    from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    from pyrogram.errors import FloodWait
    from pyrogram import Client as PyroClient
    from config import DB, API_ID, API_HASH, safe_create_forum_topic
    import asyncio
    import re
    import time

    cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("🛑 Cancel Task", callback_data=f"cancel_task_{uid}")]])

    try:
        source_id = int(source_str)
        dest_id = int(dest_str)
    except ValueError:
        return await message.reply_text("❌ Error: IDs numeric honi chahiye (e.g. `-100...`).")

    session_string = DB.get("USERBOT_SESSION") or getattr(config, "SESSION_STRING", None)
    if not session_string or not API_ID:
        return await message.reply_text("❌ **Userbot Not Logged In!** Dashboard se pehle Userbot session connect karein.")

    progress_msg = await message.reply_text("⏳ **Step 1/3:** Userbot Engine starting & verifying channel access...", reply_markup=cancel_kb)

    userbot = PyroClient(
        f"topicarr_{uid}_{int(time.time())}",
        api_id=API_ID,
        api_hash=API_HASH,
        session_string=session_string,
        in_memory=True
    )

    try:
        await userbot.start()

        # 1. Access Check for Source Channel
        try:
            source_chat = await userbot.get_chat(source_id)
            source_title = source_chat.title or str(source_id)
        except Exception as e:
            await userbot.stop()
            return await progress_msg.edit_text(
                f"❌ **SOURCE ACCESS DENIED!**\n\n"
                f"Userbot ko Source Channel (`{source_id}`) ka access nahi mila.\n\n"
                f"👉 **Solution:** Jis number se Userbot login hai, use Source Channel me Admin ya Member banayein.\n\n"
                f"Error Details: `{e}`"
            )

        # 2. Access Check for Destination Supergroup
        try:
            dest_chat = await userbot.get_chat(dest_id)
            dest_title = dest_chat.title or str(dest_id)
            if not getattr(dest_chat, "is_forum", False):
                await progress_msg.edit_text(
                    f"⚠️ **Notice:** Destination Supergroup (`{dest_title}`) me Forum Topics mode ON nahi tha! Kripya check karein."
                )
                await asyncio.sleep(2)
        except Exception as e:
            await userbot.stop()
            return await progress_msg.edit_text(
                f"❌ **DESTINATION ACCESS DENIED!**\n\n"
                f"Userbot ko Destination Supergroup (`{dest_id}`) ka access nahi mila.\n\n"
                f"👉 **Solution:** Userbot aur Bot dono ko Destination Supergroup me **Admin (with Manage Topics & Post Messages)** banayein.\n\n"
                f"Error Details: `{e}`"
            )

        await progress_msg.edit_text(
            f"✅ **Access Verified!**\n"
            f"📥 Source: `{source_title}`\n"
            f"📤 Destination: `{dest_title}`\n\n"
            f"⏳ **Step 2/3:** Calculating message range & loading content...",
            reply_markup=cancel_kb
        )

        # 3. Determine message range
        if end_msg_id <= 0:
            async for last_m in userbot.get_chat_history(source_id, limit=1):
                end_msg_id = last_m.id
                break

        if end_msg_id <= 0:
            end_msg_id = 5000

        if start_msg_id <= 0:
            start_msg_id = 1

        min_id = min(start_msg_id, end_msg_id)
        max_id = max(start_msg_id, end_msg_id)
        message_ids = list(range(min_id, max_id + 1))

        all_messages = []
        for i in range(0, len(message_ids), 200):
            if uid in bot_client.cancel_tasks:
                break
            chunk = message_ids[i:i + 200]
            try:
                msgs = await userbot.get_messages(source_id, chunk)
                if not isinstance(msgs, list):
                    msgs = [msgs]
                for m in msgs:
                    if m and not getattr(m, "empty", False):
                        all_messages.append(m)
                await asyncio.sleep(0.3)
            except Exception as fe:
                logger.warning(f"Error fetching chunk: {fe}")

        if uid in bot_client.cancel_tasks:
            await userbot.stop()
            return await progress_msg.edit_text("🛑 **Topic Arrangement Task Cancelled by User.**")

        total_msgs = len(all_messages)
        if total_msgs == 0:
            await userbot.stop()
            return await progress_msg.edit_text(f"❌ Error: ID `{min_id}` se `{max_id}` ke beech koi messages nahi mile.")

        await progress_msg.edit_text(
            f"✅ **Loaded {total_msgs} messages!**\n\n"
            f"⏳ **Step 3/3:** Arranging files into respective topics via User Session...",
            reply_markup=cancel_kb
        )

        # 4. Process and sort messages into topics
        tag_clean = topic_tag.rstrip(":").strip() if topic_tag else "Topic"
        topic_pattern = rf"(?:^|\n)\s*{re.escape(tag_clean)}\s*[:\-]\s*(.*?)(?=\s+Batch:|\n|$)"

        created_topics = {}       # topic_key -> topic_id
        topic_counts = {}         # topic_key -> count
        forwarded_count = 0
        scanned_count = 0
        skipped_count = 0
        start_time = time.time()

        for m in all_messages:
            if uid in bot_client.cancel_tasks:
                await progress_msg.edit_text(f"🛑 **Task Stopped by User!**\nForwarded `{forwarded_count}` files before stopping.")
                break

            scanned_count += 1
            cap = m.caption or m.text or ""

            # Only process media or documents or videos or audio or text
            if not (m.video or m.document or m.photo or m.audio or m.animation or m.text):
                continue

            # Match topic name from caption
            t_match = re.search(topic_pattern, cap, re.IGNORECASE)
            if t_match:
                raw_topic_name = t_match.group(1).strip()
            else:
                raw_topic_name = "📁 Miscellaneous / General"

            raw_topic_name = raw_topic_name[:120].strip()
            topic_key = raw_topic_name.lower()

            # Ensure topic exists in destination group
            if topic_key not in created_topics:
                new_topic_id = await safe_create_forum_topic(userbot, dest_id, raw_topic_name)
                if not new_topic_id and bot_client:
                    new_topic_id = await safe_create_forum_topic(bot_client, dest_id, raw_topic_name)

                if new_topic_id:
                    created_topics[topic_key] = int(new_topic_id)
                    topic_counts[topic_key] = 0
                    try:
                        await userbot.send_message(
                            dest_id,
                            f"📁 **Topic:** `{raw_topic_name}`\n━━━━━━━━━━━━━━━━━━━━━━━━━\n📌 *All lectures, notes, and study material related to {raw_topic_name} are archived here.*",
                            message_thread_id=int(new_topic_id)
                        )
                    except Exception:
                        pass
                else:
                    logger.warning(f"Failed to create topic '{raw_topic_name}' in {dest_id}")

            topic_id = created_topics.get(topic_key)

            # Copy message using Userbot (without Forwarded From tag, preserving original caption & watermark)
            copy_success = False
            for attempt in range(3):
                try:
                    kwargs = {"message_thread_id": int(topic_id)} if topic_id else {}
                    if clean_caption and cap:
                        kwargs["caption"] = re.sub(r'(?i)(?:^|\n)\s*Extracted\s+By\s*[:➤\-]?\s*.*', '', cap).strip()

                    await userbot.copy_message(
                        chat_id=dest_id,
                        from_chat_id=source_id,
                        message_id=m.id,
                        **kwargs
                    )
                    forwarded_count += 1
                    topic_counts[topic_key] = topic_counts.get(topic_key, 0) + 1
                    copy_success = True
                    await asyncio.sleep(1.2)
                    break
                except FloodWait as fw:
                    await asyncio.sleep(fw.value + 1)
                except Exception as ce:
                    logger.warning(f"Error copying msg {m.id}: {ce}")
                    await asyncio.sleep(0.5)

            if not copy_success:
                skipped_count += 1

            # Live progress update every 15 items
            if scanned_count % 15 == 0 or scanned_count == total_msgs:
                try:
                    elapsed = int(time.time() - start_time)
                    mins, secs = divmod(elapsed, 60)
                    time_str = f"{mins}m {secs}s" if mins else f"{secs}s"

                    recent_topics_str = ", ".join([k.title() for k in list(created_topics.keys())[-3:]]) or "None"
                    await progress_msg.edit_text(
                        f"⚡ **Topic Arrangement In Progress...**\n━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"📥 **Source:** `{source_title}`\n"
                        f"📤 **Destination:** `{dest_title}`\n"
                        f"🏷️ **Active Topic:** `{raw_topic_name}`\n\n"
                        f"📊 **Live Metrics:**\n"
                        f"• 🔍 **Scanned:** `{scanned_count} / {total_msgs}`\n"
                        f"• 📁 **Topics Created:** `{len(created_topics)}`\n"
                        f"• 📤 **Files Forwarded:** `{forwarded_count}`\n"
                        f"• ⚠️ **Skipped/Errors:** `{skipped_count}`\n"
                        f"• ⏱️ **Elapsed Time:** `{time_str}`\n\n"
                        f"📌 *Recent Topics:* `{recent_topics_str}`\n\n"
                        f"*(Userbot session is copying files cleanly without forward tag)*",
                        reply_markup=cancel_kb,
                        parse_mode=ParseMode.MARKDOWN
                    )
                except Exception:
                    pass

        await userbot.stop()

        if uid not in bot_client.cancel_tasks:
            topics_summary = []
            for t_k, count in sorted(topic_counts.items(), key=lambda x: -x[1])[:10]:
                topics_summary.append(f"• **{t_k.title()}:** `{count} files`")

            summary_str = "\n".join(topics_summary) if topics_summary else "• None"
            if len(topic_counts) > 10:
                summary_str += f"\n• ... aur `{len(topic_counts) - 10}` aur topics!"

            await progress_msg.edit_text(
                f"🎉 **TOPIC ARRANGEMENT COMPLETED!**\n━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📥 **Source:** `{source_title}`\n"
                f"📤 **Destination:** `{dest_title}`\n\n"
                f"📊 **Final Statistics:**\n"
                f"📁 **Total Topics Created:** `{len(created_topics)}`\n"
                f"📤 **Total Files Forwarded:** `{forwarded_count}`\n"
                f"🔍 **Total Messages Scanned:** `{total_msgs}`\n\n"
                f"📑 **Top Topics Arranged:**\n{summary_str}\n\n"
                f"✨ *Sabhi files unke respective topics me bina forward tag ke successfully copy ho chuki hain!*",
                parse_mode=ParseMode.MARKDOWN
            )
    except Exception as err:
        import traceback
        traceback.print_exc()
        try:
            await userbot.stop()
        except Exception:
            pass
        await progress_msg.edit_text(f"❌ **Topic Arranger Error:** `{err}`")


async def cmd_topicarrange(client: Client, message: Message):
    if not is_admin_msg(message):
        return
    uid = message.from_user.id
    from config import DB, API_ID
    session_string = DB.get("USERBOT_SESSION") or getattr(config, "SESSION_STRING", None)
    if not session_string or not API_ID:
        return await message.reply_text("❌ **Userbot Not Logged In!** Dashboard ya Owner panel se pehle Userbot login karein.", parse_mode=ParseMode.MARKDOWN)

    ADMIN_WIZARD[uid] = {"step": "topicarr_source", "back_cb": "dash_db"}
    cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="wiz_cancel")]])
    await message.reply_text(
        "🗂️ **Topic Arrangement Tool Started!**\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "👉 **Step 1/4:** Kripya **Source Channel ID** bhejein jahan se files / content arrange karni hain (e.g. `-1001234567890`):",
        reply_markup=cancel_kb,
        parse_mode=ParseMode.MARKDOWN
    )



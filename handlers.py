import io, os, re, time, asyncio
from datetime import datetime
from pyrogram import Client
from pyrogram.errors import FloodWait
from telegram import Update, ChatMember, InlineKeyboardButton, InlineKeyboardMarkup, BotCommandScopeChat, BotCommand, InputMediaPhoto, InputMediaVideo, InputMediaDocument, InputMediaAudio, InputMediaAnimation
from telegram.constants import ChatType, ParseMode
from telegram.error import Forbidden, RetryAfter, BadRequest
from telegram.ext import ContextTypes

from config import *

# --- MENU SETTERS ---
async def set_role_based_commands(user_id, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_cmds = [BotCommand("start", "Open Main Menu"), BotCommand("id", "Get Telegram ID"), BotCommand("myinfo", "Check Active Demos")]
        if str(user_id) == str(OWNER_ID) or is_admin(user_id):
            admin_cmds = user_cmds + [BotCommand("stats", "Bot Statistics"), BotCommand("batchstats", "Batch Info")]
            await context.bot.set_my_commands(admin_cmds, scope=BotCommandScopeChat(user_id))
        else: await context.bot.set_my_commands(user_cmds, scope=BotCommandScopeChat(user_id))
    except Exception: pass

# --- COMMANDS ---
async def cmd_add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    try:
        new_admin = int(context.args[0])
        if new_admin not in DB["ADMIN_IDS"]: DB["ADMIN_IDS"].append(new_admin); await save_data_async(); msg = await update.effective_message.reply_text(f"✅ User {new_admin} is now Admin.")
        else: msg = await update.effective_message.reply_text("⚠️ Already Admin.")
    except Exception: pass

async def cmd_del_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to delete the replied-to message."""
    if not is_admin(update.effective_user.id) or not update.message or not update.message.reply_to_message: 
        return
    
    key = (update.effective_chat.id, update.message.reply_to_message.message_id)
    if key in MESSAGE_MAP:
        tc, tm = MESSAGE_MAP[key]
        try:
            await context.bot.delete_message(tc, tm)
            await update.message.reply_to_message.delete()
            await update.message.delete()
            del MESSAGE_MAP[key]
            if (tc, tm) in MESSAGE_MAP:
                del MESSAGE_MAP[(tc, tm)]
        except Exception as e:
            logger.error(f"Error deleting message: {e}")
            msg = await update.effective_message.reply_text(f"⚠️ Delete failed: {e}")
            await schedule_delete(context, msg)
    else:
        try:
            await update.message.reply_to_message.delete()
            await update.message.delete()
        except Exception as e:
            logger.error(f"Delete failed: {e}")

async def cmd_del_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    try:
        target = int(context.args[0])
        if target in DB["ADMIN_IDS"]: DB["ADMIN_IDS"].remove(target); await save_data_async(); msg = await update.effective_message.reply_text(f"🗑 User {target} removed.")
    except Exception: pass

async def cmd_sync(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    msg = await update.effective_message.reply_text("🔄 Background sync started manually.")
    context.job_queue.run_once(background_sync, 1)
    await schedule_delete(context, msg)

async def cmd_joinall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    if not SESSION_STRING or not API_ID: return await update.effective_message.reply_text("❌ Error: Env variables missing.")
    msg = await update.effective_message.reply_text("⏳ Auto-joining userbot...")
    client = Client("temp_bot", api_id=API_ID, api_hash=API_HASH, session_string=SESSION_STRING, in_memory=True)
    try: await client.start(); userbot_id = (await client.get_me()).id; await client.stop()
    except Exception as e: return await msg.edit_text(f"❌ Error: {e}")
    all_chats = [MANDATORY_CHANNEL_ID] + list(DB["FREE_CHANNELS"].keys()) + list(DB["PAID_CHANNELS"].keys())
    success = failed = 0
    for cid in all_chats:
        try: await context.bot.promote_chat_member(chat_id=int(cid), user_id=userbot_id, can_invite_users=True, can_manage_chat=True); success += 1; await asyncio.sleep(0.5) 
        except Exception: failed += 1
    await msg.edit_text(f"✅ **Auto-Join Process Pura Hua!**\nSuccess: `{success}`\nFailed: `{failed}`", parse_mode=ParseMode.MARKDOWN)

async def cmd_lockpaid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    DB["PAID_LOCKED"] = not DB.get("PAID_LOCKED", False); await save_data_async()
    await update.effective_message.reply_text("Paid Batches **LOCKED 🔐**." if DB["PAID_LOCKED"] else "Paid Batches **UNLOCKED 🔓**.", parse_mode=ParseMode.MARKDOWN)

async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat, user, msg_obj = update.effective_chat, update.effective_user, update.effective_message
    text = f"👤 **Your User ID:** `{user.id}`" if chat.type == ChatType.PRIVATE and user else f"🆔 **Chat ID:** `{chat.id}`"
    if chat.type != ChatType.PRIVATE:
        if msg_obj and msg_obj.is_topic_message and msg_obj.message_thread_id: text += f"\n🧵 **Topic ID:** `{msg_obj.message_thread_id}`"
        if user: text += f"\n👤 **User ID:** `{user.id}`"
    try:
        if msg_obj: await msg_obj.reply_text(text, parse_mode=ParseMode.MARKDOWN)
        else: await context.bot.send_message(chat.id, text, parse_mode=ParseMode.MARKDOWN)
    except Exception: pass
    if user and is_admin(user.id): await schedule_delete(context, msg_obj)

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)

async def cmd_backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    save_data_sync()
    if os.path.exists(DATA_FILE): await update.effective_message.reply_document(document=open(DATA_FILE, "rb"), caption="DB Backup")

async def cmd_all_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    report = f"ALL USERS DUMP - {datetime.now()}\n" + "-" * 40 + "\nID | Name | Username\n"
    for uid, data in DB["USER_DATA"].items(): report += f"{uid} | {data.get('name')} | @{data.get('username')}\n"
    f = io.BytesIO(report.encode("utf-8")); f.name = "all_users.txt"
    await update.effective_message.reply_document(document=f, caption="✅ All Users List")

async def cmd_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id) or not context.args: return
    try: target = int(context.args[0])
    except ValueError: return
    if target not in DB["BLOCKED_USERS"] and target != OWNER_ID: 
        await execute_universal_kick(target, context, permanent_ban=True)
        await update.effective_message.reply_text(f"🚫 User `{target}` BANNED.", parse_mode=ParseMode.MARKDOWN)

async def cmd_unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id) or not context.args: return
    try: target = int(context.args[0])
    except ValueError: return
    if target in DB["BLOCKED_USERS"]: 
        DB["BLOCKED_USERS"].remove(target); await save_data_async()
        all_channels = list(DB["FREE_CHANNELS"].keys()) + list(DB["PAID_CHANNELS"].keys()) + [MANDATORY_CHANNEL_ID]
        for bid in all_channels:
            try: await context.bot.unban_chat_member(int(bid), target)
            except Exception: pass
        await update.effective_message.reply_text(f"✅ User `{target}` UNBLOCKED.", parse_mode=ParseMode.MARKDOWN)

async def cmd_reset_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id) or len(context.args) == 0: return
    target_uid = int(context.args[0]); user_key = target_uid if target_uid in DB["USER_DATA"] else str(target_uid)
    if user_key in DB["USER_DATA"]:
        DB["USER_DATA"][user_key]["demos"] = {}; DB["USER_DATA"][user_key]["demo_history"] = []
        if target_uid in DB["BLOCKED_USERS"]: DB["BLOCKED_USERS"].remove(target_uid)
        await save_data_async(); await update.effective_message.reply_text(f"✅ User `{target_uid}` reset.", parse_mode=ParseMode.MARKDOWN)

async def cmd_find_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id) or not context.args: return
    query = context.args[0].replace("@", "").lower(); found = []
    for uid, data in DB["USER_DATA"].items():
        if query in data.get("username", "").lower(): found.append(f"🆔 `{uid}` | @{data.get('username', '')}")
    await update.effective_message.reply_text("🔍 **Found:**\n\n" + "\n".join(found) if found else "❌ Not found.", parse_mode=ParseMode.MARKDOWN)

async def cmd_addcat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id) or not context.args: return
    new_cat = " ".join(context.args).strip()
    categories = DB.get("CATEGORIES", DEFAULT_CATEGORIES)
    if new_cat not in categories: DB["CATEGORIES"].append(new_cat); await save_data_async()
    await update.effective_message.reply_text(f"✅ Added Category: {new_cat}")

async def cmd_setcategory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id) or not context.args: return
    try:
        cid = int(context.args[0])
        kb = [[InlineKeyboardButton(c, callback_data=f"setexistingcat_{cid}_{i}")] for i, c in enumerate(DB.get("CATEGORIES", DEFAULT_CATEGORIES))]
        await update.effective_message.reply_text(f"Nayi category select karein for `{cid}`:", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
    except Exception:
        await update.effective_message.reply_text("❌ Error: Valid Batch ID bhejein.")

async def cmd_delcat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    kb = [[InlineKeyboardButton(f"🗑 Delete: {c}", callback_data=f"delcat_{i}")] for i, c in enumerate(DB.get("CATEGORIES", DEFAULT_CATEGORIES)) if c != "Other Batches"]
    kb.append([InlineKeyboardButton("❌ Cancel", callback_data="dash_home")])
    await update.effective_message.reply_text("🗑 **Delete Category:**", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

async def cmd_setcategory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id) or not context.args: return
    cid = int(context.args[0])
    kb = [[InlineKeyboardButton(c, callback_data=f"setexistingcat_{cid}_{i}")] for i, c in enumerate(DB.get("CATEGORIES", DEFAULT_CATEGORIES))]
    await update.effective_message.reply_text(f"Nayi category select karein for {cid}:", reply_markup=InlineKeyboardMarkup(kb))

async def cmd_batch_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    msg = await update.effective_message.reply_text("⏳ Calculating...")
    text = "📊 **BATCH STATS**\n\n"
    for cid, name in {**DB["FREE_CHANNELS"], **DB["PAID_CHANNELS"]}.items():
        try: count = await context.bot.get_chat_member_count(cid)
        except Exception: count = "N/A"
        text += f"📂 **{name}** | ID: `{cid}` | Members: `{count}`\n"
    await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN)

async def cmd_set_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id) or len(context.args) < 2: return
    DB["CUSTOM_WELCOMES"][int(context.args[0])] = " ".join(context.args[1:]); await save_data_async(); await update.effective_message.reply_text("✅ Welcome Set.")

async def cmd_set_testbot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id) or not context.args: return
    DB["TEST_BOT_LINK"] = context.args[0]; await save_data_async(); await update.effective_message.reply_text("✅ Test bot link updated.")

async def cmd_extend_demo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id) or len(context.args) < 3: return
    uid, bid, hours = int(context.args[0]), str(context.args[1]), float(context.args[2])
    if uid in DB["USER_DATA"] and bid in DB["USER_DATA"].get(uid, {}).get("demos", {}):
        d = DB["USER_DATA"][uid]["demos"][bid]
        DB["USER_DATA"][uid]["demos"][bid] = {"expiry": max((d["expiry"] if isinstance(d, dict) else float(d)), time.time()) + (hours * 3600), "warned": False}
        await save_data_async(); await update.effective_message.reply_text("✅ Extended.")

async def cmd_kick_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id) or len(context.args) < 2: return
    uid, bid = int(context.args[0]), int(context.args[1])
    try:
        await context.bot.ban_chat_member(bid, uid); await context.bot.unban_chat_member(bid, uid)
        if uid in DB["USER_DATA"] and str(bid) in DB["USER_DATA"].get(uid, {}).get("demos", {}): del DB["USER_DATA"][uid]["demos"][str(bid)]; await save_data_async()
        await update.effective_message.reply_text("✅ Kicked.")
    except Exception: pass

async def cmd_myinfo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id; data = DB["USER_DATA"].get(uid, {})
    txt = f"👤 **MY INFO**\n🆔 ID: `{uid}`\n"
    if update.callback_query: await context.bot.send_message(uid, txt, parse_mode=ParseMode.MARKDOWN)

async def cmd_approve_demo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    args = context.args; link = None
    
    if hasattr(update, 'message') and update.message and update.message.reply_to_message:
        m = re.search(r'(https?://t\.me/(?:\+|joinchat/)[a-zA-Z0-9_\-]+)', update.message.reply_to_message.text or "")
        if m: link = m.group(1)
        
    if not link and args and "t.me" in args[0]: link = args[0].strip()
    if not link: return await update.effective_message.reply_text("❌ Link nahi mila.")
    
    ld = DB["LINK_MAP"].get(link); target_uid = ld.get("u") if isinstance(ld, dict) else None; batch_id = ld.get("b") if isinstance(ld, dict) else ld
    if not target_uid or not batch_id: return
    try:
        await context.bot.approve_chat_join_request(batch_id, target_uid)
        DB["USER_DATA"].setdefault(target_uid, {}).setdefault("demos", {})[str(batch_id)] = {"expiry": time.time() + 10800, "warned": False}
        await save_data_async(); await update.effective_message.reply_text("✅ **APPROVED (DEMO)**")
    except Exception: pass

async def cmd_approve_perm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    link = None
    if hasattr(update, 'message') and update.message and update.message.reply_to_message:
        m = re.search(r'(https?://t\.me/(?:\+|joinchat/)[a-zA-Z0-9_\-]+)', update.message.reply_to_message.text or "")
        if m: link = m.group(1)
        
    if not link and context.args: link = context.args[0]
    if not link: return
    
    ld = DB["LINK_MAP"].get(link); target_uid = ld.get("u") if isinstance(ld, dict) else None; batch_id = ld.get("b") if isinstance(ld, dict) else ld
    try:
        await context.bot.approve_chat_join_request(batch_id, target_uid)
        if str(batch_id) in DB["USER_DATA"].get(target_uid, {}).get("demos", {}): del DB["USER_DATA"][target_uid]["demos"][str(batch_id)]
        await save_data_async(); await update.effective_message.reply_text("✅ **APPROVED (PERM)**")
    except Exception: pass

async def cmd_delbatch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id) or len(context.args) < 2: return
    t, cid = context.args[0].lower(), int(context.args[1]); d = DB["FREE_CHANNELS"] if t == "free" else DB["PAID_CHANNELS"]
    if cid in d: del d[cid]; await save_data_async(); await update.effective_message.reply_text("✅ Deleted")

async def cmd_addbatch_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    ADMIN_WIZARD[update.effective_user.id] = {"step": "ask_cat"}; kb = []
    for i in range(0, len(DB.get("CATEGORIES", DEFAULT_CATEGORIES)), 2):
        row = [InlineKeyboardButton(DB.get("CATEGORIES", DEFAULT_CATEGORIES)[i], callback_data=f"wcat_{i}")]
        if i+1 < len(DB.get("CATEGORIES", DEFAULT_CATEGORIES)): row.append(InlineKeyboardButton(DB.get("CATEGORIES", DEFAULT_CATEGORIES)[i+1], callback_data=f"wcat_{i+1}"))
        kb.append(row)
    await update.effective_message.reply_text("🆕 **Add Batch Wizard**\nSelect Category:", reply_markup=InlineKeyboardMarkup(kb))

async def cmd_broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    BROADCAST_STATE[update.effective_user.id] = {"type": "broadcast", "step": "wait_msg"}
    await update.effective_message.reply_text("📢 Send message to broadcast.")

async def cmd_post_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    BROADCAST_STATE[update.effective_user.id] = {"type": "post", "step": "wait_msg"}
    await update.effective_message.reply_text("📝 Send message to post.")

async def cmd_user_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    try: target_id = int(context.args[0])
    except Exception: return await update.effective_message.reply_text("Usage: /user [id]")
    info = DB["USER_DATA"].get(target_id, {})
    r = f"USER DETAILS: {target_id}\nName: {info.get('name', 'Unknown')}\n\n" + ("🚫 BLOCKED\n\n" if target_id in DB['BLOCKED_USERS'] else "") + "--- MEMBERSHIP ---\n"
    found = False
    for cid in set(list(DB["ALL_CHATS"].keys()) + list(DB["FREE_CHANNELS"].keys()) + list(DB["PAID_CHANNELS"].keys())):
        try:
            if (await context.bot.get_chat_member(cid, target_id)).status in [ChatMember.MEMBER, ChatMember.ADMINISTRATOR, ChatMember.OWNER, ChatMember.RESTRICTED]: r += f"{DB['ALL_CHATS'].get(cid, cid)}: ✅\n"; found = True
        except Exception: pass
    if not found: r += "Not found in any batch.\n"
    if "demo_history" in info: r += "\n--- DEMO HISTORY ---\n" + "\n".join([f"• {h}" for h in info["demo_history"]])
    f = io.BytesIO(r.encode("utf-8")); f.name = f"scan_{target_id}.txt"
    await update.effective_message.reply_document(document=f, caption="🔍 Deep Scan")

async def cmd_batches(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    r = "ALL BATCHES\n" + "="*30 + "\n" + "\n".join([f"{cid} | {DB['ALL_CHATS'].get(cid, 'Unknown')}" for cid in set(list(DB["ALL_CHATS"].keys()) + list(DB["FREE_CHANNELS"].keys()) + list(DB["PAID_CHANNELS"].keys()))])
    f = io.BytesIO(r.encode("utf-8")); f.name = "batches.txt"
    await update.effective_message.reply_document(document=f)

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    t = f"📊 **Statistics**\n💾 Storage: {'MongoDB ☁️' if MONGO_URL else 'Local 📁'}\n🔒 Lockdown: {'🔴 ON' if not DB.get('NEW_USERS_ALLOWED', True) else '🟢 OFF'}\n🔓 Free Locked: {'🔴 YES' if DB.get('FREE_LOCKED', False) else '🟢 NO'}\n🔐 Paid Locked: {'🔴 YES' if DB.get('PAID_LOCKED', False) else '🟢 NO'}\n🤖 Test Bot Locked: {'🔴 YES' if DB.get('TEST_BOT_LOCKED', False) else '🟢 NO'}\n\n👥 Users: {len(DB['USER_DATA'])}\n🆓 Free: {len(DB['FREE_CHANNELS'])}\n💎 Paid: {len(DB['PAID_CHANNELS'])}\n🚫 Blocked: {len(DB['BLOCKED_USERS'])}"
    await update.effective_message.reply_text(t, parse_mode=ParseMode.MARKDOWN)

async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid in BROADCAST_STATE: del BROADCAST_STATE[uid]
    if uid in ADMIN_WIZARD: del ADMIN_WIZARD[uid]
    await update.effective_message.reply_text("❌ Cancelled")

async def cmd_lockdown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    DB["NEW_USERS_ALLOWED"] = not DB.get("NEW_USERS_ALLOWED", True); await save_data_async()
    msg = await update.effective_message.reply_text("🔓 **Lockdown Lifted!**" if DB["NEW_USERS_ALLOWED"] else "🔒 **Lockdown Enabled!**", parse_mode=ParseMode.MARKDOWN)
    await schedule_delete(context, msg)

async def cmd_lockfree(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    DB["FREE_LOCKED"] = not DB.get("FREE_LOCKED", False); await save_data_async()
    await update.effective_message.reply_text("Free Batches **LOCKED 🔒**." if DB["FREE_LOCKED"] else "Free Batches **UNLOCKED 🔓**.", parse_mode=ParseMode.MARKDOWN)

async def cmd_locktestbot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    DB["TEST_BOT_LOCKED"] = not DB.get("TEST_BOT_LOCKED", False); await save_data_async()
    await update.effective_message.reply_text("Test Bot **LOCKED 🔒**." if DB["TEST_BOT_LOCKED"] else "Test Bot **UNLOCKED 🔓**.", parse_mode=ParseMode.MARKDOWN)

async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID: return
    if not SESSION_STRING or not API_ID: return await update.effective_message.reply_text("❌ **Userbot Config Missing!**")
    msg = await update.effective_message.reply_text("⏳ **Super Exit /clear Start...**")
    userbot = Client("clear_bot", api_id=API_ID, api_hash=API_HASH, session_string=SESSION_STRING, in_memory=True)
    try:
        await userbot.start()
        all_channels = list(DB["FREE_CHANNELS"].keys()) + list(DB["PAID_CHANNELS"].keys())
        removed_count = checked_users = safe_users = 0
        for bid in all_channels:
            try:
                bname = DB["ALL_CHATS"].get(int(bid), f"Batch {bid}")
                async for member in userbot.get_chat_members(int(bid)):
                    uid = member.user.id; checked_users += 1
                    if uid != OWNER_ID and not member.user.is_bot:
                        is_in_main = False
                        try: await userbot.get_chat_member(MANDATORY_CHANNEL_ID, uid); is_in_main = True; safe_users += 1
                        except Exception: pass
                        if not is_in_main:
                            try: await context.bot.ban_chat_member(int(bid), uid); await context.bot.unban_chat_member(int(bid), uid); removed_count += 1; await asyncio.sleep(1.5)
                            except Exception: pass
                    await asyncio.sleep(0.1)
            except Exception: pass
        await userbot.stop(); await msg.edit_text(f"✅ **/clear Process Pura Hua!**\n\nChecked: `{checked_users}`\nSafe: `{safe_users}`\nRemoved: `{removed_count}`", parse_mode=ParseMode.MARKDOWN)
    except Exception as e: await msg.edit_text(f"❌ Error: `{e}`")

async def cmd_maintenance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    DB["MAINTENANCE_MODE"] = not DB.get("MAINTENANCE_MODE", False); await save_data_async()
    msg = await update.effective_message.reply_text("🛠️ **Maintenance Mode Enabled!**\nNormal users ka support message ab aana band ho gaya hai." if DB["MAINTENANCE_MODE"] else "✅ **Maintenance Mode Disabled!**\nBot ab normally kaam kar raha hai.", parse_mode=ParseMode.MARKDOWN)
    await schedule_delete(context, msg)


# --- WIZARDS, MENUS & CALLBACKS ---
async def wizard_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; uid = q.from_user.id
    if uid not in ADMIN_WIZARD: return await q.answer("Expired")
    if q.data.startswith("wcat_"): 
        cat_idx = int(q.data.split('_')[1]); ADMIN_WIZARD[uid]["category"] = DB.get("CATEGORIES", DEFAULT_CATEGORIES)[cat_idx]; ADMIN_WIZARD[uid]["step"] = "ask_type"
        kb = [[InlineKeyboardButton("Free", callback_data="wiz_free"), InlineKeyboardButton("Paid", callback_data="wiz_paid")]]
        return await q.edit_message_text(f"✅ Category: **{ADMIN_WIZARD[uid]['category']}**\n\n➡️ **Step 2:** Select Batch Type:", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
    if q.data in ["wiz_free", "wiz_paid"]: 
        if "category" not in ADMIN_WIZARD[uid]: return await q.answer("Start again")
        ADMIN_WIZARD[uid]["type"] = q.data.split('_')[1]; ADMIN_WIZARD[uid]["step"] = "ask_id"
        return await q.edit_message_text(f"➡️ **Step 3:** Send **Channel ID** for {q.data.split('_')[1].upper()}:", parse_mode=ParseMode.MARKDOWN)

async def wizard_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or update.effective_user.id not in ADMIN_WIZARD: return False
    uid, state = update.effective_user.id, ADMIN_WIZARD[update.effective_user.id]
    
    if state["step"] == "ask_id":
        try:
            cid = int(update.message.text); cname = (await context.bot.get_chat(cid)).title or f"Batch {cid}"
            (DB["FREE_CHANNELS"] if state["type"] == "free" else DB["PAID_CHANNELS"])[cid] = cname
            DB["ALL_CHATS"][cid] = cname; DB.setdefault("BATCH_CATEGORIES", {})[str(cid)] = state["category"]
            await save_data_async(); await update.effective_message.reply_text(f"✅ **Added!**\nName: {cname} ({cid})\nCategory: {state['category']}", parse_mode=ParseMode.MARKDOWN)
            if state["type"] == "free":
                b_count = 0; await update.effective_message.reply_text("📢 Sending Auto-Broadcast...", parse_mode=ParseMode.MARKDOWN)
                for t_cid in list(DB["ALL_CHATS"].keys()):
                    if t_cid != cid: 
                        try:
                            sent_msg = await context.bot.send_message(t_cid, f"🎉 <b>NEW FREE BATCH ADDED!</b>\n📛 Name: {cname}\n👉 Join via Bot Menu!", parse_mode=ParseMode.HTML)
                            DB.setdefault("SCHEDULED_DELETES", []).append({"c": t_cid, "m": sent_msg.message_id, "t": time.time() + 10800 }); b_count += 1
                        except Exception: pass
                await update.effective_message.reply_text(f"✅ Broadcast sent to {b_count} chats."); await save_data_async()
            del ADMIN_WIZARD[uid]
        except Exception: await update.effective_message.reply_text("❌ Error. Ensure valid ID.")
        return True

    elif state["step"].startswith("call_cmd_"):
        cmd_name = state["step"].replace("call_cmd_", "")
        context.args = update.message.text.split()
        try:
            cmds = {"addadmin": cmd_add_admin, "deladmin": cmd_del_admin, "ban": cmd_ban, "unban": cmd_unban, "kick": cmd_kick_user, "find": cmd_find_user, "resetuser": cmd_reset_user, "demo": cmd_approve_demo, "perm": cmd_approve_perm, "extend": cmd_extend_demo, "settestbot": cmd_set_testbot, "setwelcome": cmd_set_welcome, "delbatch": cmd_delbatch, "addcat": cmd_addcat, "setcat": cmd_setcategory}
            if cmd_name in cmds: await cmds[cmd_name](update, context)
        except Exception: pass
        if uid in ADMIN_WIZARD: del ADMIN_WIZARD[uid]
        return True
    return False

async def handle_broadcast_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or update.effective_user.id not in BROADCAST_STATE: return False
    state = BROADCAST_STATE[update.effective_user.id]
    if state["step"] == "wait_msg":
        state["content"] = update.message; state["step"] = "confirm"
        kb = [[InlineKeyboardButton("✅ YES", callback_data="bc_yes"), InlineKeyboardButton("❌ NO", callback_data="bc_no")]]
        await update.effective_message.reply_text("📢 **Confirm?**", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
        return True
    return False

async def broadcast_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; uid = q.from_user.id
    if uid not in BROADCAST_STATE: return await q.answer("Expired")
    if q.data == "bc_no": del BROADCAST_STATE[uid]; return await q.edit_message_text("❌ Cancelled")
    if q.data == "bc_yes":
        await q.edit_message_text("⏳ Processing..."); count = 0
        targets = list(DB["USER_DATA"].keys()) if BROADCAST_STATE[uid]["type"] == "broadcast" else list(DB["FREE_CHANNELS"].keys()) + list(DB["PAID_CHANNELS"].keys())
        for tid in targets:
            try: await context.bot.copy_message(tid, uid, BROADCAST_STATE[uid]["content"].message_id); count += 1; await asyncio.sleep(0.05)
            except RetryAfter as e: await asyncio.sleep(e.retry_after + 1)
            except Exception: pass
        await context.bot.send_message(uid, f"✅ Done. Sent to {count}."); del BROADCAST_STATE[uid]

async def general_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; uid = q.from_user.id; data = q.data
    if uid in DB["BLOCKED_USERS"] or check_spam(uid): return await q.answer("Blocked/Wait", show_alert=True)
    if data.startswith("wiz_"): return await wizard_callback(update, context)
    if data.startswith("bc_"): return await broadcast_callback(update, context)
    
    if data == "dash_home":
        if uid in ADMIN_WIZARD: del ADMIN_WIZARD[uid]
        await q.answer(); await start(update, context)

    elif data == "dash_locks":
        kb = [
            [InlineKeyboardButton(f"System Lockdown: {'🔴 ON' if not DB.get('NEW_USERS_ALLOWED', True) else '🟢 OFF'}", callback_data="toggle_lockdown")],
            [InlineKeyboardButton(f"Free Batches: {'🔴 LOCKED' if DB.get('FREE_LOCKED', False) else '🟢 OPEN'}", callback_data="toggle_free"), InlineKeyboardButton(f"Paid Batches: {'🔴 LOCKED' if DB.get('PAID_LOCKED', False) else '🟢 OPEN'}", callback_data="toggle_paid")],
            [InlineKeyboardButton(f"Test Bot: {'🔴 LOCKED' if DB.get('TEST_BOT_LOCKED', False) else '🟢 OPEN'}", callback_data="toggle_testbot")],
            [InlineKeyboardButton(f"Maintenance Mode: {'🔴 ON' if DB.get('MAINTENANCE_MODE', False) else '🟢 OFF'}", callback_data="toggle_maintenance")],
            [InlineKeyboardButton("🔙 Back to Terminal", callback_data="dash_home")]
        ]
        await q.edit_message_text("🔒 **Security & Access Control**", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

    elif data.startswith("toggle_"):
        key = data.split("_")[1].upper()
        if key == "LOCKDOWN": DB["NEW_USERS_ALLOWED"] = not DB.get("NEW_USERS_ALLOWED", True)
        elif key == "MAINTENANCE": DB["MAINTENANCE_MODE"] = not DB.get("MAINTENANCE_MODE", False)
        elif key == "FREE": DB["FREE_LOCKED"] = not DB.get("FREE_LOCKED", False)
        elif key == "PAID": DB["PAID_LOCKED"] = not DB.get("PAID_LOCKED", False)
        elif key == "TESTBOT": DB["TEST_BOT_LOCKED"] = not DB.get("TEST_BOT_LOCKED", False)
        await save_data_async()
        
        kb = [
            [InlineKeyboardButton(f"System Lockdown: {'🔴 ON' if not DB.get('NEW_USERS_ALLOWED', True) else '🟢 OFF'}", callback_data="toggle_lockdown")],
            [InlineKeyboardButton(f"Free Batches: {'🔴 LOCKED' if DB.get('FREE_LOCKED', False) else '🟢 OPEN'}", callback_data="toggle_free"), InlineKeyboardButton(f"Paid Batches: {'🔴 LOCKED' if DB.get('PAID_LOCKED', False) else '🟢 OPEN'}", callback_data="toggle_paid")],
            [InlineKeyboardButton(f"Test Bot: {'🔴 LOCKED' if DB.get('TEST_BOT_LOCKED', False) else '🟢 OPEN'}", callback_data="toggle_testbot")],
            [InlineKeyboardButton(f"Maintenance Mode: {'🔴 ON' if DB.get('MAINTENANCE_MODE', False) else '🟢 OFF'}", callback_data="toggle_maintenance")],
            [InlineKeyboardButton("🔙 Back to Terminal", callback_data="dash_home")]
        ]
        await q.edit_message_text("🔒 **Security & Access Control**", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

    elif data == "dash_db":
        kb = [[InlineKeyboardButton("📥 Download Backup", callback_data="act_backup"), InlineKeyboardButton("🔄 Run Sync", callback_data="act_sync")], [InlineKeyboardButton("👥 Download All Users List", callback_data="act_allusers")], [InlineKeyboardButton("🔙 Back", callback_data="dash_home")]]
        await q.edit_message_text("🗄️ **Database Tools**", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

    elif data in ["dash_batches", "adash_batches"]:
        kb = [
            [InlineKeyboardButton("➕ Add Batch", callback_data="act_addbatch"), InlineKeyboardButton("🗑️ Delete Batch", callback_data="input_delbatch")], 
            [InlineKeyboardButton("📁 Add Category", callback_data="input_addcat"), InlineKeyboardButton("🗑️ Delete Category", callback_data="act_delcat")], 
            [InlineKeyboardButton("📂 Set Batch Category", callback_data="input_setcat")], # 👈 YE RAHA NAYA BUTTON
            [InlineKeyboardButton("📊 Batch Stats", callback_data="act_batchstats")], 
            [InlineKeyboardButton("🔙 Back", callback_data="dash_home")]
        ]
        await q.edit_message_text("📦 **Batches Management**", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

    elif data == "dash_staff":
        kb = [[InlineKeyboardButton("👮 Add Admin", callback_data="input_addadmin"), InlineKeyboardButton("🚫 Remove Admin", callback_data="input_deladmin")], [InlineKeyboardButton("🔙 Back", callback_data="dash_home")]]
        await q.edit_message_text("👥 **Staff Management**", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

    elif data in ["dash_comms", "adash_comms"]:
        kb = [[InlineKeyboardButton("📢 Broadcast", callback_data="act_broadcast"), InlineKeyboardButton("📝 Post Message", callback_data="act_post")], [InlineKeyboardButton("🔗 Set Test Bot", callback_data="input_settestbot"), InlineKeyboardButton("👋 Set Welcome", callback_data="input_setwelcome")], [InlineKeyboardButton("🔙 Back", callback_data="dash_home")]]
        await q.edit_message_text("📢 **Communications**", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

    elif data == "dash_stats": await q.answer("Generating Stats..."); await cmd_stats(update, context)

    elif data == "adash_users":
        kb = [[InlineKeyboardButton("🚫 Ban", callback_data="input_ban"), InlineKeyboardButton("✅ Unban", callback_data="input_unban")], [InlineKeyboardButton("🥾 Kick", callback_data="input_kick"), InlineKeyboardButton("🔍 Find", callback_data="input_find")], [InlineKeyboardButton("🔄 Reset User Data", callback_data="input_resetuser")], [InlineKeyboardButton("🔙 Back", callback_data="dash_home")]]
        await q.edit_message_text("👤 **User Management**", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

    elif data == "adash_approvals":
        kb = [[InlineKeyboardButton("⏳ Approve Demo", callback_data="input_demo"), InlineKeyboardButton("💎 Approve Perm", callback_data="input_perm")], [InlineKeyboardButton("➕ Extend Demo Time", callback_data="input_extend")], [InlineKeyboardButton("🔙 Back", callback_data="dash_home")]]
        await q.edit_message_text("✅ **Access Approvals**", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

    elif data == "act_backup": await q.answer("Sending..."); await cmd_backup(update, context)
    elif data == "act_sync": await q.answer("Sync Started!"); await cmd_sync(update, context)
    elif data == "act_allusers": await q.answer("Generating..."); await cmd_all_users(update, context)
    elif data == "act_batchstats": await q.answer("Calculating..."); await cmd_batch_stats(update, context)
    elif data == "act_addbatch": await cmd_addbatch_start(update, context)
    elif data == "act_delcat": await cmd_delcat(update, context)
    elif data == "act_broadcast": await cmd_broadcast_start(update, context)
    elif data == "act_post": await cmd_post_start(update, context)

    elif data.startswith("input_"):
        cmd_name = data.split("_")[1]; ADMIN_WIZARD[uid] = {"step": f"call_cmd_{cmd_name}"}
        prompts = {"addadmin": "Send User ID to make Admin:", "deladmin": "Send User ID to remove from Admin:", "ban": "Send User ID to Ban:", "unban": "Send User ID to Unban:", "kick": "Send User ID and Batch ID\nFormat: `uid bid`", "find": "Send Username to find:", "resetuser": "Send User ID to reset:", "demo": "Send Link and Time:\nFormat: `link 10h`", "perm": "Send Link to approve:", "extend": "Send User ID, Batch ID, Hours:\nFormat: `uid bid 24`", "settestbot": "Send new Test Bot link:", "setwelcome": "Send Batch ID and Welcome Msg:\nFormat: `bid message`", "delbatch": "Send Type and ID:\nFormat: `free 123` or `paid 123`", "addcat": "Send Name for new Category:", "setcat": "Send Batch ID to change category:"}
        await q.edit_message_text(f"⚡ **INPUT REQUIRED FOR: {cmd_name.upper()}**\n\n{prompts.get(cmd_name, 'Send input:')}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel Input", callback_data="dash_home")]]), parse_mode=ParseMode.MARKDOWN)

    elif data == "accept_tnc": DB.setdefault("USER_DATA", {}).setdefault(uid, {})["tnc_accepted"] = True; await save_data_async(); await show_user_menu(update)
    elif data == "u_main": await show_user_menu(update)
    elif data == "my_info": await q.answer(); await cmd_myinfo(update, context)
    elif data == "test_bot":
        if DB.get("TEST_BOT_LOCKED", False): return await q.answer("🔒 Locked by Admin.", show_alert=True)
        if not await check_membership(uid, context): return await q.answer("❌ Join Main Channel First!", show_alert=True)
        if not DB.get("TEST_BOT_LINK"): return await q.answer("⚠️ Test Bot is not setup by Admin yet!", show_alert=True)
        await q.answer("Verifying & Generating Link...")
        kb = [[InlineKeyboardButton("🔗 Open Test Bot", url=DB.get("TEST_BOT_LINK"))]]
        try: sent_msg = await context.bot.send_message(uid, "🤖 **Test Bot Access Verification:**\n\nYou are verified! Click the button below to open the Test Bot.", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN); await schedule_delete(context, sent_msg, delay=60)
        except Exception: pass

    elif data.startswith("my_batches_"):
        if not DB["USER_DATA"].get(uid, {}).get("tnc_accepted", False): return await show_tnc_menu(update, context)
        await q.edit_message_text("⏳ **Aapke batches fetch kiye ja rahe hain... Please wait.**", parse_mode=ParseMode.MARKDOWN)
        page = int(data.split("_")[-1])
        all_batches = {**DB["FREE_CHANNELS"], **DB["PAID_CHANNELS"]}; joined_batches = []
        async def check_member(cid, name):
            try:
                m = await context.bot.get_chat_member(cid, uid)
                if m.status in [ChatMember.MEMBER, ChatMember.ADMINISTRATOR, ChatMember.OWNER, ChatMember.RESTRICTED]: return (cid, name)
            except Exception: pass
            return None
        results = await asyncio.gather(*[check_member(cid, name) for cid, name in all_batches.items()]); joined_batches = [r for r in results if r is not None]
        if not joined_batches: return await q.edit_message_text("❌ Aap abhi kisi bhi batch me join nahi hain.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Menu", callback_data="u_main")]]), parse_mode=ParseMode.MARKDOWN)
        MAX_PER_PAGE = 10; total_batches = len(joined_batches); start_idx = page * MAX_PER_PAGE; end_idx = start_idx + MAX_PER_PAGE
        kb = [[InlineKeyboardButton(f"✅ {name}", url=f"https://t.me/c/{str(cid).replace('-100', '')}/1")] for cid, name in joined_batches[start_idx:end_idx]]
        nav_buttons = []
        if page > 0: nav_buttons.append(InlineKeyboardButton("⬅️ Back", callback_data=f"my_batches_{page-1}"))
        if end_idx < total_batches: nav_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"my_batches_{page+1}"))
        if nav_buttons: kb.append(nav_buttons)
        kb.append([InlineKeyboardButton("🔙 Main Menu", callback_data="u_main")])
        await q.edit_message_text(f"📚 **My Batches (Page {page+1})**\n\nYahan wo sabhi batches hain jisme aap successfully join hain. Click karke direct channel access karein:", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

    elif data.startswith("all_batches_"):
        kb = [[InlineKeyboardButton(cat, callback_data=f"showcat_{i}")] for i, cat in enumerate(DB.get("CATEGORIES", DEFAULT_CATEGORIES))] + [[InlineKeyboardButton("🔙 Main Menu", callback_data="u_main")]]
        await q.edit_message_text("🌐 **All Batches - Select Category:**", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
    elif data.startswith("showcat_"):
        cat_idx = int(data.split("_")[1]); kb = [[InlineKeyboardButton("🆓 Free Batches", callback_data=f"listcat_{cat_idx}_free_0"), InlineKeyboardButton("💎 Paid Batches", callback_data=f"listcat_{cat_idx}_paid_0")], [InlineKeyboardButton("🔙 Back to Categories", callback_data="all_batches_0")]]
        await q.edit_message_text(f"📂 **Category: {DB.get('CATEGORIES', DEFAULT_CATEGORIES)[cat_idx]}**\n\nAapko kis type ke batch chahiye?", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
    elif data.startswith("setexistingcat_"):
        parts = data.split("_")
        cid, cat_idx = parts[1], int(parts[2])
        selected_cat = DB.get("CATEGORIES", DEFAULT_CATEGORIES)[cat_idx]
        
        # Nayi category assign karna
        DB.setdefault("BATCH_CATEGORIES", {})[str(cid)] = selected_cat
        await save_data_async()
        await q.edit_message_text(f"✅ Batch `{cid}` ab **{selected_cat}** category me set ho gaya hai!", parse_mode=ParseMode.MARKDOWN)

    elif data.startswith("listcat_"):
        if not DB["USER_DATA"].get(uid, {}).get("tnc_accepted", False): return await show_tnc_menu(update, context)
        parts = data.split("_"); cat_idx, b_type, page = int(parts[1]), parts[2], int(parts[3])
        cat_name = DB.get("CATEGORIES", DEFAULT_CATEGORIES)[cat_idx]
        if b_type == "free" and DB.get("FREE_LOCKED", False): return await q.answer("🔒 Free Batches Locked.", show_alert=True)
        if b_type == "paid" and DB.get("PAID_LOCKED", False): return await q.answer("🔒 Paid Batches Locked.", show_alert=True)
        source_dict = DB["FREE_CHANNELS"] if b_type == "free" else DB["PAID_CHANNELS"]
        filtered_batches = [(cid, name) for cid, name in source_dict.items() if DB.get("BATCH_CATEGORIES", {}).get(str(cid), "Other Batches") == cat_name]
        if not filtered_batches: return await q.edit_message_text(f"❌ Is category ({cat_name}) me abhi koi {b_type.title()} batch nahi hai.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data=f"showcat_{cat_idx}")]]), parse_mode=ParseMode.MARKDOWN)
        MAX_PER_PAGE = 10; total = len(filtered_batches); start_idx = page * MAX_PER_PAGE; end_idx = start_idx + MAX_PER_PAGE
        kb = [[InlineKeyboardButton(f"{'🔗' if b_type == 'free' else '💎'} {name}", callback_data=f"get_f_{cid}" if b_type == "free" else f"view_p_{cid}")] for cid, name in filtered_batches[start_idx:end_idx]]
        nav_buttons = []
        if page > 0: nav_buttons.append(InlineKeyboardButton("⬅️ Back", callback_data=f"listcat_{cat_idx}_{b_type}_{page-1}"))
        if end_idx < total: nav_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"listcat_{cat_idx}_{b_type}_{page+1}"))
        if nav_buttons: kb.append(nav_buttons)
        kb.append([InlineKeyboardButton("🔙 Category Menu", callback_data=f"showcat_{cat_idx}")])
        await q.edit_message_text(f"📚 **{cat_name} ({b_type.title()})**\n\nNeeche diye gaye batches par click karke join karein:", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

    elif data.startswith("delcat_"):
        cat_idx = int(data.split("_")[1]); categories = DB.get("CATEGORIES", DEFAULT_CATEGORIES)
        if cat_idx >= len(categories): return await q.answer("Invalid category", show_alert=True)
        deleted_cat = categories[cat_idx]
        if deleted_cat == "Other Batches": return await q.answer("❌ 'Other Batches' ko delete nahi kiya ja sakta!", show_alert=True)
        DB["CATEGORIES"].remove(deleted_cat); shifted_count = 0
        if "BATCH_CATEGORIES" in DB:
            for cid, cat in DB["BATCH_CATEGORIES"].items():
                if cat == deleted_cat: DB["BATCH_CATEGORIES"][cid] = "Other Batches"; shifted_count += 1
        await save_data_async(); await q.edit_message_text(f"✅ Category **{deleted_cat}** delete kar di gayi hai.\n\n🔄 Uske **{shifted_count} batches** automatically 'Other Batches' me shift ho gaye hain.", parse_mode=ParseMode.MARKDOWN)

    elif data == "cancel_delcat": await q.edit_message_text("❌ Category deletion cancelled.")

    elif data.startswith("get_f_"):
        cid = int(data.split("_")[2])
        if await is_already_in_channel(context, cid, uid): return await q.answer("⚠️ Already Joined!", show_alert=True)
        try:
            bname = DB["ALL_CHATS"].get(cid, f"Batch {cid}")
            l = await context.bot.create_chat_invite_link(cid, creates_join_request=True, name=f"Free-{uid}", expire_date=int(time.time())+60)
            sent_msg = await context.bot.send_message(uid, f"🔗 <b>Link:</b>\n\n<b>{bname}</b>\n\n{l.invite_link}\n\nℹ️ <i>Request auto-approved.</i>\n⏳ <i>(Expires in 1 min)</i>", parse_mode=ParseMode.HTML)
            await schedule_delete(context, sent_msg, delay=60); await q.answer("Sent to DM")
        except Exception as e: await q.answer(f"Bot Error: {e}", show_alert=True)

    elif data.startswith("view_p_"):
        cid = int(data.split("_")[2]); await q.answer()
        kb = [[InlineKeyboardButton("🔗 Request Access", callback_data=f"req_access_{cid}")], [InlineKeyboardButton("🔙 Back", callback_data="u_main")]]
        try: await q.edit_message_text("💎 **Premium Access:**\nClick below.", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
        except Exception: pass

    elif data.startswith("req_access_"):
        cid = int(data.split("_")[2])
        if not await check_membership(uid, context): return await q.answer("❌ Join Main First!", show_alert=True)
        if await is_already_in_channel(context, cid, uid): return await q.answer("⚠️ Already joined!", show_alert=True)
        await q.answer("🔄 Generating Link...")
        try:
            bname = DB["ALL_CHATS"].get(cid, f"Batch {cid}"); l = await context.bot.create_chat_invite_link(cid, creates_join_request=True, name=f"Req-{uid}", expire_date=int(time.time())+60)
            DB["LINK_MAP"][l.invite_link] = {"u": uid, "b": cid}; await save_data_async()
            topic_id = await get_or_create_topic(update.effective_user, context)
            if topic_id:
                try: await context.bot.send_message(SUPPORT_GROUP_ID, f"🔔 **NEW REQUEST**\n👤 User: {update.effective_user.mention_html()}\n📂 Batch: <b>{bname}</b>\n🔗 Link: {l.invite_link}\n\n👇 **Action:**\n/demo {l.invite_link}\n/per {l.invite_link}", message_thread_id=topic_id, parse_mode=ParseMode.HTML)
                except Exception: pass
            user_msg_obj = await context.bot.send_message(uid, f"✅ <b>Access Link Generated!</b>\n\n<b>{bname}</b>\n\n🔗 {l.invite_link}\n\nℹ️ <b>Sent to Admin.</b> Wait for approval.\n⏳ <i>(Expires in 1 min)</i>", parse_mode=ParseMode.HTML)
            await schedule_delete(context, user_msg_obj, delay=60)
        except Exception as e: await context.bot.send_message(uid, f"❌ Error: {e}")

# --- START, MENUS & CORE EVENTS ---
async def show_tnc_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton("✅ I Accept", callback_data="accept_tnc")]]
    txt = "📜 **WELCOME!**\n⚠️ Rules: Do not leave main channel. Do not block bot."
    if update.callback_query: await update.callback_query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
    else: await update.message.reply_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

async def show_user_menu(update: Update):
    kb = [[InlineKeyboardButton("📚 My Batches", callback_data="my_batches_0"), InlineKeyboardButton("🌐 All Batches", callback_data="all_batches_0")], [InlineKeyboardButton("🤖 Test Bot", callback_data="test_bot")], [InlineKeyboardButton("🆘 Support", url=f"tg://user?id={SUPPORT_GROUP_ID}")], [InlineKeyboardButton("ℹ️ My Info", callback_data="my_info")]]
    txt = "🌟 **Welcome to the Premium Hub!** 🌟\nYour centralized portal for exclusive communities.\n\n👇 *Select an option below:*"
    if update.callback_query: await update.callback_query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
    else: await update.message.reply_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user; await set_role_based_commands(user.id, context)
    if user.id not in DB["USER_DATA"]: 
        DB["USER_DATA"][user.id] = {"name": user.full_name, "username": user.username, "joined_at": time.time(), "demos": {}, "tnc_accepted": False}
        await save_data_async()
    await get_or_create_topic(user, context)

    if str(user.id) == str(OWNER_ID):
        kb = [[InlineKeyboardButton("🔒 Security", callback_data="dash_locks"), InlineKeyboardButton("🗄️ Database", callback_data="dash_db")], [InlineKeyboardButton("📦 Batches", callback_data="dash_batches"), InlineKeyboardButton("👥 Staff", callback_data="dash_staff")], [InlineKeyboardButton("📢 Comms", callback_data="dash_comms"), InlineKeyboardButton("📊 Analytics", callback_data="dash_stats")]]
        text = "🚀 **SYSTEM MASTER TERMINAL**\nSelect a module:"
        if update.callback_query: await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
        else: await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
    elif is_admin(user.id):
        kb = [[InlineKeyboardButton("👤 Users", callback_data="adash_users"), InlineKeyboardButton("✅ Approvals", callback_data="adash_approvals")], [InlineKeyboardButton("📁 Batches", callback_data="adash_batches"), InlineKeyboardButton("📢 Comms", callback_data="adash_comms")]]
        text = "🛡️ **ADMINISTRATOR DASHBOARD**\nSelect an action:"
        if update.callback_query: await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
        else: await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
    elif await check_membership(user.id, context):
        if not DB["USER_DATA"].get(user.id, {}).get("tnc_accepted", False): await show_tnc_menu(update, context)
        else: await show_user_menu(update)
    else:
        if not DB.get("NEW_USERS_ALLOWED", True): return await update.effective_message.reply_text("⛔ **Entry Closed!**", parse_mode=ParseMode.MARKDOWN)
        kb = [[InlineKeyboardButton("📢 Join Channel", url=MANDATORY_CHANNEL_LINK)], [InlineKeyboardButton("✅ Verified", callback_data="verify")]]
        await update.effective_message.reply_text("⚠️ **Join Main Channel First**", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

async def delete_service_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try: await update.message.delete()
    except Exception: pass

async def handle_reaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message_reaction: return
    r = update.message_reaction
    if (r.chat.id, r.message_id) in MESSAGE_MAP:
        tc, tm = MESSAGE_MAP[(r.chat.id, r.message_id)]
        try: await context.bot.set_message_reaction(tc, tm, reaction=r.new_reaction)
        except Exception: pass

async def handle_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.edited_message: return
    m = update.edited_message; key = (m.chat.id, m.message_id)
    if key in MESSAGE_MAP:
        tc, tm = MESSAGE_MAP[key]
        try:
            if not m.photo and not m.video and not m.document and not m.audio and not m.animation:
                if m.text: await context.bot.edit_message_text(m.text, chat_id=tc, message_id=tm, entities=m.entities)
            else:
                new_media = None
                if m.photo: new_media = InputMediaPhoto(m.photo[-1].file_id, caption=m.caption, caption_entities=m.caption_entities)
                elif m.video: new_media = InputMediaVideo(m.video.file_id, caption=m.caption, caption_entities=m.caption_entities)
                elif m.document: new_media = InputMediaDocument(m.document.file_id, caption=m.caption, caption_entities=m.caption_entities)
                elif m.audio: new_media = InputMediaAudio(m.audio.file_id, caption=m.caption, caption_entities=m.caption_entities)
                elif m.animation: new_media = InputMediaAnimation(m.animation.file_id, caption=m.caption, caption_entities=m.caption_entities)
                if new_media: await context.bot.edit_message_media(chat_id=tc, message_id=tm, media=new_media)
                elif m.caption is not None: await context.bot.edit_message_caption(chat_id=tc, message_id=tm, caption=m.caption, caption_entities=m.caption_entities)
        except Exception as e: logger.error(f"Edit Sync Error: {e}")

async def main_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user, chat = update.effective_user, update.effective_chat
    if not user or check_spam(user.id): return
    if user.id not in DB["BLOCKED_USERS"]:
        if await wizard_message(update, context): return
        if await handle_broadcast_flow(update, context): return
    if chat.type == ChatType.PRIVATE:
        if DB.get("MAINTENANCE_MODE", False) and not is_admin(user.id): return await update.effective_message.reply_text("⚠️ **Under Maintenance.**")
        topic_id = await get_or_create_topic(user, context)
        if topic_id:
            reply_id = None
            if update.message.reply_to_message:
                reply_key = (chat.id, update.message.reply_to_message.message_id)
                if reply_key in MESSAGE_MAP: _, reply_id = MESSAGE_MAP[reply_key]
            try:
                sent = await context.bot.copy_message(SUPPORT_GROUP_ID, chat.id, update.message.id, message_thread_id=topic_id, reply_to_message_id=reply_id)
                MESSAGE_MAP[(chat.id, update.message.id)] = (SUPPORT_GROUP_ID, sent.message_id)
                MESSAGE_MAP[(SUPPORT_GROUP_ID, sent.message_id)] = (chat.id, update.message.id)
            except Exception: pass
    elif chat.id == SUPPORT_GROUP_ID and update.message.message_thread_id:
        if update.message.from_user.id == context.bot.id: return
        topic_id = update.message.message_thread_id; target_uid = None
        for u, t in DB["USER_TOPICS"].items():
            if t == topic_id: target_uid = int(u); break
        if target_uid:
            reply_id = None
            if update.message.reply_to_message:
                reply_key = (SUPPORT_GROUP_ID, update.message.reply_to_message.message_id)
                if reply_key in MESSAGE_MAP: _, reply_id = MESSAGE_MAP[reply_key]
            try:
                sent = await context.bot.copy_message(target_uid, chat.id, update.message.id, reply_to_message_id=reply_id)
                MESSAGE_MAP[(SUPPORT_GROUP_ID, update.message.id)] = (target_uid, sent.message_id)
                MESSAGE_MAP[(target_uid, sent.message_id)] = (SUPPORT_GROUP_ID, update.message.id)
            except Exception: pass

async def on_chat_member_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    result = update.chat_member; user = result.new_chat_member.user; status = result.new_chat_member.status
    if str(result.chat.id).replace("-100", "") == str(MANDATORY_CHANNEL_ID).replace("-100", "") and status in [ChatMember.LEFT, ChatMember.BANNED]:
        await execute_universal_kick(user.id, context)

async def track_chats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.my_chat_member.chat; status = update.my_chat_member.new_chat_member.status
    if chat.type == ChatType.PRIVATE and status == ChatMember.BANNED: await execute_universal_kick(chat.id, context)

async def background_sync(context: ContextTypes.DEFAULT_TYPE):
    global SPAM_CACHE; SPAM_CACHE = {k: v for k, v in SPAM_CACHE.items() if time.time() - v < 2.0} 
    if len(MESSAGE_MAP) > 5000: MESSAGE_MAP.clear()
    for uid in list(DB["USER_DATA"].keys()):
        user_id = int(uid)
        if user_id in DB["BLOCKED_USERS"] or is_admin(user_id): continue
        try: status = (await context.bot.get_chat_member(MANDATORY_CHANNEL_ID, user_id)).status
        except Exception: continue 
        if status in [ChatMember.LEFT, ChatMember.BANNED]: await execute_universal_kick(user_id, context)
        await asyncio.sleep(0.5)

async def handle_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    req = update.chat_join_request; chat = req.chat; user = req.from_user
    if user.id in DB["BLOCKED_USERS"]:
        try: await context.bot.decline_chat_join_request(chat.id, user.id)
        except Exception: pass
        return
    if chat.id in DB["FREE_CHANNELS"]:
        if await check_membership(user.id, context):
            try:
                await context.bot.approve_chat_join_request(chat.id, user.id)
                welcome_str = DB["CUSTOM_WELCOMES"].get(chat.id, f"✅ **Approved!**\nWelcome to {chat.title}")
                w_msg = await context.bot.send_message(user.id, welcome_str, parse_mode=ParseMode.MARKDOWN); await schedule_delete(context, w_msg, delay=60)
            except Exception: pass
        else:
            try: 
                await context.bot.send_message(user.id, f"⚠️ **Declined!**\nJoin Main:\n{MANDATORY_CHANNEL_LINK}", parse_mode=ParseMode.MARKDOWN)
                await context.bot.decline_chat_join_request(chat.id, user.id)
            except Exception: pass
    elif chat.id in DB["PAID_CHANNELS"]:
        if req.invite_link and req.invite_link.invite_link in DB["LINK_MAP"]:
            try: await context.bot.revoke_chat_invite_link(chat.id, req.invite_link.invite_link)
            except Exception: pass

async def check_demos(context: ContextTypes.DEFAULT_TYPE):
    now = time.time(); mod = False
    for uid, data in list(DB["USER_DATA"].items()):
        if not data.get("demos"): continue
        for bid, d_data in data["demos"].copy().items():
            expiry = d_data["expiry"] if isinstance(d_data, dict) else float(d_data)
            if now > expiry:
                try: await context.bot.ban_chat_member(int(bid), int(uid)); await context.bot.unban_chat_member(int(bid), int(uid))
                except Exception: pass
                if bid in data["demos"]: del data["demos"][bid]; mod = True
    if DB.get("SCHEDULED_DELETES"):
        surviving = [item for item in DB["SCHEDULED_DELETES"] if now <= item["t"]]
        if len(surviving) != len(DB["SCHEDULED_DELETES"]): DB["SCHEDULED_DELETES"] = surviving; mod = True
    if mod: await save_data_async()

async def auto_backup_db(context: ContextTypes.DEFAULT_TYPE):
    if LOG_CHANNEL_ID and os.path.exists(DATA_FILE):
        try: await context.bot.send_document(LOG_CHANNEL_ID, document=open(DATA_FILE, "rb"), caption="🛡️ Auto Backup")
        except Exception: pass

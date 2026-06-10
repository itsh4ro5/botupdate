import os
import asyncio
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, 
    ChatJoinRequestHandler, ChatMemberHandler, MessageReactionHandler, filters, ContextTypes
)
from telegram.request import HTTPXRequest
from telegram.error import Conflict
from hypercorn.asyncio import serve
from hypercorn.config import Config as HyperConfig

print("🚀 ADVANCED ASYNC BOT SCRIPT EXECUTING...", flush=True)

import config
from config import TELEGRAM_BOT_TOKEN, LOG_CHANNEL_ID, load_data, logger
from handlers import *
from app import app as web_app  # Quart Web App import

async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    if isinstance(context.error, Conflict):
        logger.warning("⚠️ Conflict Error: Purana aur naya bot ek sath chal raha hai. Ignoring...")
        return
    logger.error(msg="Exception while handling an update:", exc_info=context.error)
    try:
        if LOG_CHANNEL_ID:
            await context.bot.send_message(chat_id=LOG_CHANNEL_ID, text=f"⚠️ **CRITICAL ERROR**\n`{context.error}`", parse_mode="Markdown")
    except Exception: pass

async def run_bot_and_server():
    print("🔄 Loading Database...", flush=True)
    load_data()
    
    print("🤖 Building Telegram Bot...", flush=True)
    
    # 👇 SOLUTION FIX: Network Connection Timeout ko 5s se badhakar 60s kar diya gaya hai. Ab server weak hone par bhi timeout nahi hoga!
    t_request = HTTPXRequest(connection_pool_size=100, connect_timeout=60.0, read_timeout=60.0)
    
    bot_app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).request(t_request).build()
    
    # Is bot instance ko globally save kar rahe hain taaki app.py ise use kar sake
    config.bot_app = bot_app 
    
    commands = [
        ("start", cmd_start), ("id", cmd_id), ("del", cmd_del_msg),
        ("addadmin", cmd_add_admin), ("deladmin", cmd_del_admin), ("backup", cmd_backup),
        ("allusers", cmd_all_users), ("ban", cmd_ban), ("unban", cmd_unban),
        ("resetuser", cmd_reset_user), ("find", cmd_find_user), ("extend", cmd_extend_demo),
        ("kick", cmd_kick_user), ("myinfo", cmd_myinfo), ("batchstats", cmd_batch_stats),
        ("setwelcome", cmd_set_welcome), ("settestbot", cmd_set_testbot),
        ("locktestbot", cmd_locktestbot), ("lockdown", cmd_lockdown), ("lockfree", cmd_lockfree),
        ("lockpaid", cmd_lockpaid), ("sync", cmd_sync), ("joinall", cmd_joinall),
        ("demo", cmd_approve_demo), ("per", cmd_approve_perm), ("stats", cmd_stats),
        ("user", cmd_user_details), ("batches", cmd_batches), ("addbatch", cmd_addbatch_start),
        ("delbatch", cmd_delbatch), ("broadcast", cmd_broadcast_start), ("post", cmd_post_start),
        ("cancel", cmd_cancel), ("addcat", cmd_addcat), ("setcat", cmd_setcategory),
        ("delcat", cmd_delcat), ("clear", cmd_clear), ("maintenance", cmd_maintenance),
        ("emptybatch", cmd_emptybatch), ("userbotphone", cmd_userbotphone),
        ("userbototp", cmd_userbototp), ("userbotpass", cmd_userbotpass)
    ]
    for cmd_name, func in commands: bot_app.add_handler(CommandHandler(cmd_name, func))
    
    bot_app.add_handler(MessageHandler(filters.Regex(r"^/id(@\w+)?$") & filters.ChatType.CHANNEL, cmd_id))
    bot_app.add_handler(CallbackQueryHandler(general_callback))
    bot_app.add_handler(ChatJoinRequestHandler(handle_join_request))
    bot_app.add_handler(ChatMemberHandler(on_chat_member_update, ChatMemberHandler.CHAT_MEMBER))
    bot_app.add_handler(ChatMemberHandler(track_chats, ChatMemberHandler.MY_CHAT_MEMBER))
    bot_app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS | filters.StatusUpdate.LEFT_CHAT_MEMBER, delete_service_messages))
    bot_app.add_handler(MessageReactionHandler(handle_reaction))
    bot_app.add_handler(MessageHandler(filters.UpdateType.EDITED_MESSAGE, handle_edit))
    bot_app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, main_message_handler))
    bot_app.add_error_handler(global_error_handler)
    
    if bot_app.job_queue: 
        bot_app.job_queue.run_repeating(check_demos, interval=60, first=10)
        bot_app.job_queue.run_repeating(background_sync, interval=600, first=30)
        bot_app.job_queue.run_repeating(auto_backup_db, interval=86400, first=60)

    # 1. Start Telegram Bot Asynchronously
    print("⏳ Initializing Bot connection to Telegram API...", flush=True)
    await bot_app.initialize()
    await bot_app.start()
    await bot_app.updater.start_polling(drop_pending_updates=True)
    print("✅ Bot Started Polling successfully!", flush=True)

    # 2. Start Quart Web Server on the SAME Event Loop (Hypercorn)
    port = int(os.environ.get("PORT", "7860"))
    hyper_config = HyperConfig()
    hyper_config.bind = [f"0.0.0.0:{port}"]
    print(f"⏳ Starting Quart Web Server on port {port}...", flush=True)
    
    # Ye function server ko live rakhega aur dono (Web + Bot) ko smoothly chalayega
    await serve(web_app, hyper_config)
    
    # Graceful Shutdown
    await bot_app.updater.stop()
    await bot_app.stop()
    await bot_app.shutdown()

if __name__ == "__main__":
    try:
        # Run everything in a single master event loop
        asyncio.run(run_bot_and_server())
    except KeyboardInterrupt:
        print("Shutdown requested", flush=True)

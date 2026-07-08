import os
import asyncio
import sys
import time
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, 
    ChatJoinRequestHandler, ChatMemberHandler, MessageReactionHandler, filters, ContextTypes
)
from telegram.request import HTTPXRequest
from telegram.error import Conflict
from hypercorn.asyncio import serve
from hypercorn.config import Config as HyperConfig

print("🚀 ENTERPRISE ASYNC ENGINE STARTING...", flush=True)

import config
from config import TELEGRAM_BOT_TOKEN, LOG_CHANNEL_ID, OWNER_ID, load_data, logger
from handlers import *
from app import app as web_app  

async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    if isinstance(context.error, Conflict):
        logger.warning("⚠️ Conflict Error: Telegram says another process is already polling this same bot token (an old Render/Heroku deploy, a duplicate Space, or a stuck previous container). Stop every other running instance, then fully restart this Space. Updates will be silently dropped until only ONE poller is active.")
        return
    logger.error(msg="Exception while handling an update:", exc_info=context.error)
    try:
        if LOG_CHANNEL_ID:
            await context.bot.send_message(chat_id=LOG_CHANNEL_ID, text=f"⚠️ **CRITICAL ERROR**\n`{context.error}`", parse_mode="Markdown")
    except Exception: pass

async def init_bot_with_retry(bot_app, retries=5):
    for attempt in range(1, retries + 1):
        try:
            print(f"⏳ Attempt {attempt}/{retries} - Connecting via Gateway...", flush=True)
            await bot_app.initialize()
            me = await bot_app.bot.get_me()
            print(f"✅ Connection Established! Authorized as @{me.username} (id={me.id})", flush=True)
            return True
        except Exception as e:
            print(f"⚠️ Gateway bypass failed on attempt {attempt}: {type(e).__name__}: {e}", flush=True)
            if attempt == retries:
                print("❌ Could not connect to Telegram after all retries. Bot will NOT respond to commands until this is fixed. Web dashboard stays up.", flush=True)
                return False
            print("🔄 Re-routing socket stream in 3 seconds...", flush=True)
            await asyncio.sleep(3)

# Speed test command
async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    start_time = time.time()
    msg = await update.message.reply_text("🏓 Pinging...")
    end_time = time.time()
    ping_time = round((end_time - start_time) * 1000)
    await msg.edit_text(f"🏓 **Pong!**\n⚡ **Response Speed:** `{ping_time}ms`", parse_mode="Markdown")
            
async def run_bot_and_server():
    # 👇 MAGIC FIX: Web Server ko SABSE PEHLE background task me start karo! 👇
    # Isse Hugging Face ko 1 second me Port 7860 mil jayega aur Space TURANT "RUNNING" show karega!
    port = int(os.environ.get("PORT", "7860"))
    hyper_config = HyperConfig()
    hyper_config.bind = [f"0.0.0.0:{port}"]
    print(f"⏳ Starting Async Web Server Dashboard on port {port} immediately...", flush=True)
    web_server_task = asyncio.create_task(serve(web_app, hyper_config))
    
    # 3 second wait karo taaki HF Health Check complete ho jaye aur green label aa jaye
    await asyncio.sleep(3)
    
    print("🔄 Syncing Data Matrices...", flush=True)
    load_data()
    
    is_connected = False
    bot_app = None

    if not TELEGRAM_BOT_TOKEN:
        print("❌ TELEGRAM_BOT_TOKEN is missing/empty. Skipping bot engine, web dashboard stays up.", flush=True)
        await web_server_task
        return

    try:
        print("🤖 Configuring Telegram Bot Instance...", flush=True)
        CUSTOM_BASE_URL = os.environ.get("CUSTOM_BASE_URL", "https://api.telegram.org/bot")
        print(f"🔗 Network Base URL Pointed to: {CUSTOM_BASE_URL}", flush=True)

        t_request = HTTPXRequest(
            connection_pool_size=50, 
            connect_timeout=20.0,  
            read_timeout=20.0,
            write_timeout=20.0
        )

        bot_app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).base_url(CUSTOM_BASE_URL).request(t_request).build()
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
            ("ping", cmd_ping)
        ]
        for cmd_name, func in commands: bot_app.add_handler(CommandHandler(cmd_name, func))

        bot_app.add_handler(MessageHandler(filters.Regex(r"^/id(@\w+)?$") & filters.ChatType.CHANNEL, cmd_id))
        bot_app.add_handler(CallbackQueryHandler(general_callback))
        bot_app.add_handler(ChatJoinRequestHandler(handle_join_request))
        bot_app.add_handler(ChatMemberHandler(on_chat_member_update, ChatMemberHandler.CHAT_MEMBER))
        bot_app.add_handler(ChatMemberHandler(track_chats, ChatMemberHandler.MY_CHAT_MEMBER))
        bot_app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS | filters.StatusUpdate.LEFT_CHAT_MEMBER, delete_service_messages))
        bot_app.add_error_handler(global_error_handler)

        bot_app.add_handler(MessageReactionHandler(handle_reaction))
        bot_app.add_handler(MessageHandler(filters.UpdateType.EDITED_MESSAGE, handle_edit))
        bot_app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, main_message_handler))

        if bot_app.job_queue:
            bot_app.job_queue.run_repeating(check_demos, interval=60, first=10)
            bot_app.job_queue.run_repeating(background_sync, interval=21600, first=60)
            bot_app.job_queue.run_repeating(auto_backup_db, interval=86400, first=60)

        is_connected = await init_bot_with_retry(bot_app)

        if not is_connected:
            print("⚠️ Direct core initialization failed. Web dashboard will remain active.", flush=True)
        else:
            await bot_app.start()
            # 👇 Cloudflare Timeout bypass karne ke liye timeout=3 rakha hai 👇
            await bot_app.updater.start_polling(
                drop_pending_updates=True,
                timeout=3,
                poll_interval=0.5,
                allowed_updates=Update.ALL_TYPES
            )
            print("✅ BOT ENGINE OPERATIONAL! Polling loop listening...", flush=True)

            # 👇 Sirf OWNER_ID par fast alert bhejega bina load ke 👇
            try:
                if OWNER_ID and OWNER_ID != 0:
                    await bot_app.bot.send_message(
                        chat_id=OWNER_ID,
                        text="🟢 **BOT IS LIVE & RUNNING ON HUGGING FACE!**\n⚡ *Web Dashboard & Polling Engine Active!*",
                        parse_mode="Markdown"
                    )
            except Exception as e:
                print(f"⚠️ Owner DM alert failed: {e}", flush=True)

    except Exception:
        # 👇 CRITICAL: koi bhi bot-engine error web dashboard ko kabhi crash
        # nahi karega. Poora traceback log me print hoga taaki aap Space
        # logs me exact wajah dekh sakein, lekin container zinda rahega. 👇
        import traceback
        print("❌ BOT ENGINE FAILED TO START. Web dashboard stays up. Full error below:", flush=True)
        traceback.print_exc()
        is_connected = False

    # Web server task ko infinite chalne do taaki container kabhi stop ya restart na ho
    await web_server_task

    if is_connected and bot_app is not None:
        try:
            await bot_app.updater.stop()
            await bot_app.stop()
            await bot_app.shutdown()
        except Exception:
            pass

if __name__ == "__main__":
    import nest_asyncio
    nest_asyncio.apply() 
    try:
        asyncio.run(run_bot_and_server())
    except KeyboardInterrupt:
        print("Safely shutting down terminal cores.", flush=True)

import os
import asyncio
import sys
import time
import traceback
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


def _safe_port(default=7860):
    raw = os.environ.get("PORT", "")
    raw = (raw or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        print(f"⚠️ PORT env var '{raw}' is not a valid integer. Using default {default}.", flush=True)
        return default


async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    if isinstance(context.error, Conflict):
        logger.warning(
            "⚠️ Conflict Error: Telegram says another process is already polling this same bot "
            "token (an old Render/Heroku deploy, a duplicate Space, or a stuck previous container). "
            "Stop every other running instance, then fully restart this Space. Updates will be "
            "silently dropped until only ONE poller is active."
        )
        return
    logger.error(msg="Exception while handling an update:", exc_info=context.error)
    try:
        if LOG_CHANNEL_ID:
            await context.bot.send_message(
                chat_id=LOG_CHANNEL_ID,
                text=f"⚠️ **CRITICAL ERROR**\n`{context.error}`",
                parse_mode="Markdown"
            )
    except Exception:
        pass


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


async def build_and_run_bot_engine():
    """
    Builds, connects, and runs the Telegram bot engine.
    Every failure inside here is caught and logged - it NEVER propagates up
    and kills the web server or the process.
    Returns the running bot_app on success, or None on failure.
    """
    if not TELEGRAM_BOT_TOKEN:
        print("❌ TELEGRAM_BOT_TOKEN is missing/empty. Skipping bot engine, web dashboard stays up.", flush=True)
        return None

    try:
        print("🤖 Configuring Telegram Bot Instance...", flush=True)
        CUSTOM_BASE_URL = os.environ.get("CUSTOM_BASE_URL", "").strip() or "https://api.telegram.org/bot"
        print(f"🔗 Network Base URL Pointed to: {CUSTOM_BASE_URL}", flush=True)
        if "api.telegram.org" not in CUSTOM_BASE_URL:
            print(
                "⚠️ WARNING: This is NOT the official Telegram API domain. You're routing through a "
                "third-party relay/proxy. Long-polling (getUpdates) often breaks silently through such "
                "relays because they don't hold connections open long enough. Remove the CUSTOM_BASE_URL "
                "Space variable to go direct.",
                flush=True
            )

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
        for cmd_name, func in commands:
            bot_app.add_handler(CommandHandler(cmd_name, func))

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
            print("⚠️ Bot engine could not connect. Web dashboard will remain active.", flush=True)
            return None

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

        return bot_app

    except Exception:
        print("❌ BOT ENGINE FAILED TO START. Web dashboard stays up. Full error below:", flush=True)
        traceback.print_exc()
        return None


async def bot_supervisor_loop():
    """
    Keeps trying to (re)start the bot engine forever, in the background,
    without ever touching the web server. If the bot engine dies for any
    reason after a successful start, this notices and rebuilds it.
    """
    while True:
        bot_app = await build_and_run_bot_engine()
        if bot_app is None:
            # Could not connect this round - wait and try again, forever.
            print("🔁 Will retry bot engine startup in 30 seconds...", flush=True)
            await asyncio.sleep(30)
            continue

        # Bot is up. Watch it - if the updater's polling task ever dies
        # unexpectedly, clean up and loop around to rebuild it.
        try:
            while True:
                await asyncio.sleep(15)
                if not bot_app.updater.running:
                    print("⚠️ Polling loop stopped unexpectedly. Rebuilding bot engine...", flush=True)
                    break
        except Exception:
            print("❌ Bot supervisor watch loop crashed. Rebuilding bot engine. Full error below:", flush=True)
            traceback.print_exc()
        finally:
            try:
                if bot_app.updater.running:
                    await bot_app.updater.stop()
                if bot_app.running:
                    await bot_app.stop()
                await bot_app.shutdown()
            except Exception:
                pass

        await asyncio.sleep(5)


async def main():
    port = _safe_port(7860)
    hyper_config = HyperConfig()
    hyper_config.bind = [f"0.0.0.0:{port}"]
    print(f"⏳ Starting Async Web Server Dashboard on port {port} immediately...", flush=True)

    # 👇 Web server runs forever as its own task. Nothing the bot engine does
    # can ever cancel or crash this. The Space stays "Running" no matter what. 👇
    web_server_task = asyncio.create_task(serve(web_app, hyper_config))

    # 3 second wait karo taaki HF Health Check complete ho jaye aur green label aa jaye
    await asyncio.sleep(3)

    print("🔄 Syncing Data Matrices...", flush=True)
    try:
        load_data()
    except Exception:
        print("❌ load_data() failed. Continuing with empty in-memory DB. Full error below:", flush=True)
        traceback.print_exc()

    # 👇 Bot engine runs as its own supervised background task too, completely
    # decoupled from the web server. If it keeps failing, it keeps quietly
    # retrying in the background forever instead of taking anything down. 👇
    asyncio.create_task(bot_supervisor_loop())

    await web_server_task


if __name__ == "__main__":
    import nest_asyncio
    nest_asyncio.apply()

    # 👇 OUTERMOST SAFETY NET: no matter what goes wrong, anywhere, the
    # process itself never exits with an error. This is what stops
    # Hugging Face from ever seeing a crash to restart from. 👇
    while True:
        try:
            asyncio.run(main())
            # main() only returns if the web server task itself ends, which
            # should never happen in normal operation - but if it does,
            # restart everything rather than letting the process exit.
            print("⚠️ main() returned unexpectedly. Restarting in 5 seconds...", flush=True)
            time.sleep(5)
        except KeyboardInterrupt:
            print("Safely shutting down terminal cores.", flush=True)
            break
        except Exception:
            print("❌ FATAL ERROR AT TOP LEVEL. This should never happen, but the process will restart itself instead of dying. Full error below:", flush=True)
            traceback.print_exc()
            time.sleep(5)

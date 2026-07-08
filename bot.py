import os
import time
import asyncio
import traceback
import importlib

# If you don't even see THIS line in the logs, the container never reached
# Python (a build/entrypoint problem, not a code problem).
print("🟢 BOOT[1/5]: bot.py process started.", flush=True)


def _safe_port(default=7860):
    raw = (os.environ.get("PORT", "") or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        print(f"⚠️ PORT '{raw}' invalid, using {default}.", flush=True)
        return default


PORT = _safe_port()

# ONLY light, rock-solid imports at module level. Nothing you edited, nothing
# heavy (no telegram / pyrogram / handlers here) so this file can ALWAYS reach
# main() and open the port no matter what.
from hypercorn.asyncio import serve
from hypercorn.config import Config as HyperConfig

print("🟢 BOOT[2/5]: web server libs loaded.", flush=True)


def _load_web_app():
    """Import the Quart dashboard. If it fails, serve a tiny fallback so the
    port still opens (HF shows 'Running') and the error is visible in logs."""
    try:
        from app import app as web_app
        print("🟢 BOOT[3/5]: dashboard app imported.", flush=True)
        return web_app
    except Exception:
        print("❌ dashboard import failed — serving fallback page so the Space "
              "still shows Running. Traceback:", flush=True)
        traceback.print_exc()
        from quart import Quart
        fb = Quart(__name__)

        @fb.route("/")
        async def _root():
            return "Web layer import failed. Check container logs.", 500

        return fb


WEB_APP = _load_web_app()


async def _run_bot_engine():
    """Everything that can possibly break lives here and runs as a background
    task. Heavy imports happen in a worker THREAD, so even a hang or a slow
    import can never freeze the web server or the HF health check."""
    print("🟡 BOT: importing telegram + your handlers (in worker thread)...", flush=True)

    def _heavy_imports():
        import config as _config
        _handlers = importlib.import_module("handlers")
        return _config, _handlers

    try:
        config, H = await asyncio.to_thread(_heavy_imports)
    except Exception:
        print("❌ BOT IMPORT FAILED. Dashboard stays up (Space = Running) but the "
              "bot can't start until this import is fixed. Traceback:", flush=True)
        traceback.print_exc()
        return

    from telegram import Update
    from telegram.request import HTTPXRequest
    from telegram.error import Conflict
    from telegram.ext import (
        ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler,
        ChatJoinRequestHandler, ChatMemberHandler, MessageReactionHandler,
        filters, ContextTypes,
    )

    logger = config.logger
    TELEGRAM_BOT_TOKEN = config.TELEGRAM_BOT_TOKEN
    OWNER_ID = config.OWNER_ID
    LOG_CHANNEL_ID = config.LOG_CHANNEL_ID

    if not TELEGRAM_BOT_TOKEN:
        print("❌ TELEGRAM_BOT_TOKEN secret is empty. Set it in HF Space settings "
              "→ Variables & secrets. Dashboard stays up.", flush=True)
        return

    async def global_error_handler(update, context):
        if isinstance(context.error, Conflict):
            logger.warning("⚠️ Conflict: another process is polling this same token "
                           "(old deploy / duplicate Space). Stop all other instances.")
            return
        logger.error(msg="Exception while handling an update:", exc_info=context.error)
        try:
            if LOG_CHANNEL_ID:
                await context.bot.send_message(
                    LOG_CHANNEL_ID, f"⚠️ *CRITICAL ERROR*\n`{context.error}`",
                    parse_mode="Markdown")
        except Exception:
            pass

    async def cmd_ping(update, context):
        t = time.time()
        m = await update.message.reply_text("🏓 Pinging...")
        await m.edit_text(f"🏓 *Pong!*\n⚡ `{round((time.time() - t) * 1000)}ms`",
                          parse_mode="Markdown")

    # 👇 PROXY PURI TARAH REMOVE KAR DIYA GAYA HAI (Strictly Direct Telegram API) 👇
    print(
        "🟡 BOT: Connecting DIRECTLY to official Telegram API"
        " (api.telegram.org)...",
        flush=True,
    )

    request = HTTPXRequest(
        connection_pool_size=50,
        connect_timeout=20.0,
        read_timeout=20.0,
        write_timeout=20.0,
    )
    # .base_url() ko yahan se hamesha ke liye remove kar diya hai!
    app = (
        ApplicationBuilder()
        .token(TELEGRAM_BOT_TOKEN)
        .request(request)
        .build()
    )
    config.bot_app = app

    commands = [
        ("start", H.cmd_start), ("id", H.cmd_id), ("del", H.cmd_del_msg),
        ("addadmin", H.cmd_add_admin), ("deladmin", H.cmd_del_admin), ("backup", H.cmd_backup),
        ("allusers", H.cmd_all_users), ("ban", H.cmd_ban), ("unban", H.cmd_unban),
        ("resetuser", H.cmd_reset_user), ("find", H.cmd_find_user), ("extend", H.cmd_extend_demo),
        ("kick", H.cmd_kick_user), ("myinfo", H.cmd_myinfo), ("batchstats", H.cmd_batch_stats),
        ("setwelcome", H.cmd_set_welcome), ("settestbot", H.cmd_set_testbot),
        ("locktestbot", H.cmd_locktestbot), ("lockdown", H.cmd_lockdown), ("lockfree", H.cmd_lockfree),
        ("lockpaid", H.cmd_lockpaid), ("sync", H.cmd_sync), ("joinall", H.cmd_joinall),
        ("demo", H.cmd_approve_demo), ("per", H.cmd_approve_perm), ("stats", H.cmd_stats),
        ("user", H.cmd_user_details), ("batches", H.cmd_batches), ("addbatch", H.cmd_addbatch_start),
        ("delbatch", H.cmd_delbatch), ("broadcast", H.cmd_broadcast_start), ("post", H.cmd_post_start),
        ("cancel", H.cmd_cancel), ("addcat", H.cmd_addcat), ("setcat", H.cmd_setcategory),
        ("delcat", H.cmd_delcat), ("clear", H.cmd_clear), ("maintenance", H.cmd_maintenance),
        ("ping", cmd_ping),
    ]
    for name, fn in commands:
        app.add_handler(CommandHandler(name, fn))

    app.add_handler(MessageHandler(filters.Regex(r"^/id(@\w+)?$") & filters.ChatType.CHANNEL, H.cmd_id))
    app.add_handler(CallbackQueryHandler(H.general_callback))
    app.add_handler(ChatJoinRequestHandler(H.handle_join_request))
    app.add_handler(ChatMemberHandler(H.on_chat_member_update, ChatMemberHandler.CHAT_MEMBER))
    app.add_handler(ChatMemberHandler(H.track_chats, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(MessageHandler(
        filters.StatusUpdate.NEW_CHAT_MEMBERS | filters.StatusUpdate.LEFT_CHAT_MEMBER,
        H.delete_service_messages))
    app.add_handler(MessageReactionHandler(H.handle_reaction))
    app.add_handler(MessageHandler(filters.UpdateType.EDITED_MESSAGE, H.handle_edit))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, H.main_message_handler))
    app.add_error_handler(global_error_handler)

    if app.job_queue:
        app.job_queue.run_repeating(H.check_demos, interval=60, first=10)
        app.job_queue.run_repeating(H.background_sync, interval=21600, first=60)
        app.job_queue.run_repeating(H.auto_backup_db, interval=86400, first=60)

    for attempt in range(1, 6):
        try:
            print(f"🟡 BOT: connecting to Telegram (attempt {attempt}/5)...", flush=True)
            await app.initialize()
            me = await app.bot.get_me()
            print(f"🟢 BOT: authorized as @{me.username} (id={me.id}).", flush=True)
            break
        except Exception as e:
            print(f"⚠️ connect failed: {type(e).__name__}: {e}", flush=True)
            if attempt == 5:
                print("❌ Could not reach Telegram after 5 tries. If HF's IP is "
                      "rate-limited, set a CUSTOM_BASE_URL proxy secret. Dashboard stays up.",
                      flush=True)
                return
            await asyncio.sleep(3)

    await app.start()
    await app.updater.start_polling(
        drop_pending_updates=True, timeout=20, poll_interval=1.0,
        allowed_updates=Update.ALL_TYPES,
    )
    print("🟢 BOT: OPERATIONAL — polling for commands.", flush=True)

    try:
        if OWNER_ID:
            await app.bot.send_message(
                OWNER_ID, "🟢 *BOT LIVE ON HUGGING FACE!*\n⚡ Dashboard + polling active.",
                parse_mode="Markdown")
    except Exception as e:
        print(f"⚠️ owner DM failed: {e}", flush=True)

    try:
        while app.updater.running:
            await asyncio.sleep(15)
    finally:
        try:
            await app.updater.stop()
            await app.stop()
            await app.shutdown()
        except Exception:
            pass
    print("⚠️ BOT: polling stopped; supervisor will rebuild.", flush=True)


async def _bot_supervisor():
    while True:
        try:
            await _run_bot_engine()
        except Exception:
            print("❌ bot engine crashed; rebuilding in 15s. Traceback:", flush=True)
            traceback.print_exc()
        await asyncio.sleep(15)


async def main():
    cfg = HyperConfig()
    cfg.bind = [f"0.0.0.0:{PORT}"]
    cfg.accesslog = "-"
    print(f"🟢 BOOT[4/5]: opening web port 0.0.0.0:{PORT} "
          f"(this is what makes HF show 'Running')...", flush=True)

    # The web server is the heartbeat. It starts FIRST, before any bot code.
    web_task = asyncio.create_task(serve(WEB_APP, cfg))
    await asyncio.sleep(1)  # let the socket bind

    async def _late_start():
        try:
            import config
            await asyncio.to_thread(config.load_data)  # off-thread: slow DB can't stall the port
            print("🟢 BOOT[5/5]: data loaded.", flush=True)
        except Exception:
            print("⚠️ load_data failed; continuing with empty DB. Traceback:", flush=True)
            traceback.print_exc()
        asyncio.create_task(_bot_supervisor())

    asyncio.create_task(_late_start())
    await web_task


if __name__ == "__main__":
    import nest_asyncio
    import traceback
    import time
    
    nest_asyncio.apply() 
    try:
        # 👇 YAHAN MAGIC FIX KIYA GAYA HAI (main() kar diya hai) 👇
        asyncio.run(main())
    except KeyboardInterrupt:
        print("🛑 Safely shutting down terminal cores.", flush=True)
    except Exception as e:
        # Diagnostic Crash Trap Engine
        print("=========================================================", flush=True)
        print("🚨 FATAL CRASH DETECTED! PREVENTING CONTAINER EXIT...", flush=True)
        print("=========================================================", flush=True)
        print("👇 ASLI ERROR NEECHE LIKHI HAI (DHYAN SE PADHO) 👇\n", flush=True)
        
        traceback.print_exc()
        
        print("\n=========================================================", flush=True)
        print("⏳ Diagnostic Mode: Container ko 10 minute ke liye zinda rakha ja raha hai...", flush=True)
        print("👉 Ab aaram se Hugging Face ke 'Logs' tab me ja kar error padho!", flush=True)
        print("=========================================================", flush=True)
        
        time.sleep(600)

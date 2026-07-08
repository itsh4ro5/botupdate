import os
import time
import asyncio
import traceback
import importlib

print("🟢 BOOT[1/5]: Pyrogram MTProto Engine Starting...", flush=True)

def _safe_port(default=7860):
    raw = (os.environ.get("PORT", "") or "").strip()
    return int(raw) if raw.isdigit() else default

PORT = _safe_port()

# Light imports for Web Dashboard
from hypercorn.asyncio import serve
from hypercorn.config import Config as HyperConfig

print("🟢 BOOT[2/5]: Web server libs loaded.", flush=True)

def _load_web_app():
    try:
        from app import app as web_app
        print("🟢 BOOT[3/5]: Dashboard app imported.", flush=True)
        return web_app
    except Exception:
        print("❌ Dashboard import failed — Serving fallback page.", flush=True)
        traceback.print_exc()
        from quart import Quart
        fb = Quart(__name__)
        @fb.route("/")
        async def _root():
            return "Web layer import failed. Check container logs.", 500
        return fb

WEB_APP = _load_web_app()

async def _run_pyrogram_engine():
    """Pyrogram MTProto Sockets Worker Engine (Zero Proxy Required)"""
    print("🟡 BOT: Loading Config & Handlers...", flush=True)

    try:
        import config
        import handlers as H
    except Exception:
        print("❌ BOT IMPORT FAILED. Dashboard stays up (Space = Running). Traceback:", flush=True)
        traceback.print_exc()
        return

    from pyrogram import Client, filters
    from pyrogram.types import Message, CallbackQuery

    API_ID = getattr(config, "API_ID", 0)
    API_HASH = getattr(config, "API_HASH", "")
    BOT_TOKEN = getattr(config, "TELEGRAM_BOT_TOKEN", "")
    OWNER_ID = getattr(config, "OWNER_ID", 0)

    if not BOT_TOKEN or not API_ID or not API_HASH:
        print("❌ TELEGRAM_BOT_TOKEN, API_ID ya API_HASH missing hai! HF Secrets check karein.", flush=True)
        return

    # 🔥 PYROGRAM MTPROTO CLIENT (Direct TCP Connection - Zero Proxy!)
    app = Client(
        "kamal_master_bot",
        api_id=int(API_ID),
        api_hash=str(API_HASH),
        bot_token=str(BOT_TOKEN),
        in_memory=True, # Disk I/O bachane ke liye memory me session
        workers=50      # 50 Parallel async workers for super fast speed!
    )
    config.bot_app = app

    # --- COMMAND HANDLERS REGISTRATION ---
    @app.on_message(filters.command("start") & filters.private)
    async def _on_start(client: Client, message: Message):
        await H.cmd_start(client, message)

    @app.on_message(filters.command("ping"))
    async def _on_ping(client: Client, message: Message):
        t = time.time()
        m = await message.reply_text("🏓 **Pinging MTProto Sockets...**")
        await m.edit_text(f"🏓 **Pong!**\n⚡ **Speed:** `{round((time.time() - t) * 1000)}ms`\n🛡️ **Protocol:** `Pyrogram MTProto`")

    @app.on_message(filters.command("stats") & filters.user(OWNER_ID))
    async def _on_stats(client: Client, message: Message):
        await H.cmd_stats(client, message)

    @app.on_callback_query()
    async def _on_callback(client: Client, query: CallbackQuery):
        await H.general_callback(client, query)

    # --- STARTING THE MTPROTO CONNECTION ---
    for attempt in range(1, 6):
        try:
            print(f"🟡 BOT: Connecting via Pyrogram MTProto Sockets (Attempt {attempt}/5)...", flush=True)
            await app.start()
            me = await app.get_me()
            print(f"🟢 BOT: Authorized successfully as @{me.username} (ID: {me.id})!", flush=True)
            break
        except Exception as e:
            print(f"⚠️ Socket Connect Error: {type(e).__name__}: {e}", flush=True)
            if attempt == 5:
                print("❌ Could not connect to Telegram servers.", flush=True)
                return
            await asyncio.sleep(3)

    print("🟢 BOT: OPERATIONAL — MTProto Socket Listening...", flush=True)

    try:
        if OWNER_ID:
            await app.send_message(
                int(OWNER_ID), 
                "🟢 **BOT IS LIVE ON HUGGING FACE (PYROGRAM MODE)!**\n\n"
                "⚡ **Status:** `Running Smoothly without Proxy`\n"
                "🛡️ **Engine:** `Pyrogram MTProto TCP Sockets`\n"
                "💡 *Send /ping to test speed!*"
            )
    except Exception as e:
        print(f"⚠️ Owner alert failed: {e}", flush=True)

    # Background Tasks Scheduler
    async def run_jobs():
        while True:
            try:
                await asyncio.sleep(60)
                if hasattr(H, "check_demos"):
                    await H.check_demos(app)
            except Exception as e:
                print(f"Job Error: {e}", flush=True)
                
    asyncio.create_task(run_jobs())

    # Keep alive loop
    try:
        while True:
            await asyncio.sleep(15)
    finally:
        try:
            await app.stop()
        except Exception:
            pass
        print("⚠️ BOT: Socket disconnected.", flush=True)

async def _bot_supervisor():
    while True:
        try:
            await _run_pyrogram_engine()
        except Exception:
            print("❌ Bot engine crashed; Rebuilding in 15s. Traceback:", flush=True)
            traceback.print_exc()
        await asyncio.sleep(15)

async def main():
    cfg = HyperConfig()
    cfg.bind = [f"0.0.0.0:{PORT}"]
    cfg.accesslog = "-"
    print(f"🟢 BOOT[4/5]: Opening web port 0.0.0.0:{PORT} (Hugging Face Health Check)...", flush=True)

    # Start Dashboard first
    web_task = asyncio.create_task(serve(WEB_APP, cfg))
    await asyncio.sleep(1) 

    async def _late_start():
        try:
            import config
            await asyncio.to_thread(config.load_data)
            print("🟢 BOOT[5/5]: Database matrices loaded.", flush=True)
        except Exception:
            print("⚠️ load_data failed; Continuing. Traceback:", flush=True)
            traceback.print_exc()
        asyncio.create_task(_bot_supervisor())

    asyncio.create_task(_late_start())
    await web_task

if __name__ == "__main__":
  import nest_asyncio
  import time
  import traceback

  nest_asyncio.apply()
  try:
    # 👇 YAHAN EXPLICITLY main() CALL KARNA HAI 👇
    asyncio.run(main())
  except KeyboardInterrupt:
    print("🛑 Safely shutting down terminal cores.", flush=True)
  except Exception as e:
    print(
        "=========================================================", flush=True
    )
    print("🚨 FATAL CRASH DETECTED! PREVENTING CONTAINER EXIT...", flush=True)
    print(
        "=========================================================", flush=True
    )
    print("👇 ASLI ERROR NEECHE LIKHI HAI (DHYAN SE PADHO) 👇\n", flush=True)

    traceback.print_exc()

    print(
        "\n=========================================================", flush=True
    )
    print(
        "⏳ Diagnostic Mode: Container ko 10 minute ke liye zinda rakha ja"
        " raha hai...",
        flush=True,
    )
    print(
        "👉 Ab aaram se Hugging Face ke 'Logs' tab me ja kar error padho!",
        flush=True,
    )
    print(
        "=========================================================", flush=True
    )

    time.sleep(600)

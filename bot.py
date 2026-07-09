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

    # 🔥 PYROGRAM MTPROTO CLIENT
    app = Client(
        "kamal_master_bot",
        api_id=int(API_ID),
        api_hash=str(API_HASH),
        bot_token=str(BOT_TOKEN),
        in_memory=True, 
        workers=50      
    )
    config.bot_app = app

    # =====================================================================
    # 1. COMMAND HANDLERS REGISTRATION
    # =====================================================================
    @app.on_message(filters.command("start") & filters.private)
    async def _on_start(client: Client, message: Message):
        await H.cmd_start(client, message)

    @app.on_message(filters.command("ping"))
    async def _on_ping(client: Client, message: Message):
        t = time.time()
        m = await message.reply_text("🏓 **Pinging MTProto Sockets...**")
        await m.edit_text(f"🏓 **Pong!**\n⚡ **Speed:** `{round((time.time() - t) * 1000)}ms`\n🛡️ **Protocol:** `Pyrogram MTProto`")

    @app.on_message(filters.command("id") & filters.private)
    async def _on_id(client: Client, message: Message):
        await H.cmd_id(client, message)

    @app.on_message(filters.channel & filters.regex(r"^/id(@\w+)?$"))
    async def _on_channel_id(client: Client, message: Message):
        await H.cmd_id(client, message)

    # Saare 40+ Admin, Owner aur General Commands Mapping
    all_commands = {
        "myinfo": H.cmd_myinfo, "addadmin": H.cmd_add_admin, "deladmin": H.cmd_del_admin,
        "backup": H.cmd_backup, "allusers": H.cmd_all_users, "ban": H.cmd_ban,
        "unban": H.cmd_unban, "resetuser": H.cmd_reset_user, "find": H.cmd_find_user,
        "extend": H.cmd_extend_demo, "kick": H.cmd_kick_user, "batchstats": H.cmd_batch_stats,
        "setwelcome": H.cmd_set_welcome, "settestbot": H.cmd_set_testbot,
        "locktestbot": H.cmd_locktestbot, "lockdown": H.cmd_lockdown, "lockfree": H.cmd_lockfree,
        "lockpaid": H.cmd_lockpaid, "sync": H.cmd_sync, "joinall": H.cmd_joinall,
        "demo": H.cmd_approve_demo, "per": H.cmd_approve_perm, "perm": H.cmd_approve_perm,
        "stats": H.cmd_stats, "user": H.cmd_user_details, "batches": H.cmd_batches,
        "addbatch": H.cmd_addbatch_start, "delbatch": H.cmd_delbatch, "broadcast": H.cmd_broadcast_start,
        "post": H.cmd_post_start, "cancel": H.cmd_cancel, "addcat": H.cmd_addcat,
        "setcat": H.cmd_setcategory, "delcat": H.cmd_delcat, "clear": H.cmd_clear,
        "maintenance": H.cmd_maintenance, "emptybatch": H.cmd_emptybatch,
        "userbotphone": H.cmd_userbotphone, "userbototp": H.cmd_userbototp,
        "userbotpass": H.cmd_userbotpass, "del": H.cmd_del_msg,
    }

    @app.on_message(filters.command(list(all_commands.keys())))
    async def _on_all_cmds(client: Client, message: Message):
        if not message.command: return
        cmd_name = message.command[0].lower()
        if cmd_name in all_commands:
            await all_commands[cmd_name](client, message)

    # =====================================================================
    # 2. CALLBACK QUERY HANDLER (Buttons)
    # =====================================================================
    @app.on_callback_query()
    async def _on_callback(client: Client, query: CallbackQuery):
        await H.general_callback(client, query)

    # =====================================================================
    # 3. CHAT JOIN REQUEST & MEMBER UPDATES (Auto-Kick & Approvals)
    # =====================================================================
    @app.on_chat_join_request()
    async def _on_join_req(client: Client, request):
        await H.handle_join_request(client, request)

    @app.on_chat_member_updated()
    async def _on_member_update(client: Client, update):
        await H.on_chat_member_update(client, update)
        if hasattr(H, "track_chats"):
            await H.track_chats(client, update)

    @app.on_message(filters.new_chat_members | filters.left_chat_member)
    async def _on_service_msg(client: Client, message: Message):
        if hasattr(H, "delete_service_messages"):
            await H.delete_service_messages(client, message)

    # =====================================================================
    # 4. LIVE MESSAGE SYNCING (Edit, Delete)
    # =====================================================================
    @app.on_edited_message(filters.all)
    async def _on_edit(client: Client, message: Message):
        if hasattr(H, "handle_edit"):
            await H.handle_edit(client, message)

    @app.on_deleted_messages()
    async def _on_delete(client: Client, messages):
        if hasattr(H, "handle_delete"):
            await H.handle_delete(client, messages)

    # =====================================================================
    # 5. REGULAR MESSAGES & SUPPORT TICKETS (🔥 YAHAN SOLVE HUA AAPKA BUG 🔥)
    # Ye handler normal text/photo/video ko Support Group me forward karega
    # =====================================================================
    @app.on_message(filters.all & ~filters.command(list(all_commands.keys()) + ["start", "ping", "id"]))
    async def _on_regular_msg(client: Client, message: Message):
        if message.text and message.text.startswith("/"): return
        if message.caption and message.caption.startswith("/"): return
        await H.main_message_handler(client, message)

    # --- STARTING THE MTPROTO CONNECTION ---
    for attempt in range(1, 6):
        try:
            print(f"🟡 BOT: Connecting via Pyrogram MTProto Sockets (Attempt {attempt}/5)...", flush=True)
            await app.start()
            me = await app.get_me()
            print(f"✅ BOT LIVE! Authorized successfully as @{me.username} (ID: {me.id})!", flush=True)
            
            # 🔥 AUTOMATIC PEER CACHE SYNC
            print("🔄 Syncing dialogs & peers from Telegram cache...", flush=True)
            try:
                async for _ in app.get_dialogs(limit=100):
                    pass
                print("✅ Peer cache synced successfully!", flush=True)
            except Exception as diag_err:
                print(f"⚠️ Dialog sync warning: {diag_err}", flush=True)
                
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
                "🛡️ **Engine:** `All Handlers & Web Dashboard 100% Fixed`\n"
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
    nest_asyncio.apply() 
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("🛑 Safely shutting down terminal cores.", flush=True)
    except Exception as e:
        print("=========================================================", flush=True)
        print("🚨 FATAL CRASH DETECTED! PREVENTING CONTAINER EXIT...", flush=True)
        print("=========================================================", flush=True)
        traceback.print_exc()
        print("\n⏳ Diagnostic Mode: Container sleeping for 10 minutes...", flush=True)
        time.sleep(600)

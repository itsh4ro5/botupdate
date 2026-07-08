import os
import time
import asyncio
import traceback

print("🟢 BOOT[1/5]: Pyrogram MTProto Engine Starting...", flush=True)

def _safe_port(default=7860):
    raw = (os.environ.get("PORT", "") or "").strip()
    return int(raw) if raw.isdigit() else default

PORT = _safe_port()

# =====================================================================
# 1. QUART WEB SERVER (Preserving your awesome app.py Dashboard!)
# =====================================================================
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

# =====================================================================
# 2. PYROGRAM BOT ENGINE (100% Synced with handlers.py)
# =====================================================================
async def _run_pyrogram_engine():
    print("🟡 BOT: Loading Config & Handlers...", flush=True)
    try:
        import config
        import handlers as H
    except Exception:
        print("❌ BOT IMPORT FAILED. Dashboard stays up. Traceback:", flush=True)
        traceback.print_exc()
        return

    from pyrogram import Client, filters
    from pyrogram.types import CallbackQuery, Message

    API_ID = getattr(config, "API_ID", 0)
    API_HASH = getattr(config, "API_HASH", "")
    BOT_TOKEN = getattr(config, "TELEGRAM_BOT_TOKEN", "")
    OWNER_ID = getattr(config, "OWNER_ID", 0)

    if not BOT_TOKEN or not API_ID or not API_HASH:
        print("❌ TELEGRAM_BOT_TOKEN, API_ID ya API_HASH missing hai! HF Secrets check karein.", flush=True)
        return

    # 🔥 PYROGRAM MTPROTO CLIENT
    bot = Client(
        "kamal_master_bot",
        api_id=int(API_ID),
        api_hash=str(API_HASH),
        bot_token=str(BOT_TOKEN),
        workers=50
    )
    config.bot_app = bot

    # --- A. PRIVATE CHAT COMMANDS ---
    @bot.on_message(filters.command("start") & filters.private)
    async def _on_start(client: Client, message: Message):
        await H.cmd_start(client, message)

    @bot.on_message(filters.command("ping"))
    async def _on_ping(client: Client, message: Message):
        t = time.time()
        m = await message.reply_text("🏓 **Pinging MTProto Sockets...**")
        await m.edit_text(f"🏓 **Pong!**\n⚡ **Speed:** `{round((time.time() - t) * 1000)}ms`\n🛡️ **Protocol:** `Pyrogram MTProto`")

    @bot.on_message(filters.command("id") & filters.private)
    async def _on_id(client: Client, message: Message):
        await H.cmd_id(client, message)

    @bot.on_message(filters.channel & filters.regex(r"^/id(@\w+)?$"))
    async def _on_channel_id(client: Client, message: Message):
        await H.cmd_id(client, message)

    # --- B. FULL ADMIN COMMANDS MAPPING (Matches handlers.py exactly) ---
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

    @bot.on_message(filters.command(list(all_commands.keys())))
    async def _on_all_cmds(client: Client, message: Message):
        if not message.command: return
        cmd_name = message.command[0].lower()
        if cmd_name in all_commands:
            await all_commands[cmd_name](client, message)

    # --- C. BUTTONS & CALLBACKS ---
    @bot.on_callback_query()
    async def _on_callback(client: Client, query: CallbackQuery):
        await H.general_callback(client, query)

    # --- D. JOIN REQUESTS & MEMBER UPDATES ---
    @bot.on_chat_join_request()
    async def _on_join_req(client: Client, request):
        await H.handle_join_request(client, request)

    @bot.on_chat_member_updated()
    async def _on_member_update(client: Client, update):
        await H.on_chat_member_update(client, update)
        await H.track_chats(client, update)

    @bot.on_message(filters.new_chat_members | filters.left_chat_member)
    async def _on_service_msg(client: Client, message: Message):
        await H.delete_service_messages(client, message)

    # --- E. LIVE MESSAGE SYNCING (Edit, Delete, Reactions) ---
    @bot.on_edited_message(filters.all)
    async def _on_edit(client: Client, message: Message):
        await H.handle_edit(client, message)

    @bot.on_deleted_messages()
    async def _on_delete(client: Client, messages):
        if hasattr(H, "handle_delete"):
            await H.handle_delete(client, messages)

    if hasattr(bot, "on_message_reaction"):
        @bot.on_message_reaction()
        async def _on_reaction(client: Client, update):
            if hasattr(H, "handle_reaction"):
                await H.handle_reaction(client, update)

    # --- F. REGULAR MESSAGES (Support Tickets Engine) ---
    @bot.on_message(filters.all & ~filters.command(list(all_commands.keys()) + ["start", "ping", "id"]))
    async def _on_regular_msg(client: Client, message: Message):
        if message.text and message.text.startswith("/"): return
        if message.caption and message.caption.startswith("/"): return
        await H.main_message_handler(client, message)

    # =====================================================================
    # 3. STARTING THE MTPROTO CONNECTION
    # =====================================================================
    print("🚀 BOT ENGINE OPERATIONAL! Connecting to Telegram MTProto Sockets...", flush=True)
    for attempt in range(1, 6):
        try:
            await bot.start()
            me = await bot.get_me()
            print(f"✅ BOT LIVE! Authorized successfully as @{me.username} (ID: {me.id})!", flush=True)
            break
        except Exception as e:
            print(f"⚠️ Socket Connect Error: {type(e).__name__}: {e}", flush=True)
            if attempt == 5:
                print("❌ Could not connect to Telegram servers.", flush=True)
                return
            await asyncio.sleep(3)

    try:
        if OWNER_ID:
            await bot.send_message(
                int(OWNER_ID),
                "🟢 **BOT IS LIVE ON HUGGING FACE!**\n\n"
                "⚡ **Status:** `Running Smoothly (Quart + Pyrogram)`\n"
                "🛡️ **Engine:** `100% Synced with handlers.py & app.py`\n"
                "💡 *Send /ping to test response speed!*"
            )
    except Exception as e:
        print(f"⚠️ Owner DM alert failed: {e}", flush=True)

    # =====================================================================
    # 4. BACKGROUND JOBS SCHEDULER
    # =====================================================================
    async def run_jobs():
        await asyncio.sleep(10)
        last_sync = time.time()
        last_backup = time.time()
        while True:
            try:
                await asyncio.sleep(60)
                # 1 Minute checks
                if hasattr(H, "check_demos"):
                    await H.check_demos(bot)

                now = time.time()
                # 6 Hour Sync Logic
                if now - last_sync >= 21600:
                    if hasattr(H, "background_sync"):
                        asyncio.create_task(H.background_sync(bot))
                    last_sync = now

                # 24 Hour Backup Logic
                if now - last_backup >= 86400:
                    if hasattr(H, "auto_backup_db"):
                        asyncio.create_task(H.auto_backup_db(bot))
                    last_backup = now

            except Exception as e:
                print(f"⚠️ Background job warning: {e}", flush=True)

    asyncio.create_task(run_jobs())

    try:
        while True:
            await asyncio.sleep(15)
    finally:
        try:
            await bot.stop()
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


# =====================================================================
# MAIN INITIALIZATION LOOP
# =====================================================================
async def main():
    cfg = HyperConfig()
    cfg.bind = [f"0.0.0.0:{PORT}"]
    cfg.accesslog = "-"
    print(f"🟢 BOOT[4/5]: Opening web port 0.0.0.0:{PORT} (Hugging Face Health Check)...", flush=True)

    # Web App starts instantly (This fixes the "Restarting" issue while keeping UI)
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

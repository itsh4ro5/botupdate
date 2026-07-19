import os
import time
import asyncio
import traceback
import importlib
from pyrogram import StopPropagation

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
    from pyrogram.errors import FloodWait

    # 🔥 Reaction sync (best-effort): Pyrogram 2.0.106 has NO
    # @app.on_message_reaction() decorator — that convenience wrapper only
    # exists in newer forks/betas. We hook the raw MTProto update instead
    # (see the on_raw_update handler registered further down). Imported
    # defensively: if this frozen build's bundled TL schema doesn't have
    # the raw type either, reaction sync just disables itself below
    # instead of crashing the whole engine.
    try:
        from pyrogram.raw.types import UpdateBotMessageReaction
        REACTION_RAW_TYPE_AVAILABLE = True
    except ImportError:
        UpdateBotMessageReaction = None
        REACTION_RAW_TYPE_AVAILABLE = False

    async def _start_with_floodwait_guard(client):
        """
        Repeatedly attempts client.start(), automatically sleeping through
        any FloodWait Telegram throws — there is no way around a FloodWait,
        the only valid move is to wait it out. FloodWait retries are NOT
        counted against the outer reconnect-attempt budget, since it isn't
        a connection failure. Any other exception is re-raised so the outer
        loop in _run_pyrogram_engine can handle it as a normal socket error.
        """
        import config
        while True:
            try:
                await client.start()
                return
            except FloodWait as e:
                wait_time = int(getattr(e, "value", getattr(e, "x", 30)))
                config.FLOOD_WAIT_UNTIL = time.time() + wait_time
                resume_at = time.strftime('%H:%M:%S', time.localtime(config.FLOOD_WAIT_UNTIL))
                print(f"🐢 FloodWait from Telegram! Sleeping {wait_time}s (resuming ~{resume_at})...", flush=True)
                await asyncio.sleep(wait_time + 2)
                config.FLOOD_WAIT_UNTIL = 0
                print("🟢 FloodWait cooldown complete — retrying connection...", flush=True)
                continue

    API_ID = getattr(config, "API_ID", 0)
    API_HASH = getattr(config, "API_HASH", "")
    BOT_TOKEN = getattr(config, "TELEGRAM_BOT_TOKEN", "")
    OWNER_ID = getattr(config, "OWNER_ID", 0)

    if not BOT_TOKEN or not API_ID or not API_HASH:
        print("❌ TELEGRAM_BOT_TOKEN, API_ID ya API_HASH missing hai! HF Secrets check karein.", flush=True)
        return

    app = Client(
        "kamal_master_bot",
        api_id=int(API_ID),
        api_hash=str(API_HASH),
        bot_token=str(BOT_TOKEN),
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

    # group=-100 lagane se ye bot ka sabse pehla action ban jayega
    @app.on_message(
        filters.new_chat_members | filters.left_chat_member | filters.new_chat_title | filters.all,
        group=-100,
    )
    async def _on_service_msg(client: Client, message: Message):
        is_service = False

        # 1. Explicit join/leave/title-change filters (covers "joined",
        #    "left", "joined via invite link", and title-change service
        #    messages instantly)
        if message.new_chat_members or message.left_chat_member or message.new_chat_title:
            is_service = True
        # 2. Any other classic service messages (pinned, photo change, etc.)
        elif getattr(message, "service", None):
            is_service = True
            
        # 2. Naye Telegram Updates (Jinko Pyrogram nahi samajhta, wo empty aate hain)
        else:
            has_content = any([
                message.text, message.media, message.caption, 
                message.location, message.contact, message.poll, 
                message.sticker, message.game, message.dice
            ])
            # Agar message mein koi text/media nahi hai, matlab wo system message hai
            if not has_content:
                is_service = True

        if is_service:
            if hasattr(H, "delete_service_messages"):
                await H.delete_service_messages(client, message)
            # Aage ke handlers ko is message ko process karne se rok do
            raise StopPropagation

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
    # 4b. LIVE REACTION SYNCING (RAW UPDATES — no on_message_reaction in
    # Pyrogram 2.0.106, so we hook the raw MTProto update directly)
    # =====================================================================
    if REACTION_RAW_TYPE_AVAILABLE:
        @app.on_raw_update()
        async def _on_raw_update(client: Client, update, users, chats):
            if isinstance(update, UpdateBotMessageReaction) and hasattr(H, "handle_reaction"):
                try:
                    await H.handle_reaction(client, update)
                except Exception:
                    print("⚠️ Reaction sync error:", flush=True)
                    traceback.print_exc()
        print("🟢 Reaction sync ENABLED (raw updates).", flush=True)
    else:
        print("⚠️ Reaction sync DISABLED — this Pyrogram build's raw schema has no UpdateBotMessageReaction. Everything else (2-way text routing, edits, deletes) is unaffected.", flush=True)

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
            await _start_with_floodwait_guard(app)
            me = await app.get_me()
            print(f"✅ BOT LIVE! Authorized successfully as @{me.username} (ID: {me.id})!", flush=True)
            
            # 🔥 NATIVE MTPROTO PEER CACHE WARM-UP (no HTTP hacks)
            # NOTE: get_dialogs() is a USER-ACCOUNT-ONLY method — Telegram
            # rejects it for bots with BOT_METHOD_INVALID (bots have no
            # "dialog list" concept), so it's deliberately not used here.
            # get_chat() on the numeric ID is the correct bot-compatible
            # primitive: since this bot is already a member/admin of the
            # Support Group, Telegram resolves it correctly via
            # channels.GetChannels even with no prior local peer knowledge
            # — the only thing that was ever blocking this was pyrogram's
            # own MIN_CHANNEL_ID bound rejecting the ID locally before the
            # request could even be sent (see the patch in config.py).
            print("🔄 Warming up Support Group peer cache via native get_chat()...", flush=True)
            try:
                import config
                supp_id = getattr(config, "SUPPORT_GROUP_ID", 0)
                if supp_id:
                    for peer_attempt in range(1, 4):
                        try:
                            await app.get_chat(int(supp_id))
                            print("✅ Support Group peer cached successfully!", flush=True)
                            break
                        except Exception as peer_err:
                            print(f"⚠️ Support Group peer cache attempt {peer_attempt}/3 failed: {peer_err}", flush=True)
                            if peer_attempt < 3:
                                await asyncio.sleep(2)
            except Exception as diag_err:
                print(f"⚠️ Peer warm-up warning: {diag_err}", flush=True)
                
            break
        except FloodWait as e:
            # Belt-and-suspenders: covers FloodWait raised anywhere else in
            # this try block (e.g. get_me(), get_dialogs(), get_chat()),
            # not just from client.start() itself.
            import config
            wait_time = int(getattr(e, "value", getattr(e, "x", 30)))
            config.FLOOD_WAIT_UNTIL = time.time() + wait_time
            print(f"🐢 FloodWait caught during startup sequence! Sleeping {wait_time}s...", flush=True)
            await asyncio.sleep(wait_time + 2)
            config.FLOOD_WAIT_UNTIL = 0
            # Doesn't count against the 5-attempt budget — retry immediately.
            continue
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

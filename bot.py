import os
import threading
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, 
    ChatJoinRequestHandler, ChatMemberHandler, MessageReactionHandler, filters, ContextTypes
)
from telegram.error import Conflict

from config import TELEGRAM_BOT_TOKEN, LOG_CHANNEL_ID, load_data, logger
from handlers import *

try:
    from flask import Flask
    def _start_keepalive():
        port = int(os.environ.get("PORT", "7860"))
        app = Flask(__name__)
        @app.route('/')
        def index(): return "Bot Running - Premium Level 🚀", 200
        def run(): app.run(host="0.0.0.0", port=port, use_reloader=False)
        t = threading.Thread(target=run, daemon=True)
        t.start()
except ImportError:
    def _start_keepalive(): pass

# 👇 NAYA: Global Error Handler (Ye bot ko crash hone se bachayega) 👇
async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    # Agar Conflict error hai toh use ignore karo bina spam kiye
    if isinstance(context.error, Conflict):
        logger.warning("⚠️ Conflict Error: Purana aur naya bot ek sath chal raha hai. Ignoring...")
        return

    logger.error(msg="Exception while handling an update:", exc_info=context.error)
    try:
        if LOG_CHANNEL_ID:
            error_msg = f"⚠️ **CRITICAL ERROR** ⚠️\n\n`{context.error}`"
            await context.bot.send_message(chat_id=LOG_CHANNEL_ID, text=error_msg, parse_mode="Markdown")
    except Exception:
        pass


def main():
    _start_keepalive()
    load_data()
    
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    
    # --- SARE COMMANDS WAPAS REGISTER KIYE GAYE HAIN ---
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
        ("delcat", cmd_delcat), ("clear", cmd_clear), ("maintenance", cmd_maintenance)
    ]
    
    for cmd_name, func in commands:
        app.add_handler(CommandHandler(cmd_name, func))
        
    app.add_handler(MessageHandler(filters.Regex(r"^/id(@\w+)?$") & filters.ChatType.CHANNEL, cmd_id))
    
    # --- CALLBACKS & EVENTS ---
    app.add_handler(CallbackQueryHandler(general_callback))
    app.add_handler(ChatJoinRequestHandler(handle_join_request))
    
    app.add_handler(ChatMemberHandler(on_chat_member_update, ChatMemberHandler.CHAT_MEMBER))
    app.add_handler(ChatMemberHandler(track_chats, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS | filters.StatusUpdate.LEFT_CHAT_MEMBER, delete_service_messages))
    
    app.add_handler(MessageReactionHandler(handle_reaction))
    app.add_handler(MessageHandler(filters.UpdateType.EDITED_MESSAGE, handle_edit))
    
    # Message Handler (Wizards ke liye)
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, main_message_handler))
    
    # 👇 NAYA: Error handler ko bot se connect kiya 👇
    app.add_error_handler(global_error_handler)
    
    if app.job_queue: 
        app.job_queue.run_repeating(check_demos, interval=60, first=10)
        app.job_queue.run_repeating(background_sync, interval=600, first=30)
        app.job_queue.run_repeating(auto_backup_db, interval=86400, first=60)
    
    logger.info("✅ Bot v38.0 Started Successfully!")
    
    # 👇 FIX: drop_pending_updates=True lagaya taaki deploy ke waqt purane pending messages conflict na karein 👇
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()

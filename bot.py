import os
import threading
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, 
    ChatJoinRequestHandler, ChatMemberHandler, filters
)

# Core imports
from config import TELEGRAM_BOT_TOKEN, load_data, logger
from handlers import *

# --- FLASK KEEPALIVE SERVER ---
try:
    from flask import Flask
    def _start_keepalive():
        port = int(os.environ.get("PORT", "7860"))
        app = Flask(__name__)
        @app.route('/')
        def index(): return "Bot Running - v36.0 (Modular & Button Dashboard UI)", 200
        def run(): app.run(host="0.0.0.0", port=port, use_reloader=False)
        t = threading.Thread(target=run, daemon=True)
        t.start()
except ImportError:
    def _start_keepalive(): pass

def main():
    _start_keepalive()
    load_data()
    
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Base command routing (Fallback, kyunki ab sab buttons pe hai)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler("maintenance", cmd_maintenance))
    app.add_handler(MessageHandler(filters.Regex(r"^/id(@\w+)?$") & filters.ChatType.CHANNEL, cmd_id))
    
    # Callback UI & Events
    app.add_handler(CallbackQueryHandler(general_callback))
    app.add_handler(ChatMemberHandler(on_chat_member_update, ChatMemberHandler.CHAT_MEMBER))
    app.add_handler(ChatMemberHandler(track_chats, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS | filters.StatusUpdate.LEFT_CHAT_MEMBER, delete_service_messages))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, main_message_handler))
    
    # Background Jobs
    if app.job_queue: 
        app.job_queue.run_repeating(check_demos, interval=60, first=10)
        app.job_queue.run_repeating(background_sync, interval=600, first=30)
        app.job_queue.run_repeating(auto_backup_db, interval=86400, first=60)
    
    logger.info("✅ Bot v36.0 (Modular + Ultimate Dashboard) Started Successfully!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()

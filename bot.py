import asyncio
import importlib
import os
import threading
import time
import traceback
from flask import Flask, jsonify

print("🟢 BOOT[1/4]: Starting Flask Health Check Server...", flush=True)

# 1. FLASK WEB SERVER (Keeps Hugging Face Space 'Running 🟢' 24/7)
app = Flask(__name__)


@app.route("/")
def home():
  return "🟢 Kamal Master Bot Engine is Live & Running Smoothly!"


@app.route("/health")
def health():
  return jsonify({"status": "healthy", "engine": "Pyrogram MTProto"})


def run_flask():
  port = int(os.environ.get("PORT", 7860))
  print(f"🟢 BOOT[2/4]: Binding Flask Web Dashboard on port {port}...", flush=True)
  # Flask synchronously chalta hai, HF ko turant port mil jayega!
  app.run(host="0.0.0.0", port=port, use_reloader=False)


# 2. PYROGRAM BOT BACKGROUND WORKER
def run_bot_thread():
  print(
      "🟢 BOOT[3/4]: Initializing Pyrogram Engine in Background Thread...",
      flush=True,
  )
  loop = asyncio.new_event_loop()
  asyncio.set_event_loop(loop)

  try:
    import config
    import handlers as H
    from pyrogram import Client, filters
    from pyrogram.types import CallbackQuery, Message

    # Load Data synchronously
    try:
      config.load_data()
      print("🟢 BOOT[4/4]: Database matrices loaded successfully!", flush=True)
    except Exception as e:
      print(f"⚠️ Database load warning: {e}", flush=True)

    API_ID = getattr(config, "API_ID", 0)
    API_HASH = getattr(config, "API_HASH", "")
    BOT_TOKEN = getattr(config, "TELEGRAM_BOT_TOKEN", "")
    OWNER_ID = getattr(config, "OWNER_ID", 0)

    if not BOT_TOKEN or not API_ID or not API_HASH:
      print(
          "❌ TELEGRAM_BOT_TOKEN, API_ID ya API_HASH missing hai! HF Secrets"
          " check karein.",
          flush=True,
      )
      return

    # 🔥 PYROGRAM MTPROTO CLIENT (Direct TCP Connection - Zero Proxy Required!)
    bot = Client(
        "kamal_master_bot",
        api_id=int(API_ID),
        api_hash=str(API_HASH),
        bot_token=str(BOT_TOKEN),
        in_memory=True,  # Disk I/O bachane ke liye memory me session
        workers=50,  # 50 Parallel workers for turbo speed!
    )
    config.bot_app = bot

    # --- ROUTING HANDLERS ---
    @bot.on_message(filters.command("start") & filters.private)
    async def _on_start(client: Client, message: Message):
      await H.cmd_start(client, message)

    @bot.on_message(filters.command("ping"))
    async def _on_ping(client: Client, message: Message):
      t = time.time()
      m = await message.reply_text("🏓 **Pinging MTProto Sockets...**")
      await m.edit_text(
          f"🏓 **Pong!**\n⚡ **Speed:** `{round((time.time() - t) * 1000)}ms`\n🛡️"
          " **Protocol:** `Pyrogram MTProto`"
      )

    @bot.on_message(filters.command("stats") & filters.user(OWNER_ID))
    async def _on_stats(client: Client, message: Message):
      await H.cmd_stats(client, message)

    @bot.on_callback_query()
    async def _on_callback(client: Client, query: CallbackQuery):
      await H.general_callback(client, query)

    @bot.on_message(filters.all & ~filters.command(""))
    async def _on_all_msg(client: Client, message: Message):
      if message.text and message.text.startswith("/"):
        return
      await H.main_message_handler(client, message)

    print(
        "🚀 BOT ENGINE OPERATIONAL! Connecting to Telegram MTProto Sockets...",
        flush=True,
      )
    bot.start()
    me = loop.run_until_complete(bot.get_me())
    print(
        f"✅ BOT LIVE! Authorized successfully as @{me.username} (ID: {me.id})!",
        flush=True,
    )

    try:
      if OWNER_ID:
        loop.run_until_complete(
            bot.send_message(
                int(OWNER_ID),
                "🟢 **BOT IS LIVE ON HUGGING FACE!**\n\n⚡ **Status:** `Running"
                " Smoothly (Flask + Pyrogram Hybrid)`\n🛡️ **Engine:**"
                " `Pyrogram MTProto TCP Sockets`\n💡 *Send /ping to test"
                " speed!*",
            )
        )
    except Exception as e:
      print(f"⚠️ Owner alert failed: {e}", flush=True)

    # Infinite idle loop to keep bot running
    while True:
      time.sleep(10)

  except Exception as e:
    print(
        "=========================================================", flush=True
    )
    print("🚨 BOT THREAD CRASHED! READ THE EXACT ERROR BELOW:", flush=True)
    print(
        "=========================================================", flush=True
    )
    traceback.print_exc()
    print(
        "=========================================================", flush=True
    )
    print(
        "💡 Note: Flask Server abhi bhi chalu hai taaki Space 'Running' rahe"
        " aur tum logs padh sako!",
        flush=True,
    )
    while True:
      time.sleep(60)  # Keeps thread alive so Docker doesn't restart


if __name__ == "__main__":
  # Step 1: Start Pyrogram Bot in a Background Thread
  bot_worker = threading.Thread(target=run_bot_thread, daemon=True)
  bot_worker.start()

  # Step 2: Start Flask Web Server on Main Thread (Instantly opens Port 7860)
  run_flask()

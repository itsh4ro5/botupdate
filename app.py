import os
from quart import Quart, jsonify

app = Quart(__name__)

# Lightweight health check endpoints for Hugging Face Spaces & deployment platforms
@app.route('/health')
@app.route('/')
async def health_check():
    return jsonify({
        "status": "healthy",
        "service": "Telegram Bot",
        "port": int(os.environ.get("PORT", 7860))
    }), 200

# Legacy compatibility stub
db_fs = None

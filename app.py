import os
import threading
import time
from flask import Flask, jsonify, render_template
from config import DB, logger

app = Flask(__name__)

@app.route('/')
def index():
    # Flask automatically is file ko 'templates' folder ke andar dhoondhega
    return render_template('dashboard.html')

@app.route('/api/user/<int:user_id>')
def get_user_data(user_id):
    user_data = DB["USER_DATA"].get(str(user_id)) or DB["USER_DATA"].get(user_id)
    if not user_data:
        return jsonify({"error": "User not found"}), 404
    
    response = {"free_batches": [], "paid_batches": [], "demos": []}
    all_chats_dict = DB.get("ALL_CHATS", {})
    now = time.time()

    # Demos aur unka live status check karein
    if "demos" in user_data:
        for bid, d_data in user_data["demos"].items():
            bname = all_chats_dict.get(bid) or all_chats_dict.get(int(bid)) or f"Batch {bid}"
            expiry_time = d_data["expiry"] if isinstance(d_data, dict) else float(d_data)
            is_expired = now > expiry_time
            expiry_str = time.strftime('%d %b %Y, %I:%M %p', time.localtime(expiry_time))
            
            response["demos"].append({
                "id": bid, "name": bname, "is_expired": is_expired, "expiry_date": expiry_str
            })

    return jsonify(response), 200

@app.route('/health')
def health():
    return "OK", 200

def start_background_server():
    print("⏳ Starting Separate Flask App Server...", flush=True)
    port = int(os.environ.get("PORT", "7860"))
    
    def run():
        app.run(host="0.0.0.0", port=port, use_reloader=False)
        
    t = threading.Thread(target=run, daemon=True)
    t.start()
    print("✅ Separate Flask App Server Started Successfully!", flush=True)

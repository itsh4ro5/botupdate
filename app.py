import os
import threading
import time
import urllib.request
import json
import io
from flask import Flask, jsonify, render_template, send_file
from config import DB, OWNER_ID, is_admin, save_data_sync

app = Flask(__name__)

AVATAR_CACHE = {}  
LAST_SYNC = {}    

# 👇 FIX 1: Thread-Safe API Request (No Deadlock!) 👇
def sync_user_batches_background(user_id):
    now = time.time()
    # 3 Minute ka cooldown taaki Telegram API block na kare
    if now - LAST_SYNC.get(user_id, 0) < 180: 
        return
    LAST_SYNC[user_id] = now

    def run_sync():
        try:
            from config import TELEGRAM_BOT_TOKEN
            user_data = DB["USER_DATA"].get(str(user_id)) or DB["USER_DATA"].get(user_id)
            if not user_data: return
            
            joined = []
            all_batches = {**DB.get("FREE_CHANNELS", {}), **DB.get("PAID_CHANNELS", {})}
            
            for bid in all_batches.keys():
                try:
                    # 100% Thread-Safe synchronous request, Bot ko freeze nahi karega
                    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getChatMember?chat_id={bid}&user_id={user_id}"
                    req = urllib.request.Request(url)
                    with urllib.request.urlopen(req, timeout=5) as response:
                        res_data = json.loads(response.read().decode())
                        if res_data.get("ok") and res_data["result"]["status"] in ['member', 'administrator', 'creator', 'restricted']:
                            joined.append(int(bid))
                except Exception:
                    pass
            
            user_data["joined_batches"] = joined
            save_data_sync() # Safely save in background
            print(f"🔄 [Sync Completed] Cache updated for User: {user_id}", flush=True)
        except Exception as e:
            print(f"❌ Sync Thread Failed: {e}", flush=True)

    threading.Thread(target=run_sync, daemon=True).start()


@app.route('/')
def index():
    return render_template('dashboard.html')

# 👇 NAYA ROUTE (Aap yahan se apne naye routes banana shuru kar sakte hain) 👇
@app.route('/api/stats')
def api_stats():
    return jsonify({
        "status": "Dashboard API is running 🚀", 
        "total_registered_users": len(DB.get("USER_DATA", {})),
        "total_active_batches": len(DB.get("ALL_CHATS", {}))
    })

@app.route('/api/user/avatar/<int:user_id>')
def get_user_avatar(user_id):
    now = time.time()
    if user_id in AVATAR_CACHE and now - AVATAR_CACHE[user_id]["time"] < 3600:
        return send_file(io.BytesIO(AVATAR_CACHE[user_id]["bytes"]), mimetype='image/jpeg')

    from config import TELEGRAM_BOT_TOKEN
    if not TELEGRAM_BOT_TOKEN: return "Token missing", 400
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUserProfilePhotos?user_id={user_id}&limit=1"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as response:
            res_data = json.loads(response.read().decode())
        
        if res_data.get("ok") and res_data["result"]["total_count"] > 0:
            photos = res_data["result"]["photos"][0]
            file_id = photos[1]["file_id"] if len(photos) > 1 else photos[0]["file_id"]
            
            file_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getFile?file_id={file_id}"
            with urllib.request.urlopen(file_url, timeout=5) as file_res:
                file_data = json.loads(file_res.read().decode())
            
            if file_data.get("ok"):
                file_path = file_data["result"]["file_path"]
                img_url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}"
                with urllib.request.urlopen(img_url, timeout=5) as img_res:
                    img_bytes = img_res.read()
                
                AVATAR_CACHE[user_id] = {"bytes": img_bytes, "time": now}
                return send_file(io.BytesIO(img_bytes), mimetype='image/jpeg')
    except Exception:
        pass
    return "No avatar", 404

@app.route('/api/user/<int:user_id>')
def get_user_data(user_id):
    # Data fetch karne ki process background me chalegi, page instant load hoga!
    sync_user_batches_background(user_id)

    user_data = DB["USER_DATA"].get(str(user_id)) or DB["USER_DATA"].get(user_id)
    is_user_owner = (str(user_id) == str(OWNER_ID)) or (user_id == OWNER_ID)
    is_user_admin = is_admin(user_id)
    
    response = {
        "is_owner": is_user_owner,
        "is_admin": is_user_admin,
        "user_info": {
            "id": user_id,
            "name": user_data.get("name", "Premium Member") if user_data else "Guest User",
            "username": user_data.get("username", "N/A") if user_data else "N/A"
        },
        "my_batches": [],
        "explore_hub": [],
        "demos": []
    }
    
    all_chats_dict = DB.get("ALL_CHATS", {})
    now = time.time()

    if user_data:
        joined_list = user_data.get("joined_batches", [])
        demo_keys = list(user_data.get("demos", {}).keys())

        # 1. Active Demos Processing
        if "demos" in user_data:
            for bid, d_data in user_data["demos"].items():
                bname = all_chats_dict.get(bid) or all_chats_dict.get(int(bid)) or f"Batch {bid}"
                expiry_time = d_data["expiry"] if isinstance(d_data, dict) else float(d_data)
                is_expired = now > expiry_time
                expiry_str = time.strftime('%d %b %Y, %I:%M %p', time.localtime(expiry_time))
                time_left = max(0, int(expiry_time - now))
                
                response["demos"].append({
                    "id": bid, "name": bname, "is_expired": is_expired, "expiry_date": expiry_str, "time_left_hours": round(time_left / 3600, 1)
                })

        # 2. Free Batches
        for bid, name in DB.get("FREE_CHANNELS", {}).items():
            bid_int = int(bid)
            is_joined = (bid_int in joined_list) or (str(bid) in joined_list)
            batch_payload = {"id": bid, "name": name, "type": "Free Channel"}
            
            if is_joined:
                batch_payload["status"] = "Joined ✅"
                response["my_batches"].append(batch_payload)
            else:
                batch_payload["status"] = "Join Now 📂"
                response["explore_hub"].append(batch_payload)
                
        # 3. Paid Batches (FIX 2: Expired demos handle ho gaye)
        for bid, name in DB.get("PAID_CHANNELS", {}).items():
            bid_str = str(bid)
            is_joined = (int(bid) in joined_list) or (bid_str in joined_list)
            has_demo = bid_str in demo_keys
            
            batch_payload = {"id": bid, "name": name, "type": "Premium Core"}
            
            if is_joined:
                batch_payload["status"] = "Lifetime Access 💎"
                response["my_batches"].append(batch_payload)
            elif has_demo:
                expiry_time = user_data["demos"][bid_str]["expiry"] if isinstance(user_data["demos"][bid_str], dict) else float(user_data["demos"][bid_str])
                if now > expiry_time:
                    batch_payload["status"] = "Expired ❌"
                    response["explore_hub"].append(batch_payload) # Wapas Explore me bhej diya
                else:
                    batch_payload["status"] = "Demo Run ⏳"
                    response["my_batches"].append(batch_payload)
            else:
                batch_payload["status"] = "Buy Access 🔐"
                response["explore_hub"].append(batch_payload)

    if is_user_owner or is_user_admin:
        response["system_stats"] = {
            "total_users": len(DB.get("USER_DATA", {})),
            "blocked_users": len(DB.get("BLOCKED_USERS", [])),
            "free_batches_count": len(DB.get("FREE_CHANNELS", {})),
            "paid_batches_count": len(DB.get("PAID_CHANNELS", {})),
            "maintenance_mode": DB.get("MAINTENANCE_MODE", False),
            "lockdown_mode": not DB.get("NEW_USERS_ALLOWED", True)
        }

    return jsonify(response), 200

def start_background_server():
    print("⏳ Starting Dashboard API Server...", flush=True)
    port = int(os.environ.get("PORT", "7860"))
    def run():
        app.run(host="0.0.0.0", port=port, use_reloader=False)
    t = threading.Thread(target=run, daemon=True)
    t.start()
    print("✅ Dashboard API Server Running!", flush=True)

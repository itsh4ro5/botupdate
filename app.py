import os
import threading
import time
import urllib.request
import json
import io
from flask import Flask, jsonify, render_template, send_file
from config import DB, OWNER_ID, is_admin, logger

app = Flask(__name__)

@app.route('/')
def index():
    return render_template('dashboard.html')

# 👇 NAYA SECURE ROUTE: Telegram se DP fetch karne ke liye 👇
@app.route('/api/user/avatar/<int:user_id>')
def get_user_avatar(user_id):
    from config import TELEGRAM_BOT_TOKEN
    if not TELEGRAM_BOT_TOKEN:
        return "Token missing", 400
    try:
        # 1. User ki profile photos ki list mangwate hain
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUserProfilePhotos?user_id={user_id}&limit=1"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req) as response:
            res_data = json.loads(response.read().decode())
        
        if res_data.get("ok") and res_data["result"]["total_count"] > 0:
            photos = res_data["result"]["photos"][0]
            # Medium size photo select karte hain bandwidth bachane ke liye
            file_id = photos[1]["file_id"] if len(photos) > 1 else photos[0]["file_id"]
            
            # 2. File ka internal path nikalte hain
            file_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getFile?file_id={file_id}"
            with urllib.request.urlopen(file_url) as file_res:
                file_data = json.loads(file_res.read().decode())
            
            if file_data.get("ok"):
                file_path = file_data["result"]["file_path"]
                # 3. Actual image bytes download karke Flask se stream karte hain
                img_url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}"
                with urllib.request.urlopen(img_url) as img_res:
                    img_bytes = img_res.read()
                
                return send_file(io.BytesIO(img_bytes), mimetype='image/jpeg')
    except Exception as e:
        print(f"⚠️ Error fetching avatar: {e}", flush=True)
    
    # Agar DP nahi set hoto 404 return karega jisse HTML fallback trigger ho sake
    return "No avatar", 404

@app.route('/api/user/<int:user_id>')
def get_user_data(user_id):
    user_data = DB["USER_DATA"].get(str(user_id)) or DB["USER_DATA"].get(user_id)
    is_user_owner = (str(user_id) == str(OWNER_ID)) or (user_id == OWNER_ID)
    is_user_admin = is_admin(user_id)
    
    response = {
        "is_owner": is_user_owner,
        "is_admin": is_user_admin,
        "user_info": {
            "id": user_id,
            "name": user_data.get("name", "Premium Member") if user_data else "Guest User",
            "username": user_data.get("username", "N/A") if user_data else "N/A",
            "tnc_accepted": user_data.get("tnc_accepted", False) if user_data else False
        },
        "free_batches": [],
        "paid_batches": [],
        "demos": []
    }
    
    all_chats_dict = DB.get("ALL_CHATS", {})
    now = time.time()

    if user_data:
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

        for bid, name in DB.get("FREE_CHANNELS", {}).items():
            response["free_batches"].append({"id": bid, "name": name, "status": "Joined ✅"})
            
        for bid, name in DB.get("PAID_CHANNELS", {}).items():
            has_demo = str(bid) in user_data.get("demos", {})
            status = "Demo Run ⏳" if has_demo else "Lifetime Access 💎"
            response["paid_batches"].append({"id": bid, "name": name, "status": status})

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

def health():
    return "OK", 200

def start_background_server():
    print("⏳ Starting Premium Multi-Role Flask Server...", flush=True)
    port = int(os.environ.get("PORT", "7860"))
    def run():
        app.run(host="0.0.0.0", port=port, use_reloader=False)
    t = threading.Thread(target=run, daemon=True)
    t.start()
    print("✅ Premium Multi-Role Flask Server Running Perfectly!", flush=True)

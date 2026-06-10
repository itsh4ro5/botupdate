import os
import threading
import time
from flask import Flask, jsonify, render_template
from config import DB, OWNER_ID, is_admin, logger

app = Flask(__name__)

@app.route('/')
def index():
    # Flask automatically templates folder se dashboard.html render karega
    return render_template('dashboard.html')

@app.route('/api/user/<int:user_id>')
def get_user_data(user_id):
    # MongoDB/Memory se user data nikalna
    user_data = DB["USER_DATA"].get(str(user_id)) or DB["USER_DATA"].get(user_id)
    
    # Owner aur Admin authorization check
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

    # --- USER DATA PARSING ---
    if user_data:
        # Active aur Expired Demos filter karna
        if "demos" in user_data:
            for bid, d_data in user_data["demos"].items():
                bname = all_chats_dict.get(bid) or all_chats_dict.get(int(bid)) or f"Batch {bid}"
                expiry_time = d_data["expiry"] if isinstance(d_data, dict) else float(d_data)
                is_expired = now > expiry_time
                expiry_str = time.strftime('%d %b %Y, %I:%M %p', time.localtime(expiry_time))
                
                # Time left calculation (in seconds)
                time_left = max(0, int(expiry_time - now))
                
                response["demos"].append({
                    "id": bid,
                    "name": bname,
                    "is_expired": is_expired,
                    "expiry_date": expiry_str,
                    "time_left_hours": round(time_left / 3600, 1)
                })

        # Available Free aur Paid Batches map karna
        for bid, name in DB.get("FREE_CHANNELS", {}).items():
            response["free_batches"].append({"id": bid, "name": name, "status": "Joined ✅"})
            
        for bid, name in DB.get("PAID_CHANNELS", {}).items():
            # Agar user ke pass is batch ka demo chal raha hai
            has_demo = str(bid) in user_data.get("demos", {})
            status = "Demo Run ⏳" if has_demo else "Lifetime Access 💎"
            response["paid_batches"].append({"id": bid, "name": name, "status": status})

    # --- OWNER/ADMIN ANALYTICS DUMP ---
    if is_user_owner or is_user_admin:
        response["system_stats"] = {
            "total_users": len(DB.get("USER_DATA", {})),
            "blocked_users": len(DB.get("BLOCKED_USERS", [])),
            "free_batches_count": len(DB.get("FREE_CHANNELS", {})),
            "paid_batches_count": len(DB.get("PAID_CHANNELS", {})),
            "maintenance_mode": DB.get("MAINTENANCE_MODE", False),
            "lockdown_mode": not DB.get("NEW_USERS_ALLOWED", True),
            "free_locked": DB.get("FREE_LOCKED", False),
            "paid_locked": DB.get("PAID_LOCKED", False)
        }

    return jsonify(response), 200

@app.route('/health')
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

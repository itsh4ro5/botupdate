import time
import io
import asyncio
from quart import Quart, jsonify, render_template, send_file
import config
from config import DB, OWNER_ID, is_admin

app = Quart(__name__)
AVATAR_CACHE = {}

@app.route('/')
async def index():
    return await render_template('dashboard.html')

# 👇 NAYA ROUTE: Explore Batches ka dedicated page render karne ke liye 👇
@app.route('/explore')
async def explore_page():
    return await render_template('explore.html')

@app.route('/api/user/avatar/<int:user_id>')
async def get_user_avatar(user_id):
    now = time.time()
    if user_id in AVATAR_CACHE and now - AVATAR_CACHE[user_id]["time"] < 3600:
        return await send_file(io.BytesIO(AVATAR_CACHE[user_id]["bytes"]), mimetype='image/jpeg')

    if not hasattr(config, 'bot_app') or not config.bot_app:
        return "Bot not ready", 503

    try:
        bot = config.bot_app.bot
        photos = await bot.get_user_profile_photos(user_id, limit=1)
        
        if photos.total_count > 0:
            photo = photos.photos[0][-1]
            file = await bot.get_file(photo.file_id)
            
            out = bytearray()
            await file.download_to_memory(out)
            img_bytes = bytes(out)
            
            AVATAR_CACHE[user_id] = {"bytes": img_bytes, "time": now}
            return await send_file(io.BytesIO(img_bytes), mimetype='image/jpeg')
    except Exception:
        pass
        
    return "No avatar", 404

@app.route('/api/user/<int:user_id>')
async def get_user_data(user_id):
    user_data = DB["USER_DATA"].get(str(user_id)) or DB["USER_DATA"].get(user_id) or {}
    is_user_owner = (str(user_id) == str(OWNER_ID)) or (user_id == OWNER_ID)
    is_user_admin = is_admin(user_id)
    
    response = {
        "is_owner": is_user_owner,
        "is_admin": is_user_admin,
        "user_info": {
            "id": user_id,
            "name": user_data.get("name", "Premium Member"),
            "username": user_data.get("username", "N/A")
        },
        "my_batches": [],
        "demos": []
    }
    
    all_chats_dict = DB.get("ALL_CHATS", {})
    batch_cats = DB.get("BATCH_CATEGORIES", {})
    now = time.time()
    joined_list = []
    
    if hasattr(config, 'bot_app') and config.bot_app:
        bot = config.bot_app.bot
        async def check_membership(bid):
            try:
                m = await bot.get_chat_member(int(bid), user_id)
                if m.status in ['member', 'administrator', 'creator', 'restricted']:
                    return int(bid)
            except Exception: pass
            return None

        tasks = [check_membership(bid) for bid in all_chats_dict.keys()]
        results = await asyncio.gather(*tasks)
        joined_list = [r for r in results if r is not None]

        if str(user_id) in DB["USER_DATA"]:
            DB["USER_DATA"][str(user_id)]["joined_batches"] = joined_list
            asyncio.create_task(config.save_data_async())

    demo_keys = list(user_data.get("demos", {}).keys()) if user_data else []

    if "demos" in user_data:
        for bid, d_data in user_data["demos"].items():
            bname = all_chats_dict.get(bid) or all_chats_dict.get(int(bid)) or f"Batch {bid}"
            expiry_time = d_data["expiry"] if isinstance(d_data, dict) else float(d_data)
            is_expired = now > expiry_time
            time_left = max(0, int(expiry_time - now))
            
            response["demos"].append({
                "id": bid, "name": bname, "is_expired": is_expired, 
                "expiry_date": time.strftime('%d %b %Y, %I:%M %p', time.localtime(expiry_time)), 
                "time_left_hours": round(time_left / 3600, 1)
            })

    # Subscribed Free Batches
    for bid, name in DB.get("FREE_CHANNELS", {}).items():
        is_joined = int(bid) in joined_list or str(bid) in joined_list
        if is_joined:
            response["my_batches"].append({"id": bid, "name": name, "type": "Free Channel", "status": "Joined ✅", "category": batch_cats.get(str(bid), "Other Batches")})
            
    # Subscribed Paid Batches
    for bid, name in DB.get("PAID_CHANNELS", {}).items():
        bid_str = str(bid)
        is_joined = int(bid) in joined_list or bid_str in joined_list
        has_demo = bid_str in demo_keys
        
        if is_joined or has_demo:
            status = "Lifetime Access 💎" if is_joined else "Demo Run ⏳"
            if has_demo:
                exp = user_data["demos"][bid_str]["expiry"] if isinstance(user_data["demos"][bid_str], dict) else float(user_data["demos"][bid_str])
                if now > exp: continue
            response["my_batches"].append({"id": bid, "name": name, "type": "Premium Core", "status": status, "category": batch_cats.get(bid_str, "Other Batches")})

    if is_user_owner or is_user_admin:
        response["system_stats"] = {
            "total_users": len(DB.get("USER_DATA", {})),
            "blocked_users": len(DB.get("BLOCKED_USERS", [])),
            "maintenance_mode": DB.get("MAINTENANCE_MODE", False),
            "lockdown_mode": not DB.get("NEW_USERS_ALLOWED", True)
        }

    return jsonify(response)

# 👇 NAYA API ENDPOINT: Explore Page ke liye Category wise Free/Paid distribute karne ke liye 👇
@app.route('/api/explore/<int:user_id>')
async def api_explore_data(user_id):
    user_data = DB["USER_DATA"].get(str(user_id)) or DB["USER_DATA"].get(user_id) or {}
    joined_list = user_data.get("joined_batches", [])
    demo_keys = list(user_data.get("demos", {}).keys())
    now = time.time()

    categories = DB.get("CATEGORIES", [])
    free_channels = DB.get("FREE_CHANNELS", {})
    paid_channels = DB.get("PAID_CHANNELS", {})
    batch_cats = DB.get("BATCH_CATEGORIES", {})

    # Structure initialization
    explore_data = {cat: {"free": [], "paid": []} for cat in categories}
    if "Other Batches" not in explore_data:
        explore_data["Other Batches"] = {"free": [], "paid": []}

    # Free Sorting
    for bid, name in free_channels.items():
        cat = batch_cats.get(str(bid), "Other Batches")
        if cat not in explore_data: explore_data[cat] = {"free": [], "paid": []}
        is_joined = int(bid) in joined_list or str(bid) in joined_list
        status = "Joined ✅" if is_joined else "Join Now 📂"
        explore_data[cat]["free"].append({"id": bid, "name": name, "status": status})

    # Paid Sorting
    for bid, name in paid_channels.items():
        bid_str = str(bid)
        cat = batch_cats.get(bid_str, "Other Batches")
        if cat not in explore_data: explore_data[cat] = {"free": [], "paid": []}
        is_joined = int(bid) in joined_list or bid_str in joined_list
        has_demo = bid_str in demo_keys
        
        status = "Lifetime Access 💎" if is_joined else ("Demo Run ⏳" if has_demo else "Buy Access 🔐")
        if has_demo:
            exp = user_data["demos"][bid_str]["expiry"] if isinstance(user_data["demos"][bid_str], dict) else float(user_data["demos"][bid_str])
            if now > exp: status = "Expired ❌"

        explore_data[cat]["paid"].append({"id": bid, "name": name, "status": status})

    return jsonify({
        "categories": categories,
        "explore_data": explore_data
    })

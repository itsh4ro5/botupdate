import time
import io
import asyncio
import datetime
import httpx
import json
import os
from quart import Quart, jsonify, render_template, send_file, request
import config
from config import DB, OWNER_ID, is_admin, get_membership_cached
from pyrogram.enums import ChatMemberStatus

TESTBOOK_API_URL = "https://itsh4r01-live-stream-engine.hf.space"
app = Quart(__name__)

AVATAR_CACHE = {}
AVATAR_CACHE_MAX_ENTRIES = 500

@app.route('/health')
async def health():
    is_active, remaining = config.get_flood_wait_status()
    return jsonify({
        "status": "OK",
        "flood_wait_active": is_active,
        "flood_wait_seconds": remaining
    }), 200

@app.route('/')
async def index():
    try: return await render_template('dashboard.html')
    except Exception as e: return f"Error: {e}", 200

@app.route('/explore')
async def explore_page():
    try: return await render_template('explore.html')
    except Exception as e: return f"Error: {e}", 200

@app.route('/admin_panel')
async def admin_page():
    try: return await render_template('admin.html')
    except Exception as e: return f"Error: {e}", 200

@app.route('/owner_panel')
async def owner_page():
    try: return await render_template('owner.html')
    except Exception as e: return f"Error: {e}", 200

@app.route('/api/user/avatar/<int:user_id>')
async def get_user_avatar(user_id):
    now = time.time()
    if user_id in AVATAR_CACHE and now - AVATAR_CACHE[user_id]["time"] < 3600:
        return await send_file(io.BytesIO(AVATAR_CACHE[user_id]["bytes"]), mimetype='image/jpeg')
    if not hasattr(config, 'bot_app') or not config.bot_app:
        return "Bot not ready", 503
    try:
        bot = config.bot_app
        photo = None
        async for p in bot.get_chat_photos(user_id, limit=1):
            photo = p
            break
        if photo:
            file_obj = await bot.download_media(photo.file_id, in_memory=True)
            img_bytes = file_obj.getvalue() if hasattr(file_obj, "getvalue") else file_obj
            if img_bytes:
                if len(AVATAR_CACHE) >= AVATAR_CACHE_MAX_ENTRIES:
                    oldest_uid = min(AVATAR_CACHE, key=lambda k: AVATAR_CACHE[k]["time"])
                    del AVATAR_CACHE[oldest_uid]
                AVATAR_CACHE[user_id] = {"bytes": img_bytes, "time": now}
                return await send_file(io.BytesIO(img_bytes), mimetype='image/jpeg')
    except Exception:
        pass
    return "No avatar", 404

@app.route('/api/user/<int:user_id>')
async def get_user_data(user_id):
    user_key = str(user_id) if str(user_id) in DB["USER_DATA"] else (user_id if user_id in DB["USER_DATA"] else None)
    user_data = DB["USER_DATA"].get(user_key) if user_key else {}
    
    is_user_owner = (str(user_id) == str(OWNER_ID)) or (user_id == OWNER_ID)
    is_user_admin = is_admin(user_id)
    flood_active, flood_seconds = config.get_flood_wait_status()
    
    current_streak = user_data.get("current_streak", 0)
    if user_key and user_data:
        today_str = datetime.datetime.now().strftime('%Y-%m-%d')
        last_active = user_data.get('last_active_date', '')
        if last_active != today_str:
            if last_active:
                last_date = datetime.datetime.strptime(last_active, '%Y-%m-%d')
                delta = (datetime.datetime.now() - last_date).days
                if delta == 1: current_streak += 1
                else: current_streak = 1
            else: current_streak = 1
            
            DB["USER_DATA"][user_key]['last_active_date'] = today_str
            DB["USER_DATA"][user_key]['current_streak'] = current_streak
            asyncio.create_task(config.save_data_async())
            
    response = {
        "is_owner": is_user_owner,
        "is_admin": is_user_admin,
        "flood_wait_active": flood_active,
        "flood_wait_seconds": flood_seconds,
        "user_info": {
            "id": user_id,
            "name": user_data.get("name", "Premium Member"),
            "username": user_data.get("username", "N/A"),
            "streak": current_streak
        },
        "my_batches": [],
        "demos": []
    }
    
    all_chats_dict = DB.get("ALL_CHATS", {})
    batch_cats = DB.get("BATCH_CATEGORIES", {})
    now = time.time()
    joined_list = []
    
    if hasattr(config, 'bot_app') and config.bot_app:
        bot = config.bot_app
        async def check_membership(bid):
            is_member = await get_membership_cached(bot, bid, user_id)
            return int(bid) if is_member else None
        tasks = [check_membership(bid) for bid in all_chats_dict.keys()]
        results = await asyncio.gather(*tasks)
        joined_list = [r for r in results if r is not None]
        
        if user_key:
            DB["USER_DATA"][user_key]["joined_batches"] = joined_list
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

    for bid, name in DB.get("FREE_CHANNELS", {}).items():
        if int(bid) in joined_list or str(bid) in joined_list:
            response["my_batches"].append({"id": bid, "name": name, "type": "Free Channel", "status": "Joined ✔️", "category": batch_cats.get(str(bid), "Other Batches")})
            
    for bid, name in DB.get("PAID_CHANNELS", {}).items():
        bid_str = str(bid)
        is_joined = int(bid) in joined_list or bid_str in joined_list
        has_demo = bid_str in demo_keys
        if is_joined or has_demo:
            status = "Lifetime Access 👑" if is_joined else "Demo Run ⏳"
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

@app.route('/api/owner/users/<int:req_user_id>')
async def api_owner_users(req_user_id):
    if str(req_user_id) != str(OWNER_ID) and req_user_id != OWNER_ID:
        return jsonify({"error": "Unauthorized. Owner Access Only."}), 403
    all_chats_dict = DB.get("ALL_CHATS", {})
    free_chats = DB.get("FREE_CHANNELS", {})
    paid_chats = DB.get("PAID_CHANNELS", {})
    users_list = []
    now = time.time()
    for uid, data in DB.get("USER_DATA", {}).items():
        joined_batches = data.get("joined_batches", [])
        free_joined = []
        paid_joined = []
        for bid in joined_batches:
            bid_str = str(bid)
            bid_int = int(bid) if bid_str.lstrip('-').isdigit() else bid
            
            if bid_str in free_chats or bid_int in free_chats:
                name = free_chats.get(bid_str) or free_chats.get(bid_int) or all_chats_dict.get(bid_int, f"Batch {bid}")
                free_joined.append({"id": bid, "name": name})
            elif bid_str in paid_chats or bid_int in paid_chats:
                name = paid_chats.get(bid_str) or paid_chats.get(bid_int) or all_chats_dict.get(bid_int, f"Batch {bid}")
                paid_joined.append({"id": bid, "name": name})
            else:
                name = all_chats_dict.get(bid_int, f"Batch {bid}")
                paid_joined.append({"id": bid, "name": name}) 
        
        demos_list = []
        for bid, d_data in data.get("demos", {}).items():
            exp = d_data["expiry"] if isinstance(d_data, dict) else float(d_data)
            demos_list.append({
                "id": bid, "name": all_chats_dict.get(int(bid) if str(bid).lstrip('-').isdigit() else bid, f"Batch {bid}"),
                "is_expired": now > exp, "time_left": max(0, int(exp - now)) // 3600
            })
            
        users_list.append({
            "id": uid, 
            "name": data.get("name", "Unknown"), 
            "username": data.get("username", "N/A"),
            "streak": data.get("current_streak", 0), 
            "free_joined": free_joined,
            "paid_joined": paid_joined,
            "demos": demos_list,
            "is_admin": is_admin(uid),
            "is_owner": str(uid) == str(OWNER_ID)
        })
    return jsonify({"users": users_list})

@app.route('/api/owner/action', methods=['POST'])
async def api_owner_action():
    from quart import request
    data = await request.json
    req_user_id = data.get('req_user_id')
    action = data.get('action')
    target_uid = int(data.get('target_uid'))
    
    if str(req_user_id) != str(OWNER_ID) and req_user_id != OWNER_ID:
        return jsonify({"error": "Unauthorized"}), 403
        
    bot = getattr(config, 'bot_app', None)
    if not bot:
        return jsonify({"error": "Bot not ready"}), 503

    if action == "ban":
        if target_uid not in DB.get("BLOCKED_USERS", []):
            DB.setdefault("BLOCKED_USERS", []).append(target_uid)
        asyncio.create_task(config.save_data_async())
        try:
            await config.execute_universal_kick(target_uid, bot, permanent_ban=True)
        except Exception:
            pass
        return jsonify({"success": True})
        
    elif action == "kick":
        batch_id = int(data.get('batch_id'))
        try:
            await bot.ban_chat_member(batch_id, target_uid)
            await bot.unban_chat_member(batch_id, target_uid)
            if target_uid in DB.get("USER_DATA", {}) and "demos" in DB["USER_DATA"][target_uid]:
                if str(batch_id) in DB["USER_DATA"][target_uid]["demos"]:
                    del DB["USER_DATA"][target_uid]["demos"][str(batch_id)]
            asyncio.create_task(config.save_data_async())
            return jsonify({"success": True})
        except Exception as e:
            return jsonify({"error": str(e)}), 500
            
    return jsonify({"error": "Invalid Action"}), 400

@app.route('/quiz')
async def quiz_page():
    try: return await render_template('test_generator.html')
    except Exception as e: return f"Error: {e}", 200

# =====================================================================
# DAILY FLASH CHALLENGE LOGIC (GAMIFICATION & JSON BUFFER)
# =====================================================================

@app.route('/api/user_stats/<int:user_id>')
async def get_flash_stats(user_id):
    user_key = str(user_id) if str(user_id) in DB["USER_DATA"] else (user_id if user_id in DB["USER_DATA"] else None)
    user_data = DB["USER_DATA"].get(user_key, {})
    
    stats = {
        "points": user_data.get("points", 0),
        "streak": user_data.get("flash_streak", 0),
        "badges": user_data.get("badges", ["Novice"])
    }
    return jsonify(stats)

@app.route('/api/daily_quiz/<int:user_id>')
async def get_daily_quiz(user_id):
    user_key = str(user_id) if str(user_id) in DB["USER_DATA"] else (user_id if user_id in DB["USER_DATA"] else None)
    user_data = DB["USER_DATA"].get(user_key, {})
    
    day_number = datetime.datetime.now().day
    filename = f"daily_questions/day_{day_number:03d}.json"
    
    today_str = datetime.datetime.now().strftime('%Y-%m-%d')
    if user_data.get("last_flash_solved_date") == today_str:
        return jsonify({"already_solved": True})

    if not os.path.exists(filename):
        return jsonify({"error": "No challenge configured for today."}), 404

    with open(filename, 'r', encoding='utf-8') as f:
        question_data = json.load(f)
        
    safe_data = {
        "id": question_data["id"],
        "question": question_data["question"],
        "options": question_data["options"]
    }
    return jsonify(safe_data)

@app.route('/api/submit_quiz', methods=['POST'])
async def submit_quiz():
    data = await request.json
    user_id = data.get("uid")
    selected_option = data.get("selected_option")
    
    user_key = str(user_id) if str(user_id) in DB["USER_DATA"] else (user_id if user_id in DB["USER_DATA"] else None)
    if not user_key:
        return jsonify({"error": "User not found"}), 404

    day_number = datetime.datetime.now().day
    filename = f"daily_questions/day_{day_number:03d}.json"
    
    if not os.path.exists(filename):
        return jsonify({"error": "Question file missing"}), 404

    with open(filename, 'r', encoding='utf-8') as f:
        question_data = json.load(f)
        
    correct_index = question_data["answer_index"]
    is_correct = (selected_option == correct_index)
    
    # Update Stats
    today_str = datetime.datetime.now().strftime('%Y-%m-%d')
    DB["USER_DATA"][user_key]["last_flash_solved_date"] = today_str
    
    if is_correct:
        points = DB["USER_DATA"][user_key].get("points", 0) + 10
        streak = DB["USER_DATA"][user_key].get("flash_streak", 0) + 1
        badges = DB["USER_DATA"][user_key].get("badges", ["Novice"])
        
        if points >= 50 and "Quiz Master" not in badges: badges.append("Quiz Master")
        if streak >= 7 and "Study Addict" not in badges: badges.append("Study Addict")
            
        DB["USER_DATA"][user_key]["points"] = points
        DB["USER_DATA"][user_key]["flash_streak"] = streak
        DB["USER_DATA"][user_key]["badges"] = badges
        
    asyncio.create_task(config.save_data_async())
    
    return jsonify({"is_correct": is_correct, "correct_index": correct_index})

# =====================================================================
# TESTBOOK PROXY LOGIC (Kept Intact for future use, NO AI PDF)
# =====================================================================

@app.route('/api/tb/search')
async def tb_search_proxy():
    query = request.args.get('q')
    async with httpx.AsyncClient(timeout=30) as client:
        res = await client.get(f"{TESTBOOK_API_URL}/api/search?q={query}")
        return jsonify(res.json().get('results', []))

@app.route('/api/tb/series/<slug>')
async def tb_series_proxy(slug):
    async with httpx.AsyncClient(timeout=30) as client:
        res = await client.get(f"{TESTBOOK_API_URL}/api/series/{slug}")
        return jsonify(res.json().get('details', {}))

@app.route('/api/tb/tests/<series_id>/<section_id>/<sub_id>')
async def tb_tests_proxy(series_id, section_id, sub_id):
    async with httpx.AsyncClient(timeout=30) as client:
        res = await client.get(f"{TESTBOOK_API_URL}/api/tests?series_id={series_id}&section_id={section_id}&sub_id={sub_id}")
        return jsonify(res.json().get('tests', []))

@app.route('/api/tb/extract/<test_id>', methods=['POST'])
async def tb_extract_proxy(test_id):
    from quart import request
    data = await request.json
    async with httpx.AsyncClient(timeout=60) as client:
        res = await client.get(f"{TESTBOOK_API_URL}/api/extract/{test_id}")
        q_data = res.json().get('quiz_data', {})
    if 'error' in q_data:
        return jsonify({"error": q_data['error']}), 400

    from html_generator import generate_html
    details = {
        "Test Series": data.get('series_details', {}).get('name', 'N/A'),
        "Section": data.get('section', {}).get('name', 'N/A'),
        "Subsection": data.get('subsection', {}).get('name', 'N/A'),
        "Test Name": data.get('test_summary', {}).get('title', 'N/A'),
        "Questions": str(data.get('test_summary', {}).get('questionCount', '?')),
        "Duration": f"{data.get('test_summary', {}).get('duration', 'N/A')} minutes",
        "Total Marks": str(data.get('test_summary', {}).get('totalMark', 'N/A')),
        "Correct": "+1",
        "Incorrect": "-0.25" 
    }
    html_content = generate_html(q_data, details)
    return html_content, 200, {'Content-Type': 'text/html'}

@app.route('/flash')
async def flash_page():
    try: return await render_template('flash_arena.html')
    except Exception as e: return f"Error: {e}", 200

@app.route('/api/explore/<int:user_id>')
async def api_explore_data(user_id):
    user_key = str(user_id) if str(user_id) in DB["USER_DATA"] else (user_id if user_id in DB["USER_DATA"] else None)
    user_data = DB["USER_DATA"].get(user_key) if user_key else {}
    
    joined_list = user_data.get("joined_batches", [])
    demo_keys = list(user_data.get("demos", {}).keys())
    now = time.time()
    
    explore_data = {cat: {"free": [], "paid": []} for cat in DB.get("CATEGORIES", [])}
    if "Other Batches" not in explore_data: explore_data["Other Batches"] = {"free": [], "paid": []}
    
    for bid, name in DB.get("FREE_CHANNELS", {}).items():
        cat = DB.get("BATCH_CATEGORIES", {}).get(str(bid), "Other Batches")
        if cat not in explore_data: explore_data[cat] = {"free": [], "paid": []}
        is_joined = int(bid) in joined_list or str(bid) in joined_list
        explore_data[cat]["free"].append({"id": bid, "name": name, "status": "Joined ✔️" if is_joined else "Join Now ➡️"})
        
    for bid, name in DB.get("PAID_CHANNELS", {}).items():
        bid_str = str(bid)
        cat = DB.get("BATCH_CATEGORIES", {}).get(bid_str, "Other Batches")
        if cat not in explore_data: explore_data[cat] = {"free": [], "paid": []}
        
        is_joined = int(bid) in joined_list or bid_str in joined_list
        has_demo = bid_str in demo_keys
        status = "Lifetime Access 👑" if is_joined else ("Demo Run ⏳" if has_demo else "Buy Access 🔒")
        
        if has_demo:
            exp = user_data["demos"][bid_str]["expiry"] if isinstance(user_data["demos"][bid_str], dict) else float(user_data["demos"][bid_str])
            if now > exp: status = "Expired ❌"
            
        explore_data[cat]["paid"].append({"id": bid, "name": name, "status": status})
        
    return jsonify({"categories": DB.get("CATEGORIES", []), "explore_data": explore_data, "paid_locked": DB.get("PAID_LOCKED", False)})

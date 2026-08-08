import time
import io
import asyncio
import datetime
from cryptography.fernet import Fernet
import httpx
import json
import os
import base64
import aiofiles
from quart import Quart, jsonify, render_template, send_file, request
import config
from config import DB, OWNER_ID, is_admin, get_membership_cached
from pyrogram.enums import ChatMemberStatus
from pyrogram.errors import FloodWait

# ==========================================
# FIREBASE SETUP (Secure Init)
# ==========================================
import firebase_admin
from firebase_admin import credentials, firestore

def get_cipher():
    # Hugging Face Secrets se 'TG_ENCRYPTION_KEY' nikalenge
    # Key banana ke liye terminal me chalayein: 
    # python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    key = os.environ.get("TG_ENCRYPTION_KEY")
    if not key:
        # Fallback for local testing if secret is not set
        key = b'zF2wzO0BqB6b7H3H7uW7r0UvQ1z6k3l7t2p8s5g4m9Y=' 
    return Fernet(key)

def init_firebase():
    cred_b64 = os.environ.get("FIREBASE_CRED_B64")
    if not cred_b64:
        print("🚨 FIREBASE ERROR: FIREBASE_CRED_B64 secret HF me nahi mila!")
        return None
        
    try:
        cred_dict = json.loads(base64.b64decode(cred_b64).decode('utf-8'))
        project_id = cred_dict.get("project_id")
        cred = credentials.Certificate(cred_dict)
        
        if not firebase_admin._apps:
            firebase_admin.initialize_app(cred, {'projectId': project_id})
            
        print(f"✅ Firebase Firestore Connected Successfully! (Project: {project_id})")
        return firestore.client()
    except Exception as e:
        print(f"🚨 Firebase Init Error: {e}")
        return None

db_fs = init_firebase()

def sync_fs_write(uid, data):
    if db_fs: db_fs.collection('users').document(str(uid)).set(data, merge=True)

def sync_fs_read(uid):
    if db_fs: 
        doc = db_fs.collection('users').document(str(uid)).get()
        return doc.to_dict() if doc.exists else {}
    return {}

TESTBOOK_API_URL = "https://itsh4r01-live-stream-engine.hf.space"
app = Quart(__name__)

# ==========================================
# STABILITY FIX: HTTPX Connection Pooling & Error Handling
# ==========================================
@app.before_serving
async def startup_http_client():
    # Reuse single client for all requests to save RAM and avoid Timeout/502 errors
    app.http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(60.0),
        limits=httpx.Limits(max_keepalive_connections=100, max_connections=200)
    )

@app.after_serving
async def shutdown_http_client():
    await app.http_client.aclose()

@app.errorhandler(Exception)
async def handle_global_error(error):
    print(f"🚨 Server Error: {error}")
    return jsonify({"error": "Internal Server Error", "details": str(error)}), 500

AVATAR_CACHE = {}
AVATAR_CACHE_MAX_ENTRIES = 500

# ==========================================
# MIDDLEWARE: MANDATORY CHANNEL ENFORCEMENT
# ==========================================
async def enforce_mandatory(user_id):
    if not getattr(config, "MANDATORY_CHANNEL_ID", 0): return None
    if str(user_id) == str(config.OWNER_ID) or config.is_admin(user_id): return None
    
    bot = getattr(config, 'bot_app', None)
    if not bot: return None
    
    is_joined = await config.get_membership_cached(bot, config.MANDATORY_CHANNEL_ID, user_id)
    if not is_joined:
        return jsonify({"error": "must_join", "channel_link": getattr(config, "MANDATORY_CHANNEL_LINK", "https://t.me/")}), 403
    return None

# ==========================================
# PAGE ROUTES (Passing Bot Username)
# ==========================================
@app.route('/favicon.ico')
async def favicon():
    # Ignore browser logo requests silently
    return "", 204

@app.route('/health')
async def health():
    is_active, remaining = config.get_flood_wait_status()
    return jsonify({"status": "OK", "flood_wait_active": is_active, "flood_wait_seconds": remaining}), 200

@app.route('/')
async def index():
    return await render_template('dashboard.html', bot_username=getattr(config, 'BOT_USERNAME', 'H4R_Bot'))

@app.route('/explore')
async def explore_page():
    return await render_template('explore.html', bot_username=getattr(config, 'BOT_USERNAME', 'H4R_Bot'))

@app.route('/extractor')
async def extractor_page():
    return await render_template('extractor.html', bot_username=getattr(config, 'BOT_USERNAME', 'H4R_Bot'))

@app.route('/admin_panel')
async def admin_page():
    return await render_template('admin.html', bot_username=getattr(config, 'BOT_USERNAME', 'H4R_Bot'))

@app.route('/owner_panel')
async def owner_page():
    return await render_template('owner.html', bot_username=getattr(config, 'BOT_USERNAME', 'H4R_Bot'))

@app.route('/quiz')
async def quiz_page():
    return await render_template('test_generator.html', bot_username=getattr(config, 'BOT_USERNAME', 'H4R_Bot'))

@app.route('/flash')
async def flash_page():
    return await render_template('flash_arena.html', bot_username=getattr(config, 'BOT_USERNAME', 'H4R_Bot'))

@app.route('/profile')
async def profile_page():
    return await render_template('profile.html', bot_username=getattr(config, 'BOT_USERNAME', 'H4R_Bot'))

@app.route('/leaderboard')
async def leaderboard_page():
    return await render_template('leaderboard.html', bot_username=getattr(config, 'BOT_USERNAME', 'H4R_Bot'))

# ==========================================
# OTT PLAYER & SESSION API
# ==========================================
@app.route('/sw.js')
async def service_worker():
    # Service Worker ko root path chahiye, isliye isko explicitly serve karte hain
    return await send_file('static/sw.js', mimetype='application/javascript')

@app.route('/player')
async def player_page():
    # Frontend HTML render, passing API keys securely from config
    return await render_template('player.html', 
                                 api_id=getattr(config, 'API_ID', 2040), 
                                 api_hash=getattr(config, 'API_HASH', 'b18441a1ff607e10a989891a5462e627'),
                                 bot_username=getattr(config, 'BOT_USERNAME', 'H4R_Bot'))

@app.route('/pdf')
async def pdf_page():
    return await render_template('pdf.html')

@app.route('/api/session/save', methods=['POST'])
async def save_session():
    data = await request.json
    uid = data.get("uid")
    session_str = data.get("session")
    
    if not uid or not session_str:
        return jsonify({"success": False, "error": "Missing data"}), 400
        
    try:
        cipher = get_cipher()
        encrypted_session = cipher.encrypt(session_str.encode()).decode()
        
        # Saving directly to Firebase
        await asyncio.to_thread(sync_fs_write, uid, {"tg_session": encrypted_session})
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/session/get/<int:uid>')
async def get_user_session(uid):
    try:
        fs_data = await asyncio.to_thread(sync_fs_read, uid)
        enc_session = fs_data.get("tg_session")
        
        if enc_session:
            cipher = get_cipher()
            dec_session = cipher.decrypt(enc_session.encode()).decode()
            return jsonify({"success": True, "session": dec_session})
            
        return jsonify({"success": False, "error": "No session found"})
    except Exception as e:
        return jsonify({"success": False, "error": "Decryption failed"}), 500

@app.route('/api/thumb/<file_id>')
async def get_thumbnail(file_id):
    if not hasattr(config, 'bot_app') or not config.bot_app:
        return "Bot not ready", 503
    try:
        bot = config.bot_app
        file_obj = await bot.download_media(file_id, in_memory=True)
        img_bytes = file_obj.getvalue() if hasattr(file_obj, "getvalue") else file_obj
        if img_bytes:
            return await send_file(io.BytesIO(img_bytes), mimetype='image/jpeg')
    except Exception as e:
        pass
    return "Not found", 404

# ==========================================
# AVATAR & USER DASHBOARD LOGIC
# ==========================================
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
    chk = await enforce_mandatory(user_id)
    if chk: return chk
    
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
        sem = asyncio.Semaphore(5)  # Limit concurrent MTProto requests to avoid instant FloodWaits
        
        async def check_membership(bid):
            async with sem:
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
            response["my_batches"].append({"id": bid, "name": name, "type": "Free Channel", "status": "Joined", "category": batch_cats.get(str(bid), "Other Batches")})
            
    for bid, name in DB.get("PAID_CHANNELS", {}).items():
        bid_str = str(bid)
        is_joined = int(bid) in joined_list or bid_str in joined_list
        has_demo = bid_str in demo_keys
        if is_joined or has_demo:
            status = "Lifetime Access" if is_joined else "Demo Run"
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

# ==========================================
# BATCH CONTENT API (For Subfolders & Videos)
# ==========================================
@app.route('/api/batch/<chat_id>')
async def get_batch_data(chat_id):
    try:
        # Firebase se specific batch ka document uthayenge
        doc = await asyncio.to_thread(db_fs.collection('batch_contents').document(str(chat_id)).get)
        
        if doc.exists:
            return jsonify({"success": True, "data": doc.to_dict()})
        else:
            return jsonify({"success": False, "error": "Abhi is batch ka data index nahi hua hai. Admin ko bolkar Scan karwayein."}), 404
            
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# =====================================================================
# DAILY FLASH CHALLENGE (FIREBASE GAMIFICATION)
# =====================================================================
@app.route('/api/profile_stats/<int:user_id>')
async def get_profile_stats(user_id):
    chk = await enforce_mandatory(user_id)
    if chk: return chk
    
    fs_data = await asyncio.to_thread(sync_fs_read, user_id)
    
    total_flash = fs_data.get("flash_attempted", 0)
    correct_flash = fs_data.get("flash_correct", 0)
    accuracy = int((correct_flash / total_flash) * 100) if total_flash > 0 else 0
    
    stats = {
        "name": fs_data.get("name", "Unknown"),
        "points": fs_data.get("points", 0),
        "streak": fs_data.get("streak", 0),
        "badges": fs_data.get("badges", ["Novice"]),
        "tests_given": total_flash,
        "accuracy": accuracy
    }
    return jsonify(stats)

@app.route('/api/daily_quiz/<int:user_id>')
async def get_daily_quiz(user_id):
    chk = await enforce_mandatory(user_id)
    if chk: return chk
    
    fs_data = await asyncio.to_thread(sync_fs_read, user_id)
    day_number = datetime.datetime.now().day
    filename = f"daily_questions/day_{day_number:03d}.json"
    
    today_str = datetime.datetime.now().strftime('%Y-%m-%d')
    
    if not os.path.exists(filename):
        question_data = {
            "id": "q_dummy",
            "question": "Welcome to H4R! This is a test question to check Firebase saving. What is 2 + 2?",
            "options": ["3", "4", "5", "6"],
            "answer_index": 1,
            "flashcard": "Math is simple! 2 + 2 = 4. And your Firebase is working perfectly!"
        }
    else:
        async with aiofiles.open(filename, 'r', encoding='utf-8') as f:
            content = await f.read()
            question_data = json.loads(content)
            
    if fs_data.get("last_played") == today_str:
        return jsonify({
            "already_solved": True,
            "flashcard": question_data.get("flashcard", "Keep learning everyday!")
        })
        
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
    if not user_key: return jsonify({"error": "User not found"}), 404
    
    day_number = datetime.datetime.now().day
    filename = f"daily_questions/day_{day_number:03d}.json"
    
    if not os.path.exists(filename):
        question_data = {
            "answer_index": 1,
            "flashcard": "Math is simple! 2 + 2 = 4. And your Firebase is working perfectly!",
            "options": ["3", "4", "5", "6"]
        }
    else:
        async with aiofiles.open(filename, 'r', encoding='utf-8') as f: 
            content = await f.read()
            question_data = json.loads(content)
            
    correct_index = question_data["answer_index"]
    is_correct = (selected_option == correct_index)
    flashcard_text = question_data.get("flashcard", "Concept updated!")
    
    today_str = datetime.datetime.now().strftime('%Y-%m-%d')
    
    fs_data = await asyncio.to_thread(sync_fs_read, user_id)
    points = fs_data.get("points", 0)
    streak = fs_data.get("streak", 0)
    attempts = fs_data.get("flash_attempted", 0) + 1
    corrects = fs_data.get("flash_correct", 0)
    badges = fs_data.get("badges", ["Novice"])
    
    if is_correct:
        points += 10
        streak += 1
        corrects += 1
        if points >= 50 and "Quiz Master" not in badges: badges.append("Quiz Master")
        if streak >= 7 and "Study Addict" not in badges: badges.append("Study Addict")
    else:
        streak = 0
        
    accuracy = int((corrects / attempts) * 100)
    new_fs_data = {
        "uid": user_id,
        "name": DB["USER_DATA"][user_key].get("name", "User"),
        "points": points,
        "streak": streak,
        "flash_attempted": attempts,
        "flash_correct": corrects,
        "accuracy": accuracy,
        "badges": badges,
        "last_played": today_str
    }
    
    await asyncio.to_thread(sync_fs_write, user_id, new_fs_data)
    
    return jsonify({
        "is_correct": is_correct, 
        "correct_index": correct_index,
        "correct_text": question_data["options"][correct_index],
        "flashcard": flashcard_text
    })

@app.route('/api/past_flashcards')
async def past_flashcards():
    past_cards = []
    today = datetime.datetime.now().day
    if os.path.exists("daily_questions"):
        for file in os.listdir("daily_questions"):
            if file.endswith(".json"):
                day_num = int(file.split("_")[1].split(".")[0])
                if day_num < today:
                    async with aiofiles.open(os.path.join("daily_questions", file), 'r', encoding='utf-8') as f:
                        content = await f.read()
                        data = json.loads(content)
                        past_cards.append({
                            "day": day_num,
                            "question": data["question"],
                            "answer": data["options"][data["answer_index"]],
                            "flashcard": data.get("flashcard", "No specific trick provided.")
                        })
    past_cards.sort(key=lambda x: x["day"], reverse=True)
    return jsonify(past_cards)

@app.route('/api/leaderboard/<metric>')
async def get_leaderboard(metric):
    if not db_fs: return jsonify({"error": "Firebase not connected"}), 500
    def fetch_lb():
        docs = db_fs.collection('users').order_by(metric, direction=firestore.Query.DESCENDING).limit(10).stream()
        return [doc.to_dict() for doc in docs]
    lb_data = await asyncio.to_thread(fetch_lb)
    return jsonify(lb_data)

# =====================================================================
# EXPLORE & TESTBOOK LOGIC (Proxy)
# =====================================================================
@app.route('/api/explore/<int:user_id>')
async def api_explore_data(user_id):
    chk = await enforce_mandatory(user_id)
    if chk: return chk
    
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
        explore_data[cat]["free"].append({"id": bid, "name": name, "status": "Joined" if is_joined else "Join Now"})
        
    for bid, name in DB.get("PAID_CHANNELS", {}).items():
        bid_str = str(bid)
        cat = DB.get("BATCH_CATEGORIES", {}).get(bid_str, "Other Batches")
        if cat not in explore_data: explore_data[cat] = {"free": [], "paid": []}
        
        is_joined = int(bid) in joined_list or bid_str in joined_list
        has_demo = bid_str in demo_keys
        status = "Lifetime Access" if is_joined else ("Demo Run" if has_demo else "Buy Access")
        
        if has_demo:
            exp = user_data["demos"][bid_str]["expiry"] if isinstance(user_data["demos"][bid_str], dict) else float(user_data["demos"][bid_str])
            if now > exp: status = "Expired"
            
        explore_data[cat]["paid"].append({"id": bid, "name": name, "status": status})
        
    return jsonify({"categories": DB.get("CATEGORIES", []), "explore_data": explore_data, "paid_locked": DB.get("PAID_LOCKED", False)})

@app.route('/api/tb/search')
async def tb_search_proxy():
    query = request.args.get('q')
    res = await app.http_client.get(f"{TESTBOOK_API_URL}/api/search?q={query}")
    return jsonify(res.json().get('results', []))

@app.route('/api/tb/series/<slug>')
async def tb_series_proxy(slug):
    res = await app.http_client.get(f"{TESTBOOK_API_URL}/api/series/{slug}")
    return jsonify(res.json().get('details', {}))

@app.route('/api/tb/tests/<series_id>/<section_id>/<sub_id>')
async def tb_tests_proxy(series_id, section_id, sub_id):
    res = await app.http_client.get(f"{TESTBOOK_API_URL}/api/tests?series_id={series_id}&section_id={section_id}&sub_id={sub_id}")
    return jsonify(res.json().get('tests', []))

@app.route('/api/tb/extract/<test_id>', methods=['POST'])
async def tb_extract_proxy(test_id):
    data = await request.json
    res = await app.http_client.get(f"{TESTBOOK_API_URL}/api/extract/{test_id}")
    q_data = res.json().get('quiz_data', {})
    
    if 'error' in q_data:
        return jsonify({"error": q_data['error']}), 400
        
    from html_generator import generate_html
    details = {
        "Test Series": (data.get('series_details') or {}).get('name', 'N/A'),
        "Section": (data.get('section') or {}).get('name', 'N/A'),
        "Subsection": (data.get('subsection') or {}).get('name', 'N/A'),
        "Test Name": (data.get('test_summary') or {}).get('title', 'N/A'),
        "Questions": str((data.get('test_summary') or {}).get('questionCount', '?')),
        "Duration": f"{(data.get('test_summary') or {}).get('duration', 'N/A')} minutes",
        "Total Marks": str((data.get('test_summary') or {}).get('totalMark', 'N/A')),
        "Correct": "+1",
        "Incorrect": "-0.25" 
    }
    
    html_content = await asyncio.to_thread(generate_html, q_data, details)
    return html_content, 200, {'Content-Type': 'text/html'}

@app.route('/api/tb/pdf-note')
async def tb_pdf_note_proxy():
    target_url = request.args.get('url')
    if not target_url:
        return jsonify({"error": "Missing URL parameter"}), 400
        
    res = await app.http_client.get(f"{TESTBOOK_API_URL}/api/extract/pdf-note?url={target_url}")
        
    if res.status_code != 200:
        return jsonify({"error": "Failed to extract PDF"}), res.status_code
        
    return jsonify(res.json()), 200

@app.route('/api/tb/current-affairs', methods=['POST'])
async def tb_current_affairs_proxy():
    data = await request.json
    target_url = data.get('url')
    if not target_url:
        return jsonify({"error": "Missing URL parameter"}), 400
        
    res = await app.http_client.get(f"{TESTBOOK_API_URL}/api/extract/current-affairs?url={target_url}")
    res_data = res.json()
    q_data = res_data.get('quiz_data', {})
        
    if 'error' in q_data or 'error' in res_data:
        error_msg = q_data.get('error') or res_data.get('error')
        return jsonify({"error": error_msg}), 400
        
    from html_generator import generate_html
    
    details = {
        "Test Series": "Daily Current Affairs",
        "Section": "General Knowledge",
        "Subsection": "Daily Updates",
        "Test Name": q_data.get('title', 'Current Affairs Quiz'),
        "Questions": str(len(q_data.get('questions', []))),
        "Duration": "15 minutes",
        "Total Marks": str(len(q_data.get('questions', []))),
        "Correct": "+1",
        "Incorrect": "-0.25" 
    }
    
    html_content = await asyncio.to_thread(generate_html, q_data, details)
    return html_content, 200, {'Content-Type': 'text/html'}

# FIX: SECURE RATE LIMITING FOR POINTS
@app.route('/api/submit_tb_test', methods=['POST'])
async def submit_tb_test():
    data = await request.json
    user_id = data.get("uid")
    
    if not user_id: 
        return jsonify({"error": "No user ID"}), 400
        
    fs_data = await asyncio.to_thread(sync_fs_read, user_id)
    
    today_str = datetime.datetime.now().strftime('%Y-%m-%d')
    last_played = fs_data.get("last_ca_played", "")
    tests_today = fs_data.get("ca_tests_today", 0)

    # Security check: User can only earn points for max 3 CA tests per day
    if last_played == today_str and tests_today >= 3:
        return jsonify({"success": True, "message": "Daily limit reached for points."})
    
    if last_played != today_str:
        tests_today = 0
        
    points = fs_data.get("points", 0) + 50 
    attempts = fs_data.get("flash_attempted", 0) + 1  
    
    new_fs_data = {
        "uid": user_id,
        "points": points,
        "flash_attempted": attempts,
        "last_ca_played": today_str,
        "ca_tests_today": tests_today + 1
    }
    
    await asyncio.to_thread(sync_fs_write, user_id, new_fs_data)
    return jsonify({"success": True, "points_added": 50})

# =====================================================================
# OWNER API LOGIC (Secured Action)
# =====================================================================
@app.route('/api/owner/users/<int:req_user_id>')
async def api_owner_users(req_user_id):
    if str(req_user_id) != str(OWNER_ID) and req_user_id != OWNER_ID:
        return jsonify({"error": "Unauthorized"}), 403
        
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

# =====================================================================
# STUDY NOTES API PROXIES (For extractor.html)
# =====================================================================
@app.route('/api/study/groups', methods=['GET'])
async def tb_study_groups_proxy():
    res = await app.http_client.get(f"{TESTBOOK_API_URL}/api/study/groups")
    return jsonify(res.json()), res.status_code

@app.route('/api/study/subjects', methods=['GET'])
async def tb_study_subjects_proxy():
    group_id = request.args.get('group_id')
    res = await app.http_client.get(f"{TESTBOOK_API_URL}/api/study/subjects?group_id={group_id}")
    return jsonify(res.json()), res.status_code

@app.route('/api/study/chapters', methods=['GET'])
async def tb_study_chapters_proxy():
    subject_id = request.args.get('subject_id')
    res = await app.http_client.get(f"{TESTBOOK_API_URL}/api/study/chapters?subject_id={subject_id}")
    return jsonify(res.json()), res.status_code

@app.route('/api/study/notes', methods=['GET'])
async def tb_study_notes_proxy():
    chapter_id = request.args.get('chapter_id')
    res = await app.http_client.get(f"{TESTBOOK_API_URL}/api/study/notes?chapter_id={chapter_id}")
    return jsonify(res.json()), res.status_code

@app.route('/api/study/note-pdf', methods=['GET'])
async def tb_study_note_pdf_proxy():
    note_id = request.args.get('note_id')
    if not note_id:
        return jsonify({"error": "Missing 'note_id' parameter"}), 400
    res = await app.http_client.get(f"{TESTBOOK_API_URL}/api/study/note-pdf?note_id={note_id}")
    return jsonify(res.json()), res.status_code

# FIX: ASYNC TASK FOR KICK TO PREVENT FLOODWAIT HANGING
@app.route('/api/owner/action', methods=['POST'])
async def api_owner_action():
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
        # Prevent API hang by running kick in background task
        asyncio.create_task(config.execute_universal_kick(target_uid, bot, permanent_ban=True))
        return jsonify({"success": True})
        
    elif action == "kick":
        batch_id = int(data.get('batch_id'))
        
        # Async background worker for single batch kick
        async def _background_kick():
            try:
                await bot.ban_chat_member(batch_id, target_uid)
                await bot.unban_chat_member(batch_id, target_uid)
                if target_uid in DB.get("USER_DATA", {}) and "demos" in DB["USER_DATA"][target_uid]:
                    if str(batch_id) in DB["USER_DATA"][target_uid]["demos"]:
                        del DB["USER_DATA"][target_uid]["demos"][str(batch_id)]
                await config.save_data_async()
            except FloodWait as e:
                await asyncio.sleep(e.value + 1)
                try:
                    await bot.ban_chat_member(batch_id, target_uid)
                    await bot.unban_chat_member(batch_id, target_uid)
                    if target_uid in DB.get("USER_DATA", {}) and "demos" in DB["USER_DATA"][target_uid]:
                        if str(batch_id) in DB["USER_DATA"][target_uid]["demos"]:
                            del DB["USER_DATA"][target_uid]["demos"][str(batch_id)]
                    await config.save_data_async()
                except Exception:
                    pass
            except Exception:
                pass
                
        asyncio.create_task(_background_kick())
        return jsonify({"success": True, "message": "Kick action processing in background."})
            
    return jsonify({"error": "Invalid Action"}), 400

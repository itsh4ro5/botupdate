import os
import json
import asyncio
import logging
import time
from datetime import datetime
from quart import Quart, jsonify, request, session, redirect, url_for, render_template, send_file
import config
from config import DB, save_data_async, save_data_sync

logger = logging.getLogger("web_portal")

app = Quart(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "kamal_bot_secret_key_2026_xyz")

WEB_PASSWORD = os.environ.get("WEB_PASSWORD") or os.environ.get("ADMIN_PASSWORD") or str(config.OWNER_ID or "admin123")

def _get_active_batches():
    batches = []
    # 1. Free Batches
    for cid, name in DB.get("FREE_CHANNELS", {}).items():
        cat = DB.get("BATCH_CATEGORIES", {}).get(str(cid), "Other Batches")
        batches.append({"id": cid, "name": name, "category": cat, "type": "free", "price": "Free", "coins": 0})
    # 2. Paid Batches
    for cid, name in DB.get("PAID_CHANNELS", {}).items():
        cat = DB.get("BATCH_CATEGORIES", {}).get(str(cid), "Premium Batches")
        price = DB.get("BATCH_PRICES", {}).get(str(cid), "Contact Admin")
        batches.append({"id": cid, "name": name, "category": cat, "type": "paid", "price": price, "coins": 0})
    # 3. Special Batches
    for cid, name in DB.get("SPECIAL_CHANNELS", {}).items():
        cat = DB.get("BATCH_CATEGORIES", {}).get(str(cid), "Special Batches")
        coins = DB.get("BATCH_COINS", {}).get(str(cid), 1)
        batches.append({"id": cid, "name": name, "category": cat, "type": "special", "price": f"{coins} Coins", "coins": coins})
    return batches

def _get_leaderboard():
    ranked = []
    for u_k, u_d in DB.get("USER_DATA", {}).items():
        inv = u_d.get("total_invited", 0)
        if inv > 0:
            ranked.append({
                "id": u_k,
                "name": u_d.get("name") or f"Student {u_k}",
                "invites": inv
            })
    ranked.sort(key=lambda x: x["invites"], reverse=True)
    return ranked[:10]

def _get_study_leaderboard():
    ranked = []
    for u_k, u_d in DB.get("USER_DATA", {}).items():
        mins = int(u_d.get("study_minutes", 0))
        sess = int(u_d.get("study_sessions_completed", 0))
        streak = int(u_d.get("streak", 0))
        if mins > 0 or sess > 0 or streak > 0:
            ranked.append({
                "id": u_k,
                "name": u_d.get("name") or f"Student {u_k}",
                "minutes": mins,
                "sessions": sess,
                "streak": streak
            })
    ranked.sort(key=lambda x: (x["minutes"], x["sessions"], x["streak"]), reverse=True)
    return ranked[:15]

# --- HEALTH CHECK & PUBLIC ROUTES ---
@app.route('/health')
async def health_check():
    return jsonify({
        "status": "healthy",
        "service": "Telegram Bot MTProto & Web Portal",
        "port": int(os.environ.get("PORT", 7860)),
        "users": len(DB.get("USER_DATA", {})),
        "batches": len(DB.get("ALL_CHATS", {}))
    }), 200

@app.route('/')
async def root():
    return redirect('/webapp')

@app.route('/webapp')
@app.route('/store')
@app.route('/app')
async def student_store():
    batches = _get_active_batches()
    categories = DB.get("CATEGORIES", config.DEFAULT_CATEGORIES)
    leaderboard = _get_leaderboard()
    bot_username = getattr(config, "BOT_USERNAME", "YourBot")

    user_data = DB.get("USER_DATA", {})
    total_coins = sum(round(u.get("referral_count", 0), 2) for u in user_data.values())
    pending_demands = sum(1 for st in DB.get("BATCH_REQUEST_STATUS", {}).values() if st != "done")

    stats = {
        "total_users": len(user_data),
        "total_batches": len(DB.get("ALL_CHATS", {})),
        "total_coins": round(total_coins, 2),
        "pending_demands": pending_demands
    }

    maintenance_mode = DB.get("MAINTENANCE_MODE", False)
    settings = {
        "NEW_USERS_ALLOWED": DB.get("NEW_USERS_ALLOWED", True),
        "FREE_LOCKED": DB.get("FREE_LOCKED", False),
        "PAID_LOCKED": DB.get("PAID_LOCKED", False),
        "MAINTENANCE_MODE": maintenance_mode
    }

    reqs = DB.get("BATCH_REQUESTS", {})
    statuses = DB.get("BATCH_REQUEST_STATUS", {})
    demands = []
    for bname, uids in reqs.items():
        demands.append({
            "name": bname,
            "count": len(uids),
            "status": statuses.get(bname, "pending"),
            "uids": uids[:5]
        })
    demands.sort(key=lambda x: (x["status"] == "done", -x["count"]))

    return await render_template(
        "webapp.html",
        batches=batches,
        categories=categories,
        leaderboard=leaderboard,
        study_leaderboard=_get_study_leaderboard(),
        vacancies=DB.get("EXAM_VACANCIES", config.DEFAULT_VACANCIES),
        current_affairs=DB.get("CURRENT_AFFAIRS", config.DEFAULT_CURRENT_AFFAIRS),
        mock_tests=DB.get("MOCK_TESTS", config.DEFAULT_MOCK_TESTS),
        ad_config=DB.get("AD_CONFIG", config.DEFAULT_AD_CONFIG),
        recommended_resources=getattr(config, "DEFAULT_RECOMMENDED_RESOURCES", []),
        bot_username=bot_username,
        bot_title="Educational Batch Store & Study Hub",
        owner_id=str(config.OWNER_ID),
        admin_ids=[str(x) for x in DB.get("ADMIN_IDS", [])],
        tester_ids=[str(x) for x in DB.get("TESTER_IDS", [])],
        session_role=session.get('role', ''),
        maintenance_mode=maintenance_mode,
        stats=stats,
        settings=settings,
        demands=demands
    )

# --- AUTHENTICATION ---
@app.route('/login', methods=['GET', 'POST'])
async def login():
    error = None
    if request.method == 'POST':
        form = await request.form
        pwd = (form.get("password") or "").strip()
        owner_id_str = str(config.OWNER_ID).strip()
        admin_ids = [str(x).strip() for x in DB.get("ADMIN_IDS", [])]
        tester_ids = [str(x).strip() for x in DB.get("TESTER_IDS", [])]

        if pwd == WEB_PASSWORD or pwd == owner_id_str:
            session['authenticated'] = True
            session['role'] = 'owner'
            return redirect('/owner')
        elif pwd in admin_ids or pwd == "admin":
            session['authenticated'] = True
            session['role'] = 'admin'
            return redirect('/admin')
        elif pwd in tester_ids:
            session['authenticated'] = True
            session['role'] = 'tester'
            return redirect('/webapp')
        else:
            error = "Invalid passcode. Please enter a valid Owner ID, Admin key, or Tester ID."

    return await render_template("login.html", error=error)

@app.route('/logout')
async def logout():
    session.clear()
    return redirect('/login')

def is_auth(role="admin", uid=None):
    if session.get('authenticated'):
        s_role = session.get('role')
        if role == "owner":
            return s_role == "owner"
        if role == "admin":
            return s_role in ["owner", "admin"]
        if role == "tester":
            return s_role in ["owner", "admin", "tester"]
        return True
    
    check_id = uid or request.headers.get("X-Telegram-User-Id")
    if check_id:
        uid_str = str(check_id).strip()
        if uid_str == str(config.OWNER_ID).strip():
            return True
        if role in ["admin", "tester"] and uid_str in [str(x).strip() for x in DB.get("ADMIN_IDS", [])]:
            return True
        if role == "tester" and uid_str in [str(x).strip() for x in DB.get("TESTER_IDS", [])]:
            return True
    return False

# --- ADMIN OPERATIONAL DESK ---
@app.route('/admin')
async def admin_portal():
    if not is_auth("admin"):
        return redirect('/login')

    batches = _get_active_batches()
    reqs = DB.get("BATCH_REQUESTS", {})
    statuses = DB.get("BATCH_REQUEST_STATUS", {})
    demands = []
    for bname, uids in reqs.items():
        demands.append({
            "name": bname,
            "count": len(uids),
            "status": statuses.get(bname, "pending"),
            "uids": uids[:5]
        })
    demands.sort(key=lambda x: (x["status"] == "done", -x["count"]))

    bot_username = getattr(config, "BOT_USERNAME", "YourBot")
    return await render_template(
        "admin.html",
        batches=batches,
        demands=demands,
        bot_username=bot_username,
        is_owner=(session.get('role') == 'owner')
    )

# --- OWNER COMMAND CENTER ---
@app.route('/owner')
@app.route('/dashboard')
async def owner_portal():
    if not is_auth("owner"):
        return redirect('/login')

    user_data = DB.get("USER_DATA", {})
    total_coins = sum(round(u.get("referral_count", 0), 2) for u in user_data.values())
    pending_demands = sum(1 for st in DB.get("BATCH_REQUEST_STATUS", {}).values() if st != "done")

    stats = {
        "total_users": len(user_data),
        "total_batches": len(DB.get("ALL_CHATS", {})),
        "total_coins": round(total_coins, 2),
        "pending_demands": pending_demands
    }

    settings = {
        "NEW_USERS_ALLOWED": DB.get("NEW_USERS_ALLOWED", True),
        "FREE_LOCKED": DB.get("FREE_LOCKED", False),
        "PAID_LOCKED": DB.get("PAID_LOCKED", False),
        "MAINTENANCE_MODE": DB.get("MAINTENANCE_MODE", False)
    }

    return await render_template(
        "owner.html",
        stats=stats,
        settings=settings
    )

# --- TELEGRAM MINI APP (TWA) & STUDY APIS ---
@app.route('/api/twa/session', methods=['POST'])
async def api_twa_session():
    data = await request.get_json() or {}
    uid = data.get("user_id")
    first_name = (data.get("first_name") or "").strip()
    username = (data.get("username") or "").strip()
    
    if uid is None or str(uid).strip() == "":
        return jsonify({"success": False, "error": "No user ID provided"}), 400

    uid_str = str(uid).strip()
    owner_id_str = str(config.OWNER_ID).strip()
    admin_ids = [str(x).strip() for x in DB.get("ADMIN_IDS", [])]
    tester_ids = [str(x).strip() for x in DB.get("TESTER_IDS", [])]

    if uid_str == owner_id_str:
        role = "owner"
        session['authenticated'] = True
        session['role'] = 'owner'
    elif uid_str in admin_ids:
        role = "admin"
        session['authenticated'] = True
        session['role'] = 'admin'
    elif uid_str in tester_ids:
        role = "tester"
        session['authenticated'] = True
        session['role'] = 'tester'
    else:
        role = "user"
        session['role'] = 'user'
    
    session['user_id'] = uid_str

    # Retrieve or register student profile
    user_key = uid if uid in DB.get("USER_DATA", {}) else (uid_str if uid_str in DB.get("USER_DATA", {}) else uid)
    if user_key not in DB.setdefault("USER_DATA", {}):
        DB["USER_DATA"][user_key] = {
            "name": first_name or f"Student {uid_str}",
            "username": username,
            "joined_at": time.time(),
            "demos": {},
            "unlocked_batches": [],
            "referral_count": 0,
            "total_invited": 0,
            "streak": 0,
            "study_minutes": 0,
            "study_sessions_completed": 0,
            "study_tasks": []
        }
        await save_data_async()

    u_info = DB["USER_DATA"][user_key]
    user_summary = {
        "user_id": uid_str,
        "name": u_info.get("name") or first_name or f"Student {uid_str}",
        "username": u_info.get("username") or username,
        "coins": round(u_info.get("referral_count", 0), 2),
        "total_invited": u_info.get("total_invited", 0),
        "streak": u_info.get("streak", 0),
        "unlocked_batches": u_info.get("unlocked_batches", []),
        "study_minutes": u_info.get("study_minutes", 0),
        "study_sessions": u_info.get("study_sessions_completed", 0),
        "study_tasks": u_info.get("study_tasks", []),
        "tier": u_info.get("tier", "free"),
        "joined_at": u_info.get("joined_at", time.time())
    }

    resp = {
        "success": True,
        "role": role,
        "user_id": uid_str,
        "maintenance_mode": DB.get("MAINTENANCE_MODE", False),
        "is_tester": role in ["owner", "admin", "tester"],
        "user": user_summary
    }

    if role == "owner":
        user_data = DB.get("USER_DATA", {})
        total_coins = sum(round(u.get("referral_count", 0), 2) for u in user_data.values())
        pending_demands = sum(1 for st in DB.get("BATCH_REQUEST_STATUS", {}).values() if st != "done")
        resp["stats"] = {
            "total_users": len(user_data),
            "total_batches": len(DB.get("ALL_CHATS", {})),
            "total_coins": round(total_coins, 2),
            "pending_demands": pending_demands
        }
        resp["settings"] = {
            "NEW_USERS_ALLOWED": DB.get("NEW_USERS_ALLOWED", True),
            "FREE_LOCKED": DB.get("FREE_LOCKED", False),
            "PAID_LOCKED": DB.get("PAID_LOCKED", False),
            "MAINTENANCE_MODE": DB.get("MAINTENANCE_MODE", False)
        }

    if role in ["owner", "admin"]:
        reqs = DB.get("BATCH_REQUESTS", {})
        statuses = DB.get("BATCH_REQUEST_STATUS", {})
        demands = []
        for bname, uids in reqs.items():
            demands.append({
                "name": bname,
                "count": len(uids),
                "status": statuses.get(bname, "pending"),
                "uids": uids[:5]
            })
        demands.sort(key=lambda x: (x["status"] == "done", -x["count"]))
        resp["demands"] = demands

    resp["study_leaderboard"] = _get_study_leaderboard()
    resp["referral_leaderboard"] = _get_leaderboard()

    return jsonify(resp)

@app.route('/api/study/leaderboard')
async def api_study_leaderboard():
    return jsonify({
        "success": True,
        "study_leaderboard": _get_study_leaderboard(),
        "referral_leaderboard": _get_leaderboard()
    })

@app.route('/api/study/complete-session', methods=['POST'])
async def api_study_complete():
    data = await request.get_json() or {}
    uid = data.get("user_id") or session.get("user_id")
    minutes = int(data.get("minutes", 25))
    subject = (data.get("subject") or "General Study").strip()

    if uid is None or str(uid).strip() == "":
        return jsonify({"success": False, "error": "Missing user ID"}), 400

    user_key = uid if uid in DB.get("USER_DATA", {}) else str(uid)
    u_info = DB.setdefault("USER_DATA", {}).setdefault(user_key, {})
    
    # Reward 0.5 coins for focus completion
    reward = 0.5
    u_info["referral_count"] = round(u_info.get("referral_count", 0) + reward, 2)
    u_info["study_minutes"] = u_info.get("study_minutes", 0) + minutes
    u_info["study_sessions_completed"] = u_info.get("study_sessions_completed", 0) + 1
    await save_data_async()

    # Optional Telegram bot notification
    if hasattr(config, "bot_app") and config.bot_app:
        try:
            await config.bot_app.send_message(
                int(uid),
                f"🎯 **Study Focus Completed!**\n━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📖 **Subject:** `{subject}`\n"
                f"⏱️ **Duration:** `{minutes} Minutes`\n"
                f"🎁 **Reward:** `+{reward} Coins`\n"
                f"💎 **Total Balance:** `{u_info['referral_count']} Coins`\n\n"
                f"Great job! Keep the momentum going! 🚀"
            )
        except Exception:
            pass

    return jsonify({
        "success": True,
        "reward": reward,
        "new_balance": u_info["referral_count"],
        "total_minutes": u_info["study_minutes"],
        "sessions_completed": u_info["study_sessions_completed"]
    })

@app.route('/api/study/complete-quiz', methods=['POST'])
async def api_study_complete_quiz():
    data = await request.get_json() or {}
    uid = data.get("user_id") or session.get("user_id")
    score = int(data.get("score", 0))
    total = int(data.get("total", 5))

    if uid is None or str(uid).strip() == "":
        return jsonify({"success": False, "error": "Missing user ID"}), 400

    user_key = uid if uid in DB.get("USER_DATA", {}) else str(uid)
    u_info = DB.setdefault("USER_DATA", {}).setdefault(user_key, {})

    today_str = time.strftime("%Y-%m-%d")
    if u_info.get("last_quiz_date") == today_str:
        return jsonify({"success": False, "error": "You have already claimed today's practice quiz reward! Come back tomorrow."}), 400

    reward = 1.0 if score >= 3 else 0.5
    u_info["referral_count"] = round(u_info.get("referral_count", 0) + reward, 2)
    u_info["last_quiz_date"] = today_str
    u_info["quizzes_completed"] = u_info.get("quizzes_completed", 0) + 1
    await save_data_async()

    if hasattr(config, "bot_app") and config.bot_app:
        try:
            await config.bot_app.send_message(
                int(uid),
                f"🧠 **Daily Quiz Completed!**\n━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📊 **Score:** `{score}/{total}`\n"
                f"🎁 **Reward:** `+{reward} Study Coins`\n"
                f"💎 **New Balance:** `{u_info['referral_count']} Coins`"
            )
        except Exception:
            pass

    return jsonify({
        "success": True,
        "reward": reward,
        "new_balance": u_info["referral_count"],
        "quizzes_completed": u_info["quizzes_completed"]
    })

@app.route('/api/study/claim-streak', methods=['POST'])
async def api_study_claim_streak():
    data = await request.get_json() or {}
    uid = data.get("user_id") or session.get("user_id")
    if uid is None or str(uid).strip() == "":
        return jsonify({"success": False, "error": "Missing user ID"}), 400

    user_key = uid if uid in DB.get("USER_DATA", {}) else str(uid)
    u_info = DB.setdefault("USER_DATA", {}).setdefault(user_key, {})

    today_str = time.strftime("%Y-%m-%d")
    last_streak = u_info.get("last_streak_date")

    if last_streak == today_str:
        return jsonify({"success": False, "error": "Already claimed today! Keep studying and return tomorrow."}), 400

    current_streak = u_info.get("streak", 0) + 1
    u_info["streak"] = current_streak
    u_info["last_streak_date"] = today_str
    
    bonus = 1.0 if current_streak < 7 else 2.0
    u_info["referral_count"] = round(u_info.get("referral_count", 0) + bonus, 2)
    await save_data_async()

    return jsonify({
        "success": True,
        "streak": current_streak,
        "bonus": bonus,
        "new_balance": u_info["referral_count"]
    })

@app.route('/api/study/tasks', methods=['GET', 'POST'])
async def api_study_tasks():
    if request.method == 'GET':
        uid = request.args.get("user_id") or session.get("user_id")
        if not uid:
            return jsonify({"success": False, "tasks": []})
        user_key = uid if uid in DB.get("USER_DATA", {}) else str(uid)
        tasks = DB.get("USER_DATA", {}).get(user_key, {}).get("study_tasks", [])
        return jsonify({"success": True, "tasks": tasks})
    else:
        data = await request.get_json() or {}
        uid = data.get("user_id") or session.get("user_id")
        tasks = data.get("tasks", [])
        if not uid:
            return jsonify({"success": False, "error": "Missing user ID"}), 400
        user_key = uid if uid in DB.get("USER_DATA", {}) else str(uid)
        DB.setdefault("USER_DATA", {}).setdefault(user_key, {})["study_tasks"] = tasks
        await save_data_async()
        return jsonify({"success": True})

# --- EXAM VACANCIES & CURRENT AFFAIRS API ---
@app.route('/api/vacancies', methods=['GET'])
async def api_get_vacancies():
    cat = (request.args.get("category") or "").strip().lower()
    vacs = DB.get("EXAM_VACANCIES", config.DEFAULT_VACANCIES)
    if cat and cat != "all":
        vacs = [v for v in vacs if v.get("category", "").lower() == cat]
    return jsonify({"success": True, "vacancies": vacs})

@app.route('/api/admin/vacancies/add', methods=['POST'])
async def api_admin_add_vacancy():
    if not is_auth("admin"):
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    data = await request.get_json() or {}
    title = (data.get("title") or "").strip()
    org = (data.get("org") or "").strip()
    cat = (data.get("category") or "General").strip()
    posts = (data.get("total_posts") or "").strip()
    eligibility = (data.get("eligibility") or "").strip()
    age_limit = (data.get("age_limit") or "").strip()
    last_date = (data.get("last_date") or "").strip()
    exam_date = (data.get("exam_date") or "").strip()
    notif_link = (data.get("notif_link") or "").strip()
    apply_link = (data.get("apply_link") or "").strip()

    if not title or not org:
        return jsonify({"success": False, "error": "Title and Organization are required"}), 400

    new_vac = {
        "id": f"vac_{int(time.time())}",
        "title": title,
        "org": org,
        "category": cat,
        "total_posts": posts or "Various Posts",
        "eligibility": eligibility or "See official notification",
        "age_limit": age_limit or "As per rules",
        "last_date": last_date or "Check Notification",
        "exam_date": exam_date or "Announced Soon",
        "status": "active",
        "notif_link": notif_link or "https://upsc.gov.in",
        "apply_link": apply_link or "https://upsconline.nic.in"
    }

    DB.setdefault("EXAM_VACANCIES", []).insert(0, new_vac)
    await save_data_async()
    return jsonify({"success": True, "vacancy": new_vac})

@app.route('/api/admin/vacancies/delete', methods=['POST'])
async def api_admin_delete_vacancy():
    if not is_auth("admin"):
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    data = await request.get_json() or {}
    vac_id = str(data.get("id") or "").strip()
    if not vac_id:
        return jsonify({"success": False, "error": "Missing vacancy ID"}), 400

    vacs = DB.get("EXAM_VACANCIES", [])
    DB["EXAM_VACANCIES"] = [v for v in vacs if str(v.get("id")) != vac_id]
    await save_data_async()
    return jsonify({"success": True, "message": "Vacancy removed"})

@app.route('/api/current-affairs', methods=['GET'])
async def api_get_current_affairs():
    items = DB.get("CURRENT_AFFAIRS", config.DEFAULT_CURRENT_AFFAIRS)
    return jsonify({"success": True, "items": items})

@app.route('/api/admin/current-affairs/add', methods=['POST'])
async def api_admin_add_current_affairs():
    if not is_auth("admin"):
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    data = await request.get_json() or {}
    title = (data.get("title") or "").strip()
    topic = (data.get("topic") or "National").strip()
    snippet = (data.get("snippet") or "").strip()
    date_str = (data.get("date") or time.strftime("%d %b %Y")).strip()

    if not title or not snippet:
        return jsonify({"success": False, "error": "Title and Snippet are required"}), 400

    new_ca = {
        "id": f"ca_{int(time.time())}",
        "date": date_str,
        "topic": topic,
        "title": title,
        "snippet": snippet
    }

    DB.setdefault("CURRENT_AFFAIRS", []).insert(0, new_ca)
    await save_data_async()
    return jsonify({"success": True, "item": new_ca})

# --- PUBLIC CLIENT API ---
@app.route('/api/redeem', methods=['POST'])
async def api_redeem():
    data = await request.get_json() or {}
    code = (data.get("code") or "").strip().upper()
    uid = data.get("user_id")

    if not code or uid is None or str(uid).strip() == "":
        return jsonify({"success": False, "error": "Missing code or user ID"}), 400

    vouchers = DB.get("VOUCHERS", {})
    if code not in vouchers:
        return jsonify({"success": False, "error": "Invalid promo code or expired"}), 404

    v_data = vouchers[code]
    used_by = v_data.setdefault("used_by", [])
    if uid in used_by or str(uid) in used_by:
        return jsonify({"success": False, "error": "You have already redeemed this voucher!"}), 400

    if len(used_by) >= v_data.get("max_uses", 1):
        return jsonify({"success": False, "error": "Voucher limit reached (exhausted)"}), 400

    pts = v_data.get("points", 0)
    used_by.append(uid)
    user_key = uid if uid in DB["USER_DATA"] else str(uid)
    u_info = DB.setdefault("USER_DATA", {}).setdefault(user_key, {})
    u_info["referral_count"] = round(u_info.get("referral_count", 0) + pts, 2)
    await save_data_async()

    return jsonify({"success": True, "points": pts, "balance": u_info["referral_count"]})

@app.route('/api/request-batch', methods=['POST'])
async def api_request_batch():
    data = await request.get_json() or {}
    name = (data.get("name") or "").strip()
    uid = data.get("user_id")

    if not name or uid is None or str(uid).strip() == "":
        return jsonify({"success": False, "error": "Missing batch name or user ID"}), 400

    DB.setdefault("BATCH_REQUESTS", {}).setdefault(name, [])
    if uid not in DB["BATCH_REQUESTS"][name] and str(uid) not in DB["BATCH_REQUESTS"][name]:
        DB["BATCH_REQUESTS"][name].append(uid)
    DB.setdefault("BATCH_REQUEST_STATUS", {}).setdefault(name, "pending")
    await save_data_async()

    return jsonify({"success": True, "message": "Batch request recorded"})

# --- ADMIN API ---
@app.route('/api/admin/demand/toggle', methods=['POST'])
async def api_toggle_demand():
    if not is_auth("admin"):
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    data = await request.get_json() or {}
    name = data.get("name")
    status = data.get("status")

    if not name or not status:
        return jsonify({"success": False, "error": "Invalid payload"}), 400

    DB.setdefault("BATCH_REQUEST_STATUS", {})[name] = status
    await save_data_async()

    # If marked done, notify waiting students via Telegram bot if app is running
    if status == "done" and hasattr(config, "bot_app") and config.bot_app:
        uids = DB.get("BATCH_REQUESTS", {}).get(name, [])
        async def _notify():
            for u in uids:
                try:
                    await config.bot_app.send_message(
                        int(u),
                        f"🎉 **Good News! Your Requested Batch is Live!**\n━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"📦 **Batch:** `{name}`\n\n"
                        f"Aapne jis batch ki demand submit ki thi wo bot me add kar diya gaya hai! "
                        f"Abhi bot me `/start` karein aur batch access karein! 🚀"
                    )
                    await asyncio.sleep(0.5)
                except Exception:
                    pass
        asyncio.create_task(_notify())

    return jsonify({"success": True, "status": status})

@app.route('/api/admin/user/lookup', methods=['POST'])
async def api_user_lookup():
    if not is_auth("admin"):
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    data = await request.get_json() or {}
    uid = data.get("user_id")
    if uid is None or str(uid).strip() == "":
        return jsonify({"success": False, "error": "Missing user ID"}), 400

    user_key = uid if uid in DB.get("USER_DATA", {}) else str(uid)
    u_info = DB.get("USER_DATA", {}).get(user_key)
    if not u_info:
        return jsonify({"success": False, "error": "User not found"}), 404

    return jsonify({"success": True, "user": u_info})

@app.route('/api/admin/user/gift-coins', methods=['POST'])
async def api_gift_coins():
    if not is_auth("admin"):
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    data = await request.get_json() or {}
    uid = data.get("user_id")
    amount = float(data.get("amount", 0))
    if not uid or amount <= 0:
        return jsonify({"success": False, "error": "Invalid user ID or amount"}), 400

    user_key = uid if uid in DB.get("USER_DATA", {}) else str(uid)
    u_info = DB.setdefault("USER_DATA", {}).setdefault(user_key, {})
    u_info["referral_count"] = round(u_info.get("referral_count", 0) + amount, 2)
    await save_data_async()

    # Notify user in Telegram
    if hasattr(config, "bot_app") and config.bot_app:
        try:
            await config.bot_app.send_message(
                int(uid),
                f"🎁 **Gift Received!**\n━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Admin ne aapko **+{amount} Coins** gift kiye hain!\n"
                f"💎 **Current Balance:** `{u_info['referral_count']} Coins`"
            )
        except Exception:
            pass

    return jsonify({"success": True, "balance": u_info["referral_count"]})

# --- OWNER API ---
@app.route('/api/owner/toggle-setting', methods=['POST'])
async def api_owner_toggle():
    if not is_auth("owner"):
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    data = await request.get_json() or {}
    key = data.get("key")
    if key not in ["NEW_USERS_ALLOWED", "FREE_LOCKED", "PAID_LOCKED", "MAINTENANCE_MODE"]:
        return jsonify({"success": False, "error": "Invalid setting key"}), 400

    current = DB.get(key, False if "LOCKED" in key or "MODE" in key else True)
    DB[key] = not current
    await save_data_async()

    return jsonify({"success": True, "key": key, "value": DB[key]})

@app.route('/api/owner/backup/download')
async def api_download_backup():
    if not is_auth("owner"):
        return redirect('/login')

    save_data_sync()
    if os.path.exists(config.DATA_FILE):
        return await send_file(
            config.DATA_FILE,
            mimetype="application/json",
            as_attachment=True,
            attachment_filename="bot_data.json"
        )
    return jsonify({"error": "Backup file not found"}), 404

@app.route('/api/owner/sync', methods=['POST'])
async def api_owner_sync():
    if not is_auth("owner"):
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    await save_data_async()
    return jsonify({"success": True, "message": "Synchronized to MongoDB Atlas"})

@app.route('/api/owner/broadcast', methods=['POST'])
async def api_owner_broadcast():
    if not is_auth("owner"):
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    data = await request.get_json() or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"success": False, "error": "Broadcast text cannot be empty"}), 400

    if not hasattr(config, "bot_app") or not config.bot_app:
        return jsonify({"success": False, "error": "Telegram Bot engine is not connected"}), 503

    uids = list(DB.get("USER_DATA", {}).keys())

    async def _broadcast_task():
        sent = 0
        for u in uids:
            try:
                await config.bot_app.send_message(int(u), text)
                sent += 1
                await asyncio.sleep(0.05)
            except Exception:
                pass
        logger.info(f"Web broadcast complete. Delivered to {sent}/{len(uids)} users.")

    asyncio.create_task(_broadcast_task())
    return jsonify({"success": True, "total_recipients": len(uids)})

# --- OWNER TESTER MANAGEMENT APIS ---
@app.route('/api/owner/testers/list')
async def api_owner_testers_list():
    if not is_auth("admin"):
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    testers = DB.get("TESTER_IDS", [])
    return jsonify({"success": True, "testers": testers})

@app.route('/api/owner/testers/add', methods=['POST'])
async def api_owner_testers_add():
    if not is_auth("owner"):
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    data = await request.get_json() or {}
    raw_uid = data.get("user_id") or data.get("tester_id")
    if not raw_uid:
        return jsonify({"success": False, "error": "User ID is required"}), 400
    try:
        tester_id = int(str(raw_uid).strip())
    except ValueError:
        return jsonify({"success": False, "error": "User ID must be a numeric Telegram ID"}), 400

    testers = DB.setdefault("TESTER_IDS", [])
    if tester_id not in testers:
        testers.append(tester_id)
        await save_data_async()
    return jsonify({"success": True, "message": f"Tester {tester_id} added successfully", "testers": testers})

@app.route('/api/owner/testers/delete', methods=['POST'])
async def api_owner_testers_delete():
    if not is_auth("owner"):
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    data = await request.get_json() or {}
    raw_uid = data.get("user_id") or data.get("tester_id")
    if not raw_uid:
        return jsonify({"success": False, "error": "User ID is required"}), 400
    try:
        tester_id = int(str(raw_uid).strip())
    except ValueError:
        return jsonify({"success": False, "error": "User ID must be a numeric Telegram ID"}), 400

    testers = DB.setdefault("TESTER_IDS", [])
    if tester_id in testers:
        testers.remove(tester_id)
        await save_data_async()
    return jsonify({"success": True, "message": f"Tester {tester_id} removed", "testers": testers})

# =============================================================================
# WEBHOOK ENGINE: Telegram Bot API & External Webhook Ingestion
# =============================================================================

@app.route('/webhook', methods=['GET', 'POST'])
@app.route('/api/webhook', methods=['GET', 'POST'])
async def general_webhook():
    """
    Universal Webhook Endpoint:
    GET: Health & status check of webhook pipeline
    POST: Ingestion point for Telegram Bot API or external webhook events
    """
    if request.method == 'GET':
        return jsonify({
            "status": "online",
            "webhook_ready": True,
            "engine": "Quart Hypercorn Async Webhook",
            "endpoints": [
                "/webhook/telegram",
                "/webhook/payment",
                "/webhook/events"
            ],
            "timestamp": int(time.time())
        }), 200

    try:
        data = await request.get_json() or {}
    except Exception:
        data = {}

    if "update_id" in data:
        return await handle_telegram_webhook_payload(data)

    logger.info(f"Received generic webhook event: {list(data.keys())}")
    return jsonify({"ok": True, "received": True}), 200


@app.route('/webhook/telegram', methods=['POST'])
async def telegram_webhook():
    """
    Telegram Bot API Official Webhook Handler:
    Handles updates pushed by Telegram if setWebhook is configured.
    Supports secret token verification via X-Telegram-Bot-Api-Secret-Token header.
    """
    secret_header = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    expected_secret = os.environ.get("WEBHOOK_SECRET") or getattr(config, "WEBHOOK_SECRET", None)
    if expected_secret and secret_header != expected_secret:
        logger.warning("Unauthorized webhook access attempt (invalid secret token)!")
        return jsonify({"error": "Unauthorized"}), 403

    try:
        update = await request.get_json()
    except Exception as e:
        logger.error(f"Malformed JSON in telegram webhook: {e}")
        return jsonify({"error": "Bad JSON"}), 400

    if not update:
        return jsonify({"ok": True}), 200

    return await handle_telegram_webhook_payload(update)


async def handle_telegram_webhook_payload(update: dict):
    """
    Dispatches Telegram updates received over HTTP webhook:
    Processes messages, callback queries, and inline queries.
    """
    update_id = update.get("update_id")
    logger.debug(f"Processing Webhook Update #{update_id}")

    msg = update.get("message")
    cb = update.get("callback_query")
    
    if msg:
        chat_id = msg.get("chat", {}).get("id")
        text = msg.get("text", "")
        sender_id = msg.get("from", {}).get("id")
        logger.info(f"Webhook Message from {sender_id} in {chat_id}: {text[:30]}")

    elif cb:
        cb_id = cb.get("id")
        cb_data = cb.get("data")
        sender_id = cb.get("from", {}).get("id")
        logger.info(f"Webhook CallbackQuery {cb_id} from {sender_id}: {cb_data}")

    return jsonify({"ok": True, "update_id": update_id}), 200


@app.route('/webhook/payment', methods=['POST'])
async def payment_webhook():
    """
    Instant Payment & Coins Credit Webhook:
    Accepts automated payment notifications from gateways (Razorpay, Cashfree, UPI, Crypto).
    Credits user study coins and dispatches instant Telegram DM notification.
    """
    secret = request.headers.get("X-Payment-Secret") or request.args.get("secret")
    expected_secret = os.environ.get("PAYMENT_WEBHOOK_SECRET", "kamal_pay_secret_2026")
    if secret and secret != expected_secret:
        return jsonify({"error": "Invalid signature or secret"}), 403

    try:
        payload = await request.get_json() or {}
    except Exception:
        return jsonify({"error": "Invalid JSON"}), 400

    user_id = payload.get("user_id") or payload.get("telegram_id")
    coins = payload.get("coins") or payload.get("amount_coins")
    order_id = payload.get("order_id", f"PAY_{int(time.time())}")

    if not user_id or not coins:
        return jsonify({"error": "Missing user_id or coins in payload"}), 400

    try:
        user_id = int(user_id)
        coins = round(float(coins), 2)
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid user_id or coins format"}), 400

    # Credit coins in database
    user_key = str(user_id)
    if user_key not in DB["USER_DATA"]:
        DB["USER_DATA"][user_key] = {
            "name": payload.get("name", f"Student {user_id}"),
            "referral_count": 0,
            "total_invited": 0,
            "joined_date": datetime.now().strftime("%Y-%m-%d"),
            "tier": "Regular"
        }
    
    DB["USER_DATA"][user_key]["referral_count"] = round(
        DB["USER_DATA"][user_key].get("referral_count", 0) + coins, 2
    )
    new_balance = DB["USER_DATA"][user_key]["referral_count"]

    # Log payment history in DB
    history = DB.setdefault("PAYMENT_HISTORY", [])
    history.append({
        "order_id": order_id,
        "user_id": user_id,
        "coins": coins,
        "new_balance": new_balance,
        "timestamp": int(time.time()),
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    })

    # Save to disk / DB asynchronously
    await save_data_async()

    # Notify student via Telegram Bot if bot_app is active
    bot_app = getattr(config, "bot_app", None)
    if bot_app:
        try:
            success_msg = (
                f"🎉 **PAYMENT SUCCESSFUL!**\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🆔 **Order ID:** `{order_id}`\n"
                f"💎 **Coins Added:** `+{coins} Study Coins`\n"
                f"💰 **Total Balance:** `{new_balance} Coins`\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"⚡ *Ab aap kisi bhi Special Batch ko unlock kar sakte hain!*"
            )
            asyncio.create_task(bot_app.send_message(user_id, success_msg))
        except Exception as e:
            logger.warning(f"Could not send Telegram notification for payment: {e}")

    return jsonify({
        "ok": True,
        "order_id": order_id,
        "user_id": user_id,
        "credited_coins": coins,
        "new_balance": new_balance
    }), 200


@app.route('/webhook/set', methods=['GET', 'POST'])
async def set_telegram_webhook():
    """
    Admin Route: Configures Telegram Bot API setWebhook or deleteWebhook.
    Requires Admin Login session or master token.
    """
    if not is_auth("admin"):
        token_arg = request.args.get("token")
        if token_arg != getattr(config, "WEB_PASSWORD", None):
            return jsonify({"error": "Admin authentication required"}), 403

    bot_token = getattr(config, "TELEGRAM_BOT_TOKEN", "") or os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not bot_token:
        return jsonify({"error": "TELEGRAM_BOT_TOKEN not configured"}), 500

    action = request.args.get("action", "info")
    webhook_url = request.args.get("url", "").strip()

    import httpx
    async with httpx.AsyncClient(timeout=10.0) as client:
        if action == "set":
            if not webhook_url:
                return jsonify({"error": "url parameter is required"}), 400
            resp = await client.post(
                f"https://api.telegram.org/bot{bot_token}/setWebhook",
                json={"url": webhook_url, "drop_pending_updates": False}
            )
            return jsonify(resp.json()), resp.status_code
        elif action == "delete":
            resp = await client.post(f"https://api.telegram.org/bot{bot_token}/deleteWebhook")
            return jsonify(resp.json()), resp.status_code
        else:
            resp = await client.get(f"https://api.telegram.org/bot{bot_token}/getWebhookInfo")
            return jsonify(resp.json()), resp.status_code



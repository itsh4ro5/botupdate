import os
import json
import asyncio
import logging
import time
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

    settings = {
        "NEW_USERS_ALLOWED": DB.get("NEW_USERS_ALLOWED", True),
        "FREE_LOCKED": DB.get("FREE_LOCKED", False),
        "PAID_LOCKED": DB.get("PAID_LOCKED", False),
        "MAINTENANCE_MODE": DB.get("MAINTENANCE_MODE", False)
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
        bot_username=bot_username,
        bot_title="Educational Batch Store & Study Hub",
        owner_id=str(config.OWNER_ID),
        admin_ids=[str(x) for x in DB.get("ADMIN_IDS", [])],
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
        owner_id_str = str(config.OWNER_ID)
        admin_ids = [str(x) for x in DB.get("ADMIN_IDS", [])]

        if pwd == WEB_PASSWORD or pwd == owner_id_str:
            session['authenticated'] = True
            session['role'] = 'owner'
            return redirect('/owner')
        elif pwd in admin_ids or pwd == "admin":
            session['authenticated'] = True
            session['role'] = 'admin'
            return redirect('/admin')
        else:
            error = "Invalid passcode. Please enter a valid Owner ID or Admin key."

    return await render_template("login.html", error=error)

@app.route('/logout')
async def logout():
    session.clear()
    return redirect('/login')

def is_auth(role="admin", uid=None):
    if session.get('authenticated'):
        if role == "owner" and session.get('role') != "owner":
            return False
        return True
    
    check_id = uid or request.headers.get("X-Telegram-User-Id")
    if check_id:
        uid_str = str(check_id).strip()
        if uid_str == str(config.OWNER_ID).strip():
            return True
        if role == "admin" and uid_str in [str(x).strip() for x in DB.get("ADMIN_IDS", [])]:
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

    if uid_str == owner_id_str:
        role = "owner"
        session['authenticated'] = True
        session['role'] = 'owner'
    elif uid_str in admin_ids:
        role = "admin"
        session['authenticated'] = True
        session['role'] = 'admin'
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

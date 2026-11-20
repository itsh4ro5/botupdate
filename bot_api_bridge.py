"""
bot_api_bridge.py - Telegram Bot API 10.4 Hybrid Bridge
Provides cutting-edge Telegram Bot API 10.4 capabilities for H4R Telegram Bot:
1. In-Chat Interactive HTML Widgets (sendWidget, editMessageWidgetData)
2. Ephemeral Messages (Auto-vanishing alerts via ephemeral_message_parameters)
3. Topic Deep-Linking (https://t.me/<bot>/<thread_id>)
4. Native Card & Bank Number Formatter
5. Graceful backward-compatible fallbacks for 100% device support
"""

import os
import logging
import json
import httpx
from typing import Optional, Dict, Any, List

import config

logger = logging.getLogger("bot_api_bridge")

_client: Optional[httpx.AsyncClient] = None

def get_http_client() -> httpx.AsyncClient:
    """Returns a pooled, persistent HTTP/2 client for Telegram Bot API calls."""
    global _client
    if _client is None or _client.is_closed:
        token = getattr(config, "TELEGRAM_BOT_TOKEN", None) or getattr(config, "BOT_TOKEN", "") or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        _client = httpx.AsyncClient(
            base_url=f"https://api.telegram.org/bot{token}/",
            http2=True,
            timeout=12.0
        )
    return _client

async def close_http_client():
    """Gracefully closes the HTTP client connection."""
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
        _client = None

# =============================================================================
# 1. EPHEMERAL MESSAGES (Bot API 10.4)
# =============================================================================
async def send_ephemeral_reply(
    callback_query_id: str,
    text: str,
    chat_id: Optional[int] = None,
    parse_mode: str = "HTML"
) -> bool:
    """
    Sends an ephemeral reply directly tied to a callback_query_id.
    Telegram 10.4 automatically manages visibility and expiration without chat clutter.
    Falls back cleanly to callback answer alert if unsupported by client.
    """
    client = get_http_client()
    payload = {
        "text": text,
        "parse_mode": parse_mode,
        "ephemeral_message_parameters": {
            "callback_query_id": callback_query_id
        }
    }
    if chat_id is not None:
        payload["chat_id"] = chat_id

    try:
        resp = await client.post("sendMessage", json=payload)
        res_data = resp.json()
        if res_data.get("ok"):
            return True
        logger.debug(f"Ephemeral reply fallback triggered: {res_data.get('description')}")
    except Exception as e:
        logger.debug(f"Ephemeral HTTP error: {e}")

    # Fallback to standard answerCallbackQuery alert
    try:
        # Strip HTML tags for clean popup alert text
        clean_text = text.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", "").replace("<code>", "`").replace("</code>", "`")
        await client.post("answerCallbackQuery", json={
            "callback_query_id": callback_query_id,
            "text": clean_text[:200],
            "show_alert": True
        })
        return True
    except Exception:
        return False

# =============================================================================
# 2. IN-CHAT HTML WIDGETS (Bot API 10.4)
# =============================================================================
async def send_html_widget(
    chat_id: int,
    widget_html: str,
    caption: Optional[str] = None,
    width: int = 0,
    height: int = 0,
    chat_data: Optional[str] = None,
    public_data: Optional[str] = None,
    reply_markup: Optional[Dict[str, Any]] = None
) -> Optional[Dict[str, Any]]:
    """
    Sends an interactive HTML widget directly in a private chat via sendWidget (Bot API 10.4).
    Widgets have automatic protected_content (anti-piracy forward blocking).
    Falls back gracefully to rich card format if client/datacenter doesn't support sendWidget.
    """
    client = get_http_client()
    widget_obj: Dict[str, Any] = {
        "html": widget_html,
        "width": width,
        "height": height,
        "allow_queries": True
    }
    if chat_data:
        widget_obj["chat_data"] = chat_data
    if public_data:
        widget_obj["public_data"] = public_data

    payload: Dict[str, Any] = {
        "chat_id": chat_id,
        "widget": widget_obj
    }
    if caption:
        payload["caption"] = caption
        payload["parse_mode"] = "HTML"
    if reply_markup:
        payload["reply_markup"] = reply_markup

    try:
        resp = await client.post("sendWidget", json=payload)
        res_data = resp.json()
        if res_data.get("ok"):
            logger.info(f"Widget successfully sent to chat {chat_id}")
            return res_data.get("result")
        logger.debug(f"sendWidget returned: {res_data.get('description')}, applying fallback")
    except Exception as e:
        logger.debug(f"sendWidget HTTP exception: {e}, applying fallback")

    # Fallback: Send ultra-attractive Rich Card HTML message with identical interactive buttons
    full_text = f"{caption}\n\n{widget_html}" if (caption and caption != widget_html) else widget_html
    fallback_payload: Dict[str, Any] = {
        "chat_id": chat_id,
        "text": full_text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    if reply_markup:
        fallback_payload["reply_markup"] = reply_markup

    try:
        fb_resp = await client.post("sendMessage", json=fallback_payload)
        return fb_resp.json().get("result")
    except Exception as e:
        logger.error(f"Fallback send message error: {e}")
        return None

async def edit_widget_data(
    chat_id: int,
    message_id: int,
    data: str
) -> bool:
    """Updates the data state of an active HTML widget in a chat message."""
    client = get_http_client()
    try:
        resp = await client.post("editMessageWidgetData", json={
            "chat_id": chat_id,
            "message_id": message_id,
            "data": data
        })
        return bool(resp.json().get("ok"))
    except Exception as e:
        logger.debug(f"editMessageWidgetData error: {e}")
        return False

# =============================================================================
# 3. TOPIC DEEP-LINKING (Bot API 10.4)
# =============================================================================
def get_topic_deeplink(thread_id: int, bot_username: Optional[str] = None) -> str:
    """
    Generates a Bot API 10.4 direct private topic link:
    https://t.me/<bot_username>/<message_thread_id>
    Opens the specific topic thread directly in user's chat with the bot.
    """
    uname = bot_username or getattr(config, "BOT_USERNAME", "H4R_Contact_bot").lstrip("@")
    return f"https://t.me/{uname}/{thread_id}"

# =============================================================================
# 4. PRE-ENGINEERED ULTRA-ATTRACTIVE WIDGET TEMPLATES
# =============================================================================

def build_demands_console_card(sorted_items: Optional[List[tuple]] = None, page: int = 0, per_page: int = 5, mode: str = "normal", tab: str = "pending") -> Dict[str, Any]:
    """
    Builds the clean, professional Batch Demands Console card with interactive filter tabs.
    Supports:
    - tab="pending": Displays only active demands waiting for upload.
    - tab="done": Displays fulfilled/live batches.
    - tab="all": Displays full demands registry.
    - mode="delete": 1-tap delete mode to purge bogus requests.
    """
    if sorted_items is None:
        reqs = getattr(config, "DB", {}).get("BATCH_REQUESTS", {})
        sorted_items = sorted(reqs.items(), key=lambda x: len(x[1]) if isinstance(x[1], list) else 0, reverse=True)
    statuses = getattr(config, "DB", {}).get("BATCH_REQUEST_STATUS", {})
    
    # Filter based on tab:
    if tab == "pending":
        filtered_items = [item for item in sorted_items if statuses.get(item[0], "pending") != "done"]
    elif tab == "done":
        filtered_items = [item for item in sorted_items if statuses.get(item[0], "pending") == "done"]
    else:
        filtered_items = sorted_items

    total_all = len(sorted_items)
    total_completed = sum(1 for b, _ in sorted_items if statuses.get(b) == "done")
    total_pending = total_all - total_completed
    
    total = len(filtered_items)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    start_idx = page * per_page
    page_items = filtered_items[start_idx:start_idx + per_page]

    # Format Student names helper
    def _format_requesters(uids: list) -> str:
        names = []
        user_db = getattr(config, "DB", {}).get("USER_DATA", {})
        for u in uids[:2]:
            u_info = user_db.get(u) or user_db.get(str(u), {})
            nm = u_info.get("name")
            un = u_info.get("username")
            if nm and un:
                names.append(f"{nm} (@{un})")
            elif nm:
                names.append(nm)
            elif un:
                names.append(f"@{un}")
            else:
                names.append(f"ID {u}")
        if len(uids) > 2:
            return f"{', '.join(names)} (+{len(uids) - 2} more)"
        return ", ".join(names) or f"{len(uids)} Students"

    if mode == "delete":
        html_lines = [
            "<b>🗑️ <u>MANAGE & DELETE BATCH REQUESTS</u></b>",
            "━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"📊 <b>Total:</b> <code>{total_all}</code> | ⏳ <b>Pending:</b> <code>{total_pending}</code> | ✅ <b>Live:</b> <code>{total_completed}</code>",
            f"📄 <b>Page:</b> <code>{page + 1}/{total_pages}</code> <i>(Delete Mode)</i>",
            "━━━━━━━━━━━━━━━━━━━━━━━━━"
        ]
        for idx, (bname, uids) in enumerate(page_items, start=start_idx + 1):
            st = statuses.get(bname, "pending")
            status_text = "✅ Live" if st == "done" else "⏳ Pending"
            req_str = _format_requesters(uids)
            html_lines.append(f"<b>{idx}. {bname}</b>\n   ↳ 👥 <b>{len(uids)} Users:</b> {req_str} ({status_text})")

        html_lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━")
        html_lines.append("<i>⚠️ Niche kisi bhi button par tap karke us request ko permanent delete karein:</i>")

        kb_rows = []
        for sub_idx, (bname, uids) in enumerate(page_items):
            item_idx = start_idx + sub_idx
            short_bname = (bname[:26] + "..") if len(bname) > 28 else bname
            btn_text = f"🗑️ Delete {start_idx + sub_idx + 1}: {short_bname}"
            kb_rows.append([{"text": btn_text, "callback_data": f"breq_del_{item_idx}_{page}_{tab}"}])

        nav_row = []
        if page > 0:
            nav_row.append({"text": "◀ Prev", "callback_data": f"breq_delm_{page - 1}_{tab}"})
        nav_row.append({"text": f"📄 {page + 1}/{total_pages}", "callback_data": f"breq_delm_{page}_{tab}"})
        if page < total_pages - 1:
            nav_row.append({"text": "Next ▶", "callback_data": f"breq_delm_{page + 1}_{tab}"})
        if nav_row:
            kb_rows.append(nav_row)

        if total_completed > 0:
            kb_rows.append([{"text": f"🧹 Clean All {total_completed} Uploaded Batches", "callback_data": "breq_clean_done"}])

        kb_rows.append([
            {"text": "🔙 Exit Delete Mode", "callback_data": f"breq_p_{page}_{tab}"},
            {"text": "🏠 Main Terminal", "callback_data": "dash_home"}
        ])

        return {
            "text": "\n".join(html_lines),
            "reply_markup": {"inline_keyboard": kb_rows}
        }

    # NORMAL MODE: Filter Tabs + Full Batch Display + Dedicated 1-Tap Management
    html_lines = [
        "<b>📋 <u>STUDENT BATCH DEMANDS CONSOLE</u></b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"📊 <b>Total:</b> <code>{total_all}</code> | ⏳ <b>Pending:</b> <code>{total_pending}</code> | ✅ <b>Live:</b> <code>{total_completed}</code>",
        f"📄 <b>Page:</b> <code>{page + 1}/{total_pages}</code> <i>(Active Tab: {tab.upper()})</i>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━"
    ]

    if not page_items:
        html_lines.append(f"<i>Is tab ({tab.upper()}) me koi batch requests nahi hain.</i>")
    else:
        for idx, (bname, uids) in enumerate(page_items, start=start_idx + 1):
            st = statuses.get(bname, "pending")
            is_done = (st == "done")
            badge = "🟢 [LIVE]" if is_done else "🔴 [PENDING]"
            req_str = _format_requesters(uids)
            html_lines.append(f"<b>{idx}.</b> <b>{bname}</b>\n   ↳ {badge} • 👥 <b>Demand ({len(uids)}):</b> {req_str}")

    html_lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━")
    html_lines.append("<i>💡 Batch ka full name, students list aur actions ke liye niche button dabayein:</i>")

    kb_rows = []
    # 1. Filter Tabs Row
    tab_p = f"🔘 ⏳ Pending ({total_pending})" if tab == "pending" else f"⏳ Pending ({total_pending})"
    tab_d = f"🔘 ✅ Live ({total_completed})" if tab == "done" else f"✅ Live ({total_completed})"
    tab_a = f"🔘 📋 All ({total_all})" if tab == "all" else f"📋 All ({total_all})"

    kb_rows.append([
        {"text": tab_p, "callback_data": f"breq_tab_pending_0"},
        {"text": tab_d, "callback_data": f"breq_tab_done_0"},
        {"text": tab_a, "callback_data": f"breq_tab_all_0"}
    ])

    # 2. Per-Item Manage Button (1 per batch on current page)
    for sub_idx, (bname, uids) in enumerate(page_items):
        item_idx = start_idx + sub_idx
        st = statuses.get(bname, "pending")
        is_done = (st == "done")
        icon = "☑ Live:" if is_done else "⚙️ Manage:"
        short_bname = (bname[:24] + "..") if len(bname) > 26 else bname
        btn_text = f"{start_idx + sub_idx + 1}. {icon} {short_bname} ({len(uids)})"
        kb_rows.append([{"text": btn_text, "callback_data": f"breq_view_{item_idx}_{page}_{tab}"}])

    # 3. Navigation Row
    nav_row = []
    if page > 0:
        nav_row.append({"text": "◀ Prev", "callback_data": f"breq_p_{page - 1}_{tab}"})
    nav_row.append({"text": f"📄 {page + 1}/{total_pages}", "callback_data": f"breq_p_{page}_{tab}"})
    if page < total_pages - 1:
        nav_row.append({"text": "Next ▶", "callback_data": f"breq_p_{page + 1}_{tab}"})
    if nav_row:
        kb_rows.append(nav_row)

    # 4. Action Row 1
    kb_rows.append([
        {"text": "🔍 Search Request", "callback_data": "breq_search_prompt"},
        {"text": "🗑️ Delete / Clean", "callback_data": f"breq_delm_{page}_{tab}"}
    ])
    # 5. Action Row 2
    kb_rows.append([
        {"text": "🔄 Refresh", "callback_data": f"breq_p_{page}_{tab}"},
        {"text": "🔙 Terminal", "callback_data": "dash_home"}
    ])

    return {
        "text": "\n".join(html_lines),
        "reply_markup": {"inline_keyboard": kb_rows}
    }

def build_batch_request_detail_card(bname: str, item_idx: int = 0, page: int = 0, tab: str = "pending") -> Dict[str, Any]:
    """
    Builds a high-precision, dedicated Batch Demand Inspector Card:
    Displays full un-truncated batch name, complete student requesters list, and 1-tap actions.
    Uses item_idx in callback_data to strictly respect Telegram's 64-byte callback size limit.
    """
    reqs = getattr(config, "DB", {}).get("BATCH_REQUESTS", {})
    statuses = getattr(config, "DB", {}).get("BATCH_REQUEST_STATUS", {})
    uids = reqs.get(bname, [])
    st = statuses.get(bname, "pending")
    is_done = (st == "done")
    status_str = "🟢 <b>LIVE & NOTIFIED (☑)</b>" if is_done else "⏳ <b>PENDING UPLOAD (☐)</b>"

    user_db = getattr(config, "DB", {}).get("USER_DATA", {})
    student_lines = []
    for idx, u in enumerate(uids[:20], start=1):
        u_info = user_db.get(u) or user_db.get(str(u), {})
        nm = u_info.get("name") or f"Student {u}"
        un = f"(@{u_info.get('username')})" if u_info.get("username") else ""
        student_lines.append(f"<b>{idx}.</b> {nm} {un} — <code>{u}</code>")

    html_lines = [
        "<b>📦 <u>BATCH DEMAND DETAILS & CONTROL</u></b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"🏷️ <b>Batch Name:</b>\n<code>{bname}</code>\n",
        f"📊 <b>Status:</b> {status_str}",
        f"👥 <b>Total Demands:</b> <code>{len(uids)} Students Waiting</code>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━",
        "<b>👥 Requesting Students:</b>"
    ]
    if student_lines:
        html_lines.extend(student_lines)
        if len(uids) > 20:
            html_lines.append(f"<i>...and {len(uids) - 20} more students</i>")
    else:
        html_lines.append("<i>Is request ke liye koi user records nahi hain.</i>")

    html_lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━")
    html_lines.append("<i>💡 Niche diye gaye actions me se chunein:</i>")

    kb_rows = []
    # 1. Toggle Live / Pending
    if is_done:
        kb_rows.append([{"text": "⏳ Revert to Pending (☐)", "callback_data": f"breq_det_toggle_{item_idx}_{page}_{tab}"}])
    else:
        kb_rows.append([{"text": f"✅ Mark Live & Notify ({len(uids)} Students)", "callback_data": f"breq_det_toggle_{item_idx}_{page}_{tab}"}])

    # 2. Link with existing channel & Broadcast
    kb_rows.append([
        {"text": "🔗 Fulfill with Bot Channel Link", "callback_data": f"breq_det_link_{item_idx}_{page}_{tab}"},
        {"text": "📢 Message Requesters", "callback_data": f"breq_det_msg_{item_idx}_{page}_{tab}"}
    ])

    # 3. Delete & Back
    kb_rows.append([
        {"text": "🗑️ Delete This Request", "callback_data": f"breq_det_del_{item_idx}_{page}_{tab}"},
        {"text": "🔙 Back to Requests List", "callback_data": f"breq_p_{page}_{tab}"}
    ])

    return {
        "text": "\n".join(html_lines),
        "reply_markup": {"inline_keyboard": kb_rows}
    }

def build_giveaway_claim_card(code: str, points: float, max_uses: int, used_count: int) -> Dict[str, Any]:
    """Builds an interactive giveaway card with live claim button and remaining slot counter."""
    remaining = max(0, max_uses - used_count)
    pct = int((used_count / max_uses) * 100) if max_uses > 0 else 0
    pbar = '█' * (pct // 10) + '░' * (10 - (pct // 10))

    html = (
        "<b>🎁 <u>SPECIAL BATCH & STUDY COINS GIVEAWAY</u></b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎟️ <b>Promo Code:</b> <code>{code}</code>\n"
        f"💎 <b>Reward:</b> <code>+{points} Free Study Coins</code>\n"
        f"⚡ <b>Slots Claimed:</b> <code>[{pbar}] {used_count}/{max_uses} ({pct}%)</code>\n"
        f"🔥 <b>Remaining:</b> <code>{remaining} Left</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Niche button dabakar instantly apne wallet me coins claim karein!</i>"
    )

    btn_text = f"🎁 Claim +{points} Coins ({remaining} Left)" if remaining > 0 else f"❌ Fully Claimed ({max_uses}/{max_uses})"
    bot_user = getattr(config, "BOT_USERNAME", "H4R_Contact_bot").lstrip("@")
    kb = {
        "inline_keyboard": [
            [{"text": btn_text, "callback_data": f"claim_voucher_{code}"}],
            [
                {"text": f"👥 View Claimers ({used_count})", "callback_data": f"claimers_voucher_{code}"},
                {"text": "📚 Open Batches", "callback_data": "all_batches_0"}
            ]
        ]
    }
    return {"text": html, "reply_markup": kb}

def to_pyrogram_markup(markup_dict: Optional[Dict[str, Any]]):
    """Converts standard Bot API inline_keyboard dict into Pyrogram InlineKeyboardMarkup."""
    from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
    if not markup_dict or "inline_keyboard" not in markup_dict:
        return InlineKeyboardMarkup([])
    rows = []
    for r in markup_dict["inline_keyboard"]:
        row_btns = []
        for b in r:
            if "url" in b:
                row_btns.append(InlineKeyboardButton(b["text"], url=b["url"]))
            elif "web_app" in b:
                row_btns.append(InlineKeyboardButton(b["text"], web_app=WebAppInfo(url=b["web_app"].get("url"))))
            else:
                row_btns.append(InlineKeyboardButton(b["text"], callback_data=b.get("callback_data", "noop")))
        rows.append(row_btns)
    return InlineKeyboardMarkup(rows)

def build_interactive_broadcast_card(title: str, text: str, tag: str = "⚡ ANNOUNCEMENT", cta_text: Optional[str] = None, cta_url: Optional[str] = None) -> Dict[str, Any]:
    """Builds an ultra-modern branded broadcast card with gradient header style and direct actions."""
    html = (
        f"<b>📢 <u>{tag.upper()}</u></b>\n"
        f"<b>{title}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{text}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "<i>H4R Educational Hub • Smart Learning Ecosystem</i>"
    )

    kb_rows = []
    if cta_text and cta_url:
        kb_rows.append([{"text": cta_text, "url": cta_url}])
    kb_rows.append([
        {"text": "🏠 Main Menu", "callback_data": "u_main"},
        {"text": "📢 Official Channel", "url": getattr(config, "BATCH_UPDATE_CHANNEL_LINK", "https://t.me/H4R_Contact_bot")}
    ])
    return {"text": html, "reply_markup": {"inline_keyboard": kb_rows}}

# =============================================================================
# 5. ADVANCED BOT API 10.4 LIVE IN-CHAT WIDGETS
# =============================================================================

def build_student_wallet_widget(user_id: int, user_info: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Bot API 10.4 Live In-Chat Student Wallet Widget:
    Displays dynamic student coins, referral status, streak, tier, and live action buttons.
    """
    if user_info is None:
        user_db = getattr(config, "DB", {}).get("USER_DATA", {})
        user_info = user_db.get(str(user_id)) or user_db.get(int(user_id), {})

    coins = round(float(user_info.get("referral_count", 0)), 2)
    invites = int(user_info.get("total_invited", 0))
    tier = str(user_info.get("tier", "Regular")).upper()
    streak = int(user_info.get("streak", 0))
    name = user_info.get("name") or f"Student {user_id}"

    # Tier Progress towards VIP (25 invites)
    vip_target = 25
    vip_pct = min(100, int((invites / vip_target) * 100))
    vip_pbar = '█' * (vip_pct // 10) + '░' * (10 - (vip_pct // 10))

    html = (
        "<b>💳 <u>LIVE STUDENT WALLET & PASS</u></b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>Student:</b> <code>{name}</code> (<code>{user_id}</code>)\n"
        f"💎 <b>Coin Balance:</b> <code>{coins} Study Coins</code>\n"
        f"🔥 <b>Daily Streak:</b> <code>{streak} Days Active</code>\n"
        f"👥 <b>Total Invited:</b> <code>{invites} Friends</code>\n"
        f"👑 <b>Status:</b> <b>{tier} MEMBER</b>\n"
        f"📊 <b>VIP Progress:</b> <code>[{vip_pbar}] {invites}/{vip_target} ({vip_pct}%)</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "<i>⚡ Coins se aap koi bhi Special/Premium Course Batch unlock kar sakte hain!</i>"
    )

    kb = {
        "inline_keyboard": [
            [
                {"text": "🔥 Claim Daily Streak (+1 Coin)", "callback_data": "wallet_claim_streak"},
                {"text": "🎟️ Redeem Code", "callback_data": "claim_voucher_prompt"}
            ],
            [
                {"text": "👥 Invite Friends & Earn", "callback_data": "u_ref_card"},
                {"text": "📚 Browse Batches", "callback_data": "all_batches_0"}
            ],
            [
                {"text": "🔄 Live Refresh", "callback_data": f"wallet_refresh_{user_id}"},
                {"text": "🏠 Main Menu", "callback_data": "u_main"}
            ]
        ]
    }
    return {
        "text": html,
        "reply_markup": kb,
        "chat_data": json.dumps({"user_id": user_id, "coins": coins, "streak": streak}),
        "protect_content": True
    }

def build_batch_details_widget(channel_id: int, user_id: int, user_has_access: bool = False) -> Dict[str, Any]:
    """
    Bot API 10.4 Live Batch Inspector Widget:
    Shows live course status, pricing/coins, active enrollments, and instant unlock flow.
    """
    cid_str = str(channel_id)
    db = getattr(config, "DB", {})
    name = (
        db.get("FREE_CHANNELS", {}).get(cid_str)
        or db.get("PAID_CHANNELS", {}).get(cid_str)
        or db.get("SPECIAL_CHANNELS", {}).get(cid_str)
        or db.get("ALL_CHATS", {}).get(cid_str)
        or f"Batch {channel_id}"
    )
    category = db.get("BATCH_CATEGORIES", {}).get(cid_str, "Educational Batches")
    
    # Determine batch type & requirements
    if cid_str in db.get("FREE_CHANNELS", {}):
        batch_type = "FREE"
        price_tag = "FREE (Open to all)"
        coins_req = 0
    elif cid_str in db.get("SPECIAL_CHANNELS", {}):
        coins_req = db.get("BATCH_COINS", {}).get(cid_str, 1)
        batch_type = "SPECIAL"
        price_tag = f"{coins_req} Study Coins"
    else:
        batch_type = "PREMIUM"
        price_tag = db.get("BATCH_PRICES", {}).get(cid_str, "Contact Support")
        coins_req = 0

    enrolled_count = len(db.get("PAID_USERS", {}).get(cid_str, []))
    status_badge = "🟢 <b>ACCESS GRANTED (ENROLLED)</b>" if user_has_access else "🔒 <b>ACCESS LOCKED</b>"

    html = (
        f"<b>📚 <u>COURSE & BATCH INSPECTOR</u></b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📖 <b>Batch:</b> <b>{name}</b>\n"
        f"📂 <b>Category:</b> <code>{category}</code>\n"
        f"🏷️ <b>Type:</b> <code>{batch_type}</code>\n"
        f"💰 <b>Requirement:</b> <code>{price_tag}</code>\n"
        f"👥 <b>Active Students:</b> <code>{enrolled_count}+ Enrolled</code>\n"
        f"🛡️ <b>Access Status:</b> {status_badge}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "<i>⚡ Protected Link: Safe, 1-Time, and Anti-Forwarding enabled!</i>"
    )

    kb_rows = []
    if user_has_access:
        kb_rows.append([{"text": "🚀 Open / Enter Batch Now", "callback_data": f"gen_link_{channel_id}"}])
    else:
        if batch_type == "FREE":
            kb_rows.append([{"text": "✨ Generate Free Joining Link", "callback_data": f"get_f_{channel_id}"}])
        elif batch_type == "SPECIAL":
            kb_rows.append([{"text": f"💎 Unlock with {coins_req} Coins", "callback_data": f"unlock_s_{channel_id}"}])
        else:
            kb_rows.append([
                {"text": "💳 Purchase Access / Pay", "callback_data": f"req_access_{channel_id}"},
                {"text": "🎁 Request Free Demo", "callback_data": f"req_demo_{channel_id}"}
            ])

    kb_rows.append([
        {"text": "🔄 Refresh Status", "callback_data": f"batch_inspect_{channel_id}"},
        {"text": "📚 Back to Batches", "callback_data": "all_batches_0"}
    ])
    kb_rows.append([{"text": "🏠 Main Menu", "callback_data": "u_main"}])

    return {
        "text": html,
        "reply_markup": {"inline_keyboard": kb_rows},
        "chat_data": json.dumps({"cid": channel_id, "type": batch_type, "has_access": user_has_access}),
        "protect_content": True
    }

def build_leaderboard_widget(user_id: int, page: int = 0) -> Dict[str, Any]:
    """
    Bot API 10.4 Live Dynamic Leaderboard Widget:
    Displays top students, user rank, live medals, and pagination.
    """
    user_db = getattr(config, "DB", {}).get("USER_DATA", {})
    ranked = []
    for uid, udata in user_db.items():
        inv = int(udata.get("total_invited", 0))
        coins = round(float(udata.get("referral_count", 0)), 1)
        if inv > 0 or coins > 0:
            ranked.append({
                "id": str(uid),
                "name": udata.get("name") or f"Student {uid}",
                "invites": inv,
                "coins": coins
            })

    ranked.sort(key=lambda x: (x["invites"], x["coins"]), reverse=True)
    total_ranked = len(ranked)

    my_rank = "N/A"
    my_invites = 0
    my_coins = 0.0
    for idx, r in enumerate(ranked, start=1):
        if r["id"] == str(user_id):
            my_rank = f"#{idx}"
            my_invites = r["invites"]
            my_coins = r["coins"]
            break

    per_page = 5
    total_pages = max(1, (total_ranked + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    items = ranked[page * per_page : (page + 1) * per_page]

    medals = {1: "🥇", 2: "🥈", 3: "🥉"}

    html_lines = [
        "<b>🏆 <u>LIVE HALL OF FAME & LEADERBOARD</u></b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"📊 <b>Total Competitors:</b> <code>{total_ranked} Students</code>",
        f"👤 <b>Your Rank:</b> <code>{my_rank}</code> (<code>{my_invites}</code> Invites | <code>{my_coins}</code> Coins)",
        f"📄 <b>Page:</b> <code>{page + 1}/{total_pages}</code>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━"
    ]

    for idx, item in enumerate(items, start=page * per_page + 1):
        badge = medals.get(idx, f"<b>#{idx}</b>")
        html_lines.append(f"{badge} <b>{item['name']}</b>\n   ↳ 👥 <code>{item['invites']}</code> Invites • 💎 <code>{item['coins']}</code> Coins")

    if not items:
        html_lines.append("<i>Abhi koi ranking record nahi hai. Dosto ko invite karke #1 banein!</i>")

    html_lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━")
    html_lines.append("<i>💡 Har dost ko invite karne par Coins milte hain jisse batch unlock hota hai!</i>")

    kb_rows = []
    nav_row = []
    if page > 0:
        nav_row.append({"text": "◀ Prev", "callback_data": f"lb_page_{page - 1}"})
    nav_row.append({"text": f"📄 {page + 1}/{total_pages}", "callback_data": f"lb_page_{page}"})
    if page < total_pages - 1:
        nav_row.append({"text": "Next ▶", "callback_data": f"lb_page_{page + 1}"})
    if nav_row:
        kb_rows.append(nav_row)

    kb_rows.append([
        {"text": "👥 Invite & Climb Rank", "callback_data": "u_ref_card"},
        {"text": "🔄 Live Refresh", "callback_data": f"lb_page_{page}"}
    ])
    kb_rows.append([{"text": "🏠 Main Menu", "callback_data": "u_main"}])

    return {
        "text": "\n".join(html_lines),
        "reply_markup": {"inline_keyboard": kb_rows},
        "chat_data": json.dumps({"page": page, "my_rank": my_rank}),
        "protect_content": True
    }


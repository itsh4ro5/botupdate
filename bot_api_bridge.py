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
    fallback_payload: Dict[str, Any] = {
        "chat_id": chat_id,
        "text": caption or widget_html,
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

def build_demands_console_card(sorted_items: List[tuple], page: int = 0, per_page: int = 5) -> Dict[str, Any]:
    """
    Builds the interactive Batch Demands Console widget card for the Owner/Admin.
    Enables 1-click mark live & notify directly from chat!
    """
    total = len(sorted_items)
    statuses = config.DB.get("BATCH_REQUEST_STATUS", {})
    completed = sum(1 for b, _ in sorted_items if statuses.get(b) == "done")
    pending = total - completed
    pct = int((completed / total) * 100) if total > 0 else 0

    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    start_idx = page * per_page
    page_items = sorted_items[start_idx:start_idx + per_page]

    # HTML Card Body
    html_lines = [
        "<b>📋 <u>LIVE BATCH DEMANDS & TASK CONSOLE</u></b>",
        f"<b>Progress:</b> <code>[{'█' * (pct // 10)}{'░' * (10 - (pct // 10))}] {completed}/{total} ({pct}%)</code>",
        f"<b>📦 Total:</b> <code>{total}</code> | <b>⏳ Pending:</b> <code>{pending}</code> | <b>✅ Live:</b> <code>{completed}</code>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━"
    ]

    for idx, (bname, uids) in enumerate(page_items, start=start_idx + 1):
        st = statuses.get(bname, "pending")
        is_done = (st == "done")
        badge = "🟢" if is_done else "🔴"
        status_text = "<b>[☑ UPLOADED & LIVE]</b>" if is_done else "<i>[☐ PENDING APPROVAL]</i>"
        html_lines.append(f"{badge} <b>{idx}. {bname}</b>\n   ↳ 👥 <b>{len(uids)} Students Waiting</b> — {status_text}")

    html_lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━")
    html_lines.append("<i>💡 Niche buttons se 1-click me batch Live karein. Students ko instant notification chali jayegi!</i>")

    # Interactive Keyboard Rows
    kb_rows = []
    for sub_idx, (bname, uids) in enumerate(page_items):
        item_idx = start_idx + sub_idx
        st = statuses.get(bname, "pending")
        is_done = (st == "done")
        icon = "✅ Live" if is_done else "⚡ Mark Live & Notify"
        short_bname = (bname[:16] + "..") if len(bname) > 18 else bname
        btn_text = f"{icon}: {short_bname}"
        kb_rows.append([
            {"text": btn_text, "callback_data": f"breq_t_{item_idx}_{page}"},
            {"text": f"👥 Users ({len(uids)})", "callback_data": f"breq_w_{item_idx}_{page}"}
        ])

    # Nav Row
    nav_row = []
    if page > 0:
        nav_row.append({"text": "◀ Prev", "callback_data": f"breq_p_{page - 1}"})
    nav_row.append({"text": f"📄 {page + 1}/{total_pages}", "callback_data": f"breq_p_{page}"})
    if page < total_pages - 1:
        nav_row.append({"text": "Next ▶", "callback_data": f"breq_p_{page + 1}"})
    if nav_row:
        kb_rows.append(nav_row)

    kb_rows.append([
        {"text": "🔄 Refresh Console", "callback_data": f"breq_p_{page}"},
        {"text": "🔙 Back to Terminal", "callback_data": "dash_home"}
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
                {"text": "📚 Open Batches", "url": f"https://t.me/{bot_user}/app"}
            ]
        ]
    }
    return {"text": html, "reply_markup": kb}

def to_pyrogram_markup(markup_dict: Optional[Dict[str, Any]]):
    """Converts standard Bot API inline_keyboard dict into Pyrogram InlineKeyboardMarkup."""
    from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    if not markup_dict or "inline_keyboard" not in markup_dict:
        return InlineKeyboardMarkup([])
    rows = []
    for r in markup_dict["inline_keyboard"]:
        row_btns = []
        for b in r:
            if "url" in b:
                row_btns.append(InlineKeyboardButton(b["text"], url=b["url"]))
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
        {"text": "🏠 Study Dashboard", "url": "https://t.me/" + getattr(config, "BOT_USERNAME", "H4R_Contact_bot").lstrip("@") + "/app"},
        {"text": "📢 Official Channel", "url": getattr(config, "BATCH_UPDATE_CHANNEL_LINK", "https://t.me/H4R_Contact_bot")}
    ])
    return {"text": html, "reply_markup": {"inline_keyboard": kb_rows}}

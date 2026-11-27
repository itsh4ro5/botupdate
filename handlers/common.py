# -*- coding: utf-8 -*-
import asyncio
from datetime import datetime, timedelta
import io
import os
import re
import traceback
import time
import urllib.parse
import logging
import json
import config
from config import *
import richpyro as rp
import pyrogram
from pyrogram import Client, filters
from pyrogram.enums import ButtonStyle, ChatMemberStatus, ChatType, ParseMode
from pyrogram.errors import (
    ChatAdminRequired,
    FloodWait,
    InputUserDeactivated,
    PeerIdInvalid,
    RPCError,
    SessionPasswordNeeded,
    UserIsBlocked,
    UserNotParticipant,
)
from pyrogram.types import (
    BotCommand,
    BotCommandScopeChat,
    CallbackQuery,
    ChatJoinRequest,
    ChatMemberUpdated,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaAnimation,
    InputMediaAudio,
    InputMediaDocument,
    InputMediaPhoto,
    InputMediaVideo,
    Message,
    WebAppInfo,
)

from bot_api_bridge import (
    send_ephemeral_reply,
    send_html_widget,
    edit_widget_data,
    get_topic_deeplink,
    build_demands_console_card,
    build_batch_request_detail_card,
    build_giveaway_claim_card,
    build_interactive_broadcast_card,
    build_student_wallet_widget,
    build_batch_details_widget,
    build_leaderboard_widget,
    to_pyrogram_markup,
)

logger = logging.getLogger(__name__)
# Custom Animated / Premium Emojis from @istevedogs Emoji Icons Pack
CUSTOM_EMOJIS = {
    "CHECK": "4967612238874542985",       # ✅
    "CHECK_2": "4967770182001885951",     # ✅
    "CHECK_GREEN": "4969981205396063191", # ✅
    "CROSS": "4970239229851337393",       # ✖️
    "CROSS_BOX": "4967749291280958323",   # ❎
    "ALERT": "4967800109334004515",       # ❗️
    "SIREN": "4969741112429249159",       # 🚨
    "STOP": "4967893421793477515",        # 🛑
    "QUESTION": "4967611285391803206",    # ❓
    "PLUS": "4967481744883188442",        # ➕
    "MINUS": "4967648879240544998",       # ➖
    "INFO": "4970095936857440997",        # ℹ️
    "BELL": "4967778303785042605",        # 🔔
    "SPARKLE": "4970033831630341184",     # ❇️
    "LIGHT": "4968003407315993509",       # 💡
    "LIGHTNING": "4967489802241835579",   # ⚡️
    "HEART": "4967994490963886991",       # ❤️
    "THUMB_UP": "4967792296788501647",    # 👍
    "BROADCAST": "4969807937825407652",   # 📢
    "ANNOUNCE": "4969904819402703699",    # 📣
    "DOWNLOAD": "4967594608033792786",    # 📥
    "STATS": "4967970726909838084",       # 📊
    "DIAMOND": "4968025655246586724",     # 💎
    "FOLDER": "4967570599166608213",      # 📂
    "GEAR": "4967490064234840998",       # ⚙️
    "GIFT": "4967740821605450859",        # 🎁
    "KEY": "4969770296732025426",         # 🔑
    "LOCK": "4970224115861422958",        # 🔒
    "PIN": "4970003148383978212",         # 📍
    "CLOCK": "4967710224258433643",       # 🕰
    "CROWN": "4969765374699504624",       # 👑
    "MEDAL": "4967587877820039770",       # 🥇
    "MAIL": "4967770306555937780",        # 📧
    "SHIELD": "4967861870963720868",      # 🛡
    "REFRESH": "4967823117473809209",     # 🔃
    "LINK": "4967826519087907994",        # 🔗
    "DOC": "4967625145251267473",         # 📄
    "BAN": "4967927656977793959",         # 🚫
}

_SYNC_IN_PROGRESS = False
_BOT_SELF_ID = None
USER_FLOW_STATE = {}

# --- ANTI-SPAM CONFIG: Link generation cooldown & per-batch lock ---
GLOBAL_LINK_COOLDOWN = 15 * 60   # Rule A: 15 minutes between ANY link generation
ACTIVE_LINK_TTL = 60             # Rule B: matches the 60s invite-link expiry

def _patch_rich_text_classes():
    for name in dir(pyrogram.types):
        if name.startswith("RichText") or name.startswith("InputRich"):
            cls = getattr(pyrogram.types, name)
            if hasattr(cls, "write") and callable(getattr(cls, "write")):
                orig_fn = cls.write
                def _wrap(fn):
                    async def _safe_write(self, *args, **kwargs):
                        try:
                            return await fn(self, *args, **kwargs)
                        except TypeError as te:
                            if "unexpected keyword argument" in str(te):
                                cleaned = {k: v for k, v in kwargs.items() if k in ["client", "chat_id"]}
                                return await fn(self, *args, **cleaned)
                            raise
                    return _safe_write
                cls.write = _wrap(orig_fn)

    try:
        from pyrogram.types.input_content.input_rich_message import InputRichMessage
        if hasattr(InputRichMessage, "write") and callable(getattr(InputRichMessage, "write")):
            orig_irm_write = InputRichMessage.write
            async def _safe_irm_write(self, *args, **kwargs):
                try:
                    return await orig_irm_write(self, *args, **kwargs)
                except TypeError as te:
                    if "unexpected keyword argument" in str(te):
                        cleaned = {k: v for k, v in kwargs.items() if k in ["client", "chat_id"]}
                        return await orig_irm_write(self, *args, **cleaned)
                    raise
            InputRichMessage.write = _safe_irm_write
    except Exception:
        pass


_patch_rich_text_classes()

def ce(name: str, fallback: str = ""):
    eid = CUSTOM_EMOJIS.get(name)
    if eid:
        try:
            return rp.custom_emoji(eid, fallback or "✨")
        except Exception:
            return fallback
    return fallback


def is_vip_user(uid) -> bool:
    """VIP = 25+ lifetime successful refers (tier flag set in process_successful_referral)."""
    key = uid if uid in DB["USER_DATA"] else str(uid)
    return DB["USER_DATA"].get(key, {}).get("tier") == "vip"


async def send_vip_treat(client: Client, uid: int):
    """VIPs ko action complete karne par ek premium sticker/GIF bhejta hai (agar admin ne /setvipsticker se set kiya hai)."""
    sticker_id = DB.get("VIP_STICKER_ID")
    if not sticker_id:
        return
    try:
        if DB.get("VIP_STICKER_TYPE") == "animation":
            await client.send_animation(uid, sticker_id)
        else:
            await client.send_sticker(uid, sticker_id)
    except Exception:
        pass


def get_cooldown_remaining(uid: int) -> int:
    """Rule A: returns remaining whole minutes of the global cooldown (0 = clear). VIPs bypass this entirely."""
    if is_vip_user(uid):
        return 0
    entry = DB.get("PENDING_REQUESTS", {}).get(str(uid))
    if not entry:
        return 0
    elapsed = time.time() - entry.get("last_link_ts", 0)
    remaining = GLOBAL_LINK_COOLDOWN - elapsed
    if remaining <= 0:
        return 0
    return max(1, int(remaining // 60) + (1 if remaining % 60 else 0))


def has_active_request(uid: int, cid: int) -> bool:
    """Rule B: True if this user still has a live pending link for this exact batch."""
    entry = DB.get("PENDING_REQUESTS", {}).get(str(uid))
    if not entry:
        return False
    exp = entry.get("active_batches", {}).get(str(cid))
    return bool(exp and time.time() < exp)


def register_link_request(uid: int, cid: int):
    """Call this the moment a link is successfully generated (free or paid)."""
    now = time.time()
    entry = DB["PENDING_REQUESTS"].setdefault(
        str(uid), {"last_link_ts": 0, "active_batches": {}}
    )
    entry["last_link_ts"] = now
    entry["active_batches"][str(cid)] = now + ACTIVE_LINK_TTL
    asyncio.create_task(save_data_async())


def clear_active_request(uid, cid):
    """Call this when an admin approves/rejects a request early, freeing the slot."""
    entry = DB.get("PENDING_REQUESTS", {}).get(str(uid))
    if entry and str(cid) in entry.get("active_batches", {}):
        del entry["active_batches"][str(cid)]


def get_args(message: Message):
    if getattr(message, "command", None) and len(message.command) > 1:
        return message.command[1:]
    txt = (getattr(message, "text", None) or getattr(message, "caption", None) or "").strip()
    if txt:
        parts = txt.split()
        if txt.startswith(("/", "!", ".")):
            return parts[1:]
        return parts
    return []


def is_admin_msg(message: Message) -> bool:
    if message.from_user:
        return is_admin(message.from_user.id)
    return bool(message.sender_chat) and str(message.chat.id) == str(SUPPORT_GROUP_ID)


def is_owner(user_id: int) -> bool:
    return bool(user_id) and str(user_id) == str(OWNER_ID)


def is_owner_msg(message: Message) -> bool:
    return bool(message.from_user) and is_owner(message.from_user.id)


async def check_membership_pyro(uid: int, client: Client):
    if not MANDATORY_CHANNEL_ID:
        return True
    return await get_membership_cached(client, MANDATORY_CHANNEL_ID, uid)


async def is_already_in_channel_pyro(client: Client, cid: int, uid: int):
    return await get_membership_cached(client, cid, uid)


async def set_role_based_commands(user_id: int, client: Client):
    try:
        user_cmds = [
            BotCommand("start", "Open Main Menu"),
            BotCommand("study", "Launch Study Hub & Pomodoro"),
            BotCommand("webapp", "Open Mini App Store"),
            BotCommand("myinfo", "Check Active Demos & Wallet"),
            BotCommand("id", "Get Telegram ID"),
        ]
        if str(user_id) == str(OWNER_ID) or is_admin(user_id):
            admin_cmds = user_cmds + [
                BotCommand("stats", "Bot Statistics"),
                BotCommand("batchstats", "Batch Info"),
                BotCommand("genvoucher", "Generate Promo Code"),
                BotCommand("batchrequests", "View Batch Requests"),
            ]
            await client.set_bot_commands(
                admin_cmds, scope=BotCommandScopeChat(chat_id=user_id)
            )
        else:
            await client.set_bot_commands(
                user_cmds, scope=BotCommandScopeChat(chat_id=user_id)
            )
        try:
            from pyrogram.types import MenuButtonCommands
            await client.set_chat_menu_button(chat_id=user_id, menu_button=MenuButtonCommands())
        except Exception:
            pass
    except Exception:
        pass



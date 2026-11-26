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
    build_giveaway_claim_card,
    build_interactive_broadcast_card,
    build_student_wallet_widget,
    build_batch_details_widget,
    build_leaderboard_widget,
    to_pyrogram_markup,
)

logger = logging.getLogger(__name__)
from .common import *
from .common import _SYNC_IN_PROGRESS, _BOT_SELF_ID, _patch_rich_text_classes
from .economy import process_successful_referral

async def cmd_id(client: Client, message: Message):
    chat, user = message.chat, message.from_user
    text = (
        f"  **Your User ID:** `{user.id}`"
        if chat.type == ChatType.PRIVATE and user
        else f"  **Chat ID:** `{chat.id}`"
    )
    if chat.type != ChatType.PRIVATE:
        if message.message_thread_id:
            text += f"\n  **Topic ID:** `{message.message_thread_id}`"
        if user:
            text += f"\n  **User ID:** `{user.id}`"
    try:
        await message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
    except Exception:
        pass
    if user and is_admin(user.id):
        await schedule_delete(client, message)


async def cmd_start(client: Client, message: Message):
    await start(client, message)


async def cmd_webapp(client: Client, message: Message):
    uid = message.from_user.id if message.from_user else message.chat.id
    if not is_admin(uid):
        return await message.reply_text("🔒 **Study Portal abhi private maintenance me hai.**", parse_mode=ParseMode.MARKDOWN)

    url = getattr(config, "WEB_APP_URL", "") or "https://itsh4r01-user-manager.hf.space"
    direct_app_url = f"{url}?uid={uid}"
    
    kb = [
        [InlineKeyboardButton("🚀 Launch Staff Mini App (TWA)", web_app=WebAppInfo(url=direct_app_url))],
        [InlineKeyboardButton("🌐 Open in Web Browser", url=direct_app_url)]
    ]
    role_text = "👑 **Owner Console**" if str(uid) == str(config.OWNER_ID) else "⚡ **Admin Desk**"
    await message.reply_text(
        f"📱 **Staff Control Portal**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Detected Role: {role_text}\n\n"
        f"👇 *Staff portal open karne ke liye niche tap karein:*",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.MARKDOWN
    )


async def cmd_study(client: Client, message: Message):
    await message.reply_text("📚 Apne batches dekhne ke liye `/batch` use karein ya `/start` dabayein.", parse_mode=ParseMode.MARKDOWN)


async def cmd_vacancies(client: Client, message: Message):
    url = getattr(config, "WEB_APP_URL", "") or "https://itsh4r01-user-manager.hf.space"
    uid = message.from_user.id if message.from_user else message.chat.id
    vac_app_url = f"{url}?uid={uid}&tab=vacancies"

    vacs = DB.get("EXAM_VACANCIES", config.DEFAULT_VACANCIES)
    txt = (
        "📢 **2026 Active Government Exam Vacancies & Alerts**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "⚡ *Live recruitment radar for competitive aspirants:*\n\n"
    )

    kb = []
    for idx, v in enumerate(vacs[:5], 1):
        txt += (
            f"**{idx}. {v.get('title')}**\n"
            f"🏛️ *Commission:* `{v.get('org')}`\n"
            f"👥 *Posts:* `{v.get('total_posts')}`\n"
            f"📅 *Last Date:* **{v.get('last_date')}**\n"
            f"🎯 *Exam Date:* `{v.get('exam_date')}`\n\n"
        )
        if v.get("apply_link"):
            kb.append([InlineKeyboardButton(f"🚀 Apply: {v.get('org')}", url=v.get("apply_link"))])

    kb.append([InlineKeyboardButton("📱 Open Full Vacancy Radar (Mini App)", web_app=WebAppInfo(url=vac_app_url))])
    kb.append([InlineKeyboardButton("🏠 Main Menu", callback_data="u_main")])

    await message.reply_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)


async def cmd_quiz(client: Client, message: Message):
    url = getattr(config, "WEB_APP_URL", "") or "https://itsh4r01-user-manager.hf.space"
    uid = message.from_user.id if message.from_user else message.chat.id
    quiz_app_url = f"{url}?uid={uid}&tab=study"

    kb = [
        [InlineKeyboardButton("🧠 Launch Multi-Subject Quiz Arena", web_app=WebAppInfo(url=quiz_app_url))],
        [InlineKeyboardButton("🏆 Top Leaderboard", callback_data="show_leaderboard")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="u_main")]
    ]

    txt = (
        "🧠 **Multi-Subject Exam Practice Quiz Arena**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🎯 *Subject Quizzes Available:*\n"
        "• 🏛️ **Indian Polity** (Articles, Writs, Amendments)\n"
        "• 📜 **Modern History** (Freedom Struggle & Movements)\n"
        "• 🌍 **Geography** (Rivers, Passes, National Parks)\n"
        "• 📈 **Indian Economy** (RBI, Inflation, Budget)\n"
        "• 📐 **CSAT Math & Aptitude** (Speed Math, Ratios)\n"
        "• 🧠 **Logical Reasoning** (Coding, Puzzles)\n"
        "• 🧬 **General Science** (Physics, Bio, Space Tech)\n"
        "• 📰 **Current Affairs** (High-Yield 2026 Milestones)\n\n"
        "🎁 **Reward:** Score ≥3 to claim **+1.0 Study Coins** daily!\n\n"
        "👇 *Tap below to launch the Quiz Arena:* "
    )
    await message.reply_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)


async def cmd_currentaffairs(client: Client, message: Message):
    url = getattr(config, "WEB_APP_URL", "") or "https://itsh4r01-user-manager.hf.space"
    uid = message.from_user.id if message.from_user else message.chat.id
    ca_app_url = f"{url}?uid={uid}&tab=study"

    ca_list = DB.get("CURRENT_AFFAIRS", config.DEFAULT_CURRENT_AFFAIRS)
    txt = (
        "📰 **Daily High-Yield Current Affairs Digest**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "⚡ *Curated for UPSC, SSC, Railways & State PSCs:*\n\n"
    )

    for idx, item in enumerate(ca_list[:5], 1):
        txt += (
            f"**{idx}. [{item.get('topic')}]**\n"
            f"📌 *{item.get('title')}*\n"
            f"_{item.get('snippet')}_\n"
            f"🗓️ `{item.get('date')}`\n\n"
        )

    kb = [
        [InlineKeyboardButton("📖 Open Digest & Notes in Mini App", web_app=WebAppInfo(url=ca_app_url))],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="u_main")]
    ]
    await message.reply_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)


async def show_tnc_menu(client: Client, message: Message):
    import richpyro as rp
    tnc_msg = rp.message(
        rp.para("⚠️ ", rp.bold("STRICT WARNING & TERMS OF SERVICE")),
        rp.divider(),
        rp.para("🇬🇧 ", rp.bold("ENGLISH:")),
        rp.para("If you leave the Main Channel or block this bot, you will be ", rp.bold("INSTANTLY REMOVED"), " from ALL joined groups and channels."),
        rp.divider(),
        rp.para("🇮🇳 ", rp.bold("HINDI:")),
        rp.para("Agar aapne Main Channel ko chhoda (leave kiya) ya is bot ko block kiya, toh aapko sabhi groups aur channels se ", rp.bold("TURANT NIKAL"), " diya jayega."),
        rp.divider(),
        rp.para("✅ ", rp.italic("Click 'I Read & Accept' only if you agree to these terms.")),
        rp.buttons(rp.btn("✅ I Read & Accept", "accept_tnc", style=rp.Style.SUCCESS))
    )
    await rp.send(client, message.chat.id, tnc_msg)


async def show_tnc_menu_cb(client: Client, q: CallbackQuery):
    import richpyro as rp
    tnc_msg = rp.message(
        rp.para("⚠️ ", rp.bold("STRICT WARNING & TERMS OF SERVICE")),
        rp.divider(),
        rp.para("🇬🇧 ", rp.bold("ENGLISH:")),
        rp.para("If you leave the Main Channel or block this bot, you will be ", rp.bold("INSTANTLY REMOVED"), " from ALL joined groups and channels."),
        rp.divider(),
        rp.para("🇮🇳 ", rp.bold("HINDI:")),
        rp.para("Agar aapne Main Channel ko chhoda (leave kiya) ya is bot ko block kiya, toh aapko sabhi groups aur channels se ", rp.bold("TURANT NIKAL"), " diya jayega."),
        rp.divider(),
        rp.para("✅ ", rp.italic("Click 'I Read & Accept' only if you agree to these terms.")),
        rp.buttons(rp.btn("✅ I Read & Accept", "accept_tnc", style=rp.Style.SUCCESS))
    )
    await rp.edit(client, q.message.chat.id, q.message.id, tnc_msg)


def build_home_menu(user_key, user):
    """Normal users: standard half-width layout. VIPs: personalized greeting + exclusive full-width buttons."""
    
    # Check if user came from a Batch Share Link
    pending_batch = DB["USER_DATA"].get(user_key, {}).get("pending_batch")
    if pending_batch:
        bname = DB.get("ALL_CHATS", {}).get(int(pending_batch)) or DB.get("ALL_CHATS", {}).get(str(pending_batch)) or "Shared Batch"
        
        return rp.message(
            rp.heading("🎉 Invitation Received!"),
            rp.divider(),
            rp.para(
                "📌 ", rp.bold("Invitation Details:\n"),
                "➤ You have been invited by a friend to join this exclusive batch.\n",
                "➤ Click below to view details and join instantly."
            ),
            rp.buttons(
                rp.btn("🚀 Open Shared Batch", f"open_batch_{pending_batch}", style=rp.Style.SUCCESS),
                rp.btn("🏠 Main Menu", "clear_pending_batch", style=rp.Style.DANGER)
            )
        )

    vip = DB["USER_DATA"].get(user_key, {}).get("tier") == "vip"
    first_name = (user.first_name if user and user.first_name else "there")

    if vip:
        total_inv = DB["USER_DATA"].get(user_key, {}).get("total_invited", 0)
        pts = DB["USER_DATA"].get(user_key, {}).get("referral_count", 0)
        streak = DB["USER_DATA"].get(user_key, {}).get("streak", 0)
        return rp.message(
            rp.heading("👑 Elite VIP Dashboard"),
            rp.para("Welcome back, ", rp.bold(first_name), "!"),
            rp.divider(),
            rp.table(
                rows=[
                    [rp.table_cell("Wallet Balance", is_header=True), rp.table_cell("Total Referrals", is_header=True), rp.table_cell("Daily Streak", is_header=True)],
                    [rp.table_cell(f"💎 {pts} Coins"), rp.table_cell(f"👥 {total_inv}"), rp.table_cell(f"🔥 {streak} Days")]
                ],
                bordered=True, striped=True
            ),
            rp.expandable_quote([
                rp.bold("📌 VIP Privilege Pointers:\n\n"),
                rp.bold("• "), "📚 ", rp.bold("My Batches"), " — ", rp.italic("Aapke sabhi VIP aur unlocked batches\n\n"),
                rp.bold("• "), "🌐 ", rp.bold("All Batches"), " — ", rp.italic("Complete batches & engineering catalog\n\n"),
                rp.bold("• "), "🔥 ", rp.bold("Daily Streak"), " — ", rp.italic("Roz login karke bonus points claim karein\n\n"),
                rp.bold("• "), "🏆 ", rp.bold("Leaderboard"), " — ", rp.italic("Top students aur referral champions ranking\n\n"),
                rp.bold("• "), "💎 ", rp.bold("VIP Materials"), " — ", rp.italic("Exclusive study notes aur lectures\n\n"),
                rp.bold("• "), "🎁 ", rp.bold("Monthly Bonus"), " — ", rp.italic("Har mahine VIP bonus rewards\n\n"),
                rp.bold("• "), "🚀 ", rp.bold("Refer & Earn"), " — ", rp.italic("Friends ko invite karein aur free coins earn karein\n\n"),
                rp.bold("• "), "🎟️ ", rp.bold("Redeem Voucher"), " — ", rp.italic("Promo codes se free coins activate karein\n\n"),
                rp.bold("• "), "🙋 ", rp.bold("Request Batch"), " — ", rp.italic("Naye batch ki demand direct submit karein\n\n"),
                rp.bold("• "), "ℹ️ ", rp.bold("My Profile"), " — ", rp.italic("Aapka account, balance aur join history")
            ]),
            rp.buttons(
                rp.btn("👑 My Batches", "my_batches_0", style=rp.Style.SUCCESS),
                rp.btn("🌟 All Batches", "all_batches_0", style=rp.Style.DEFAULT)
            ),
            rp.buttons(
                rp.btn("🎁 Mega Giveaway", "menu_giveaway", style=rp.Style.SUCCESS),
                rp.btn("🗳️ Batch Demands", "menu_batch_demands", style=rp.Style.PRIMARY)
            ),
            rp.buttons(
                rp.btn("🔥 Daily Streak", "claim_daily_streak", style=rp.Style.SUCCESS),
                rp.btn("🚀 Refer & Earn", "menu_refer", style=rp.Style.SUCCESS)
            ),
            rp.buttons(
                rp.btn("🏆 Leaderboard", "show_leaderboard", style=rp.Style.DEFAULT),
                rp.btn("🎟️ Redeem Code", "redeem_voucher_btn", style=rp.Style.DEFAULT)
            ),
            rp.buttons(
                rp.btn("💎 VIP Materials", "vip_materials", style=rp.Style.DEFAULT),
                rp.btn("🎁 Monthly Bonus", "vip_monthly_bonus", style=rp.Style.DEFAULT)
            ),
            rp.buttons(
                rp.btn("🙋 Request Batch", "req_batch_btn", style=rp.Style.DEFAULT),
                rp.btn("🤖 Test Bot", "test_bot", style=rp.Style.DEFAULT)
            ),
            rp.buttons(
                rp.btn("ℹ️ My Profile", "my_info", style=rp.Style.DEFAULT),
                rp.url_btn("📢 Updates", BATCH_UPDATE_CHANNEL_LINK, style=rp.Style.LINK)
            )
        )
    else:
        streak = DB["USER_DATA"].get(user_key, {}).get("streak", 0)
        return rp.message(
            rp.heading("🌟 Premium Study Hub"),
            rp.para("Welcome, ", rp.bold(first_name), "! Niche diye gaye options se apna section chunein:"),
            rp.divider(),
            rp.expandable_quote([
                rp.bold("📌 Quick Feature Pointers:\n\n"),
                rp.bold("• "), "📚 ", rp.bold("My Batches"), " — ", rp.italic("Aapke unlocked aur enrolled study courses\n\n"),
                rp.bold("• "), "🌐 ", rp.bold("All Batches"), " — ", rp.italic("Complete batches & engineering catalog\n\n"),
                rp.bold("• "), "🔥 ", rp.bold("Daily Streak"), " — ", rp.italic("Rozana login karke free coins claim karein\n\n"),
                rp.bold("• "), "🏆 ", rp.bold("Leaderboard"), " — ", rp.italic("Top students & referral rank dekhein\n\n"),
                rp.bold("• "), "🎁 ", rp.bold("Refer & Earn"), " — ", rp.italic("Dosto ko invite karein aur free courses payein\n\n"),
                rp.bold("• "), "🎟️ ", rp.bold("Redeem Voucher"), " — ", rp.italic("Promo codes se free coins activate karein\n\n"),
                rp.bold("• "), "🙋 ", rp.bold("Request Batch"), " — ", rp.italic("Apne manpasand batch ki request dalein\n\n"),
                rp.bold("• "), "🤖 ", rp.bold("Test Bot"), " — ", rp.italic("Practice test series solve karein\n\n"),
                rp.bold("• "), "ℹ️ ", rp.bold("Profile"), " — ", rp.italic("Wallet balance, streak & account details")
            ]),
            rp.buttons(
                rp.btn("📚 My Batches", "my_batches_0", style=rp.Style.SUCCESS),
                rp.btn("🌐 All Batches", "all_batches_0", style=rp.Style.DEFAULT)
            ),
            rp.buttons(
                rp.btn("🎁 Mega Giveaway", "menu_giveaway", style=rp.Style.SUCCESS),
                rp.btn("🗳️ Batch Demands", "menu_batch_demands", style=rp.Style.PRIMARY)
            ),
            rp.buttons(
                rp.btn("🔥 Daily Streak", "claim_daily_streak", style=rp.Style.SUCCESS),
                rp.btn("🎁 Refer & Earn", "menu_refer", style=rp.Style.SUCCESS)
            ),
            rp.buttons(
                rp.btn("🏆 Leaderboard", "show_leaderboard", style=rp.Style.DEFAULT),
                rp.btn("🎟️ Redeem Code", "redeem_voucher_btn", style=rp.Style.DEFAULT)
            ),
            rp.buttons(
                rp.btn("🙋 Request Batch", "req_batch_btn", style=rp.Style.DEFAULT),
                rp.btn("🤖 Test Bot", "test_bot", style=rp.Style.DEFAULT)
            ),
            rp.buttons(
                rp.btn("ℹ️ My Profile", "my_info", style=rp.Style.DEFAULT),
                rp.url_btn("📢 Updates", BATCH_UPDATE_CHANNEL_LINK, style=rp.Style.LINK)
            )
        )


async def show_user_menu(client: Client, message: Message):
    user_key = message.from_user.id if message.from_user.id in DB["USER_DATA"] else str(message.from_user.id)
    msg = build_home_menu(user_key, message.from_user)
    await rp.send(client, message.chat.id, msg)


async def show_user_menu_cb(client: Client, q: CallbackQuery):
    user_key = q.from_user.id if q.from_user.id in DB["USER_DATA"] else str(q.from_user.id)
    msg = build_home_menu(user_key, q.from_user)
    await rp.edit(client, q.message.chat.id, q.message.id, msg)


def build_security_panel_msg():
    new_users = DB.get('NEW_USERS_ALLOWED', True)
    free_locked = DB.get('FREE_LOCKED', False)
    paid_locked = DB.get('PAID_LOCKED', False)
    testbot_locked = DB.get('TEST_BOT_LOCKED', False)
    maint_mode = DB.get('MAINTENANCE_MODE', False)

    lockdown_btn = rp.btn(
        f"🔐 Lockdown: {'🔴 ON' if not new_users else '🟢 OFF'}",
        "toggle_lockdown",
        style=rp.Style.DANGER if not new_users else rp.Style.SUCCESS
    )
    free_btn = rp.btn(
        f"🆓 Free: {'🔴 LOCK' if free_locked else '🟢 OPEN'}",
        "toggle_free",
        style=rp.Style.DANGER if free_locked else rp.Style.SUCCESS
    )
    paid_btn = rp.btn(
        f"💰 Paid: {'🔴 LOCK' if paid_locked else '🟢 OPEN'}",
        "toggle_paid",
        style=rp.Style.DANGER if paid_locked else rp.Style.SUCCESS
    )
    testbot_btn = rp.btn(
        f"🤖 Test Bot: {'🔴 LOCK' if testbot_locked else '🟢 OPEN'}",
        "toggle_testbot",
        style=rp.Style.DANGER if testbot_locked else rp.Style.SUCCESS
    )
    maint_btn = rp.btn(
        f"🛠️ Maint: {'🔴 ON' if maint_mode else '🟢 OFF'}",
        "toggle_maintenance",
        style=rp.Style.DANGER if maint_mode else rp.Style.SUCCESS
    )

    return rp.message(
        rp.heading("🔒 Security & Access Control"),
        rp.divider(),
        rp.expandable_quote([
            rp.bold("📌 Security Controls Pointers:\n\n"),
            rp.bold("• "), "🔒 ", rp.bold("Lockdown"), " — ", rp.italic("Naye users ko bot me aane se rokein ya allow karein\n\n"),
            rp.bold("• "), "🆓 ", rp.bold("Free Batches"), " — ", rp.italic("Free batches section ko instant lock/unlock karein\n\n"),
            rp.bold("• "), "💰 ", rp.bold("Paid Batches"), " — ", rp.italic("Paid courses catalog ko lock/unlock karein\n\n"),
            rp.bold("• "), "🤖 ", rp.bold("Test Bot"), " — ", rp.italic("Practice test bot access toggle karein\n\n"),
            rp.bold("• "), "🛠️ ", rp.bold("Maintenance"), " — ", rp.italic("Server update ke waqt users ko temporarily block karein")
        ]),
        rp.buttons(lockdown_btn, free_btn),
        rp.buttons(paid_btn, testbot_btn),
        rp.buttons(maint_btn),
        rp.buttons(rp.btn("🔙 Back to Terminal", "dash_home", style=rp.Style.DANGER))
    )


def build_database_panel_msg():
    return rp.message(
        rp.heading("💾 Database Management Tools"),
        rp.divider(),
        rp.expandable_quote([
            rp.bold("📌 Database Tools Pointers:\n\n"),
            rp.bold("• "), "📥 ", rp.bold("Download Backup"), " — ", rp.italic("Bot database ka poora JSON backup download karein\n\n"),
            rp.bold("• "), "🔄 ", rp.bold("Run Sync"), " — ", rp.italic("Storage aur MongoDB database sync karein\n\n"),
            rp.bold("• "), "👥 ", rp.bold("All Users List"), " — ", rp.italic("Sabhi registered users ki list export karein\n\n"),
            rp.bold("• "), "🔍 ", rp.bold("Specific User Data"), " — ", rp.italic("User ID ya username se complete details dekhein\n\n"),
            rp.bold("• "), "🎟️ ", rp.bold("Generate Redeem Code"), " — ", rp.italic("Referral coins/points ke naye promo voucher banayein\n\n"),
            rp.bold("• "), "📋 ", rp.bold("Active Vouchers"), " — ", rp.italic("Sabhi active redeem codes ki list dekhein\n\n"),
            rp.bold("• "), "🎁 ", rp.bold("Gift Coin"), " — ", rp.italic("User ke wallet me free coins credit karein\n\n"),
            rp.bold("• "), "🚫 ", rp.bold("Ban / Unban"), " — ", rp.italic("User access control manage karein\n\n"),
            rp.bold("• "), "🧪 ", rp.bold("Authorized Testers"), " — ", rp.italic("WebApp Maintenance Mode bypass ke liye tester IDs manage karein\n\n"),
            rp.bold("• "), "📋 ", rp.bold("Request Batches"), " — ", rp.italic("User-requested batches ki complete checklist aur interactive status dekhein\n\n"),
            rp.bold("• "), "🗂️ ", rp.bold("Topic Arrangement"), " — ", rp.italic("Source channel se files caption ke Topic tag ke hisab se destination supergroup ke topics me auto-sort karein\n\n"),
            rp.bold("• "), "☢️ ", rp.bold("Hard Delete User"), " — ", rp.italic("User ka saara record database se delete karein")
        ]),
        rp.buttons(
            rp.btn("📥 Download Backup", "act_backup", style=rp.Style.DEFAULT),
            rp.btn("🔄 Run Sync", "act_sync", style=rp.Style.DEFAULT)
        ),
        rp.buttons(
            rp.btn("👥 All Users List", "act_allusers", style=rp.Style.DEFAULT),
            rp.btn("🔍 User Lookup", "input_userlookup", style=rp.Style.DEFAULT)
        ),
        rp.buttons(
            rp.btn("🎟️ Gen Voucher", "input_genvoucher", style=rp.Style.DEFAULT),
            rp.btn("📋 Active Vouchers", "act_vouchers", style=rp.Style.DEFAULT)
        ),
        rp.buttons(
            rp.btn("🎁 Gift Coin", "giftcoin_start", style=rp.Style.DEFAULT),
            rp.btn("☢️ Hard Delete", "input_deluser", style=rp.Style.DANGER)
        ),
        rp.buttons(
            rp.btn("🚫 Ban User", "input_ban", style=rp.Style.DANGER),
            rp.btn("✅ Unban User", "input_unban", style=rp.Style.SUCCESS)
        ),
        rp.buttons(
            rp.btn("🧪 Manage Testers", "dash_testers", style=rp.Style.PRIMARY),
            rp.btn("📋 Request Batches", "view_batch_requests", style=rp.Style.SUCCESS)
        ),
        rp.buttons(
            rp.btn("🗂️ Topic Arrangement", "start_topic_arranger", style=rp.Style.SUCCESS),
            rp.btn("🎁 Manage Giveaway", "dash_giveaway", style=rp.Style.PRIMARY)
        ),
        rp.buttons(rp.btn("🔙 Back to Terminal", "dash_home", style=rp.Style.DANGER))
    )


def build_testers_panel_msg():
    testers = DB.get("TESTER_IDS", [])
    if testers:
        t_list = "\n".join([f"• `{uid}`" for uid in testers])
    else:
        t_list = "*(Abhi koi authorized tester add nahi hai)*"
    return rp.message(
        rp.heading("🧪 Authorized Testers Management"),
        rp.divider(),
        rp.expandable_quote([
            rp.bold("📌 Authorized Testers Info:\n\n"),
            "In sabhi users ko WebApp me **Maintenance Mode** bypass karke test karne ki poori permission milti hai.\n\n",
            rp.bold(f"📋 Total Active Testers ({len(testers)}):\n"),
            t_list,
            "\n\n",
            rp.italic("Use the buttons below to add or remove Telegram user IDs:")
        ]),
        rp.buttons(
            rp.btn("➕ Add Tester", "input_addtester", style=rp.Style.SUCCESS),
            rp.btn("🗑️ Remove Tester", "input_deltester", style=rp.Style.DANGER)
        ),
        rp.buttons(rp.btn("🔙 Back to Database Tools", "dash_db", style=rp.Style.DANGER))
    )


def build_batches_panel_msg(back_cb="dash_home"):
    back_text = "🔙 Back to Admin Panel" if back_cb == "goto_admin_panel" else "🔙 Back to Terminal"
    return rp.message(
        rp.heading("📦 Batches Management"),
        rp.divider(),
        rp.expandable_quote([
            rp.bold("📌 Batches Tools Pointers:\n\n"),
            rp.bold("• "), "➕ ", rp.bold("Add Batch"), " — ", rp.italic("Naya Free/Paid/Special batch link add karein\n\n"),
            rp.bold("• "), "🗑️ ", rp.bold("Delete Batch"), " — ", rp.italic("Existing batch catalog se remove karein\n\n"),
            rp.bold("• "), "📁 ", rp.bold("Categories"), " — ", rp.italic("Nayi category banayein ya remove karein\n\n"),
            rp.bold("• "), "🏷️ ", rp.bold("Set Category"), " — ", rp.italic("Batches ko unki specific category me assign karein\n\n"),
            rp.bold("• "), "🧹 ", rp.bold("Empty Batch"), " — ", rp.italic("Batch se un-approved users ko mass-kick karein\n\n"),
            rp.bold("• "), "📢 ", rp.bold("Post Update"), " — ", rp.italic("Batch channel me latest update notice post karein\n\n"),
            rp.bold("• "), "🚀 ", rp.bold("Super Forwarder"), " — ", rp.italic("Messages & lectures channel se auto copy karein\n\n"),
            rp.bold("• "), "🛡️ ", rp.bold("Clean Unverified"), " — ", rp.italic("Non-members ko batch se remove karein\n\n"),
            rp.bold("• "), "📝 ", rp.bold("Caption Changer"), " — ", rp.italic("Files ke captions automatically edit karein\n\n"),
            rp.bold("• "), "📊 ", rp.bold("Batch Stats"), " — ", rp.italic("Kisi batch ke total members aur active users dekhein")
        ]),
        rp.buttons(
            rp.btn("➕ Add Batch", "act_addbatch", style=rp.Style.SUCCESS),
            rp.btn("🗑️ Delete Batch", "input_delbatch", style=rp.Style.DANGER)
        ),
        rp.buttons(
            rp.btn("📁➕ Add Category", "input_addcat", style=rp.Style.DEFAULT),
            rp.btn("📁🗑️ Del Category", "act_delcat", style=rp.Style.DEFAULT)
        ),
        rp.buttons(
            rp.btn("🏷️ Set Category", "input_setcat", style=rp.Style.DEFAULT),
            rp.btn("🧹 Empty Batch", "input_emptybatch", style=rp.Style.DEFAULT)
        ),
        rp.buttons(
            rp.btn("📢 Post Update", "input_updatepost", style=rp.Style.DEFAULT),
            rp.btn("🚀 Super Forwarder", "input_superfwd", style=rp.Style.DEFAULT)
        ),
        rp.buttons(
            rp.btn("🛡️ Clean Unverified", "input_cleanbatch", style=rp.Style.DEFAULT),
            rp.btn("📝 Caption Changer", "input_advcap", style=rp.Style.DEFAULT)
        ),
        rp.buttons(
            rp.btn("📊 Batch Stats", "act_batchstats", style=rp.Style.DEFAULT),
            rp.btn("📋 Demands Checklist", "view_batch_requests", style=rp.Style.SUCCESS)
        ),
        rp.buttons(rp.btn(back_text, back_cb, style=rp.Style.DANGER))
    )


def build_staff_panel_msg(back_cb="dash_home"):
    back_text = "🔙 Back to Admin Panel" if back_cb == "goto_admin_panel" else "🔙 Back to Terminal"
    return rp.message(
        rp.heading("🧑‍💼 Staff & Admin Management"),
        rp.divider(),
        rp.expandable_quote([
            rp.bold("📌 Staff Tools Pointers:\n\n"),
            rp.bold("• "), "➕ ", rp.bold("Add Admin"), " — ", rp.italic("Kisi user ko bot me Admin rights grant karein\n\n"),
            rp.bold("• "), "➖ ", rp.bold("Remove Admin"), " — ", rp.italic("Kisi user se Admin permissions revoke karein\n\n"),
            rp.bold("• "), "📋 ", rp.bold("Admin List"), " — ", rp.italic("Current active admins aur unki IDs ki list dekhein")
        ]),
        rp.buttons(
            rp.btn("➕ Add Admin", "input_addadmin", style=rp.Style.SUCCESS),
            rp.btn("➖ Remove Admin", "input_deladmin", style=rp.Style.DANGER)
        ),
        rp.buttons(
            rp.btn("📋 Admin List", "act_adminlist", style=rp.Style.DEFAULT)
        ),
        rp.buttons(rp.btn(back_text, back_cb, style=rp.Style.DANGER))
    )


def build_comms_panel_msg(back_cb="dash_home"):
    back_text = "🔙 Back to Admin Panel" if back_cb == "goto_admin_panel" else "🔙 Back to Terminal"
    return rp.message(
        rp.heading("📢 Communications & Messaging"),
        rp.divider(),
        rp.expandable_quote([
            rp.bold("📌 Communications Pointers:\n\n"),
            rp.bold("• "), "📢 ", rp.bold("Broadcast"), " — ", rp.italic("Bot ke sabhi users ko mass message/media bhejein\n\n"),
            rp.bold("• "), "📝 ", rp.bold("Post Message"), " — ", rp.italic("Kisi specific channel ya group me message bhejein\n\n"),
            rp.bold("• "), "🤖 ", rp.bold("Set Test Bot"), " — ", rp.italic("Practice Test bot ka direct link configure karein\n\n"),
            rp.bold("• "), "👋 ", rp.bold("Set Welcome"), " — ", rp.italic("Kisi batch channel ke liye custom welcome message set karein")
        ]),
        rp.buttons(
            rp.btn("📢 Broadcast to All", "act_broadcast", style=rp.Style.DEFAULT),
            rp.btn("📝 Post Message", "act_post", style=rp.Style.DEFAULT)
        ),
        rp.buttons(
            rp.btn("🤖 Set Test Bot Link", "input_settestbot", style=rp.Style.DEFAULT),
            rp.btn("👋 Set Welcome Msg", "input_setwelcome", style=rp.Style.DEFAULT)
        ),
        rp.buttons(rp.btn(back_text, back_cb, style=rp.Style.DANGER))
    )


def build_userbot_panel_msg(session, phone):
    if session:
        status_text = "🟢 Active & Ready"
        quote_text = [
            rp.bold("📌 Userbot Status & Tools:\n\n"),
            rp.bold("• "), "📞 ", rp.bold("Logged In Number: "), rp.code(phone), "\n\n",
            rp.bold("• "), "⚡ ", rp.bold("Ready Commands: "), rp.italic("Userbot /emptybatch, /clear, /joinall execute karne ke liye ready hai.")
        ]
        action_btn = rp.buttons(rp.btn("🚪 Logout (Delete Session)", "userbot_logout", style=rp.Style.DANGER))
    else:
        status_text = "🔴 Not Logged In"
        quote_text = [
            rp.bold("📌 Userbot Status:\n\n"),
            rp.bold("• "), "⚠️ ", rp.bold("No Session: "), rp.italic("Koi active userbot session nahi mila.\n\n"),
            rp.bold("• "), "🔑 ", rp.bold("Login Required: "), rp.italic("Userbot mass cleanup features use karne ke liye login karein.")
        ]
        action_btn = rp.buttons(rp.btn("🔑 Login Now", "input_userbotphone", style=rp.Style.SUCCESS))

    return rp.message(
        rp.heading("🤖 USERBOT CONTROL PANEL"),
        rp.para("Status: ", rp.bold(status_text)),
        rp.divider(),
        rp.expandable_quote(quote_text),
        action_btn,
        rp.buttons(rp.btn("🔙 Back to Terminal", "dash_home", style=rp.Style.DANGER))
    )


def build_admin_users_panel_msg(back_cb="goto_admin_panel"):
    back_text = "🔙 Back to Admin Panel" if back_cb == "goto_admin_panel" else "🔙 Back to Terminal"
    return rp.message(
        rp.heading("👥 User Management"),
        rp.divider(),
        rp.expandable_quote([
            rp.bold("📌 User Tools Pointers:\n\n"),
            rp.bold("• "), "🚫 ", rp.bold("Ban / Unban"), " — ", rp.italic("User access control manage karein\n\n"),
            rp.bold("• "), "👢 ", rp.bold("Kick User"), " — ", rp.italic("Batch se member ko remove karein\n\n"),
            rp.bold("• "), "🔍 ", rp.bold("Find User"), " — ", rp.italic("Username ya ID se lookup karein\n\n"),
            rp.bold("• "), "♻️ ", rp.bold("Reset Data"), " — ", rp.italic("User stats aur demo reset karein")
        ]),
        rp.buttons(
            rp.btn("🚫 Ban User", "input_ban", style=rp.Style.DANGER),
            rp.btn("✅ Unban User", "input_unban", style=rp.Style.SUCCESS)
        ),
        rp.buttons(
            rp.btn("👢 Kick User", "input_kick", style=rp.Style.DANGER),
            rp.btn("🔍 Find User", "input_find", style=rp.Style.DEFAULT)
        ),
        rp.buttons(
            rp.btn("♻️ Reset User Data", "input_resetuser", style=rp.Style.DEFAULT)
        ),
        rp.buttons(rp.btn(back_text, back_cb, style=rp.Style.DANGER))
    )


def build_admin_approvals_panel_msg(back_cb="goto_admin_panel"):
    back_text = "🔙 Back to Admin Panel" if back_cb == "goto_admin_panel" else "🔙 Back to Terminal"
    return rp.message(
        rp.heading("✅ Access Approvals"),
        rp.divider(),
        rp.expandable_quote([
            rp.bold("📌 Approvals Guide Pointers:\n\n"),
            rp.bold("• "), "🕐 ", rp.bold("Approve Demo"), " — ", rp.italic("Demo access grant karein\n\n"),
            rp.bold("• "), "✅ ", rp.bold("Approve Perm"), " — ", rp.italic("Permanent access approve karein\n\n"),
            rp.bold("• "), "⏳ ", rp.bold("Extend Demo"), " — ", rp.italic("User ke time limit ko extend karein\n\n"),
            rp.bold("• "), "🔗 ", rp.bold("Generate Link"), " — ", rp.italic("3-hour demo invite link banayein")
        ]),
        rp.buttons(
            rp.btn("🕐 Approve Demo", "input_demo", style=rp.Style.SUCCESS),
            rp.btn("✅ Approve Perm", "input_perm", style=rp.Style.SUCCESS)
        ),
        rp.buttons(
            rp.btn("⏳ Extend Demo", "input_extend", style=rp.Style.DEFAULT),
            rp.btn("🔗 Gen Demo Link", "input_gendemo", style=rp.Style.DEFAULT)
        ),
        rp.buttons(rp.btn(back_text, back_cb, style=rp.Style.DANGER))
    )


def build_commands_manual_msg():
    return rp.message(
        rp.heading("📖 Master Commands & Manual"),
        rp.para("Neeche sabhi hidden aur terminal commands diye gaye hain. Expand karke details dekhein:"),
        rp.divider(),
        rp.expandable_quote([
            rp.bold("🤖 Userbot & Cleanup Commands:\n\n"),
            rp.bold("• "), "🌐 ", rp.code("/joinall"), " — Userbot sabhi channels auto-join karein\n\n",
            rp.bold("• "), "🧹 ", rp.code("/emptybatch <chat_id>"), " — Batch se un-approved mass-kick karein\n\n",
            rp.bold("• "), "🗑️ ", rp.code("/clear <chat_id>"), " — Specific chat ke messages delete karein\n\n",
            rp.bold("• "), "❌ ", rp.code("/del <N>"), " — Pichle N messages delete karein (Ex: ", rp.code("/del 10"), ")"
        ]),
        rp.expandable_quote([
            rp.bold("🔑 Access & Approvals Commands:\n\n"),
            rp.bold("• "), "⏳ ", rp.code("/demo <link> <time>"), " — Temporary demo approve karein\n\n",
            rp.bold("• "), "✅ ", rp.code("/perm <link>"), " — Permanent access approve karein\n\n",
            rp.bold("• "), "🔗 ", rp.code("/gendemo <uid> <bid>"), " — 3-hour demo invite link banayein\n\n",
            rp.bold("• "), "⏱️ ", rp.code("/extend <uid> <bid> <hours>"), " — Active demo extend karein\n\n",
            rp.bold("• "), "👢 ", rp.code("/kick <uid> <bid>"), " — User ko channel se remove karein"
        ]),
        rp.expandable_quote([
            rp.bold("💎 Batch & Config Commands:\n\n"),
            rp.bold("• "), "💰 ", rp.code("/setprice <bid> <price>"), " — Paid Batch price update karein\n\n",
            rp.bold("• "), "📝 ", rp.code("/setrequestnote <msg>"), " — Batch demand note customize karein\n\n",
            rp.bold("• "), "👋 ", rp.code("/setwelcome <bid> <msg>"), " — Custom welcome message set karein\n\n",
            rp.bold("• "), "🤖 ", rp.code("/settestbot <link>"), " — Test bot link update karein"
        ]),
        rp.expandable_quote([
            rp.bold("💾 Database & Promo Commands:\n\n"),
            rp.bold("• "), "🎟️ ", rp.code("/genvoucher <CODE> <PTS> <USES>"), " — Promo voucher banayein\n\n",
            rp.bold("• "), "📋 ", rp.code("/vouchers"), " — Active promo vouchers ki list dekhein\n\n",
            rp.bold("• "), "🙋 ", rp.code("/batchrequests"), " — Users ki batch demands dekhein\n\n",
            rp.bold("• "), "💾 ", rp.code("/backup"), " / ", rp.code("/sync"), " / ", rp.code("/allusers")
        ]),
        rp.buttons(rp.btn("🔙 Back to Terminal", "dash_home", style=rp.Style.DANGER))
    )


def build_owner_panel_kb():
    return rp.message(
        rp.heading("👑 SYSTEM MASTER TERMINAL"),
        rp.divider(),
        rp.expandable_quote([
            rp.bold("📌 System Master Modules Pointers:\n\n"),
            rp.bold("• "), "🔒 ", rp.bold("Security"), " — ", rp.italic("Lockdown, locks, maintenance mode\n\n"),
            rp.bold("• "), "💾 ", rp.bold("Database"), " — ", rp.italic("Backup, users, coins, vouchers\n\n"),
            rp.bold("• "), "📦 ", rp.bold("Batches"), " — ", rp.italic("Add/delete batches & categories\n\n"),
            rp.bold("• "), "🧑‍💼 ", rp.bold("Staff"), " — ", rp.italic("Admin permissions grant/revoke & staff list\n\n"),
            rp.bold("• "), "📢 ", rp.bold("Comms"), " — ", rp.italic("Global broadcast & batch notifications\n\n"),
            rp.bold("• "), "📊 ", rp.bold("Analytics"), " — ", rp.italic("Real-time performance & stats\n\n"),
            rp.bold("• "), "🤖 ", rp.bold("Userbot"), " — ", rp.italic("String session manager & status\n\n"),
            rp.bold("• "), "📖 ", rp.bold("Commands"), " — ", rp.italic("Hidden commands manual")
        ]),
        rp.buttons(
            rp.btn("🔒 Security & Locks", "dash_locks", style=rp.Style.DEFAULT),
            rp.btn("💾 Database Tools", "dash_db", style=rp.Style.DEFAULT)
        ),
        rp.buttons(
            rp.btn("📦 Batches & Cats", "dash_batches", style=rp.Style.DEFAULT),
            rp.btn("🧑‍💼 Staff & Admins", "dash_staff", style=rp.Style.DEFAULT)
        ),
        rp.buttons(
            rp.btn("📢 Communications", "dash_comms", style=rp.Style.DEFAULT),
            rp.btn("📊 Analytics & Stats", "dash_stats", style=rp.Style.DEFAULT)
        ),
        rp.buttons(
            rp.btn("🤖 Userbot Login", "userbot_details", style=rp.Style.DEFAULT),
            rp.btn("📖 All Commands", "dash_commands", style=rp.Style.DEFAULT)
        ),
        rp.buttons(
            rp.btn("🔄 Switch Panel", "role_selector", style=rp.Style.SUCCESS)
        )
    )


def build_admin_panel_kb():
    return rp.message(
        rp.heading("🛡 ADMINISTRATOR DASHBOARD"),
        rp.divider(),
        rp.expandable_quote([
            rp.bold("📌 Admin Tools Pointers:\n\n"),
            rp.bold("• "), "👥 ", rp.bold("Users"), " — ", rp.italic("Ban, Unban, Kick, Find & Reset user data\n\n"),
            rp.bold("• "), "✅ ", rp.bold("Approvals"), " — ", rp.italic("Demo aur Permanent access request approval\n\n"),
            rp.bold("• "), "📦 ", rp.bold("Batches"), " — ", rp.italic("Add/Delete batches & categories\n\n"),
            rp.bold("• "), "📢 ", rp.bold("Comms"), " — ", rp.italic("Broadcast announcements & custom posts")
        ]),
        rp.buttons(
            rp.btn("👥 Users Management", "adash_users", style=rp.Style.DEFAULT),
            rp.btn("✅ Approvals Center", "adash_approvals", style=rp.Style.DEFAULT)
        ),
        rp.buttons(
            rp.btn("📦 Batches Management", "adash_batches", style=rp.Style.DEFAULT),
            rp.btn("📢 Communications", "adash_comms", style=rp.Style.DEFAULT)
        ),
        rp.buttons(
            rp.btn("🔄 Switch Panel", "role_selector", style=rp.Style.SUCCESS)
        )
    )


def build_role_selector_kb(user_id):
    is_owner = str(user_id) == str(OWNER_ID)
    owner_btn = []
    if is_owner:
        owner_btn = [rp.buttons(rp.btn("👑 Owner Panel", "goto_owner_panel", style=rp.Style.DEFAULT))]
        
    return rp.message(
        rp.heading("🎛 Select Panel"),
        rp.para(rp.bold("Aap kis panel me jaana chahte hain?")),
        rp.divider(),
        *owner_btn,
        rp.buttons(
            rp.btn("🛡 Admin Panel", "goto_admin_panel", style=rp.Style.DEFAULT),
            rp.btn("👤 User Panel", "goto_user_panel", style=rp.Style.DEFAULT)
        )
    )


async def show_role_selector(client: Client, message: Message, user):
    msg = build_role_selector_kb(user.id)
    await client.send_rich_message(message.chat.id, rich_message=msg)


async def show_role_selector_cb(client: Client, q: CallbackQuery):
    msg = build_role_selector_kb(q.from_user.id)
    await rp.edit(client, q.message.chat.id, q.message.id, msg)


async def goto_owner_panel_cb(client: Client, q: CallbackQuery):
    if str(q.from_user.id) != str(OWNER_ID):
        return await q.answer("  Access Denied! Owner Only.", show_alert=True)
    await q.answer()
    msg = build_owner_panel_kb()
    await rp.edit(client, q.message.chat.id, q.message.id, msg)


async def goto_admin_panel_cb(client: Client, q: CallbackQuery):
    if not is_admin(q.from_user.id):
        return await q.answer("  Access Denied! Admins Only.", show_alert=True)
    await q.answer()
    msg = build_admin_panel_kb()
    await rp.edit(client, q.message.chat.id, q.message.id, msg)


async def goto_user_panel_cb(client: Client, q: CallbackQuery):
    await q.answer()
    await show_user_menu_cb(client, q)


async def start(client: Client, message: Message):
    user = message.from_user
    await safe_reaction(client, message.chat.id, message.id, "🎉")
    await set_role_based_commands(user.id, client)
    
    user_key = user.id if user.id in DB.get("USER_DATA", {}) else (str(user.id) if str(user.id) in DB.get("USER_DATA", {}) else user.id)
    
    # STRICT NEW USER CHECK FLAG
    is_new_user = False
    
    if user_key not in DB["USER_DATA"]:
        is_new_user = True
        DB["USER_DATA"][user_key] = {
            "name": f"{user.first_name or ''} {user.last_name or ''}".strip(),
            "username": user.username,
            "joined_at": time.time(),
            "demos": {},
            "tnc_accepted": False,
            "unlocked_batches": [],
            "referral_count": 0,
            "total_invited": 0
        }
        await save_data_async()

    DB["USER_DATA"][user_key].setdefault("unlocked_batches", [])

    # --- CHECK REFERRAL DEEP LINK PARAMETER ---
    args = get_args(message)
    target_batch_to_open = None
    
    if args:
        param = args[0].strip()
        referrer_id = None
        
        # Case A: Batch Share Link (batch_CID_UID)
        if param.startswith("batch_"):
            parts = param.split("_")
            if len(parts) >= 3:
                target_batch_to_open = parts[1]
                if parts[2].isdigit():
                    referrer_id = int(parts[2])
            elif len(parts) == 2 and parts[1].lstrip("-").isdigit():
                target_batch_to_open = parts[1]
                
        # Case B: Standard Referral
        elif param.isdigit():
            referrer_id = int(param)
        elif param.startswith("ref_"):
            parts = param.split("_")
            if len(parts) >= 2 and parts[-1].isdigit():
                referrer_id = int(parts[-1])

        # Case C: Giveaway Candidate Vote Link (vote_CANDIDATEID)
        elif param.startswith("vote_"):
            parts = param.split("_")
            if len(parts) >= 2 and parts[1].isdigit():
                cand_id = parts[1]
                if str(cand_id) != str(user.id):
                    referrer_id = int(cand_id)
                    DB["USER_DATA"][user_key]["pending_vote"] = cand_id

        # Case D: Batch Demand Campaign Vote Link (demand_INDEX)
        elif param.startswith("demand_"):
            parts = param.split("_")
            if len(parts) >= 2 and parts[1].isdigit():
                demand_idx = parts[1]
                DB["USER_DATA"][user_key]["pending_demand"] = demand_idx

        # Save pending referral if it's a new user
        if is_new_user and referrer_id and str(referrer_id) != str(user.id):
            DB["USER_DATA"][user_key]["pending_referral"] = referrer_id
            
        # Save pending batch to show immediately after mandatory checks
        if target_batch_to_open:
            DB["USER_DATA"][user_key]["pending_batch"] = target_batch_to_open
            
        await save_data_async()

    await get_or_create_topic(user, client)

    import richpyro as rp
    # --- LOADING ANIMATION ---
    loading_msg = await rp.send(
        client, 
        message.chat.id, 
        rp.message(rp.para("⏳ ", rp.italic("Loading, please wait...")))
    )
    await asyncio.sleep(0.7)

    if str(user.id) == str(OWNER_ID) or is_admin(user.id):
        # Owner ko 3 panel (Owner/Admin/User) aur Admin ko 2 panel (Admin/User) dikhega
        msg = build_role_selector_kb(user.id)
        await rp.edit(client, loading_msg.chat.id, loading_msg.id, msg)
    elif await check_membership_pyro(user.id, client):
        if not DB["USER_DATA"].get(user_key, {}).get("tnc_accepted", False):
            tnc_msg = rp.message(
                rp.para("⚠️ ", rp.bold("STRICT WARNING & TERMS OF SERVICE")),
                rp.divider(),
                rp.para("🇬🇧 ", rp.bold("ENGLISH:")),
                rp.para("If you leave the Main Channel or block this bot, you will be ", rp.bold("INSTANTLY REMOVED"), " from ALL joined groups and channels."),
                rp.divider(),
                rp.para("🇮🇳 ", rp.bold("HINDI:")),
                rp.para("Agar aapne Main Channel ko chhoda (leave kiya) ya is bot ko block kiya, toh aapko sabhi groups aur channels se ", rp.bold("TURANT NIKAL"), " diya jayega."),
                rp.divider(),
                rp.para("✅ ", rp.italic("Click 'I Read & Accept' only if you agree to these terms.")),
                rp.buttons(rp.btn("✅ I Read & Accept", "accept_tnc", style=rp.Style.SUCCESS))
            )
            await rp.edit(client, message.chat.id, loading_msg.id, tnc_msg)
        else:
            if "pending_referral" in DB["USER_DATA"][user_key]:
                referrer_id = DB["USER_DATA"][user_key].pop("pending_referral")
                await save_data_async()
                await process_successful_referral(client, user.id, referrer_id)

            if "pending_vote" in DB["USER_DATA"][user_key]:
                cand_id = DB["USER_DATA"][user_key].pop("pending_vote")
                await save_data_async()
                from .economy import cast_giveaway_vote
                await cast_giveaway_vote(client, user, cand_id)

            if "pending_demand" in DB["USER_DATA"][user_key]:
                d_idx = DB["USER_DATA"][user_key].pop("pending_demand")
                await save_data_async()
                bname = DB.get("DEMAND_SLUGS", {}).get(str(d_idx))
                if bname:
                    from .batches import cast_batch_demand_vote
                    await cast_batch_demand_vote(client, user.id, bname)

            msg = build_home_menu(user_key, user)
            await rp.edit(client, loading_msg.chat.id, loading_msg.id, msg)
    else:
        if not DB.get("NEW_USERS_ALLOWED", True):
            return await loading_msg.edit_text("🚫 **Entry Closed!**", parse_mode=ParseMode.MARKDOWN)
        kb = [
            [InlineKeyboardButton("📢 Join Channel", url=MANDATORY_CHANNEL_LINK)],
            [InlineKeyboardButton("✅ I've Joined", callback_data="verify")],
        ]
        await loading_msg.edit_text(
            "📢 **Join Main Channel First / पहले Channel Join Karein**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "🇬🇧 **1️⃣** Tap **📢 Join Channel** below and join it.\n"
            "🇮🇳 **1️⃣** Neeche **📢 Join Channel** par tap karke channel join karein.\n\n"
            "🇬🇧 **2️⃣** Come back here and tap **✅ I've Joined** to unlock the bot.\n"
            "🇮🇳 **2️⃣** Wapas yahan aakar **✅ I've Joined** par tap karein aur bot unlock karein.\n\n"
            "💡 *This unlocks batches, free demos, and referral rewards.*",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode=ParseMode.MARKDOWN,
        )


async def start_from_cb(client: Client, q: CallbackQuery):
    user = q.from_user
    user_key = user.id if user.id in DB.get("USER_DATA", {}) else (str(user.id) if str(user.id) in DB.get("USER_DATA", {}) else user.id)
    await set_role_based_commands(user.id, client)
    
    if str(user.id) == str(OWNER_ID):
        msg = build_owner_panel_kb()
        await rp.edit(client, q.message.chat.id, q.message.id, msg)
    elif is_admin(user.id):
        msg = build_admin_panel_kb()
        await rp.edit(client, q.message.chat.id, q.message.id, msg)
    elif await check_membership_pyro(user.id, client):
        if not DB["USER_DATA"].get(user_key, {}).get("tnc_accepted", False):
            await show_tnc_menu_cb(client, q)
        else:
            if "pending_referral" in DB["USER_DATA"].get(user_key, {}):
                referrer_id = DB["USER_DATA"][user_key].pop("pending_referral")
                await save_data_async()
                await process_successful_referral(client, user.id, referrer_id)
            await show_user_menu_cb(client, q)
    else:
        if not DB.get("NEW_USERS_ALLOWED", True):
            return await q.edit_message_text("🚫 **Entry Closed!**", parse_mode=ParseMode.MARKDOWN)
        kb = [
            [InlineKeyboardButton("📢 Join Channel", url=MANDATORY_CHANNEL_LINK)],
            [InlineKeyboardButton("✅ I've Joined", callback_data="verify")],
        ]
        await q.edit_message_text(
            "📢 **Join Main Channel First / पहले Channel Join Karein**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "🇬🇧 **1️⃣** Tap **📢 Join Channel** below and join it.\n"
            "🇮🇳 **1️⃣** Neeche **📢 Join Channel** par tap karke channel join karein.\n\n"
            "🇬🇧 **2️⃣** Come back here and tap **✅ I've Joined** to unlock the bot.\n"
            "🇮🇳 **2️⃣** Wapas yahan aakar **✅ I've Joined** par tap karein aur bot unlock karein.\n\n"
            "💡 *This unlocks batches, free demos, and referral rewards.*",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode=ParseMode.MARKDOWN,
        )



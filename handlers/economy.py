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

async def process_successful_referral(client: Client, referee_id: int, referrer_id: int):
    # Strict key check (integer and string types handles both safely)
    referrer_key = str(referrer_id) if str(referrer_id) in DB["USER_DATA"] else (referrer_id if referrer_id in DB["USER_DATA"] else str(referrer_id))
    referee_key = str(referee_id) if str(referee_id) in DB["USER_DATA"] else (referee_id if referee_id in DB["USER_DATA"] else str(referee_id))
    
    if referrer_key not in DB["USER_DATA"]:
        return

    # Coins (referral_count) and Invites badhayein instantly
    DB["USER_DATA"][referrer_key]["referral_count"] = DB["USER_DATA"][referrer_key].get("referral_count", 0) + 1
    DB["USER_DATA"][referrer_key]["total_invited"] = DB["USER_DATA"][referrer_key].get("total_invited", 0) + 1

    # Har 5 refers par +1 bonus coin logic
    milestone_hit = DB["USER_DATA"][referrer_key]["total_invited"] % 5 == 0
    if milestone_hit:
        DB["USER_DATA"][referrer_key]["referral_count"] += 1

    total_now = DB["USER_DATA"][referrer_key]["total_invited"]
    newly_vip = total_now >= 25 and DB["USER_DATA"][referrer_key].get("tier") != "vip"
    if newly_vip:
        DB["USER_DATA"][referrer_key]["tier"] = "vip"

    if referee_key in DB["USER_DATA"]:
        DB["USER_DATA"][referee_key]["referred_by"] = referrer_id
        DB["USER_DATA"][referee_key].setdefault("welcome_bonus_claimed", False)

    await save_data_async()

    # --- NAYA LOGIC: SUPPORT GROUP TOPIC NOTIFICATION ---
    from config import SUPPORT_GROUP_ID, get_or_create_topic
    if SUPPORT_GROUP_ID:
        try:
            referrer_user = await client.get_users(int(referrer_id))
            topic_id = await get_or_create_topic(referrer_user, client)
            if topic_id:
                await client.send_message(
                    chat_id=int(SUPPORT_GROUP_ID),
                    text=f"🎉 <b>{total_now} refers successful by the user</b>",
                    message_thread_id=topic_id,
                    parse_mode=ParseMode.HTML
                )
        except Exception as e:
            logger.error(f"Support topic notification failed: {e}")

    # Welcome bonus check for the referrer if they were pending
    if not DB["USER_DATA"][referrer_key].get("welcome_bonus_claimed", True) and DB["USER_DATA"][referrer_key].get("referred_by"):
        DB["USER_DATA"][referrer_key]["referral_count"] = DB["USER_DATA"][referrer_key].get("referral_count", 0) + 1
        DB["USER_DATA"][referrer_key]["welcome_bonus_claimed"] = True
        await save_data_async()
        try:
            await client.send_message(
                referrer_id,
                "🔓 **WELCOME BONUS UNLOCKED!**\n\n"
                "Aapne apna pehla successful refer kar diya, isliye aapka pending **1 Coin Welcome Bonus** bhi ab wallet me add ho gaya hai! 🎉",
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception:
            pass

    # Referrer message
    try:
        await client.send_message(
            referrer_id,
            f"🎉 **REFERRAL SUCCESSFUL!**\n\n"
            f"Aapke bheje gaye link se ek naye user ne saare steps complete kar liye hain! Aapko **1 Coin** mil gaya hai. 🎁\n"
            f"💰 Total Balance: `{DB['USER_DATA'][referrer_key]['referral_count']}` Coins",
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception:
        pass

    if milestone_hit:
        try:
            await client.send_message(
                referrer_id,
                f"🏆 **MILESTONE BONUS!**\n\n"
                f"Aapne **{total_now} successful refers** poore kar liye hain — har 5 refers par 1 EXTRA Coin milta hai. "
                f"Aapke wallet me **+1 Bonus Coin** add ho gaya hai! 🎁",
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception:
            pass

    if newly_vip:
        try:
            await client.send_message(
                referrer_id,
                "👑 **VIP REFERRER UNLOCKED!**\n\n"
                "Aapne 25+ dosto ko refer kar diya hai! Aapko ab bot ke andar **👑 VIP Referrer** tag mil gaya hai — "
                "yeh 'My Info' aur 'Refer & Earn' section me dikhega.",
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception:
            pass

    if DB["USER_DATA"][referrer_key].get("tier") == "vip":
        await send_vip_treat(client, referrer_id)

    try:
        await client.send_message(
            referee_id,
            "👋 **WELCOME!**\n\n"
            "Aapne referral link se join kiya hai. Apna **1 Coin Welcome Bonus** paane ke liye, "
            "aapko bhi apna khud ka referral link kam se kam ek dost ko bhejna hoga aur unse join karwana hoga. 🎁\n"
            "Apna link paane ke liye 'Refer & Earn' button dabayein.",
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception:
        pass


async def cmd_genvoucher(client: Client, message: Message):
    if not is_admin_msg(message):
        return
    args = get_args(message)
    if len(args) < 3:
        return await message.reply_text(
            "🎟️ **Usage:** `/genvoucher <CODE> <POINTS> <MAX_USES>`\n"
            "Example: `/genvoucher DIWALI2026 5 100`",
            parse_mode=ParseMode.MARKDOWN
        )
    code = args[0].upper().strip()
    try:
        pts = float(args[1])
        max_uses = int(args[2])
    except ValueError:
        return await message.reply_text("❌ Error: Points aur Max Uses numeric hone chahiye.")

    DB.setdefault("VOUCHERS", {})[code] = {
        "points": pts,
        "max_uses": max_uses,
        "used_by": [],
        "created_at": time.time()
    }
    await save_data_async()

    card = build_giveaway_claim_card(code, pts, max_uses, 0)
    pyro_kb = to_pyrogram_markup(card["reply_markup"])
    copy_btn = make_copy_button(f"📋 Copy: {code}", code)
    pyro_kb.inline_keyboard.append([
        copy_btn,
        InlineKeyboardButton("📢 Broadcast Giveaway", callback_data=f"bc_giveaway_{code}")
    ])

    await message.reply_text(card["text"], reply_markup=pyro_kb, parse_mode=ParseMode.HTML)


async def cmd_list_vouchers(client: Client, message: Message):
    if not is_admin_msg(message):
        return
    vouchers = DB.get("VOUCHERS", {})
    if not vouchers:
        return await message.reply_text("📭 Abhi tak koi vouchers create nahi kiye gaye hain.")
    txt = "🎟️ **ACTIVE PROMO VOUCHERS**\n━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    for code, data in vouchers.items():
        used = len(data.get("used_by", []))
        max_u = data.get("max_uses", 1)
        pts = data.get("points", 0)
        txt += f"• `{code}`: `{pts} Coins` | `{used}/{max_u} Used`\n"
    if len(txt) > 3500:
        f = io.BytesIO(txt.encode("utf-8"))
        f.name = "active_vouchers.txt"
        f.seek(0)
        await message.reply_document(document=f, caption="🎟️ Active Vouchers List (File)")
    else:
        await message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)


async def cmd_genvoucher_from_wizard(client: Client, message: Message):
    if not is_admin_msg(message):
        return
    parts = (message.text or "").strip().split()
    if len(parts) < 3:
        return await message.reply_text(
            "❌ Format invalid! Kripya is format me bhejein:\n`<CODE> <COINS> <MAX_USES>`\nExample: `DIWALI 5 100`",
            parse_mode=ParseMode.MARKDOWN
        )
    code = parts[0].upper().strip()
    try:
        pts = float(parts[1])
        max_uses = int(parts[2])
    except ValueError:
        return await message.reply_text("❌ Error: Coins aur Max Uses numeric hone chahiye.")

    DB.setdefault("VOUCHERS", {})[code] = {
        "points": pts,
        "max_uses": max_uses,
        "used_by": [],
        "created_at": time.time()
    }
    await save_data_async()

    card = build_giveaway_claim_card(code, pts, max_uses, 0)
    pyro_kb = to_pyrogram_markup(card["reply_markup"])
    copy_btn = make_copy_button(f"📋 Copy: {code}", code)
    pyro_kb.inline_keyboard.append([
        copy_btn,
        InlineKeyboardButton("📢 Broadcast Giveaway", callback_data=f"bc_giveaway_{code}")
    ])

    await message.reply_text(card["text"], reply_markup=pyro_kb, parse_mode=ParseMode.HTML)


async def cmd_myinfo(client: Client, message: Message):
    if not message.from_user:
        return
    uid = message.from_user.id
    user_key = uid if uid in DB["USER_DATA"] else str(uid)
    u_data = DB["USER_DATA"].get(user_key, {})
    widget = build_student_wallet_widget(uid, u_data)
    pm = to_pyrogram_markup(widget.get("reply_markup"))
    await message.reply_text(widget["text"], reply_markup=pm, parse_mode=ParseMode.HTML)


async def cmd_user_details(client: Client, message: Message):
    if not is_admin_msg(message):
        return
    args = get_args(message)
    try:
        target_id = int(args[0])
    except Exception:
        return await message.reply_text("Usage: /user [id]")
    info = DB["USER_DATA"].get(target_id, {})
    r = (
        f"USER DETAILS: {target_id}\nName: {info.get('name', 'Unknown')}\n\n"
        + ("  BLOCKED\n\n" if target_id in DB.get("BLOCKED_USERS", []) else "")
        + "--- MEMBERSHIP ---\n"
    )
    all_target_cids = list(set(
        list(DB.get("ALL_CHATS", {}).keys())
        + list(DB.get("FREE_CHANNELS", {}).keys())
        + list(DB.get("PAID_CHANNELS", {}).keys())
        + list(DB.get("SPECIAL_CHANNELS", {}).keys())
    ))
    found = False
    sem = asyncio.Semaphore(5)

    async def _check_membership(cid):
        async with sem:
            try:
                m = await client.get_chat_member(int(cid), target_id)
                if m.status in [
                    ChatMemberStatus.MEMBER,
                    ChatMemberStatus.ADMINISTRATOR,
                    ChatMemberStatus.OWNER,
                    ChatMemberStatus.RESTRICTED,
                ]:
                    chat_name = DB.get('ALL_CHATS', {}).get(cid, cid)
                    return f"{chat_name}: Joined\n"
            except Exception:
                pass
            return None

    results = await asyncio.gather(*[_check_membership(cid) for cid in all_target_cids], return_exceptions=True)
    for res in results:
        if isinstance(res, str) and res:
            r += res
            found = True
    if not found:
        r += "Not found in any batch.\n"
    if "demo_history" in info:
        r += "\n--- DEMO HISTORY ---\n" + "\n".join([f"  {h}" for h in info["demo_history"]])
    f = io.BytesIO(r.encode("utf-8"))
    f.name = f"scan_{target_id}.txt"
    f.seek(0)
    await message.reply_document(document=f, caption="  Deep Scan")


# =====================================================================
# 🎁 VIRAL FREE BATCH GIVEAWAY & VOTING ENGINE
# =====================================================================

def build_giveaway_hub_msg(user_id: int, bot_username: str = "H4R_Contact_bot"):
    """
    Builds the interactive Giveaway Hub message.
    - If user entered: displays their target batch, votes count, rank, and shareable vote link.
    - If user not entered: checks coins (3 coins required). If < 3, shows invite link to earn coins.
      If >= 3, shows button to register candidate batch.
    """
    cfg = DB.get("GIVEAWAY_CONFIG", {"active": True, "title": "🎉 Free Batch Mega Giveaway", "required_coins": 3})
    title = cfg.get("title", "🎉 Free Batch Mega Giveaway")
    is_active = cfg.get("active", True)
    req_coins = cfg.get("required_coins", 3)
    
    if not is_active:
        return rp.message(
            rp.heading("🎁 Mega Free Batch Giveaway"),
            rp.divider(),
            rp.expandable_quote([
                rp.bold("⚠️ Giveaway Paused:\n\n"),
                "Abhi koi active giveaway nahi chal raha hai.\n",
                "Next mega giveaway jald hi announce hoga. Tab tak ke liye daily streak aur referral coins collect karte rahein! 🚀"
            ]),
            rp.divider(),
            rp.buttons(
                rp.btn("🏆 Previous Leaderboard", "giveaway_leaderboard", style=rp.Style.PRIMARY),
                rp.btn("🏠 Main Menu", "u_main", style=rp.Style.DANGER)
            )
        )

    user_key = str(user_id) if str(user_id) in DB.get("USER_DATA", {}) else user_id
    user_coins = DB.get("USER_DATA", {}).get(user_key, {}).get("referral_count", 0)
    participants = DB.get("GIVEAWAY_PARTICIPANTS", {})
    entry = participants.get(str(user_id))

    if entry:
        target_batch = entry.get("batch", "Any Batch of Choice")
        votes = entry.get("votes", 0)
        
        sorted_p = sorted(participants.items(), key=lambda x: x[1].get("votes", 0), reverse=True)
        rank = 1
        for idx, (p_id, p_data) in enumerate(sorted_p, start=1):
            if str(p_id) == str(user_id):
                rank = idx
                break
        
        vote_link = f"https://t.me/{bot_username}?start=vote_{user_id}"
        share_url = f"https://t.me/share/url?url={urllib.parse.quote(vote_link)}&text={urllib.parse.quote(f'🔥 Bhaiyon, mujhe Free Batch Giveaway me vote karein! Mera target batch: {target_batch}. Ek vote se mera batch unlock hoga! 🗳️')}"

        return rp.message(
            rp.heading(title),
            rp.divider(),
            rp.marked(f" 🎟️ Candidate Status: REGISTERED | 🏆 Current Rank: #{rank} "),
            rp.table(
                rows=[
                    [rp.table_cell("Target Batch", is_header=True), rp.table_cell("Total Votes", is_header=True), rp.table_cell("Leaderboard Rank", is_header=True)],
                    [rp.table_cell(f"🎯 {target_batch[:16]}"), rp.table_cell(f"🗳️ {votes} Votes"), rp.table_cell(f"🏅 #{rank}")]
                ],
                bordered=True, striped=True
            ),
            rp.divider(),
            rp.expandable_quote([
                rp.bold("📢 Apne Votes Badhayein (Viral Strategy):\n\n"),
                "1. Neeche diye gaye ", rp.bold("Share & Rally Votes"), " button ko apne college WhatsApp & Telegram groups me bhejein.\n",
                "2. Har student jo aapke link par click karke bot start karega, aapko ", rp.bold("+1 Vote"), " milega!\n",
                "3. Contest end hone par sabse zyada votes wale candidate ko unka manpasand batch bilkul ", rp.bold("100% FREE"), " unlock ho jayega! 🎁"
            ]),
            rp.para("🔗 ", rp.bold("Your Personal Voting Link:\n"), rp.code(vote_link)),
            rp.divider(),
            rp.buttons(
                rp.url_btn("📤 Share & Rally Votes", share_url, style=rp.Style.LINK),
                rp.btn("🏆 Live Leaderboard", "giveaway_leaderboard", style=rp.Style.SUCCESS)
            ),
            rp.buttons(
                rp.btn("🔄 Refresh Status", "menu_giveaway", style=rp.Style.PRIMARY),
                rp.btn("🏠 Main Menu", "u_main", style=rp.Style.DANGER)
            )
        )
    else:
        vote_count_total = len(DB.get("GIVEAWAY_VOTES", {}))
        contestant_count = len(participants)

        if user_coins < req_coins:
            needed = req_coins - user_coins
            ref_link = f"https://t.me/{bot_username}?start=ref_{user_id}"
            share_ref = f"https://t.me/share/url?url={urllib.parse.quote(ref_link)}&text={urllib.parse.quote('Join H4R Contact Bot for Free SSC JE & Engineering Batches, Notes & Quizzes! 📚')}"
            
            return rp.message(
                rp.heading(title),
                rp.divider(),
                rp.para(
                    "🎁 ", rp.bold("Win ANY Course/Batch of your choice 100% FREE!\n"),
                    "Jis student ke pass sabse zyada votes honge, usko unka manpasand batch giveaway me diya jayega."
                ),
                rp.marked(f" 👥 Total Contestants: {contestant_count} | 🗳️ Total Votes Cast: {vote_count_total} "),
                rp.divider(),
                rp.expandable_quote([
                    rp.bold("🎟️ Entry Requirements:\n\n"),
                    f"• Giveaway me participate karne ke liye ", rp.bold(f"{req_coins} Coins"), " ki zaroorat hai.\n",
                    f"• Aapke wallet me abhi: ", rp.bold(f"{user_coins} Coins"), f" hain (Aur ", rp.bold(f"{needed} Coins"), " chahiye).\n\n",
                    "💡 ", rp.bold("Coins Kaise Kamayein?\n"),
                    "• Har 1 friend ko invite karne par ", rp.bold("+1 Coin"), " milta hai!\n",
                    f"• Bas {needed} friends ko apna link share karein aur turant Giveaway me enter karein!"
                ]),
                rp.para("🔗 ", rp.bold("Your Referral Invite Link:\n"), rp.code(ref_link)),
                rp.divider(),
                rp.buttons(
                    rp.url_btn("🚀 Invite Friends (+1 Coin)", share_ref, style=rp.Style.LINK),
                    rp.btn("🏆 View Leaderboard", "giveaway_leaderboard", style=rp.Style.PRIMARY)
                ),
                rp.buttons(rp.btn("🏠 Main Menu", "u_main", style=rp.Style.DANGER))
            )
        else:
            return rp.message(
                rp.heading(title),
                rp.divider(),
                rp.marked(f" 💎 Your Balance: {user_coins} Coins | 🎟️ Entry Fee: {req_coins} Coins "),
                rp.divider(),
                rp.expandable_quote([
                    rp.bold("🎉 You are Eligible to Participate!\n\n"),
                    f"Aapke paas ", rp.bold(f"{user_coins} Coins"), f" hain! Aap ", rp.bold(f"{req_coins} Coins"), " use karke Giveaway Contest me register kar sakte hain.\n\n",
                    "• Register karne ke baad aapko apna personal ", rp.bold("Voting Link"), " milega.\n",
                    "• Friends aur groups se votes collect karein. Top candidate ko unka batch free milega!"
                ]),
                rp.divider(),
                rp.buttons(
                    rp.btn(f"🎟️ Enter Contest ({req_coins} Coins)", "giveaway_enter_prompt", style=rp.Style.SUCCESS),
                    rp.btn("🏆 Live Leaderboard", "giveaway_leaderboard", style=rp.Style.PRIMARY)
                ),
                rp.buttons(rp.btn("🏠 Main Menu", "u_main", style=rp.Style.DANGER))
            )


def build_giveaway_leaderboard_msg(page: int = 0, current_uid: int = None):
    """
    Builds the real-time Giveaway Hall of Fame & Leaderboard.
    """
    participants = DB.get("GIVEAWAY_PARTICIPANTS", {})
    sorted_p = sorted(participants.items(), key=lambda x: x[1].get("votes", 0), reverse=True)
    total = len(sorted_p)

    if total == 0:
        return rp.message(
            rp.heading("🏆 Giveaway Leaderboard"),
            rp.divider(),
            rp.expandable_quote([
                rp.bold("ℹ️ No Contestants Yet:\n\n"),
                "Abhi tak kisi candidate ne register nahi kiya hai. Pehle register karein aur top rank payein! 🚀"
            ]),
            rp.divider(),
            rp.buttons(
                rp.btn("🎁 Enter Giveaway", "menu_giveaway", style=rp.Style.SUCCESS),
                rp.btn("🏠 Main Menu", "u_main", style=rp.Style.DANGER)
            )
        )

    PER_PAGE = 8
    total_pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    start_idx = page * PER_PAGE
    end_idx = min(start_idx + PER_PAGE, total)
    page_items = sorted_p[start_idx:end_idx]

    medals = ["🥇", "🥈", "🥉"]
    body_lines = [
        rp.heading("🏆 Giveaway Live Leaderboard"),
        rp.divider(),
        rp.marked(f" 👥 Total Contestants: {total} | 📄 Page: {page + 1}/{total_pages} "),
        rp.divider(),
    ]

    for rank, (cand_id, cand_data) in enumerate(page_items, start=start_idx + 1):
        badge = medals[rank - 1] if rank <= 3 else f"#{rank}"
        cname = cand_data.get("name", "Student")
        cbatch = cand_data.get("batch", "Any Batch")[:20]
        votes = cand_data.get("votes", 0)
        is_me = current_uid and str(cand_id) == str(current_uid)
        me_mark = " (You) 📍" if is_me else ""
        
        body_lines.append(
            rp.para(f"{badge} ", rp.bold(f"{cname}{me_mark}"), f" — ", rp.code(f"{votes} Votes"), f"\n   🎯 ", rp.italic(cbatch))
        )

    if current_uid and str(current_uid) in participants:
        user_rank = 1
        for idx, (p_id, _) in enumerate(sorted_p, start=1):
            if str(p_id) == str(current_uid):
                user_rank = idx
                break
        my_votes = participants[str(current_uid)].get("votes", 0)
        body_lines.append(rp.divider())
        body_lines.append(
            rp.para("📍 ", rp.bold(f"Your Standing: Rank #{user_rank}"), f" ({my_votes} Votes)")
        )

    nav_btns = []
    if page > 0:
        nav_btns.append(rp.btn("⬅️ Prev", f"gw_lb_page_{page - 1}", style=rp.Style.DEFAULT))
    if page < total_pages - 1:
        nav_btns.append(rp.btn("Next ➡️", f"gw_lb_page_{page + 1}", style=rp.Style.DEFAULT))

    body_lines.append(rp.divider())
    if nav_btns:
        body_lines.append(rp.buttons(*nav_btns))
    
    body_lines.append(
        rp.buttons(
            rp.btn("🎁 My Giveaway Status", "menu_giveaway", style=rp.Style.SUCCESS),
            rp.btn("🏠 Main Menu", "u_main", style=rp.Style.DANGER)
        )
    )
    return rp.message(*body_lines)


async def cast_giveaway_vote(client: Client, voter_user, candidate_id: str):
    """
    Casts a vote for a candidate in the active giveaway.
    - Prevents voting for self.
    - Prevents duplicate voting by the same voter.
    - Updates candidate vote count and voter list.
    - Notifies the candidate in DM or support group topic.
    """
    participants = DB.get("GIVEAWAY_PARTICIPANTS", {})
    if str(candidate_id) not in participants:
        return False, "Candidate is not registered in the active giveaway."

    if str(voter_user.id) == str(candidate_id):
        return False, "❌ Aap khud ke liye vote nahi kar sakte!"

    votes_map = DB.setdefault("GIVEAWAY_VOTES", {})
    if str(voter_user.id) in votes_map:
        prev_cand = participants.get(votes_map[str(voter_user.id)], {}).get("name", "another student")
        return False, f"⚠️ Aap pehle hi candidate **{prev_cand}** ko vote kar chuke hain!\n*(Har student sirf 1 vote de sakta hai)*"

    votes_map[str(voter_user.id)] = str(candidate_id)
    participants[str(candidate_id)]["votes"] = participants[str(candidate_id)].get("votes", 0) + 1
    participants[str(candidate_id)].setdefault("voters", []).append(voter_user.id)
    await save_data_async()

    cand_name = participants[str(candidate_id)].get("name", "Candidate")
    cand_batch = participants[str(candidate_id)].get("batch", "Target Batch")
    cand_total = participants[str(candidate_id)]["votes"]

    try:
        await client.send_message(
            int(candidate_id),
            f"🎉 **NEW VOTE RECEIVED!**\n━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 **{voter_user.first_name}** ne abhi aapke liye vote kiya hai!\n"
            f"🏆 Total Votes: `{cand_total}`\n"
            f"🎯 Target Batch: `{cand_batch}`\n\n"
            f"Keep sharing your link to maintain top rank! 🚀",
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception:
        pass

    return True, f"✅ Aapka vote **{cand_name}** ({cand_batch}) ke liye count ho gaya!\n🏆 Current Votes: **{cand_total}**"


def build_admin_giveaway_panel_msg():
    """Builds Admin Giveaway Control panel."""
    cfg = DB.get("GIVEAWAY_CONFIG", {"active": True, "title": "🎉 Free Batch Mega Giveaway", "required_coins": 3})
    participants = DB.get("GIVEAWAY_PARTICIPANTS", {})
    votes = DB.get("GIVEAWAY_VOTES", {})
    is_active = cfg.get("active", True)
    
    sorted_p = sorted(participants.items(), key=lambda x: x[1].get("votes", 0), reverse=True)
    top_cand = sorted_p[0][1] if sorted_p else None

    return rp.message(
        rp.heading("🎁 Admin Giveaway Console"),
        rp.divider(),
        rp.marked(f" ⚙️ Status: {'🟢 ACTIVE' if is_active else '🔴 PAUSED'} | 🎟️ Req Coins: {cfg.get('required_coins', 3)} "),
        rp.table(
            rows=[
                [rp.table_cell("Total Contestants", is_header=True), rp.table_cell("Total Votes Cast", is_header=True), rp.table_cell("Current Leader", is_header=True)],
                [rp.table_cell(f"👥 {len(participants)}"), rp.table_cell(f"🗳️ {len(votes)}"), rp.table_cell(f"👑 {top_cand.get('name', 'None')[:14] if top_cand else 'None'} ({top_cand.get('votes', 0) if top_cand else 0})")]
            ],
            bordered=True, striped=True
        ),
        rp.divider(),
        rp.buttons(
            rp.btn("⏸️ Pause/Resume" if is_active else "▶️ Resume Giveaway", "gw_toggle_status", style=rp.Style.PRIMARY),
            rp.btn("👑 Declare Winner", "gw_declare_winner", style=rp.Style.SUCCESS)
        ),
        rp.buttons(
            rp.btn("🏆 View Full Leaderboard", "giveaway_leaderboard", style=rp.Style.PRIMARY),
            rp.btn("🗑️ Reset Contest", "gw_reset_confirm", style=rp.Style.DANGER)
        ),
        rp.buttons(rp.btn("🔙 Back to Database Panel", "dash_db", style=rp.Style.DANGER))
    )


async def cmd_giveaway(client: Client, message: Message):
    """Command /giveaway handler"""
    if not message.from_user:
        return
    bot_info = getattr(client, "me", None)
    bot_uname = bot_info.username if bot_info and bot_info.username else "H4R_Contact_bot"
    msg = build_giveaway_hub_msg(message.from_user.id, bot_uname)
    await rp.send(client, message.chat.id, msg)




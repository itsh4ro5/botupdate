import asyncio
import json
import logging
import os
import sys
from pyrogram import Client
from pyrogram.types import ChatPrivileges
from pyrogram.errors import FloodWait, RPCError

# Configure logger
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("batch_restorer")

import config
from config import DB, API_ID, API_HASH, OWNER_ID, save_data_sync, deduplicate_channels_in_memory

async def run_batch_restore(backup_json_path: str, session_string: str = None, bot_token: str = None):
    """
    Restores missing batches from an old backup JSON into current DB/MongoDB.
    Rules:
    1. Preserves all batches that were already manually updated/added recently.
    2. Uses Userbot session to:
       - Access channels/groups
       - Add bot as a member
       - Promote bot to Administrator with full privileges
       - Retrieve/create invite links
       - Assign the original category from backup
    3. Saves everything to MongoDB Atlas and local disk.
    """
    if not os.path.exists(backup_json_path):
        return {
            "success": False,
            "error": f"Backup file not found at: {backup_json_path}"
        }

    # 1. Parse backup JSON file
    try:
        with open(backup_json_path, "r", encoding="utf-8") as f:
            raw_backup = json.load(f)
    except Exception as e:
        return {"success": False, "error": f"Failed to parse JSON file: {e}"}

    # Handle MongoDB export envelope
    if isinstance(raw_backup, list) and len(raw_backup) > 0 and "data" in raw_backup[0]:
        backup_data = raw_backup[0]["data"]
    elif isinstance(raw_backup, dict) and "data" in raw_backup:
        backup_data = raw_backup["data"]
    elif isinstance(raw_backup, dict):
        backup_data = raw_backup
    else:
        return {"success": False, "error": "Invalid JSON backup format."}

    # Extract old batch collections
    old_free = backup_data.get("FREE_CHANNELS", {})
    old_paid = backup_data.get("PAID_CHANNELS", {})
    old_special = backup_data.get("SPECIAL_CHANNELS", {})
    old_all_chats = backup_data.get("ALL_CHATS", {})
    old_categories = backup_data.get("BATCH_CATEGORIES", {})
    old_prices = backup_data.get("BATCH_PRICES", {})
    old_link_map = backup_data.get("LINK_MAP", {})

    # Ensure current DB is loaded
    config.load_data()
    deduplicate_channels_in_memory()

    current_free = set(DB.get("FREE_CHANNELS", {}).keys())
    current_paid = set(DB.get("PAID_CHANNELS", {}).keys())
    current_special = set(DB.get("SPECIAL_CHANNELS", {}).keys())
    current_all = current_free | current_paid | current_special

    logger.info(f"Current Batches in DB: {len(current_all)}")

    # Identify batches from backup
    all_old_batches = []
    for cid_raw, title in old_special.items():
        all_old_batches.append((cid_raw, title, "special"))
    for cid_raw, title in old_paid.items():
        all_old_batches.append((cid_raw, title, "paid"))
    for cid_raw, title in old_free.items():
        all_old_batches.append((cid_raw, title, "free"))

    # Also check if any chat in ALL_CHATS was not in specific dicts
    for cid_raw, title in old_all_chats.items():
        try:
            n_cid = int(str(cid_raw).strip())
        except Exception:
            continue
        if not any(int(str(x[0]).strip()) == n_cid for x in all_old_batches):
            # Default to free if unclassified
            all_old_batches.append((cid_raw, title, "free"))

    # Filter out batches that were ALREADY MANUALLY UPDATED
    missing_batches = []
    skipped_existing = []

    for cid_raw, title, b_type in all_old_batches:
        try:
            cid = int(str(cid_raw).strip())
        except Exception:
            continue

        if cid in current_all or str(cid) in current_all:
            skipped_existing.append((cid, title, "Already in DB / Manually Updated"))
            continue

        category = old_categories.get(str(cid)) or old_categories.get(cid) or "Other Batches"
        price = old_prices.get(str(cid)) or old_prices.get(cid)
        old_link = old_link_map.get(str(cid)) or old_link_map.get(cid)

        missing_batches.append({
            "cid": cid,
            "title": title,
            "type": b_type,
            "category": category,
            "price": price,
            "old_link": old_link
        })

    logger.info(f"Old Backup Total Batches: {len(all_old_batches)}")
    logger.info(f"Already Existing / Manually Updated (Skipped): {len(skipped_existing)}")
    logger.info(f"Missing Batches To Restore: {len(missing_batches)}")

    if not missing_batches:
        return {
            "success": True,
            "message": "Sabhi batches pehle se hi current database me exist karte hain! Koi missing batch nahi mila.",
            "skipped_count": len(skipped_existing),
            "restored_count": 0,
            "failed_count": 0,
            "details": skipped_existing
        }

    # 2. Setup Userbot Session
    active_session = session_string or DB.get("USERBOT_SESSION") or os.environ.get("SESSION_STRING")
    if not active_session:
        return {
            "success": False,
            "error": "No Userbot Session found! Pehle Userbot Login karein ya session string provide karein taaki bot ko channels me add/promote kiya ja sake.",
            "missing_count": len(missing_batches)
        }

    # 3. Setup Bot Client to get Bot's own identity
    bot_token = bot_token or config.TELEGRAM_BOT_TOKEN
    bot_client = Client("bot_temp_worker", api_id=API_ID, api_hash=API_HASH, bot_token=bot_token, in_memory=True)
    await bot_client.start()
    bot_me = await bot_client.get_me()
    bot_id = bot_me.id
    bot_username = bot_me.username or str(bot_id)
    logger.info(f"Bot Identity: @{bot_username} (ID: {bot_id})")

    # 4. Start Userbot Client
    userbot = Client("userbot_restore_worker", api_id=API_ID, api_hash=API_HASH, session_string=active_session, in_memory=True)
    await userbot.start()
    userbot_me = await userbot.get_me()
    logger.info(f"Userbot Logged in as: {userbot_me.first_name} (ID: {userbot_me.id})")

    # Privileges: Full Administrator permissions
    full_privileges = ChatPrivileges(
        can_manage_chat=True,
        can_post_messages=True,
        can_edit_messages=True,
        can_delete_messages=True,
        can_manage_video_chats=True,
        can_restrict_members=True,
        can_promote_members=True,
        can_change_info=True,
        can_invite_users=True,
        can_pin_messages=True,
        is_anonymous=False
    )

    restored_list = []
    failed_list = []

    # 5. Process each missing batch
    for item in missing_batches:
        cid = item["cid"]
        expected_title = item["title"]
        b_type = item["type"]
        category = item["category"]

        logger.info(f"Processing Batch [{b_type.upper()}]: {expected_title} ({cid})...")
        try:
            # 5a. Access chat via userbot
            chat = None
            try:
                chat = await userbot.get_chat(cid)
            except Exception as e:
                logger.warning(f"Userbot could not fetch chat {cid}: {e}")

            actual_title = chat.title if chat else expected_title

            # 5b. Add bot to chat
            try:
                await userbot.add_chat_members(chat_id=cid, user_ids=bot_id)
                logger.info(f"  Added bot to {actual_title}")
            except Exception as e:
                err_str = str(e)
                if "USER_ALREADY_PARTICIPANT" in err_str or "USER_IS_ALREADY_SET" in err_str:
                    logger.info(f"  Bot is already in {actual_title}")
                else:
                    logger.warning(f"  Could not add bot to {actual_title}: {e}")

            # 5c. Promote bot to Administrator with all permissions
            promoted = False
            try:
                await userbot.promote_chat_member(
                    chat_id=cid,
                    user_id=bot_id,
                    privileges=full_privileges
                )
                promoted = True
                logger.info(f"  Successfully promoted bot to ADMIN in {actual_title}")
            except Exception as e:
                logger.warning(f"  Promote failed in {actual_title} (Userbot may not have rights): {e}")

            # 5d. Retrieve or generate invite link
            invite_link = item.get("old_link")
            if not invite_link or not str(invite_link).startswith("http"):
                try:
                    exported = await userbot.create_chat_invite_link(chat_id=cid, creates_join_request=True)
                    invite_link = exported.invite_link
                    logger.info(f"  Generated new join-request invite link: {invite_link}")
                except Exception:
                    pass

            if invite_link:
                DB.setdefault("LINK_MAP", {})[str(cid)] = invite_link

            # 5e. Register into Database
            if b_type == "special":
                DB.setdefault("SPECIAL_CHANNELS", {})[cid] = actual_title
            elif b_type == "paid":
                DB.setdefault("PAID_CHANNELS", {})[cid] = actual_title
            else:
                DB.setdefault("FREE_CHANNELS", {})[cid] = actual_title

            DB.setdefault("ALL_CHATS", {})[cid] = actual_title
            DB.setdefault("BATCH_CATEGORIES", {})[str(cid)] = category

            if item.get("price") and b_type == "paid":
                DB.setdefault("BATCH_PRICES", {})[str(cid)] = item["price"]

            restored_list.append({
                "cid": cid,
                "title": actual_title,
                "type": b_type,
                "category": category,
                "promoted": promoted,
                "has_link": bool(invite_link)
            })

            # Small delay to avoid Telegram flood limits
            await asyncio.sleep(0.5)

        except FloodWait as fw:
            logger.warning(f"FloodWait encountered: sleeping for {fw.value}s")
            await asyncio.sleep(fw.value + 1)
        except Exception as err:
            logger.error(f"Failed to restore batch {cid}: {err}")
            failed_list.append({"cid": cid, "title": expected_title, "error": str(err)})

    # Stop temporary clients
    try:
        await userbot.stop()
    except Exception:
        pass
    try:
        await bot_client.stop()
    except Exception:
        pass

    # 6. Normalize and persist to MongoDB Atlas & Local JSON
    deduplicate_channels_in_memory()
    await asyncio.to_thread(save_data_sync)
    logger.info("Database successfully synchronized to MongoDB Atlas!")

    return {
        "success": True,
        "restored_count": len(restored_list),
        "skipped_count": len(skipped_existing),
        "failed_count": len(failed_list),
        "restored": restored_list,
        "skipped": skipped_existing,
        "failed": failed_list
    }

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Restore batches from old MongoDB JSON backup into current bot DB.")
    parser.add_argument("--backup", "-b", default="backup.json", help="Path to backup JSON file")
    parser.add_argument("--session", "-s", default=None, help="Userbot string session (optional, defaults to DB/env)")
    args = parser.parse_args()

    loop = asyncio.get_event_loop()
    res = loop.run_until_complete(run_batch_restore(args.backup, args.session))
    print("\n" + "="*50)
    print("BATCH RESTORATION SUMMARY REPORT")
    print("="*50)
    if res.get("success"):
        print(f"✅ Successfully Restored : {res.get('restored_count')}")
        print(f"⏩ Skipped (Preserved)   : {res.get('skipped_count')} (Already manually updated)")
        print(f"❌ Failed               : {res.get('failed_count')}")
        if res.get("restored"):
            print("\nRestored Batches:")
            for b in res["restored"]:
                status = "Promoted Admin" if b["promoted"] else "Added (Check Admin Rights)"
                print(f"  • [{b['type'].upper()}] {b['title']} -> {b['category']} ({status})")
    else:
        print(f"❌ Error: {res.get('error')}")

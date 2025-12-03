#!/usr/bin/env python3
"""
Terabox Downloader Bot (Pyrogram) — spooled (buffered) downloads

- Resolves Terabox links via configurable API
- Downloads into a SpooledTemporaryFile (keeps small files in memory)
- Uploads the file-like to dumb channel and copies to user (or uploads directly)
- Force-sub checks, admin commands, SQLite persistence
"""

import os
import re
import asyncio
import logging
import json
import sqlite3
import tempfile
import uuid
from typing import Optional, Any, Dict
from urllib.parse import quote_plus, urlparse
import aiohttp
from dotenv import load_dotenv
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
ADMIN_ID = os.getenv("ADMIN_ID", "")
API_TEMPLATE = os.getenv("API_TEMPLATE", "https://teradl.tiiny.io/?key=RushVx&link={link}")
TMP_SPOOL_LIMIT_MB = int(os.getenv("TMP_SPOOL_LIMIT_MB", "16"))  # keep up to 16 MB in memory by default
MAX_FILE_SIZE = int(os.getenv("MAX_FILE_SIZE", "0") or "0")  # 0 means no limit

if not BOT_TOKEN or not API_ID or not API_HASH:
    raise RuntimeError("BOT_TOKEN, API_ID and API_HASH must be set in environment")

# Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# DB
DB_FILE = "bot_data.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS stats (
            key TEXT PRIMARY KEY,
            value INTEGER
        )
        """
    )
    conn.commit()
    cur.close()
    conn.close()

def db_set(key: str, value: str):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("REPLACE INTO settings(key, value) VALUES(?, ?)", (key, value))
    conn.commit()
    cur.close()
    conn.close()

def db_get(key: str) -> Optional[str]:
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("SELECT value FROM settings WHERE key=?", (key,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row[0] if row else None

def db_delete(key: str):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("DELETE FROM settings WHERE key=?", (key,))
    conn.commit()
    cur.close()
    conn.close()

def stat_incr(key: str, inc: int = 1):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("SELECT value FROM stats WHERE key=?", (key,))
    row = cur.fetchone()
    if row:
        val = int(row[0]) + inc
        cur.execute("UPDATE stats SET value=? WHERE key=?", (val, key))
    else:
        val = inc
        cur.execute("INSERT INTO stats(key, value) VALUES(?, ?)", (key, val))
    conn.commit()
    cur.close()
    conn.close()

def stat_get(key: str) -> int:
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()
    cur.execute("SELECT value FROM stats WHERE key=?", (key,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return int(row[0]) if row else 0

init_db()
# load existing template if stored
if db_get("api_template") is None:
    db_set("api_template", API_TEMPLATE)

# Regex
TERABOX_RX = re.compile(r"https?://[^\s]*terabox[^\s]*", re.IGNORECASE)
GENERIC_URL_RX = re.compile(r"https?://[^\s]+")

def is_admin(user_id: int) -> bool:
    if not ADMIN_ID:
        return False
    admin_ids = [s.strip() for s in ADMIN_ID.split(",") if s.strip()]
    return str(user_id) in admin_ids

# Pyrogram client
app = Client(
    "terabox_bot",
    api_id=int(API_ID),
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
)

# Resolver API call
async def call_resolver_api(link: str, timeout: int = 120) -> Dict[str, Any]:
    api_template = db_get("api_template") or API_TEMPLATE
    encoded = quote_plus(link)
    url = api_template.format(link=encoded)
    logger.info("Calling resolver: %s", url)
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=timeout) as resp:
            text = await resp.text()
            try:
                data = json.loads(text)
                return {"ok": True, "data": data, "status_code": resp.status}
            except Exception:
                return {"ok": True, "data": text, "status_code": resp.status}

def extract_url(api_data: Any) -> Optional[str]:
    if isinstance(api_data, str):
        m = GENERIC_URL_RX.search(api_data)
        return m.group(0) if m else None
    if isinstance(api_data, dict):
        for key in ("download", "url", "link", "download_url"):
            val = api_data.get(key)
            if isinstance(val, str) and val.startswith("http"):
                return val
        # recursive find
        def find(obj):
            if isinstance(obj, str):
                if obj.startswith("http"):
                    return obj
                m = GENERIC_URL_RX.search(obj)
                if m:
                    return m.group(0)
            elif isinstance(obj, dict):
                for v in obj.values():
                    r = find(v)
                    if r:
                        return r
            elif isinstance(obj, list):
                for v in obj:
                    r = find(v)
                    if r:
                        return r
            return None
        return find(api_data)
    return None

async def ensure_joined(user_id: int) -> Optional[str]:
    fs = db_get("force_sub")
    if not fs:
        return None
    try:
        member = await app.get_chat_member(fs, user_id)
        if member.status in ("creator", "administrator", "member"):
            return None
        return "You must join the required channel to use this bot."
    except Exception as e:
        logger.warning("Force-sub check failed: %s", e)
        return "Cannot verify membership: make sure the bot is admin in the force-sub channel."

# Admin decorator
def admin_only(func):
    async def wrapper(client, message):
        uid = message.from_user.id if message.from_user else None
        if not uid or not is_admin(uid):
            await message.reply_text("You're not authorized to use this command.")
            return
        return await func(client, message)
    return wrapper

# Helpers
def filename_from_url(url: str) -> str:
    p = urlparse(url)
    name = os.path.basename(p.path)
    if not name:
        return f"file_{uuid.uuid4().hex}"
    return name

# Download into a SpooledTemporaryFile
async def download_into_spooled_file(url: str, spooled_limit_mb: int, status_edit_cb, max_size: int = 0, timeout: int = 0):
    """
    Download the URL into a SpooledTemporaryFile.
    - spooled_limit_mb: maximum memory (in MB) to keep before spooling to disk.
    - status_edit_cb(percent, downloaded, total) coroutine for progress updates.
    Returns (spooled_file, total_bytes, content_type, filename_hint)
    Caller must close the spooled_file when done.
    """
    spooled_limit = spooled_limit_mb * 1024 * 1024
    tmp = tempfile.SpooledTemporaryFile(max_size=spooled_limit)
    timeout = timeout or None
    max_size = max_size or 0
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=timeout) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Download failed, status: {resp.status}")
            total = resp.content_length or 0
            if max_size and total and total > max_size:
                raise RuntimeError(f"File too large: {total} bytes (limit {max_size})")
            cd = resp.headers.get("content-disposition", "")
            ctype = resp.headers.get("content-type", "")
            filename_hint = None
            # try filename from content-disposition
            if cd:
                import re
                m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', cd, flags=re.IGNORECASE)
                if m:
                    filename_hint = m.group(1).strip()
            if not filename_hint:
                filename_hint = filename_from_url(str(resp.url))
            downloaded = 0
            chunk_size = 1024 * 64
            last_percent = -1
            async for chunk in resp.content.iter_chunked(chunk_size):
                if not chunk:
                    break
                tmp.write(chunk)
                downloaded += len(chunk)
                if max_size and not total and downloaded > max_size:
                    raise RuntimeError(f"File too large (downloaded {downloaded} > limit {max_size})")
                if total:
                    percent = int(downloaded * 100 / total)
                else:
                    percent = min(99, int(downloaded / (1024*1024)))
                if percent != last_percent and (percent % 2 == 0 or percent in (0,100)):
                    try:
                        await status_edit_cb(percent, downloaded, total)
                    except Exception:
                        pass
                    last_percent = percent
            # final
            try:
                await status_edit_cb(100, downloaded, total)
            except Exception:
                pass
            tmp.seek(0)
            return tmp, downloaded, ctype or None, filename_hint

# Progress callbacks
def make_progress_cb(status_message, prefix: str = "Uploading"):
    loop = asyncio.get_event_loop()
    async def edit_status(percent, current, total):
        try:
            if total and total > 0:
                await status_message.edit_text(f"{prefix}: {percent}% ({current}/{total} bytes)")
            else:
                await status_message.edit_text(f"{prefix}: {percent}% ({current} bytes)")
        except Exception:
            pass

    def progress(current, total, *args):
        try:
            percent = (current * 100 / total) if total else 0
            loop.create_task(edit_status(percent, current, total))
        except Exception:
            pass

    return progress

# Commands
@app.on_message(filters.command("start") & ~filters.edited)
async def start_handler(client, message):
    txt = (
        "Hi! Send me a Terabox share link and I'll resolve, download and send the file. "
        "Files will also be stored to your dumb channel if configured.\n\n"
        "Use /help to see commands."
    )
    await message.reply_text(txt)

@app.on_message(filters.command("help") & ~filters.edited)
async def help_handler(client, message):
    txt = "Send a terabox link. Admin commands: /set_force_sub, /remove_force_sub, /set_dumb_channel, /remove_dumb_channel, /set_api_template, /stats"
    await message.reply_text(txt)

@app.on_message(filters.command("set_dumb_channel") & ~filters.edited)
@admin_only
async def set_dumb_channel(client, message):
    args = message.text.strip().split(maxsplit=1)
    if len(args) < 2:
        await message.reply_text("Usage: /set_dumb_channel <channel_username_or_id>")
        return
    ch = args[1].strip()
    try:
        await client.get_chat(ch)
        db_set("dumb_channel", ch)
        await message.reply_text(f"Dumb channel set to {ch}")
    except Exception as e:
        await message.reply_text(f"Failed to set dumb channel: {e}")

@app.on_message(filters.command("remove_dumb_channel") & ~filters.edited)
@admin_only
async def remove_dumb_channel(client, message):
    db_delete("dumb_channel")
    await message.reply_text("Dumb channel removed.")

@app.on_message(filters.command("set_force_sub") & ~filters.edited)
@admin_only
async def set_force_sub(client, message):
    args = message.text.strip().split(maxsplit=1)
    if len(args) < 2:
        await message.reply_text("Usage: /set_force_sub <channel_username_or_id>")
        return
    ch = args[1].strip()
    try:
        await client.get_chat(ch)
        db_set("force_sub", ch)
        await message.reply_text(f"Force-sub set to {ch}")
    except Exception as e:
        await message.reply_text(f"Failed to set force-sub: {e}")

@app.on_message(filters.command("remove_force_sub") & ~filters.edited)
@admin_only
async def remove_force_sub(client, message):
    db_delete("force_sub")
    await message.reply_text("Force-sub removed.")

@app.on_message(filters.command("set_api_template") & ~filters.edited)
@admin_only
async def set_api_template_cmd(client, message):
    args = message.text.strip().split(maxsplit=1)
    if len(args) < 2:
        await message.reply_text("Usage: /set_api_template <api_url_template>")
        return
    tpl = args[1].strip()
    db_set("api_template", tpl)
    await message.reply_text("API template updated.")

@app.on_message(filters.command("stats") & ~filters.edited)
@admin_only
async def stats_cmd(client, message):
    total = stat_get("resolved_links")
    uploaded = stat_get("uploaded_files")
    await message.reply_text(f"Resolved links: {total}\nUploaded files: {uploaded}")

# Main handler
@app.on_message(filters.text & ~filters.edited)
async def text_handler(client, message):
    txt = message.text or ""
    found = TERABOX_RX.search(txt)
    if not found:
        return

    # Force-sub
    fs_reason = await ensure_joined(message.from_user.id)
    if fs_reason:
        await message.reply_text(f"Access denied: {fs_reason}")
        return

    m = GENERIC_URL_RX.search(txt)
    link = m.group(0) if m else txt.strip()
    status_msg = await message.reply_text("Resolving your Terabox link...")

    # Resolve
    try:
        result = await call_resolver_api(link)
    except Exception as e:
        logger.exception("Resolver API call error")
        await status_msg.edit_text(f"Error contacting resolver API: {e}")
        return

    if not result.get("ok"):
        await status_msg.edit_text("Resolver API returned an error.")
        return

    api_data = result.get("data")
    direct = extract_url(api_data)
    if not direct:
        if isinstance(api_data, str):
            snippet = api_data[:1000]
            await status_msg.edit_text(f"Could not parse direct URL. Raw response:\n\n{snippet}")
        else:
            await status_msg.edit_text("Could not parse direct URL from API response.")
        return

    stat_incr("resolved_links", 1)

    # Download into spooled temp file
    async def download_status_cb(percent, downloaded, total):
        try:
            if total and total > 0:
                await status_msg.edit_text(f"Downloading: {percent}% ({downloaded}/{total} bytes)")
            else:
                await status_msg.edit_text(f"Downloading: {downloaded} bytes")
        except Exception:
            pass

    try:
        spooled_file, downloaded_bytes, content_type, filename_hint = await download_into_spooled_file(
            direct,
            spooled_limit_mb=TMP_SPOOL_LIMIT_MB,
            status_edit_cb=download_status_cb,
            max_size=MAX_FILE_SIZE or 0,
            timeout=0
        )
    except Exception as e:
        logger.exception("Download into spooled file failed")
        await status_msg.edit_text(f"Download failed: {e}")
        return

    # Decide type
    is_video = False
    if content_type and content_type.startswith("video"):
        is_video = True
    else:
        ext = os.path.splitext(filename_hint)[1].lower()
        if ext in (".mp4", ".mkv", ".mov", ".webm", ".ts"):
            is_video = True

    # Upload
    dc = db_get("dumb_channel")
    try:
        # Pyrogram accepts file-like objects for send_document/send_video.
        # Ensure file pointer is at start.
        spooled_file.seek(0)
        if dc:
            await status_msg.edit_text("Uploading to dumb channel...")
            progress_cb = make_progress_cb(status_msg, prefix="Uploading to dumb channel")
            if is_video:
                posted = await client.send_video(
                    chat_id=dc,
                    video=spooled_file,
                    caption=f"Saved for user @{message.from_user.username or message.from_user.id}",
                    file_name=filename_hint,
                    progress=progress_cb,
                    progress_args=()
                )
            else:
                posted = await client.send_document(
                    chat_id=dc,
                    document=spooled_file,
                    caption=f"Saved for user @{message.from_user.username or message.from_user.id}",
                    file_name=filename_hint,
                    progress=progress_cb,
                    progress_args=()
                )
            # Copy to user
            await status_msg.edit_text("Copying file to you...")
            try:
                await client.copy_message(chat_id=message.chat.id, from_chat_id=dc, message_id=posted.message_id)
            except Exception as e:
                logger.warning("copy_message failed, fallback to direct send: %s", e)
                spooled_file.seek(0)
                progress_cb2 = make_progress_cb(status_msg, prefix="Uploading to you")
                if is_video:
                    await client.send_video(chat_id=message.chat.id, video=spooled_file, caption="Here is your file", file_name=filename_hint, progress=progress_cb2, progress_args=())
                else:
                    await client.send_document(chat_id=message.chat.id, document=spooled_file, caption="Here is your file", file_name=filename_hint, progress=progress_cb2, progress_args=())
            stat_incr("uploaded_files", 1)
            await status_msg.edit_text("Done! File stored in dumb channel and delivered to you.")
        else:
            await status_msg.edit_text("Uploading file to you...")
            progress_cb = make_progress_cb(status_msg, prefix="Uploading to you")
            spooled_file.seek(0)
            if is_video:
                await client.send_video(chat_id=message.chat.id, video=spooled_file, caption="Here is your file", file_name=filename_hint, progress=progress_cb, progress_args=())
            else:
                await client.send_document(chat_id=message.chat.id, document=spooled_file, caption="Here is your file", file_name=filename_hint, progress=progress_cb, progress_args=())
            stat_incr("uploaded_files", 1)
            await status_msg.edit_text("Done! Uploaded to you.")
    except Exception as e:
        logger.exception("Upload failed")
        await status_msg.edit_text(f"Upload failed: {e}")
    finally:
        try:
            spooled_file.close()
        except Exception:
            pass

if __name__ == "__main__":
    logger.info("Starting bot (spooled file flow)...")
    app.run()
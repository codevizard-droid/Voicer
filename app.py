import os
import uuid
import asyncio
import subprocess
import threading
import requests
import html
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
client = None  # Глобальна змінна

# === ДОПОМІЖНІ ФУНКЦІЇ ===
def convert_to_wav(input_path, wav_path):
    cmd = ['ffmpeg', '-i', input_path, '-ar', '16000', '-ac', '1', '-y', wav_path]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        return True
    except:
        return False

def split_audio(wav_path, chunk_minutes=5):
    chunks = []
    base = wav_path.rsplit('.', 1)[0]
    chunk_seconds = chunk_minutes * 60
    i = 0
    start = 0
    while True:
        chunk_path = f"{base}_part{i}.wav"
        cmd = ['ffmpeg', '-i', wav_path, '-ss', str(start), '-t', str(chunk_seconds),
               '-ar', '16000', '-ac', '1', '-y', chunk_path]
        try:
            subprocess.run(cmd, check=True, capture_output=True)
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
    try:
        with open(audio_path, 'rb') as f:
            response = requests.post(
                'https://api.groq.com/openai/v1/audio/transcriptions',
                headers={'Authorization': f'Bearer {GROQ_API_KEY}'},
                files={'file': ('audio.wav', f, 'audio/wav')},
                data={'model': 'whisper-large-v3', 'language': 'uk',
                      'response_format': 'text', 'temperature': '0'},
                timeout=120
            )
        if response.status_code == 200:
            text = response.text.strip()
            return {"text": text, "success": bool(text)}
        return {"text": f"Помилка {response.status_code}", "success": False}
    except Exception as e:
        return {"text": f"❌ {str(e)[:100]}", "success": False}

# === ВЕБ-СЕРВЕР ===
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()
    def log_message(self, *a): pass

# === ОБРОБНИКИ ===
async def handle_message(event):
    message = event.message
    user_id = message.sender_id
    user_settings = get_user_settings(user_id)
    
    lang = user_settings.get('language', DEFAULT_SETTINGS['language'])
    auto_translate = user_settings.get('auto_translate', DEFAULT_SETTINGS['auto_translate'])
    translate_to = user_settings.get('translate_to', DEFAULT_SETTINGS['translate_to'])
    
    if not message.is_reply:
        await message.reply("❌ Використовуйте як відповідь на голосове повідомлення.")
        return
    
    replied = await message.get_reply_message()
    if not (replied.voice or replied.video_note or replied.audio):
        await message.reply("❌ Це не голосове повідомлення.")
        return
    
    ext = ".ogg" if replied.voice else (".mp4" if replied.video_note else ".mp3")
    
    try:
        await message.delete()
    except:
        pass
    
    status_msg = await replied.reply("⏳ Завантаження...")
    tid = str(uuid.uuid4())
    inp = f"/tmp/{tid}{ext}"
    wav = f"/tmp/{tid}.wav"
    
    try:
        async def progress_callback(current, total):
            percent = (current / total) * 100
            try:
                await status_msg.edit(f"⏳ Завантаження... {percent:.0f}%")
            except:
                pass
        
        await client.download_media(replied, inp, progress_callback=progress_callback)
        await status_msg.edit("🔄 Конвертація...")
        
        if not convert_to_wav(inp, wav):
            await status_msg.edit("❌ Помилка конвертації")
            return
        
        wav_size_mb = os.path.getsize(wav) / (1024 * 1024)
        all_text = []
        
        if wav_size_mb > 20:
            await status_msg.edit("✂️ Розбиваю...")
            chunks = split_audio(wav, 5)
            for i, chunk in enumerate(chunks):
                await status_msg.edit(f"🎙 {i+1}/{len(chunks)}")
                r = transcribe_audio(chunk)
                if r.get("success"):
                    all_text.append(r["text"])
                os.remove(chunk)
        else:
            await status_msg.edit("🎙 Розшифровка...")
            r = transcribe_audio(wav)
            if not r.get("success"):
                await status_msg.edit(f"❌ {r.get('text')}")
                return
            all_text.append(r["text"])
        
        full_text = " ".join(all_text).strip()
        if not full_text:
            await status_msg.edit("❌ Порожній результат")
            return
        
        # Переклад
        translated_text = None
        if auto_translate:
            await status_msg.edit(f"🌍 Переклад...")
            try:
                from deep_translator import GoogleTranslator
                translated_text = GoogleTranslator(source='auto', target=translate_to).translate(full_text)
            except Exception as e:
                print(f"❌ Переклад: {e}")
        
        # Формування
        safe_original = html.escape(full_text)
        lang_names = {"uk": "🇺🇦 Українська", "en": "🇬🇧 English", "ru": "🇷🇺 Русский"}
        target_names = {"uk": "🇺🇦", "en": "🇬🇧", "ru": "🇷🇺", "pl": "🇵🇱"}
        
        if translated_text:
            safe_translation = html.escape(translated_text)
            final_text = (
                f"📝 <b>Розшифровка ({lang_names.get(lang, lang)}):</b>\n"
                f"<blockquote expandable>{safe_original}</blockquote>\n\n"
                f"🌍 <b>Переклад ({target_names.get(translate_to, translate_to)}):</b>\n"
                f"<blockquote expandable>{safe_translation}</blockquote>"
            )
        else:
            final_text = (
                f"📝 <b>Розшифровка ({lang_names.get(lang, lang)}):</b>\n"
                f"<blockquote expandable>{safe_original}</blockquote>"
            )
        
        await status_msg.delete()
        
        if len(final_text) > 4000:
            for i in range(0, len(final_text), 4000):
                await replied.reply(final_text[i:i+4000], parse_mode='html')
        else:
            await replied.reply(final_text, parse_mode='html')
        
    except Exception as e:
        print(f"❌ {e}")
        try:
            await status_msg.edit(f"❌ Помилка")
        except:
            pass
    finally:
        for p in [inp, wav]:
            if os.path.exists(p):
                try: os.remove(p)
                except: pass

async def show_settings_menu(event):
    """Показує меню налаштувань"""
    user_id = event.sender_id
    user_settings = get_user_settings(user_id)
    
    lang = user_settings.get('language', DEFAULT_SETTINGS['language'])
    auto_translate = user_settings.get('auto_translate', DEFAULT_SETTINGS['auto_translate'])
    translate_to = user_settings.get('translate_to', DEFAULT_SETTINGS['translate_to'])
    
    lang_names = {"uk": "Українська 🇺🇦", "en": "English 🇬🇧", "ru": "Русский 🇷🇺"}
    target_names = {"en": "English 🇬🇧", "uk": "Українська 🇺🇦", "pl": "Polski 🇵🇱", "ru": "Русский 🇷🇺"}
    
    text = (
        "⚙️ <b>Налаштування бота</b>\n\n"
        f"🌐 <b>Мова розшифровки:</b> {lang_names.get(lang, lang)}\n"
        f"🔄 <b>Автопереклад:</b> {'✅ Увімкнено' if auto_translate else '❌ Вимкнено'}\n"
        f"🎯 <b>Мова перекладу:</b> {target_names.get(translate_to, translate_to)}\n"
    )
    
    buttons = [
        [Button.inline("🌐 Мова розшифровки", b"menu_lang")],
        [Button.inline(f"🔄 Автопереклад: {'Вимкнути' if auto_translate else 'Увімкнути'}", b"toggle_translate")],
        [Button.inline("🎯 Мова перекладу", b"menu_target_lang")],
        [Button.inline("❌ Закрити", b"close_menu")]
    ]
    
    if hasattr(event, 'edit'):
        try:
            await event.edit(text, buttons=buttons, parse_mode='html')
            return
        except:
            pass
    await event.respond(text, buttons=buttons, parse_mode='html')

async def handle_callback(event):
    """Обробка натискань кнопок"""
    data = event.data.decode('utf-8')
    user_id = event.sender_id
    
    if data == "menu_lang":
        buttons = [
            [Button.inline("🇺🇦 Українська", b"set_lang_uk")],
            [Button.inline("🇬🇧 English", b"set_lang_en")],
            [Button.inline("🇷🇺 Русский", b"set_lang_ru")],
            [Button.inline("⬅️ Назад", b"back_to_menu")]
        ]
        await event.edit("Оберіть мову розшифровки:", buttons=buttons)
    
    elif data.startswith("set_lang_"):
        lang = data.replace("set_lang_", "")
        update_user_settings(user_id, {"language": lang})
        await event.answer("✅ Збережено")
        await show_settings_menu(event)
    
    elif data == "toggle_translate":
        s = get_user_settings(user_id)
        current = s.get('auto_translate', False)
        update_user_settings(user_id, {"auto_translate": not current})
        await event.answer("✅ Збережено")
        await show_settings_menu(event)
    
    elif data == "menu_target_lang":
        buttons = [
            [Button.inline("🇬🇧 English", b"set_target_en")],
            [Button.inline("🇺🇦 Українська", b"set_target_uk")],
            [Button.inline("🇵🇱 Polski", b"set_target_pl")],
            [Button.inline("🇷🇺 Русский", b"set_target_ru")],
            [Button.inline("⬅️ Назад", b"back_to_menu")]
        ]
        await event.edit("Оберіть мову перекладу:", buttons=buttons)
    
    elif data.startswith("set_target_"):
        lang = data.replace("set_target_", "")
        update_user_settings(user_id, {"translate_to": lang})
        await event.answer("✅ Збережено")
        await show_settings_menu(event)
    
    elif data == "back_to_menu":
        await show_settings_menu(event)
    
    elif data == "close_menu":
        await event.delete()
    
    else:
        await event.answer()

# === ЗАПУСК БОТА ===
async def run_bot():
    global client, BOT_STATUS, BOT_NAME
    
    BOT_STATUS = "запуск..."
    
    # СТВОРЮЄМО КЛІЄНТ ПЕРЕД РЕЄСТРАЦІЄЮ ОБРОБНИКІВ
    client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
    
    # ТЕПЕР РЕЄСТРУЄМО ОБРОБНИКИ
    @client.on(events.NewMessage(pattern=r'^\.(t|р|s|transcribe|розшифруй|short)$'))
    async def handler_msg(e):
        await handle_message(e)
    
    @client.on(events.NewMessage(pattern=r'^/settings$'))
    async def handler_settings(e):
        await show_settings_menu(e)
    
    @client.on(events.NewMessage(pattern=r'^/start$'))
    async def handler_start(e):
        await e.respond(
            "🎙 <b>Voice Transcriber Bot</b>\n\n"
            "📋 <b>Як користуватись:</b>\n"
            "• Відповідайте <code>.t</code> на голосове — отримаєте текст\n"
            "• <code>/settings</code> — налаштування\n\n"
            "⚙️ Підтримка: автопереклад, вибір мови, згортання тексту",
            parse_mode='html'
        )
    
    @client.on(events.CallbackQuery())
    async def handler_callback(e):
        await handle_callback(e)
    
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

# === MAIN ===
if __name__ == "__main__":
    threading.Thread(target=start_bot, daemon=True).start()
    srv = HTTPServer(('0.0.0.0', PORT), Handler)
    print(f"🌐 Порт {PORT}")
    srv.serve_forever()

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
    user_id = message.sender_id
    user_settings = get_user_settings(user_id)
    
    # Отримуємо налаштування користувача
    lang = user_settings.get('language', DEFAULT_SETTINGS['language'])
    auto_translate = user_settings.get('auto_translate', DEFAULT_SETTINGS['auto_translate'])
    translate_to = user_settings.get('translate_to', DEFAULT_SETTINGS['translate_to'])
    
    # Перевірка: чи це відповідь на повідомлення
    if not message.is_reply:
        await message.reply("❌ Будь ласка, використовуйте цю команду як відповідь на голосове повідомлення.")
        return
    
    replied = await message.get_reply_message()
    
    # Перевірка: чи це голосове/відео/аудіо
    if not (replied.voice or replied.video_note or replied.audio):
        await message.reply("❌ Повідомлення, на яке ви відповіли, не є голосовим.")
        return
    
    # Визначаємо розширення
    ext = ".ogg" if replied.voice else (".mp4" if replied.video_note else ".mp3")
    
    # Видаляємо команду .t
    try:
        await message.delete()
    except:
        pass
    
    # Відправляємо статус як відповідь на голосове
    status_msg = await replied.reply("⏳ Завантаження аудіо...")
    
    # Тимчасові файли
    tid = str(uuid.uuid4())
    inp = f"/tmp/{tid}{ext}"
    wav = f"/tmp/{tid}.wav"
    
    try:
        # === ЕТАП 1: ЗАВАНТАЖЕННЯ З ПРОГРЕСОМ ===
        async def progress_callback(current, total):
            percent = (current / total) * 100
            try:
                await status_msg.edit(f"⏳ Завантаження... {percent:.0f}%")
            except:
                pass
        
        await client.download_media(replied, inp, progress_callback=progress_callback)
        print("📥 Завантажено")
        
        # === ЕТАП 2: КОНВЕРТАЦІЯ ===
        await status_msg.edit("🔄 Конвертація...")
        if not convert_to_wav(inp, wav):
            await status_msg.edit("❌ Помилка конвертації аудіо")
            return
        print("🔄 Конвертовано")
        
        # === ЕТАП 3: РОЗШИФРОВКА ===
        wav_size_mb = os.path.getsize(wav) / (1024 * 1024)
        all_text = []
        
        if wav_size_mb > 20:
            # Розбиваємо на частини
            await status_msg.edit("✂️ Розбиваю на частини...")
            chunks = split_audio(wav, chunk_minutes=5)
            total_chunks = len(chunks)
            print(f"📦 Частин: {total_chunks}")
            
            for i, chunk in enumerate(chunks):
                await status_msg.edit(f"🎙 Розшифровка... {i+1}/{total_chunks}")
                r = transcribe_audio(chunk)
                if r.get("success"):
                    all_text.append(r["text"])
                os.remove(chunk)
        else:
            await status_msg.edit("🎙 Розшифровка...")
            r = transcribe_audio(wav)
            if not r.get("success"):
                await status_msg.edit(f"❌ {r.get('text', 'Не вдалося')}")
                return
            all_text.append(r["text"])
        
        full_text = " ".join(all_text).strip()
        
        if not full_text:
            await status_msg.edit("❌ Порожній результат")
            return
        
        print(f"✅ Розшифровано: {full_text[:80]}...")
        
        # === ЕТАП 4: ПЕРЕКЛАД (якщо увімкнено) ===
        translated_text = None
        if auto_translate:
            await status_msg.edit(f"🌍 Перекладаю на {translate_to}...")
            try:
                from googletrans import Translator
                translator = Translator()
                translation = translator.translate(full_text, dest=translate_to)
                translated_text = translation.text
                print(f"✅ Перекладено: {translated_text[:80]}...")
            except Exception as e:
                print(f"❌ Помилка перекладу: {e}")
                translated_text = None
        
        # === ЕТАП 5: ФОРМУВАННЯ РЕЗУЛЬТАТУ ===
        # Екрануємо HTML-символи
        safe_original = html.escape(full_text)
        
        # Назви мов
        lang_names = {"uk": "🇺🇦 Українська", "en": "🇬🇧 English", "ru": "🇷🇺 Русский"}
        target_names = {"uk": "🇺🇦 Українська", "en": "🇬🇧 English", "ru": "🇷🇺 Русский", "pl": "🇵🇱 Polski"}
        
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
        
        # === ЕТАП 6: ВІДПРАВКА ЯК ВІДПОВІДЬ НА ГОЛОСОВЕ ===
        await status_msg.delete()
        
        if len(final_text) > 4000:
            # Розбиваємо на частини
            parts = [final_text[i:i+4000] for i in range(0, len(final_text), 4000)]
            for part in parts:
                await replied.reply(part, parse_mode='html')
        else:
            await replied.reply(final_text, parse_mode='html')
        
        print("📤 Відправлено")
        
    except Exception as e:
        print(f"❌ Помилка: {e}")
        try:
            await status_msg.edit(f"❌ Помилка: {str(e)[:100]}")
        except:
            pass
    finally:
        # Очищення тимчасових файлів
        for p in [inp, wav]:
            if os.path.exists(p):
                try:
                    os.remove(p)
                except:
                    pass

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

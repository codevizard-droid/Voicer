import os
import uuid
import asyncio
import subprocess
import threading
import requests
import html
from http.server import HTTPServer, BaseHTTPRequestHandler
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from settings import (
    get_user_settings,
    update_user_settings,
    DEFAULT_SETTINGS,
    add_auto_chat,
    remove_auto_chat,
    is_auto_chat
)

# ============================================
# НАЛАШТУВАННЯ
# ============================================
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
SESSION_STRING = os.getenv("SESSION_STRING", "")
PORT = int(os.getenv("PORT", "7860"))
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

BOT_STATUS = "starting..."
BOT_NAME = ""
client = None

# ============================================
# ДОПОМІЖНІ ФУНКЦІЇ
# ============================================
def convert_to_wav(input_path, wav_path):
    """Конвертація аудіо у WAV 16kHz mono"""
    cmd = ['ffmpeg', '-i', input_path, '-ar', '16000', '-ac', '1', '-y', wav_path]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        return True
    except Exception as e:
        print(f"❌ FFmpeg: {e}")
        return False


def split_audio(wav_path, chunk_minutes=5):
    """Розбиває WAV на частини"""
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


def transcribe_audio(audio_path, language=None):
    """Розшифровка через Groq API з авто-визначенням мови.
    language=None — автоматичне визначення.
    language='uk' — примусово українська.
    """
    try:
        with open(audio_path, 'rb') as f:
            data = {
                'model': 'whisper-large-v3',
                'response_format': 'verbose_json',
                'temperature': '0'
            }
            if language:
                data['language'] = language
            
            response = requests.post(
                'https://api.groq.com/openai/v1/audio/transcriptions',
                headers={'Authorization': f'Bearer {GROQ_API_KEY}'},
                files={'file': ('audio.wav', f, 'audio/wav')},
                data=data,
                timeout=120
            )
        
        if response.status_code == 200:
            result = response.json()
            text = result.get('text', '').strip()
            detected = result.get('language', language or 'uk')
            return {
                "text": text,
                "language": detected,
                "success": bool(text)
            }
        return {"text": f"Помилка {response.status_code}", "success": False}
    except Exception as e:
        return {"text": f"❌ {str(e)[:100]}", "success": False}


# ============================================
# ВЕБ-СЕРВЕР для UptimeRobot
# ============================================
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()
    def log_message(self, *a):
        pass

def translate_text(text, target_lang):
    """Переклад через Groq LLM"""
    lang_names = {
        "en": "English",
        "uk": "Ukrainian",
        "ru": "Russian",
        "pl": "Polish"
    }
    target_name = lang_names.get(target_lang, target_lang)
    
    try:
        response = requests.post(
            'https://api.groq.com/openai/v1/chat/completions',
            headers={
                'Authorization': f'Bearer {GROQ_API_KEY}',
                'Content-Type': 'application/json'
            },
            json={
                'model': 'llama-3.3-70b-versatile',
                'messages': [
                    {
                        'role': 'system',
                        'content': f'Translate to {target_name}. Return ONLY the translation.'
                    },
                    {
                        'role': 'user',
                        'content': text
                    }
                ],
                'temperature': 0.3,
                'max_tokens': 4000
            },
            timeout=60
        )
        
        if response.status_code == 200:
            result = response.json()
            return result['choices'][0]['message']['content'].strip()
        else:
            # Повертаємо помилку як текст, щоб побачити її в чаті
            return f"[ERROR {response.status_code}]: {response.text[:200]}"
    except Exception as e:
        return f"[EXCEPTION]: {str(e)[:200]}"

# ============================================
# ОБРОБНИК ТРАНСКРИПЦІЇ (відповідь .t)
# ============================================
async def handle_message(event):
    message = event.message
    user_id = message.sender_id
    user_settings = get_user_settings(user_id)
    
    mode = user_settings.get('language_mode', 'auto')
    lang_param = None if mode == 'auto' else mode
    auto_translate = user_settings.get('auto_translate', False)
    translate_to = user_settings.get('translate_to', 'en')
    
    if not message.is_reply:
        await message.reply("❌ Використовуйте команду як відповідь на голосове повідомлення.")
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
        print(f"📥 Завантажено: {inp}")
        
        await status_msg.edit("🔄 Конвертація...")
        if not convert_to_wav(inp, wav):
            await status_msg.edit("❌ Помилка конвертації")
            return
        print("🔄 Конвертовано")
        
        wav_size_mb = os.path.getsize(wav) / (1024 * 1024)
        all_text = []
        detected_lang = None
        
        if wav_size_mb > 20:
            await status_msg.edit("✂️ Розбиваю на частини...")
            chunks = split_audio(wav, 5)
            total = len(chunks)
            print(f"📦 Частин: {total}")
            
            for i, chunk in enumerate(chunks):
                await status_msg.edit(f"🎙 Розшифровка... {i+1}/{total}")
                r = transcribe_audio(chunk, language=lang_param)
                if r.get("success"):
                    all_text.append(r["text"])
                    if not detected_lang:
                        detected_lang = r.get("language")
                try:
                    os.remove(chunk)
                except:
                    pass
        else:
            await status_msg.edit("🎙 Розшифровка...")
            r = transcribe_audio(wav, language=lang_param)
            if not r.get("success"):
                await status_msg.edit(f"❌ {r.get('text', 'Не вдалося')}")
                return
            all_text.append(r["text"])
            detected_lang = r.get("language")
        
        full_text = " ".join(all_text).strip()
        if not full_text:
            await status_msg.edit("❌ Порожній результат")
            return
        
        print(f"✅ Розшифровано: {full_text[:80]}...")
        
        # === ПЕРЕКЛАД ===
        translated_text = None
        if auto_translate:
            await status_msg.edit(f"🌍 Перекладаю на {translate_to}...")
            translated_text = translate_text(full_text, translate_to)
            if translated_text:
                print(f"✅ Перекладено: {translated_text[:80]}...")
        
        # === ФОРМУВАННЯ ===
        def escape_md(text):
            return text.replace("\\", "\\\\").replace("*", "\\*").replace("_", "\\_").replace("`", "\\`").replace("[", "\\[").replace("]", "\\]")
        
        safe_original = escape_md(full_text)
        
        lang_display = {
            "uk": "🇺🇦 Ukrainian",
            "en": "🇬🇧 English",
            "ru": "Russian",
            "pl": "🇵🇱 Polish",
            "de": "🇩🇪 German",
            "fr": "🇫🇷 French",
            "es": "🇪🇸 Spanish",
            "it": "🇮🇹 Italian",
        }
        
        display_lang = detected_lang or mode
        lang_label = lang_display.get(display_lang, display_lang.upper() if display_lang else "?")
        
        if translated_text:
            safe_translation = escape_md(translated_text)
            target_label = lang_display.get(translate_to, translate_to.upper())
            final_text = (
                f"📝 **Розшифровка ({lang_label}):**\n"
                f"**> {safe_original}\n\n"
                f"🌍 **Переклад ({target_label}):**\n"
                f"**> {safe_translation}\n"
            )
        else:
            final_text = (
                f"📝 **Розшифровка ({lang_label}):**\n"
                f"**> {safe_original}\n"
            )
        
        await status_msg.delete()
        
        if len(final_text) > 4000:
            parts = [final_text[i:i+4000] for i in range(0, len(final_text), 4000)]
            for part in parts:
                await replied.reply(part, parse_mode='markdown')
        else:
            await replied.reply(final_text, parse_mode='markdown')
        
        print("📤 Відправлено")
        
    except Exception as e:
        print(f"❌ Помилка: {e}")
        try:
            await status_msg.edit(f"❌ Помилка: {str(e)[:100]}")
        except:
            pass
    finally:
        for p in [inp, wav]:
            if os.path.exists(p):
                try:
                    os.remove(p)
                except:
                    pass


# ============================================
# МЕНЮ НАЛАШТУВАНЬ
# ============================================
async def show_settings_menu(event):
    user_id = event.sender_id
    user_settings = get_user_settings(user_id)
    
    mode = user_settings.get('language_mode', 'auto')
    auto_translate = user_settings.get('auto_translate', False)
    translate_to = user_settings.get('translate_to', 'en')
    
    mode_names = {
        "auto": "🎯 Auto-detect",
        "uk": "🇺🇦 Ukrainian",
        "en": "🇬🇧 English",
        "ru": "Russian"
    }
    target_names = {
        "en": "🇬🇧 English",
        "uk": "🇺🇦 Ukrainian",
        "ru": "Russian"
    }
    
    text = (
        "⚙️ <b>Settings</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "🎙 <b>TRANSCRIPTION MODE</b>\n"
        f"   • {mode_names.get(mode, mode)}\n\n"
        "🌍 <b>TRANSLATION</b>\n"
        f"   • {'✅ Enabled' if auto_translate else '❌ Disabled'}\n"
        f"   • Target: {target_names.get(translate_to, translate_to)}\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "🎙 <b>Transcription mode:</b>\n"
        "<code>/auto</code> — auto-detect\n"
        "<code>/lang_en</code> — 🇬🇧 English\n"
        "<code>/lang_uk</code> — 🇺🇦 Ukrainian\n"
        "<code>/lang_ru</code> — Russian\n\n"
        "🌍 <b>Translation:</b>\n"
        "<code>/tr_on</code> / <code>/tr_off</code>\n"
        "<code>/to_en</code> — 🇬🇧 English\n"
        "<code>/to_uk</code> — 🇺🇦 Ukrainian\n"
        "<code>/to_ru</code> — Russian\n\n"
        "🤖 <b>Auto-transcription:</b>\n"
        "<code>+chat</code> — add chat\n"
        "<code>-chat</code> — remove chat\n"
        "<code>/chats</code> — list"
    )
    
    try:
        await event.message.delete()
    except:
        pass
    
    await event.respond(text, parse_mode='html')


async def handle_settings_command(event):
    user_id = event.sender_id
    text = event.message.text.strip().lower()
    
    # === TRANSCRIPTION MODE ===
    if text == '/auto':
        update_user_settings(user_id, {"language_mode": "auto"})
        await event.respond("✅ Mode: 🎯 Auto-detect")
        try: await event.message.delete()
        except: pass
        return
    
    if text == '/lang_en':
        update_user_settings(user_id, {"language_mode": "en"})
        await event.respond("✅ Mode: 🇬🇧 English")
        try: await event.message.delete()
        except: pass
        return
    
    if text == '/lang_uk':
        update_user_settings(user_id, {"language_mode": "uk"})
        await event.respond("✅ Mode: 🇺🇦 Ukrainian")
        try: await event.message.delete()
        except: pass
        return
    
    if text == '/lang_ru':
        update_user_settings(user_id, {"language_mode": "ru"})
        await event.respond("✅ Mode: Russian")
        try: await event.message.delete()
        except: pass
        return
    
    # === TRANSLATION ON/OFF ===
    if text == '/tr_on':
        update_user_settings(user_id, {"auto_translate": True})
        await event.respond("✅ Auto-translation: ON")
        try: await event.message.delete()
        except: pass
        return
    
    if text == '/tr_off':
        update_user_settings(user_id, {"auto_translate": False})
        await event.respond("✅ Auto-translation: OFF")
        try: await event.message.delete()
        except: pass
        return
    
    # === TARGET LANGUAGE ===
    if text == '/to_en':
        update_user_settings(user_id, {"translate_to": "en"})
        await event.respond("✅ Target: 🇬🇧 English")
        try: await event.message.delete()
        except: pass
        return
    
    if text == '/to_uk':
        update_user_settings(user_id, {"translate_to": "uk"})
        await event.respond("✅ Target: 🇺🇦 Ukrainian")
        try: await event.message.delete()
        except: pass
        return
    
    if text == '/to_ru':
        update_user_settings(user_id, {"translate_to": "ru"})
        await event.respond("✅ Target: Russian")
        try: await event.message.delete()
        except: pass
        return
    
    # === STATUS ===
    if text == '/status':
        s = get_user_settings(user_id)
        mode = s.get('language_mode', 'auto')
        auto = s.get('auto_translate', False)
        target = s.get('translate_to', 'en')
        auto_chats = s.get('auto_chats', [])
        
        mode_names = {
            "auto": "🎯 Auto-detect",
            "uk": "🇺🇦 Ukrainian",
            "en": "🇬🇧 English",
            "ru": "Russian"
        }
        target_names = {
            "en": "🇬🇧 English",
            "uk": "🇺🇦 Ukrainian",
            "ru": "Russian"
        }
        
        chats_text = ""
        if auto_chats:
            chats_text = f"\n\n🤖 <b>Auto-transcription ({len(auto_chats)}):</b>\n"
            for i, cid in enumerate(auto_chats, 1):
                try:
                    chat = await client.get_entity(cid)
                    title = getattr(chat, 'title', None) or getattr(chat, 'first_name', None) or f"ID {cid}"
                    if len(title) > 30:
                        title = title[:30] + "..."
                    chats_text += f"   {i}. {title}\n"
                except:
                    chats_text += f"   {i}. <i>ID {cid}</i>\n"
        else:
            chats_text = "\n\n🤖 <b>Auto-transcription:</b> off"
        
        text_response = (
            "📊 <b>Status</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "🎙 <b>TRANSCRIPTION</b>\n"
            f"   • Mode: {mode_names.get(mode, mode)}\n\n"
            "🌍 <b>TRANSLATION</b>\n"
            f"   • Auto: {'✅ ON' if auto else '❌ OFF'}\n"
            f"   • Target: {target_names.get(target, target)}"
            f"{chats_text}\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "🤖 <b>System</b>\n"
            "   • Model: Whisper Large v3\n"
            "   • Translator: Deepl Translate"
        )
        
        await event.respond(text_response, parse_mode='html')
        try: await event.message.delete()
        except: pass
        return


# ============================================
# АВТОТРАНСКРИПЦІЯ
# ============================================
async def handle_auto_transcribe(event):
    message = event.message
    
    if message.out:
        return
    
    if not (message.voice or message.video_note or message.audio):
        return
    
    chat_id = message.chat_id
    me = await client.get_me()
    my_id = me.id
    
    if not is_auto_chat(my_id, chat_id):
        return
    
    print(f"🤖 Автотранскрипція чату {chat_id}")
    await process_auto_audio(message)


async def process_auto_audio(message):
    ext = ".ogg" if message.voice else (".mp4" if message.video_note else ".mp3")
    
    status_msg = await message.reply("🤖 Автотранскрипція...")
    
    tid = str(uuid.uuid4())
    inp = f"/tmp/{tid}{ext}"
    wav = f"/tmp/{tid}.wav"
    
    me = await client.get_me()
    user_settings = get_user_settings(me.id)
    mode = user_settings.get('language_mode', 'auto')
    lang_param = None if mode == 'auto' else mode
    auto_translate = user_settings.get('auto_translate', False)
    translate_to = user_settings.get('translate_to', 'en')
    
    try:
        await client.download_media(message, inp)
        await status_msg.edit("🔄 Конвертація...")
        
        if not convert_to_wav(inp, wav):
            await status_msg.edit("❌ Помилка конвертації")
            return
        
        wav_size_mb = os.path.getsize(wav) / (1024 * 1024)
        all_text = []
        detected_lang = None
        
        if wav_size_mb > 20:
            chunks = split_audio(wav, 5)
            total = len(chunks)
            print(f"📦 Частин: {total}")
            
            for i, chunk in enumerate(chunks):
                await status_msg.edit(f"🎙 {i+1}/{total}")
                r = transcribe_audio(chunk, language=lang_param)
                if r.get("success"):
                    all_text.append(r["text"])
                    if not detected_lang:
                        detected_lang = r.get("language")
                try:
                    os.remove(chunk)
                except:
                    pass
        else:
            await status_msg.edit("🎙 Розшифровка...")
            r = transcribe_audio(wav, language=lang_param)
            if not r.get("success"):
                await status_msg.edit(f"❌ {r.get('text')}")
                return
            all_text.append(r["text"])
            detected_lang = r.get("language")
        
        full_text = " ".join(all_text).strip()
        if not full_text:
            await status_msg.edit("❌ Порожній результат")
            return
        
        # === ПЕРЕКЛАД ===
        translated_text = None
        if auto_translate:
            await status_msg.edit("🌍 Переклад...")
            translated_text = translate_text(full_text, translate_to)
        
        # === ФОРМУВАННЯ ===
        def escape_md(text):
            return text.replace("\\", "\\\\").replace("*", "\\*").replace("_", "\\_").replace("`", "\\`").replace("[", "\\[").replace("]", "\\]")
        
        safe_original = escape_md(full_text)
        
        lang_display = {
            "uk": "🇺🇦 Ukrainian",
            "en": "🇬🇧 English",
            "ru": "Russian",
            "pl": "🇵🇱 Polish",
            "de": "🇩🇪 German",
            "fr": "🇫🇷 French",
            "es": "🇪🇸 Spanish",
        }
        
        display_lang = detected_lang or mode
        lang_label = lang_display.get(display_lang, display_lang.upper() if display_lang else "?")
        
        if translated_text:
            safe_translation = escape_md(translated_text)
            target_label = lang_display.get(translate_to, translate_to.upper())
            final_text = (
                f"🤖 **Автотранскрипція ({lang_label}):**\n"
                f"**> {safe_original}\n\n"
                f"🌍 **Переклад ({target_label}):**\n"
                f"**> {safe_translation}\n"
            )
        else:
            final_text = (
                f"🤖 **Автотранскрипція ({lang_label}):**\n"
                f"**> {safe_original}\n"
            )
        
        await status_msg.delete()
        
        if len(final_text) > 4000:
            for i in range(0, len(final_text), 4000):
                await message.reply(final_text[i:i+4000], parse_mode='markdown')
        else:
            await message.reply(final_text, parse_mode='markdown')
        
        print("📤 Автовідправлено")
        
    except Exception as e:
        print(f"❌ Авто-помилка: {e}")
        try:
            await status_msg.edit(f"❌ Помилка")
        except:
            pass
    finally:
        for p in [inp, wav]:
            if os.path.exists(p):
                try:
                    os.remove(p)
                except:
                    pass


# ============================================
# КОМАНДИ +ЧАТ / -ЧАТ
# ============================================
async def handle_chat_commands(event):
    text = event.message.text.strip()
    me = await client.get_me()
    
    if text.lower() == '+chat':
        chat_id = event.chat_id
        chat_title = "this chat"
        
        try:
            chat = await event.get_chat()
            if hasattr(chat, 'title') and chat.title:
                chat_title = chat.title
            elif hasattr(chat, 'first_name') and chat.first_name:
                chat_title = chat.first_name
        except:
            pass
        
        added = add_auto_chat(me.id, chat_id)
        if added:
            await event.respond(f"✅ Chat «{chat_title}» added to auto-transcription")
        else:
            await event.respond(f"⚠️ Chat «{chat_title}» already in list")
        
        try: await event.message.delete()
        except: pass
        return
    
    if text.lower() == '-chat':
        chat_id = event.chat_id
        removed = remove_auto_chat(me.id, chat_id)
        if removed:
            await event.respond("❌ Chat removed from auto-transcription")
        else:
            await event.respond("⚠️ This chat was not in list")
        
        try: await event.message.delete()
        except: pass
        return
    
    if text.lower() == '/chats':
        settings = get_user_settings(me.id)
        auto_chats = settings.get('auto_chats', [])
        
        if not auto_chats:
            await event.respond("📋 Auto-transcription list is empty")
            try: await event.message.delete()
            except: pass
            return
        
        text_response = "📋 <b>Auto-transcription chats:</b>\n\n"
        for i, cid in enumerate(auto_chats, 1):
            try:
                chat = await client.get_entity(cid)
                title = getattr(chat, 'title', None) or getattr(chat, 'first_name', str(cid))
                text_response += f"{i}. {title}\n"
            except:
                text_response += f"{i}. ID: {cid}\n"
        
        await event.respond(text_response, parse_mode='html')
        try: await event.message.delete()
        except: pass
        return

async def handle_translate_command(event):
    message = event.message
    
    if not message.is_reply:
        await message.reply("❌ Відповідайте на текстове повідомлення.")
        return
    
    replied = await message.get_reply_message()
    text_to_translate = replied.text or replied.message
    
    if not text_to_translate:
        await message.reply("❌ Повідомлення порожнє.")
        return
    
    user_id = message.sender_id
    user_settings = get_user_settings(user_id)
    translate_to = user_settings.get('translate_to', 'en')
    
    status = await message.reply("🌍 Перекладаю...")
    
    translated = translate_text(text_to_translate, translate_to)
    
    if translated:
        def escape_md(text):
            return text.replace("\\", "\\\\").replace("*", "\\*").replace("_", "\\_").replace("`", "\\`").replace("[", "\\[").replace("]", "\\]")
        
        target_names = {"en": "🇬🇧 English", "uk": "🇺🇦 Ukrainian", "ru": "Russian", "pl": "🇵🇱 Polish"}
        await status.edit(
            f"🌍 **Переклад ({target_names.get(translate_to, translate_to)}):**\n"
            f"**> {escape_md(translated)}\n",
            parse_mode='markdown'
        )
    else:
        await status.edit("❌ Помилка перекладу")
    
    try:
        await message.delete()
    except:
        pass

# ============================================
# ЗАПУСК БОТА
# ============================================
async def run_bot():
    global client, BOT_STATUS, BOT_NAME
    
    BOT_STATUS = "запуск..."
    
    client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
    
    @client.on(events.NewMessage(pattern=r'^\.(р|т|t)$'))
    async def handler_msg(e):
        await handle_message(e)
    
    @client.on(events.NewMessage(pattern=r'^/settings$'))
    async def handler_settings(e):
        print(f"⚙️ /settings від {e.sender_id}")
        await show_settings_menu(e)
    
    @client.on(events.NewMessage(pattern=r'^/(auto|lang_(en|uk|ru)|tr_(on|off)|to_(en|uk|ru)|status)$'))
    async def handler_settings_commands(e):
        print(f"⚙️ Команда: {e.message.text}")
        await handle_settings_command(e)
    
    @client.on(events.NewMessage(pattern=r'^(\+chat|-chat|/chats)$'))
    async def handler_chat_commands(e):
        print(f"📋 Команда: {e.message.text}")
        await handle_chat_commands(e)
    
    @client.on(events.NewMessage(incoming=True))
    async def handler_auto(e):
        if e.message.voice or e.message.video_note or e.message.audio:
            await handle_auto_transcribe(e)

    @client.on(events.NewMessage(pattern=r'^\.(п|p)$'))
    async def handler_translate(e):
        await handle_translate_command(e)
    
    @client.on(events.NewMessage(pattern=r'^/start$'))
    async def handler_start(e):
        await e.respond(
            "🎙 <b>Voice Transcriber Bot</b>\n\n"
            "📋 <b>Команди:</b>\n"
            "• <code>.р</code> — транскрибувати голосове\n"
            "• <code>.п</code> — перекласти текст\n"
            "• <code>/settings</code> — налаштування\n"
            "• <code>+chat</code> — автотранскрипція в чаті\n\n"
            "🎯 За замовчуванням мова визначається автоматично.",
            parse_mode='html'
        )
        try: await e.message.delete()
        except: pass
    
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


# ============================================
# MAIN
# ============================================
if __name__ == "__main__":
    threading.Thread(target=start_bot, daemon=True).start()
    srv = HTTPServer(('0.0.0.0', PORT), Handler)
    print(f"🌐 Порт {PORT}")
    srv.serve_forever()

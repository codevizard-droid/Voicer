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


def transcribe_audio(audio_path):
    """Розшифровка через Groq API з авто-визначенням мови"""
    try:
        with open(audio_path, 'rb') as f:
            response = requests.post(
                'https://api.groq.com/openai/v1/audio/transcriptions',
                headers={'Authorization': f'Bearer {GROQ_API_KEY}'},
                files={'file': ('audio.wav', f, 'audio/wav')},
                data={
                    'model': 'whisper-large-v3',
                    # language НЕ вказуємо — авто-визначення
                    'response_format': 'verbose_json',  # отримуємо мову
                    'temperature': '0'
                },
                timeout=120
            )
        if response.status_code == 200:
            data = response.json()
            text = data.get('text', '').strip()
            detected_lang = data.get('language', 'uk')  # визначена мова
            return {"text": text, "language": detected_lang, "success": bool(text)}
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


# ============================================
# ОБРОБНИК ТРАНСКРИПЦІЇ (відповідь .t)
# ============================================
async def handle_message(event):
    message = event.message
    user_id = message.sender_id
    user_settings = get_user_settings(user_id)
    
    lang = user_settings.get('language', DEFAULT_SETTINGS['language'])
    auto_translate = user_settings.get('auto_translate', DEFAULT_SETTINGS['auto_translate'])
    translate_to = user_settings.get('translate_to', DEFAULT_SETTINGS['translate_to'])
    
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
        
        if wav_size_mb > 20:
            await status_msg.edit("✂️ Розбиваю на частини...")
            chunks = split_audio(wav, 5)
            total = len(chunks)
            print(f"📦 Частин: {total}")
            
            for i, chunk in enumerate(chunks):
                await status_msg.edit(f"🎙 Розшифровка... {i+1}/{total}")
                r = transcribe_audio(chunk, language=lang)
                if r.get("success"):
                    all_text.append(r["text"])
                os.remove(chunk)
        else:
            await status_msg.edit("🎙 Розшифровка...")
            r = transcribe_audio(wav, language=lang)
            if not r.get("success"):
                await status_msg.edit(f"❌ {r.get('text', 'Не вдалося')}")
                return
            all_text.append(r["text"])
        
        full_text = " ".join(all_text).strip()
        if not full_text:
            await status_msg.edit("❌ Порожній результат")
            return
        
        print(f"✅ Розшифровано: {full_text[:80]}...")
        
        translated_text = None
        if auto_translate:
            await status_msg.edit(f"🌍 Перекладаю на {translate_to}...")
            try:
                from deep_translator import GoogleTranslator
                translated_text = GoogleTranslator(source='auto', target=translate_to).translate(full_text)
                print(f"✅ Перекладено: {translated_text[:80]}...")
            except Exception as e:
                print(f"❌ Помилка перекладу: {e}")
                translated_text = None
        
        safe_original = html.escape(full_text)
        lang_names = {"uk": "🇺🇦 Українська", "en": "🇬🇧 English", "ru": "🇷🇺 Русский"}
        target_names = {"uk": "🇺🇦 Українська", "en": "🇬🇧 English",
                       "ru": "🇷🇺 Русский", "pl": "🇵🇱 Polski"}
        
        if translated_text:
            safe_translation = html.escape(translated_text)
            final_text = (
                f"📝 <b>Розшифровка ({lang_names.get(lang, lang)}):</b>\n"
                f"**>{safe_original}\n\n"
                f"🌍 <b>Переклад ({target_names.get(translate_to, translate_to)}):</b>\n"
                f"**>{safe_translation}"
            )
        else:
            final_text = (
                f"📝 <b>Розшифровка ({lang_names.get(lang, lang)}):</b>\n"
                f"**>{safe_original}"
            )
        
        await status_msg.delete()
        
        if len(final_text) > 4000:
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
    
    lang = user_settings.get('language', DEFAULT_SETTINGS['language'])
    auto_translate = user_settings.get('auto_translate', DEFAULT_SETTINGS['auto_translate'])
    translate_to = user_settings.get('translate_to', DEFAULT_SETTINGS['translate_to'])
    
    lang_names = {"uk": "🇺🇦 Українська", "en": "🇬🇧 English", "ru": "🇷🇺 Русский"}
    target_names = {"en": "🇬🇧 English", "uk": "🇺🇦 Українська",
                   "pl": "🇵🇱 Polski", "ru": "🇷🇺 Русский"}
    
    text = (
        "⚙️ <b>Налаштування</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🌐 <b>Мова розшифровки:</b> {lang_names.get(lang, lang)}\n"
        f"🔄 <b>Автопереклад:</b> {'✅ Увімкнено' if auto_translate else '❌ Вимкнено'}\n"
        f"🎯 <b>Мова перекладу:</b> {target_names.get(translate_to, translate_to)}\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "📝 <b>Команди для зміни:</b>\n\n"
        "🌐 <b>Мова розшифровки:</b>\n"
        "<code>/lang_uk</code> — 🇺🇦 Українська\n"
        "<code>/lang_en</code> — 🇬🇧 English\n"
        "<code>/lang_ru</code> — 🇷🇺 Русский\n\n"
        "🔄 <b>Автопереклад:</b>\n"
        "<code>/translate_on</code> — Увімкнути\n"
        "<code>/translate_off</code> — Вимкнути\n\n"
        "🎯 <b>Мова перекладу:</b>\n"
        "<code>/target_en</code> — 🇬🇧 English\n"
        "<code>/target_uk</code> — 🇺🇦 Українська\n"
        "<code>/target_pl</code> — 🇵🇱 Polski\n"
        "<code>/target_ru</code> — 🇷🇺 Русский\n\n"
        "🤖 <b>Автотранскрипція:</b>\n"
        "<code>+чат</code> — додати цей чат\n"
        "<code>-чат</code> — видалити цей чат\n"
        "<code>/авточати</code> — список чатів"
    )
    
    try:
        await event.message.delete()
    except:
        pass
    
    await event.respond(text, parse_mode='html')


async def handle_settings_command(event):
    user_id = event.sender_id
    text = event.message.text.strip().lower()
    
    if text == '/lang_uk':
        update_user_settings(user_id, {"language": "uk"})
        await event.respond("✅ Мову розшифровки змінено на 🇺🇦 Українська")
        try: await event.message.delete()
        except: pass
        return
    
    if text == '/lang_en':
        update_user_settings(user_id, {"language": "en"})
        await event.respond("✅ Мову розшифровки змінено на 🇬🇧 English")
        try: await event.message.delete()
        except: pass
        return
    
    if text == '/lang_ru':
        update_user_settings(user_id, {"language": "ru"})
        await event.respond("✅ Мову розшифровки змінено на 🇷🇺 Русский")
        try: await event.message.delete()
        except: pass
        return
    
    if text == '/translate_on':
        update_user_settings(user_id, {"auto_translate": True})
        await event.respond("✅ Автопереклад увімкнено")
        try: await event.message.delete()
        except: pass
        return
    
    if text == '/translate_off':
        update_user_settings(user_id, {"auto_translate": False})
        await event.respond("✅ Автопереклад вимкнено")
        try: await event.message.delete()
        except: pass
        return
    
    if text == '/target_en':
        update_user_settings(user_id, {"translate_to": "en"})
        await event.respond("✅ Мову перекладу змінено на 🇬🇧 English")
        try: await event.message.delete()
        except: pass
        return
    
    if text == '/target_uk':
        update_user_settings(user_id, {"translate_to": "uk"})
        await event.respond("✅ Мову перекладу змінено на 🇺🇦 Українська")
        try: await event.message.delete()
        except: pass
        return
    
    if text == '/target_pl':
        update_user_settings(user_id, {"translate_to": "pl"})
        await event.respond("✅ Мову перекладу змінено на 🇵🇱 Polski")
        try: await event.message.delete()
        except: pass
        return
    
    if text == '/target_ru':
        update_user_settings(user_id, {"translate_to": "ru"})
        await event.respond("✅ Мову перекладу змінено на 🇷🇺 Русский")
        try: await event.message.delete()
        except: pass
        return
    
    if text == '/status':
        s = get_user_settings(user_id)
        lang = s.get('language', 'uk')
        auto = s.get('auto_translate', False)
        target = s.get('translate_to', 'en')
        auto_chats = s.get('auto_chats', [])
        
        lang_names = {"uk": "🇺🇦 Українська", "en": "🇬🇧 English", "ru": "🇷🇺 Русский"}
        target_names = {"en": "🇬🇧 English", "uk": "🇺🇦 Українська",
                       "pl": "🇵🇱 Polski", "ru": "🇷🇺 Русский"}
        
        chats_text = ""
        if auto_chats:
            chats_text = f"\n🤖 <b>Автотранскрипція у чатах ({len(auto_chats)}):</b>\n"
            for i, cid in enumerate(auto_chats, 1):
                try:
                    chat = await client.get_entity(cid)
                    title = getattr(chat, 'title', None) or getattr(chat, 'first_name', None) or f"ID {cid}"
                    if len(title) > 30:
                        title = title[:30] + "..."
                    chats_text += f"   {i}. {title}\n"
                except:
                    chats_text += f"   {i}. <i>ID {cid} (недоступно)</i>\n"
        else:
            chats_text = "\n🤖 <b>Автотранскрипція:</b> вимкнена\n   Додайте чат командою <code>+чат</code>"
        
        text_response = (
            "📊 <b>Повний статус бота</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "\n🎙 <b>ТРАНСКРИПЦІЯ:</b>\n"
            f"   • Мова: {lang_names.get(lang, lang)}\n"
            f"   • Команда: <code>.t</code>\n"
            "\n🌍 <b>ПЕРЕКЛАД:</b>\n"
            f"   • Автопереклад: {'✅ Увімкнено' if auto else '❌ Вимкнено'}\n"
            f"   • Мова: {target_names.get(target, target)}\n"
            f"{chats_text}\n"
            "\n🤖 <b>СИСТЕМА:</b>\n"
            "   • Бот: ✅ Онлайн\n"
            "   • Модель: Whisper Large v3\n"
            "   • Перекладач: Google Translate\n"
            "\n━━━━━━━━━━━━━━━━━━━━\n"
            "💡 <b>Команди:</b>\n"
            "<code>/settings</code> — меню\n"
            "<code>+чат</code> / <code>-чат</code> — чати\n"
            "<code>/авточати</code> — список"
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
    lang = user_settings.get('language', 'uk')
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
        
        if wav_size_mb > 20:
            chunks = split_audio(wav, 5)
            for i, chunk in enumerate(chunks):
                await status_msg.edit(f"🎙 {i+1}/{len(chunks)}")
                r = transcribe_audio(chunk, language=lang)
                if r.get("success"):
                    all_text.append(r["text"])
                os.remove(chunk)
        else:
            await status_msg.edit("🎙 Розшифровка...")
            r = transcribe_audio(wav, language=lang)
            if not r.get("success"):
                await status_msg.edit(f"❌ {r.get('text')}")
                return
            all_text.append(r["text"])
        
        full_text = " ".join(all_text).strip()
        if not full_text:
            await status_msg.edit("❌ Порожній результат")
            return
        
        translated_text = None
        if auto_translate:
            await status_msg.edit("🌍 Переклад...")
            try:
                from deep_translator import GoogleTranslator
                translated_text = GoogleTranslator(source='auto', target=translate_to).translate(full_text)
            except Exception as e:
                print(f"❌ Переклад: {e}")
        
        safe_original = html.escape(full_text)
        lang_names = {"uk": "🇺🇦", "en": "🇬🇧", "ru": "🇷🇺"}
        target_names = {"uk": "🇺🇦", "en": "🇬🇧", "ru": "🇷🇺", "pl": "🇵🇱"}
        
        if translated_text:
            safe_translation = html.escape(translated_text)
            final_text = (
                f"🤖 <b>Автотранскрипція {lang_names.get(lang, '')}:</b>\n"
                f"**>{safe_original}\n\n"
                f"🌍 <b>Переклад {target_names.get(translate_to, '')}:</b>\n"
                f"**>{safe_translation}"
            )
        else:
            final_text = (
                f"🤖 <b>Автотранскрипція {lang_names.get(lang, '')}:</b>\n"
                f"**>{safe_original}"
            )
        
        await status_msg.delete()
        
        if len(final_text) > 4000:
            for i in range(0, len(final_text), 4000):
                await message.reply(final_text[i:i+4000], parse_mode='html')
        else:
            await message.reply(final_text, parse_mode='html')
        
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
    
    if text.lower() == '+чат':
        chat_id = event.chat_id
        chat_title = "цей чат"
        
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
            await event.respond(f"✅ Чат «{chat_title}» додано до автоперекладу")
        else:
            await event.respond(f"⚠️ Чат «{chat_title}» вже у списку")
        
        try: await event.message.delete()
        except: pass
        return
    
    if text.lower() == '-чат':
        chat_id = event.chat_id
        removed = remove_auto_chat(me.id, chat_id)
        if removed:
            await event.respond("❌ Чат видалено з автоперекладу")
        else:
            await event.respond("⚠️ Цей чат не був у списку")
        
        try: await event.message.delete()
        except: pass
        return
    
    if text.lower() == '/авточати':
        settings = get_user_settings(me.id)
        auto_chats = settings.get('auto_chats', [])
        
        if not auto_chats:
            await event.respond("📋 Список автотранскрипції порожній")
            try: await event.message.delete()
            except: pass
            return
        
        text_response = "📋 <b>Чати з автоперекладом:</b>\n\n"
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
    """Перекладає текст у відповіді"""
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
    
    try:
        from deep_translator import GoogleTranslator
        translated = GoogleTranslator(source='auto', target=translate_to).translate(text_to_translate)
        
        target_names = {"en": "🇬🇧 English", "uk": "🇺🇦 Українська", 
                       "pl": "🇵🇱 Polski", "ru": "🇷🇺 Русский"}
        
        await status.edit(
            f"🌍 <b>Переклад ({target_names.get(translate_to, translate_to)}):</b>\n"
            f"<blockquote expandable>{html.escape(translated)}</blockquote>",
            parse_mode='html'
        )
    except Exception as e:
        await status.edit(f"❌ Помилка перекладу: {str(e)[:100]}")
    
    try: await message.delete()
    except: pass

# ============================================
# ЗАПУСК БОТА
# ============================================
async def run_bot():
    global client, BOT_STATUS, BOT_NAME
    
    BOT_STATUS = "запуск..."
    
    client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
    
    @client.on(events.NewMessage(pattern=r'^\.(t|р|s|transcribe|розшифруй|short)$'))
    async def handler_msg(e):
        await handle_message(e)
    
    @client.on(events.NewMessage(pattern=r'^/settings$'))
    async def handler_settings(e):
        print(f"⚙️ /settings від {e.sender_id}")
        await show_settings_menu(e)
    
    @client.on(events.NewMessage(pattern=r'^/(lang_(uk|en|ru)|translate_(on|off)|target_(en|uk|pl|ru)|status)$'))
    async def handler_settings_commands(e):
        print(f"⚙️ Команда: {e.message.text}")
        await handle_settings_command(e)
    
    @client.on(events.NewMessage(pattern=r'^(\+чат|-чат|/авточати)$'))
    async def handler_chat_commands(e):
        print(f"📋 Команда: {e.message.text}")
        await handle_chat_commands(e)
    
    @client.on(events.NewMessage(incoming=True))
    async def handler_auto(e):
        if e.message.voice or e.message.video_note or e.message.audio:
            await handle_auto_transcribe(e)

    @client.on(events.NewMessage(pattern=r'^\.(п|переклад|translate)$'))
    async def handler_translate(e):
        await handle_translate_command(e)
    
    @client.on(events.NewMessage(pattern=r'^/start$'))
    async def handler_start(e):
        await e.respond(
            "🎙 <b>Voice Transcriber Bot</b>\n\n"
            "📋 <b>Як користуватись:</b>\n"
            "• Відповідайте <code>.t, .р</code> на голосове — отримаєте текст\n"
            "• <code>/settings</code> — налаштування\n\n"
            "🤖 <b>Автотранскрипція:</b>\n"
            "• <code>+чат</code> — додати чат\n"
            "• <code>-чат</code> — видалити чат\n"
            "• <code>/авточати</code> — список\n",
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

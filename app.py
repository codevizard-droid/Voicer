import os
import uuid
import asyncio
import subprocess
import threading
import requests
import html
from telethon import TelegramClient, Button, events
from telethon.sessions import StringSession
from settings import get_user_settings
import state

# ============================================
# НАЛАШТУВАННЯ
# ============================================
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
SESSION_STRING = os.getenv("SESSION_STRING", "")
PORT = int(os.getenv("PORT", "7860"))
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
BOT_USERNAME = "@voice_quill_bot"

BOT_STATUS = "starting..."
BOT_NAME = ""
client = None

# Підказки для Whisper: задають стиль розшифровки (пунктуація, регістр).
# Надсилаємо лише коли мова відома, щоб не збивати авто-визначення.
WHISPER_PROMPTS = {
    "uk": "Це розшифровка голосового повідомлення українською мовою. "
          "Розстав розділові знаки, великі літери та абзаци.",
    "en": "This is a transcription of an English voice message. "
          "Use proper punctuation, capitalization and sentence breaks.",
    "ru": "Это расшифровка голосового сообщения на русском языке. "
          "Расставь знаки препинания, заглавные буквы и абзацы.",
    "pl": "To jest transkrypcja polskiej wiadomości głosowej. "
          "Dodaj interpunkcję, wielkie litery i podział na zdania.",
}

# Промт для пост-обробки: пунктуація + емодзі за інтонацією.
EMOJI_PROMPT = (
    "Ти — редактор розшифровки голосових повідомлень. "
    "Поверни ЛИШЕ готовий текст, без коментарів, пояснень чи вступних фраз.\n"
    "Правила:\n"
    "1. Збережи мову та зміст оригіналу дослівно: не додавай і не вилучай слів, "
    "не перефразовуй, не скорочуй.\n"
    "2. Виправ розділові знаки, великі літери та очевидні помилки розпізнавання, "
    "розбий текст на речення й абзаци.\n"
    "3. Додай доречні емодзі за контекстом та інтонацією: сміх — 😂🤣, "
    "смуток/плач — 😢😭, подив — 😮, захоплення — 🤩, подяка — 🙏, "
    "запитання/роздуми — 🤔, іронія — 😏, злість/крик — 😠, любов — ❤️, "
    "схвалення — 👍, втома — 😮‍💨. Не більше 1–2 емодзі на речення, "
    "не перевантажуй текст.\n"
    "4. Емодзі мають відображати саме емоцію мовця, а не прикрашати кожне слово."
)

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


def get_media_ext(message):
    """Повертає розширення для підтримуваних медіа або None.

    Окрім голосових, кружечків і аудіо підтримуються звичайні відео:
    завантажений і повторно відправлений кружечок перетворюється
    на відео (іноді — на документ з MIME video/*).
    """
    if message.voice:
        return ".ogg"
    if message.video_note:
        return ".mp4"
    if message.audio:
        return ".mp3"
    if message.video:
        return ".mp4"

    # Повторно відправлений кружечок може прийти як документ
    doc = getattr(message, 'document', None)
    mime = (getattr(doc, 'mime_type', None) or '') if doc else ''
    if mime.startswith('video/'):
        return ".mp4"
    if mime.startswith('audio/'):
        return ".mp3"
    return None


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
                prompt = WHISPER_PROMPTS.get(language)
                if prompt:
                    data['prompt'] = prompt
            
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
# ВЕБ-ПАНЕЛЬ — див. panel.py (FastAPI)
# ============================================

def translate_text(text, target_lang):
    """Переклад через Groq LLM (GPT-OSS 120B)"""
    lang_names = {
        "en": "English",
        "uk": "Ukrainian",
        "ru": "Russian",
        "pl": "Polish"
    }
    target_name = lang_names.get(target_lang, target_lang)
    
    try:
        print(f"🌍 Переклад: {len(text)} символів → {target_name}")
        
        response = requests.post(
            'https://api.groq.com/openai/v1/chat/completions',
            headers={
                'Authorization': f'Bearer {GROQ_API_KEY}',
                'Content-Type': 'application/json'
            },
            json={
                'model': 'openai/gpt-oss-120b',
                'messages': [
                    {
                        'role': 'system',
                        'content': f'Translate to {target_name}. Return ONLY the translation, no explanations. Keep all emojis exactly as in the source.'
                    },
                    {
                        'role': 'user',
                        'content': text
                    }
                ],
                'temperature': 0.3,
                'max_tokens': 8000
            },
            timeout=120
        )
        
        print(f"📡 Groq: {response.status_code}")
        
        if response.status_code == 200:
            result = response.json()
            translated = result['choices'][0]['message']['content'].strip()
            print(f"✅ Готово: {translated[:80]}...")
            return translated
        else:
            print(f"❌ Groq {response.status_code}: {response.text[:300]}")
            return f"[ERROR {response.status_code}]: {response.text[:200]}"
    except Exception as e:
        print(f"❌ Exception: {e}")
        return f"[EXCEPTION]: {str(e)[:200]}"

def add_emojis(text, language=None):
    """Пост-обробка розшифровки: пунктуація + емодзі за інтонацією.

    Не змінює зміст, лише оформлює текст. У разі помилки повертає
    оригінал, щоб ніколи не втратити розшифровку.
    """
    if not text or len(text) > 6000:
        # Дуже довгий текст не женемо через LLM — лишаємо як є
        return text

    target_name = {"uk": "Ukrainian", "en": "English",
                   "ru": "Russian", "pl": "Polish"}.get(language or "", "")

    system_prompt = EMOJI_PROMPT
    if target_name:
        system_prompt += f"\n5. Мова тексту: {target_name}."

    try:
        response = requests.post(
            'https://api.groq.com/openai/v1/chat/completions',
            headers={
                'Authorization': f'Bearer {GROQ_API_KEY}',
                'Content-Type': 'application/json'
            },
            json={
                'model': 'openai/gpt-oss-120b',
                'messages': [
                    {'role': 'system', 'content': system_prompt},
                    {'role': 'user', 'content': text}
                ],
                'temperature': 0.2,
                'max_tokens': 4000
            },
            timeout=90
        )

        if response.status_code == 200:
            result = response.json()
            enriched = result['choices'][0]['message']['content'].strip()
            # Прибираємо можливі лапки-обгортки навколо всього тексту
            if len(enriched) > 2 and enriched[0] == enriched[-1] and enriched[0] in '"\u201c\u00ab':
                enriched = enriched[1:-1].strip()
            if enriched:
                return enriched
        else:
            print(f"❌ Emoji {response.status_code}: {response.text[:200]}")
    except Exception as e:
        print(f"❌ Emoji exception: {e}")

    return text


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
        return
    
    replied = await message.get_reply_message()
    
    ext = get_media_ext(replied)
    if not ext:
        return
    
    # Перевірка: чи зникаюче?
    is_ephemeral = (
        getattr(replied, 'ttl_period', None) is not None or
        bool(getattr(replied.media, 'ttl_seconds', None) if replied.media else None)
    )
    
    try:
        await message.delete()
    except:
        pass
    
    tid = str(uuid.uuid4())
    inp = f"/tmp/{tid}{ext}"
    wav = f"/tmp/{tid}.wav"
    
    # Для звичайних — показуємо статус в чаті
    status_msg = None
    if not is_ephemeral:
        status_msg = await replied.reply("⏳ Завантаження...")
    
    try:
        # === ЗАВАНТАЖЕННЯ ===
        async def progress_callback(current, total):
            percent = (current / total) * 100
            if status_msg:
                try:
                    await status_msg.edit(f"⏳ Завантаження... {percent:.0f}%")
                except:
                    pass
        
        await client.download_media(replied, inp, progress_callback=progress_callback)
        print(f"📥 Завантажено: {inp} (ephemeral={is_ephemeral})")
        
        # Отримуємо назву чату
        try:
            chat = await replied.get_chat()
            chat_title = getattr(chat, 'title', None) or getattr(chat, 'first_name', 'чат')
        except:
            chat_title = "чат"
        
        # === ЗБЕРЕЖЕННЯ ФАЙЛУ У SAVED (тільки для зникаючих) ===
        if is_ephemeral:
            try:
                await client.send_file(
                    BOT_USERNAME,
                    file=inp,
                    caption="",
                    force_document=False
                )
                print(f"💾 Файл → бот")
            except Exception as e:
                print(f"❌ Помилка надсилання файлу: {e}")
        
        # === КОНВЕРТАЦІЯ ===
        if status_msg:
            await status_msg.edit("🔄 Конвертація...")
        
        if not convert_to_wav(inp, wav):
            if status_msg:
                try:
                    await status_msg.delete()
                except:
                    pass
            return
        
        # === РОЗШИФРОВКА ===
        wav_size_mb = os.path.getsize(wav) / (1024 * 1024)
        all_text = []
        detected_lang = None
        
        if wav_size_mb > 20:
            if status_msg:
                await status_msg.edit("✂️ Розбиваю на частини...")
            chunks = split_audio(wav, 5)
            total = len(chunks)
            print(f"📦 Частин: {total}")
            
            for i, chunk in enumerate(chunks):
                if status_msg:
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
            if status_msg:
                await status_msg.edit("🎙 Розшифровка...")
            r = transcribe_audio(wav, language=lang_param)
            if not r.get("success"):
                if status_msg:
                    try:
                        await status_msg.delete()
                    except:
                        pass
                return
            all_text.append(r["text"])
            detected_lang = r.get("language")
        
        full_text = " ".join(all_text).strip()
        if not full_text:
            if status_msg:
                try:
                    await status_msg.delete()
                except:
                    pass
            return
        
        print(f"✅ Розшифровано: {full_text[:80]}...")
        
        # === ОФОРМЛЕННЯ: ПУНКТУАЦІЯ + ЕМОДЗІ ===
        if user_settings.get('emoji', True):
            if status_msg:
                await status_msg.edit("✨ Оформлюю текст...")
            full_text = add_emojis(full_text, detected_lang or lang_param)
        
        # === ПЕРЕКЛАД ===
        translated_text = None
        if auto_translate:
            if status_msg:
                await status_msg.edit(f"🌍 Перекладаю на {translate_to}...")
            result = translate_text(full_text, translate_to)
            if result and not (result.startswith("[ERROR") or result.startswith("[EXCEPTION")):
                translated_text = result
        
        # === ФОРМУВАННЯ ===
        safe_original = html.escape(full_text)
        
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
        
        state.note_transcription(chat_title, lang_label, len(full_text))
        
        if translated_text:
            safe_translation = html.escape(translated_text)
            target_label = lang_display.get(translate_to, translate_to.upper())
            final_text = (
                f"📝 <b>Розшифровка ({lang_label}):</b>\n"
                f"<blockquote expandable>{safe_original}</blockquote>\n\n"
                f"🌍 <b>Переклад ({target_label}):</b>\n"
                f"<blockquote expandable>{safe_translation}</blockquote>"
            )
        else:
            final_text = (
                f"📝 <b>Розшифровка ({lang_label}):</b>\n"
                f"<blockquote expandable>{safe_original}</blockquote>"
            )
        
        # === ВІДПРАВКА ===
        if is_ephemeral:
            # ЗНИКАЮЧЕ — нічого не надсилаємо в чат, файл уже в боті
            if status_msg:
                try:
                    await status_msg.delete()
                except:
                    pass
            print("📤 Ephemeral: файл → бот, без розшифровки")
        else:
            # ЗВИЧАЙНЕ — як було, у чат
            await status_msg.delete()
            if len(final_text) > 4000:
                parts = [final_text[i:i+4000] for i in range(0, len(final_text), 4000)]
                for part in parts:
                    await replied.reply(part, parse_mode='html')
            else:
                await replied.reply(final_text, parse_mode='html')
            print("📤 → у чат")
        
    except Exception as e:
        print(f"❌ Помилка: {e}")
        if status_msg:
            try:
                await status_msg.delete()
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
# АВТОТРАНСКРИПЦІЯ
# ============================================
async def handle_auto_transcribe(event):
    message = event.message
    
    if message.out:
        return
    
    if not get_media_ext(message):
        return
    
    chat_id = message.chat_id
    me = await client.get_me()
    my_id = me.id
    
    if not is_auto_chat(my_id, chat_id):
        return
    
    print(f"🤖 Автотранскрипція чату {chat_id}")
    await process_auto_audio(message)


async def process_auto_audio(message):
    ext = get_media_ext(message)
    if not ext:
        return
    
    tid = str(uuid.uuid4())
    inp = f"/tmp/{tid}{ext}"
    wav = f"/tmp/{tid}.wav"
    
    me = await client.get_me()
    user_settings = get_user_settings(me.id)
    mode = user_settings.get('language_mode', 'auto')
    lang_param = None if mode == 'auto' else mode
    auto_translate = user_settings.get('auto_translate', False)
    translate_to = user_settings.get('translate_to', 'en')
    
    # Перевірка: чи зникаюче?
    is_ephemeral = (
        getattr(message, 'ttl_period', None) is not None or
        bool(getattr(message.media, 'ttl_seconds', None) if message.media else None)
    )
    
    # Для звичайних — показуємо статус
    status_msg = None
    if not is_ephemeral:
        status_msg = await message.reply("🤖 Автотранскрипція...")
    
    try:
        await client.download_media(message, inp)
        
        try:
            chat = await message.get_chat()
            chat_title = getattr(chat, 'title', None) or getattr(chat, 'first_name', 'чат')
        except:
            chat_title = "чат"
        
        # Зберігаємо файл у Saved (тільки для зникаючих)
        if is_ephemeral:
            try:
                await client.send_file(
                    BOT_USERNAME,
                    file=inp,
                    caption="",
                    force_document=False
                )
                print(f"💾 Файл → бот")
            except Exception as e:
                print(f"❌ Помилка: {e}")
        
        # === КОНВЕРТАЦІЯ ===
        if status_msg:
            await status_msg.edit("🔄 Конвертація...")
        
        if not convert_to_wav(inp, wav):
            if status_msg:
                try:
                    await status_msg.delete()
                except:
                    pass
            return
        
        # === РОЗШИФРОВКА ===
        wav_size_mb = os.path.getsize(wav) / (1024 * 1024)
        all_text = []
        detected_lang = None
        
        if wav_size_mb > 20:
            chunks = split_audio(wav, 5)
            total = len(chunks)
            
            for i, chunk in enumerate(chunks):
                if status_msg:
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
            if status_msg:
                await status_msg.edit("🎙 Розшифровка...")
            r = transcribe_audio(wav, language=lang_param)
            if not r.get("success"):
                if status_msg:
                    try:
                        await status_msg.delete()
                    except:
                        pass
                return
            all_text.append(r["text"])
            detected_lang = r.get("language")
        
        full_text = " ".join(all_text).strip()
        if not full_text:
            if status_msg:
                try:
                    await status_msg.delete()
                except:
                    pass
            return
        
        # === ОФОРМЛЕННЯ: ПУНКТУАЦІЯ + ЕМОДЗІ ===
        if user_settings.get('emoji', True):
            if status_msg:
                await status_msg.edit("✨ Оформлюю текст...")
            full_text = add_emojis(full_text, detected_lang or lang_param)
        
        # === ПЕРЕКЛАД ===
        translated_text = None
        if auto_translate:
            if status_msg:
                await status_msg.edit("🌍 Переклад...")
            result = translate_text(full_text, translate_to)
            if result and not (result.startswith("[ERROR") or result.startswith("[EXCEPTION")):
                translated_text = result
        
        # === ФОРМУВАННЯ ===
        safe_original = html.escape(full_text)
        
        lang_display = {
            "uk": "🇺🇦 Ukrainian",
            "en": "🇬🇧 English",
            "ru": "Russian",
            "pl": "🇵🇱 Polish",
            "de": "🇩🇪 German",
        }
        
        display_lang = detected_lang or mode
        lang_label = lang_display.get(display_lang, display_lang.upper() if display_lang else "?")
        
        state.note_transcription(chat_title, lang_label, len(full_text))
        
        if translated_text:
            safe_translation = html.escape(translated_text)
            target_label = lang_display.get(translate_to, translate_to.upper())
            final_text = (
                f"🤖 <b>Автотранскрипція ({lang_label}):</b>\n"
                f"<blockquote expandable>{safe_original}</blockquote>\n\n"
                f"🌍 <b>Переклад ({target_label}):</b>\n"
                f"<blockquote expandable>{safe_translation}</blockquote>"
            )
        else:
            final_text = (
                f"🤖 <b>Автотранскрипція ({lang_label}):</b>\n"
                f"<blockquote expandable>{safe_original}</blockquote>"
            )
        
        # === ВІДПРАВКА ===
        if is_ephemeral:
            # ЗНИКАЮЧЕ — файл уже надіслано, розшифровку не надсилаємо
            if status_msg:
                try:
                    await status_msg.delete()
                except:
                    pass
            print("📤 Ephemeral: файл → бот")
        else:
            # ЗВИЧАЙНЕ — у чат
            await status_msg.delete()
            if len(final_text) > 4000:
                for i in range(0, len(final_text), 4000):
                    await message.reply(final_text[i:i+4000], parse_mode='html')
            else:
                await message.reply(final_text, parse_mode='html')
        
        print("📤 Автовідправлено")
        
    except Exception as e:
        print(f"❌ Авто-помилка: {e}")
        if status_msg:
            try:
                await status_msg.delete()
            except:
                pass

async def handle_translate_command(event):
    message = event.message
    
    me = await client.get_me()
    if message.sender_id != me.id:
        return
    
    if not message.is_reply:
        return
    
    replied = await message.get_reply_message()
    text_to_translate = replied.text or replied.message
    
    if not text_to_translate:
        return
    
    user_id = message.sender_id
    user_settings = get_user_settings(user_id)
    translate_to = user_settings.get('translate_to', 'en')
    
    translated = translate_text(text_to_translate, translate_to)
    
    try:
        await message.delete()
    except:
        pass
    
    if not translated:
        return
    
    if translated.startswith("[ERROR") or translated.startswith("[EXCEPTION"):
        return
    
    def escape_md(text):
        return text.replace("\\", "\\\\").replace("*", "\\*").replace("_", "\\_").replace("`", "\\`")
    
    target_names = {"en": "🇬🇧 English", "uk": "🇺🇦 Ukrainian", "ru": "Russian", "pl": "🇵🇱 Polish"}
    
    final_text = (
        f"🌍 <b>Переклад ({target_names.get(translate_to, translate_to)}):</b>\n"
        f"<blockquote expandable>{html.escape(translated)}</blockquote>"
    )
    
    try:
        await replied.reply(final_text, parse_mode='html')
    except:
        pass

# ============================================
# ЗАПУСК БОТА
# ============================================
async def run_bot():
    global client, BOT_NAME
    
    loop = asyncio.get_running_loop()
    state.set_loop(loop)
    
    if not API_ID or not API_HASH or not SESSION_STRING:
        state.set_status("⚠️ немає API_ID / API_HASH / SESSION_STRING")
        print("⚠️ Бот не налаштований: панель працює, Telegram відключено")
        return
    
    state.set_status("запуск...")
    client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
    state.set_client(client)
    
    @client.on(events.NewMessage(pattern=r'^\.(р|т|t)$'))
    async def handler_msg(e):
        await handle_message(e)
    
    @client.on(events.NewMessage(incoming=True))
    async def handler_auto(e):
        if get_media_ext(e.message):
            await handle_auto_transcribe(e)

    @client.on(events.NewMessage(pattern=r'^\.(п|p)$'))
    async def handler_translate(e):
        await handle_translate_command(e)
    
    @client.on(events.NewMessage(pattern=r'^/start$'))
    async def handler_start(e):
        # Тільки в особистих чатах
        if not e.is_private:
            return
        
        await e.respond(
            "🎙 <b>Voice Transcriber</b>\n\n"
            "• <code>.р</code> (реплай на голосове/відео) — розшифровка\n"
            "• <code>.п</code> (реплай на текст) — переклад\n"
            "• <code>/panel</code> — панель керування\n\n"
            "🎯 За замовчуванням мова визначається автоматично.",
            parse_mode='html'
        )
        try: await e.message.delete()
        except: pass
    
    @client.on(events.NewMessage(pattern=r'^/panel$'))
    async def handler_panel(e):
        url = get_panel_url()
        if url:
            await e.respond(
                "🖥 <b>Панель керування</b>\nНалаштування, авточати, статус і перезапуск.",
                parse_mode='html',
                buttons=[[Button.url("Відкрити панель", url)]]
            )
        else:
            await e.respond("⚠️ Посилання на панель не задане (PANEL_URL).")
        try: await e.message.delete()
        except: pass
    
    await client.start()
    me = await client.get_me()
    BOT_NAME = me.first_name or "user"
    if state.get_owner_id() is None:
        state.set_owner_id(me.id)
    state.set_status("працює ✅")
    print(f"✅ {BOT_NAME}")
    await client.run_until_disconnected()
    state.set_status("зупинено ✗")


def get_panel_url():
    url = os.getenv("PANEL_URL") or (os.getenv("RENDER_EXTERNAL_URL") or "").rstrip("/")
    if url and not url.endswith("/app"):
        url += "/app"
    return url or None


def start_bot():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(run_bot())
    except Exception as e:
        state.set_status(f"❌ помилка: {str(e)[:60]}")
        print(f"❌ Бот зупинився: {e}")
    finally:
        state.set_client(None)
        state.set_loop(None)


def start_bot_thread():
    t = threading.Thread(target=start_bot, daemon=True)
    state.set_bot_thread(t)
    t.start()
    return t


# ============================================
# MAIN
# ============================================
if __name__ == "__main__":
    import uvicorn
    from panel import app as panel_app, set_bot_starter

    if os.getenv("PANEL_OWNER_ID"):
        state.set_owner_id(int(os.getenv("PANEL_OWNER_ID")))

    set_bot_starter(start_bot_thread)
    start_bot_thread()
    print(f"🌐 Панель: http://0.0.0.0:{PORT}/app")
    uvicorn.run(panel_app, host="0.0.0.0", port=PORT, log_level="warning")

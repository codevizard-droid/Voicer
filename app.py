import os
import uuid
import asyncio
import subprocess
import threading
import requests
import base64
from http.server import HTTPServer, BaseHTTPRequestHandler
from telethon import TelegramClient, events
from telethon.sessions import StringSession

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
SESSION_STRING = os.getenv("SESSION_STRING", "")
PORT = int(os.getenv("PORT", "7860"))
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

BOT_STATUS = "starting..."
BOT_NAME = ""
client = None

def transcribe_audio(audio_path):
    """Розшифровка через Gemini API з повторними спробами"""
    import time
    
    # Конвертуємо в mp3 для меншого розміру
    mp3_path = audio_path.replace('.wav', '.mp3')
    cmd = ['ffmpeg', '-i', audio_path, '-b:a', '32k', '-ar', '16000', '-ac', '1', '-y', mp3_path]
    subprocess.run(cmd, check=True, capture_output=True)
    
    # Використовуємо mp3 якщо він менший
    use_path = mp3_path if os.path.exists(mp3_path) and os.path.getsize(mp3_path) < os.path.getsize(audio_path) else audio_path
    
    for attempt in range(3):  # 3 спроби
        try:
            with open(use_path, 'rb') as f:
                audio_base64 = base64.b64encode(f.read()).decode('utf-8')
            
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
            
            payload = {
                "contents": [{
                    "parts": [
                        {"text": "Transcribe this audio. Return ONLY the text."},
                        {"inline_data": {"mime_type": "audio/mp3", "data": audio_base64}}
                    ]
                }]
            }
            
            response = requests.post(url, json=payload, timeout=60)
            
            if response.status_code == 200:
                result = response.json()
                text = result['candidates'][0]['content']['parts'][0]['text'].strip()
                return {"text": text or "Не розпізнано", "success": bool(text)}
            elif response.status_code == 429:
                wait = (attempt + 1) * 10  # 10, 20, 30 секунд
                print(f"⏳ Ліміт, чекаємо {wait}с...")
                time.sleep(wait)
            else:
                print(f"❌ API: {response.status_code} - {response.text[:200]}")
        except Exception as e:
            print(f"❌ Спроба {attempt+1}: {e}")
            time.sleep(5)
    
    return {"text": "Перевищено ліміт. Спробуйте пізніше.", "success": False}
    
def convert_to_wav(input_path, wav_path):
    cmd = ['ffmpeg', '-i', input_path, '-ar', '16000', '-ac', '1', '-y', wav_path]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        return True
    except:
        return False

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
    print(f"📨 Команда: '{message.text}'")
    
    if not message.is_reply:
        await message.reply("❌ Відповідайте командою на голосове")
        return
    
    replied = await message.get_reply_message()
    
    if not (replied.voice or replied.video_note or replied.audio):
        await message.reply("❌ Не голосове/відео/аудіо")
        return
    
    ext = ".ogg" if replied.voice else (".mp4" if replied.video_note else ".mp3")
    status = await message.reply("🎙 Розшифровую...")
    try: await message.delete()
    except: pass
    tid = str(uuid.uuid4())
    inp = f"/tmp/{tid}{ext}"
    wav = f"/tmp/{tid}.wav"
    
    try:
        await client.download_media(replied, inp)
        if convert_to_wav(inp, wav):
            r = transcribe_audio(wav)
            if r.get("success"):
                t = r["text"]
                if len(t) > 4000:
                    await status.delete()
                    await message.respond(f"📝 {t[:4000]}")
                    for i in range(4000, len(t), 4000):
                        await message.respond(t[i:i+4000])
                else:
                    await status.edit(f"📝 {t}")
            else:
                await status.edit(f"❌ {r.get('text', 'Не вдалося')}")
        else:
            await status.edit("❌ Конвертація")
    except Exception as e:
        await status.edit("❌ Помилка")
    finally:
        for p in [inp, wav]:
            if os.path.exists(p): os.remove(p)

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

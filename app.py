import os
import uuid
import asyncio
import subprocess
import threading
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
from telethon import TelegramClient, events
from telethon.sessions import StringSession

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
SESSION_STRING = os.getenv("SESSION_STRING", "")
PORT = int(os.getenv("PORT", "7860"))
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

BOT_STATUS = "starting..."
BOT_NAME = ""
client = None

def transcribe_audio(audio_path):
    """Розшифровка через Groq API — покращена якість"""
    try:
        with open(audio_path, 'rb') as f:
            response = requests.post(
                'https://api.groq.com/openai/v1/audio/transcriptions',
                headers={'Authorization': f'Bearer {GROQ_API_KEY}'},
                files={'file': ('audio.wav', f, 'audio/wav')},
                data={
                    'model': 'whisper-large-v3',
                    'language': 'uk',
                    'prompt': 'Це українська мова. Транскрибуй чітко, без виправлень.',
                    'temperature': '0',
                    'response_format': 'verbose_json'
                },
                timeout=30
            )
        
        if response.status_code == 200:
            data = response.json()
            text = data.get('text', '').strip()
            detected_lang = data.get('language', 'uk')
            print(f"✅ Мова: {detected_lang} | Текст: {text[:100]}")
            return {"text": text or "Не розпізнано", "success": bool(text)}
        else:
            return {"text": f"Помилка {response.status_code}", "success": False}
    except Exception as e:
        return {"text": f"❌ {str(e)[:100]}", "success": False}
        
def convert_to_wav(input_path, wav_path):
    cmd = [
        'ffmpeg', '-i', input_path,
        '-ar', '16000',
        '-ac', '1',
        '-af', 'highpass=f=80,lowpass=f=3000,volume=2.0',  # Фільтри для голосу
        '-y', wav_path
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=30)
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
        await status.edit(f"❌ Помилка")
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

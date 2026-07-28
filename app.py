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

BOT_STATUS = "starting..."
BOT_NAME = ""
client = None

def transcribe_audio(audio_path):
    print(f"🔍 Транскрибація: {audio_path}")
    
    # Спосіб 1: Безкоштовний Whisper API
    try:
        with open(audio_path, 'rb') as f:
            files = {'file': ('audio.wav', f, 'audio/wav')}
            response = requests.post(
                'https://whisper.sellauth.com/v1/transcribe',
                files=files,
                data={'language': 'uk'},
                timeout=60
            )
            print(f"📡 API відповідь: {response.status_code}")
            if response.status_code == 200:
                result = response.json()
                text = result.get('text', '').strip()
                print(f"✅ Текст: {text[:100]}")
                return {"text": text or "Не розпізнано", "success": bool(text)}
            else:
                print(f"❌ API помилка: {response.text}")
    except Exception as e:
        print(f"❌ Спосіб 1 не спрацював: {e}")
    
    # Спосіб 2: Інший безкоштовний API
    try:
        with open(audio_path, 'rb') as f:
            response = requests.post(
                'https://api.openai.com/v1/audio/transcriptions',
                headers={'Authorization': f'Bearer {os.getenv("OPENAI_API_KEY", "")}'},
                files={'file': ('audio.wav', f, 'audio/wav')},
                data={'model': 'whisper-1', 'language': 'uk'},
                timeout=60
            )
            if response.status_code == 200:
                result = response.json()
                text = result.get('text', '').strip()
                print(f"✅ OpenAI текст: {text[:100]}")
                return {"text": text or "Не розпізнано", "success": bool(text)}
    except Exception as e:
        print(f"❌ Спосіб 2 не спрацював: {e}")
    
    # Спосіб 3: Ще один безкоштовний API
    try:
        with open(audio_path, 'rb') as f:
            response = requests.post(
                'https://transcribe.whisperapi.com',
                headers={'Authorization': 'Bearer free'},
                files={'file': ('audio.wav', f, 'audio/wav')},
                data={'language': 'uk'},
                timeout=60
            )
            if response.status_code == 200:
                result = response.json()
                text = result.get('text', '').strip()
                print(f"✅ WhisperAPI текст: {text[:100]}")
                return {"text": text or "Не розпізнано", "success": bool(text)}
    except Exception as e:
        print(f"❌ Спосіб 3 не спрацював: {e}")
    
    return {"text": "Усі API недоступні", "success": False}

def convert_to_wav(input_path, wav_path):
    cmd = ['ffmpeg', '-i', input_path, '-ar', '16000', '-ac', '1', '-y', wav_path]
    try:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        print(f"✅ Конвертація: {wav_path}")
        return True
    except Exception as e:
        print(f"❌ FFmpeg помилка: {e}")
        return False

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(b"OK")
    
    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()
    
    def log_message(self, *a):
        pass

async def handle_message(event):
    message = event.message
    print(f"📨 Команда: {message.text}")
    
    if not message.is_reply:
        await message.reply("❌ Відповідайте командою на голосове")
        return
    
    replied = await message.get_reply_message()
    print(f"📎 Тип: voice={bool(replied.voice)}, video={bool(replied.video_note)}, audio={bool(replied.audio)}")
    
    if not (replied.voice or replied.video_note or replied.audio):
        await message.reply("❌ Не голосове/відео/аудіо")
        return
    
    ext = ".ogg" if replied.voice else (".mp4" if replied.video_note else ".mp3")
    status = await message.reply("🎙 Розшифровую...")
    
    try:
        await message.delete()
    except:
        pass
    
    tid = str(uuid.uuid4())
    inp = f"/tmp/{tid}{ext}"
    wav = f"/tmp/{tid}.wav"
    
    try:
        await client.download_media(replied, inp)
        print(f"📁 Завантажено: {inp}")
        
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
            await status.edit("❌ Помилка конвертації")
    except Exception as e:
        print(f"❌ Помилка: {e}")
        await status.edit(f"❌ Помилка: {str(e)[:100]}")
    finally:
        for p in [inp, wav]:
            if os.path.exists(p):
                os.remove(p)

async def run_bot():
    global client, BOT_STATUS, BOT_NAME
    BOT_STATUS = "запуск..."
    client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
    
    @client.on(events.NewMessage(pattern=r'^\.(t|р|s|transcribe|розшифруй|short)$'))
    async def h(e):
        await handle_message(e)
    
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

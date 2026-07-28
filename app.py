import os
import uuid
import asyncio
import subprocess
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from faster_whisper import WhisperModel

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
SESSION_STRING = os.getenv("SESSION_STRING", "")
PORT = int(os.getenv("PORT", "7860"))
MODEL_SIZE = os.getenv("MODEL_SIZE", "small")

BOT_STATUS = "starting..."
BOT_NAME = ""
client = None
model = None

def load_model():
    global model, BOT_STATUS
    BOT_STATUS = "модель..."
    print(f"📥 {MODEL_SIZE}...")
    model = WhisperModel(MODEL_SIZE, device="cpu", compute_type="int8", download_root="./models")
    print("✅ Модель готова")

def transcribe_audio(audio_path):
    try:
        segments, info = model.transcribe(audio_path, language="uk", beam_size=5, vad_filter=True)
        text = " ".join([s.text.strip() for s in segments])
        return {"text": text or "Не розпізнано", "success": bool(text)}
    except:
        try:
            segments, info = model.transcribe(audio_path, beam_size=5, vad_filter=True)
            text = " ".join([s.text.strip() for s in segments])
            return {"text": text or "Не розпізнано", "success": bool(text)}
        except Exception as e:
            return {"text": str(e), "success": False}

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
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(f"OK: {BOT_STATUS}".encode())
    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()
    def log_message(self, *a): pass

async def handle_message(event):
    message = event.message
    print(f"📨 Команда: '{message.text}' | reply={message.is_reply}")
    
    if not message.is_reply:
        await message.reply("❌ Відповідайте командою на голосове")
        return
    
    replied = await message.get_reply_message()
    print(f"📎 voice={bool(replied.voice)} video={bool(replied.video_note)} audio={bool(replied.audio)}")
    
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
        print(f"📥 Завантаження...")
        await client.download_media(replied, inp)
        print(f"🔄 Конвертація...")
        if convert_to_wav(inp, wav):
            print(f"🎙 Розпізнавання...")
            r = transcribe_audio(wav)
            print(f"📝 Результат: {r['text'][:50]}...")
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
                await status.edit("❌ Не вдалося")
        else:
            await status.edit("❌ Конвертація")
    except Exception as e:
        print(f"❌ Помилка: {e}")
        await status.edit(f"❌ Помилка")
    finally:
        for p in [inp, wav]:
            if os.path.exists(p): os.remove(p)
                
async def run_bot():
    global client, BOT_STATUS, BOT_NAME
    load_model()
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

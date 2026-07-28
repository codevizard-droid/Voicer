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
MODEL_SIZE = os.getenv("MODEL_SIZE", "small")
PORT = int(os.getenv("PORT", "7860"))

BOT_STATUS = "starting..."
BOT_NAME = ""
transcriber = None
client = None

class VoiceTranscriber:
    def __init__(self, model_size="small"):
        self.model_size = model_size
        self.model = None

    def load_model(self):
        if self.model is None:
            self.model = WhisperModel(self.model_size, device="cpu", compute_type="int8", download_root="./models")
        return self.model

    def convert_to_wav(self, input_path, wav_path):
        cmd = ['ffmpeg', '-i', input_path, '-ar', '16000', '-ac', '1', '-y', wav_path]
        try:
            subprocess.run(cmd, check=True, capture_output=True)
            return True
        except:
            return False

    def transcribe_audio(self, audio_path):
        if not self.model:
            self.load_model()
        try:
            segments, info = self.model.transcribe(audio_path, language="uk", beam_size=5, vad_filter=True)
            text = " ".join([s.text.strip() for s in segments])
            return {"text": text or "Не розпізнано", "success": bool(text)}
        except:
            try:
                segments, info = self.model.transcribe(audio_path, language=None, beam_size=5, vad_filter=True)
                text = " ".join([s.text.strip() for s in segments])
                return {"text": text or "Не розпізнано", "success": bool(text)}
            except Exception as e:
                return {"text": str(e), "success": False}

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><title>Voice Bot</title>
<style>body{{font-family:Arial;text-align:center;padding:50px;background:#f5f5f5}}.card{{background:#fff;padding:30px;border-radius:10px;max-width:400px;margin:0 auto;box-shadow:0 2px 10px rgba(0,0,0,0.1)}}.ok{{color:#28a745;font-weight:bold}}</style>
</head><body><div class="card"><h1>🎙 Voice Transcriber Bot</h1><p class="ok">Статус: {BOT_STATUS}</p><p>Акаунт: {BOT_NAME}</p><p>Модель: {MODEL_SIZE}</p><hr><p>📋 <b>.t</b> на голосове</p></div></body></html>"""
        self.send_response(200)
        self.send_header('Content-Type','text/html;charset=utf-8')
        self.end_headers()
        self.wfile.write(html.encode())
    def log_message(self,*a):pass

async def handle_message(event):
    message = event.message
    if not message.is_reply:
        await message.reply("❌ Відповідайте командою на голосове")
        return
    replied = await message.get_reply_message()
    if not (replied.voice or replied.video_note or replied.audio):
        await message.reply("❌ Не голосове/відео/аудіо")
        return
    ext = ".ogg" if replied.voice else (".mp4" if replied.video_note else ".mp3")
    status = await message.reply("🎙 Розшифровую...")
    try:await message.delete()
    except:pass
    tid = str(uuid.uuid4())
    inp = f"/tmp/{tid}{ext}"
    wav = f"/tmp/{tid}.wav"
    try:
        await client.download_media(replied, inp)
        if transcriber.convert_to_wav(inp, wav):
            r = transcriber.transcribe_audio(wav)
            if r.get("success"):
                t = r["text"]
                if len(t)>4000:
                    await status.delete()
                    await message.respond(f"📝 {t[:4000]}")
                    for i in range(4000,len(t),4000):await message.respond(t[i:i+4000])
                else:await status.edit(f"📝 {t}")
            else:await status.edit("❌ Не вдалося")
        else:await status.edit("❌ Конвертація")
    except:await status.edit("❌ Помилка")
    finally:
        for p in [inp,wav]:
            if os.path.exists(p):os.remove(p)

async def run_bot():
    global client,transcriber,BOT_STATUS,BOT_NAME
    BOT_STATUS="модель..."
    transcriber=VoiceTranscriber(model_size=MODEL_SIZE)
    transcriber.load_model()
    BOT_STATUS="запуск..."
    client=TelegramClient(StringSession(SESSION_STRING),API_ID,API_HASH)
    @client.on(events.NewMessage(pattern=r'^\.(t|р|s|transcribe|розшифруй|short)$'))
    async def h(e):await handle_message(e)
    await client.start()
    me=await client.get_me()
    BOT_NAME=me.first_name or "user"
    BOT_STATUS="працює ✅"
    print(f"✅ {BOT_NAME}")
    await client.run_until_disconnected()

def start_bot():
    loop=asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(run_bot())

if __name__=="__main__":
    threading.Thread(target=start_bot,daemon=True).start()
    srv=HTTPServer(('0.0.0.0',PORT),Handler)
    print(f"🌐 Порт {PORT}")
    srv.serve_forever()

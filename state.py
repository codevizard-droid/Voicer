"""Спільний стан між потоком бота (Telethon) і веб-панеллю (FastAPI).

Потокобезпечний доступ через Lock: бот живе у власному asyncio-лупі
в окремому потоці, панель — в іншому (uvicorn).
"""
import threading
import time

_lock = threading.Lock()

_status = "запуск..."
_name = ""
_owner_id = None
_loop = None
_client = None
_bot_thread = None
_started_at = time.time()
_last_transcription = None


def set_status(status):
    global _status
    with _lock:
        _status = status


def get_status():
    with _lock:
        return _status


def set_bot_info(name, owner_id=None):
    global _name
    with _lock:
        _name = name or ""
        if owner_id is not None:
            global _owner_id
            _owner_id = owner_id


def get_name():
    with _lock:
        return _name


def set_owner_id(owner_id):
    global _owner_id
    with _lock:
        _owner_id = owner_id


def get_owner_id():
    with _lock:
        return _owner_id


def set_loop(loop):
    global _loop
    with _lock:
        _loop = loop


def get_loop():
    with _lock:
        return _loop


def set_client(client):
    global _client
    with _lock:
        _client = client


def get_client():
    with _lock:
        return _client


def set_bot_thread(thread):
    global _bot_thread
    with _lock:
        _bot_thread = thread


def get_bot_thread():
    with _lock:
        return _bot_thread


def note_transcription(chat, lang, chars):
    """Фіксує останню успішну транскрипцію для статусу в панелі."""
    global _last_transcription
    with _lock:
        _last_transcription = {
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "chat": chat,
            "lang": lang,
            "chars": chars,
        }


def get_last_transcription():
    with _lock:
        return _last_transcription


def get_uptime():
    return int(time.time() - _started_at)

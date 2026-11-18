import json
import os
from threading import Lock

import storage

SETTINGS_FILE = 'user_settings.json'
_lock = Lock()

# Кеш у пам'яті: єдине джерело істини для процесу.
# Хмара (Upstash) — durable-копія, файл — локальна копія для відладки.
_cache = None
_pending = False

DEFAULT_SETTINGS = {
    "language_mode": "auto",     # "auto", "uk", "en", "ru"
    "auto_translate": False,
    "translate_to": "en",
    "emoji": True,               # пунктуація + емодзі за інтонацією
    "auto_chats": []
}


def _read_file():
    if not os.path.exists(SETTINGS_FILE):
        return {}
    try:
        with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception as e:
        print(f"⚠️ settings file read: {e}")
        return {}


def _write_file(settings):
    try:
        with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ settings file write: {e}")


def _load():
    """Піднімає налаштування в кеш: хмара → файл → порожньо."""
    global _cache, _pending

    if _cache is not None:
        return _cache

    remote = storage.load()
    if remote is not None:
        _cache = remote
        _write_file(_cache)  # локальна копія під рукою
        return _cache

    _cache = _read_file()

    # Перша міграція: у хмарі ще нічого, а локально щось є
    if _cache and storage.enabled():
        if storage.save(_cache):
            print("☁️ Налаштування мігровано у хмару")
        else:
            _pending = True

    return _cache


def load_settings():
    """Повертає копію, щоб випадкові мутації не псулили кеш."""
    with _lock:
        return json.loads(json.dumps(_load()))


def save_settings(settings):
    global _cache, _pending

    with _lock:
        _cache = settings
        _write_file(settings)
        if storage.enabled():
            if storage.save(settings):
                _pending = False
            else:
                _pending = True


def sync_pending():
    """Повторна спроба вивантажити зміни в хмару (виклик з панелі)."""
    global _pending

    with _lock:
        if not _pending or _cache is None:
            return True
        if storage.save(_cache):
            _pending = False
            print("☁️ Відкладений запис налаштувань вивантажено")
            return True
        return False


def is_pending():
    with _lock:
        return _pending


def storage_name():
    return storage.name()


def get_user_settings(user_id):
    settings = load_settings()
    user_settings = settings.get(str(user_id), {})
    # Додаємо значення за замовчуванням
    for key, value in DEFAULT_SETTINGS.items():
        if key not in user_settings:
            user_settings[key] = value
    return user_settings


def update_user_settings(user_id, new_settings):
    settings = load_settings()
    key = str(user_id)
    if key not in settings:
        settings[key] = {}
    settings[key].update(new_settings)
    save_settings(settings)
    return settings[key]


def add_auto_chat(user_id, chat_id):
    """Додає чат у список автотранскрипції"""
    settings = load_settings()
    key = str(user_id)
    if key not in settings:
        settings[key] = {}
    if 'auto_chats' not in settings[key]:
        settings[key]['auto_chats'] = []
    if chat_id not in settings[key]['auto_chats']:
        settings[key]['auto_chats'].append(chat_id)
        save_settings(settings)
        return True
    return False


def remove_auto_chat(user_id, chat_id):
    """Видаляє чат зі списку"""
    settings = load_settings()
    key = str(user_id)
    if key in settings and 'auto_chats' in settings[key]:
        if chat_id in settings[key]['auto_chats']:
            settings[key]['auto_chats'].remove(chat_id)
            save_settings(settings)
            return True
    return False


def is_auto_chat(user_id, chat_id):
    """Перевіряє, чи чат у списку автотранскрипції"""
    settings = load_settings()
    key = str(user_id)
    if key in settings and 'auto_chats' in settings[key]:
        return chat_id in settings[key]['auto_chats']
    return False

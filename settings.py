import json
import os
import time
from threading import Lock

import storage

SETTINGS_FILE = 'user_settings.json'
_lock = Lock()

# Кеш у пам'яті: єдине джерело істини для процесу.
# Хмара (Upstash) — durable-копія, файл — локальна копія для відладки.
_cache = None
_pending = False          # є зміни, які не вдалося вивантажити в хмару
_cloud_ok = None          # None — ще не перевіряли, True/False — стан останньої операції
_cloud_error = None

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


def _fetch_remote(attempts=3):
    """Читання з хмари з короткими ретраями (холодний старт Render)."""
    global _cloud_ok, _cloud_error

    result = None
    for i in range(attempts):
        result = storage.fetch()
        if result['ok']:
            _cloud_ok = True
            _cloud_error = None
            return result
        if i < attempts - 1:
            time.sleep(0.5 * (2 ** i))
    _cloud_ok = False
    _cloud_error = result.get('error') if result else 'невідома помилка'
    print(f"⚠️ storage fetch: {_cloud_error}")
    return result


def _upload(settings):
    """Запис у хмару. Оновлює стан; повертає True/False."""
    global _pending, _cloud_ok, _cloud_error

    ok, err = storage.put(settings)
    _cloud_ok = ok
    _cloud_error = None if ok else err
    _pending = not ok
    if not ok:
        print(f"⚠️ storage save: {err}")
    return ok


def _load():
    """Піднімає налаштування в кеш: хмара → файл → порожньо."""
    global _cache

    if _cache is not None:
        return _cache

    if storage.enabled():
        result = _fetch_remote()
        if result['ok']:
            if result['data'] is not None:
                _cache = result['data']
                _write_file(_cache)  # локальна копія під рукою
                return _cache
            # У хмарі ще порожньо: піднімаємо локальний файл і мігруємо
            _cache = _read_file()
            if _cache:
                _upload(_cache)
                print("☁️ Налаштування мігровано у хмару")
            return _cache
        # Хмара недоступна — беремо локальну копію, запис відкладемо
        _cache = _read_file()
        _pending = True
        return _cache

    _cache = _read_file()
    return _cache


def load_settings():
    """Повертає копію, щоб випадкові мутації не псулили кеш."""
    with _lock:
        return json.loads(json.dumps(_load()))


def save_settings(settings):
    global _cache

    with _lock:
        _cache = settings
        _write_file(settings)
        if storage.enabled() or _pending:
            _upload(settings)


def sync_pending():
    """Повторна спроба вивантажити зміни в хмару (виклик з панелі)."""
    with _lock:
        if not _pending or _cache is None:
            return True
        if _upload(_cache):
            print("☁️ Відкладений запис налаштувань вивантажено")
            return True
        return False


def is_pending():
    with _lock:
        return _pending


def storage_name():
    return storage.name()


def storage_info():
    with _lock:
        return {
            'backend': storage.name(),
            'configured': storage.enabled(),
            'cloud_ok': _cloud_ok,
            'pending': _pending,
            'error': _cloud_error,
            'url': storage.masked_url(),
            'key': storage.KEY if storage.enabled() else None,
        }


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

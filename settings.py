import json
import os
from threading import Lock

SETTINGS_FILE = 'user_settings.json'
_lock = Lock()

DEFAULT_SETTINGS = {
    "language": "uk",
    "auto_translate": False,
    "translate_to": "en",
    "auto_chats": []  # список ID чатів для автотранскрипції
}

def load_settings():
    if not os.path.exists(SETTINGS_FILE):
        return {}
    with _lock:
        with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
            try:
                return json.load(f)
            except:
                return {}

def save_settings(settings):
    with _lock:
        with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)

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

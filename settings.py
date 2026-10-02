import json
import os
from threading import Lock

SETTINGS_FILE = 'user_settings.json'
_lock = Lock()

def load_settings():
    """Завантажує налаштування з файлу."""
    if not os.path.exists(SETTINGS_FILE):
        return {}
    with _lock:
        with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)

def save_settings(settings):
    """Зберігає налаштування у файл."""
    with _lock:
        with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)

def get_user_settings(user_id):
    """Повертає налаштування для конкретного користувача."""
    settings = load_settings()
    return settings.get(str(user_id), {})

def update_user_settings(user_id, new_settings):
    """Оновлює налаштування для користувача."""
    settings = load_settings()
    user_key = str(user_id)
    if user_key not in settings:
        settings[user_key] = {}
    settings[user_key].update(new_settings)
    save_settings(settings)
    return settings[user_key]

# Значення за замовчуванням
DEFAULT_SETTINGS = {
    "language": "uk",          # Мова розшифровки
    "auto_translate": False,   # Автоматичний переклад
    "translate_to": "en"       # Мова перекладу за замовчуванням
}

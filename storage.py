"""Durable-сховище налаштувань через Upstash Redis REST API.

Render Free має ефемерний диск: `user_settings.json` зникає після кожного
рестарту/деплою. Upstash REST — це звичайний HTTPS, тому не потрібні нові
залежності й постійне з'єднання.

Активується змінними оточення (обидві мають бути задані):
    UPSTASH_REDIS_REST_URL
    UPSTASH_REDIS_REST_TOKEN

Якщо вони не задані — працює локальний файл (як раніше).
"""
import json
import os

import requests

_KEY = "voicer:settings"
_TIMEOUT = 8


def _creds():
    url = (os.getenv("UPSTASH_REDIS_REST_URL") or "").rstrip("/")
    token = os.getenv("UPSTASH_REDIS_REST_TOKEN") or ""
    return url, token


def enabled():
    url, token = _creds()
    return bool(url and token)


def name():
    return "upstash" if enabled() else "file"


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


def load():
    """Читає словник налаштувань із хмари.

    None — якщо сховище не налаштоване, порожнє або недоступне.
    """
    url, token = _creds()
    if not url or not token:
        return None
    try:
        r = requests.get(f"{url}/get/{_KEY}", headers=_headers(token), timeout=_TIMEOUT)
        r.raise_for_status()
        value = r.json().get("result")
        if not value:
            return None
        data = json.loads(value)
        return data if isinstance(data, dict) else None
    except Exception as e:
        print(f"⚠️ storage load: {e}")
        return None


def save(data):
    """Записує словник у хмару. True — якщо успішно."""
    url, token = _creds()
    if not url or not token:
        return False
    try:
        payload = json.dumps(data, ensure_ascii=False)
        r = requests.post(
            f"{url}/set/{_KEY}",
            headers=_headers(token),
            data=payload.encode("utf-8"),
            timeout=_TIMEOUT,
        )
        r.raise_for_status()
        return True
    except Exception as e:
        print(f"⚠️ storage save: {e}")
        return False

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

KEY = "voicer:settings"
TIMEOUT = 8


def _creds():
    url = (os.getenv("UPSTASH_REDIS_REST_URL") or "").rstrip("/")
    token = os.getenv("UPSTASH_REDIS_REST_TOKEN") or ""
    return url, token


def enabled():
    url, token = _creds()
    return bool(url and token)


def name():
    return "upstash" if enabled() else "file"


def masked_url():
    """URL для показу в панелі без токена (токен і так окремо)."""
    url, _ = _creds()
    if not url:
        return None
    if len(url) <= 42:
        return url
    return url[:30] + "…" + url[-10:]


def fetch():
    """Читає налаштування з хмари.

    Повертає dict:
        configured — чи задані env
        ok         — чи вдалося виконати запит
        data       — словник або None (ключа ще немає)
        error      — текст помилки або None
    """
    url, token = _creds()
    if not url or not token:
        return {
            "configured": False,
            "ok": False,
            "data": None,
            "error": "UPSTASH_REDIS_REST_URL / _TOKEN не задані",
        }
    try:
        r = requests.get(
            f"{url}/get/{KEY}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        value = r.json().get("result")
        if not value:
            return {"configured": True, "ok": True, "data": None, "error": None}
        data = json.loads(value)
        if not isinstance(data, dict):
            return {
                "configured": True,
                "ok": False,
                "data": None,
                "error": "значення не є JSON-об'єктом",
            }
        return {"configured": True, "ok": True, "data": data, "error": None}
    except Exception as e:
        return {"configured": True, "ok": False, "data": None, "error": str(e)[:200]}


def put(data):
    """Записує словник у хмару. Повертає (ok, error)."""
    url, token = _creds()
    if not url or not token:
        return False, "UPSTASH_REDIS_REST_URL / _TOKEN не задані"
    try:
        payload = json.dumps(data, ensure_ascii=False)
        r = requests.post(
            f"{url}/set/{KEY}",
            headers={"Authorization": f"Bearer {token}"},
            data=payload.encode("utf-8"),
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        return True, None
    except Exception as e:
        return False, str(e)[:200]

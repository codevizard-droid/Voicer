"""Веб-панель керування ботом: FastAPI замість голого http.server.

- GET /            → "OK" для UptimeRobot (як раніше)
- GET /app         → панель (index.html)
- GET /static/*    → стилі/скрипти панелі
- /api/*           → статус, налаштування, авточати, перезапуск

Авторизація: якщо заданий PANEL_TOKEN — усі шляхи крім "/" вимагають
?token=... або заголовка X-Panel-Token.
"""
import asyncio
import os
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import state
import settings as user_store
from settings import (
    get_user_settings,
    update_user_settings,
    add_auto_chat,
    remove_auto_chat,
)

WEBAPP_DIR = Path(__file__).parent / 'webapp'
PANEL_TOKEN = os.getenv('PANEL_TOKEN', '')

LANGUAGE_MODES = ('auto', 'uk', 'en', 'ru', 'pl', 'de', 'fr', 'es')
TARGET_LANGS = ('en', 'uk', 'ru', 'pl', 'de', 'fr', 'es')

app = FastAPI(title='Voicer panel')

# Колбек, який ставить app.py: перезапуск потоку бота з панелі
_bot_starter = None


def set_bot_starter(fn):
    global _bot_starter
    _bot_starter = fn


# ============================================
# АВТОРИЗАЦІЯ
# ============================================
@app.middleware('http')
async def token_auth(request: Request, call_next):
    if PANEL_TOKEN and request.url.path != '/':
        token = (
            request.query_params.get('token')
            or request.headers.get('x-panel-token')
        )
        if token != PANEL_TOKEN:
            return PlainTextResponse('Unauthorized', status_code=401)
    return await call_next(request)


# ============================================
# UPTIME + СТАТИКА
# ============================================
@app.get('/')
async def uptime():
    return PlainTextResponse('OK')


@app.get('/app')
async def panel():
    return FileResponse(WEBAPP_DIR / 'index.html')


app.mount('/static', StaticFiles(directory=WEBAPP_DIR), name='static')


# ============================================
# API: СТАТУС
# ============================================
@app.get('/api/status')
async def api_status():
    # Якщо минулий запис у хмару не вдався — пробуємо ще раз (панель опитує статус)
    if user_store.is_pending():
        await asyncio.to_thread(user_store.sync_pending)

    return {
        'status': state.get_status(),
        'name': state.get_name(),
        'uptime_seconds': state.get_uptime(),
        'owner_ready': state.get_owner_id() is not None,
        'storage': user_store.storage_name(),
        'storage_pending': user_store.is_pending(),
        'last_transcription': state.get_last_transcription(),
        'server_time': datetime.now().isoformat(timespec='seconds'),
    }


# ============================================
# API: НАЛАШТУВАННЯ
# ============================================
def _owner_id_or_409():
    owner = state.get_owner_id()
    if owner is None:
        raise HTTPException(
            status_code=409,
            detail='Бот ще не запущений — налаштування недоступні',
        )
    return owner


@app.get('/api/settings')
async def api_get_settings():
    owner = _owner_id_or_409()
    s = get_user_settings(owner)
    return {
        'language_mode': s.get('language_mode', 'auto'),
        'auto_translate': s.get('auto_translate', False),
        'translate_to': s.get('translate_to', 'en'),
        'emoji': s.get('emoji', True),
    }


class SettingsPatch(BaseModel):
    language_mode: str | None = None
    auto_translate: bool | None = None
    translate_to: str | None = None
    emoji: bool | None = None


@app.put('/api/settings')
async def api_put_settings(patch: SettingsPatch):
    owner = _owner_id_or_409()

    updates = {}
    if patch.language_mode is not None:
        if patch.language_mode not in LANGUAGE_MODES:
            raise HTTPException(400, f'Невірний language_mode: {patch.language_mode}')
        updates['language_mode'] = patch.language_mode
    if patch.translate_to is not None:
        if patch.translate_to not in TARGET_LANGS:
            raise HTTPException(400, f'Невірний translate_to: {patch.translate_to}')
        updates['translate_to'] = patch.translate_to
    if patch.auto_translate is not None:
        updates['auto_translate'] = patch.auto_translate
    if patch.emoji is not None:
        updates['emoji'] = patch.emoji

    if not updates:
        raise HTTPException(400, 'Порожній запит')

    updated = update_user_settings(owner, updates)
    return {
        'language_mode': updated.get('language_mode', 'auto'),
        'auto_translate': updated.get('auto_translate', False),
        'translate_to': updated.get('translate_to', 'en'),
        'emoji': updated.get('emoji', True),
    }


# ============================================
# API: АВТОЧАТИ
# ============================================
async def _chat_title(client, cid):
    """Назва чата через клієнт бота (в його потоці). Неблокуюче очікування."""
    loop = state.get_loop()
    if not (client and loop):
        return None
    try:
        fut = asyncio.run_coroutine_threadsafe(client.get_entity(cid), loop)
        ent = await asyncio.wrap_future(fut)
        return (
            getattr(ent, 'title', None)
            or getattr(ent, 'first_name', None)
            or getattr(ent, 'username', None)
        )
    except Exception:
        return None


@app.get('/api/chats')
async def api_get_chats():
    owner = _owner_id_or_409()
    ids = get_user_settings(owner).get('auto_chats', [])
    client = state.get_client()

    chats = []
    for cid in ids:
        title = await _chat_title(client, cid)
        chats.append({'chat_id': cid, 'title': title})
    return {'chats': chats}


class ChatAdd(BaseModel):
    chat_id: int


@app.post('/api/chats')
async def api_add_chat(item: ChatAdd):
    owner = _owner_id_or_409()
    added = add_auto_chat(owner, item.chat_id)
    return {'added': added, 'chat_id': item.chat_id}


@app.delete('/api/chats/{chat_id}')
async def api_remove_chat(chat_id: int):
    owner = _owner_id_or_409()
    removed = remove_auto_chat(owner, chat_id)
    return {'removed': removed, 'chat_id': chat_id}


# ============================================
# API: ПЕРЕЗАПУСК БОТА
# ============================================
@app.post('/api/restart')
def api_restart():
    """Синхронний ендпоінт (threadpool): тут безпечно блокуватися на join()."""
    client = state.get_client()
    loop = state.get_loop()
    thread = state.get_bot_thread()

    if not (client and loop and thread and thread.is_alive()):
        raise HTTPException(409, 'Бот не запущений — перезапуск неможливий')

    state.set_status('перезапуск...')
    try:
        fut = asyncio.run_coroutine_threadsafe(client.disconnect(), loop)
        fut.result(timeout=20)
    except Exception as e:
        state.set_status('❌ помилка перезапуску')
        raise HTTPException(500, f'Не вдалося зупинити бота: {e}')

    thread.join(timeout=20)

    if _bot_starter is None:
        raise HTTPException(500, 'Перезапускач бота не ініціалізований')

    _bot_starter()
    return {'restarted': True}

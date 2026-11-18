# Voicer

Telegram-юзербот (Telethon) для розшифровки голосових, кружечків і відео:
Groq Whisper → пунктуація/емодзі через Groq LLM → (опційно) переклад.
Керування — через веб-панель.

## Команди в чаті

- `.р` (реплай на голосове/кружечок/відео) — розшифрувати
- `.п` (реплай на текст) — перекласти
- `+chat` / `-chat` — додати/прибрати поточний чат з автотранскрипції
- `/panel` — посилання на панель керування

## Веб-панель

- `/` — `OK` (для UptimeRobot)
- `/app` — панель: налаштування, авточати, статус, перезапуск

## Змінні оточення

| Змінна | Обов'язкова | Призначення |
|---|---|---|
| `API_ID`, `API_HASH`, `SESSION_STRING` | так | Telethon-акаунт |
| `GROQ_API_KEY` | так | Whisper + LLM |
| `PORT` | ні | порт (типово 7860) |
| `PANEL_TOKEN` | ні | захист панелі; запит `?token=…` |
| `PANEL_URL` | ні | посилання в `/panel`; на Render береться `RENDER_EXTERNAL_URL` |
| `PANEL_OWNER_ID` | ні | зафіксувати власника налаштувань (типово — ID акаунта бота) |
| `UPSTASH_REDIS_REST_URL` | ні* | durable-сховище налаштувань |
| `UPSTASH_REDIS_REST_TOKEN` | ні* | те саме |

\* Без Upstash на Render налаштування живуть лише до рестарту/деплою:
файлова система там ефемерна.

## Upstash (безкоштовно)

1. https://console.upstash.com → **Redis** → *Create Database* (Free, регіон ближче до Render).
2. Скопіювати **UPSTASH_REDIS_REST_URL** і **UPSTASH_REDIS_REST_TOKEN**.
3. Додати їх у Render → *Environment*.
4. Перезапустити сервіс. У панелі картка «Сховище» має показати ☁️ Upstash.

Ключ: `voicer:settings`. У разі недоступності хмари зміни лишаються в пам'яті
й вивантажуються повторно (панель опитує статус кожні 10 с).

## Локальний запуск

```bash
uv venv .venv
uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/python app.py
```

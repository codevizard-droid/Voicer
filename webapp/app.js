/* Voice панель — клієнтська логіка */
'use strict';

const $ = (id) => document.getElementById(id);

/* ---------- API ---------- */
let token = localStorage.getItem('panel_token') || '';

async function api(path, opts = {}) {
  const url = new URL(path, location.origin);
  if (token) url.searchParams.set('token', token);
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  });
  if (res.status === 401) {
    showTokenOverlay('Невірний токен доступу');
    throw new Error('unauthorized');
  }
  if (!res.ok) {
    let detail = 'HTTP ' + res.status;
    try { detail = (await res.json()).detail || detail; } catch (e) {}
    throw new Error(detail);
  }
  return res.json();
}

/* ---------- Тост ---------- */
let toastTimer = null;
function toast(msg, isError = false) {
  const el = $('toast');
  el.textContent = msg;
  el.classList.toggle('error', isError);
  el.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('show'), 2600);
}

/* ---------- Токен ---------- */
function showTokenOverlay(errText = '') {
  $('token-overlay').classList.remove('hidden');
  $('token-error').textContent = errText;
  $('inp-token').focus();
}

$('btn-token').addEventListener('click', () => {
  token = $('inp-token').value.trim();
  if (!token) return;
  localStorage.setItem('panel_token', token);
  $('token-overlay').classList.add('hidden');
  refreshAll();
});

$('inp-token').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') $('btn-token').click();
});

/* ---------- Таби ---------- */
document.querySelectorAll('.tab').forEach((btn) => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach((b) => b.classList.remove('active'));
    document.querySelectorAll('.tab-page').forEach((p) => p.classList.remove('active'));
    btn.classList.add('active');
    $('tab-' + btn.dataset.tab).classList.add('active');
    if (btn.dataset.tab === 'settings') loadSettings().catch(showErr);
    if (btn.dataset.tab === 'chats') loadChats().catch(showErr);
  });
});

function showErr(e) {
  if (e && e.message && e.message !== 'unauthorized') toast(e.message, true);
}

/* ---------- Статус ---------- */
function fmtUptime(sec) {
  const d = Math.floor(sec / 86400);
  const h = Math.floor((sec % 86400) / 3600);
  const m = Math.floor((sec % 3600) / 60);
  if (d > 0) return `${d} д ${h} год`;
  if (h > 0) return `${h} год ${m} хв`;
  return `${m} хв`;
}

function setStatusPill(status) {
  const pill = $('status-pill');
  pill.textContent = status;
  pill.classList.remove('ok', 'warn', 'err');
  if (status.includes('працює')) pill.classList.add('ok');
  else if (status.includes('помилк') || status.includes('зупинено') || status.includes('✗')) pill.classList.add('err');
  else pill.classList.add('warn');
}

async function loadStatus() {
  const s = await api('/api/status');
  setStatusPill(s.status);
  $('bot-name').textContent = s.name ? `Бот: ${s.name}` : 'Бот не підключений';
  $('uptime').textContent = fmtUptime(s.uptime_seconds);
  renderStorage(s);
  renderKeepalive(s.keepalive);

  const lt = s.last_transcription;
  $('last-tr').innerHTML = lt
    ? `🕐 ${lt.time}<br>💬 Чат: <b>${escapeHtml(lt.chat || '—')}</b><br>🌐 Мова: <b>${escapeHtml(lt.lang || '—')}</b><br>✍️ Символів: <b>${lt.chars}</b>`
    : 'Ще не було транскрипцій після запуску сервера';
}

function renderStorage(s) {
  const el = $('storage');
  const info = s.storage_info || {};
  let html;

  if (s.storage === 'upstash') {
    html = '☁️ Upstash (хмара) — налаштування переживають рестарт';
    if (s.storage_pending) {
      html += '<br><span class="pill err">⚠️ є несинхронізовані зміни</span>';
    } else if (info.cloud_ok === false) {
      html += '<br><span class="pill err">⚠️ останній запит до хмари не вдався</span>';
    } else if (info.cloud_ok === true) {
      html += '<br><span class="pill ok">✅ синхронізовано</span>';
    }
    if (info.key) {
      html += `<div class="muted small-note">Ключ: ${escapeHtml(info.key)}</div>`;
    }
    if (info.error) {
      html += `<div class="muted small-note">Помилка: ${escapeHtml(info.error)}</div>`;
    }
  } else {
    html = '💾 Локальний файл — <b>зникне після рестарту Render</b>'
      + '<br><span class="muted small-note">Додай UPSTASH_REDIS_REST_URL і UPSTASH_REDIS_REST_TOKEN у змінні оточення.</span>';
  }

  const owner = s.owner_id ?? '—';
  const bot = s.bot_user_id ?? '—';
  html += `<div class="muted small-note">ID налаштувань: ${owner} · ID акаунта бота: ${bot}</div>`;
  if (s.owner_id && s.bot_user_id && s.owner_id !== s.bot_user_id) {
    html += '<br><span class="pill err">⚠️ PANEL_OWNER_ID не збігається з акаунтом бота — панель і бот бачать різні списки чатів</span>';
  }

  el.innerHTML = html;
}

function renderKeepalive(k) {
  const el = $('keepalive');
  if (!k) {
    el.textContent = '—';
    return;
  }
  if (!k.enabled) {
    el.innerHTML = `❌ Вимкнено — <span class="muted">${escapeHtml(k.reason || '')}</span>`;
    return;
  }
  let html = `🩺 Кожні ${Math.round(k.interval / 60)} хв → ${escapeHtml(k.url)}`;
  if (k.last_status) {
    html += `<div class="muted small-note">Останній пінг: ${escapeHtml(String(k.last_status))}</div>`;
  }
  el.innerHTML = html;
}

$('btn-storage-test').addEventListener('click', async () => {
  const btn = $('btn-storage-test');
  const old = btn.textContent;
  btn.disabled = true;
  btn.textContent = '⏳ Перевіряю...';
  try {
    const r = await api('/api/storage/test', { method: 'POST', body: '{}' });
    if (r.ok) {
      toast(`✅ Сховище працює (${r.chats_in_cloud.length} чатів у хмарі)`);
    } else {
      toast('❌ ' + (r.error || 'перевірка не пройдена'), true);
    }
    loadStatus().catch(showErr);
  } catch (e) {
    showErr(e);
  } finally {
    btn.disabled = false;
    btn.textContent = old;
  }
});

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

/* ---------- Налаштування ---------- */
const SETTINGS_FIELDS = ['sel-mode', 'sel-target', 'tg-translate', 'tg-emoji'];

async function loadSettings() {
  const s = await api('/api/settings');
  $('sel-mode').value = s.language_mode;
  $('sel-target').value = s.translate_to;
  $('tg-translate').checked = s.auto_translate;
  $('tg-emoji').checked = s.emoji;
  $('sel-target').disabled = !s.auto_translate;
}

async function saveSetting(patch) {
  try {
    const s = await api('/api/settings', { method: 'PUT', body: JSON.stringify(patch) });
    $('sel-mode').value = s.language_mode;
    $('sel-target').value = s.translate_to;
    $('tg-translate').checked = s.auto_translate;
    $('tg-emoji').checked = s.emoji;
    $('sel-target').disabled = !s.auto_translate;
    toast('✅ Збережено');
  } catch (e) { showErr(e); }
}

$('sel-mode').addEventListener('change', (e) => saveSetting({ language_mode: e.target.value }));
$('sel-target').addEventListener('change', (e) => saveSetting({ translate_to: e.target.value }));
$('tg-translate').addEventListener('change', (e) => saveSetting({ auto_translate: e.target.checked }));
$('tg-emoji').addEventListener('change', (e) => saveSetting({ emoji: e.target.checked }));

/* ---------- Авточати ---------- */
async function loadChats() {
  const data = await api('/api/chats');
  const list = $('chat-list');
  list.innerHTML = '';
  const chats = data.chats || [];
  $('chats-empty').style.display = chats.length ? 'none' : 'block';

  for (const c of chats) {
    const li = document.createElement('li');

    const info = document.createElement('div');
    const title = c.title ? escapeHtml(c.title) : '<span class="muted">назва недоступна</span>';
    info.innerHTML = `${title}<br><span class="chat-id">ID: ${c.chat_id}</span>`;

    const del = document.createElement('button');
    del.className = 'chat-del';
    del.textContent = '✕ Видалити';
    del.addEventListener('click', async () => {
      try {
        await api(`/api/chats/${c.chat_id}`, { method: 'DELETE' });
        toast('🗑 Чат видалено');
        loadChats().catch(showErr);
      } catch (e) { showErr(e); }
    });

    li.appendChild(info);
    li.appendChild(del);
    list.appendChild(li);
  }
}

$('btn-add-chat').addEventListener('click', async () => {
  const raw = $('inp-chat-id').value.trim();
  const id = parseInt(raw, 10);
  if (!raw || Number.isNaN(id)) {
    toast('Введи числовий ID чата', true);
    return;
  }
  try {
    const r = await api('/api/chats', { method: 'POST', body: JSON.stringify({ chat_id: id }) });
    toast(r.added ? '✅ Чат додано' : '⚠️ Чат уже в списку');
    $('inp-chat-id').value = '';
    loadChats().catch(showErr);
  } catch (e) { showErr(e); }
});

/* ---------- Перезапуск ---------- */
$('btn-restart').addEventListener('click', async () => {
  if (!confirm('Перезапустити бота? Активна розшифровка буде перервана.')) return;
  const btn = $('btn-restart');
  btn.disabled = true;
  btn.textContent = '⏳ Перезапуск...';
  try {
    await api('/api/restart', { method: 'POST', body: '{}' });
    toast('✅ Бот перезапускається');
    setTimeout(loadStatus().catch(showErr), 3000);
  } catch (e) { showErr(e); }
  finally {
    setTimeout(() => {
      btn.disabled = false;
      btn.textContent = '🔄 Перезапустити бота';
    }, 3000);
  }
});

/* ---------- Старт ---------- */
function refreshAll() {
  loadStatus().catch(showErr);
}

refreshAll();
setInterval(refreshAll, 10000);

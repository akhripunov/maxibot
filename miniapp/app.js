const $ = (s) => document.querySelector(s);
let all = [], channel = '', status = 'all', current = null;

function getInitData() {
  const bridgeData = window.WebApp?.initData;
  if (typeof bridgeData === 'string' && bridgeData) return new URLSearchParams(bridgeData).get('WebAppData') || bridgeData;
  const fragment = new URLSearchParams(location.hash.slice(1));
  return fragment.get('WebAppData') || new URLSearchParams(location.search).get('WebAppData') || '';
}
const initData = getInitData();
const escapeHtml = (value = '') => String(value).replace(/[&<>'"]/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));
const avatar = (url, fallback, className = '') => url ? `<img class="${className}" src="${encodeURI(url)}" alt="">` : fallback;
const api = (url, options = {}) => fetch(url, {...options, headers: {'X-MAX-Init-Data': initData, ...(options.headers || {})}});
const time = value => new Date(value).toLocaleTimeString('ru-RU', {hour:'2-digit', minute:'2-digit'});

async function loadChannels() {
  const response = await api('/api/channels'); if (!response.ok) return showApiError(response);
  const xs = await response.json(); const totalUnread = xs.reduce((sum, x) => sum + (x.unread || 0), 0);
  if (totalUnread) $('#channels .filter.active').insertAdjacentHTML('beforeend', `<span class="channel-badge">${totalUnread}</span>`);
  $('#channels').insertAdjacentHTML('beforeend', xs.map(x => `<button class="filter" data-channel="${x.id}"><span class="channel-avatar">${avatar(x.avatar_url, '📕')}</span>${x.unread ? `<span class="channel-badge">${x.unread}</span>` : ''}<span>${escapeHtml(x.title)}</span></button>`).join(''));
  document.querySelectorAll('.filter').forEach(button => button.onclick = () => { channel = button.dataset.channel; document.querySelectorAll('.filter').forEach(item => item.classList.remove('active')); button.classList.add('active'); load(); });
}
async function load() {
  const url = new URL('/api/dialogs', location); if (channel) url.searchParams.set('channel_id', channel); url.searchParams.set('status', status); url.searchParams.set('q', $('#search').value);
  const response = await api(url); if (!response.ok) return showApiError(response); all = await response.json(); render();
}
function showApiError(response) { $('#dialogs').innerHTML = response.status === 403 ? '<div class="empty">У этого MAX-пользователя нет доступа к CRM.</div>' : '<div class="empty">Не удалось подтвердить авторизацию MAX. Закройте CRM и откройте её снова кнопкой «Старт».</div>'; }
function render() {
  if (!all.length) { $('#dialogs').innerHTML = '<div class="empty">Нет диалогов</div>'; return; }
  $('#dialogs').innerHTML = all.map(item => `<article class="dialog" data-id="${item.id}"><div class="avatar">${avatar(item.avatar_url, '●')}</div><div class="dialog-main"><b>${escapeHtml(item.user_name)}${item.unread ? `<span class="badge">${item.unread}</span>` : ''}</b><div class="last">${escapeHtml(item.last_message)}</div><div class="source">${avatar(item.channel_avatar_url, '', 'source-avatar')}${escapeHtml(item.source)}</div></div><div class="time">${time(item.last_message_at)}</div></article>`).join('');
  document.querySelectorAll('.dialog').forEach(item => item.onclick = () => openDialog(item.dataset.id));
}
async function openDialog(id) {
  const response = await api('/api/dialogs/' + id); if (!response.ok) return showApiError(response); current = await response.json();
  $('#dialogs').hidden = true; $('#channels').hidden = true; $('.search').hidden = true; $('nav').hidden = true; $('#conversation').hidden = false; $('main').classList.add('conversation-open');
  $('#dialog-title').textContent = current.user_name; $('#dialog-source').textContent = current.source; $('#dialog-avatar').innerHTML = avatar(current.avatar_url, '●'); $('#block').classList.toggle('is-blocked', current.blocked); renderMessages(true);
}
function renderMessages(scrollToBottom = false) {
  const messages = $('#messages'); messages.innerHTML = current.messages.map(message => `<div class="bubble ${message.direction === 'out' ? 'out' : ''}">${message.image_url ? `<img class="message-image" src="${encodeURI(message.image_url)}" alt="Фото">` : ''}<div>${escapeHtml(message.text).replace(/\n/g, '<br>')}</div><small>${time(message.created_at)}</small></div>`).join('');
  if (scrollToBottom) requestAnimationFrame(() => { messages.scrollTop = messages.scrollHeight; });
}
function closeDialog() { $('#conversation').hidden = true; $('#dialogs').hidden = false; $('#channels').hidden = false; $('.search').hidden = false; $('nav').hidden = false; $('main').classList.remove('conversation-open'); load(); }
$('#back').onclick = closeDialog; $('#search').oninput = load; $('#theme').onclick = () => document.body.classList.toggle('light');
document.querySelectorAll('.tab').forEach(button => button.onclick = () => { status = button.dataset.status; document.querySelectorAll('.tab').forEach(item => item.classList.remove('active')); button.classList.add('active'); load(); });
$('#reply').onsubmit = async event => { event.preventDefault(); const input = event.currentTarget.querySelector('input'); const text = input.value.trim(); if (!text) return; const submit = event.currentTarget.querySelector('.send'); submit.disabled = true; try { const response = await api('/api/dialogs/' + current.id + '/messages', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({text})}); if (!response.ok) return; current.messages.push({...await response.json(), created_at: new Date().toISOString()}); input.value = ''; renderMessages(true); } finally { submit.disabled = false; input.focus(); } };
$('#block').onclick = async () => { const action = current.blocked ? 'unblock' : 'block'; const response = await api('/api/dialogs/' + current.id + '/' + action, {method:'POST'}); if (response.ok) { current.blocked = !current.blocked; $('#block').classList.toggle('is-blocked', current.blocked); } };
$('#pin').onclick = event => event.currentTarget.classList.toggle('is-active');
$('#tag').onclick = event => event.currentTarget.classList.toggle('is-active');
$('.attach').onclick = () => $('#image-input').click();
$('#image-input').onchange = async event => { const file = event.target.files?.[0]; if (!file || !current) return; const attach = $('.attach'); attach.disabled = true; try { const response = await api('/api/dialogs/' + current.id + '/image', {method:'POST', headers:{'Content-Type': file.type, 'X-Upload-Filename': encodeURIComponent(file.name)}, body:file}); if (!response.ok) return; current.messages.push(await response.json()); renderMessages(true); } finally { event.target.value = ''; attach.disabled = false; } };
loadChannels(); load();

(() => {
  const api = '/api/admin';
  const authBanner = document.querySelector('#auth-banner');
  const authMessage = document.querySelector('#auth-message');
  const lastUpdated = document.querySelector('#last-updated');
  const refreshButton = document.querySelector('#refresh');
  const logoutButton = document.querySelector('#logout');
  const authActions = document.querySelector('#auth-actions');
  const bootstrapToken = document.querySelector('#bootstrap-token');
  const registerButton = document.querySelector('#register-passkey');
  const loginButton = document.querySelector('#login-passkey');
  let authEpoch = 0;
  let lastAuthState = {enrolled: false, authenticated: false};
  const esc = value => String(value ?? '—');
  const valueOrUnknown = value => value === null || value === undefined ? 'N/A' : esc(value);

  const supersedeAuthTransition = () => ++authEpoch;
  const isCurrentAuthTransition = epoch => epoch === authEpoch;

  function renderAuthState({enrolled = false, authenticated = false} = {}) {
    lastAuthState = {enrolled: Boolean(enrolled), authenticated: Boolean(authenticated)};
    authBanner.hidden = lastAuthState.authenticated;
    authActions.hidden = lastAuthState.authenticated;
    bootstrapToken.hidden = lastAuthState.authenticated || lastAuthState.enrolled;
    registerButton.hidden = lastAuthState.authenticated || lastAuthState.enrolled;
    loginButton.hidden = lastAuthState.authenticated || !lastAuthState.enrolled;
    logoutButton.hidden = !lastAuthState.authenticated;
  }

  function resetLockedView() {
    ['total_clients', 'paying_clients', 'active_subscriptions', 'mrr'].forEach(key => setMetric(key, null));
    document.querySelectorAll('[data-window]').forEach(node => { node.textContent = 'N/A'; });
    document.querySelectorAll('#usage-list strong').forEach(node => { node.textContent = 'N/A'; });
    document.querySelectorAll('#health-list div').forEach(row => {
      const target = row.querySelector('strong');
      target.textContent = 'N/A';
      target.classList.remove('ok');
    });
    const pill = document.querySelector('#health-pill');
    pill.textContent = 'UNKNOWN';
    pill.className = 'state-pill unknown';
    lastUpdated.textContent = 'Данные не загружены';
  }

  function showAuthError(status) {
    resetLockedView();
    renderAuthState({enrolled: lastAuthState.enrolled, authenticated: false});
    authMessage.textContent = status === 403
      ? 'Недостаточно прав владельца для просмотра этой панели.'
      : 'Сервер не подтвердил доступ. Обратитесь к владельцу системы.';
  }

  async function getJson(path) {
    const response = await fetch(`${api}${path}`, {credentials: 'same-origin', headers: {Accept: 'application/json'}});
    if (response.status === 401 || response.status === 403) { showAuthError(response.status); throw new Error(`AUTH_${response.status}`); }
    if (!response.ok) throw new Error(`HTTP_${response.status}`);
    return response.json();
  }

  const base64urlToUint8Array = value => {
    const normalized = String(value).replace(/-/g, '+').replace(/_/g, '/');
    const padding = (4 - (normalized.length % 4)) % 4;
    const binary = atob(normalized + '='.repeat(padding));
    return Uint8Array.from(binary, char => char.charCodeAt(0));
  };
  const base64url = value => {
    const bytes = ArrayBuffer.isView(value)
      ? new Uint8Array(value.buffer, value.byteOffset, value.byteLength)
      : new Uint8Array(value);
    let text = '';
    bytes.forEach(byte => { text += String.fromCharCode(byte); });
    return btoa(text).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  };
  const publicKeyCredentialToJSON = credential => {
    const response = credential.response;
    const serialized = {id: credential.id, type: credential.type, rawId: base64url(credential.rawId), response: {}};
    if (response.clientDataJSON !== undefined) serialized.response.clientDataJSON = base64url(response.clientDataJSON);
    if (response.attestationObject !== undefined) serialized.response.attestationObject = base64url(response.attestationObject);
    if (response.authenticatorData !== undefined) serialized.response.authenticatorData = base64url(response.authenticatorData);
    if (response.signature !== undefined) serialized.response.signature = base64url(response.signature);
    if (response.userHandle !== undefined && response.userHandle !== null) serialized.response.userHandle = base64url(response.userHandle);
    const transports = typeof response.getTransports === 'function' ? response.getTransports() : response.transports;
    if (transports !== undefined) serialized.response.transports = transports;
    return serialized;
  };
  const creationOptions = options => ({...options, challenge: base64urlToUint8Array(options.challenge).buffer, user: {...options.user, id: base64urlToUint8Array(options.user.id).buffer}, excludeCredentials: (options.excludeCredentials || []).map(item => ({...item, id: base64urlToUint8Array(item.id).buffer}))});
  const requestOptions = options => ({...options, challenge: base64urlToUint8Array(options.challenge).buffer, allowCredentials: (options.allowCredentials || []).map(item => ({...item, id: base64urlToUint8Array(item.id).buffer}))});
  if (globalThis.__MAXIBOT_ADMIN_TEST__) Object.assign(globalThis.__MAXIBOT_ADMIN_TEST__, {base64urlToUint8Array, creationOptions, requestOptions, publicKeyCredentialToJSON});

  async function passkeyLogin() {
    const epoch = supersedeAuthTransition();
    const optionsResponse = await fetch(`${api}/auth/login/options`, {method: 'POST', credentials: 'same-origin', headers: {Accept: 'application/json'}});
    if (optionsResponse.status === 401 || optionsResponse.status === 403) { showAuthError(optionsResponse.status); throw new Error(`AUTH_${optionsResponse.status}`); }
    if (!optionsResponse.ok) throw new Error(`AUTH_${optionsResponse.status}`);
    const ceremony = await optionsResponse.json();
    if (!isCurrentAuthTransition(epoch)) return;
    const credential = await navigator.credentials.get({publicKey: requestOptions(ceremony.publicKey)});
    await fetch(`${api}/auth/login/verify`, {method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json', Accept: 'application/json'}, body: JSON.stringify({challenge_id: ceremony.challenge_id, credential: publicKeyCredentialToJSON(credential)})}).then(response => {
      if (response.status === 401 || response.status === 403) showAuthError(response.status);
      if (!response.ok) throw new Error(`AUTH_${response.status}`);
      return response.json();
    });
    if (!isCurrentAuthTransition(epoch)) return;
    renderAuthState({enrolled: true, authenticated: true});
    await loadOverview(epoch);
    if (!isCurrentAuthTransition(epoch)) return;
    renderAuthState({enrolled: true, authenticated: true});
  }

  async function passkeyRegister() {
    const epoch = supersedeAuthTransition();
    const token = bootstrapToken.value.trim();
    if (!token) throw new Error('BOOTSTRAP_REQUIRED');
    const response = await fetch(`${api}/auth/register/options`, {method: 'POST', credentials: 'same-origin', headers: {'X-WebAdmin-Bootstrap': token, Accept: 'application/json'}});
    if (!response.ok) throw new Error(`AUTH_${response.status}`);
    const ceremony = await response.json();
    if (!isCurrentAuthTransition(epoch)) return;
    const credential = await navigator.credentials.create({publicKey: creationOptions(ceremony.publicKey)});
    const verify = await fetch(`${api}/auth/register/verify`, {method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json', Accept: 'application/json'}, body: JSON.stringify({challenge_id: ceremony.challenge_id, credential: publicKeyCredentialToJSON(credential)})});
    if (!verify.ok) throw new Error(`AUTH_${verify.status}`);
    if (!isCurrentAuthTransition(epoch)) return;
    bootstrapToken.value = ''; await passkeyLogin();
  }

  async function checkAuth() {
    const epoch = authEpoch;
    const status = await getJson('/auth/status');
    if (!isCurrentAuthTransition(epoch)) return;
    if (status.authenticated) {
      renderAuthState({enrolled: true, authenticated: true});
      await loadOverview(epoch);
      if (!isCurrentAuthTransition(epoch)) return;
      renderAuthState({enrolled: true, authenticated: true});
      return;
    }
    resetLockedView();
    authMessage.textContent = status.enrolled ? 'Подтвердите доступ сохранённым Passkey.' : 'Для первого владельца нужен одноразовый bootstrap-код.';
    renderAuthState({enrolled: status.enrolled, authenticated: false});
  }

  async function logout() {
    const epoch = supersedeAuthTransition();
    const response = await fetch(`${api}/auth/logout`, {method: 'POST', credentials: 'same-origin', headers: {Accept: 'application/json'}});
    if (!response.ok) throw new Error(`AUTH_${response.status}`);
    if (!isCurrentAuthTransition(epoch)) return;
    resetLockedView();
    renderAuthState({enrolled: true, authenticated: false});
    authMessage.textContent = 'Сессия завершена. Для входа подтвердите Passkey.';
  }

  function setMetric(name, value) {
    document.querySelectorAll(`[data-metric="${name}"]`).forEach(node => { node.textContent = valueOrUnknown(value); });
  }

  async function loadOverview(epoch = null) {
    try {
      const [overview, week, today, usage, health] = await Promise.all([
        getJson('/overview?window_days=30'), getJson('/overview?window_days=7'),
        getJson('/overview?window_days=1'), getJson('/product-usage'), getJson('/system-health'),
      ]);
      if (epoch !== null && !isCurrentAuthTransition(epoch)) return;
      const metrics = overview.metrics || {};
      ['total_clients', 'paying_clients', 'active_subscriptions', 'mrr'].forEach(key => setMetric(key, metrics[key]));
      const windows = {1: today, 7: week, 30: overview};
      document.querySelectorAll('[data-window]').forEach(node => {
        const report = windows[node.dataset.window];
        node.textContent = valueOrUnknown(report?.metrics?.[node.dataset.field]);
      });
      const counts = usage.counts || {};
      const usageMap = {channels: 'Каналы', bots: 'Боты', conversations: 'Диалоги', ai_runs: 'AI-запуски'};
      document.querySelectorAll('#usage-list div').forEach(row => {
        const label = row.querySelector('span')?.textContent;
        const key = Object.keys(usageMap).find(candidate => usageMap[candidate] === label);
        if (key) row.querySelector('strong').textContent = valueOrUnknown(counts[key]);
      });
      const healthMap = {API: health.api?.status, 'База данных': health.database?.status, MAX: health.max?.mode};
      document.querySelectorAll('#health-list div').forEach(row => {
        const label = row.querySelector('span')?.textContent;
        const value = healthMap[label];
        const target = row.querySelector('strong');
        target.textContent = valueOrUnknown(value);
        target.classList.remove('ok');
        if (value === 'ready' || value === 'real') target.classList.add('ok');
      });
      const pill = document.querySelector('#health-pill');
      pill.textContent = health.api?.status === 'ready' && health.database?.status === 'ready' ? 'READY' : 'UNKNOWN';
      pill.className = `state-pill ${pill.textContent === 'READY' ? 'ready' : 'unknown'}`;
      lastUpdated.textContent = `Обновлено ${new Date().toLocaleTimeString('ru-RU', {hour:'2-digit', minute:'2-digit'})}`;
    } catch (error) {
      if (epoch === null || isCurrentAuthTransition(epoch)) resetLockedView();
      throw error;
    }
  }

  async function refresh() {
    refreshButton.disabled = true; refreshButton.textContent = 'Обновляем…';
    try { await loadOverview(); } catch (error) { if (!String(error.message).startsWith('AUTH_')) { resetLockedView(); renderAuthState(lastAuthState); authMessage.textContent = 'Данные временно недоступны. Попробуйте обновить страницу.'; } } finally { refreshButton.disabled = false; refreshButton.textContent = 'Обновить'; }
  }

  if (globalThis.__MAXIBOT_ADMIN_TEST__) Object.assign(globalThis.__MAXIBOT_ADMIN_TEST__, {resetLockedView, renderAuthState, checkAuth, loadOverview, logout, passkeyLogin});

  document.querySelectorAll('.side-link').forEach(link => link.addEventListener('click', () => {
    document.querySelectorAll('.side-link').forEach(item => item.classList.toggle('active', item === link));
    document.querySelectorAll('.page-section').forEach(section => section.classList.toggle('active-section', section.id === link.dataset.section));
  }));
  refreshButton.addEventListener('click', refresh);
  logoutButton.addEventListener('click', () => logout().catch(error => { resetLockedView(); renderAuthState({enrolled: lastAuthState.enrolled, authenticated: false}); authMessage.textContent = `Выход не выполнен: ${error.message}`; }));
  loginButton.addEventListener('click', () => passkeyLogin().catch(error => { resetLockedView(); renderAuthState({enrolled: true, authenticated: false}); authMessage.textContent = `Вход не выполнен: ${error.message}`; }));
  registerButton.addEventListener('click', () => passkeyRegister().catch(error => { resetLockedView(); renderAuthState({enrolled: false, authenticated: false}); authMessage.textContent = `Регистрация не выполнена: ${error.message}`; }));
  if (!globalThis.__MAXIBOT_ADMIN_TEST__?.skipAutoAuth) checkAuth().catch(error => { resetLockedView(); renderAuthState({enrolled: false, authenticated: false}); authMessage.textContent = `Сервер авторизации недоступен: ${error.message}`; });
})();

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
      await loadQA(epoch).catch(() => {});
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

  const qaValue = value => value === null || value === undefined || value === '' ? 'UNKNOWN' : esc(value);
  const qaNode = id => document.querySelector(`#${id}`);
  function renderQA(data = {}) {
    const summary = data.current_summary || data.summary || {};
    const counts = data.headline_counts || data.counts || {};
    const verdict = String(summary.verdict || data.verdict || data.qa_status || 'UNKNOWN').toUpperCase();
    qaNode('qa-verdict').textContent = verdict;
    qaNode('qa-verdict').className = `state-pill ${verdict === 'PASS' ? 'ready' : 'unknown'}`;
    qaNode('qa-summary-title').textContent = qaValue(summary.title || summary.label || (verdict === 'PASS' ? 'Проверка пройдена' : 'Проверка не подтверждена'));
    qaNode('qa-summary-reason').textContent = qaValue(summary.reason || data.reason);
    qaNode('qa-tested-sha').textContent = qaValue(summary.tested_sha || data.tested_sha);
    qaNode('qa-main-sha').textContent = qaValue(summary.current_main_sha || summary.main_sha || data.current_main_sha);
    qaNode('qa-production-sha').textContent = qaValue(summary.production_sha || data.production_sha);
    qaNode('qa-checked-at').textContent = qaValue(summary.checked_at || data.checked_at);
    const parity = String(summary.parity || data.parity || 'UNKNOWN').toUpperCase();
    qaNode('qa-parity').textContent = `СОВПАДЕНИЕ: ${parity}`;
    qaNode('qa-parity').className = `qa-parity state-pill ${parity === 'MATCH' ? 'ready' : 'unknown'}`;
    ['total', 'passed', 'failed', 'skipped'].forEach(key => { qaNode(`qa-count-${key}`).textContent = qaValue(counts[key]); });
    const renderList = (id, values, empty) => {
      const node = qaNode(id); node.textContent = '';
      if (!Array.isArray(values) || !values.length) {
        const row = document.createElement('div'); row.className = 'qa-empty'; row.textContent = empty; node.appendChild(row); return;
      }
      values.forEach(item => {
        const row = document.createElement('div');
        if (typeof item === 'string') row.textContent = item;
        else {
          const title = document.createElement('strong'); title.textContent = qaValue(item.name || item.title || item.test || item.run_id);
          const detail = document.createElement('small'); detail.textContent = qaValue(item.status || item.reason || item.sha || item.finished_at);
          row.append(title, detail);
        }
        node.appendChild(row);
      });
    };
    renderList('qa-suite-matrix', data.suite_matrix || data.suites, 'UNVERIFIED');
    renderList('qa-failures', data.failures || data.blockers, 'UNVERIFIED');
    renderList('qa-unverified', data.unverified || data.not_run, 'UNVERIFIED');
    renderList('qa-recent-runs', data.recent_runs || data.runs, 'NOT RUN');
    qaNode('qa-trend').textContent = Array.isArray(data.trend) && data.trend.length ? `Тренд: ${data.trend.map(qaValue).join(' → ')}` : 'Тренд недоступен без истории запусков.';
  }

  async function loadQA(epoch = null) {
    try {
      const data = await getJson('/qa-release');
      if (epoch !== null && !isCurrentAuthTransition(epoch)) return;
      renderQA(data);
    } catch (error) {
      if (epoch === null || isCurrentAuthTransition(epoch)) renderQA();
      throw error;
    }
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

  const journeyStatusClass = value => String(value || 'UNKNOWN').toLowerCase();
  const journeyText = value => value === null || value === undefined ? 'N/A' : esc(value);
  const journeyYesNo = value => value === 'UNKNOWN' ? 'UNKNOWN' : value ? 'YES' : 'NO';
  function renderJourney(data = {}) {
    const summary = data.summary || {};
    const summaryNode = document.querySelector('#journey-summary');
    const cards = [['Каналы', summary.channels], ['Боты', summary.bots], ['Активные journey', summary.active_journeys], ['Проблемные объекты', summary.problem_objects], ['Общий статус', summary.overall_status]];
    summaryNode.innerHTML = cards.map(([label, value], index) => `<article class="metric-card ${index === 4 ? 'accent' : ''}"><span>${label}</span><strong>${journeyText(value)}</strong><small>${index === 4 ? 'по наблюдаемым данным' : 'за выбранный период'}</small></article>`).join('');
    const stageHtml = stages => (stages || []).map(stage => `<div class="journey-stage"><small>${journeyText(stage.name)}</small><strong>${journeyText(stage.count)}</strong><span class="journey-stage-status ${journeyStatusClass(stage.status)}">${journeyText(stage.status)}${stage.conversion_from_previous_percent === null || stage.conversion_from_previous_percent === undefined ? '' : ` · ${stage.conversion_from_previous_percent}%`}</span></div>`).join('');
    document.querySelector('#journey-normalized').innerHTML = stageHtml(data.normalized_funnel) || '<div class="journey-empty">UNKNOWN</div>';
    const problems = data.problem_objects || [];
    document.querySelector('#journey-problems').innerHTML = problems.length ? problems.map(item => `<div class="journey-problem"><div><strong>${journeyText(item.name)}</strong><small>${item.type === 'bot' ? 'Бот' : 'Канал'} · ${journeyText(item.reason)}</small></div><span class="journey-status ${journeyStatusClass(item.status)}">${journeyText(item.status)}</span></div>`).join('') : '<div class="journey-empty">Проблем не обнаружено или недостаточно данных</div>';
    const objects = data.objects || [];
    document.querySelector('#journey-objects').innerHTML = objects.length ? objects.map(item => `<div class="journey-object"><div class="journey-object-main"><strong>${journeyText(item.name)}</strong><small>${item.type === 'bot' ? 'Бот' : 'Канал'} · ${journeyText(item.reason || 'Каноническая воронка')}</small><div class="journey-object-stages">${(item.stages || []).map(stage => `<span>${journeyText(stage.name)}: ${journeyText(stage.count)}</span>`).join('')}</div></div><span class="journey-status ${journeyStatusClass(item.status)}">${journeyText(item.status)}</span></div>`).join('') : '<div class="journey-empty">Нет данных за выбранный период</div>';
    const journeys = data.recent_journeys || [];
    document.querySelector('#journey-recent').innerHTML = journeys.length ? journeys.map(item => `<tr><td>${journeyText(new Date(item.time).toLocaleString('ru-RU'))}</td><td>${journeyText(item.object)}<br><span class="muted">${journeyText(item.type)}</span></td><td>${journeyText(item.source)}</td><td>${journeyYesNo(item.yclid_present)}</td><td>${journeyYesNo(item.identity_resolved)}</td><td>${journeyYesNo(item.destination_reached)}</td><td>${journeyYesNo(item.product_conversion)}</td><td>${journeyYesNo(item.metrica)}</td><td><span class="journey-status ${journeyStatusClass(item.status)}">${journeyText(item.status)}</span></td></tr>`).join('') : '<tr><td colspan="9" class="muted">Нет данных</td></tr>';
  }
  async function loadJourney() {
    const params = new URLSearchParams({days: document.querySelector('#journey-days').value, object_type: document.querySelector('#journey-type').value, problem: document.querySelector('#journey-problem').value, search: document.querySelector('#journey-search').value});
    try { renderJourney(await getJson(`/journey?${params}`)); } catch (error) { if (!String(error.message).startsWith('AUTH_')) document.querySelector('#journey-objects').innerHTML = '<div class="journey-empty">Данные временно недоступны</div>'; }
  }

  if (globalThis.__MAXIBOT_ADMIN_TEST__) Object.assign(globalThis.__MAXIBOT_ADMIN_TEST__, {resetLockedView, renderAuthState, renderQA, loadQA, checkAuth, loadOverview, logout, passkeyLogin});

  document.querySelectorAll('.side-link').forEach(link => link.addEventListener('click', () => {
    document.querySelectorAll('.side-link').forEach(item => item.classList.toggle('active', item === link));
    document.querySelectorAll('.page-section').forEach(section => section.classList.toggle('active-section', section.id === link.dataset.section));
    if (link.dataset.section === 'journey' && lastAuthState.authenticated) loadJourney();
  }));
  ['journey-type', 'journey-problem', 'journey-days'].forEach(id => document.querySelector(`#${id}`)?.addEventListener('change', loadJourney));
  document.querySelector('#journey-search')?.addEventListener('input', () => { clearTimeout(globalThis.__journeySearchTimer); globalThis.__journeySearchTimer = setTimeout(loadJourney, 250); });
  document.querySelector('#journey-refresh')?.addEventListener('click', loadJourney);
  refreshButton.addEventListener('click', refresh);
  logoutButton.addEventListener('click', () => logout().catch(error => { resetLockedView(); renderAuthState({enrolled: lastAuthState.enrolled, authenticated: false}); authMessage.textContent = `Выход не выполнен: ${error.message}`; }));
  loginButton.addEventListener('click', () => passkeyLogin().catch(error => { resetLockedView(); renderAuthState({enrolled: true, authenticated: false}); authMessage.textContent = `Вход не выполнен: ${error.message}`; }));
  registerButton.addEventListener('click', () => passkeyRegister().catch(error => { resetLockedView(); renderAuthState({enrolled: false, authenticated: false}); authMessage.textContent = `Регистрация не выполнена: ${error.message}`; }));
  if (!globalThis.__MAXIBOT_ADMIN_TEST__?.skipAutoAuth) checkAuth().catch(error => { resetLockedView(); renderAuthState({enrolled: false, authenticated: false}); authMessage.textContent = `Сервер авторизации недоступен: ${error.message}`; });
})();

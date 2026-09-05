import json
import subprocess
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _run_js(expression: str) -> dict:
    source = (ROOT / "app/static/admin/admin.js").read_text(encoding="utf-8")
    harness = f"""
const fs = require('fs');
const vm = require('vm');
const elements = () => ({{hidden:false, textContent:'', value:'', addEventListener(){{}}, classList:{{add(){{}}}}}});
const document = {{querySelector: elements, querySelectorAll: () => [], addEventListener(){{}}}};
const atob = value => Buffer.from(value, 'base64').toString('binary');
const btoa = value => Buffer.from(value, 'binary').toString('base64');
const context = {{window:null, document, fetch: async () => ({{status:401, ok:false}}), console, setTimeout, clearTimeout, atob, btoa, __MAXIBOT_ADMIN_TEST__:{{}}}};
context.window = context;
vm.runInNewContext(fs.readFileSync({json.dumps(str(ROOT / 'app/static/admin/admin.js'))}, 'utf8'), context);
const test = context.__MAXIBOT_ADMIN_TEST__;
{expression}
"""
    result = subprocess.run(["node", "-e", harness], check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def _run_admin_behavior(expression: str) -> dict:
    source = (ROOT / "app/static/admin/admin.js").read_text(encoding="utf-8")
    harness = f"""
const fs = require('fs');
const vm = require('vm');
const metrics = ['total_clients', 'paying_clients', 'active_subscriptions', 'mrr'].map(name => {{
  const node = {{textContent:'', dataset:{{metric:name}}, classList:{{add(){{}}, remove(){{}}}}, addEventListener(){{}}}};
  return node;
}});
const windows = [1, 7, 30].map(window => ({{textContent:'', dataset:{{window:String(window), field:'new_clients'}}, addEventListener(){{}}}}));
const usage = ['Каналы', 'Боты', 'Диалоги', 'AI-запуски'].map(label => {{
  const strong = {{textContent:'', classList:{{add(){{}}, remove(){{}}}}}};
  return {{querySelector(selector){{ return selector === 'span' ? {{textContent:label}} : strong; }}, addEventListener(){{}}}};
}});
const health = ['API', 'База данных', 'MAX'].map(label => {{
  const strong = {{textContent:'', classList:{{add(){{}}, remove(){{}}}}}};
  return {{querySelector(selector){{ return selector === 'span' ? {{textContent:label}} : strong; }}, addEventListener(){{}}}};
}});
const elements = {{
  '#auth-banner': {{hidden:true, textContent:'', addEventListener(){{}}}},
  '#auth-message': {{hidden:false, textContent:'', addEventListener(){{}}}},
  '#last-updated': {{hidden:false, textContent:'', addEventListener(){{}}}},
  '#refresh': {{hidden:false, textContent:'', disabled:false, addEventListener(){{}}}},
  '#logout': {{hidden:true, textContent:'', addEventListener(){{}}}},
  '#auth-actions': {{hidden:true, textContent:'', addEventListener(){{}}}},
  '#bootstrap-token': {{hidden:true, value:'', addEventListener(){{}}}},
  '#register-passkey': {{hidden:true, addEventListener(){{}}}},
  '#login-passkey': {{hidden:true, addEventListener(){{}}}},
  '#health-pill': {{textContent:'', className:'', addEventListener(){{}}}},
}};
const document = {{
  querySelector(selector) {{
    if (selector.startsWith('[data-metric="')) return metrics.find(node => node.dataset.metric === selector.slice(14, -2));
    return elements[selector] || {{hidden:false, textContent:'', classList:{{add(){{}}, remove(){{}}}}, addEventListener(){{}}}};
  }},
  querySelectorAll(selector) {{
    if (selector === '[data-window]') return windows;
    if (selector.startsWith('[data-metric="')) return metrics.filter(node => node.dataset.metric === selector.slice(14, -2));
    if (selector === '#usage-list div') return usage;
    if (selector === '#usage-list strong') return usage.map(row => row.querySelector('strong'));
    if (selector === '#health-list div') return health;
    if (selector === '[data-metric]') return metrics;
    if (selector === '.side-link' || selector === '.page-section') return [];
    return [];
  }},
}};
const json = value => ({{status:200, ok:true, json:async () => value}});
let fetchImpl = async () => ({{status:401, ok:false}});
const fetch = (...args) => fetchImpl(...args);
const context = {{window:null, document, fetch, console, setTimeout, clearTimeout, atob:value => Buffer.from(value, 'base64').toString('binary'), btoa:value => Buffer.from(value, 'binary').toString('base64'), navigator:{{credentials:{{get:async () => ({{id:'login', type:'public-key', rawId:new Uint8Array([1]).buffer, response:{{clientDataJSON:new Uint8Array([1]).buffer, authenticatorData:new Uint8Array([2]).buffer, signature:new Uint8Array([3]).buffer}}}})}}}}, __MAXIBOT_ADMIN_TEST__:{{skipAutoAuth:true}}}};
context.window = context;
vm.runInNewContext(fs.readFileSync({json.dumps(str(ROOT / 'app/static/admin/admin.js'))}, 'utf8'), context);
const test = context.__MAXIBOT_ADMIN_TEST__;
  const state = {{elements, metrics, windows, usage, health}};
(async () => {{
{textwrap.indent(expression, '  ') }
}})().catch(error => {{ console.error(error); process.exit(1); }});
"""
    try:
        result = subprocess.run(["node", "-e", textwrap.dedent(harness)], check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as error:
        raise AssertionError(error.stderr) from error
    return json.loads(result.stdout)


def test_base64url_codec_handles_unpadded_urlsafe_edge_bytes():
    result = _run_js("console.log(JSON.stringify(Array.from(test.base64urlToUint8Array('-_8'))));")
    assert result == [251, 255]


def test_registration_and_login_options_convert_all_binary_fields():
    expression = """
const creation = test.creationOptions({challenge:'-_8', user:{id:'AAE', name:'owner'}, excludeCredentials:[{id:'_w', type:'public-key'}]});
const request = test.requestOptions({challenge:'-_8', allowCredentials:[{id:'AAE', type:'public-key'}]});
const bytes = value => Array.from(new Uint8Array(value));
console.log(JSON.stringify({creation:[bytes(creation.challenge), bytes(creation.user.id), bytes(creation.excludeCredentials[0].id)], request:[bytes(request.challenge), bytes(request.allowCredentials[0].id)]}));
"""
    result = _run_js(expression)
    assert result == {"creation": [[251, 255], [0, 1], [255]], "request": [[251, 255], [0, 1]]}


def test_credential_serialization_reads_non_enumerable_webidl_fields():
    expression = """
const nonEnumerable = (object, values) => Object.defineProperties(object, Object.fromEntries(Object.entries(values).map(([key, value]) => [key, {value, enumerable:false}])));
const registration = nonEnumerable({response: nonEnumerable({}, {clientDataJSON:new Uint8Array([0,1]).buffer, attestationObject:new Uint8Array([251,255]).buffer, getTransports:() => ['internal']})}, {id:'reg-1', type:'public-key', rawId:new Uint8Array([255]).buffer});
const assertion = nonEnumerable({response: nonEnumerable({}, {clientDataJSON:new Uint8Array([0,1]).buffer, authenticatorData:new Uint8Array([251,255]).buffer, signature:new Uint8Array([255]).buffer, userHandle:new Uint8Array([0]).buffer, getTransports:() => ['hybrid']})}, {id:'login-1', type:'public-key', rawId:new Uint8Array([0,1]).buffer});
console.log(JSON.stringify({registration:test.publicKeyCredentialToJSON(registration), assertion:test.publicKeyCredentialToJSON(assertion)}));
"""
    result = _run_js(expression)
    assert result == {
        "registration": {"id": "reg-1", "type": "public-key", "rawId": "_w", "response": {"clientDataJSON": "AAE", "attestationObject": "-_8", "transports": ["internal"]}},
        "assertion": {"id": "login-1", "type": "public-key", "rawId": "AAE", "response": {"clientDataJSON": "AAE", "authenticatorData": "-_8", "signature": "_w", "userHandle": "AA", "transports": ["hybrid"]}},
    }


def test_unauthenticated_status_clears_protected_dom_and_keeps_unknown_state():
    expression = """
state.metrics.forEach(node => node.textContent = '123');
state.windows.forEach(node => node.textContent = '7');
state.usage.forEach(row => row.querySelector('strong').textContent = '9');
state.health.forEach(row => row.querySelector('strong').textContent = 'ready');
state.elements['#health-pill'].textContent = 'READY';
state.elements['#last-updated'].textContent = 'Обновлено';
fetchImpl = async () => json({authenticated:false, enrolled:true});
await new Promise(resolve => setTimeout(resolve, 0));
await test.checkAuth();
console.log(JSON.stringify({metrics:state.metrics.map(node => node.textContent), windows:state.windows.map(node => node.textContent), usage:state.usage.map(row => row.querySelector('strong').textContent), health:state.health.map(row => row.querySelector('strong').textContent), pill:state.elements['#health-pill'].textContent, updated:state.elements['#last-updated'].textContent}));
"""
    result = _run_admin_behavior(expression)
    assert result == {"metrics": ["N/A"] * 4, "windows": ["N/A"] * 3, "usage": ["N/A"] * 4, "health": ["N/A"] * 3, "pill": "UNKNOWN", "updated": "Данные не загружены"}


def test_not_enrolled_shows_registration_only():
    expression = """
fetchImpl = async () => json({authenticated:false, enrolled:false});
await new Promise(resolve => setTimeout(resolve, 0));
await test.checkAuth();
console.log(JSON.stringify({banner:state.elements['#auth-banner'].hidden, actions:state.elements['#auth-actions'].hidden, bootstrap:state.elements['#bootstrap-token'].hidden, register:state.elements['#register-passkey'].hidden, login:state.elements['#login-passkey'].hidden, logout:state.elements['#logout'].hidden}));
"""
    result = _run_admin_behavior(expression)
    assert result == {"banner": False, "actions": False, "bootstrap": False, "register": False, "login": True, "logout": True}


def test_enrolled_logged_out_shows_login_only():
    expression = """
fetchImpl = async () => json({authenticated:false, enrolled:true});
await new Promise(resolve => setTimeout(resolve, 0));
await test.checkAuth();
console.log(JSON.stringify({banner:state.elements['#auth-banner'].hidden, actions:state.elements['#auth-actions'].hidden, bootstrap:state.elements['#bootstrap-token'].hidden, register:state.elements['#register-passkey'].hidden, login:state.elements['#login-passkey'].hidden, logout:state.elements['#logout'].hidden}));
"""
    result = _run_admin_behavior(expression)
    assert result == {"banner": False, "actions": False, "bootstrap": True, "register": True, "login": False, "logout": True}


def test_authenticated_state_shows_dashboard_and_logout_only():
    expression = """
fetchImpl = async (request) => {
  const path = request;
  if (path.includes('/auth/status')) return json({authenticated:true, enrolled:true});
  if (path.includes('/overview?window_days=30')) return json({metrics:{total_clients:4, paying_clients:2, active_subscriptions:2, mrr:null}});
  if (path.includes('/overview?window_days=7') || path.includes('/overview?window_days=1')) return json({metrics:{new_clients:1}});
  if (path.includes('/product-usage')) return json({counts:{}});
  if (path.includes('/system-health')) return json({api:{status:'ready'}, database:{status:'ready'}, max:{mode:'real'}});
  return json({ok:true});
};
await new Promise(resolve => setTimeout(resolve, 0));
await test.checkAuth();
console.log(JSON.stringify({banner:state.elements['#auth-banner'].hidden, actions:state.elements['#auth-actions'].hidden, bootstrap:state.elements['#bootstrap-token'].hidden, register:state.elements['#register-passkey'].hidden, login:state.elements['#login-passkey'].hidden, logout:state.elements['#logout'].hidden, metric:state.metrics[0].textContent}));
"""
    result = _run_admin_behavior(expression)
    assert result == {"banner": True, "actions": True, "bootstrap": True, "register": True, "login": True, "logout": False, "metric": "4"}


def test_protected_api_401_clears_stale_dom():
    expression = """
state.metrics.forEach(node => node.textContent = '123');
state.elements['#health-pill'].textContent = 'READY';
fetchImpl = async () => ({status:401, ok:false});
await new Promise(resolve => setTimeout(resolve, 0));
await test.loadOverview().catch(() => {});
console.log(JSON.stringify({metrics:state.metrics.map(node => node.textContent), pill:state.elements['#health-pill'].textContent, updated:state.elements['#last-updated'].textContent}));
"""
    result = _run_admin_behavior(expression)
    assert result == {"metrics": ["N/A"] * 4, "pill": "UNKNOWN", "updated": "Данные не загружены"}


def test_successful_logout_clears_protected_dom():
    expression = """
state.metrics.forEach(node => node.textContent = '123');
state.elements['#health-pill'].textContent = 'READY';
fetchImpl = async () => json({ok:true});
await new Promise(resolve => setTimeout(resolve, 0));
await test.logout();
console.log(JSON.stringify({metrics:state.metrics.map(node => node.textContent), pill:state.elements['#health-pill'].textContent, banner:state.elements['#auth-banner'].hidden, actions:state.elements['#auth-actions'].hidden, bootstrap:state.elements['#bootstrap-token'].hidden, register:state.elements['#register-passkey'].hidden, login:state.elements['#login-passkey'].hidden, logout:state.elements['#logout'].hidden, message:state.elements['#auth-message'].textContent}));
"""
    result = _run_admin_behavior(expression)
    assert result == {"metrics": ["N/A"] * 4, "pill": "UNKNOWN", "banner": False, "actions": False, "bootstrap": True, "register": True, "login": False, "logout": True, "message": "Сессия завершена. Для входа подтвердите Passkey."}


def test_successful_login_loads_overview_before_unlocking_banner():
    expression = """
fetchImpl = async (request) => {
  const path = request;
  if (path.includes('/auth/login/options')) return json({challenge_id:'c1', publicKey:{challenge:'AA', allowCredentials:[]}});
  if (path.includes('/auth/login/verify')) return json({authenticated:true});
  if (path.includes('/overview?window_days=30')) return json({metrics:{total_clients:42}});
  if (path.includes('/overview?window_days=7') || path.includes('/overview?window_days=1')) return json({metrics:{new_clients:3}});
  if (path.includes('/product-usage')) return json({counts:{}});
  if (path.includes('/system-health')) return json({api:{status:'ready'}, database:{status:'ready'}, max:{mode:'real'}});
  return json({authenticated:false, enrolled:true});
};
await new Promise(resolve => setTimeout(resolve, 0));
await test.passkeyLogin();
console.log(JSON.stringify({metric:state.metrics[0].textContent, banner:state.elements['#auth-banner'].hidden, actions:state.elements['#auth-actions'].hidden}));
"""
    result = _run_admin_behavior(expression)
    assert result == {"metric": "42", "banner": True, "actions": True}


def test_delayed_initial_check_auth_cannot_overwrite_successful_login():
    expression = """
let releaseInitialStatus;
let loginStarted = false;
const delayedStatus = new Promise(resolve => { releaseInitialStatus = resolve; });
fetchImpl = async (request) => {
  const path = request;
  if (path.includes('/auth/status') && !loginStarted) return delayedStatus;
  if (path.includes('/auth/login/options')) return json({challenge_id:'c1', publicKey:{challenge:'AA', allowCredentials:[]}});
  if (path.includes('/auth/login/verify')) return json({authenticated:true});
  if (path.includes('/overview?window_days=30')) return json({metrics:{total_clients:42}});
  if (path.includes('/overview?window_days=7') || path.includes('/overview?window_days=1')) return json({metrics:{new_clients:3}});
  if (path.includes('/product-usage')) return json({counts:{}});
  if (path.includes('/system-health')) return json({api:{status:'ready'}, database:{status:'ready'}, max:{mode:'real'}});
  return json({authenticated:true, enrolled:true});
};
const initialCheck = test.checkAuth();
await new Promise(resolve => setTimeout(resolve, 0));
loginStarted = true;
await test.passkeyLogin();
releaseInitialStatus(json({authenticated:false, enrolled:false}));
await initialCheck;
console.log(JSON.stringify({metric:state.metrics[0].textContent, banner:state.elements['#auth-banner'].hidden, actions:state.elements['#auth-actions'].hidden, login:state.elements['#login-passkey'].hidden, register:state.elements['#register-passkey'].hidden, bootstrap:state.elements['#bootstrap-token'].hidden, logout:state.elements['#logout'].hidden, message:state.elements['#auth-message'].textContent}));
"""
    result = _run_admin_behavior(expression)
    assert result == {"metric": "42", "banner": True, "actions": True, "login": True, "register": True, "bootstrap": True, "logout": False, "message": ""}

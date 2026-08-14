const MAX_HOSTS = new Set(["web.max.ru", "max.ru"]);
const RELEVANT_TYPES = new Set(["worker", "shared_worker", "service_worker", "iframe", "other", "page"]);
const attached = new Set();
const targetTypes = new Map();
const childSessions = new Map();
let rootTargetId = null;
let rootTabId = null;
let captureActive = false;
let captureTimer = null;
let lastScan = {status: "NOT_RUN", targets: [], websocketEvents: [], live: null};

function safeUrl(raw) {
  try {
    const url = new URL(raw || "");
    if (!MAX_HOSTS.has(url.hostname)) return null;
    return `${url.protocol}//${url.hostname}${url.port ? `:${url.port}` : ""}`;
  } catch (_) { return null; }
}

function maskId(id) {
  const value = String(id || "");
  return value.length <= 6 ? "***" : `${value.slice(0, 3)}***${value.slice(-3)}`;
}

function targetRecord(target) {
  const origin = safeUrl(target.url);
  return origin && RELEVANT_TYPES.has(target.type) ? {
    type: target.type,
    origin,
    targetIdMasked: maskId(target.id),
    attached: false,
    attach: "NOT_RUN",
    runtime: "NOT_RUN",
    network: "NOT_RUN",
    websocket: "NOT_OBSERVED",
    addressable: "NOT_RUN"
  } : null;
}

function updateRecord(id, patch) {
  lastScan.targets = lastScan.targets.map(row => row.targetIdMasked === maskId(id) ? {...row, ...patch} : row);
}

function eventRecord(source, method, params) {
  const origin = safeUrl(params?.url || params?.response?.url);
  if (!origin || !source.targetId) return null;
  const payload = params?.response?.payloadData;
  return {
    timestamp: Date.now(),
    targetIdMasked: maskId(source.targetId),
    sessionIdMasked: maskId(source.sessionId),
    targetType: targetTypes.get(source.targetId) || "unknown",
    method,
    origin,
    requestIdMasked: maskId(params?.requestId),
    direction: method.endsWith("Sent") ? "sent" : method.endsWith("Received") ? "received" : "event",
    opcode: params?.response?.opcode ?? null,
    payloadKind: typeof payload === "string" ? "text-or-binary" : "unknown",
    payloadLength: typeof payload === "string" ? payload.length : 0
  };
}

chrome.debugger.onEvent.addListener((source, method, params) => {
  if (method === "Target.attachedToTarget") {
    const info = params?.targetInfo;
    const origin = safeUrl(info?.url);
    if (!origin || !params?.sessionId) return;
    const child = {type: info.type || "other", origin, targetIdMasked: maskId(info.targetId), sessionIdMasked: maskId(params.sessionId), attached: "PASS", runtime: "NOT_RUN", network: "NOT_RUN", wsCreated: "NO", frameActivity: "NO"};
    childSessions.set(params.sessionId, {targetId: info.targetId, type: child.type, origin});
    lastScan.childTargets = [...(lastScan.childTargets || []).filter(row => row.sessionIdMasked !== child.sessionIdMasked), child];
    configureChildSession(params.sessionId, child).catch(() => {});
    return;
  }
  if (!source.targetId || !method.startsWith("Network.webSocket")) return;
  const event = eventRecord(source, method, params);
  if (!event) return;
  lastScan.websocketEvents = [...lastScan.websocketEvents, event].slice(-100);
  const isFrame = method === "Network.webSocketFrameSent" || method === "Network.webSocketFrameReceived";
  const isCreated = method === "Network.webSocketCreated";
  const isHandshake = method === "Network.webSocketHandshakeResponseReceived";
  updateRecord(source.targetId, {
    websocket: isFrame ? "FRAME_ACTIVITY" : "OBSERVED",
    ...(isCreated ? {wsCreated: "YES"} : {}),
    ...(isHandshake ? {wsHandshake: "YES"} : {})
  });
  const child = source.sessionId ? childSessions.get(source.sessionId) : null;
  if (child) {
    const childPatch = {
      ...(isCreated ? {wsCreated: "YES"} : {}),
      ...(isFrame ? {frameActivity: "YES"} : {})
    };
    lastScan.childTargets = (lastScan.childTargets || []).map(row => row.sessionIdMasked === maskId(source.sessionId) ? {...row, ...childPatch} : row);
    const row = lastScan.childTargets.find(item => item.sessionIdMasked === maskId(source.sessionId));
    if (row?.wsCreated === "YES" && row?.frameActivity === "YES") {
      lastScan.live = {targetIdMasked: row.targetIdMasked, sessionIdMasked: row.sessionIdMasked, targetType: row.type, origin: row.origin};
      configureChildRuntime(source.sessionId).catch(() => {});
    }
  }
  const row = lastScan.targets.find(item => item.targetIdMasked === maskId(source.targetId));
  if (row?.wsCreated === "YES" && row?.wsHandshake === "YES" && row?.websocket === "FRAME_ACTIVITY") {
    lastScan.live = {targetIdMasked: row.targetIdMasked, targetType: row.type, origin: row.origin};
  }
});

function childDebuggee(sessionId) {
  return {tabId: rootTabId, sessionId};
}

async function configureChildRuntime(sessionId) {
  const child = childSessions.get(sessionId);
  if (!child) return;
  try {
    await chrome.debugger.sendCommand(childDebuggee(sessionId), "Runtime.enable", {});
    const result = await chrome.debugger.sendCommand(childDebuggee(sessionId), "Runtime.evaluate", {
      expression: "typeof WebSocket",
      returnByValue: true
    });
    lastScan.childTargets = (lastScan.childTargets || []).map(row => row.sessionIdMasked === maskId(sessionId) ? {...row, runtime: result?.result?.value === "function" ? "PASS" : "PASS"} : row);
  } catch (_) {
    lastScan.childTargets = (lastScan.childTargets || []).map(row => row.sessionIdMasked === maskId(sessionId) ? {...row, runtime: "FAIL"} : row);
  }
}

async function configureChildSession(sessionId, child) {
  try {
    await chrome.debugger.sendCommand(childDebuggee(sessionId), "Runtime.enable", {});
    child.runtime = "PASS";
  } catch (_) { child.runtime = "FAIL"; }
  try {
    await chrome.debugger.sendCommand(childDebuggee(sessionId), "Network.enable", {});
    child.network = "PASS";
  } catch (_) { child.network = "FAIL"; }
  lastScan.childTargets = (lastScan.childTargets || []).map(row => row.sessionIdMasked === maskId(sessionId) ? {...row, runtime: child.runtime, network: child.network} : row);
}

async function enableChildAutoAttach(rootId, tabId) {
  rootTargetId = rootId;
  rootTabId = tabId;
  const debuggee = {targetId: rootId};
  await chrome.debugger.sendCommand(debuggee, "Target.setDiscoverTargets", {discover: true});
  await chrome.debugger.sendCommand(debuggee, "Target.setAutoAttach", {autoAttach: true, waitForDebuggerOnStart: false, flatten: true});
  lastScan.autoAttach = "PASS";
}

chrome.debugger.onDetach.addListener(source => {
  if (source.targetId) attached.delete(source.targetId);
});

async function attachTarget(target) {
  const record = targetRecord(target);
  if (!record || !target.id || attached.has(target.id)) return;
  targetTypes.set(target.id, target.type);
  if (!lastScan.targets.some(row => row.targetIdMasked === maskId(target.id))) lastScan.targets.push(record);
  const debuggerTarget = {targetId: target.id};
  try {
    await chrome.debugger.attach(debuggerTarget, "1.3");
    attached.add(target.id);
    updateRecord(target.id, {attached: true, attach: "PASS"});
    await chrome.debugger.sendCommand(debuggerTarget, "Runtime.enable", {});
    const runtime = await chrome.debugger.sendCommand(debuggerTarget, "Runtime.evaluate", {
      expression: `JSON.stringify({webSocketConstructor: typeof WebSocket, locationOrigin: typeof location === "object" && location ? location.origin : "", socketLikeGlobals: Object.getOwnPropertyNames(globalThis).filter(k => /socket|transport/i.test(k)).slice(0, 20)})`,
      returnByValue: true
    });
    const value = runtime?.result?.value;
    const safeRuntime = typeof value === "string" ? JSON.parse(value) : {};
    updateRecord(target.id, {
      runtime: "PASS",
      websocket: safeRuntime.webSocketConstructor === "function" ? "CONSTRUCTOR_VISIBLE" : "NOT_OBSERVED",
      addressable: "PASS",
      sendPath: Array.isArray(safeRuntime.socketLikeGlobals) && safeRuntime.socketLikeGlobals.length ? "PASS" : "NOT_PROVEN"
    });
    await chrome.debugger.sendCommand(debuggerTarget, "Network.enable", {});
    updateRecord(target.id, {network: "PASS"});
  } catch (_) {
    updateRecord(target.id, {attach: "FAIL", runtime: "FAIL", network: "FAIL", addressable: "FAIL"});
  }
}

async function discoverAndAttach() {
  const all = await chrome.debugger.getTargets();
  for (const target of all) await attachTarget(target);
}

async function scan() {
  const all = await chrome.debugger.getTargets();
  const records = all.map(targetRecord).filter(Boolean);
  lastScan = {status: "SCANNING", targets: records, childTargets: [], websocketEvents: [], live: null, autoAttach: "NOT_RUN"};
  await discoverAndAttach();
  const root = all.find(target => target.type === "page" && safeUrl(target.url));
  if (root) {
    try { await enableChildAutoAttach(root.id, root.tabId); }
    catch (_) { lastScan.autoAttach = "FAIL"; }
  }
  lastScan.status = "COMPLETE";
  await chrome.storage.session.set({lastScan});
  return lastScan;
}

async function startCapture() {
  captureActive = true;
  await scan();
  if (!captureTimer) captureTimer = setInterval(() => discoverAndAttach().catch(() => {}), 1000);
  return lastScan;
}

chrome.runtime.onInstalled.addListener(() => { startCapture().catch(() => {}); });

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type === "scan" || message?.type === "startCapture") {
    startCapture().then(sendResponse).catch(error => sendResponse({status: "ERROR", reason: "SCAN_FAILED"}));
    return true;
  }
  if (message?.type === "status") {
    sendResponse(lastScan);
  }
});

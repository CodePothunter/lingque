// LingQue Browser Relay Extension
// 通过 WebSocket 连接到本地中继服务器，接收 CDP 命令并执行
//
// 跨机器使用（SSH 隧道）：
//   在本机执行: ssh -L 50518:127.0.0.1:50518 用户名@服务器IP
//   扩展连接 ws://127.0.0.1:50518/ws

const RELAY_URL = 'ws://127.0.0.1:50518/ws';
let ws = null;
let targetTabId = null; // 锁定的目标 tab，不依赖 focus

// ── MV3 Service Worker 保活 ──────────────────────────────
chrome.alarms.create('keepalive', { periodInMinutes: 0.4 });

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === 'keepalive') {
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      connect();
    }
  }
});

// ── Tab 追踪 ─────────────────────────────────────────────
// navigate 命令会自动锁定 tab；也可以通过 _setTab 手动指定
async function ensureTab() {
  // 已有锁定 tab 且还存在，直接用
  if (targetTabId !== null) {
    try {
      const tab = await chrome.tabs.get(targetTabId);
      if (tab) return targetTabId;
    } catch (e) {
      // tab 已关闭，清除
      targetTabId = null;
    }
  }
  // fallback: 用当前 active tab
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (tab) {
    targetTabId = tab.id;
    return targetTabId;
  }
  return null;
}

// ── WebSocket 连接 ───────────────────────────────────────
function connect() {
  if (ws && (ws.readyState === WebSocket.CONNECTING || ws.readyState === WebSocket.OPEN)) {
    return;
  }

  ws = new WebSocket(RELAY_URL);

  ws.onopen = () => {
    console.log('[LingQue] Connected to relay server');
    updateBadge('ON', '#4CAF50');
  };

  ws.onmessage = async (event) => {
    const command = JSON.parse(event.data);
    const { id, method, params } = command;

    // 内部命令：切换目标 tab
    if (method === '_setTab') {
      targetTabId = params.tabId || null;
      ws.send(JSON.stringify({ id, result: { tabId: targetTabId } }));
      return;
    }

    try {
      const tabId = await ensureTab();
      if (tabId === null) {
        ws.send(JSON.stringify({ id, error: { message: 'No tab available' } }));
        return;
      }

      // 截图前激活 tab，防止黑屏
      if (method === 'Page.captureScreenshot') {
        await chrome.tabs.update(tabId, { active: true });
        await new Promise(r => setTimeout(r, 300));
      }

      // Attach debugger if not already attached
      try {
        await chrome.debugger.attach({ tabId }, '1.3');
      } catch (e) {
        // Already attached, ignore
      }

      // 给 CDP 命令加超时保护（截图 10s，其他 25s）
      const timeout = method === 'Page.captureScreenshot' ? 10000 : 25000;
      const result = await Promise.race([
        chrome.debugger.sendCommand({ tabId }, method, params || {}),
        new Promise((_, reject) =>
          setTimeout(() => reject(new Error(`CDP command "${method}" timed out after ${timeout}ms`)), timeout)
        ),
      ]);

      // navigate 时自动锁定该 tab
      if (method === 'Page.navigate') {
        targetTabId = tabId;
      }

      ws.send(JSON.stringify({ id, result }));
    } catch (error) {
      ws.send(JSON.stringify({ id, error: { message: error.message } }));
    }
  };

  ws.onclose = () => {
    console.log('[LingQue] Disconnected from relay server');
    updateBadge('OFF', '#F44336');
    ws = null;
  };

  ws.onerror = () => {
    updateBadge('OFF', '#F44336');
    ws = null;
  };
}

// ── Popup 通信 ───────────────────────────────────────────
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.type === 'getStatus') {
    sendResponse({
      connected: ws !== null && ws.readyState === WebSocket.OPEN,
      targetTabId,
    });
  }
  return false;
});

function updateBadge(text, color) {
  chrome.action.setBadgeText({ text });
  chrome.action.setBadgeBackgroundColor({ color });
}

connect();

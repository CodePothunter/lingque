// LingQue Browser Relay Extension
// 通过 WebSocket 连接到本地中继服务器，接收 CDP 命令并执行

const RELAY_URL = 'ws://127.0.0.1:18792/ws';
let ws = null;
let reconnectTimer = null;

function connect() {
  ws = new WebSocket(RELAY_URL);
  
  ws.onopen = () => {
    console.log('[LingQue] Connected to relay server');
    if (reconnectTimer) {
      clearInterval(reconnectTimer);
      reconnectTimer = null;
    }
  };
  
  ws.onmessage = async (event) => {
    const command = JSON.parse(event.data);
    const { id, method, params } = command;
    
    try {
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      if (!tab) {
        ws.send(JSON.stringify({ id, error: { message: 'No active tab' } }));
        return;
      }
      
      // Attach debugger if not already attached
      try {
        await chrome.debugger.attach({ tabId: tab.id }, '1.3');
      } catch (e) {
        // Already attached, ignore
      }
      
      // Execute CDP command
      const result = await chrome.debugger.sendCommand({ tabId: tab.id }, method, params || {});
      ws.send(JSON.stringify({ id, result }));
    } catch (error) {
      ws.send(JSON.stringify({ id, error: { message: error.message } }));
    }
  };
  
  ws.onclose = () => {
    console.log('[LingQue] Disconnected, reconnecting...');
    if (!reconnectTimer) {
      reconnectTimer = setInterval(connect, 3000);
    }
  };
}

connect();

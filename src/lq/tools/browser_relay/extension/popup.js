const statusEl = document.getElementById('status');
const tabInfoEl = document.getElementById('tabInfo');

chrome.runtime.sendMessage({ type: 'getStatus' }, (resp) => {
  if (!resp) return;
  if (resp.connected) {
    statusEl.className = 'status connected';
    statusEl.textContent = '已连接';
  } else {
    statusEl.className = 'status disconnected';
    statusEl.textContent = '未连接';
  }
  if (resp.targetTabId) {
    tabInfoEl.textContent = `锁定 Tab: #${resp.targetTabId}`;
  } else {
    tabInfoEl.textContent = '未锁定 Tab（将使用当前活动标签）';
  }
});

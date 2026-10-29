(function () {
  const root = document.getElementById('brokers');
  if (!root) return;
  root.addEventListener('click', async event => {
    const tab = event.target.closest('[data-broker-tab]');
    if (tab) {
      const workspace = tab.closest('.broker-workspace');
      workspace.querySelectorAll('[data-broker-tab]').forEach(button => button.setAttribute('aria-selected', String(button === tab)));
      workspace.querySelectorAll('[data-broker-view]').forEach(panel => { panel.hidden = panel.dataset.brokerView !== tab.dataset.brokerTab; });
      return;
    }
    const check = event.target.closest('[data-mcp-check]');
    if (!check || check.disabled) return;
    const workspace = check.closest('.broker-workspace');
    const status = workspace.querySelector('[data-mcp-status]');
    const error = workspace.querySelector('[data-mcp-error]');
    const details = workspace.querySelector('[data-mcp-details]');
    check.disabled = true; details.hidden = true; error.hidden = true;
    status.textContent = 'Проверяем MCP…';
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 125000);
    try {
      const response = await fetch('/v1/brokers/t-invest/mcp/check', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({environment: workspace.dataset.environment}),
        cache: 'no-store', signal: controller.signal,
      });
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Не удалось проверить MCP');
      if (!data.available || data.environment !== workspace.dataset.environment || !Array.isArray(data.capabilities) || !Array.isArray(data.tools)) throw new Error('MCP вернул неполный результат проверки');
      status.textContent = 'MCP доступен. Найдено инструментов: ' + data.tools.length;
      workspace.querySelector('[data-mcp-time]').textContent = 'Проверено: ' + data.checked_at;
      workspace.querySelector('[data-mcp-capabilities]').replaceChildren(...data.capabilities.map(item => {
        const li = document.createElement('li'); li.textContent = item.name + ' — ' + item.reason; return li;
      }));
      workspace.querySelector('[data-mcp-tools]').replaceChildren(...data.tools.map(item => {
        const li = document.createElement('li'); li.textContent = item.name + ' · схема: ' + item.schema_sha256.slice(0, 12) + ' · не допущен'; return li;
      }));
      details.hidden = false;
    } catch (exc) {
      status.textContent = 'Проверка MCP не завершена.';
      error.textContent = exc.name === 'AbortError' ? 'Превышено время проверки MCP. Повторите позже.' : exc instanceof TypeError ? 'Не удалось связаться с сервером приложения.' : exc.message;
      error.hidden = false;
    } finally {
      clearTimeout(timer); check.disabled = false;
    }
  });
})();

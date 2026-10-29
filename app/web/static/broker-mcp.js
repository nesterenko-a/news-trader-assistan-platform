(function () {
  const root = document.getElementById('brokers');
  if (!root) return;
  const llmStates = new WeakMap();
  function state(workspace) {
    if (!llmStates.has(workspace)) llmStates.set(workspace, {generation: 0, loaded: false, proof: null, configured: false});
    return llmStates.get(workspace);
  }
  async function llmRequest(url, options = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 85000);
    try {
      const response = await fetch(url, {...options, cache: 'no-store', signal: controller.signal});
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Не удалось проверить LLM');
      return data;
    } finally { clearTimeout(timer); }
  }
  async function loadModels(workspace) {
    const current = state(workspace);
    const generation = ++current.generation;
    current.proof = null; current.loaded = true; current.configured = false;
    const provider = workspace.querySelector('[data-mcp-provider]').value;
    const model = workspace.querySelector('[data-mcp-model]');
    const status = workspace.querySelector('[data-mcp-llm-status]');
    const check = workspace.querySelector('[data-mcp-check-llm]');
    model.disabled = true; check.disabled = true;
    model.replaceChildren(new Option('Загрузка моделей…', ''));
    status.textContent = 'Загружаем каталог провайдера…';
    try {
      const data = await llmRequest('/v1/brokers/t-invest/mcp/models?provider=' + encodeURIComponent(provider));
      if (generation !== current.generation) return;
      if (data.provider !== provider || !Array.isArray(data.models)) throw new Error('Некорректный каталог LLM');
      model.replaceChildren(...data.models.map(name => new Option(name, name)));
      if (data.models.includes(data.default_model)) model.value = data.default_model;
      current.configured = data.configured === true;
      model.disabled = !data.models.length;
      check.disabled = !current.configured || !model.value;
      status.textContent = data.warning || 'Каталог загружен. Поддержку инструментов нужно проверить.';
    } catch (exc) {
      if (generation !== current.generation) return;
      current.loaded = false;
      model.replaceChildren(new Option('Каталог недоступен', ''));
      status.textContent = 'Не удалось загрузить модели. Повторно откройте вкладку или смените провайдера.';
    }
  }
  root.addEventListener('change', event => {
    const workspace = event.target.closest('.broker-workspace');
    if (!workspace) return;
    if (event.target.matches('[data-mcp-provider]')) loadModels(workspace);
    if (event.target.matches('[data-mcp-model]')) {
      const current = state(workspace); current.generation++; current.proof = null;
      workspace.querySelector('[data-mcp-llm-status]').textContent = 'Модель изменена. Проверьте LLM повторно.';
      workspace.querySelector('[data-mcp-check-llm]').disabled = !current.configured || !event.target.value;
    }
  });
  root.addEventListener('click', async event => {
    const tab = event.target.closest('[data-broker-tab]');
    if (tab) {
      const workspace = tab.closest('.broker-workspace');
      workspace.querySelectorAll('[data-broker-tab]').forEach(button => button.setAttribute('aria-selected', String(button === tab)));
      workspace.querySelectorAll('[data-broker-view]').forEach(panel => { panel.hidden = panel.dataset.brokerView !== tab.dataset.brokerTab; });
      if (tab.dataset.brokerTab === 'mcp' && !state(workspace).loaded) loadModels(workspace);
      return;
    }
    const llmCheck = event.target.closest('[data-mcp-check-llm]');
    if (llmCheck && !llmCheck.disabled) {
      const workspace = llmCheck.closest('.broker-workspace');
      const current = state(workspace), generation = ++current.generation;
      const provider = workspace.querySelector('[data-mcp-provider]').value;
      const model = workspace.querySelector('[data-mcp-model]').value;
      const status = workspace.querySelector('[data-mcp-llm-status]');
      current.proof = null; llmCheck.disabled = true;
      status.textContent = 'Проверяем вызов инструмента и обработку его результата…';
      try {
        const result = await llmRequest('/v1/brokers/t-invest/mcp/check-llm', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({provider, model})});
        if (generation !== current.generation) return;
        if (result.provider !== provider || result.model !== model || !result.tool_calling || !result.check_token || result.expires_at * 1000 <= Date.now()) throw new Error('LLM не прошла проверку');
        current.proof = result;
        status.textContent = 'Вызов инструментов проверен: ' + provider + ' / ' + model + '. Действует до ' + new Date(result.expires_at * 1000).toLocaleTimeString();
      } catch (exc) {
        if (generation !== current.generation) return;
        status.textContent = exc.name === 'AbortError' ? 'Превышено время проверки LLM.' : exc instanceof TypeError ? 'Не удалось связаться с сервером приложения.' : exc.message;
      } finally { if (generation === current.generation) llmCheck.disabled = false; }
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

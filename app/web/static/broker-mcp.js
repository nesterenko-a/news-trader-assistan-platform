(function () {
  const root = document.getElementById('brokers');
  if (!root) return;
  const llmStates = new WeakMap();
  function state(workspace) {
    if (!llmStates.has(workspace)) llmStates.set(workspace, {generation: 0, loaded: false, proof: null, configured: false, mcp: false, busy: false, askGeneration: 0});
    return llmStates.get(workspace);
  }
  function account(workspace) { return workspace.querySelector('.broker-portfolio-content')?.dataset.selectedAccount || null; }
  function ready(workspace) {
    const current = state(workspace);
    workspace.querySelector('[data-mcp-ask]').disabled = current.busy || !current.mcp || !current.proof || current.proof.expires_at * 1000 <= Date.now() || !workspace.querySelector('[data-mcp-question]').value.trim();
    workspace.querySelector('[data-mcp-account]').textContent = account(workspace) ? 'Счёт: ••••' + account(workspace).slice(-4) : 'Счёт не выбран. Доступны рыночные вопросы.';
  }
  root.addEventListener('input', event => { if (event.target.matches('[data-mcp-question]')) ready(event.target.closest('.broker-workspace')); });
  document.addEventListener('broker:portfolio-updated', event => {
    const workspace = root.querySelector('#broker-workspace-' + event.detail.environment);
    if (!workspace) return;
    const current = state(workspace); current.askGeneration++; current.busy = false;
    current.askController?.abort();
    workspace.querySelector('[data-mcp-answer]').hidden = true;
    workspace.querySelector('[data-mcp-ask-status]').textContent = 'Контекст счёта обновлён. Предыдущий ответ скрыт.';
    ready(workspace);
  });
  setInterval(() => root.querySelectorAll('.broker-workspace[data-connected="true"]').forEach(ready), 1000);
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
    ready(workspace);
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
      ready(workspace);
    }
  });
  root.addEventListener('click', async event => {
    const example = event.target.closest('[data-mcp-example]');
    if (example) { const workspace = example.closest('.broker-workspace'); workspace.querySelector('[data-mcp-question]').value = example.dataset.mcpExample; ready(workspace); return; }
    const ask = event.target.closest('[data-mcp-ask]');
    if (ask && !ask.disabled) {
      const workspace = ask.closest('.broker-workspace'), current = state(workspace);
      const generation = ++current.askGeneration, selected = account(workspace);
      const pair = {provider: workspace.querySelector('[data-mcp-provider]').value, model: workspace.querySelector('[data-mcp-model]').value};
      const status = workspace.querySelector('[data-mcp-ask-status]');
      const controller = new AbortController();
      current.busy = true; current.askController = controller; ready(workspace);
      workspace.querySelector('[data-mcp-answer]').hidden = true;
      status.textContent = 'Получаем данные и формируем ответ…';
      const timer = setTimeout(() => controller.abort(), 125000);
      try {
        const response = await fetch('/v1/brokers/t-invest/mcp/ask', {method: 'POST', headers: {'Content-Type': 'application/json'}, cache: 'no-store', signal: controller.signal, body: JSON.stringify({...pair, environment: workspace.dataset.environment, account_id: selected, check_token: current.proof.check_token, question: workspace.querySelector('[data-mcp-question]').value})});
        const data = await response.json();
        if (generation !== current.askGeneration || selected !== account(workspace)) return;
        if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Не удалось получить аналитический ответ');
        if (data.environment !== workspace.dataset.environment || (data.account_id || null) !== selected || data.provider !== pair.provider || data.model !== pair.model || typeof data.answer !== 'string' || !Array.isArray(data.evidence) || !Array.isArray(data.warnings)) throw new Error('Ответ не соответствует выбранному контексту');
        workspace.querySelector('[data-mcp-answer-context]').textContent = data.environment + ' · счёт ' + (selected ? '••••' + selected.slice(-4) : 'не выбран') + ' · ' + data.provider + ' / ' + data.model + ' · ' + data.generated_at;
        workspace.querySelector('[data-mcp-answer-text]').textContent = data.answer;
        workspace.querySelector('[data-mcp-warnings]').replaceChildren(...data.warnings.map(text => {const li = document.createElement('li'); li.textContent = text; return li;}));
        workspace.querySelector('[data-mcp-evidence]').replaceChildren(...data.evidence.map(item => {
          const li = document.createElement('li'); li.textContent = item.source + ' · ' + item.tool + ' · данные: ' + (item.data_time || 'время не указано') + ' · получено: ' + item.fetched_at;
          (item.publications || []).forEach(publication => { try {const url = new URL(publication.url); if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password) return; const a = document.createElement('a'); a.href = url.href; a.rel = 'noopener noreferrer'; a.target = '_blank'; a.textContent = ' Источник · ' + (publication.date || 'дата не указана'); li.append(a);} catch (_) {} });
          return li;
        }));
        workspace.querySelector('[data-mcp-calculations]').textContent = JSON.stringify(data.calculations || [], null, 2);
        workspace.querySelector('[data-mcp-answer]').hidden = false;
        status.textContent = {ok: 'Ответ готов.', partial: 'Частичный результат: проверьте ограничения.', needs_clarification: 'Нужно уточнение.'}[data.status] || 'Ответ получен.';
      } catch (exc) { if (generation === current.askGeneration) status.textContent = exc.name === 'AbortError' ? 'Запрос остановлен по времени. Повторите позже.' : exc instanceof TypeError ? 'Не удалось связаться с сервером приложения.' : exc.message; }
      finally { clearTimeout(timer); if (generation === current.askGeneration) {current.busy = false; ready(workspace);} }
      return;
    }
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
      } finally { if (generation === current.generation) llmCheck.disabled = false; ready(workspace); }
      return;
    }
    const exporting = event.target.closest('[data-mcp-export]');
    const check = exporting || event.target.closest('[data-mcp-check]');
    if (!check || check.disabled) return;
    const workspace = check.closest('.broker-workspace');
    const status = workspace.querySelector('[data-mcp-status]');
    const error = workspace.querySelector('[data-mcp-error]');
    const details = workspace.querySelector('[data-mcp-details]');
    const mcpButtons = workspace.querySelectorAll('[data-mcp-check], [data-mcp-export]');
    mcpButtons.forEach(button => { button.disabled = true; });
    details.hidden = true; error.hidden = true;
    state(workspace).mcp = false; ready(workspace);
    status.textContent = exporting ? 'Загружаем схемы MCP…' : 'Проверяем MCP…';
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 125000);
    try {
      const response = await fetch('/v1/brokers/t-invest/mcp/check', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({environment: workspace.dataset.environment, ...(exporting ? {include_schemas: true} : {})}),
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
        const li = document.createElement('li'); li.textContent = item.name + ' · схема: ' + item.schema_sha256.slice(0, 12) + (item.approved ? ' · чтение допущено' : ' · не допущен'); return li;
      }));
      details.hidden = false;
      state(workspace).mcp = data.tools.some(tool => tool.approved === true); ready(workspace);
      if (exporting) {
        if (data.catalog_format !== 'newstrader-mcp-review-v1' || !data.tools.every(tool => tool.input_schema && tool.schema_sha256?.length === 64)) throw new Error('Сервер не вернул схемы. Обновите приложение и повторите экспорт.');
        const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], {type: 'application/json'}));
        const link = document.createElement('a');
        link.href = url; link.download = 't-invest-mcp-catalog-' + workspace.dataset.environment + '.json';
        document.body.append(link); link.click(); link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
        status.textContent = 'Каталог схем MCP подготовлен для скачивания. Инструментов: ' + data.tools.length;
      }
    } catch (exc) {
      details.hidden = true;
      status.textContent = 'Проверка MCP не завершена.';
      error.textContent = exc.name === 'AbortError' ? 'Превышено время проверки MCP. Повторите позже.' : exc instanceof TypeError ? 'Не удалось связаться с сервером приложения.' : exc.message;
      error.hidden = false;
    } finally {
      clearTimeout(timer); mcpButtons.forEach(button => { button.disabled = false; });
    }
  });
})();

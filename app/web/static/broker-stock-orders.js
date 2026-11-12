(function () {
  const dialog = document.querySelector('#broker-stock-order');
  if (!dialog) return;
  const workspace = dialog.closest('.broker-workspace');
  const content = () => workspace.querySelector('#broker-content-sandbox');
  const account = () => content()?.dataset.selectedAccount;
  const field = name => dialog.querySelector('[data-order-' + name + ']');
  const search = field('search');
  const matches = field('matches');
  const list = dialog.querySelector('[role=listbox]');
  const side = field('side');
  const lots = field('lots');
  const submit = field('submit');
  const check = field('check');
  const feedback = workspace.querySelector('[data-stock-feedback]');
  const recover = workspace.querySelector('[data-stock-recover]');
  const titles = {pending: 'Отправляется', accepted: 'Принята', partial: 'Частично исполнена',
    filled: 'Исполнена', rejected: 'Отклонена', cancelled: 'Отменена', unknown: 'Статус неизвестен'};
  const unresolved = new Set(['pending', 'accepted', 'partial', 'unknown']);
  const pending = new Map();
  const catalogs = new Map();
  let form, options = [], active = -1;

  function storageKey(id) { return 'broker-stock-order:' + id; }
  function saved(id) {
    if (!id) return null;
    try { return pending.get(id) || sessionStorage.getItem(storageKey(id)); }
    catch { return pending.get(id); }
  }
  function remember(id, key) {
    if (key) pending.set(id, key);
    else pending.delete(id);
    try {
      if (key) sessionStorage.setItem(storageKey(id), key);
      else sessionStorage.removeItem(storageKey(id));
    } catch {}
    recover.hidden = !saved(account());
  }
  function endpoint(id) {
    return '/v1/brokers/t-invest/sandbox/accounts/' + encodeURIComponent(id);
  }
  async function request(id, path, body) {
    const response = await fetch(endpoint(id) + path, {
      cache: 'no-store', redirect: 'error',
      ...(body ? {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)} : {}),
    });
    const data = await response.json();
    if (!response.ok) {
      const error = new Error(typeof data.detail === 'string' ? data.detail : 'Не удалось выполнить запрос.');
      error.status = response.status;
      throw error;
    }
    return data;
  }
  function loadCatalog(id) {
    if (!catalogs.has(id)) {
      catalogs.set(id, request(id, '/instruments').catch(error => {
        catalogs.delete(id);
        throw error;
      }));
    }
    return catalogs.get(id);
  }
  function preload() {
    const id = account();
    if (id) loadCatalog(id).catch(() => {});
  }
  function render() {
    if (!form) return;
    const details = form.details;
    const count = lots.valueAsNumber;
    const selling = side.value === 'sell';
    submit.textContent = selling ? 'Продать по рынку' : 'Купить по рынку';
    search.disabled = form.locked || Boolean(form.key);
    side.disabled = Boolean(form.key);
    lots.disabled = Boolean(form.key);
    check.hidden = !form.key || form.finished;
    check.disabled = form.busy;
    submit.disabled = !details || form.busy || Boolean(form.key) || !Number.isSafeInteger(count) || count < 1 ||
      (selling ? !details.sell_available || details.available_lots == null || count > details.available_lots : !details.buy_available);
    field('available').textContent = !details ? '' : details.available_lots == null ? 'Не удалось получить доступный остаток.' :
      'Доступно к продаже: ' + details.available_shares + ' акций · ' + details.available_lots + ' лотов.';
    const amount = Number(details?.last_price) * details?.lot * count;
    field('estimate').textContent = !details ? '' : 'В лоте: ' + details.lot + ' акций. ' +
      (details.last_price && Number.isFinite(amount) && count > 0
        ? 'Ориентировочно без комиссии: ' + amount.toLocaleString('ru-RU', {style: 'currency', currency: 'RUB'})
        : 'Оценка недоступна.');
  }
  function closeList() {
    options = [];
    active = -1;
    list.replaceChildren();
    list.hidden = true;
    matches.hidden = true;
    search.setAttribute('aria-expanded', 'false');
    search.removeAttribute('aria-activedescendant');
  }
  const normalize = value => value.trim().toLocaleLowerCase('ru').replaceAll('ё', 'е');
  function tickerRank(item, query) {
    const ticker = normalize(item.ticker);
    return ticker === query ? 0 : ticker.includes(query) ? 1 : 2;
  }
  function filterCatalog(state) {
    const query = normalize(search.value);
    closeList();
    matches.hidden = false;
    if (!state.catalog) {
      matches.textContent = state.catalogError || 'Загружаем акции…';
      return;
    }
    options = state.catalog.filter(item => query
      ? normalize(item.ticker).includes(query) || normalize(item.name).includes(query)
      : item.is_favorite);
    if (query) options.sort((a, b) => tickerRank(a, query) - tickerRank(b, query));
    matches.textContent = query ? 'Найдено: ' + options.length
      : options.length ? 'Избранное · ' + options.length : 'Нет доступных акций в избранном. Введите тикер или название.';
    list.replaceChildren(...options.map((item, index) => {
      const option = document.createElement('li');
      option.id = 'stock-order-option-' + index;
      option.dataset.index = index;
      option.setAttribute('role', 'option');
      option.setAttribute('aria-selected', 'false');
      option.textContent = [item.ticker, item.name, item.class_code].filter(Boolean).join(' · ');
      return option;
    }));
    list.hidden = !options.length;
    search.setAttribute('aria-expanded', String(!list.hidden));
  }
  async function openList(state) {
    state.catalogError = null;
    filterCatalog(state);
    if (state.catalog) return;
    try {
      state.catalog = await loadCatalog(state.account);
    } catch (error) {
      state.catalogError = error.message;
    }
    if (form === state && dialog.open && !state.uid && !matches.hidden) filterCatalog(state);
  }
  async function select(item) {
    const state = form;
    state.uid = item.instrument_uid;
    state.details = null;
    const revision = ++state.revision;
    search.value = item.ticker ? item.ticker + ' · ' + item.name : item.name;
    closeList();
    field('message').textContent = 'Получаем данные акции…';
    render();
    try {
      const details = await request(state.account, '/instruments/' + encodeURIComponent(state.uid) + '/order-details');
      if (form !== state || state.revision !== revision) return;
      state.details = details;
      field('message').textContent = '';
    } catch (error) {
      if (form === state && state.revision === revision) field('message').textContent = error.message;
    }
    if (form === state) render();
  }
  function open(button) {
    const id = account();
    if (!id) return;
    const key = saved(id);
    form = {account: id, locked: Boolean(button?.dataset.stockEdit), key, revision: 0};
    field('form').reset();
    closeList();
    field('account').textContent = 'Sandbox · ' + content().querySelector('.broker-heading h2').textContent.replace(/^Портфель\s*·?\s*/, '');
    field('side-label').hidden = !form.locked;
    field('message').textContent = key ? 'Проверьте статус ранее отправленной заявки.' : '';
    render();
    dialog.showModal();
    if (key) check.focus();
    else if (form.locked) select({instrument_uid: button.dataset.stockEdit, name: button.dataset.stockName});
    else {
      search.focus();
      openList(form);
    }
  }
  search.addEventListener('input', () => {
    const state = form;
    ++state.revision;
    state.uid = state.details = null;
    field('message').textContent = '';
    render();
    filterCatalog(state);
  });
  search.addEventListener('click', () => {
    if (form && !form.uid && (matches.hidden || form.catalogError)) openList(form);
  });
  dialog.addEventListener('pointerdown', event => {
    if (event.target !== dialog && event.target !== search && !list.contains(event.target)) closeList();
  });
  list.addEventListener('mousedown', event => {
    if (event.target.closest('[role="option"]')) event.preventDefault();
  });
  search.addEventListener('keydown', event => {
    if (event.key === 'Enter') {
      event.preventDefault();
      if (active >= 0) select(options[active]);
    } else if (!form.uid && matches.hidden && event.key === 'ArrowDown') {
      event.preventDefault();
      openList(form);
    } else if (options.length && ['ArrowDown', 'ArrowUp'].includes(event.key)) {
      event.preventDefault();
      active = active < 0 ? (event.key === 'ArrowDown' ? 0 : options.length - 1) :
        (active + options.length + (event.key === 'ArrowDown' ? 1 : -1)) % options.length;
      [...list.children].forEach((item, index) => item.setAttribute('aria-selected', String(index === active)));
      search.setAttribute('aria-activedescendant', list.children[active].id);
      list.children[active].scrollIntoView({block: 'nearest'});
    } else if (event.key === 'Tab') {
      closeList();
    }
  });
  list.addEventListener('click', event => {
    const option = event.target.closest('[data-index]');
    if (option) select(options[Number(option.dataset.index)]);
  });
  async function updatePortfolio(id) {
    const previous = content();
    if (account() !== id || previous.getAttribute('aria-busy') === 'true') return;
    const response = await fetch('/broker/t-invest/sandbox/portfolio-fragment', {cache: 'no-store', redirect: 'error'});
    if (!response.ok) throw new Error('Обновите портфель вручную.');
    const doc = new DOMParser().parseFromString(await response.text(), 'text/html');
    const next = doc.querySelector('#broker-content-sandbox');
    if (next?.dataset.selectedAccount === id && content() === previous && previous.getAttribute('aria-busy') !== 'true') {
      previous.replaceWith(next);
      document.dispatchEvent(new CustomEvent('broker:portfolio-updated', {detail: {environment: 'sandbox', accountId: id}}));
    }
  }
  async function perform(state, body) {
    state.busy = true;
    render();
    field('message').textContent = body ? 'Отправляем заявку…' : 'Проверяем статус…';
    try {
      const result = await request(state.account, body ? '/orders' : '/orders/' + state.key, body);
      state.finished = !unresolved.has(result.status) || result.status_check_expired === true;
      if (state.finished) remember(state.account, null);
      let message = (titles[result.status] || titles.unknown) +
        (result.lots_executed == null ? '.' : ' · Исполнено ' + result.lots_executed + ' из ' + result.lots_requested + ' лотов.');
      if (result.status_check_expired && unresolved.has(result.status)) message += ' Срок проверки у брокера истёк.';
      if (result.portfolio_stale) message += ' Обновите портфель вручную.';
      else if (result.lots_executed > 0) {
        try { await updatePortfolio(state.account); }
        catch { message += ' Обновите портфель вручную.'; }
      }
      if (account() === state.account) {
        feedback.textContent = message;
        feedback.dataset.account = state.account;
        feedback.hidden = false;
      }
      if (form === state) field('message').textContent = message;
    } catch (error) {
      if (body && [401, 403, 404, 409, 422].includes(error.status)) {
        remember(state.account, null);
        state.key = null;
      }
      if (form === state) field('message').textContent = state.key && !error.status
        ? 'Статус неизвестен. Проверьте эту заявку.' : error.message;
    } finally {
      state.busy = false;
      if (form === state) render();
    }
  }
  field('form').addEventListener('submit', event => {
    event.preventDefault();
    if (!form || submit.disabled) return;
    form.key = crypto.randomUUID();
    remember(form.account, form.key);
    perform(form, {operation_key: form.key, instrument_uid: form.uid, side: side.value, lots: lots.valueAsNumber});
  });
  check.addEventListener('click', () => perform(form));
  field('close').addEventListener('click', () => dialog.close());
  dialog.addEventListener('cancel', event => {
    if (matches.hidden) return;
    event.preventDefault();
    closeList();
    search.focus();
  });
  dialog.addEventListener('close', () => { if (!dialog.open) form = null; });
  side.addEventListener('change', render);
  lots.addEventListener('input', render);
  workspace.addEventListener('click', event => {
    const button = event.target.closest('[data-stock-buy], [data-stock-edit], [data-stock-recover]');
    if (button && !dialog.open && content()?.getAttribute('aria-busy') !== 'true') open(button);
  });
  document.addEventListener('broker:portfolio-updated', event => {
    if (event.detail.environment !== 'sandbox') return;
    if (form && form.account !== account()) dialog.close();
    if (feedback.dataset.account !== account()) feedback.hidden = true;
    recover.hidden = !saved(account());
    preload();
  });
  document.addEventListener('broker:opened', event => {
    if (event.detail.environment === 'sandbox') preload();
  });
  if (!workspace.hidden && !workspace.querySelector('.broker-data')?.hidden) preload();
  recover.hidden = !saved(account());
})();

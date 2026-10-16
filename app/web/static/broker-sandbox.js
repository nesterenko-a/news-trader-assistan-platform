(async function () {
  const error = document.querySelector('#sandbox-error');
  try {
    const response = await fetch('/v1/brokers/t-invest/sandbox/accounts', {cache: 'no-store'});
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Не удалось получить счета');
    if (data.error) { error.textContent = data.error; error.hidden = false; }
    document.querySelector('#sandbox-empty').hidden = Boolean(data.error) || data.accounts.length !== 0;
    for (const account of data.accounts) {
      const node = document.querySelector('#sandbox-account-template').content.cloneNode(true);
      node.querySelector('.sandbox-name').textContent = account.name;
      node.querySelector('.sandbox-id').textContent = account.masked_id + ' · ' + account.status;
      node.querySelector('.sandbox-balance').textContent = account.rub_balance === null ? 'Остаток в рублях не получен' : 'Доступные рубли: ' + account.rub_balance + ' ₽';
      node.querySelectorAll('[name="account_id"]').forEach(input => { input.value = account.id; });
      node.querySelectorAll('[name="operation_key"]').forEach(input => { input.value = crypto.randomUUID(); });
      document.querySelector('#sandbox-accounts').append(node);
    }
    for (const action of data.actions) {
      const p = document.createElement('p');
      p.textContent = ({create: 'Создание счёта', pay_in: 'Пополнение', close: 'Закрытие счёта'}[action.kind] || 'Действие') + ': ' + action.message + (action.error_code === 'sync_failed' ? '. Список не обновлён — нажмите «Обновить счета»' : '');
      document.querySelector('#sandbox-actions').append(p);
    }
  } catch (e) {
    error.textContent = e.message || 'Не удалось получить счета';
    error.hidden = false;
  } finally {
    document.querySelector('#sandbox-loading').hidden = true;
  }
  document.querySelectorAll('form').forEach(form => {
    if (!form.action.includes('/broker/t-invest/sandbox/')) return;
    form.addEventListener('submit', event => {
      if (event.defaultPrevented) return;
      form.querySelectorAll('button[type="submit"]').forEach(button => { button.disabled = true; });
    });
  });
}());

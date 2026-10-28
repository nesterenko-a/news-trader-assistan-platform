(function () {
  const root = document.querySelector('#brokers');
  if (!root) return;
  const workspaces = [...root.querySelectorAll('.broker-workspace')];
  const editors = [...root.querySelectorAll('.broker-appearance')];
  const buttons = [...root.querySelectorAll('[data-broker]')];
  const previews = new Map();
  let selected = null;
  const valid = value => ['production', 'sandbox'].includes(value);
  function signal(workspace) {
    if (workspace && !workspace.hidden && !workspace.querySelector('.broker-data')?.hidden) {
      document.dispatchEvent(new CustomEvent('broker:opened', {detail: {environment: workspace.dataset.environment}}));
    }
  }
  function updateUrl(environment, view) {
    const url = new URL(location.href);
    ['broker', 'broker_error', 'certificate', 'appearance', 'appearance_error', 'portfolio_error', 'sandbox_error'].forEach(key => url.searchParams.delete(key));
    if (environment) { url.searchParams.set('environment', environment); url.searchParams.set('view', view); }
    else { url.searchParams.delete('environment'); url.searchParams.delete('view'); }
    url.hash = '';
    history.replaceState(null, '', url);
  }
  function choose(environment, writeUrl = true) {
    selected = environment;
    workspaces.forEach(workspace => {
      workspace.hidden = workspace.dataset.environment !== environment;
      if (!workspace.hidden) {
        const panel = workspace.querySelector('.broker-connect-panel');
        panel.hidden = workspace.dataset.connected === 'true' && workspace.dataset.formError !== 'true';
        const data = workspace.querySelector('.broker-data');
        if (data) data.hidden = !panel.hidden;
      }
    });
    buttons.forEach(button => {
      const active = button.dataset.broker === environment;
      button.setAttribute('aria-expanded', String(active));
      button.setAttribute('aria-pressed', String(active));
    });
    if (writeUrl) updateUrl(environment, 'portfolio');
    signal(workspaces.find(workspace => !workspace.hidden));
  }
  function closeEditors() {
    editors.forEach(editor => { editor.hidden = true; });
    root.querySelectorAll('[data-appearance]').forEach(button => button.setAttribute('aria-expanded', 'false'));
    previews.forEach(preview => { preview.restore(); });
    previews.clear();
  }
  function openEditor(environment, writeUrl = true) {
    closeEditors();
    const editor = document.getElementById('broker-appearance-' + environment);
    editor.hidden = false;
    root.querySelector('[data-appearance="' + environment + '"]').setAttribute('aria-expanded', 'true');
    if (writeUrl) updateUrl(environment, 'appearance');
  }
  root.addEventListener('click', event => {
    const brokerButton = event.target.closest('[data-broker]');
    if (brokerButton) { closeEditors(); choose(selected === brokerButton.dataset.broker ? null : brokerButton.dataset.broker); return; }
    const edit = event.target.closest('[data-appearance]');
    if (edit) {
      const editor = document.getElementById('broker-appearance-' + edit.dataset.appearance);
      if (editor.hidden) openEditor(edit.dataset.appearance);
      else { closeEditors(); updateUrl(selected, 'portfolio'); }
      return;
    }
    if (event.target.closest('[data-close-appearance]')) { closeEditors(); updateUrl(selected, 'portfolio'); return; }
    const settings = event.target.closest('[data-connection-settings]');
    if (settings) {
      const workspace = settings.closest('.broker-workspace');
      const panel = workspace.querySelector('.broker-connect-panel');
      panel.hidden = !panel.hidden;
      workspace.querySelector('.broker-data').hidden = !panel.hidden;
      settings.textContent = panel.hidden ? 'Настройки подключения' : 'Вернуться к портфелю';
      updateUrl(workspace.dataset.environment, panel.hidden ? 'portfolio' : 'connection');
      signal(workspace);
    }
  });
  function previewFor(editor) {
    const environment = editor.id.replace('broker-appearance-', '');
    const choice = root.querySelector('[data-broker="' + environment + '"]');
    const wrap = choice.closest('.broker-choice-wrap');
    if (!previews.has(environment)) {
      const originalColor = wrap.style.getPropertyValue('--broker-color');
      const originalImage = choice.querySelector('.broker-cover')?.getAttribute('src');
      const preview = {url: null, restore() {
        wrap.style.setProperty('--broker-color', originalColor);
        const image = choice.querySelector('.broker-cover');
        if (originalImage && image) image.src = originalImage;
        else if (image) image.remove();
        if (preview.url) URL.revokeObjectURL(preview.url);
        editor.querySelector('[name="color"]').value = originalColor.trim();
        editor.querySelector('[name="image"]').value = '';
      }};
      previews.set(environment, preview);
    }
    return {choice, wrap, preview: previews.get(environment)};
  }
  root.addEventListener('input', event => {
    if (event.target.name !== 'color') return;
    previewFor(event.target.closest('.broker-appearance')).wrap.style.setProperty('--broker-color', event.target.value);
  });
  root.addEventListener('change', event => {
    if (event.target.name !== 'image') return;
    const file = event.target.files[0];
    if (!file || !['image/png', 'image/jpeg', 'image/webp'].includes(file.type) || file.size > 3 * 1024 * 1024) return;
    const {choice, preview} = previewFor(event.target.closest('.broker-appearance'));
    if (preview.url) URL.revokeObjectURL(preview.url);
    preview.url = URL.createObjectURL(file);
    let image = choice.querySelector('.broker-cover');
    if (!image) { image = document.createElement('img'); image.className = 'broker-cover'; image.alt = ''; choice.prepend(image); }
    image.src = preview.url;
  });
  function followAnchor() {
    const hash = location.hash;
    if (!['#broker-portfolio', '#broker-accounts', '#broker-sandbox'].includes(hash)) return false;
    const preferred = workspaces.find(workspace => workspace.dataset.connected === 'true')?.dataset.environment || 'production';
    const environment = hash === '#broker-sandbox' ? 'sandbox' : valid(root.dataset.initialEnvironment) ? root.dataset.initialEnvironment : preferred;
    choose(environment, false);
    const target = document.getElementById(hash === '#broker-portfolio' ? 'broker-portfolio-' + environment : hash === '#broker-accounts' ? 'broker-accounts-' + environment : 'broker-sandbox');
    requestAnimationFrame(() => (target || document.getElementById('broker-workspace-' + environment)).scrollIntoView({block: 'start'}));
    return true;
  }
  window.addEventListener('hashchange', followAnchor);
  if (!followAnchor() && valid(root.dataset.initialEnvironment)) {
    if (root.dataset.initialView === 'appearance') openEditor(root.dataset.initialEnvironment, false);
    else if (root.dataset.connected !== 'connected') {
      choose(root.dataset.initialEnvironment, false);
      if (root.dataset.initialView === 'connection') root.querySelector('#broker-workspace-' + root.dataset.initialEnvironment + ' [data-connection-settings]')?.click();
    }
  } else if (!valid(root.dataset.initialEnvironment) && root.querySelector('[data-form-error="true"]')) {
    choose(root.querySelector('[data-form-error="true"]').dataset.environment, false);
  }
}());

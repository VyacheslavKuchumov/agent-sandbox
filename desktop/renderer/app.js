'use strict';

(() => {
  const $ = (id) => document.getElementById(id);
  const api = window.sandbox;
  const apiReady = Boolean(api && ['snapshot', 'run', 'logs', 'launch', 'openDashboard'].every((method) => typeof api[method] === 'function'));
  const state = { snapshot: null, snapshotBusy: false, actionBusy: false, logsBusy: false, view: 'overview', lastError: '', logSignature: '' };
  const agentCards = new Map();
  const infraRows = new Map();
  const titles = {
    overview: ['Центр управления', 'Агенты, инфраструктура и диагностика в одном месте.', 'Обзор'],
    agents: ['Агенты', 'Управляйте окружениями и выбирайте инструмент для работы.', 'Агенты'],
    logs: ['Журналы', 'Вывод контейнеров для диагностики и проверки состояния.', 'Журналы'],
    keys: ['Ключи и сервисы', 'Имена настроенных подключений без доступа к значениям секретов.', 'Ключи'],
  };
  const text = (value, fallback = '') => typeof value === 'string' || typeof value === 'number' ? String(value) : fallback;
  const errorText = (error) => error instanceof Error ? error.message : text(error, 'Неизвестная ошибка');
  const list = (value) => Array.isArray(value) ? value : [];
  const objects = (value) => list(value).filter((item) => item && typeof item === 'object' && typeof item.id === 'string' && item.id);
  const toolsFor = (agent) => [...new Set(list(agent.tools).filter((tool) => typeof tool === 'string' && tool))];
  const stateLabels = { running: 'Работает', stopped: 'Остановлен', unavailable: 'Недоступен' };
  const healthLabels = { healthy: 'Исправен', unhealthy: 'Ошибка проверки', starting: 'Проверка запуска' };

  function element(tag, className, content) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (content !== undefined) node.textContent = content;
    return node;
  }

  function statusPill(node, status, health) {
    const unhealthy = health === 'unhealthy';
    const tone = status === 'running' ? (unhealthy ? 'warning' : 'good') : status === 'stopped' ? 'muted' : 'bad';
    node.className = `pill ${tone}`;
    node.textContent = stateLabels[status] || 'Недоступен';
    if (unhealthy && status === 'running') node.textContent = 'Требует внимания';
  }

  function showView(view) {
    if (!titles[view]) return;
    state.view = view;
    document.querySelectorAll('[data-views]').forEach((section) => { section.hidden = !section.dataset.views.split(' ').includes(view); });
    document.querySelectorAll('[data-view]').forEach((button) => {
      const active = button.dataset.view === view;
      button.classList.toggle('active', active);
      if (active) button.setAttribute('aria-current', 'page'); else button.removeAttribute('aria-current');
    });
    $('page-title').textContent = titles[view][0];
    $('page-description').textContent = titles[view][1];
    $('breadcrumb-current').textContent = titles[view][2];
  }

  function showError(message) {
    state.lastError = message;
    $('status-error').hidden = !message;
    $('status-error').textContent = message;
    $('connection-label').textContent = !apiReady ? 'API недоступен' : message ? 'Ошибка состояния' : 'Соединение активно';
    $('connection-dot').className = `status-dot ${message ? 'bad' : 'good'}`;
    $('poll-status').textContent = !apiReady ? 'API недоступен' : message ? 'повтор через 10 сек' : 'опрос состояния';
  }

  function actionButton(label, action, id, name) {
    const button = element('button', `mini-button${action === 'stop' ? ' danger' : ''}`, label);
    button.type = 'button';
    button.dataset.action = action;
    button.setAttribute('aria-label', `${label}: ${name}`);
    button.addEventListener('click', () => execute(`${label}: ${name}`, () => api.run({ action, target: id })));
    return button;
  }

  function createAgent(agent) {
    const card = element('article', 'agent-card');
    const top = element('div', 'agent-top');
    const avatar = element('span', 'agent-avatar');
    avatar.setAttribute('aria-hidden', 'true');
    const name = element('h3');
    const pill = element('span', 'pill muted');
    top.append(avatar, name, pill);
    const description = element('p', 'agent-description');
    const meta = element('div', 'agent-meta');
    const container = element('span');
    const health = element('span');
    meta.append(container, health);
    const toolControl = element('div', 'tool-control');
    const label = element('label', '', 'Инструмент');
    const select = element('select');
    // Generated IDs never contain backend values.
    select.id = `agent-tool-${++createAgent.sequence}`;
    label.htmlFor = select.id;
    toolControl.append(label, select);
    const launch = element('button', 'button launch-button', 'Открыть терминал ↗');
    launch.type = 'button';
    launch.addEventListener('click', () => {
      const current = objects(state.snapshot?.agents).find((item) => item.id === agent.id);
      if (!current || !toolsFor(current).includes(select.value)) return;
      execute(`Терминал: ${text(current.name, current.id)} / ${select.value}`, () => api.launch({ agent: agent.id, tool: select.value }));
    });
    const actions = element('div', 'agent-actions');
    if (agent.id !== 'web') {
      [['Запустить', 'start'], ['Стоп', 'stop'], ['Рестарт', 'restart']].forEach(([title, action]) => actions.append(actionButton(title, action, agent.id, text(agent.name, agent.id))));
    } else {
      actions.append(element('span', 'summary-detail', 'Управление контейнером web недоступно'));
    }
    card.append(top, description, meta, toolControl, launch, actions);
    const record = { node: card, avatar, name, pill, description, container, health, select, launch, actions, toolsSignature: null, data: agent };
    select.addEventListener('change', updateDisabled);
    return record;
  }
  createAgent.sequence = 0;

  function updateAgent(record, agent) {
    record.data = agent;
    const name = text(agent.name, agent.id);
    record.name.textContent = name;
    record.avatar.textContent = name.slice(0, 2).toUpperCase();
    record.description.textContent = text(agent.description, 'Окружение агента');
    record.container.textContent = text(agent.container, 'Контейнер не определён');
    record.health.textContent = agent.health ? `• ${healthLabels[agent.health] || text(agent.health, 'Нет данных')}` : '• Нет данных о здоровье';
    record.health.classList.toggle('health-warning', agent.health === 'unhealthy');
    statusPill(record.pill, agent.state, agent.health);
    const tools = toolsFor(agent);
    const signature = JSON.stringify(tools);
    if (signature !== record.toolsSignature) {
      const previous = record.select.value;
      record.select.replaceChildren();
      (tools.length ? tools : ['']).forEach((tool) => {
        const option = element('option', '', tool || 'Нет инструментов');
        option.value = tool;
        record.select.append(option);
      });
      if (tools.includes(previous)) record.select.value = previous;
      record.toolsSignature = signature;
    }
    record.launch.setAttribute('aria-label', `Открыть терминал: ${name}`);
  }

  function createInfra(item) {
    const node = element('div', 'infra-row');
    const symbol = element('span', 'infra-symbol', '▤');
    symbol.setAttribute('aria-hidden', 'true');
    const info = element('div', 'infra-info');
    const name = element('h3');
    const detail = element('p');
    info.append(name, detail);
    const pill = element('span', 'pill');
    const actions = element('div', 'infra-actions');
    if (['vpn', 'keys'].includes(item.id)) {
      [['Запустить', 'start'], ['Стоп', 'stop'], ['Рестарт', 'restart']].forEach(([title, action]) => actions.append(actionButton(title, action, item.id, text(item.name, item.id))));
    }
    node.append(symbol, info, pill, actions);
    return { node, name, detail, pill, actions, data: item };
  }

  function reconcile(items, records, parent, create, update, emptyMessage) {
    const ids = new Set(items.map((item) => item.id));
    for (const [id, record] of records) {
      if (!ids.has(id)) { record.node.remove(); records.delete(id); }
    }
    if (!records.size) parent.replaceChildren();
    for (const item of items) {
      let record = records.get(item.id);
      if (!record) { record = create(item); records.set(item.id, record); parent.append(record.node); }
      update(record, item);
    }
    if (!items.length) parent.replaceChildren(element('div', 'empty-state', emptyMessage));
  }

  function renderKeys(keys) {
    const parent = $('keys-list');
    const valid = list(keys).filter((item) => item && typeof item === 'object');
    const signature = JSON.stringify(valid);
    if (signature === renderKeys.signature) return;
    renderKeys.signature = signature;
    parent.replaceChildren();
    valid.forEach((item) => {
      const row = element('div', 'key-row');
      const name = element('div', 'key-agent', text(item.agent, 'Агент'));
      const tags = element('div', 'service-tags');
      const services = list(item.services).filter((service) => typeof service === 'string' && service);
      services.forEach((service) => tags.append(element('span', 'service-tag', service)));
      if (!services.length) tags.append(element('span', 'summary-detail', 'Нет настроенных сервисов'));
      row.append(name, tags);
      parent.append(row);
    });
    if (!valid.length) parent.append(element('div', 'empty-state', 'Настроенные ключи не найдены. Здесь отображаются только названия сервисов.'));
  }

  function renderLogTargets(agents, infrastructure) {
    const targets = new Map();
    [...agents, ...infrastructure].forEach((item) => targets.set(item.id, text(item.name, item.id)));
    ['vpn', 'keys', 'mailgw', 'drivegw'].forEach((id) => { if (!targets.has(id)) targets.set(id, id); });
    const signature = JSON.stringify([...targets]);
    if (signature === state.logSignature) return;
    state.logSignature = signature;
    const previous = $('log-target').value;
    $('log-target').replaceChildren();
    const placeholder = element('option', '', 'Выберите источник');
    placeholder.value = '';
    $('log-target').append(placeholder);
    targets.forEach((name, id) => {
      const option = element('option', '', `${name} · ${id}`);
      option.value = id;
      $('log-target').append(option);
    });
    $('log-target').value = targets.has(previous) ? previous : '';
  }

  function renderSnapshot(snapshot) {
    const agents = objects(snapshot.agents);
    const infrastructure = objects(snapshot.infrastructure);
    const runningAgents = agents.filter((item) => item.state === 'running').length;
    const runningInfra = infrastructure.filter((item) => item.state === 'running').length;
    $('demo-banner').hidden = snapshot.demo !== true;
    $('agents-running').textContent = String(runningAgents);
    $('agents-total').textContent = `из ${agents.length} окружений`;
    $('infra-running').textContent = String(runningInfra);
    $('infra-total').textContent = `из ${infrastructure.length} сервисов`;
    const services = new Set(list(snapshot.keys).flatMap((item) => list(item?.services)).filter((item) => typeof item === 'string' && item));
    $('keys-total').textContent = String(services.size);
    $('agent-count').textContent = `${agents.length} окружений`;
    $('workspace-root').textContent = text(snapshot.root, 'Локальная песочница');
    const date = snapshot.updatedAt == null ? null : new Date(snapshot.updatedAt);
    $('updated-at').textContent = date && !Number.isNaN(date.getTime()) ? `Обновлено ${date.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}` : 'Время обновления неизвестно';
    reconcile(agents, agentCards, $('agent-grid'), createAgent, updateAgent, 'Агенты не найдены. Обновите состояние после настройки окружения.');
    reconcile(infrastructure, infraRows, $('infrastructure-list'), createInfra, (record, item) => {
      record.data = item;
      record.name.textContent = text(item.name, item.id);
      record.detail.textContent = [text(item.container, 'Контейнер не определён'), healthLabels[item.health] || text(item.health)].filter(Boolean).join(' · ');
      statusPill(record.pill, item.state, item.health);
    }, 'Сервисы инфраструктуры не найдены.');
    renderKeys(snapshot.keys);
    renderLogTargets(agents, infrastructure);
    const all = [...agents, ...infrastructure];
    const unavailable = all.some((item) => item.state === 'unavailable' || !stateLabels[item.state]);
    const unhealthy = all.some((item) => item.health === 'unhealthy');
    const overall = $('overall-status');
    overall.className = `pill ${snapshot.error || unavailable || unhealthy ? 'warning' : 'good'}`;
    overall.textContent = snapshot.error ? 'Ошибка состояния' : unavailable ? 'Есть недоступные контейнеры' : unhealthy ? 'Требует внимания' : all.length ? 'Состояние получено' : 'Нет контейнеров';
    showError(snapshot.error ? `Не удалось полностью получить состояние. ${text(snapshot.error, 'Docker недоступен.')}` : '');
  }

  function updateDisabled() {
    const busy = state.actionBusy;
    const controlBlocked = !apiReady || !state.snapshot || Boolean(state.lastError) || busy || state.snapshotBusy;
    $('refresh-snapshot').disabled = !apiReady || state.snapshotBusy || busy;
    $('refresh-snapshot').setAttribute('aria-busy', String(state.snapshotBusy));
    for (const record of [...agentCards.values(), ...infraRows.values()]) {
      const running = record.data.state === 'running';
      const available = ['running', 'stopped'].includes(record.data.state);
      record.actions.querySelectorAll('button').forEach((button) => {
        button.disabled = controlBlocked || !available || (button.dataset.action === 'start' ? running : !running);
      });
      if (record.select) {
        record.select.disabled = controlBlocked || !toolsFor(record.data).length || !available;
        record.launch.disabled = controlBlocked || !available || !record.select.value;
        record.launch.title = record.data.id === 'web' ? 'Создать новую сессию Web в отдельном терминале' : 'Открыть CLI в отдельном терминале; окружение запустится при необходимости';
      }
    }
    ['tunnel-button', 'test-button'].forEach((id) => { $(id).disabled = controlBlocked; });
    ['grafana-button', 'network-button'].forEach((id) => { $(id).disabled = !apiReady || busy; });
    $('log-target').disabled = !apiReady || state.logsBusy || !state.snapshot;
    $('refresh-logs').disabled = !apiReady || state.logsBusy || !$('log-target').value;
    $('refresh-logs').textContent = state.logsBusy ? 'Загрузка…' : 'Обновить журнал';
    $('clear-output').disabled = busy;
  }

  async function refreshSnapshot() {
    if (!apiReady || state.snapshotBusy || state.actionBusy) return;
    state.snapshotBusy = true;
    updateDisabled();
    try {
      const snapshot = await api.snapshot();
      if (!snapshot || typeof snapshot !== 'object' || !Array.isArray(snapshot.agents) || !Array.isArray(snapshot.infrastructure) || !Array.isArray(snapshot.keys)) throw new Error('Получен некорректный ответ API состояния.');
      state.snapshot = snapshot;
      renderSnapshot(snapshot);
    } catch (error) {
      showError(`Состояние недоступно. ${errorText(error)}${state.snapshot ? ' Показаны последние полученные данные.' : ' Проверьте доступность Docker и повторите обновление.'}`);
      $('overall-status').className = 'pill bad';
      $('overall-status').textContent = 'Нет актуального состояния';
      if (!state.snapshot) {
        $('agent-grid').replaceChildren(element('div', 'empty-state', 'Список агентов недоступен.'));
        $('infrastructure-list').replaceChildren(element('div', 'empty-state', 'Список сервисов недоступен.'));
        $('keys-list').replaceChildren(element('div', 'empty-state', 'Метаданные ключей недоступны.'));
      }
    } finally {
      state.snapshotBusy = false;
      updateDisabled();
    }
  }

  function formatResult(result) {
    if (!result || typeof result !== 'object' || typeof result.ok !== 'boolean') throw new Error('Получен некорректный результат команды.');
    const parts = [];
    const stdout = text(result.stdout);
    const stderr = text(result.stderr);
    if (stdout) parts.push(stdout);
    if (stderr) parts.push(`STDERR\n${stderr}`);
    if (result.code !== null && result.code !== undefined) parts.push(`Код завершения: ${text(result.code, 'неизвестен')}`);
    return parts.join('\n\n') || (result.ok ? 'Действие завершено без вывода.' : 'Действие не выполнено или отменено в системном диалоге.');
  }

  async function execute(label, operation) {
    if (!apiReady || state.actionBusy) return;
    state.actionBusy = true;
    $('action-notice').hidden = false;
    $('action-message').textContent = `${label} · Выполняется / ожидание подтверждения…`;
    $('command-caption').textContent = label;
    $('command-status').textContent = 'Выполняется / ожидание подтверждения…';
    $('command-output').classList.remove('error');
    $('command-output').textContent = `${label}\nОжидание результата. Если открыт системный диалог, подтвердите или отмените действие.`;
    $('command-output').setAttribute('aria-busy', 'true');
    updateDisabled();
    try {
      const result = await operation();
      const output = formatResult(result);
      const cancelled = !result.ok && (result.cancelled === true || /cancel|отмен/i.test(`${text(result.stdout)} ${text(result.stderr)}`) || (result.code == null && !result.stdout && !result.stderr));
      $('command-status').textContent = result.ok ? 'Выполнено' : cancelled ? 'Действие отменено' : 'Ошибка выполнения';
      $('command-output').classList.toggle('error', !result.ok && !cancelled);
      $('command-output').textContent = `${label}\n\n${output}`;
    } catch (error) {
      const message = errorText(error);
      const cancelled = /cancel|отмен/i.test(message);
      $('command-status').textContent = cancelled ? 'Действие отменено' : 'Ошибка выполнения';
      $('command-output').classList.toggle('error', !cancelled);
      $('command-output').textContent = `${label}\n\n${message}`;
    } finally {
      state.actionBusy = false;
      $('action-message').textContent = `${label} · ${$('command-status').textContent}`;
      $('command-output').setAttribute('aria-busy', 'false');
      updateDisabled();
      void refreshSnapshot();
    }
  }

  async function refreshLogs() {
    const target = $('log-target').value;
    if (!apiReady || state.logsBusy || !target) return;
    const label = $('log-target').selectedOptions[0]?.textContent || target;
    state.logsBusy = true;
    $('log-label').textContent = label;
    $('log-status').textContent = 'Загрузка…';
    $('log-output').setAttribute('aria-busy', 'true');
    updateDisabled();
    try {
      const result = await api.logs(target);
      $('log-output').textContent = formatResult(result);
      $('log-output').classList.toggle('error', !result.ok);
      $('log-status').textContent = result.ok ? `Обновлено ${new Date().toLocaleTimeString('ru-RU')}` : 'Ошибка получения';
    } catch (error) {
      $('log-output').textContent = `Не удалось получить журнал. ${errorText(error)}`;
      $('log-output').classList.add('error');
      $('log-status').textContent = 'Ошибка получения';
    } finally {
      state.logsBusy = false;
      $('log-output').setAttribute('aria-busy', 'false');
      updateDisabled();
    }
  }

  document.querySelectorAll('[data-view]').forEach((button) => button.addEventListener('click', () => showView(button.dataset.view)));
  $('refresh-snapshot').addEventListener('click', refreshSnapshot);
  $('refresh-logs').addEventListener('click', refreshLogs);
  $('log-target').addEventListener('change', () => {
    $('log-label').textContent = $('log-target').selectedOptions[0]?.textContent || 'Источник не выбран';
    $('log-status').textContent = 'Ожидание обновления';
    $('log-output').textContent = 'Нажмите «Обновить журнал», чтобы получить вывод выбранного источника.';
    $('log-output').classList.remove('error');
    updateDisabled();
  });
  $('tunnel-button').addEventListener('click', () => execute('Проверка туннеля', () => api.run({ action: 'tunnel' })));
  $('test-button').addEventListener('click', () => execute('Тест изоляции', () => api.run({ action: 'test' })));
  $('grafana-button').addEventListener('click', () => execute('Открыть Grafana', () => api.openDashboard('grafana')));
  $('network-button').addEventListener('click', () => execute('Открыть сетевой дашборд', () => api.openDashboard('network')));
  $('clear-output').addEventListener('click', () => {
    $('action-notice').hidden = true;
    $('command-output').textContent = 'Здесь появится результат выбранного действия.';
    $('command-output').classList.remove('error');
    $('command-status').textContent = 'Готов к работе';
    $('command-caption').textContent = 'Результаты действий и сообщения об ошибках.';
  });
  $('show-command').addEventListener('click', () => {
    $('command-output').scrollIntoView({ block: 'center' });
    $('command-output').focus({ preventScroll: true });
  });
  showView('overview');
  updateDisabled();
  if (apiReady) {
    void refreshSnapshot();
    const poll = window.setInterval(() => { void refreshSnapshot(); }, 10000);
    window.addEventListener('beforeunload', () => window.clearInterval(poll), { once: true });
  } else {
    showError('API window.sandbox недоступен. Откройте панель в Electron с подключённым preload. Данные и управление недоступны.');
    $('overall-status').className = 'pill bad';
    $('overall-status').textContent = 'API недоступен';
    $('agent-grid').replaceChildren(element('div', 'empty-state', 'Нет соединения с API Electron.'));
    $('infrastructure-list').replaceChildren(element('div', 'empty-state', 'Состояние инфраструктуры недоступно.'));
    $('keys-list').replaceChildren(element('div', 'empty-state', 'Метаданные ключей недоступны.'));
  }
})();

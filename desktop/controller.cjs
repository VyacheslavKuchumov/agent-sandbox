'use strict';

const { spawn } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

const AGENTS = [
  { id: 'coder', name: 'Coder', description: 'Код и проекты', tools: ['claude', 'codex', 'shell'] },
  { id: 'infra', name: 'Infra', description: 'Разработка песочницы', tools: ['claude', 'codex', 'shell'] },
  { id: 'admin', name: 'Admin', description: 'Анализ хоста и предложения', tools: ['claude', 'shell'] },
  { id: 'cad', name: 'CAD', description: 'FreeCAD и Blender', tools: ['claude', 'codex', 'shell'] },
  { id: 'assistant', name: 'Assistant', description: 'Почта и календарь', tools: ['claude'] },
  { id: 'web', name: 'Web', description: 'Исследования, новая сессия на каждый запуск', tools: ['claude', 'codex', 'shell'] },
  { id: 'opencode', name: 'OpenCode', description: 'Модели Zen без ключей', tools: ['opencode', 'shell'] },
];
const INFRA = [
  { id: 'vpn', name: 'VPN' }, { id: 'keys', name: 'Брокер ключей' },
  { id: 'mailgw', name: 'Почтовый шлюз' }, { id: 'drivegw', name: 'Google Drive' },
  { id: 'grafana', name: 'Grafana' }, { id: 'loki', name: 'Loki' }, { id: 'vector', name: 'Vector' },
];
const MANAGED = new Set([...AGENTS.filter(a => a.id !== 'web').map(a => a.id), 'vpn', 'keys']);
const LOG_TARGETS = new Set([...AGENTS.map(a => a.id), ...INFRA.map(s => s.id)]);
const KEY_FILES = ['anthropic', 'github', 'codex-auth.json', 'google.json', 'google-drive.json'];
const DASHBOARDS = Object.freeze({ grafana: 'http://127.0.0.1:3300/', network: 'http://127.0.0.1:9090/ui/' });

function redact(value) {
  return String(value).replace(/\x1b\[[0-?]*[ -/]*[@-~]/g, '')
    .replace(/(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|sk-ant-[A-Za-z0-9_-]+|sk-[A-Za-z0-9_-]{16,})/g, '[REDACTED]')
    .replace(/(Bearer\s+)\S+/gi, '$1[REDACTED]')
    .replace(/(vless:\/\/)\S+/gi, '$1[REDACTED]');
}

// No shell: arguments remain arguments, and callers can only select known operations.
function runProcess(file, args, { cwd, timeout = 30_000, maxBytes = 256 * 1024 } = {}) {
  return new Promise(resolve => {
    let stdout = '', stderr = '', bytes = 0, failure = '', done = false, timer;
    const child = spawn(file, args, { cwd, shell: false, detached: process.platform !== 'win32', stdio: ['ignore', 'pipe', 'pipe'] });
    const stop = () => {
      try { process.platform === 'win32' ? child.kill('SIGKILL') : process.kill(-child.pid, 'SIGKILL'); } catch { /* Already exited. */ }
    };
    const finish = code => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      resolve({ ok: code === 0 && !failure, code: failure ? -1 : (code ?? -1), stdout: redact(stdout), stderr: redact([stderr, failure].filter(Boolean).join('\n')) });
    };
    const append = (channel, chunk) => {
      bytes += Buffer.byteLength(chunk);
      if (bytes > maxBytes) { failure = 'Вывод команды превысил допустимый размер.'; stop(); return; }
      if (channel === 'stdout') stdout += chunk.toString(); else stderr += chunk.toString();
    };
    child.stdout.setEncoding('utf8');
    child.stderr.setEncoding('utf8');
    child.stdout.on('data', chunk => append('stdout', chunk));
    child.stderr.on('data', chunk => append('stderr', chunk));
    child.on('error', error => { failure = error.message; finish(-1); });
    child.on('close', finish);
    timer = setTimeout(() => { failure = 'Превышено время ожидания команды.'; stop(); }, timeout);
  });
}

function parseContainers(text) {
  const value = text.trim();
  if (!value) return [];
  const parsed = value.startsWith('[') ? JSON.parse(value) : value.split('\n').filter(Boolean).map(line => JSON.parse(line));
  if (!Array.isArray(parsed)) throw new Error('Некорректный ответ Docker Compose.');
  return parsed;
}

function createController({ root, demo = false, runner = runProcess, terminalLauncher } = {}) {
  const demoRunning = new Set(['coder', 'infra', 'vpn', 'keys']);
  const compose = args => ['compose', '--project-directory', root, '-f', path.join(root, 'compose.yaml'), ...args];
  const execute = (file, args, options = {}) => runner(file, args, { cwd: root, ...options });
  const success = stdout => ({ ok: true, code: 0, stdout, stderr: '' });
  function plan(request) {
    if (!request || typeof request !== 'object' || Array.isArray(request)) throw new Error('Некорректная операция.');
    const { action, target } = request;
    if (action === 'tunnel' || action === 'test') {
      if (target !== undefined) throw new Error('Для проверки не нужен target.');
      return { file: path.join(root, 'bin/sandbox'), args: [action], timeout: 180_000 };
    }
    if (!['start', 'stop', 'restart'].includes(action) || !MANAGED.has(target)) throw new Error('Недопустимая команда или сервис.');
    if (action === 'start' && AGENTS.some(a => a.id === target)) return { file: path.join(root, 'bin/sandbox'), args: ['up', target], timeout: 600_000 };
    return { file: 'docker', args: compose(action === 'start' ? ['up', '-d', target] : [action, target]), timeout: 120_000 };
  }
  async function snapshot() {
    let containers = [], error = null;
    if (!demo) {
      const result = await execute('docker', compose(['--profile', '*', 'ps', '--all', '--format', 'json']));
      if (!result.ok) error = result.stderr || 'Docker недоступен.';
      else try { containers = parseContainers(result.stdout); } catch (e) { error = e.message; }
    }
    const status = service => {
      const rows = containers.filter(c => c.Service === service);
      const container = rows.find(c => c.State === 'running') || rows[0];
      return {
        state: demo ? (demoRunning.has(service) ? 'running' : 'stopped') : error ? 'unavailable' : container?.State === 'running' ? 'running' : 'stopped',
        health: demo ? (demoRunning.has(service) ? 'healthy' : '') : (container?.Health || ''),
        container: demo ? `demo-${service}` : (container?.Name || ''),
      };
    };
    const configured = file => { try { return fs.statSync(file).isFile() && fs.statSync(file).size > 0; } catch { return false; } };
    const keys = AGENTS.map(agent => ({ agent: agent.id, services: demo ? (['coder', 'infra'].includes(agent.id) ? ['anthropic', 'github', 'codex'] : []) : KEY_FILES.filter(name => {
      const file = name === 'codex-auth.json' && ['coder', 'infra', 'cad'].includes(agent.id) ? path.join(root, 'secrets/keys', name) : path.join(root, 'secrets/keys', agent.id, name);
      return configured(file);
    }).map(name => name.replace('-auth.json', '').replace('.json', '')) }));
    return { root, demo, updatedAt: new Date().toISOString(), error, agents: AGENTS.map(a => ({ ...a, ...status(a.id) })), infrastructure: INFRA.map(s => ({ ...s, ...status(s.id) })), keys };
  }
  async function run(request) {
    const command = plan(request);
    if (demo) {
      if (request.action === 'start' || request.action === 'restart') demoRunning.add(request.target);
      if (request.action === 'stop') demoRunning.delete(request.target);
      return success('Деморежим: операция показана без выполнения системных команд.');
    }
    return execute(command.file, command.args, { timeout: command.timeout });
  }
  async function logs(target) {
    if (!LOG_TARGETS.has(target)) throw new Error('Недопустимый источник логов.');
    if (demo) return success(`[DEMO] ${target}: сервис готов\n[DEMO] Логи настоящих контейнеров в деморежиме не читаются.`);
    return execute('docker', compose(['--profile', '*', 'logs', '--no-color', '--tail', '150', target]));
  }
  function validateLaunch(request) {
    const agent = AGENTS.find(a => a.id === request?.agent);
    if (!agent || !agent.tools.includes(request.tool)) throw new Error('Недопустимый агент или инструмент.');
    return { file: path.join(root, 'bin/agent'), args: [agent.id, request.tool] };
  }
  async function launch(request) {
    const command = validateLaunch(request);
    if (demo) return success('Деморежим: терминал агента не запускается.');
    if (terminalLauncher) return terminalLauncher(command, root);
    const candidates = [
      ['konsole', ['--workdir', root, '-e']],
      ['gnome-terminal', ['--working-directory', root, '--']],
      ['xfce4-terminal', ['--working-directory', root, '--disable-server', '--execute']],
      ['xterm', ['-e']], ['x-terminal-emulator', ['-e']],
    ];
    const directories = (process.env.PATH || '').split(path.delimiter);
    const candidate = candidates.find(([name]) => directories.some(dir => { try { fs.accessSync(path.join(dir, name), fs.constants.X_OK); return true; } catch { return false; } }));
    if (!candidate) return { ok: false, code: -1, stdout: '', stderr: 'Не найден терминал. Установите Konsole, GNOME Terminal, XFCE Terminal или xterm.' };
    // Terminal clients can remain attached until the agent exits. Only await spawn.
    return new Promise(resolve => {
      const child = spawn(candidate[0], [...candidate[1], command.file, ...command.args], { cwd: root, detached: true, stdio: 'ignore', shell: false });
      child.once('error', e => resolve({ ok: false, code: -1, stdout: '', stderr: e.message }));
      child.once('spawn', () => { child.unref(); resolve(success('Запрос на открытие терминала отправлен.')); });
    });
  }
  return { snapshot, run, logs, launch, plan, validateLaunch, demo };
}

module.exports = { createController, runProcess, parseContainers, redact, AGENTS, INFRA, DASHBOARDS };

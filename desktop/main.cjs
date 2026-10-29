'use strict';
const { app, BrowserWindow, ipcMain, dialog, shell, session } = require('electron');
const path = require('node:path');
const fs = require('node:fs');
const { pathToFileURL } = require('node:url');
const { createController, DASHBOARDS } = require('./controller.cjs');

const demo = process.argv.includes('--demo');
const smoke = process.argv.includes('--smoke');
const root = path.resolve(process.env.AGENT_SANDBOX_ROOT || path.join(__dirname, '..'));
const page = path.join(__dirname, 'renderer/index.html');
const pageURL = pathToFileURL(page).href;
let window, busy = false;
const controller = createController({ root, demo });
const failed = error => ({ ok: false, code: -1, stdout: '', stderr: error.message });

function assertSender(event) {
  if (!window || event.sender !== window.webContents || event.senderFrame !== window.webContents.mainFrame || event.senderFrame.url !== pageURL) throw new Error('Недоверенный источник IPC.');
}
async function mutation(event, validate, task, label) {
  assertSender(event);
  if (busy) return failed(new Error('Дождитесь завершения текущей операции.'));
  busy = true;
  try {
    validate();
    if (!demo) {
      const choice = await dialog.showMessageBox(window, {
        type: 'question', buttons: ['Отмена', 'Выполнить'], defaultId: 0, cancelId: 0,
        title: 'Управление песочницей', message: label,
        detail: `Операция будет выполнена на хосте для проекта:\n${root}`,
      });
      if (choice.response !== 1) return { ok: false, cancelled: true, code: -1, stdout: '', stderr: 'Операция отменена.' };
    }
    return await task();
  } catch (e) { return failed(e); } finally { busy = false; }
}

app.whenReady().then(async () => {
  if (!demo && (!fs.existsSync(path.join(root, 'bin/sandbox')) || !fs.existsSync(path.join(root, 'compose.yaml')))) {
    dialog.showErrorBox('Песочница не найдена', 'Запустите приложение из репозитория или задайте AGENT_SANDBOX_ROOT.');
    app.quit(); return;
  }
  session.defaultSession.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  session.defaultSession.setPermissionCheckHandler(() => false);
  window = new BrowserWindow({
    width: 1440, height: 940, minWidth: 880, minHeight: 650,
    title: 'Agent Sandbox', backgroundColor: '#101215', autoHideMenuBar: true,
    webPreferences: { preload: path.join(__dirname, 'preload.cjs'), nodeIntegration: false, contextIsolation: true, sandbox: true, webSecurity: true, webviewTag: false, backgroundThrottling: !smoke },
  });
  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  window.webContents.on('will-navigate', event => event.preventDefault());
  window.webContents.on('will-attach-webview', event => event.preventDefault());
  window.on('close', event => {
    if (busy) {
      event.preventDefault();
      dialog.showMessageBox(window, { type: 'info', message: 'Дождитесь завершения операции перед закрытием панели.' });
    }
  });
  ipcMain.handle('sandbox:snapshot', event => { assertSender(event); return controller.snapshot(); });
  ipcMain.handle('sandbox:logs', async (event, target) => { assertSender(event); try { return await controller.logs(target); } catch (e) { return failed(e); } });
  ipcMain.handle('sandbox:run', (event, request) => mutation(event, () => controller.plan(request), () => controller.run(request), `Выполнить «${request?.action}»${request?.target ? ` для ${request.target}` : ''}?`));
  ipcMain.handle('sandbox:launch', (event, request) => mutation(event, () => controller.validateLaunch(request), () => controller.launch(request), `Открыть терминал ${request?.agent} / ${request?.tool}?`));
  ipcMain.handle('sandbox:dashboard', async (event, target) => {
    assertSender(event);
    if (!Object.hasOwn(DASHBOARDS, target)) return failed(new Error('Недопустимый dashboard.'));
    if (demo) return { ok: true, code: 0, stdout: 'Деморежим: браузер не открывается.', stderr: '' };
    try { await shell.openExternal(DASHBOARDS[target]); return { ok: true, code: 0, stdout: 'Открыто в браузере.', stderr: '' }; } catch (e) { return failed(e); }
  });
  await window.loadFile(page);
  if (smoke) {
    const watchdog = setTimeout(() => { console.error('Electron smoke timed out'); app.exit(1); }, 30_000);
    try {
      if (!demo) throw new Error('Smoke допускается только в деморежиме.');
      await new Promise(resolve => setTimeout(resolve, 1200));
      const result = await window.webContents.executeJavaScript(`(async () => {
        const data = await window.sandbox.snapshot();
        const rejected = await window.sandbox.run({action:'stop', target:'coder; invalid'});
        await window.sandbox.run({action:'stop', target:'coder'});
        const stopped = (await window.sandbox.snapshot()).agents.find(a => a.id === 'coder').state;
        await window.sandbox.run({action:'start', target:'coder'});
        const log = await window.sandbox.logs('vpn');
        const launch = await window.sandbox.launch({agent:'coder', tool:'codex'});
        const body = document.body.innerText;
        return { agents: data.agents.length, demo: data.demo, body, stopped, logOK: log.ok,
          launchOK: launch.ok, rejected: rejected.ok === false, bridge: !!window.sandbox,
          nodeExposed: typeof window.require !== 'undefined' };
      })()`);
      if (result.agents !== 7 || !result.demo || !result.bridge || result.nodeExposed || !result.body.includes('Coder') || result.stopped !== 'stopped' || !result.logOK || !result.launchOK || !result.rejected) throw new Error(`Smoke failed: ${JSON.stringify(result)}`);
      console.log('Electron smoke: IPC passed, checking UI interactions');
      await window.webContents.executeJavaScript(`(async () => {
        const wait = async predicate => {
          const until = Date.now() + 3000;
          while (!predicate()) { if (Date.now() > until) throw new Error('UI did not settle'); await new Promise(r => setTimeout(r, 20)); }
        };
        document.querySelector('[data-view="logs"]').click();
        if (document.querySelector('[data-views="logs"]').hidden) throw new Error('Logs navigation failed');
        const target = document.getElementById('log-target'); target.value = 'vpn'; target.dispatchEvent(new Event('change'));
        document.getElementById('refresh-logs').click();
        await wait(() => document.getElementById('log-output').textContent.includes('[DEMO] vpn'));
        document.querySelector('[data-view="keys"]').click();
        if (document.querySelector('[data-views="keys"]').hidden || document.querySelectorAll('.key-row').length !== 7) throw new Error('Key metadata navigation failed');
        document.querySelector('[data-view="agents"]').click();
        const card = [...document.querySelectorAll('.agent-card')].find(c => c.querySelector('h3').textContent === 'Coder');
        card.querySelector('[data-action="stop"]').click();
        await wait(() => card.querySelector('.pill').textContent === 'Остановлен' && !card.querySelector('[data-action="start"]').disabled);
        card.querySelector('[data-action="start"]').click();
        await wait(() => card.querySelector('.pill').textContent === 'Работает' && !card.querySelector('[data-action="stop"]').disabled);
        document.querySelector('[data-view="overview"]').click(); window.scrollTo(0,0);
      })()`);
      const artifacts = path.join(__dirname, 'artifacts'); fs.mkdirSync(artifacts, { recursive: true });
      fs.writeFileSync(path.join(artifacts, 'demo.png'), (await window.webContents.capturePage()).toPNG());
      window.setSize(900, 700);
      await new Promise(resolve => setTimeout(resolve, 150));
      const overflow = await window.webContents.executeJavaScript('document.documentElement.scrollWidth > window.innerWidth');
      if (overflow) throw new Error('Compact layout overflows horizontally');
      fs.writeFileSync(path.join(artifacts, 'demo-compact.png'), (await window.webContents.capturePage()).toPNG());
      console.log('Electron smoke PASS: isolated renderer, IPC validation, logs/keys navigation, demo stop/start, compact layout; screenshots artifacts/demo*.png');
      clearTimeout(watchdog);
      app.exit(0);
    } catch (e) { clearTimeout(watchdog); console.error(e); app.exit(1); }
  }
}).catch(error => { console.error(error); app.exit(1); });
app.on('window-all-closed', () => app.quit());

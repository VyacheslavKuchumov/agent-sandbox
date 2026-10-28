'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { createController, runProcess, parseContainers, redact } = require('../controller.cjs');
const root = '/tmp/project with spaces';
const good = stdout => ({ ok: true, code: 0, stdout, stderr: '' });

test('snapshot maps actual Compose state, including transient web sessions', async () => {
  const controller = createController({ root, runner: async (file, args) => {
    assert.equal(file, 'docker');
    assert.deepEqual(args.slice(-4), ['ps', '--all', '--format', 'json']);
    return good(JSON.stringify([
      { Service: 'coder', Name: 'sbx-agent-coder', State: 'running', Health: 'healthy' },
      { Service: 'web', Name: 'old-web', State: 'exited' },
      { Service: 'web', Name: 'active-web', State: 'running' },
      { Service: 'vpn', Name: 'sbx-vpn', State: 'exited' },
    ]));
  } });
  const snapshot = await controller.snapshot();
  assert.equal(snapshot.agents.find(a => a.id === 'coder').state, 'running');
  assert.equal(snapshot.agents.find(a => a.id === 'web').container, 'active-web');
  assert.equal(snapshot.infrastructure.find(a => a.id === 'vpn').state, 'stopped');
});

test('unavailable Docker and malformed responses produce explicit unavailable state', async () => {
  for (const result of [{ ok: false, stdout: '', stderr: 'Cannot connect', code: 1 }, good('not json')]) {
    const snapshot = await createController({ root, runner: async () => result }).snapshot();
    assert.ok(snapshot.error);
    assert.ok(snapshot.agents.every(a => a.state === 'unavailable'));
  }
});

test('supports both Compose JSON formats', () => {
  assert.equal(parseContainers('{"Service":"coder"}\n{"Service":"vpn"}').length, 2);
  assert.deepEqual(parseContainers('[]'), []);
  assert.deepEqual(parseContainers(''), []);
});

test('only allowed operations are translated to exact argument arrays', async () => {
  const calls = [];
  const controller = createController({ root, runner: async (...args) => { calls.push(args); return good('done'); } });
  await controller.run({ action: 'start', target: 'coder' });
  assert.deepEqual(calls[0].slice(0, 2), [path.join(root, 'bin/sandbox'), ['up', 'coder']]);
  await controller.run({ action: 'stop', target: 'infra' });
  assert.deepEqual(calls[1][1], ['compose', '--project-directory', root, '-f', path.join(root, 'compose.yaml'), 'stop', 'infra']);
  for (const request of [{ action: 'stop', target: 'web' }, { action: 'start', target: '--privileged' }, { action: 'restart', target: 'coder; touch /tmp/pwned' }, { action: 'apply' }, { action: 'test', target: 'coder' }, null]) {
    await assert.rejects(controller.run(request));
  }
  assert.equal(calls.length, 2);
});

test('logs cannot select arbitrary containers or inject flags', async () => {
  let calls = 0;
  const controller = createController({ root, runner: async (file, args) => {
    calls++; assert.deepEqual(args.slice(-5), ['logs', '--no-color', '--tail', '150', 'keys']); return good('log');
  } });
  await controller.logs('keys');
  await assert.rejects(controller.logs('--follow'));
  await assert.rejects(controller.logs('/etc/shadow'));
  assert.equal(calls, 1);
});

test('demo performs no commands, terminal launches, or real key inspection', async () => {
  const controller = createController({ root: '/nonexistent', demo: true, runner: () => { throw new Error('Unexpected command'); }, terminalLauncher: () => { throw new Error('Unexpected launch'); } });
  assert.equal((await controller.snapshot()).demo, true);
  await controller.run({ action: 'stop', target: 'coder' });
  assert.equal((await controller.snapshot()).agents.find(a => a.id === 'coder').state, 'stopped');
  await controller.logs('vpn');
  await controller.launch({ agent: 'web', tool: 'codex' });
  await assert.rejects(controller.run({ action: 'shell', target: 'coder' }));
});

test('key inventory returns service names, including shared Codex, without values', async t => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'sandbox-keys-test-'));
  t.after(() => fs.rmSync(temp, { recursive: true, force: true }));
  fs.mkdirSync(path.join(temp, 'secrets/keys/coder'), { recursive: true });
  fs.writeFileSync(path.join(temp, 'secrets/keys/coder/github'), 'private-test-token');
  fs.writeFileSync(path.join(temp, 'secrets/keys/codex-auth.json'), 'private-test-codex');
  const snapshot = await createController({ root: temp, runner: async () => good('[]') }).snapshot();
  assert.deepEqual(snapshot.keys.find(a => a.agent === 'coder').services, ['github', 'codex']);
  assert.ok(snapshot.keys.find(a => a.agent === 'infra').services.includes('codex'));
  assert.ok(!JSON.stringify(snapshot).includes('private-test'));
});

test('terminal launch uses exact agent/tool allowlist', async () => {
  let command;
  const controller = createController({ root, terminalLauncher: async c => { command = c; return good('launched'); } });
  await controller.launch({ agent: 'coder', tool: 'codex' });
  assert.deepEqual(command, { file: path.join(root, 'bin/agent'), args: ['coder', 'codex'] });
  await assert.rejects(controller.launch({ agent: 'assistant', tool: 'shell' }));
  await assert.rejects(controller.launch({ agent: 'coder', tool: 'codex --dangerous' }));
});

test('process runner treats arguments literally and surfaces exit failures', async () => {
  const literal = '$(touch /tmp/should-not-exist); test';
  const result = await runProcess(process.execPath, ['-e', 'process.stdout.write(process.argv[1]); process.exitCode=7', literal]);
  assert.equal(result.stdout, literal);
  assert.equal(result.ok, false);
  assert.equal(result.code, 7);
});

test('process runner returns missing executable errors', async () => {
  const result = await runProcess('/missing-sandbox-test-command', []);
  assert.equal(result.ok, false);
  assert.match(result.stderr, /ENOENT/);
});

test('process runner preserves UTF-8 characters split across pipe chunks', async () => {
  const result = await runProcess(process.execPath, ['-e', 'process.stdout.write(Buffer.from([0xd0])); setTimeout(()=>process.stdout.write(Buffer.from([0x96])),30)']);
  assert.equal(result.stdout, 'Ж');
});

test('process runner bounds runtime and output', async () => {
  const timeout = await runProcess(process.execPath, ['-e', 'setInterval(()=>{},1000)'], { timeout: 80 });
  assert.equal(timeout.ok, false);
  assert.match(timeout.stderr, /время/);
  const overflow = await runProcess(process.execPath, ['-e', 'process.stdout.write("x".repeat(10000))'], { maxBytes: 100 });
  assert.equal(overflow.ok, false);
  assert.match(overflow.stderr, /размер/);
});

test('common credential forms and ANSI escape sequences are redacted', () => {
  const value = redact('\x1b[31mghp_secretToken Bearer secret-token vless://secret@example.com\x1b[0m');
  assert.ok(!value.includes('secret'));
  assert.ok(!value.includes('\x1b'));
});

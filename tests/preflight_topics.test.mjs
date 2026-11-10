import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { test } from 'node:test';

const path = process.env.WEBSHELL_SOURCE || new URL('../../../apps/webshell/server.mjs', import.meta.url);
const source = readFileSync(path, 'utf8');
function fn(name) {
  const start = source.indexOf(`function ${name}(`);
  assert.notEqual(start, -1, name);
  const next = source.indexOf('\nfunction ', start + 1);
  assert.notEqual(next, -1, name);
  return source.slice(start, next);
}
function harness(running = true) {
  const context = vm.createContext({
    agentDiagnostics: () => ({ gateway: { status: running ? 'running' : 'stopped' }, sessions: { bloatedCount: 0 }, forbiddenProcesses: [] }),
    ROLE_SKILL_POLICY: { default: { allowed: [], discouraged: ['browser', 'payment', 'media-gen'] } },
    promptRoutingSummary: () => ({}),
    FORBIDDEN_PATTERNS: ['openclaw'], MAX_WEB_PROMPT_CHARS: 50000, BLOAT_TOKEN_LIMIT: 20000,
  });
  vm.runInContext(['preflightSummary', 'promptRiskPreview', 'promptRoleRisk', 'preflightError', 'runGuardSnapshot'].map(fn).join('\n'), context);
  return context;
}
test('business discussion, drafts, mixed topics and negated actions are admitted', () => {
  const h = harness();
  for (const text of [
    'Стратегия продаж: оплата, заказ, доставка, курьер и касса',
    'Напиши лендинг и текст сайта, цену и варианты оплаты',
    'Никаких оплат и заказов: нужен только анализ и план',
    'Сравни Stripe, Prodamus, invoice и delivery',
    'Описание видео и изображений для Telegram',
    'Как работает браузер Chrome и click?',
  ]) {
    const result = h.preflightSummary('marketer', text);
    assert.equal(result.ok, true, text);
    assert.equal(result.promptRisk.blockers.length, 0);
    assert.equal(result.roleRisk.blockers.length, 0);
    assert.equal(h.runGuardSnapshot(result).failedChecks.length, 0);
  }
});
test('topic detection and mismatch warnings remain visible', () => {
  const h = harness();
  const result = h.preflightSummary('marketer', 'Оплата и браузер');
  assert.ok(result.promptRisk.hits.includes('payment'));
  assert.ok(result.promptRisk.warnings.includes('browser'));
  assert.ok(result.roleRisk.warnings.includes('payment'));
});
test('runtime readiness still rejects and reports a truthful 503', () => {
  const h = harness(false);
  const result = h.preflightSummary('marketer', 'Оплата и доставка');
  assert.equal(result.ok, false);
  const error = h.preflightError(result);
  assert.equal(error.statusCode, 503);
  assert.equal(error.message, 'preflight failed: Gateway is not running');
  assert.equal(error.checks.join(','), 'gateway');
  assert.equal(error.code, 'PREFLIGHT_REJECTED');
});
test('existing explicit legacy rejection remains a 422, not an HTTP 500', () => {
  const h = harness();
  const result = h.preflightSummary('marketer', 'OpenClaw');
  assert.equal(result.ok, false);
  assert.equal(h.preflightError(result).statusCode, 422);
  assert.equal(h.preflightError(result).message, 'preflight failed: Prompt explicitly asks for OpenClaw');
});

test('actual HTTP handler preserves success, admission errors, unexpected errors and auth', async () => {
  const start = source.indexOf('const server = http.createServer(');
  const end = source.indexOf('\nserver.listen(', start);
  assert.ok(start >= 0 && end > start);
  const h = harness();
  const request = { method: 'POST', url: '/api/runs', headers: { host: '127.0.0.1:8787' } };
  for (const state of ['ok', 'unavailable', 'legacy', 'unexpected', 'unauthorized']) {
    let response;
    let calls = 0;
    Object.assign(h, {
      URL, http: { createServer: (callback) => { h.handler = callback; } },
      handleAccessToken: () => state !== 'unauthorized',
      isSafeMutationOrigin: () => true,
      readBody: async () => ({ profile: 'marketer', message: 'Оплата, заказ и доставка', toolMode: 'quick' }),
      startRun: (body) => {
        calls++;
        assert.equal(body.toolMode, 'quick');
        if (state === 'unavailable') throw h.preflightError({ checks: [{ id: 'gateway', ok: false, blocking: true, failLabel: 'Gateway is not running' }] });
        if (state === 'legacy') throw h.preflightError({ checks: [{ id: 'legacy-prompt', ok: false, blocking: true, failLabel: 'Prompt explicitly asks for OpenClaw' }] });
        if (state === 'unexpected') throw new Error('unexpected test failure');
        return { id: 'diagnostic', sessionId: 'diagnostic-session', status: 'running' };
      },
      sendJson: (_res, status, body) => { response = { status, body }; },
    });
    vm.runInContext(source.slice(start, end).replace('const server =', 'globalThis.server ='), h);
    await h.handler(request, { setHeader() {} });
    if (state === 'unauthorized') {
      assert.equal(calls, 0);
      assert.equal(response, undefined);
      continue;
    }
    assert.equal(response.status, { ok: 202, unavailable: 503, legacy: 422, unexpected: 500 }[state]);
    if (['unavailable', 'legacy'].includes(state)) assert.equal(response.body.code, 'PREFLIGHT_REJECTED');
  }
});

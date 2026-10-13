const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../app/static/js/portal_treinamento_heartbeat.js'), 'utf8');

function environment({hidden = false, config = true, fetchImpl} = {}) {
    let now = 0, next = 0;
    const timers = new Map(), requests = [];
    function target() {
        const listeners = new Map();
        return {
            listeners,
            addEventListener(type, fn) {
                if (!listeners.has(type)) listeners.set(type, new Set());
                listeners.get(type).add(fn);
            },
            removeEventListener(type, fn) { listeners.get(type)?.delete(fn); },
            emit(type, event = {}) { for (const fn of listeners.get(type) || []) fn(event); },
        };
    }
    const document = Object.assign(target(), {
        hidden,
        querySelector: () => config ? {dataset: {
            oneHeartbeatUrl: '/aprender/t/test/modulo/1/heartbeat', csrfToken: 'csrf-test',
        }} : null,
    });
    const window = Object.assign(target(), {
        setTimeout(fn, delay) { const id = ++next; timers.set(id, {fn, at: now + delay}); return id; },
        clearTimeout(id) { timers.delete(id); },
        fetch(url, options) {
            requests.push({url, options});
            return fetchImpl ? fetchImpl() : Promise.resolve({ok: true});
        },
    });
    const context = vm.createContext({window, document, performance: {now: () => now}, AbortController});
    const initialize = () => vm.runInContext(source, context);
    initialize();
    return {
        window, document, timers, requests, initialize,
        async advance(ms) {
            const end = now + ms;
            while (true) {
                const entry = [...timers].sort((a, b) => a[1].at - b[1].at)[0];
                if (!entry || entry[1].at > end) break;
                now = entry[1].at;
                timers.delete(entry[0]);
                entry[1].fn();
                await new Promise(resolve => setImmediate(resolve));
            }
            now = end;
        },
        visible(value) { document.hidden = !value; document.emit('visibilitychange'); },
    };
}

test('visible + active sends fixed payload, CSRF and same-origin cookies', async () => {
    const env = environment();
    await env.advance(14999);
    assert.equal(env.requests.length, 0);
    await env.advance(1);
    const {url, options} = env.requests[0];
    assert.equal(url, '/aprender/t/test/modulo/1/heartbeat');
    assert.equal(options.method, 'POST');
    assert.equal(options.body, '{"segundos":15}');
    assert.equal(options.headers['X-CSRFToken'], 'csrf-test');
    assert.equal(options.headers['Content-Type'], 'application/json');
    assert.equal(options.credentials, 'same-origin');
    for (let i = 0; i < 8; i++) {
        env.document.emit('mousemove');
        await env.advance(15000);
    }
    assert.equal(env.requests.length, 9);
});

test('hidden never sends, including activity events; return waits full interval', async () => {
    const env = environment();
    await env.advance(14000);
    env.visible(false);
    env.document.emit('keydown');
    await env.advance(120000);
    assert.equal(env.requests.length, 0);
    env.visible(true);
    await env.advance(14999);
    assert.equal(env.requests.length, 0);
    await env.advance(1);
    assert.equal(env.requests.length, 1);
    assert.equal(env.requests[0].options.body, '{"segundos":15}');
});

test('initially hidden has no timer', async () => {
    const env = environment({hidden: true});
    await env.advance(60000);
    assert.equal(env.requests.length, 0);
    assert.equal(env.timers.size, 0);
});

test('60s inactivity stops; each supported event resumes after 15s', async () => {
    for (const event of ['mousemove', 'mousedown', 'keydown', 'touchstart', 'scroll']) {
        const env = environment();
        await env.advance(120000);
        assert.equal(env.requests.length, 3);
        assert.equal(env.timers.size, 0);
        env.document.emit(event);
        await env.advance(14999);
        assert.equal(env.requests.length, 3);
        await env.advance(1);
        assert.equal(env.requests.length, 4);
    }
});

test('HTTP and network failures remain silent and allow later heartbeats and submit', async () => {
    for (const fetchImpl of [() => Promise.resolve({ok: false, status: 500}),
                            () => Promise.reject(new Error('network')),
                            () => { throw new Error('sync'); }]) {
        const env = environment({fetchImpl});
        await env.advance(15000);
        let submitted = false;
        env.document.addEventListener('submit', () => { submitted = true; });
        env.document.emit('submit', {preventDefault() { assert.fail('submit intercepted'); }});
        assert.equal(submitted, true);
        env.document.emit('keydown');
        await env.advance(15000);
        assert.equal(env.requests.length, 2);
    }
});

test('reinitialization keeps one timer and one listener per activity event', async () => {
    const env = environment();
    env.initialize();
    env.initialize();
    assert.equal(env.timers.size, 1);
    for (const event of ['mousemove', 'mousedown', 'keydown', 'touchstart', 'scroll', 'visibilitychange']) {
        assert.equal(env.document.listeners.get(event).size, 1);
    }
    assert.equal(env.document.listeners.has('submit'), false);
    assert.equal(env.document.listeners.has('click'), false);
    await env.advance(15000);
    assert.equal(env.requests.length, 1);
});

test('missing module config does not install controller or timer', async () => {
    const env = environment({config: false});
    await env.advance(120000);
    assert.equal(env.requests.length, 0);
    assert.equal(env.timers.size, 0);
    assert.equal(env.window.oneTreinamentoHeartbeat, undefined);
});

test('pending fetch does not overlap; old response cannot restart destroyed controller', async () => {
    let resolve;
    const env = environment({fetchImpl: () => new Promise(r => { resolve = r; })});
    await env.advance(15000);
    const oldSignal = env.requests[0].options.signal;
    env.visible(false);
    env.visible(true);
    await env.advance(30000);
    assert.equal(env.requests.length, 1);
    env.initialize();
    assert.equal(oldSignal.aborted, true);
    resolve({ok: true});
    await Promise.resolve();
    assert.equal(env.timers.size, 1);
});

test('pagehide stops and pageshow resumes for restored pages', async () => {
    const env = environment();
    env.window.emit('pagehide');
    await env.advance(120000);
    assert.equal(env.requests.length, 0);
    env.window.emit('pageshow');
    await env.advance(15000);
    assert.equal(env.requests.length, 1);
});

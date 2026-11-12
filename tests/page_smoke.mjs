// Smoke test for pihub/page/app.js without a browser.
//
//   node tests/page_smoke.mjs <payload.json>
//   node tests/page_smoke.mjs --draw <payload.json> [text ...]
//
// With --draw, only the drawing is checked: every page in three situations must
// come out without an exception and without inline styles, and every given text
// must show up on some page. That is for payloads of machines where parts of the
// scan could not be read, or where there is nothing yet (tests/test_portable.py).
//
// The payload is a real answer of payload() (tests/test_page.py makes it).
// The script is loaded with node:vm into a context with a small stand-in for
// the DOM and a fake fetch. Every page is drawn in three situations (display
// only, not logged in, logged in), the main buttons are clicked, and any
// exception fails the run. It does NOT show how anything looks.
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const SCRIPT = fs.readFileSync(path.join(here, '..', 'pihub', 'page', 'app.js'), 'utf8');
const DRAW = process.argv[2] === '--draw';
const PAYLOAD = JSON.parse(fs.readFileSync(process.argv[DRAW ? 3 : 2], 'utf8'));
const T = PAYLOAD.texts;
const tx = (key, params = {}) => Object.entries(params).reduce((s, [k, v]) => s.replaceAll('{' + k + '}', String(v)), T[key]);

process.on('unhandledRejection', (e) => { console.error('unhandled rejection:', e); process.exit(1); });
process.on('uncaughtException', (e) => { console.error('uncaught exception:', e); process.exit(1); });

// ---- a small stand-in for the DOM ----------------------------------------------

class FakeText {
  constructor(data) { this.nodeType = 3; this.data = data; this.parentNode = null; }
  get textContent() { return this.data; }
  remove() { this.parentNode?.removeChild(this); }
}

let rectCounter = 0;
class FakeElement {
  constructor(tag, doc, ns) {
    this.nodeType = 1; this.tagName = tag; this.ns = ns || 'html'; this.doc = doc;
    this.attrs = {}; this.children = []; this.parentNode = null; this.listeners = {};
    this.style = {}; this.dataset = {}; this.id = ''; this.value = ''; this.rect = null;
  }
  get className() { return this.attrs.class || ''; }
  set className(v) { this.attrs.class = String(v); }
  get classList() {
    const self = this;
    const list = () => self.className.split(/\s+/).filter(Boolean);
    return { contains: (c) => list().includes(c), add: (c) => { self.className = [...new Set([...list(), c])].join(' '); },
      remove: (c) => { self.className = list().filter((x) => x !== c).join(' '); } };
  }
  get disabled() { return 'disabled' in this.attrs; }
  set disabled(v) { if (v) this.attrs.disabled = ''; else delete this.attrs.disabled; }
  get open() { return 'open' in this.attrs; }
  set open(v) { if (v) this.attrs.open = ''; else delete this.attrs.open; }
  get isConnected() { let n = this; while (n.parentNode) n = n.parentNode; return n === this.doc.documentElement; }
  get scrollHeight() { return 600; }
  get textContent() { return this.children.map((c) => c.textContent).join(''); }
  set innerHTML(v) { throw new Error('innerHTML is not allowed'); }
  set outerHTML(v) { throw new Error('outerHTML is not allowed'); }
  insertAdjacentHTML() { throw new Error('insertAdjacentHTML is not allowed'); }
  setAttribute(k, v) { this.attrs[k] = String(v); if (k === 'id') this.id = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  hasAttribute(k) { return k in this.attrs; }
  removeAttribute(k) { delete this.attrs[k]; }
  _adopt(n) {
    if (typeof n === 'string') n = new FakeText(n);
    if (!n || !n.nodeType) throw new Error('append: not a node: ' + String(n));
    n.parentNode?.removeChild(n);
    n.parentNode = this;
    return n;
  }
  append(...nodes) { for (const n of nodes) this.children.push(this._adopt(n)); }
  replaceChildren(...nodes) {
    for (const c of this.children) c.parentNode = null;
    this.children = [];
    this.append(...nodes);
  }
  removeChild(n) { this.children = this.children.filter((c) => c !== n); n.parentNode = null; }
  remove() { this.parentNode?.removeChild(this); }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  async dispatch(type, extra = {}) {
    const event = { type, target: this, currentTarget: this, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, ...extra };
    for (const fn of this.listeners[type] || []) await fn(event);
    return event;
  }
  focus() { this.doc.activeElement = this; }
  showModal() { this.attrs.open = ''; }
  close() { if (!this.open) return; delete this.attrs.open; this.dispatch('close'); }
  getBoundingClientRect() {
    if (!this.rect) { const n = rectCounter++; this.rect = { left: 20 + (n % 3) * 150, top: 30 + Math.floor(n / 3) * 90, width: 140, height: 70 }; }
    const r = this.rect;
    return { ...r, right: r.left + r.width, bottom: r.top + r.height, x: r.left, y: r.top };
  }
  matches(simple) {
    for (const m of simple.matchAll(/^([a-z0-9]+)|\.([\w-]+)|#([\w-]+)|\[([\w-]+)(?:="([^"]*)")?\]/gi)) {
      if (m[1] && m[1].toLowerCase() !== this.tagName.toLowerCase()) return false;
      if (m[2] && !this.className.split(/\s+/).includes(m[2])) return false;
      if (m[3] && this.id !== m[3]) return false;
      if (m[4] && !(m[4] in this.attrs)) return false;
      if (m[4] && m[5] !== undefined && this.attrs[m[4]] !== m[5]) return false;
    }
    return true;
  }
  querySelectorAll(selector) {
    const parts = selector.trim().split(/\s+/);
    const out = [];
    walk(this, (n) => {
      if (n === this || !n.matches(parts[parts.length - 1])) return;
      let up = n.parentNode, i = parts.length - 2;
      while (i >= 0 && up) { if (up.nodeType === 1 && up.matches(parts[i])) i -= 1; up = up.parentNode; }
      if (i < 0) out.push(n);
    });
    return out;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}

function walk(node, fn) {
  if (node.nodeType !== 1) return;
  fn(node);
  for (const c of [...node.children]) walk(c, fn);
}

function makeDocument() {
  const doc = {
    activeElement: null,
    createElement: (tag) => new FakeElement(tag, doc),
    createElementNS: (ns, tag) => new FakeElement(tag, doc, ns),
    createTextNode: (s) => new FakeText(s),
    getElementById: (id) => doc.documentElement.querySelectorAll('#' + id)[0] || null,
    querySelector: (s) => doc.documentElement.querySelector(s),
    querySelectorAll: (s) => doc.documentElement.querySelectorAll(s),
  };
  doc.documentElement = new FakeElement('html', doc);
  doc.body = new FakeElement('body', doc);
  doc.documentElement.append(doc.body);
  const app = new FakeElement('div', doc);
  app.setAttribute('id', 'app');
  doc.body.append(app);
  return doc;
}

// ---- helpers -------------------------------------------------------------------

const all = (root, pred) => { const out = []; walk(root, (n) => { if (pred(n)) out.push(n); }); return out; };
const buttons = (root, text) => all(root, (n) => n.tagName === 'button' && n.textContent === text);
const withLabel = (root, text) => all(root, (n) => n.tagName === 'button' && (n.attrs['aria-label'] || '').includes(text));
const texts = (root) => root.textContent;
const settle = async (n = 8) => { for (let i = 0; i < n; i++) await new Promise((r) => setImmediate(r)); };
let failures = 0;
let checks = 0;
function check(cond, message) {
  checks += 1;
  if (!cond) { failures += 1; console.error('FAIL: ' + message); }
}

// The tree must carry no inline style and no inline handler attribute.
function assertClean(doc, where) {
  walk(doc.documentElement, (n) => {
    for (const k of Object.keys(n.attrs)) {
      check(k !== 'style', `${where}: <${n.tagName}> has a style attribute`);
      check(!/^on/.test(k), `${where}: <${n.tagName}> has an ${k} attribute`);
    }
  });
}

// ---- the fake server -------------------------------------------------------------

function makeServer(session, options = {}) {
  const data = structuredClone(options.data || PAYLOAD);
  const s = { session: { ...session }, data, calls: [], log: [], runStatus: 200, mutes: {} };
  s.reply = (status, body) => ({ status, ok: status >= 200 && status < 300, json: async () => body });
  s.fetch = async (url, opts = {}) => {
    const u = String(url);
    const body = opts.body ? JSON.parse(opts.body) : null;
    s.calls.push({ url: u, method: opts.method || 'GET', headers: opts.headers || {}, body });
    if (u === 'api/data') return s.reply(200, s.data);
    if (u === 'api/session') return s.reply(200, { actions: s.session.actions, password_set: s.session.password_set, logged_in: s.session.logged_in });
    if (u.startsWith('api/choices')) {
      const card = decodeURIComponent(/card=([^&]*)/.exec(u)[1]);
      const index = Number(/action=(\d+)/.exec(u)[1]);
      const source = s.data.cards[card].actions[index].choose;
      return s.reply(200, { ok: true, values: source === 'blocked_addresses' ? ['192.0.2.7'] : ['/mnt/spare'] });
    }
    if (u.startsWith('api/log?')) return s.reply(200, { ok: true, entries: s.log });
    if (opts.method !== 'POST') return s.reply(404, {});
    if (u === 'api/login') {
      if (body.password === 'good') { s.session.logged_in = true; return s.reply(200, { ok: true }); }
      if (body.password === 'locked') return s.reply(429, { ok: false, error: 'locked', seconds: 42 });
      if (body.password === 'nopw') return s.reply(401, { ok: false, error: 'no_password_set' });
      if (body.password === 'down') return s.reply(503, { ok: false, error: 'runner_unavailable' });
      return s.reply(401, { ok: false, error: 'wrong_password' });
    }
    if (u === 'api/logout') { s.session.logged_in = false; return s.reply(200, { ok: true }); }
    if (!s.session.logged_in) return s.reply(401, { ok: false, error: 'not_logged_in' });
    if (u === 'api/run') {
      if (s.runStatus !== 200) return s.reply(s.runStatus, { ok: false, error: s.runStatus === 409 ? 'busy' : 'unknown_action' });
      const action = s.data.cards[body.card].actions[body.action];
      if (action.level === 'copy') return s.reply(400, { ok: false, error: 'level_copy' });
      return s.reply(200, { ok: true, exit: s.exit ?? 0, timed_out: false, seconds: 0.31, output: 'out of ' + action.run.replace('{value}', body.value ?? ''),
        truncated: !!s.truncated, label: action.label, level: action.level, refreshed: action.level === 'change' ? true : null });
    }
    if (u === 'api/mute') {
      if (!body.reason || body.reason.trim().length < 3) return s.reply(400, { ok: false, error: 'bad_reason' });
      for (const f of s.data.state.findings) if (f.key === body.key) Object.assign(f, { muted: true, reason: body.reason, since: '2026-11-12T11:00:00+0100' });
      return s.reply(200, { ok: true });
    }
    if (u === 'api/unmute') {
      for (const f of s.data.state.findings) if (f.key === body.key) { delete f.muted; delete f.reason; delete f.since; }
      return s.reply(200, { ok: true });
    }
    return s.reply(404, {});
  };
  return s;
}

async function boot(server) {
  const doc = makeDocument();
  const resizeHandlers = [];
  const window = { scrollY: 0, scrollTo() {}, addEventListener: (type, fn) => { if (type === 'resize') resizeHandlers.push(fn); } };
  const context = vm.createContext({ document: doc, window, location: { hostname: 'pi.test' }, fetch: server.fetch, console,
    setTimeout, clearTimeout });
  vm.runInContext(SCRIPT, context, { filename: 'app.js' });
  await settle();
  const app = doc.getElementById('app');
  const ui = vm.runInContext('ui', context);
  return { doc, app, ui, resizeHandlers };
}

const TABS = ['map', 'findings', 'security', 'storage', 'history', 'files', 'daily'];
async function visitAll(page, server, label) {
  for (const tab of TABS) {
    const nav = buttons(page.app, tx('tab_' + tab));
    check(nav.length === 1, `${label}: tab ${tab} exists once`);
    await nav[0].dispatch('click');
    await settle();
    check(page.ui.tab === tab, `${label}: tab ${tab} is shown`);
    check(texts(page.app).length > 50, `${label}: tab ${tab} has content`);
    assertClean(page.doc, `${label}/${tab}`);
  }
  await buttons(page.app, tx('tab_map'))[0].dispatch('click');
  await settle();
}

const cardButton = (page, name) => all(page.app, (n) => n.tagName === 'button' && n.className.includes('card') && n.textContent.includes(name))[0];
const gotoTab = async (page, tab) => { await buttons(page.app, tx('tab_' + tab))[0].dispatch('click'); await settle(); };

if (DRAW) {
  const seen = [];
  const sessions = [{ actions: false, password_set: false, logged_in: false },
    { actions: true, password_set: true, logged_in: false }, { actions: true, password_set: true, logged_in: true }];
  for (const [n, session] of sessions.entries()) {
    const page = await boot(makeServer(session));
    for (const tab of TABS) {
      await buttons(page.app, tx('tab_' + tab))[0].dispatch('click');
      await settle();
      check(page.ui.tab === tab, `draw ${n}: tab ${tab} is shown`);
      check(texts(page.app).length > 50, `draw ${n}: tab ${tab} has content`);
      assertClean(page.doc, `draw ${n}/${tab}`);
      seen.push(texts(page.app));
    }
  }
  for (const want of process.argv.slice(4)) check(seen.some((s) => s.includes(want)), `draw: "${want}" is shown`);
  console.log(`${checks} checks, ${failures} failed`);
  if (failures) process.exit(1);
  console.log('smoke test: ok');
  process.exit(0);
}

// ---- 1. display only -------------------------------------------------------------

{
  const server = makeServer({ actions: false, password_set: false, logged_in: false });
  const page = await boot(server);
  check(texts(page.app).includes(tx('hint_display_only')), 'display only: hint is shown');
  check(buttons(page.app, tx('login')).length === 0, 'display only: no login button');
  await visitAll(page, server, 'display-only');
  check(withLabel(page.app, tx('run')).length === 0 && buttons(page.app, tx('run')).length === 0, 'display only: no run button anywhere');
  check(all(page.doc.documentElement, (n) => n.tagName === 'dialog').length === 0, 'display only: no dialog');
}

// ---- 2. not logged in ------------------------------------------------------------

{
  const server = makeServer({ actions: true, password_set: true, logged_in: false });
  const page = await boot(server);
  check(texts(page.app).includes(tx('hint_login')), 'logged out: hint says log in');
  await visitAll(page, server, 'logged-out');
  check(buttons(page.app, tx('run')).length === 0 && buttons(page.app, tx('mute')).length === 0, 'logged out: no run and no mute button');
  check(buttons(page.app, tx('btn_unblock')).length === 0, 'logged out: no unlock button');
  await gotoTab(page, 'security');
  check(texts(page.app).includes('sudo cscli decisions delete --ip 192.0.2.7'), 'logged out: unlock command is only shown');
  await gotoTab(page, 'history');
  check(texts(page.app).includes(tx('hist_log_login_needed')), 'logged out: history says the actions need a login');
  check(!server.calls.some((c) => c.url.startsWith('api/log')), 'logged out: no log request');

  await buttons(page.app, tx('login'))[0].dispatch('click');
  const dlg = all(page.doc.documentElement, (n) => n.tagName === 'dialog')[0];
  check(!!dlg && dlg.open, 'login: dialog is open');
  const input = all(dlg, (n) => n.tagName === 'input')[0];
  check(input.attrs.type === 'password' && input.attrs.autocomplete === 'current-password', 'login: password field attributes');
  check(all(dlg, (n) => n.tagName === 'label' && n.attrs.for === input.id).length === 1, 'login: field has a label');
  const form = all(dlg, (n) => n.tagName === 'form')[0];
  const attempts = [['wrong', tx('err_wrong_password')], ['locked', tx('err_locked', { n: 42 })], ['nopw', tx('err_no_password')], ['down', tx('err_runner')]];
  for (const [password, expected] of attempts) {
    input.value = password;
    const ev = await form.dispatch('submit');
    await settle();
    check(ev.defaultPrevented, 'login: form submit is intercepted');
    check(texts(dlg).includes(expected), `login: message for "${password}"`);
    check(input.value === '', 'login: password field is emptied');
    if (password === 'nopw') check(texts(dlg).includes('pihub password'), 'login: shows the terminal command when no password is set');
  }
  input.value = 'good';
  await form.dispatch('submit');
  await settle();
  check(!dlg.open && !dlg.isConnected, 'login: dialog closes on success');
  check(page.ui.session.logged_in, 'login: session is logged in');
  check(texts(page.app).includes(tx('hint_logged_in')), 'login: hint changes');
  check(buttons(page.app, tx('logout')).length === 1, 'login: logout button shown');
  const post = server.calls.find((c) => c.url === 'api/login');
  check(post.headers['X-PiHub'] === '1' && post.headers['Content-Type'] === 'application/json', 'login: POST has its headers');
  await buttons(page.app, tx('logout'))[0].dispatch('click');
  await settle();
  check(!page.ui.session.logged_in, 'logout: session is closed');
}

// ---- 3. logged in ----------------------------------------------------------------

{
  const server = makeServer({ actions: true, password_set: true, logged_in: true });
  server.log = [
    { time: '2026-11-12T10:00:00+0100', event: 'login_failed', peer: '192.0.2.50' },
    { time: '2026-11-12T10:05:00+0100', event: 'action', peer: '192.0.2.9', card: 'site', label: 'Say hello', level: 'read', command: 'echo hello', exit: 0, timed_out: false, seconds: 0.2 },
    { time: '2026-11-12T10:06:00+0100', event: 'action', peer: '192.0.2.9', card: 'site', label: 'Restart site', level: 'change', command: 'echo restarted', exit: 3, timed_out: false, seconds: 0.4 },
    { time: '2026-11-12T10:07:00+0100', event: 'login', peer: '192.0.2.9' },
  ];
  const page = await boot(server);
  await visitAll(page, server, 'logged-in');

  // Map: the card detail and the lines.
  await cardButton(page, 'Site').dispatch('click');
  await settle();
  check(page.ui.sel === 'site', 'map: Site is chosen');
  const runs = withLabel(page.app, tx('run_aria', { label: 'Say hello' }));
  check(runs.length === 1, 'map: run button for the read action');
  check(withLabel(page.app, tx('run_aria', { label: 'Copy me' })).length === 0, 'map: no run button for a copy action');
  const overlayOf = () => all(page.app, (n) => n.tagName === 'svg' && n.className === 'links')[0];
  check(overlayOf().attrs['aria-hidden'] === 'true', 'map: overlay is hidden from assistive technology');
  check(all(overlayOf(), (n) => n.tagName === 'path').length === 1, 'map: one line from Site to the card it needs');
  await cardButton(page, 'Base').dispatch('click');
  await settle();
  const paths = all(overlayOf(), (n) => n.tagName === 'path');
  check(paths.length === 2 && paths.every((p) => /^M[\d. -]+C/.test(p.attrs.d) && p.className.includes('link-dependent')), 'map: two lines from the cards that depend on Base');
  page.resizeHandlers.forEach((fn) => fn());
  await new Promise((r) => setTimeout(r, 250));
  await cardButton(page, 'Site').dispatch('click');
  await settle();

  // A read action runs at once.
  await withLabel(page.app, tx('run_aria', { label: 'Say hello' }))[0].dispatch('click');
  await settle();
  const run1 = server.calls.filter((c) => c.url === 'api/run');
  check(run1.length === 1 && run1[0].body.card === 'site' && run1[0].body.action === 0 && !('value' in run1[0].body), 'read action: request body');
  check(texts(page.app).includes('out of echo hello') && texts(page.app).includes(tx('res_done')), 'read action: result with output is shown');
  await buttons(page.app, tx('tab_findings'))[0].dispatch('click');
  await buttons(page.app, tx('tab_map'))[0].dispatch('click');
  await settle();
  check(texts(page.app).includes('out of echo hello'), 'read action: result survives a redraw');
  await buttons(page.app, tx('close'))[0].dispatch('click');
  check(!texts(page.app).includes('out of echo hello'), 'read action: result can be closed');

  // A change action asks first, and the data are loaded again afterwards.
  const dataBefore = server.calls.filter((c) => c.url === 'api/data').length;
  await withLabel(page.app, tx('run_aria', { label: 'Restart site' }))[0].dispatch('click');
  let dlg = all(page.doc.documentElement, (n) => n.tagName === 'dialog')[0];
  check(dlg && texts(dlg).includes('echo restarted') && texts(dlg).includes('The site is gone for a moment.'), 'change action: dialog shows command and effect');
  check(server.calls.filter((c) => c.url === 'api/run').length === 1, 'change action: nothing ran before confirming');
  await buttons(dlg, tx('cancel'))[0].dispatch('click');
  check(!dlg.isConnected, 'change action: cancel closes the dialog');
  await withLabel(page.app, tx('run_aria', { label: 'Restart site' }))[0].dispatch('click');
  dlg = all(page.doc.documentElement, (n) => n.tagName === 'dialog')[0];
  await buttons(dlg, tx('run'))[0].dispatch('click');
  await settle();
  check(server.calls.filter((c) => c.url === 'api/run').length === 2, 'change action: ran after confirming');
  check(server.calls.filter((c) => c.url === 'api/data').length === dataBefore + 1, 'change action: data loaded again');

  // A failing command is told in words.
  server.exit = 3; server.truncated = true;
  await withLabel(page.app, tx('run_aria', { label: 'Say hello' }))[0].dispatch('click');
  await settle();
  check(texts(page.app).includes(tx('res_failed', { n: 3 })) && texts(page.app).includes(tx('res_truncated')), 'failed action: status and truncation notice');
  server.exit = 0; server.truncated = false;

  // 409 busy.
  server.runStatus = 409;
  await withLabel(page.app, tx('run_aria', { label: 'Say hello' }))[0].dispatch('click');
  await settle();
  check(texts(page.app).includes(tx('err_busy')), 'busy: message is shown');
  server.runStatus = 200;

  // An action with a choice: guard.
  await cardButton(page, 'Guard').dispatch('click');
  await settle();
  await withLabel(page.app, tx('run_aria', { label: 'Unlock an address' }))[0].dispatch('click');
  await settle();
  dlg = all(page.doc.documentElement, (n) => n.tagName === 'dialog')[0];
  let select = all(dlg, (n) => n.tagName === 'select')[0];
  check(!!select && all(select, (n) => n.tagName === 'option').length === 1, 'choice: select with the offered value');
  check(all(dlg, (n) => n.tagName === 'label' && n.attrs.for === select.id).length === 1, 'choice: select has a label');
  check(texts(dlg).includes('echo unlock 192.0.2.7'), 'choice: command shows the chosen value');
  await buttons(dlg, tx('run'))[0].dispatch('click');
  await settle();
  const last = server.calls.filter((c) => c.url === 'api/run').pop();
  check(last.body.card === 'guard' && last.body.action === 0 && last.body.value === '192.0.2.7', 'choice: value is sent');

  // Security: unlock button with the address preselected.
  await gotoTab(page, 'security');
  const unlock = buttons(page.app, tx('btn_unblock'));
  check(unlock.length === 1, 'security: unlock button');
  await unlock[0].dispatch('click');
  await settle();
  dlg = all(page.doc.documentElement, (n) => n.tagName === 'dialog')[0];
  select = all(dlg, (n) => n.tagName === 'select')[0];
  check(select.value === '192.0.2.7', 'security: address is preselected');
  await buttons(dlg, tx('run'))[0].dispatch('click');
  await settle();
  check(texts(page.app).includes('out of echo unlock 192.0.2.7'), 'security: result is shown under the address');

  // Storage: mount button.
  await gotoTab(page, 'storage');
  const mount = buttons(page.app, tx('sto_mount'));
  check(mount.length === 1, 'storage: mount button for the unmounted target');
  await mount[0].dispatch('click');
  await settle();
  dlg = all(page.doc.documentElement, (n) => n.tagName === 'dialog')[0];
  check(all(dlg, (n) => n.tagName === 'select')[0].value === '/mnt/spare', 'storage: target is preselected');
  await buttons(dlg, tx('cancel'))[0].dispatch('click');
  check(withLabel(page.app, tx('run_aria', { label: 'Mount a target' })).length === 2, 'storage: the card action is listed with its own run button as well');

  // Muting.
  await gotoTab(page, 'findings');
  const mutes = buttons(page.app, tx('mute'));
  check(mutes.length >= 1, 'findings: mute button for findings with a key');
  await mutes[0].dispatch('click');
  dlg = all(page.doc.documentElement, (n) => n.tagName === 'dialog')[0];
  const area = all(dlg, (n) => n.tagName === 'textarea')[0];
  check(!!area && all(dlg, (n) => n.tagName === 'label' && n.attrs.for === area.id).length === 1, 'mute: reason field with label');
  const form = all(dlg, (n) => n.tagName === 'form')[0];
  area.value = 'ab';
  await form.dispatch('submit');
  await settle();
  check(texts(dlg).includes(tx('err_reason')) && !server.calls.some((c) => c.url === 'api/mute'), 'mute: a too short reason is refused before sending');
  area.value = 'we know about this';
  await form.dispatch('submit');
  await settle();
  const muteCall = server.calls.find((c) => c.url === 'api/mute');
  check(muteCall && muteCall.body.reason === 'we know about this' && muteCall.body.key === 'site:check_failed:Answers', 'mute: request body');
  check(!dlg.isConnected, 'mute: dialog closed');
  const mutedLabel = tx('muted_h', { n: 2 });
  check(texts(page.app).includes(mutedLabel), 'mute: silenced section is shown');
  check(texts(page.app).includes('we know about this'), 'mute: reason is listed');
  check(texts(page.app).includes(tx('muted_since', { date: '2026-11-12 11:00' })), 'mute: date is listed');
  await buttons(page.app, tx('unmute'))[0].dispatch('click');
  await settle();
  check(server.calls.some((c) => c.url === 'api/unmute'), 'unmute: request sent');
  check(!texts(page.app).includes('we know about this'), 'unmute: reason no longer listed');

  // History.
  await gotoTab(page, 'history');
  const charts = all(page.app, (n) => n.tagName === 'svg' && n.attrs.role === 'img');
  check(charts.length === 4, `history: four charts (found ${charts.length})`);
  check(charts.every((c) => c.attrs.viewBox && c.attrs['aria-label']), 'history: charts have viewBox and aria-label');
  const temp = all(charts[0], (n) => n.tagName === 'path' && n.className.includes('ln'))[0];
  check((temp.attrs.d.match(/M/g) || []).length >= 2, 'history: a missing reading breaks the line');
  check(all(charts[3], (n) => n.tagName === 'path' && n.className.includes('ln')).length === 2, 'history: one line per disk');
  check(texts(page.app).includes('/mnt/data'), 'history: disk lines are labelled');
  check(page.ui.range === 'day' && buttons(page.app, tx('hist_day'))[0].attrs['aria-pressed'] === 'true', 'history: 24 hours is the default');
  await buttons(page.app, tx('hist_week'))[0].dispatch('click');
  await settle();
  check(page.ui.range === 'week' && buttons(page.app, tx('hist_week'))[0].attrs['aria-pressed'] === 'true', 'history: switch to 7 days');
  check(texts(page.app).includes(tx('res_failed', { n: 3 })) && texts(page.app).includes(tx('hist_log_login_failed')), 'history: log lists results and failed logins');
  check(texts(page.app).includes(tx('st_ok') + ' → ' + tx('st_problem')), 'history: changes of state are listed');
  assertClean(page.doc, 'logged-in/final');

  // The session ends: a 401 switches to "not logged in" and says so once.
  server.session.logged_in = false;
  await gotoTab(page, 'map');
  await cardButton(page, 'Site').dispatch('click');
  await settle();
  await withLabel(page.app, tx('run_aria', { label: 'Say hello' }))[0].dispatch('click');
  await settle();
  check(!page.ui.session.logged_in, 'expiry: page switches to not logged in');
  check(texts(page.app).split(tx('session_expired')).length === 2, 'expiry: the message appears exactly once');
  check(buttons(page.app, tx('run')).length === 0 && withLabel(page.app, tx('run')).length === 0, 'expiry: run buttons are gone');
}

// ---- 4. too few readings ---------------------------------------------------------

{
  const data = structuredClone(PAYLOAD);
  data.history = { day: [data.history.day[0]], week: [], changes: [] };
  const server = makeServer({ actions: true, password_set: true, logged_in: false }, { data });
  const page = await boot(server);
  await gotoTab(page, 'history');
  check(texts(page.app).split(tx('hist_few')).length === 5, 'sparse history: a sentence instead of each chart');
  check(all(page.app, (n) => n.tagName === 'svg' && n.attrs.role === 'img').length === 0, 'sparse history: no chart');
  assertClean(page.doc, 'sparse');
}

console.log(`${checks} checks, ${failures} failed`);
if (failures) process.exit(1);
console.log('smoke test: ok');
process.exit(0);

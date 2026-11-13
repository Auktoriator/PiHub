'use strict';
// PiHub page. It draws what /api/data delivers. Login and actions are only
// requests to the server; the page itself runs nothing.
// Every text from the machine goes in as a text node, never as HTML.
// The page allows no inline styles: sizes and positions are set from here
// with element.style or SVG attributes.

let D = null;
const ui = {
  tab: 'map', sel: null, range: 'day',
  session: { actions: false, password_set: false, logged_in: false },
  running: null,      // key of the action that runs right now
  results: {},        // key -> result shown under an action until it is closed
  log: null,          // { entries } or { error } once /api/log was read
  logLoading: false,
  notice: null,       // one message at the top, e.g. "you were logged out"
  mutedOpen: false,
};
// Rebuilt on every render.
let cardEls = new Map();
let overlay = null;

function t(key, params) {
  let s = D.texts[key] ?? key;
  for (const [k, v] of Object.entries(params || {})) s = s.replaceAll('{' + k + '}', String(v));
  return s;
}

function el(tag, attrs, ...kids) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') node.className = v;
    else if (/^on[a-z]+$/.test(k)) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid == null || kid === false) continue;
    node.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return node;
}

// SVG elements need their own namespace and take every attribute as an attribute.
function svg(tag, attrs, ...kids) {
  const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [k, v] of Object.entries(attrs || {})) if (v != null && v !== false) node.setAttribute(k, v);
  for (const kid of kids.flat(Infinity)) if (kid) node.append(kid);
  return node;
}

const cmd = (text) => el('code', { class: 'cmd' }, text);
const tag = (kind, text) => el('span', { class: 'tag tag-' + kind }, text);
const sec = (title, ...kids) => el('div', { class: 'sec' }, el('h3', null, title), kids);
const statusOf = (id) => (D.state.cards[id] || {}).status || 'unknown';
const statusDot = (status) => el('span', { class: 'status s-' + status }, el('i', { 'aria-hidden': 'true' }), t('st_' + status));
const reachText = (list) => (list && list.length ? list.map((r) => t('reach_' + r)).join(', ') : t('nobody'));
const letsIn = (list) => (list && list.length ? list.map((r) => (r === 'other' ? t('other') : t('reach_' + r))).join(', ') : t('nobody'));
const portRow = (port, proto) => D.security.ports.find((r) => r.port === port && r.proto === (proto || 'tcp'));
const loggedIn = () => ui.session.actions && ui.session.logged_in;
// A placeholder in a command that is only shown, in the language of the page (texts.py).
const ph = (key) => '<' + t(key) + '>';
// What the scan could say about one section of the machine. Missing means: not tried (no scan yet).
const scanOf = (name) => (D.sections || {})[name];
const unreadable = (name) => { const s = scanOf(name); return !!s && !s.ok; };
function unreadText(name) {
  const s = scanOf(name);
  const key = s.why === 'missing_tool' ? 'sx_missing' : s.why === 'needs_sudo' ? 'sx_sudo' : 'sx_failed';
  return t(key, { what: t('what_' + name), tool: s.tool || name, error: s.error || '' });
}
const unreadNote = (name, text) => el('div', { class: 'note' }, text || unreadText(name));
const pad2 = (n) => String(n).padStart(2, '0');
const stamp = (iso) => String(iso || '').slice(0, 16).replace('T', ' ');
const num = (v, digits) => Number(v).toLocaleString(D.lang, { maximumFractionDigits: digits });

function clock(ts, withDate) {
  const d = new Date(ts * 1000);
  return (withDate ? pad2(d.getDate()) + '.' + pad2(d.getMonth() + 1) + '. ' : '') + pad2(d.getHours()) + ':' + pad2(d.getMinutes());
}

// A new tab starts at the top; picking a card on the same tab keeps the place.
function go(tab, sel) {
  const same = ui.tab === tab;
  ui.tab = tab;
  if (sel) ui.sel = sel;
  if (same) { redraw(); return; }
  render();
  window.scrollTo(0, 0);
}

// Draw again without jumping to the top.
function redraw() {
  const y = window.scrollY || 0;
  render();
  window.scrollTo(0, y);
}

function linkHref(link) {
  if (link.url) return link.url;
  return (link.scheme || 'http') + '://' + location.hostname + ':' + link.port + (link.path || '');
}

function linkRow(link) {
  return el('a', { class: 'linkrow', href: linkHref(link), target: '_blank', rel: 'noopener noreferrer' },
    el('b', null, link.label), el('span', { class: 'small muted' }, link.note || ''));
}

// ---- talking to the server ---------------------------------------------------

async function api(path, body) {
  const opts = body === undefined
    ? { cache: 'no-store' }
    : { method: 'POST', headers: { 'X-PiHub': '1', 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
  let answer;
  try {
    answer = await fetch(path, opts);
  } catch (error) {
    return { status: 0, body: { ok: false, error: 'network' } };
  }
  let data = {};
  try { data = await answer.json(); } catch (error) { data = {}; }
  if (answer.status === 401 && data.error === 'not_logged_in') expired();
  return { status: answer.status, body: data };
}

function expired() {
  if (!ui.session.logged_in) return;
  ui.session.logged_in = false;
  ui.log = null;
  ui.notice = t('session_expired');
  redraw();
}

function errorText(reply) {
  const e = reply.body.error;
  switch (e) {
    case 'wrong_password': return t('err_wrong_password');
    case 'locked': return t('err_locked', { n: reply.body.seconds ?? '' });
    case 'runner_unavailable': return t('err_runner');
    case 'busy': return t('err_busy');
    case 'bad_value': return t('err_bad_value');
    case 'unknown_action': return t('err_unknown_action');
    case 'level_copy': return t('err_level_copy');
    case 'bad_reason': return t('err_reason');
    case 'network': return t('err_network');
    default: return t('err_generic', { code: e || reply.status });
  }
}

async function loadData() {
  try {
    const answer = await fetch('api/data', { cache: 'no-store' });
    if (!answer.ok) return false;
    D = await answer.json();
    return true;
  } catch (error) {
    return false;
  }
}

async function loadSession() {
  try {
    const answer = await fetch('api/session', { cache: 'no-store' });
    const s = await answer.json();
    ui.session = { actions: !!s.actions, password_set: !!s.password_set, logged_in: !!(s.actions && s.logged_in) };
  } catch (error) {
    ui.session = { actions: false, password_set: false, logged_in: false };
  }
}

// ---- dialogs -------------------------------------------------------------------

// A modal <dialog>. Esc closes it (the browser does that); focus goes back
// to the button that opened it, or to the current tab when that is gone.
function modal(title, ...kids) {
  const opener = document.activeElement;
  const dlg = el('dialog', { class: 'dlg', 'aria-labelledby': 'dlg-title' }, el('h2', { id: 'dlg-title' }, title), kids);
  dlg.addEventListener('close', () => {
    dlg.remove();
    const back = opener && opener.isConnected ? opener : document.querySelector('nav .tab[aria-current]');
    if (back && back.focus) back.focus();
  });
  document.body.append(dlg);
  dlg.showModal();
  return dlg;
}

const field = (id, label, input, hint) => el('div', { class: 'col' },
  el('label', { for: id, class: 'lbl' }, label), input, hint && el('span', { class: 'small muted' }, hint));

function openLogin() {
  const input = el('input', { id: 'pw', class: 'field', type: 'password', autocomplete: 'current-password', required: true });
  const msg = el('div', { class: 'form-msg', role: 'alert' });
  const submit = el('button', { type: 'submit', class: 'btn primary' }, t('login'));
  const cancel = el('button', { type: 'button', class: 'btn' }, t('cancel'));
  const form = el('form', { class: 'col' }, field('pw', t('password'), input), msg, el('div', { class: 'row end' }, cancel, submit));
  const dlg = modal(t('login'), form);
  const showError = (text, withCommand) => msg.replaceChildren(...[el('p', null, text), withCommand ? cmd('pihub password') : null].filter(Boolean));
  if (!ui.session.password_set) showError(t('err_no_password'), true);
  cancel.addEventListener('click', () => dlg.close());
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (submit.disabled) return;
    submit.disabled = true;
    msg.replaceChildren();
    const reply = await api('api/login', { password: input.value });
    input.value = '';
    if (reply.status === 200 && reply.body.ok) {
      ui.session.logged_in = true;
      ui.session.password_set = true;
      ui.notice = null;
      ui.log = null;
      dlg.close();
      redraw();
      return;
    }
    if (reply.body.error === 'no_password_set') showError(t('err_no_password'), true);
    else showError(errorText(reply));
    submit.disabled = false;
    input.focus();
  });
  input.focus();
}

async function logout() {
  await api('api/logout', {});
  ui.session.logged_in = false;
  ui.results = {};
  ui.log = null;
  ui.notice = null;
  redraw();
}

// ---- running actions -------------------------------------------------------------

function resultBox(key) {
  const r = key && ui.results[key];
  if (!r) return null;
  const word = r.kind === 'done' ? t('res_done') : r.kind === 'timeout' ? t('res_timeout', { n: Math.round(r.seconds) })
    : r.kind === 'failed' ? t('res_failed', { n: r.exit }) : r.text;
  const close = el('button', { type: 'button', class: 'chip', onclick: () => { delete ui.results[key]; redraw(); } }, t('close'));
  return el('div', { class: 'result', role: 'group', 'aria-label': t('res_output') },
    el('div', { class: 'row between' },
      el('span', { class: 'row' }, tag(r.kind === 'done' ? 'done' : 'failed', word), r.seconds != null && r.kind !== 'error' && el('span', { class: 'small muted' }, t('res_took', { n: num(r.seconds, 1) }))),
      close),
    r.kind !== 'error' && (r.output
      ? el('pre', { class: 'out', tabindex: '0', 'aria-label': t('res_output') }, r.output)
      : el('span', { class: 'small muted' }, t('res_no_output'))),
    r.truncated && el('span', { class: 'small muted' }, t('res_truncated')),
    r.refreshed === false && el('span', { class: 'small muted' }, t('res_not_refreshed')));
}

async function runAction(cardId, index, value, key) {
  if (ui.running) return;
  ui.running = key;
  delete ui.results[key];
  redraw();
  const body = { card: cardId, action: index };
  if (value != null) body.value = value;
  const reply = await api('api/run', body);
  ui.running = null;
  const b = reply.body;
  if (reply.status === 200 && b.ok) {
    ui.results[key] = { kind: b.timed_out ? 'timeout' : b.exit === 0 ? 'done' : 'failed', exit: b.exit,
      seconds: b.seconds, output: b.output || '', truncated: !!b.truncated, refreshed: b.refreshed };
    ui.log = null;
    if (b.level === 'change') await loadData();
  } else if (reply.status !== 401) {
    ui.results[key] = { kind: 'error', text: errorText(reply) };
  }
  redraw();
}

// Asks first for everything that changes something, and for every action that needs a value.
function openRunDialog(ref, action, key, preselect) {
  const needsValue = !!action.choose;
  const shown = cmd(action.run);
  const msg = el('div', { class: 'form-msg', role: 'alert' });
  const select = needsValue ? el('select', { id: 'choice', class: 'field', disabled: true }) : null;
  const okBtn = el('button', { type: 'button', class: 'btn primary', disabled: needsValue }, t('run'));
  const cancel = el('button', { type: 'button', class: 'btn' }, t('cancel'));
  const showCommand = () => shown.replaceChildren(document.createTextNode(select && select.value ? action.run.replace('{value}', select.value) : action.run));
  const dlg = modal(action.label,
    el('div', { class: 'sec' }, el('h3', null, t('dlg_command')), shown),
    action.effect && el('div', { class: 'sec' }, el('h3', null, t('dlg_effect')), el('p', null, action.effect)),
    needsValue && field('choice', t('choice_label'), select),
    msg,
    el('div', { class: 'row end' }, cancel, okBtn));
  cancel.addEventListener('click', () => dlg.close());
  okBtn.addEventListener('click', () => {
    if (okBtn.disabled) return;
    const value = select ? select.value : null;
    dlg.close();
    runAction(ref.cardId, ref.index, value, key);
  });
  if (!needsValue) { okBtn.focus(); return; }
  msg.replaceChildren(el('span', { class: 'small muted' }, t('choice_loading')));
  api('api/choices?card=' + encodeURIComponent(ref.cardId) + '&action=' + encodeURIComponent(ref.index)).then((reply) => {
    msg.replaceChildren();
    const values = reply.status === 200 && reply.body.ok && Array.isArray(reply.body.values) ? reply.body.values : null;
    if (!values) { msg.append(el('p', null, errorText(reply))); return; }
    if (!values.length) { msg.append(el('p', null, t('choice_none'))); return; }
    if (preselect != null && !values.includes(preselect)) { msg.append(el('p', null, t('choice_gone'))); return; }
    select.replaceChildren(...(preselect != null ? [preselect] : values).map((v) => el('option', { value: v }, v)));
    select.value = preselect != null ? preselect : values[0];
    select.disabled = false;
    okBtn.disabled = false;
    select.addEventListener('change', showCommand);
    showCommand();
    okBtn.focus();
  });
}

function runButton(action, ref, key, label, preselect) {
  const busy = ui.running === key;
  return el('button', { type: 'button', class: 'btn run', disabled: !!ui.running,
    'aria-label': t('run_aria', { label: action.label }),
    onclick: () => {
      if (action.level === 'change' || action.choose) openRunDialog(ref, action, key, preselect);
      else runAction(ref.cardId, ref.index, null, key);
    } }, busy ? t('running') : (label || t('run')));
}

// An action shown on a card. With `ref` (card id and position) a logged-in
// user can run it; without, it is only shown.
function actionItem(a, ref) {
  const key = ref ? ref.cardId + ':' + ref.index : null;
  const canRun = ref && loggedIn() && a.level !== 'copy';
  return el('div', { class: 'item' },
    el('div', { class: 'item-head' }, el('span', null, a.label), tag(a.level, t('level_' + a.level))),
    cmd(a.run), a.effect && el('span', { class: 'small muted' }, a.effect),
    canRun && el('div', null, runButton(a, ref, key)),
    resultBox(key));
}

// The first card action that takes its value from the given list.
function findChoice(source) {
  for (const [cardId, card] of Object.entries(D.cards)) {
    const index = (card.actions || []).findIndex((a) => a.choose === source && a.level !== 'copy');
    if (index >= 0) return { ref: { cardId, index }, action: card.actions[index] };
  }
  return null;
}

// ---- header ---------------------------------------------------------------

function header() {
  const counts = { ok: 0, problem: 0, todo: 0, off: 0 };
  for (const id of Object.keys(D.cards)) { const s = statusOf(id); if (s in counts) counts[s] += 1; }
  const pills = ['ok', 'problem', 'todo', 'off'].filter((s) => counts[s] > 0 || s === 'ok')
    .map((s) => el('span', { class: 'pill status s-' + s }, el('i', { 'aria-hidden': 'true' }), t('count_' + s, { n: counts[s] })));
  const when = D.state.taken_at ? t('as_of', { time: D.state.taken_at.slice(0, 16).replace('T', ' ') }) : t('never_collected');
  const hint = !ui.session.actions ? t('hint_display_only') : loggedIn() ? t('hint_logged_in') : t('hint_login');
  const sessionButton = ui.session.actions
    ? el('button', { type: 'button', class: 'btn', onclick: loggedIn() ? logout : openLogin }, loggedIn() ? t('logout') : t('login'))
    : null;
  return el('header', { class: 'col' },
    el('div', { class: 'head' },
      el('div', { class: 'col' }, el('div', { class: 'kicker' }, [D.host.model, D.host.name].filter(Boolean).join(' · ')),
        el('h1', null, t('title')), el('div', { class: 'muted' }, t('subtitle'))),
      el('div', { class: 'row' }, pills)),
    ui.notice && el('div', { class: 'notice', role: 'status' }, el('span', null, ui.notice),
      el('button', { type: 'button', class: 'chip', onclick: () => { ui.notice = null; redraw(); } }, t('close'))),
    !D.inventory && el('div', { class: 'note' }, t('no_inventory_page')),
    el('div', { class: 'note row between' }, el('span', null, hint + ' ' + when), sessionButton));
}

function nav() {
  const tabs = ['map', 'findings', 'security', 'storage', 'history', 'files', 'daily'];
  return el('nav', { 'aria-label': 'PiHub' }, tabs.map((name) =>
    el('button', { type: 'button', class: 'tab', 'aria-current': ui.tab === name ? 'page' : null, onclick: () => go(name) }, t('tab_' + name))));
}

// ---- map ------------------------------------------------------------------

function tech(card) {
  const parts = [...(card.units || []).slice(0, 1), ...(card.containers || []).map((n) => t('container') + ' ' + n)];
  const ports = (card.ports || []).map((p) => p.port);
  if (ports.length) parts.push(t('port') + ' ' + ports.join(', '));
  return parts.join(' · ');
}

function cardButton(id, needs) {
  const card = D.cards[id];
  const rel = needs.has(id) ? 'needed' : (card.needs || []).includes(ui.sel) ? 'dependent' : null;
  const button = el('button', { type: 'button', class: 'card' + (id === ui.sel ? ' is-sel' : '') + (rel ? ' rel-' + rel : ''),
    'aria-pressed': id === ui.sel ? 'true' : 'false', onclick: () => go('map', id) },
    statusDot(statusOf(id)), el('span', { class: 'card-name' }, card.name), el('span', { class: 'card-sum' }, card.summary),
    el('span', { class: 'card-tech' }, tech(card)), rel && tag(rel, t('rel_' + rel)));
  cardEls.set(id, button);
  return button;
}

function viewMap() {
  const ids = Object.keys(D.cards);
  if (!ids.length) return el('div', { class: 'col narrow' }, el('p', { class: 'box' }, t('empty_cards')));
  if (!ui.sel || !D.cards[ui.sel]) ui.sel = ids.find((id) => statusOf(id) === 'problem') || ids[0];
  const needs = new Set(D.cards[ui.sel].needs || []);
  const areas = D.areas.map((area) => el('section', { class: 'area' },
    el('div', { class: 'row' }, el('h2', null, area.name), el('span', { class: 'small muted' }, area.summary || '')),
    el('div', { class: 'grid' }, ids.filter((id) => D.cards[id].area === area.id).map((id) => cardButton(id, needs)))));
  const lines = svg('svg', { class: 'links', 'aria-hidden': 'true', focusable: 'false' });
  const box = el('div', { class: 'areas' }, areas, lines);
  overlay = { box, lines };
  return el('div', { class: 'split' }, box, detail(ui.sel));
}

// A gently curved line from the edge of one card to the edge of another.
function curve(a, b) {
  const mid = (lo, hi) => (lo + hi) / 2;
  let p, q, c1, c2;
  if (b.t >= a.b - 1 || b.b <= a.t + 1) {
    const down = b.t >= a.b - 1;
    p = [mid(a.l, a.r), down ? a.b : a.t];
    q = [mid(b.l, b.r), down ? b.t : b.b];
    const d = (q[1] - p[1]) * 0.4;
    c1 = [p[0], p[1] + d]; c2 = [q[0], q[1] - d];
  } else {
    const right = b.l >= a.r;
    p = [right ? a.r : a.l, mid(a.t, a.b)];
    q = [right ? b.l : b.r, mid(b.t, b.b)];
    const d = (q[0] - p[0]) * 0.4;
    c1 = [p[0] + d, p[1]]; c2 = [q[0] - d, q[1]];
  }
  const f = (n) => Math.round(n * 10) / 10;
  return `M${f(p[0])} ${f(p[1])} C${f(c1[0])} ${f(c1[1])} ${f(c2[0])} ${f(c2[1])} ${f(q[0])} ${f(q[1])}`;
}

// Lines between the chosen card and the cards it needs or that need it.
// Positions are only known after the page is laid out, so this runs after render().
function drawLinks() {
  if (!overlay || !ui.sel) return;
  const { box, lines } = overlay;
  lines.replaceChildren();
  const from = cardEls.get(ui.sel);
  if (!from) return;
  const outer = box.getBoundingClientRect();
  const w = Math.max(Math.round(outer.width), 1);
  const h = Math.max(Math.round(box.scrollHeight || outer.height), 1);
  lines.setAttribute('viewBox', `0 0 ${w} ${h}`);
  lines.setAttribute('width', w);
  lines.setAttribute('height', h);
  const rect = (node) => {
    const r = node.getBoundingClientRect();
    return { l: r.left - outer.left, r: r.right - outer.left, t: r.top - outer.top, b: r.bottom - outer.top };
  };
  const a = rect(from);
  const link = (id, kind) => {
    const node = cardEls.get(id);
    if (node) lines.append(svg('path', { class: 'link link-' + kind, d: curve(a, rect(node)) }));
  };
  for (const id of D.cards[ui.sel].needs || []) link(id, 'needed');
  for (const id of Object.keys(D.cards)) if ((D.cards[id].needs || []).includes(ui.sel)) link(id, 'dependent');
}

let resizeTimer = null;
window.addEventListener('resize', () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => { if (ui.tab === 'map') drawLinks(); }, 120);
});

function detail(id) {
  const card = D.cards[id];
  const st = D.state.cards[id] || { status: 'unknown', units: {}, containers: {}, checks: [] };
  const area = D.areas.find((a) => a.id === card.area) || {};
  const out = el('aside', { class: 'detail', 'aria-label': card.name });

  out.append(el('div', { class: 'col' }, el('div', { class: 'small muted' }, area.name || ''), el('h2', null, card.name), statusDot(st.status),
    st.status === 'off' && el('p', { class: 'small' }, t('off_text', { switch: card.switch })),
    card.todo && st.status !== 'off' && el('p', { class: 'small' }, card.todo)));
  out.append(sec(t('h_about'), el('p', null, card.about)));

  const lines = [];
  for (const [name, state] of Object.entries(st.units || {})) lines.push(el('div', { class: 'kv' }, el('span', null, name), el('span', null, t('u_' + state))));
  for (const [name, state] of Object.entries(st.containers || {})) lines.push(el('div', { class: 'kv' }, el('span', null, t('container') + ' ' + name), el('span', null, state === 'unknown' ? t('u_unknown') : state)));
  (st.checks || []).forEach((c, i) => {
    const spec = (card.checks || [])[i] || {};
    lines.push(el('div', { class: 'col' },
      el('div', { class: 'kv' }, el('span', null, c.label), el('span', null, c.ok ? (c.detail || t('check_ok')) : c.timeout ? t('check_timeout') : t('check_fail'))),
      !c.ok && c.detail && cmd(c.detail),
      spec.blind_spot && el('span', { class: 'small muted' }, t('blind_spot') + ': ' + spec.blind_spot)));
  });
  if (lines.length) out.append(sec(t('h_checks'), lines));

  if ((card.ports || []).length) {
    out.append(sec(t('h_ports'), card.ports.map((p) => {
      const row = portRow(p.port, p.proto);
      return el('div', { class: 'item' },
        el('div', { class: 'item-head' }, el('span', { class: 'port-no' }, p.port + '/' + (p.proto || 'tcp')), row && tag(row.verdict, t('v_' + row.verdict))),
        el('span', { class: 'small' }, p.why),
        el('div', { class: 'kv small' }, el('span', null, t('meant_for')), el('span', null, reachText(p.reach))),
        row && el('div', { class: 'kv small' }, el('span', null, t('listens_on')), el('span', null, row.bound ? t('bound_' + row.bound) : t('nothing_listens'))),
        row && row.bound && row.bound !== 'local' && el('div', { class: 'kv small' }, el('span', null, t('firewall_lets_in')), el('span', null, letsIn(row.firewall))));
    })));
  }
  if ((card.links || []).length) out.append(sec(t('h_links'), el('div', { class: 'grid' }, card.links.map(linkRow))));
  if ((card.steps || []).length) {
    out.append(sec(t('h_steps'), card.steps.map((s, i) => el('div', { class: 'step' }, el('b', null, i + 1),
      el('div', { class: 'col' }, el('span', null, s.text), s.run && cmd(s.run))))));
  }
  if ((card.actions || []).length) out.append(sec(t('h_actions'), card.actions.map((a, i) => actionItem(a, { cardId: id, index: i }))));

  const chips = (list) => el('div', { class: 'row' }, list.map((cid) =>
    el('button', { type: 'button', class: 'chip', onclick: () => go('map', cid) }, D.cards[cid].name)));
  const needs = (card.needs || []).filter((n) => D.cards[n]);
  if (needs.length) out.append(sec(t('h_needs'), chips(needs)));
  const usedBy = Object.keys(D.cards).filter((cid) => (D.cards[cid].needs || []).includes(id));
  if (usedBy.length) out.append(sec(t('h_used_by'), chips(usedBy)));

  if ((card.places || []).length) {
    out.append(sec(t('h_places'), card.places.map((p) => el('div', { class: 'col' },
      el('div', { class: 'kv small' }, el('span', null, p.what), el('code', null, p.path)),
      p.caution && el('span', { class: 'small muted' }, t('caution') + ': ' + p.caution)))));
  }
  return out;
}

// ---- findings ---------------------------------------------------------------

function findingParts(f) {
  let kind = 'problem', title = '', meaning = '', command = '', extra = null;
  switch (f.kind) {
    case 'unit_failed': title = t('f_unit_failed', f); meaning = t('m_unit_failed'); command = 'sudo journalctl -u ' + f.name + ' -n 80 --no-pager'; break;
    case 'unit_stopped': title = t('f_unit_stopped', f); meaning = t('m_unit_stopped'); command = 'systemctl status ' + f.name + ' --no-pager'; break;
    case 'unit_missing': title = t('f_unit_missing', f); meaning = t('m_unit_missing'); command = 'systemctl list-unit-files | grep ' + f.name.split('.')[0]; break;
    case 'container_down': title = t('f_container_down', f); meaning = t('m_container'); command = 'sudo docker ps -a --filter name=' + f.name; break;
    case 'container_missing': title = t('f_container_missing', f); meaning = t('m_container'); command = 'sudo docker ps -a'; break;
    case 'check_failed': title = t('f_check_failed', f); meaning = t('m_check_failed'); command = f.run; extra = f.detail ? cmd(f.detail) : null; break;
    case 'port_only_local': title = t('f_port_only_local', f); meaning = t('m_port_only_local', { reach: reachText(f.reach) }); command = "sudo ss -tlnp 'sport = :" + parseInt(f.port, 10) + "'"; break;
    case 'port_not_listening': title = t('f_port_not_listening', f); meaning = t('m_port_not_listening'); command = "sudo ss -tlnp 'sport = :" + parseInt(f.port, 10) + "'"; break;
    case 'port_differs': title = t('f_port_differs', f); meaning = t('m_port_differs', { meant: reachText(f.meant), effective: letsIn(f.effective) }); command = 'sudo ufw status numbered'; break;
    case 'todo': kind = 'todo'; title = t('f_todo'); meaning = f.text; break;
    case 'undescribed': kind = 'new'; title = t('f_undescribed'); meaning = t('m_undescribed');
      extra = el('ul', { class: 'plain small' }, f.items.map((i) => el('li', null, t(i.kind) + ' ' + i.name + (i.detail ? ' (' + i.detail + ')' : '')))); break;
    default: title = f.kind;
  }
  return { kind, title, meaning, command, extra };
}

function findingView(f) {
  const card = f.card ? D.cards[f.card] : null;
  const { kind, title, meaning, command, extra } = findingParts(f);
  const buttons = [
    card && el('button', { type: 'button', class: 'btn', onclick: () => go('map', f.card) }, t('open_card')),
    f.key && loggedIn() && el('button', { type: 'button', class: 'btn', onclick: () => openMute(f) }, t('mute')),
  ].filter(Boolean);
  return el('section', { class: 'box' },
    el('div', { class: 'row' }, tag(kind, t('f_tag_' + kind)), el('h2', null, (card ? card.name + ': ' : '') + title)),
    el('div', { class: 'sec' }, el('h3', null, t('f_meaning')), el('p', null, meaning), extra),
    command && el('div', { class: 'sec' }, el('h3', null, t('f_next')), cmd(command)),
    buttons.length ? el('div', { class: 'row' }, buttons) : null);
}

function mutedView(f) {
  const card = f.card ? D.cards[f.card] : null;
  const { title } = findingParts(f);
  return el('div', { class: 'item' },
    el('div', { class: 'item-head' }, el('span', null, (card ? card.name + ': ' : '') + title),
      el('span', { class: 'small muted' }, t('muted_since', { date: stamp(f.since) }))),
    el('div', { class: 'kv small' }, el('span', null, t('mute_reason')), el('span', null, f.reason)),
    loggedIn() && el('div', null, el('button', { type: 'button', class: 'btn', onclick: (event) => unmute(f, event.currentTarget) }, t('unmute'))));
}

function openMute(f) {
  const { title } = findingParts(f);
  const area = el('textarea', { id: 'reason', class: 'field', rows: '3', maxlength: '300', required: true });
  const msg = el('div', { class: 'form-msg', role: 'alert' });
  const submit = el('button', { type: 'submit', class: 'btn primary' }, t('mute'));
  const cancel = el('button', { type: 'button', class: 'btn' }, t('cancel'));
  const form = el('form', { class: 'col' }, el('p', { class: 'small' }, title), field('reason', t('mute_reason'), area, t('mute_hint')), msg,
    el('div', { class: 'row end' }, cancel, submit));
  const dlg = modal(t('mute_title'), form);
  cancel.addEventListener('click', () => dlg.close());
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (submit.disabled) return;
    const reason = area.value.trim();
    if (reason.length < 3 || reason.length > 300) { msg.replaceChildren(el('p', null, t('err_reason'))); area.focus(); return; }
    submit.disabled = true;
    msg.replaceChildren();
    const reply = await api('api/mute', { key: f.key, reason });
    if (reply.status === 200 && reply.body.ok) {
      await loadData();
      dlg.close();
      redraw();
      return;
    }
    if (reply.status !== 401) msg.replaceChildren(el('p', null, errorText(reply)));
    else dlg.close();
    submit.disabled = false;
  });
  area.focus();
}

async function unmute(f, button) {
  if (button) button.disabled = true;
  const reply = await api('api/unmute', { key: f.key });
  if (reply.status === 200 && reply.body.ok) await loadData();
  else if (reply.status !== 401) ui.notice = errorText(reply);
  redraw();
}

function viewFindings() {
  const rank = { todo: 1, undescribed: 2 };
  const all = [...D.state.findings].sort((a, b) => (rank[a.kind] || 0) - (rank[b.kind] || 0));
  const live = all.filter((f) => !f.muted);
  const muted = all.filter((f) => f.muted);
  const details = muted.length ? el('details', { class: 'box', open: ui.mutedOpen }, el('summary', null, t('muted_h', { n: muted.length })),
    el('div', { class: 'col' }, muted.map(mutedView))) : null;
  if (details) details.addEventListener('toggle', () => { ui.mutedOpen = !!details.open; });
  // "Nothing to report" is only true for what could be read.
  const notes = ['units', 'containers', 'ports'].filter(unreadable).map((name) => unreadNote(name));
  if (!D.inventory) notes.unshift(unreadNote(null, t('no_inventory_page')));
  if (!Object.keys(D.cards).length) notes.push(unreadNote(null, t('empty_cards')));
  return el('div', { class: 'col narrow' }, notes, live.length ? live.map(findingView) : el('p', { class: 'box' }, t('none')), details);
}

// ---- security ---------------------------------------------------------------

function viewSecurity() {
  const s = D.security;
  const ports = s.ports.map((r) => {
    const card = r.card ? D.cards[r.card] : null;
    return el('div', { class: 'item' },
      el('div', { class: 'item-head' },
        el('span', { class: 'row' }, el('span', { class: 'port-no' }, r.port + '/' + r.proto),
          card ? el('button', { type: 'button', class: 'chip', onclick: () => go('map', r.card) }, card.name) : el('span', { class: 'muted' }, r.processes.join(', '))),
        tag(r.verdict, t('v_' + r.verdict))),
      r.why && el('span', { class: 'small' }, r.why),
      card && el('div', { class: 'kv small' }, el('span', null, t('meant_for')), el('span', null, reachText(r.meant))),
      el('div', { class: 'kv small' }, el('span', null, t('listens_on')), el('span', null, r.bound ? t('bound_' + r.bound) + (r.processes.length ? ' (' + r.processes.join(', ') + ')' : '') : t('nothing_listens'))),
      r.bound && r.bound !== 'local' && el('div', { class: 'kv small' }, el('span', null, t('firewall_lets_in')), el('span', null, s.firewall.state === 'unreadable' ? t('firewall_unknown') : letsIn(r.firewall))),
      r.firewall_only && el('span', { class: 'small muted' }, t('firewall_only')),
      r.docker && el('span', { class: 'small s-problem' }, t('docker_note')),
      r.bound && r.bound !== 'local' && el('details', null, el('summary', null, t('rules')),
        r.rules.length ? el('div', { class: 'col' }, r.rules.map(cmd)) : el('span', { class: 'small muted' }, t('no_rule'))));
  });

  const unlock = loggedIn() ? findChoice('blocked_addresses') : null;
  const blocks = unreadable('blocks')
    ? unreadNote('blocks', scanOf('blocks').why === 'missing_tool' ? t('sec_no_crowdsec') : null)
    : s.blocks.length ? s.blocks.map((b) => {
    const key = 'unblock:' + b.value;
    return el('div', { class: 'item' },
      el('div', { class: 'item-head' }, el('span', { class: 'port-no' }, b.value), el('span', { class: 'small muted' }, [b.country, t('block_left') + ' ' + b.remaining].filter(Boolean).join(' · '))),
      el('div', { class: 'kv small' }, el('span', null, t('block_reason')), el('span', null, b.reason)),
      unlock
        ? el('div', null, runButton(unlock.action, unlock.ref, key, t('btn_unblock'), b.value))
        : el('div', { class: 'kv small' }, el('span', null, t('block_unblock')), cmd('sudo cscli decisions delete --ip ' + b.value)),
      resultBox(key));
  }) : el('p', { class: 'small muted' }, t('sec_no_blocks'));

  const bans = s.bans.map((b) => el('div', { class: 'kv small' }, el('span', { class: 'port-no' }, b.address), el('span', null, b.comment || '')));

  // The ufw commands only make sense where ufw exists; the note about unlocking only where CrowdSec is read.
  const fwState = s.firewall.state;
  const noUfw = fwState === 'unreadable' && scanOf('firewall').why === 'missing_tool';
  const how = (noUfw && unreadable('blocks')) ? null : el('section', { class: 'box' }, el('h2', null, t('sec_change')),
    !noUfw && [
      el('div', { class: 'item' }, el('div', { class: 'item-head' }, el('span', null, t('how_list')), tag('read', t('level_read'))), cmd('sudo ufw status numbered')),
      el('div', { class: 'item' }, el('div', { class: 'item-head' }, el('span', null, t('how_allow')), tag('copy', t('level_copy'))),
        cmd('sudo ufw allow from ' + ph('ph_network') + ' to any port ' + ph('ph_port') + ' proto tcp'), el('span', { class: 'small muted' }, t('how_allow_note'))),
      el('div', { class: 'item' }, el('div', { class: 'item-head' }, el('span', null, t('how_delete')), tag('copy', t('level_copy'))),
        cmd('sudo ufw delete ' + ph('ph_number')), el('span', { class: 'small muted' }, t('how_delete_note'))),
    ],
    !unreadable('blocks') && el('p', { class: 'small muted' }, t('how_unblock_note')));

  const fwText = fwState === 'unreadable' ? (noUfw ? t('sec_no_firewall') : unreadText('firewall'))
    : fwState === 'inactive' ? t('sec_fw_inactive') : t('sec_firewall', s.firewall);
  return el('div', { class: 'col' },
    el('div', { class: 'note' }, fwText + ' ' + t('sec_router')),
    !D.process_names && !unreadable('ports') && el('div', { class: 'note' }, t('sec_no_names')),
    el('div', { class: 'two' },
      el('section', { class: 'box' }, el('h2', null, t('sec_ports')), unreadable('ports') ? unreadNote('ports') : ports),
      el('div', { class: 'col' },
        el('section', { class: 'box' }, el('h2', null, t('sec_blocks')), blocks),
        bans.length ? el('section', { class: 'box' }, el('h2', null, t('sec_bans')), bans) : null,
        how)));
}

// ---- storage ----------------------------------------------------------------

function viewStorage() {
  const st = D.storage;
  const usage = (target) => st.usage.find((u) => u.target === target);
  const bars = [];
  const mount = loggedIn() ? findChoice('unmounted_targets') : null;
  const planned = st.planned.map((p) => {
    const key = 'mount:' + p.target;
    return el('div', { class: 'item' },
      el('div', { class: 'item-head' }, el('code', null, p.target), tag(p.mounted ? 'as_meant' : 'problem', t(p.mounted ? 'sto_mounted' : 'sto_not_mounted'))),
      !p.mounted && (mount
        ? el('div', null, runButton(mount.action, mount.ref, key, t('sto_mount'), p.target))
        : el('div', { class: 'kv small' }, el('span', null, t('sto_mount')), cmd('sudo mount ' + p.target))),
      resultBox(key));
  });

  const disks = st.devices.filter((d) => d.type === 'disk' && !d.name.startsWith('zram')).map((disk) => {
    const parts = st.devices.filter((d) => d.parent === disk.name);
    const rows = (parts.length ? parts : [disk]).map((p) => {
      const where = p.mounted_at.filter((m) => !m.startsWith('['));
      const u = where.length ? usage(where[0]) : null;
      const fill = u ? el('i', { 'data-w': u.used_percent }) : null;
      if (fill) bars.push(fill);
      return el('div', { class: 'item' },
        el('div', { class: 'item-head' }, el('span', null, [p.name, p.label].filter(Boolean).join(' · ')), el('span', { class: 'small muted' }, [p.size, p.fstype].filter(Boolean).join(' · '))),
        el('div', { class: 'kv small' }, el('span', null, where.length ? t('sto_mounted') : t('sto_nowhere')), el('code', null, where.join(', '))),
        u && el('div', { class: 'col' }, el('div', { class: 'bar' + (u.used_percent >= 90 ? ' full' : ''), role: 'img', 'aria-label': t('sto_used', { p: u.used_percent }) }, fill),
          el('span', { class: 'small muted' }, t('sto_used', { p: u.used_percent }))));
    });
    return el('section', { class: 'box' },
      el('div', { class: 'row' }, el('h2', null, disk.model || disk.name), el('span', { class: 'small muted' }, [disk.size, disk.transport].filter(Boolean).join(' · '))), rows);
  });

  // The card whose actions mount things: the one with an action that picks an unmounted target,
  // else the first one with a "mount" command. Any machine, any card name.
  const hasAction = (id, test) => (D.cards[id].actions || []).some(test);
  const cardId = Object.keys(D.cards).find((id) => hasAction(id, (a) => a.choose === 'unmounted_targets'))
    || Object.keys(D.cards).find((id) => hasAction(id, (a) => /\bmount\b/.test(String(a.run))));
  const card = cardId ? D.cards[cardId] : null;
  if (unreadable('storage')) return el('div', { class: 'col' }, unreadNote('storage'),
    card ? el('section', { class: 'box' }, el('h2', null, t('h_actions')), (card.actions || []).map((a, i) => actionItem(a, { cardId, index: i }))) : null);
  const page = el('div', { class: 'col' },
    el('div', { class: 'two' },
      el('section', { class: 'box' }, el('h2', null, t('sto_planned')), planned),
      card ? el('section', { class: 'box' }, el('h2', null, t('h_actions')), (card.actions || []).map((a, i) => actionItem(a, { cardId, index: i }))) : null),
    el('h2', null, t('sto_devices')), el('div', { class: 'two' }, disks));
  // Widths come from data; set them here because the page allows no inline styles.
  for (const fill of bars) fill.style.width = fill.getAttribute('data-w') + '%';
  return page;
}

// ---- history ------------------------------------------------------------------

const SERIES_CLASSES = 6;

function seriesOf(def, rows) {
  const value = (v) => (typeof v === 'number' && isFinite(v) ? v : null);
  if (def.disks) {
    const targets = [...new Set(rows.flatMap((r) => Object.keys(r.disk || {})))].sort();
    return targets.map((name) => ({ name, pts: rows.map((r) => ({ t: r.t, v: value((r.disk || {})[name]) })) }));
  }
  return [{ name: null, pts: rows.map((r) => ({ t: r.t, v: value(r[def.key]) })) }];
}

// The path of one line. A reading of null breaks the line; a lone reading is a dot.
function linePath(pts, x, y) {
  let d = '', open = false;
  pts.forEach((p, i) => {
    if (p.v == null) { open = false; return; }
    const prevNull = i === 0 || pts[i - 1].v == null;
    const nextNull = i === pts.length - 1 || pts[i + 1].v == null;
    const cx = Math.round(x(p.t) * 10) / 10, cy = Math.round(y(p.v) * 10) / 10;
    d += (open ? 'L' : 'M') + cx + ' ' + cy;
    if (prevNull && nextNull) d += 'h0.01';
    open = true;
  });
  return d;
}

function chart(def, rows) {
  const title = t(def.title);
  const series = seriesOf(def, rows);
  const counted = (s) => s.pts.filter((p) => p.v != null).length;
  const box = (...kids) => el('section', { class: 'box chart' }, el('h2', null, title), kids);
  // A machine without this sensor never delivers a reading: say so instead of promising that it fills up.
  if (rows.length >= 2 && series.every((s) => counted(s) === 0)) return box(el('p', { class: 'small muted' }, t('hist_none')));
  if (rows.length < 2 || !series.some((s) => counted(s) >= 2)) return box(el('p', { class: 'small muted' }, t('hist_few')));

  const values = series.flatMap((s) => s.pts.filter((p) => p.v != null).map((p) => p.v));
  const min = Math.min(...values), max = Math.max(...values);
  const lasts = series.map((s) => { const v = s.pts.filter((p) => p.v != null); return v.length ? v[v.length - 1].v : null; });
  const last = Math.max(...lasts.filter((v) => v != null));
  let lo = 0, hi = 100;
  if (!def.fixed) { const space = (max - min) * 0.15 || 1; lo = min - space; hi = max + space; }
  const W = 320, H = 110, PAD = 6;
  const t0 = rows[0].t, t1 = rows[rows.length - 1].t;
  const x = (ts) => PAD + ((ts - t0) / Math.max(t1 - t0, 1)) * (W - 2 * PAD);
  const y = (v) => H - PAD - ((v - lo) / (hi - lo)) * (H - 2 * PAD);
  const withDate = ui.range === 'week';
  const from = clock(t0, withDate), to = clock(t1, withDate);
  const unit = def.unit ? ' ' + def.unit : '';
  const shown = (v) => num(v, def.digits) + unit;
  const label = t('chart_aria', { title, last: shown(last), min: shown(min), max: shown(max), from, to });

  const grid = [0, 0.5, 1].map((f) => svg('line', { class: 'gl', x1: PAD, x2: W - PAD, y1: PAD + f * (H - 2 * PAD), y2: PAD + f * (H - 2 * PAD) }));
  const paths = series.map((s, i) => svg('path', { class: 'ln ln-' + (i % SERIES_CLASSES), d: linePath(s.pts, x, y) }));
  const legend = def.disks ? el('ul', { class: 'legend plain small' }, series.map((s, i) => el('li', null,
    el('span', { class: 'swatch sw-' + (i % SERIES_CLASSES), 'aria-hidden': 'true' }), s.name + ': ' + (lasts[i] == null ? '–' : shown(lasts[i]))))) : null;
  return box(
    el('div', { class: 'row' }, el('span', { class: 'big' }, shown(last)), el('span', { class: 'small muted' }, t('chart_now'))),
    svg('svg', { class: 'plot', viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': label }, grid, paths),
    el('div', { class: 'row between small muted' }, el('span', null, from), el('span', null, to)),
    el('div', { class: 'row small' }, el('span', null, t('chart_min') + ': ' + shown(min)), el('span', null, t('chart_max') + ': ' + shown(max))),
    legend);
}

function logRow(e) {
  const card = e.card && D.cards[e.card] ? D.cards[e.card].name : e.card;
  const peer = e.peer ? t('hist_log_from', { peer: e.peer }) : '';
  let what, result = null, bad = false;
  if (e.event === 'action') {
    what = [card, e.label].filter(Boolean).join(': ');
    bad = e.timed_out || e.exit !== 0;
    result = e.timed_out ? t('res_timeout', { n: Math.round(e.seconds || 0) }) : e.exit === 0 ? t('res_done') : t('res_failed', { n: e.exit });
  } else if (e.event === 'login_failed') { what = t('hist_log_login_failed'); bad = true; }
  else if (e.event === 'login') what = t('hist_log_login');
  else what = String(e.event);
  return el('div', { class: 'item' },
    el('div', { class: 'item-head' }, el('span', null, what), result && tag(bad ? 'failed' : 'done', result)),
    el('div', { class: 'small muted' }, [stamp(e.time), peer].filter(Boolean).join(' · ')),
    e.event === 'action' && e.command && el('code', { class: 'small muted cmd-line' }, e.command));
}

async function loadLog() {
  ui.logLoading = true;
  const reply = await api('api/log?limit=100');
  ui.logLoading = false;
  ui.log = reply.status === 200 && reply.body.ok ? { entries: reply.body.entries || [] } : { error: true };
  if (ui.tab === 'history' && loggedIn()) redraw();
}

function viewHistory() {
  const h = D.history || { day: [], week: [], changes: [] };
  const rows = ui.range === 'week' ? h.week : h.day;
  const defs = [
    { key: 'temp_c', title: 'chart_temp', unit: '°C', digits: 1 },
    { key: 'load1', title: 'chart_load', unit: '', digits: 2 },
    { key: 'mem', title: 'chart_mem', unit: '%', digits: 0, fixed: true },
    { disks: true, title: 'chart_disk', unit: '%', digits: 0, fixed: true },
  ];
  const toggle = el('div', { class: 'row', role: 'group', 'aria-label': t('hist_range') }, ['day', 'week'].map((r) =>
    el('button', { type: 'button', class: 'tab', 'aria-pressed': ui.range === r ? 'true' : 'false',
      onclick: () => { ui.range = r; redraw(); } }, t('hist_' + r))));

  const changes = (h.changes || []).length
    ? h.changes.map((c) => {
      const card = D.cards[c.card];
      return el('div', { class: 'item' },
        el('div', { class: 'item-head' },
          card ? el('button', { type: 'button', class: 'chip', onclick: () => go('map', c.card) }, card.name) : el('span', null, c.card),
          el('span', { class: 'small muted' }, clock(c.t, true))),
        el('span', { class: 'small' }, t('st_' + c.from) + ' → ' + t('st_' + c.to)));
    })
    : el('p', { class: 'small muted' }, t('hist_no_changes'));

  let log;
  if (!loggedIn()) log = el('p', { class: 'small muted' }, t('hist_log_login_needed'));
  else {
    if (!ui.log && !ui.logLoading) loadLog();
    if (!ui.log) log = el('p', { class: 'small muted' }, t('hist_log_loading'));
    else if (ui.log.error) log = el('p', { class: 'small muted' }, t('hist_log_error'));
    else if (!ui.log.entries.length) log = el('p', { class: 'small muted' }, t('hist_log_none'));
    else log = [...ui.log.entries].sort((a, b) => String(b.time).localeCompare(String(a.time))).map(logRow);
  }

  return el('div', { class: 'col' }, toggle,
    el('div', { class: 'two' }, defs.map((def) => chart(def, rows))),
    el('div', { class: 'two' },
      el('section', { class: 'box' }, el('h2', null, t('hist_changes')), changes),
      el('section', { class: 'box' }, el('h2', null, t('hist_log')), log)));
}

// ---- files --------------------------------------------------------------------

function viewFiles() {
  const f = D.files;
  const entry = (e) => el('div', { class: 'item' },
    el('div', { class: 'item-head' }, el('code', null, '~/' + e.name + (e.kind === 'dir' ? '/' : '')), e.git ? tag('new', t('files_git')) : null),
    e.places.length ? e.places.map((p) => el('div', { class: 'col' },
      el('span', { class: 'small' }, (p.path === '~/' + e.name ? '' : p.path.slice(e.name.length + 3) + ': ') + p.what),
      p.caution && el('span', { class: 'small muted' }, t('caution') + ': ' + p.caution)))
      : el('span', { class: 'small muted' }, t('files_unknown')));
  const place = (p) => el('div', { class: 'item' }, el('div', { class: 'item-head' }, el('code', null, p.path),
    el('button', { type: 'button', class: 'chip', onclick: () => go('map', p.card) }, D.cards[p.card].name)),
    el('span', { class: 'small' }, p.what), p.caution && el('span', { class: 'small muted' }, t('caution') + ': ' + p.caution));
  // Invented examples without a card: shown, never run.
  const folder = ph('ph_folder'), file = ph('ph_file');
  const aside = '~/' + t('fc_aside_dir') + '-$(date +%F)';
  const commands = [
    ['fc_look', 'ls -la ' + folder, 'read'], ['fc_read', 'less ' + file, 'read'], ['fc_size', 'du -sh ' + folder, 'read'],
    ['fc_find', 'grep -rn "' + ph('ph_word') + '" ' + folder, 'read'], ['fc_edit', 'nano ' + file, 'copy', 'fc_edit_note'],
    ['fc_copy', 'cp -a ' + ph('ph_source') + ' ' + ph('ph_target'), 'copy'], ['fc_move', 'mv ' + ph('ph_source') + ' ' + ph('ph_target'), 'copy'],
    ['fc_remove', 'mkdir -p ' + aside + ' && mv ' + file + ' ' + aside + '/', 'copy', 'fc_remove_note'],
  ].map(([label, run, level, note]) => actionItem({ label: t(label), run, level, effect: note ? t(note) : '' }));
  return el('div', { class: 'two' },
    el('div', { class: 'col' },
      unreadable('home') && unreadNote('home'),
      el('section', { class: 'box' }, el('div', { class: 'row' }, el('h2', null, t('files_home')), el('code', { class: 'small muted' }, f.home)), f.entries.map(entry)),
      el('section', { class: 'box' }, el('h2', null, t('files_elsewhere')), f.elsewhere.map(place))),
    el('section', { class: 'box' }, el('h2', null, t('files_commands')), commands));
}

// ---- day to day ---------------------------------------------------------------

function viewDaily() {
  const links = Object.values(D.cards).flatMap((c) => c.links || []);
  const groups = D.areas.map((area) => {
    const cards = Object.values(D.cards).filter((c) => c.area === area.id && (c.actions || []).length);
    if (!cards.length) return null;
    return el('section', { class: 'box' }, el('h2', null, area.name), cards.map((c) => el('div', { class: 'sec' },
      el('button', { type: 'button', class: 'chip', onclick: () => go('map', c.id) }, c.name),
      c.actions.map((a, i) => actionItem(a, { cardId: c.id, index: i })))));
  });
  return el('div', { class: 'col' },
    el('section', { class: 'box' }, el('h2', null, t('daily_links')), el('div', { class: 'grid' }, links.map(linkRow))),
    el('h2', null, t('daily_commands')), el('div', { class: 'two' }, groups));
}

// ---- start --------------------------------------------------------------------

function render() {
  const views = { map: viewMap, findings: viewFindings, security: viewSecurity, storage: viewStorage, history: viewHistory, files: viewFiles, daily: viewDaily };
  cardEls = new Map();
  overlay = null;
  const app = document.getElementById('app');
  app.replaceChildren(header(), nav(), views[ui.tab]());
  drawLinks();
}

async function start() {
  const app = document.getElementById('app');
  if (!(await loadData())) {
    app.replaceChildren(el('p', { class: 'pad' }, 'PiHub: no data'));
    return;
  }
  await loadSession();
  document.documentElement.lang = D.lang;
  render();
}

start();

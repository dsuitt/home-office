/* Home Money: core, charts, shell, and the Income & Expense Log. All money values are integer cents. */
'use strict';
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmt = (c, d = 0) => (c < 0 ? '−' : '') + '$' + (Math.abs(c) / 100).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
const k = c => { const n = Math.abs(c) / 100; return c === 0 ? '$0' : (c < 0 ? '−' : '') + '$' + (n >= 1e6 ? (n / 1e6).toFixed(2) + 'M' : n >= 1000 ? (n / 1000).toFixed(n % 1000 === 0 ? 0 : 1) + 'k' : Math.round(n)); };
const pct = (a, b) => b ? Math.round(a / b * 100) + '%' : '—';
const sum = a => a.reduce((x, y) => x + (y || 0), 0);
const cents = v => Math.round(parseFloat(String(v).replace(/[$,\s]/g, '')) * 100) || 0;
const M = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const MF = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
const C = ['var(--c1)', 'var(--c2)', 'var(--c3)', 'var(--c5)', 'var(--c6)', 'var(--c4)', 'var(--muted)'];
const dshort = iso => { const [, m, d] = iso.split('-'); return `${M[+m - 1]} ${+d}`; };

/* ---------- API ---------- */
async function api(path, opts = {}) {
  const o = { method: opts.method || 'GET', headers: { 'X-Requested-With': 'money' }, credentials: 'same-origin' };
  if (opts.body instanceof FormData) o.body = opts.body;
  else if (opts.body !== undefined) { o.body = JSON.stringify(opts.body); o.headers['Content-Type'] = 'application/json'; }
  const r = await fetch('/api' + path, o);
  if (r.status === 401 && path !== '/login') { showLogin(); throw new Error('Sign in'); }
  const data = r.headers.get('content-type')?.includes('json') ? await r.json() : await r.text();
  if (!r.ok) throw new Error(data?.detail || 'Something went wrong');
  return data;
}
const post = (p, body = {}) => api(p, { method: 'POST', body });
const patch = (p, body) => api(p, { method: 'PATCH', body });
const put = (p, body) => api(p, { method: 'PUT', body });
const del = p => api(p, { method: 'DELETE' });

let toastTimer;
function toast(html, action) {
  const t = $('#toast'); t.innerHTML = html + (action ? `<button id="toast-act">${esc(action.label)}</button>` : '');
  t.style.display = 'block'; clearTimeout(toastTimer);
  if (action) $('#toast-act').onclick = () => { t.style.display = 'none'; action.run(); };
  toastTimer = setTimeout(() => t.style.display = 'none', action ? 9000 : 4000);
}
const fail = e => { if (e.message !== 'Sign in') toast(esc(e.message)); };

/* ---------- charts (from the dashboard mockup) ---------- */
function niceMax(v) { if (v <= 0) return 100; const p = Math.pow(10, Math.floor(Math.log10(v))); const m = v / p; return (m <= 1 ? 1 : m <= 2 ? 2 : m <= 2.5 ? 2.5 : m <= 5 ? 5 : 10) * p; }
function bars(labels, series, colors, h = 210) {
  const W = 640, H = h, L = 46, R = 8, T = 10, B = 26, max = niceMax(Math.max(0, ...series.flat().map(v => v || 0)));
  const y = v => T + (H - T - B) * (1 - v / max), bw = (W - L - R) / labels.length;
  let s = `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="bar chart">`;
  for (let i = 0; i <= 4; i++) { const v = max * i / 4; s += `<line class="gridl" x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/><text x="${L - 6}" y="${y(v) + 4}" text-anchor="end">${k(v)}</text>`; }
  labels.forEach((lb, i) => {
    const n = series.length, gw = bw * .72, w = gw / n, x0 = L + i * bw + bw * .14;
    series.forEach((ser, j) => { const v = ser[i]; if (v == null) return; s += `<rect x="${x0 + j * w}" y="${y(Math.max(v, 0))}" width="${Math.max(w - 2, 1)}" height="${y(0) - y(Math.max(v, 0))}" fill="${colors[j]}"><title>${esc(lb)}: ${fmt(v)}</title></rect>`; });
    s += `<text x="${L + i * bw + bw / 2}" y="${H - 8}" text-anchor="middle">${esc(lb)}</text>`;
  });
  return s + '</svg>';
}
function line(labels, series, colors, { h = 210, min = 0, dash = [] } = {}) {
  const W = 640, H = h, L = 52, R = 14, T = 12, B = 26, all = series.flat().filter(v => v != null);
  if (!all.length) return '<p class="empty">Nothing to chart yet.</p>';
  const lo = Math.min(min, ...all), hi = niceMax(Math.max(...all) - lo) + lo;
  const x = i => L + (W - L - R) * (labels.length > 1 ? i / (labels.length - 1) : .5), y = v => T + (H - T - B) * (1 - (v - lo) / (hi - lo));
  let s = `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="line chart">`;
  for (let i = 0; i <= 4; i++) { const v = lo + (hi - lo) * i / 4; s += `<line class="gridl" x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/><text x="${L - 6}" y="${y(v) + 4}" text-anchor="end">${k(Math.round(v))}</text>`; }
  labels.forEach((lb, i) => s += `<text x="${x(i)}" y="${H - 8}" text-anchor="middle">${esc(lb)}</text>`);
  series.forEach((ser, j) => {
    const pts = ser.map((v, i) => v == null ? null : [x(i), y(v), v, labels[i]]).filter(Boolean); if (!pts.length) return;
    const d = pts.map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join(' ');
    if (j === 0 && !dash[0]) s += `<path d="${d} L${pts.at(-1)[0]} ${y(Math.max(lo, 0))} L${pts[0][0]} ${y(Math.max(lo, 0))} Z" fill="${colors[j]}" opacity=".13"/>`;
    s += `<path d="${d}" fill="none" stroke="${colors[j]}" stroke-width="2.4" ${dash[j] ? 'stroke-dasharray="6 5"' : ''}/>`;
    pts.forEach(p => { if (!dash[j]) s += `<circle cx="${p[0]}" cy="${p[1]}" r="3" fill="${colors[j]}"><title>${esc(p[3])}: ${fmt(p[2])}</title></circle>`; });
  });
  return s + '</svg>';
}
function donut(rows, size = 170, center = '') {
  const tot = sum(rows.map(r => Math.max(r[1], 0))) || 1, r = size / 2 - 14, c = 2 * Math.PI * r; let off = 0;
  let s = `<svg viewBox="0 0 ${size} ${size}" width="${size}" style="max-width:100%" role="img" aria-label="donut chart"><g transform="rotate(-90 ${size / 2} ${size / 2})">`;
  rows.forEach((row, i) => { const len = Math.max(row[1], 0) / tot * c; s += `<circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="${C[i % C.length]}" stroke-width="24" stroke-dasharray="${Math.max(len - 1.5, 0)} ${c}" stroke-dashoffset="${-off}"/>`; off += len; });
  return s + `</g><text x="50%" y="50%" text-anchor="middle" dominant-baseline="middle" style="font-size:16px;font-weight:800;fill:var(--ink)">${center}</text></svg>`;
}
const donutBlock = (rows, center) => { const t = sum(rows.map(x => Math.max(x[1], 0))); return `<div style="display:flex;flex-wrap:wrap;gap:14px;align-items:center;justify-content:center">${donut(rows, 170, center)}<div class="legend" style="display:grid;gap:4px">${rows.map((r, i) => `<span><i style="background:${C[i % C.length]}"></i>${esc(r[0])} · ${pct(Math.max(r[1], 0), t)}</span>`).join('')}</div></div>`; };
const hbar = (rows, color = 'var(--c2)') => { const max = Math.max(...rows.map(r => Math.abs(r[1])), 1); return `<div class="grid" style="gap:8px">${rows.map(([n, v]) => `<div style="display:grid;grid-template-columns:minmax(0,1.2fr) minmax(0,1.8fr) auto;gap:10px;align-items:center;font-size:12.5px"><span>${esc(n)}</span><div class="prog"><i style="width:${Math.abs(v) / max * 100}%;background:${color}"></i></div><span class="num">${fmt(v)}</span></div>`).join('')}</div>`; };
const panel = (title, body, pad = true) => `<section class="panel"><div class="bar-h">${title}</div>${pad ? `<div class="pad">${body}</div>` : body}</section>`;

/* ---------- state ---------- */
const TABS = [['snap', 'Financial Snapshot'], ['plan', 'Categories & Goals'], ['tx', 'Income & Expense Log'], ['annual', 'Annual Spending Plan'], ['review', 'Month in Review'],
  ['nw', 'Net Worth'], ['dash', 'Dashboard Summary'], ['rec', 'Recurring'], ['learn', 'What I’ve Learned'], ['set', 'Settings']];
const state = { tab: 'snap', month: null, boot: null, year: null, f: { q: '', status: '', account: 0, category: 0, all: false } };
try { const s = JSON.parse(localStorage.getItem('money-ui') || 'null'); if (s && TABS.some(t => t[0] === s.tab)) state.tab = s.tab; } catch (e) { }
const VIEW = {}, AFTER = {};
const ym = () => state.month.split('-').map(Number);          // [year, month 1-12]
const mi = () => ym()[1] - 1;                                  // month index 0-11
const monthName = () => `${MF[mi()]} ${ym()[0]}`;
const catById = id => state.boot.categories.find(c => c.id === id);
const bucketName = key => state.boot.buckets.find(b => b.key === key)?.name || '';

/* The mockup's "book" model, built from live data: income lines + three buckets of lines with plan[12] / act[12]. */
function book() {
  const L = state.year.lines, by = b => L.filter(l => l.bucket === b && (!l.archived || sum(l.act) || sum(l.plan)));
  const B = state.boot.buckets;
  return { income: by('income'), buckets: ['ess', 'non', 'goal'].map(key => ({ ...B.find(b => b.key === key), lines: by(key) })), last: state.year.last };
}
const incT = (B, w, i) => sum(B.income.map(l => l[w][i]));
const bT = (bk, w, i) => sum(bk.lines.map(l => l[w][i]));

function catOptions(sel, { blank = 'Choose…', split = false } = {}) {
  const cats = state.boot.categories.filter(c => !c.archived || c.id === sel);
  return `<option value="">${blank}</option>` + state.boot.buckets.map(b => {
    const cs = cats.filter(c => c.bucket === b.key);
    return cs.length ? `<optgroup label="${esc(b.name)}">${cs.map(c => `<option value="${c.id}" ${c.id === sel ? 'selected' : ''}>${esc(c.name)}</option>`).join('')}</optgroup>` : '';
  }).join('') + (split ? `<optgroup label="Other"><option value="split">Split it…</option><option value="new">New category…</option></optgroup>` : '');
}

/* ---------- shell ---------- */
async function load() {
  state.boot = await api('/boot');
  if (!state.month) state.month = state.boot.today.slice(0, 7);
  state.year = await api('/year/' + ym()[0]);
}
async function refresh() { try { await load(); await render(); } catch (e) { fail(e); } }
function shiftMonth(d) { let [y, m] = ym(); m += d; if (m < 1) { m = 12; y--; } if (m > 12) { m = 1; y++; } state.month = `${y}-${String(m).padStart(2, '0')}`; refresh(); }

function header() {
  const b = state.boot, open = b.counts.ask + b.counts.sug;
  $('#top').innerHTML = `<div>
      ${b.demo ? '<span class="sample">Sample data, made up. Clear it in Settings before linking your bank.</span><br>' : ''}
      <h1>Home Money</h1></div>
    <div class="monthnav">
      <button class="btn" id="m-prev" aria-label="Previous month">‹</button><b>${monthName()}</b><button class="btn" id="m-next" aria-label="Next month">›</button>
      <button class="btn" id="m-now">Today</button>
      ${b.items.length ? '<button class="btn" id="sync">Sync now</button>' : ''}
      ${open ? `<button class="btn pri" id="go-review">Review ${open}</button>` : ''}
    </div>`;
  $('#m-prev').onclick = () => shiftMonth(-1); $('#m-next').onclick = () => shiftMonth(1);
  $('#m-now').onclick = () => { state.month = b.today.slice(0, 7); refresh(); };
  if ($('#sync')) $('#sync').onclick = syncNow;
  if ($('#go-review')) $('#go-review').onclick = () => reviewCards();
}
async function syncNow() {
  toast('Syncing with your banks…');
  try {
    const res = await post('/sync'); const bad = res.filter(r => !r.ok);
    await refresh();
    toast(bad.length ? `${bad.length} connection${bad.length > 1 ? 's need' : ' needs'} attention. See Settings.` : `Up to date. ${sum(res.map(r => r.added))} new transactions.`);
  } catch (e) { fail(e); }
}

async function render() {
  try { localStorage.setItem('money-ui', JSON.stringify({ tab: state.tab })); } catch (e) { }
  header();
  const open = state.boot.counts.ask + state.boot.counts.sug;
  $('#tabs').innerHTML = TABS.map(([id, n]) => `<button data-t="${id}" aria-current="${state.tab === id}">${id === 'review' ? MF[mi()] + ' in Review' : n}${id === 'tx' && open ? `<span class="badge">${open}</span>` : ''}</button>`).join('');
  $$('#tabs button').forEach(b => b.onclick = () => { state.tab = b.dataset.t; render(); window.scrollTo({ top: 0 }); });
  const cur = $('#tabs [aria-current="true"]'); if (cur) cur.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  try {
    const html = await VIEW[state.tab](book());
    $('#app').innerHTML = html;
    if (AFTER[state.tab]) AFTER[state.tab](book());
  } catch (e) { fail(e); }
}

function showLogin() {
  $('#top').innerHTML = ''; $('#tabs').innerHTML = '';
  $('#app').innerHTML = `<form class="login panel" id="login"><div class="bar-h">Home Money</div><div class="pad" style="display:grid;gap:10px">
    <label class="q">Name<input class="in" id="l-name" autocomplete="username" required></label>
    <label class="q">Password<input class="in" id="l-pw" type="password" autocomplete="current-password" required></label>
    <button class="btn pri" style="padding:9px">Sign in</button><p class="note" id="l-err"></p></div></form>`;
  $('#login').onsubmit = async e => {
    e.preventDefault();
    try { await post('/login', { name: $('#l-name').value, password: $('#l-pw').value }); await refresh(); }
    catch (err) { $('#l-err').textContent = err.message; }
  };
}

/* ---------- dialog helper ---------- */
function dialog(title, body, after) {
  const d = $('#dlg');
  d.innerHTML = `<div class="bar-h">${title}</div><div class="pad">${body}</div>`;
  if (!d.open) d.showModal();
  d.onclick = e => { if (e.target === d) d.close(); };
  if (after) after(d);
  return d;
}

/* ===== 3. Income & Expense Log ===== */
const STATUS = { auto: ['p-auto', 'Sorted for you'], suggested: ['p-sug', 'Suggested'], needs_you: ['p-ask', 'Needs you'], done: ['p-ok', 'Done'] };
let txCache = { items: [], total: 0 };
function txQuery() {
  const f = state.f, p = new URLSearchParams();
  if (!f.all) p.set('month', state.month);
  if (f.q) p.set('q', f.q); if (f.status) p.set('status', f.status);
  if (f.account) p.set('account', f.account); if (f.category) p.set('category', f.category);
  return '/transactions?' + p;
}
function txRow(t) {
  const l = STATUS[t.status], income = t.amount_cents > 0;
  return `<tr class="${t.status === 'needs_you' ? 'ask' : ''} ${t.hidden ? 'hid' : ''}" data-id="${t.id}">
    <td class="num" style="white-space:nowrap">${dshort(t.date)}${state.f.all ? ' ’' + t.date.slice(2, 4) : ''}</td>
    <td class="muted" style="white-space:nowrap">${esc(t.account)}</td>
    <td><button class="mname" data-open="${t.id}">${esc(t.merchant)}</button>${t.pending ? ' <span class="pill p-auto">pending</span>' : ''}${t.parent_id ? ' <span class="pill p-auto">split</span>' : ''}${t.hidden ? ' <span class="pill p-auto">hidden</span>' : ''}
      <span class="raw" title="${esc(t.raw)}">${esc(t.raw)}</span></td>
    <td class="r num" style="color:var(--good)">${income ? fmt(t.amount_cents, 2) : ''}</td><td class="r num">${income ? '' : fmt(-t.amount_cents, 2)}</td>
    <td><select class="cat" data-cat="${t.id}" aria-label="Category for ${esc(t.merchant)}">${catOptions(t.category_id, { split: !t.parent_id })}</select></td>
    <td class="muted" style="white-space:nowrap">${t.bucket ? esc(bucketName(t.bucket)) : ''}</td>
    <td>${t.status === 'suggested' ? `<button class="pill p-sug" style="border:0;cursor:pointer" data-ok="${t.id}">Suggested · OK?</button>` : `<span class="pill ${l[0]}">${l[1]}</span>`}</td>
    <td class="note" style="min-width:200px">${esc(t.reason)}${t.note ? `<div style="color:var(--ink)">📝 ${esc(t.note)}</div>` : ''}${t.tags ? `<div>${t.tags.split(',').filter(Boolean).map(x => `<span class="pill p-auto">${esc(x.trim())}</span>`).join(' ')}</div>` : ''}</td></tr>`;
}
VIEW.tx = async () => {
  txCache = await api(txQuery());
  const f = state.f, rows = txCache.items, b = state.boot;
  const ask = rows.filter(t => t.status === 'needs_you').length, sug = rows.filter(t => t.status === 'suggested').length, auto = rows.filter(t => t.status === 'auto').length;
  const scope = f.all ? 'All time' : monthName();
  return `<div class="grid">
   <div class="callout">Every row comes in from your accounts and is sorted from what you’ve done before. Fix the yellow rows and confirm the blue ones. Each answer teaches it for next time.
     ${rows.length ? (ask + sug === 0 ? `<b>${esc(scope)} is fully sorted ✓</b>` : `<b>${ask} need you · ${sug} to confirm · ${auto} sorted for you.</b>`) : ''}</div>
   <div class="filters">
     <input type="search" id="f-q" placeholder="Search merchant, note, tag or amount" value="${esc(f.q)}" aria-label="Search">
     <select id="f-status" aria-label="Status"><option value="">All statuses</option>${[['open', 'Needs review'], ['needs_you', 'Needs you'], ['suggested', 'Suggested'], ['auto', 'Sorted for you'], ['done', 'Done']].map(([v, n]) => `<option value="${v}" ${f.status === v ? 'selected' : ''}>${n}</option>`).join('')}</select>
     <select id="f-acct" aria-label="Account"><option value="0">All accounts</option>${b.accounts.map(a => `<option value="${a.id}" ${f.account === a.id ? 'selected' : ''}>${esc(a.name)}</option>`).join('')}</select>
     <select id="f-cat" aria-label="Category">${catOptions(f.category, { blank: 'All categories' })}</select>
     <label class="rowx" style="font-size:13px"><input type="checkbox" id="f-all" ${f.all ? 'checked' : ''}> All months</label>
   </div>
   <div class="rowx">
     ${sug ? `<button class="btn" id="ok-all">Confirm all ${sug} suggestions</button>` : ''}
     ${ask + sug ? `<button class="btn pri" id="cards">Review one at a time</button>` : ''}
     <button class="btn" id="add-tx">Add a transaction</button>
     <span class="note" style="margin-left:auto">${txCache.total} transactions · net ${fmt(txCache.sum_cents, 2)}</span>
   </div>
   ${panel(`Income & expense log · ${esc(scope)}`, rows.length ? `<div class="scroll"><table class="log"><thead><tr><th>Date</th><th>Account</th><th>Description</th><th class="r">Income</th><th class="r">Expense</th><th>Category</th><th>Bucket</th><th>Status</th><th>Notes</th></tr></thead>
     <tbody id="tx-body">${rows.map(txRow).join('')}</tbody></table></div>` : `<p class="empty">${b.accounts.length ? 'Nothing here for these filters.' : 'No accounts yet. Connect your bank or import a CSV in Settings.'}</p>`, false)}
   ${txCache.total > rows.length ? `<p class="note">Showing the newest ${rows.length}. Narrow the search to see the rest.</p>` : ''}
  </div>`;
};
AFTER.tx = () => {
  const f = state.f; let timer;
  $('#f-q').oninput = e => { clearTimeout(timer); timer = setTimeout(async () => { f.q = e.target.value.trim(); await render(); const q = $('#f-q'); q.focus(); q.setSelectionRange(q.value.length, q.value.length); }, 300); };
  $('#f-status').onchange = e => { f.status = e.target.value; render(); };
  $('#f-acct').onchange = e => { f.account = +e.target.value; render(); };
  $('#f-cat').onchange = e => { f.category = +e.target.value; render(); };
  $('#f-all').onchange = e => { f.all = e.target.checked; render(); };
  $$('[data-cat]').forEach(s => s.onchange = () => setCategory(+s.dataset.cat, s.value));
  $$('[data-ok]').forEach(b => b.onclick = async () => { try { await post(`/transactions/${b.dataset.ok}/confirm`); await refresh(); } catch (e) { fail(e); } });
  $$('[data-open]').forEach(b => b.onclick = () => txDetail(+b.dataset.open));
  if ($('#ok-all')) $('#ok-all').onclick = async () => { try { const r = await post('/transactions/confirm-all', { month: f.all ? '' : state.month }); await refresh(); toast(`Confirmed ${r.confirmed}.`); } catch (e) { fail(e); } };
  if ($('#cards')) $('#cards').onclick = () => reviewCards();
  $('#add-tx').onclick = addTxDialog;
};

async function newCategory(after) {
  dialog('New category', `<label>Name<input class="in" id="nc-name"></label>
    <label>Bucket<select class="in" id="nc-bucket">${state.boot.buckets.map(b => `<option value="${b.key}" ${b.key === 'non' ? 'selected' : ''}>${esc(b.name)} · ${esc(b.hint)}</option>`).join('')}</select></label>
    <div class="rowx sp"><button class="btn" id="nc-cancel">Cancel</button><button class="btn pri" id="nc-ok">Add</button></div>`, d => {
    $('#nc-cancel').onclick = () => { d.close(); render(); };
    $('#nc-ok').onclick = async () => {
      try { const r = await post('/categories', { name: $('#nc-name').value, bucket: $('#nc-bucket').value }); d.close(); await load(); if (after) await after(r.id); else await render(); } catch (e) { fail(e); }
    };
    $('#nc-name').focus();
  });
}

async function setCategory(id, value) {
  if (value === 'split') return txDetail(id, true);
  if (value === 'new') return newCategory(cid => setCategory(id, String(cid)));
  try {
    const r = await patch(`/transactions/${id}`, { category_id: value ? +value : null });
    await refresh();
    if (!value) return;
    const t = r.tx, name = esc(t.merchant), cat = esc(t.category);
    let msg = `Got it. <b>${name}</b> goes to <b>${cat}</b> from now on.`;
    if (r.resorted) msg += ` Re-sorted ${r.resorted} other${r.resorted > 1 ? 's' : ''} waiting on you.`;
    toast(msg, r.others ? { label: `Also change ${r.others} earlier`, run: async () => { try { const x = await post('/merchants/recategorize', { mkey: t.mkey, category_id: t.category_id, always: true }); await refresh(); toast(`Changed ${x.changed} ${name} transactions to ${cat}.`); } catch (e) { fail(e); } } } : null);
  } catch (e) { fail(e); }
}

async function txDetail(id, startSplit = false) {
  let d0; try { d0 = await api(`/transactions/${id}`); } catch (e) { return fail(e); }
  const t = d0.tx, parentId = t.parent_id || t.id, total = t.parent_id ? null : t.amount_cents;
  const parts = d0.parts.length ? d0.parts.map(p => ({ category_id: p.category_id, amount_cents: p.amount_cents })) : (startSplit ? [{ category_id: t.category_id, amount_cents: t.amount_cents }, { category_id: null, amount_cents: 0 }] : []);
  const draw = () => dialog(esc(t.merchant), `
    <div class="rowx sp"><span class="num" style="font-size:22px;font-weight:800;color:${t.amount_cents > 0 ? 'var(--good)' : 'inherit'}">${fmt(t.amount_cents, 2)}</span><span class="muted">${esc(t.date)} · ${esc(t.account)}</span></div>
    <div class="desc muted">${esc(t.raw)}</div>
    ${t.reason ? `<div class="callout">${esc(t.reason)}</div>` : ''}
    ${d0.history.length ? `<p class="note">Your history for this merchant: ${d0.history.map(h => `${esc(h.name)} ×${h.n}`).join(' · ')}</p>` : ''}
    <label>Name shown<input class="in" id="d-merchant" value="${esc(t.merchant)}"></label>
    ${parts.length || t.parent_id ? '' : `<label>Category<select class="in" id="d-cat">${catOptions(t.category_id)}</select></label>`}
    <label>Note<input class="in" id="d-note" value="${esc(t.note)}"></label>
    <label>Tags (comma separated)<input class="in" id="d-tags" value="${esc(t.tags)}" list="taglist"></label><datalist id="taglist">${state.boot.tags.map(x => `<option value="${esc(x)}">`).join('')}</datalist>
    <label class="row"><input type="checkbox" id="d-hide" ${t.hidden ? 'checked' : ''}> Hide from budgets and reports</label>
    ${t.parent_id ? `<p class="note">This is one part of a split. Open any part to change the split.</p>` : ''}
    ${parts.length && total !== null ? `<div><div class="bar-s" style="margin:0 -14px 8px">Split ${fmt(total, 2)}</div><div class="grid" style="gap:6px" id="parts">
        ${parts.map((p, i) => `<div class="rowx"><select class="in" data-pc="${i}" style="flex:1 1 160px">${catOptions(p.category_id)}</select><input class="in num" data-pa="${i}" style="width:110px;text-align:right" value="${(Math.abs(p.amount_cents) / 100).toFixed(2)}" inputmode="decimal"><button class="btn sm" data-pr="${i}" aria-label="Remove part">✕</button></div>`).join('')}</div>
        <div class="rowx sp" style="margin-top:8px"><button class="btn sm" id="p-add">Add a part</button><span class="note num" id="p-left"></span></div></div>` : ''}
    <div class="rowx">
      ${total !== null && !parts.length ? '<button class="btn" id="d-split">Split it</button>' : ''}
      ${parts.length && d0.parts.length ? '<button class="btn" id="d-unsplit">Undo the split</button>' : ''}
      ${!t.parent_id && t.category_id && d0.same_merchant > 1 ? `<button class="btn" id="d-always">Always sort ${esc(t.merchant)} here (${d0.same_merchant})</button>` : ''}
      <button class="btn" id="d-rule">Make a rule…</button>
      <button class="btn" id="d-same">See all from this merchant</button>
      ${['manual', 'csv', 'demo'].includes(t.source) ? '<button class="btn warn" id="d-del">Delete</button>' : ''}
    </div>
    <div class="rowx sp"><button class="btn" id="d-cancel">Cancel</button><button class="btn pri" id="d-save">Save</button></div>`, d => {
    const sign = t.amount_cents < 0 ? -1 : 1;
    const read = () => { $$('[data-pc]').forEach(s => parts[+s.dataset.pc].category_id = +s.value || null); $$('[data-pa]').forEach(s => parts[+s.dataset.pa].amount_cents = sign * Math.abs(cents(s.value))); };
    const left = () => { if (!$('#p-left')) return 0; read(); const l = total - sum(parts.map(p => p.amount_cents)); $('#p-left').textContent = l === 0 ? 'Adds up ✓' : `${fmt(Math.abs(l), 2)} ${l * sign > 0 ? 'left to assign' : 'too much'}`; return l; };
    left();
    $$('[data-pa]').forEach(s => s.oninput = left);
    $$('[data-pr]').forEach(b => b.onclick = () => { read(); parts.splice(+b.dataset.pr, 1); draw(); });
    if ($('#p-add')) $('#p-add').onclick = () => { const l = left(); parts.push({ category_id: null, amount_cents: l }); draw(); };
    if ($('#d-split')) $('#d-split').onclick = () => { parts.push({ category_id: t.category_id, amount_cents: t.amount_cents }, { category_id: null, amount_cents: 0 }); draw(); };
    if ($('#d-unsplit')) $('#d-unsplit').onclick = async () => { try { await post(`/transactions/${parentId}/split`, { parts: [] }); d.close(); await refresh(); } catch (e) { fail(e); } };
    if ($('#d-always')) $('#d-always').onclick = async () => { try { const cid = +($('#d-cat')?.value || t.category_id); const x = await post('/merchants/recategorize', { mkey: t.mkey, category_id: cid, always: true }); d.close(); await refresh(); toast(`All ${x.changed} ${esc(t.merchant)} transactions now go to ${esc(catById(cid).name)}, and new ones will too.`); } catch (e) { fail(e); } };
    $('#d-rule').onclick = () => ruleDialog({ pattern: t.mkey.toLowerCase(), category_id: +($('#d-cat')?.value || t.category_id) || null });
    $('#d-same').onclick = () => { d.close(); state.f = { q: '', status: '', account: 0, category: 0, all: true }; state.f.q = t.merchant; state.tab = 'tx'; render(); };
    if ($('#d-del')) $('#d-del').onclick = async () => { if (!confirm('Delete this transaction?')) return; try { await del(`/transactions/${t.id}`); d.close(); await refresh(); } catch (e) { fail(e); } };
    $('#d-cancel').onclick = () => d.close();
    $('#d-save').onclick = async () => {
      try {
        const body = { merchant: $('#d-merchant').value, note: $('#d-note').value, tags: $('#d-tags').value.split(',').map(x => x.trim()).filter(Boolean).join(','), hidden: $('#d-hide').checked };
        if (parts.length && total !== null) {
          if (left() !== 0) return toast('The parts need to add up to the full amount.');
          if (parts.some(p => !p.category_id)) return toast('Pick a category for each part.');
          await post(`/transactions/${t.id}/split`, { parts });
        }
        await patch(`/transactions/${t.id}`, body);
        const c = $('#d-cat'); d.close();
        if (c && (+c.value || null) !== t.category_id) await setCategory(t.id, c.value); else await refresh();
      } catch (e) { fail(e); }
    };
  });
  draw();
}

function addTxDialog() {
  const accts = state.boot.accounts;
  if (!accts.length) return toast('Add an account first, in Settings.');
  dialog('Add a transaction', `<label>Date<input class="in" type="date" id="a-date" value="${state.boot.today}"></label>
    <label>Account<select class="in" id="a-acct">${accts.map(a => `<option value="${a.id}">${esc(a.name)}</option>`).join('')}</select></label>
    <label>Merchant or description<input class="in" id="a-name"></label>
    <div class="rowx"><label style="flex:1">Amount<input class="in" id="a-amt" inputmode="decimal" placeholder="0.00"></label>
      <label style="flex:1">Type<select class="in" id="a-type"><option value="-1">Expense</option><option value="1">Income</option></select></label></div>
    <label>Category (leave blank to let it guess)<select class="in" id="a-cat">${catOptions(null, { blank: 'Guess for me' })}</select></label>
    <div class="rowx sp"><button class="btn" id="a-cancel">Cancel</button><button class="btn pri" id="a-ok">Add</button></div>`, d => {
    $('#a-cancel').onclick = () => d.close();
    $('#a-ok').onclick = async () => {
      const amt = Math.abs(cents($('#a-amt').value)); if (!amt) return toast('Enter an amount.');
      try { await post('/transactions', { date: $('#a-date').value, account_id: +$('#a-acct').value, merchant: $('#a-name').value, amount_cents: amt * +$('#a-type').value, category_id: +$('#a-cat').value || null }); d.close(); state.month = $('#a-date').value.slice(0, 7); await refresh(); } catch (e) { fail(e); }
    };
  });
}

/* One at a time, for a phone: the open items across every month, oldest first. */
async function reviewCards() {
  let queue; try { queue = (await api('/transactions?status=open&limit=500')).items.reverse(); } catch (e) { return fail(e); }
  let i = 0, done = 0;
  const cats = state.boot.categories.filter(c => !c.archived);
  const usage = {}; // most-used categories first, as quick buttons
  const next = async () => {
    if (i >= queue.length) { $('#dlg').close(); await refresh(); return toast(done ? `All caught up. ${done} sorted.` : 'Nothing left to review.'); }
    const t = queue[i]; let fresh; try { fresh = (await api(`/transactions/${t.id}`)).tx; } catch (e) { i++; return next(); }
    if (fresh.status === 'auto' || fresh.status === 'done') { i++; if (fresh.status === 'auto') done++; return next(); }   // an earlier answer already settled it
    const quick = [...new Set([fresh.category_id, ...Object.keys(usage).sort((a, b) => usage[b] - usage[a]).map(Number), ...cats.filter(c => ['ess', 'non'].includes(c.bucket)).map(c => c.id)].filter(Boolean))].slice(0, 9);
    dialog(`${i + 1} of ${queue.length}`, `<div class="card1"><div class="muted">${esc(fresh.date)} · ${esc(fresh.account)}</div>
        <div class="big">${esc(fresh.merchant)}</div><div class="desc muted">${esc(fresh.raw)}</div>
        <div class="big num" style="color:${fresh.amount_cents > 0 ? 'var(--good)' : 'inherit'}">${fmt(fresh.amount_cents, 2)}</div>
        ${fresh.reason ? `<div class="callout">${esc(fresh.reason)}</div>` : ''}</div>
      <div class="catgrid">${quick.map(id => `<button class="btn ${id === fresh.category_id ? 'on' : ''}" data-q="${id}">${id === fresh.category_id ? '✓ ' : ''}${esc(catById(id).name)}</button>`).join('')}</div>
      <label>Something else<select class="in" id="c-other">${catOptions(null, { blank: 'Pick another category…' })}</select></label>
      <div class="rowx sp"><button class="btn" id="c-stop">Stop for now</button><button class="btn" id="c-skip">Skip</button></div>`, d => {
      const pick = async cid => {
        try { if (cid === fresh.category_id && fresh.status === 'suggested') await post(`/transactions/${fresh.id}/confirm`); else await patch(`/transactions/${fresh.id}`, { category_id: cid }); usage[cid] = (usage[cid] || 0) + 1; done++; i++; next(); } catch (e) { fail(e); }
      };
      $$('[data-q]', d).forEach(b => b.onclick = () => pick(+b.dataset.q));
      $('#c-other').onchange = e => e.target.value && pick(+e.target.value);
      $('#c-skip').onclick = () => { i++; next(); };
      $('#c-stop').onclick = async () => { d.close(); await refresh(); };
    });
  };
  next();
}

function ruleDialog(seed = {}) {
  dialog('Make a rule', `<p class="note">Rules always win over what it has learned. Use one when the description says more than the merchant, like “costco gas”.</p>
    <label>When the description contains<input class="in" id="r-pat" value="${esc(seed.pattern || '')}"></label>
    <div class="rowx"><label style="flex:1">Amount at least (optional)<input class="in" id="r-min" inputmode="decimal"></label><label style="flex:1">Amount at most (optional)<input class="in" id="r-max" inputmode="decimal"></label></div>
    <label>Only in this account (optional)<select class="in" id="r-acct"><option value="">Any account</option>${state.boot.accounts.map(a => `<option value="${a.id}">${esc(a.name)}</option>`).join('')}</select></label>
    <label>Sort it as<select class="in" id="r-cat">${catOptions(seed.category_id)}</select></label>
    <label>Rename it to (optional)<input class="in" id="r-name"></label>
    <label class="row"><input type="checkbox" id="r-past" checked> Also apply to past transactions</label>
    <label class="row"><input type="checkbox" id="r-hide"> Hide these from budgets and reports</label>
    <div class="rowx sp"><button class="btn" id="r-cancel">Cancel</button><button class="btn pri" id="r-ok">Save rule</button></div>`, d => {
    $('#r-cancel').onclick = () => d.close();
    $('#r-ok').onclick = async () => {
      if (!+$('#r-cat').value) return toast('Pick a category.');
      try {
        const r = await post('/rules', { pattern: $('#r-pat').value, category_id: +$('#r-cat').value, min_cents: $('#r-min').value ? cents($('#r-min').value) : null, max_cents: $('#r-max').value ? cents($('#r-max').value) : null,
          account_id: +$('#r-acct').value || null, rename_to: $('#r-name').value, hide: $('#r-hide').checked, apply_to_past: $('#r-past').checked });
        d.close(); await refresh(); toast(`Rule saved. ${r.changed} transaction${r.changed === 1 ? '' : 's'} updated.`);
      } catch (e) { fail(e); }
    };
  });
}

window.addEventListener('DOMContentLoaded', async () => {
  try { const s = await api('/session'); if (!s.name) return showLogin(); await refresh(); } catch (e) { fail(e); }
});

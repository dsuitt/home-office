/* Home Money: the dashboard tabs, modeled on mockups/home-dashboard.html. */
'use strict';
const GROUPS = [['cash', 'Cash accounts'], ['credit', 'Credit cards'], ['loan', 'Loans'], ['investment', 'Investments'], ['property', 'Property (your estimates)']];
const ago = iso => { if (!iso) return 'never'; const d = (Date.now() - new Date(iso.length <= 10 ? iso + 'T12:00:00' : iso)) / 864e5; return d < 1 ? 'today' : d < 2 ? 'yesterday' : `${Math.floor(d)} days ago`; };

/* ===== 1. Financial Snapshot ===== */
VIEW.snap = async () => {
  const b = state.boot, bills = await api('/bills'), accts = b.accounts.filter(a => !a.hidden), now = +b.today.slice(5, 7) - 1;
  const tot = sum(accts.map(a => a.balance_cents));
  const soon = bills.recurring.filter(r => !r.income && r.next >= b.today && (new Date(r.next) - new Date(b.today)) / 864e5 <= 30);
  return `<div class="grid g2">
   ${panel(`Financial snapshot as of ${dshort(b.today)}, ${b.today.slice(0, 4)}`, accts.length ? `
     ${GROUPS.map(([g, name]) => { const rows = accts.filter(a => a.grp === g); return rows.length ? `<div class="bar-s" style="margin:0 -14px">${name}</div><div class="scroll"><table><thead><tr><th>Account</th><th>Purpose</th><th class="r">Balance</th><th>Updated</th></tr></thead><tbody>
       ${rows.map(a => `<tr><td><b>${esc(a.name)}</b>${a.mask ? ` <span class="muted">··${esc(a.mask)}</span>` : ''}</td><td class="muted">${esc(a.purpose || a.institution || '')}</td><td class="r num">${fmt(a.balance_cents)}</td>
         <td>${a.item_status && a.item_status !== 'ok' ? '<span class="pill p-ask">reconnect</span>' : `<span class="pill ${a.manual ? 'p-auto' : 'p-ok'}">${a.manual ? 'typed in' : 'synced'} ${ago(a.updated_at)}</span>`}</td></tr>`).join('')}
       <tr class="tot"><td colspan="2">Total</td><td class="r num">${fmt(sum(rows.map(a => a.balance_cents)))}</td><td></td></tr></tbody></table></div>` : ''; }).join('')}
     <div class="callout" style="margin-top:12px">Net across these accounts: <b class="num">${fmt(tot)}</b>. Linked balances update on their own whenever it syncs.</div>`
     : `<p class="empty">No accounts yet.<br><br><button class="btn pri" id="snap-connect">Connect your bank</button></p>`)}
   <div class="grid">
   ${panel('Bills at a glance', `
     <p class="note" style="margin-bottom:10px">Yearly and once-a-year costs, so nothing sneaks up on you. ${MF[now]} is highlighted because it’s now.</p>
     <div class="months">${MF.map((m, i) => `<div class="mon ${i === now ? 'now' : ''}"><div class="bar-s">${m}</div><ul>${[...bills.yearly.filter(x => x.month === i + 1).map(x => `<li><span>${esc(x.name)} <button class="btn sm" style="padding:0 5px" data-bdel="${x.id}" aria-label="Remove ${esc(x.name)}">✕</button></span><span class="num muted">${x.amount_cents ? fmt(x.amount_cents) : ''}</span></li>`),
       ...bills.recurring.filter(r => !r.income && ['yearly', 'quarterly'].includes(r.cadence) && +r.next.slice(5, 7) === i + 1).map(r => `<li><span>${esc(r.merchant)} <span class="pill p-auto">found</span></span><span class="num muted">${fmt(r.amount_cents)}</span></li>`)].join('')}</ul></div>`).join('')}</div>
     <form class="add" id="bill-add" style="margin-top:12px;grid-template-columns:minmax(0,2fr) minmax(0,1fr) minmax(0,1fr) auto"><label>Bill<input id="b-name" required placeholder="e.g. Car registration"></label>
       <label>Month<select id="b-month" class="in">${MF.map((m, i) => `<option value="${i + 1}">${m}</option>`).join('')}</select></label><label>Amount<input id="b-amt" inputmode="decimal" placeholder="0"></label><button class="btn on" style="padding:8px 14px">Add</button></form>`)}
   ${panel('Due in the next 30 days', soon.length ? `<table><tbody>${soon.map(r => `<tr><td>${dshort(r.next)}</td><td><b>${esc(r.merchant)}</b> <span class="muted">${esc(r.cadence)}</span></td><td class="r num">${fmt(r.amount_cents, 2)}</td></tr>`).join('')}
       <tr class="tot"><td colspan="2">Total</td><td class="r num">${fmt(sum(soon.map(r => r.amount_cents)), 2)}</td></tr></tbody></table>` : '<p class="note">Nothing recurring found yet. It needs a few months of history to spot the rhythm.</p>')}
   </div></div>`;
};
AFTER.snap = () => {
  if ($('#snap-connect')) $('#snap-connect').onclick = () => { state.tab = 'set'; render(); };
  $$('[data-bdel]').forEach(b => b.onclick = async () => { try { await del('/bills/' + b.dataset.bdel); render(); } catch (e) { fail(e); } });
  $('#bill-add').onsubmit = async e => { e.preventDefault(); try { await post('/bills', { name: $('#b-name').value, month: +$('#b-month').value, amount_cents: cents($('#b-amt').value) }); render(); } catch (err) { fail(err); } };
};

/* ===== 2. Categories & Goals (zero-based plan) ===== */
VIEW.plan = async B => {
  const i = mi(), goals = await api('/goals');
  const inc = incT(B, 'plan', i), bk = B.buckets.map(b => [b.name, bT(b, 'plan', i)]), left = inc - sum(bk.map(x => x[1]));
  const row = l => `<tr><td>${esc(l.name)}</td><td class="r"><input class="amt num" data-plan="${l.id}" value="${l.plan[i] / 100}" inputmode="decimal" aria-label="Monthly target for ${esc(l.name)}"></td><td class="r num muted">${l.act[i] == null ? '—' : fmt(l.act[i])}</td></tr>`;
  return `<div class="grid">
   <p class="note">Plan for <b>${monthName()}</b>. Changing a number here also sets it for the months after, until you change one of those.</p>
   <div class="grid g2">
    ${panel(`Income · ${MF[i]} plan`, `<div class="scroll"><table><thead><tr><th>Income type</th><th class="r">Monthly amount</th><th class="r">Actual</th></tr></thead><tbody>
      ${B.income.map(row).join('')}
      <tr class="tot"><td>Total</td><td class="r num">${fmt(inc)}</td><td class="r num">${B.last >= i ? fmt(incT(B, 'act', i)) : '—'}</td></tr></tbody></table></div>
      <button class="btn sm" data-addcat="income" style="margin-top:8px">Add an income type</button>`)}
    ${panel('Where every dollar goes', `${donutBlock(bk, fmt(inc))}
      <div class="zero" style="margin-top:12px;${left < 0 ? 'border-color:var(--bad);background:var(--bad-bg);color:var(--bad)' : left > 0 ? 'border-color:var(--warn);background:var(--warn-bg);color:var(--warn)' : ''}"><span>${left < 0 ? 'Over-assigned' : 'Left to assign'}: ${fmt(Math.abs(left))}</span>${left === 0 ? '<span style="font-weight:600">✓ Every dollar has a job</span>' : ''}</div>`)}
   </div>
   <div class="grid g3">
    ${B.buckets.map((b, bi) => panel(`${bi + 1}. ${esc(b.name)}`, `<div class="bar-s">${esc(b.hint)}</div><div class="scroll"><table><thead><tr><th>Category</th><th class="r">Monthly target</th><th class="r">Actual</th></tr></thead><tbody>
       ${b.lines.map(row).join('')}
       <tr class="tot"><td>Total</td><td class="r num">${fmt(bT(b, 'plan', i))}</td><td class="r num">${B.last >= i ? fmt(bT(b, 'act', i)) : '—'}</td></tr></tbody></table></div>
       <div class="pad" style="padding:8px"><button class="btn sm" data-addcat="${b.key}">Add a category</button></div>`, false)).join('')}
   </div>
   ${panel('Savings goals', `${goals.length ? `<div class="scroll"><table><thead><tr><th>Goal</th><th class="r">Target</th><th class="r">So far</th><th class="r">Remaining</th><th class="r">Per month</th><th>Ready</th><th style="width:22%">Progress</th><th></th></tr></thead><tbody>
     ${goals.map(g => { const left = Math.max(g.target_cents - g.so_far_cents, 0), n = left === 0 ? 0 : g.monthly_cents > 0 ? Math.ceil(left / g.monthly_cents) : Infinity;
       const d = new Date(+state.boot.today.slice(0, 4), +state.boot.today.slice(5, 7) - 1 + (n === Infinity ? 0 : n), 1);
       return `<tr><td><b>${esc(g.name)}</b>${g.category ? `<div class="note">counts money sorted as ${esc(g.category)}</div>` : ''}</td><td class="r num">${fmt(g.target_cents)}</td>
       <td class="r"><input class="amt num" data-gsaved="${g.id}" value="${g.so_far_cents / 100}" inputmode="decimal" aria-label="Saved so far"></td><td class="r num">${fmt(left)}</td>
       <td class="r"><input class="amt num" data-gmonthly="${g.id}" value="${g.monthly_cents / 100}" inputmode="decimal" aria-label="Per month"></td>
       <td class="ready ${n === 0 ? 'now' : n === Infinity ? 'never' : ''}">${n === 0 ? 'Reached ✓' : n === Infinity ? 'Not started' : M[d.getMonth()] + ' ' + d.getFullYear()}</td>
       <td><div class="prog"><i style="width:${Math.min(g.so_far_cents / (g.target_cents || 1), 1) * 100}%;background:var(--c5)"></i></div></td><td><button class="btn sm" data-gdel="${g.id}" aria-label="Remove ${esc(g.name)}">✕</button></td></tr>`; }).join('')}
   </tbody></table></div>` : '<p class="note">No goals yet.</p>'}
     <form class="add" id="goal-add" style="margin-top:12px;grid-template-columns:minmax(0,2fr) repeat(3,minmax(0,1fr)) minmax(0,1.4fr) auto"><label>Goal<input id="g-name" required placeholder="e.g. Italy trip"></label><label>Target<input id="g-target" inputmode="decimal" required></label>
       <label>Already saved<input id="g-saved" inputmode="decimal" value="0"></label><label>Per month<input id="g-monthly" inputmode="decimal" value="0"></label>
       <label>Count money sorted as<select id="g-cat" class="in">${`<option value="">Nothing (I’ll update it)</option>` + B.buckets[2].lines.map(l => `<option value="${l.id}">${esc(l.name)}</option>`).join('')}</select></label><button class="btn on" style="padding:8px 14px">Add</button></form>`)}
  </div>`;
};
AFTER.plan = () => {
  $$('[data-plan]').forEach(x => x.onchange = async () => { try { await put('/budget/' + state.month, { [x.dataset.plan]: cents(x.value) }); await refresh(); } catch (e) { fail(e); } });
  $$('[data-addcat]').forEach(b => b.onclick = () => newCategory());
  $$('[data-gsaved]').forEach(x => x.onchange = async () => { try { await patch('/goals/' + x.dataset.gsaved, { saved_cents: cents(x.value) }); render(); } catch (e) { fail(e); } });
  $$('[data-gmonthly]').forEach(x => x.onchange = async () => { try { await patch('/goals/' + x.dataset.gmonthly, { monthly_cents: cents(x.value) }); render(); } catch (e) { fail(e); } });
  $$('[data-gdel]').forEach(b => b.onclick = async () => { if (!confirm('Remove this goal?')) return; try { await del('/goals/' + b.dataset.gdel); render(); } catch (e) { fail(e); } });
  $('#goal-add').onsubmit = async e => { e.preventDefault(); try { await post('/goals', { name: $('#g-name').value, target_cents: cents($('#g-target').value), saved_cents: cents($('#g-saved').value), monthly_cents: cents($('#g-monthly').value), category_id: +$('#g-cat').value || null }); render(); } catch (err) { fail(err); } };
};

/* ===== 4. Annual Spending Plan ===== */
VIEW.annual = B => {
  const cell = (p, a, good) => { if (a == null) return `<td class="r num sep">${fmt(p)}</td><td class="r num muted">—</td><td class="r num muted">—</td>`; const d = a - p; const cls = Math.abs(d) < 100 ? '' : (good(d) ? 'pos' : 'neg'); return `<td class="r num sep">${fmt(p)}</td><td class="r num">${fmt(a)}</td><td class="r num ${cls}">${Math.abs(d) < 100 ? '$0' : (d > 0 ? '+' : '') + fmt(d)}</td>`; };
  const incGood = d => d >= 0, spendGood = d => d <= 0, goalGood = d => d >= 0;
  const row = (name, p, a, good, cls = '') => `<tr class="${cls}"><td>${esc(name)}</td>${M.map((_, i) => cell(p[i], a[i], good)).join('')}</tr>`;
  const sumL = (lines, w) => M.map((_, i) => w === 'act' && i > B.last ? null : sum(lines.map(l => l[w][i])));
  let body = row('Total income', sumL(B.income, 'plan'), sumL(B.income, 'act'), incGood, 'tot') + B.income.map(l => row(l.name, l.plan, l.act, incGood)).join('');
  B.buckets.forEach(b => { const g = b.key === 'goal' ? goalGood : spendGood; body += row(b.name, sumL(b.lines, 'plan'), sumL(b.lines, 'act'), g, 'tot') + b.lines.map(l => row(l.name, l.plan, l.act, g)).join(''); });
  return `<div class="grid">
   <div style="display:flex;flex-wrap:wrap;gap:10px;justify-content:space-between;align-items:center">
     <p class="note">Plan, actual and the difference for every month. Actuals fill in on their own as transactions are sorted. Scroll sideways for the full year.</p>
     <div class="diff-legend"><span class="pill pos">Better than plan</span><span class="pill neg">Worse than plan</span></div></div>
   ${panel(`Annual spending plan · ${ym()[0]}`, `<div class="scroll"><table class="ann"><thead><tr><th rowspan="2" style="background:var(--navy);color:var(--navy-ink)">Category</th>${M.map(m => `<th class="m sep" colspan="3">${m}</th>`).join('')}</tr><tr>${M.map(() => `<th class="r sep">Plan</th><th class="r">Actual</th><th class="r">+/−</th>`).join('')}</tr></thead><tbody>${body}</tbody></table></div>`, false)}
  </div>`;
};

/* ===== 5. Month in Review ===== */
VIEW.review = async B => {
  const i = mi();
  if (i > B.last) return `<div class="callout">${monthName()} hasn’t started yet. Pick an earlier month at the top.</div>`;
  const [refl, nw] = await Promise.all([api('/reflection/' + state.month), api('/networth?months=2')]);
  const inc = incT(B, 'act', i), [E, N, G] = B.buckets, e = bT(E, 'act', i), n = bT(N, 'act', i);
  const by = t => sum(G.lines.filter(l => l.kind === t).map(l => l.act[i]));
  const open = state.year.open[i] || { ask: 0, sug: 0 };
  const kp = [['Income', inc], ['Essentials', e], ['Non-essentials', n], ['Savings', by('save')], ['Investments', by('invest')], ['Debt paid', by('debt')], ['Spend rate', pct(e + n, inc)], ['Save + invest rate', pct(by('save') + by('invest'), inc)], ['Left over', inc - e - n - bT(G, 'act', i)]];
  const pie = [[E.name, e], [N.name, n], ['Savings', by('save')], ['Investments', by('invest')], ['Debt payoff', by('debt')]];
  const spend = b => { const tot = bT(b, 'act', i); return `<table><thead><tr><th>${esc(b.name)}</th><th class="r">Amount</th><th class="r">% of total</th></tr></thead><tbody>${b.lines.map(l => `<tr><td>${esc(l.name)}</td><td class="r num">${fmt(l.act[i])}</td><td class="r num">${pct(l.act[i], tot)}</td></tr>`).join('')}<tr class="tot"><td>Total</td><td class="r num">${fmt(tot)}</td><td class="r">100%</td></tr></tbody></table>`; };
  const all = [...E.lines, ...N.lines];
  const top3 = [...all].sort((a, b) => b.act[i] - a.act[i]).slice(0, 3).filter(l => l.act[i] > 0).map(l => l.name);
  const over = all.filter(l => l.act[i] > l.plan[i] * 1.1 && l.act[i] - l.plan[i] > 4000);
  // Adjust: categories over plan this month and the one before it get a one-tap fix
  const streak = l => { let s = 0; for (let j = i; j >= 0 && l.act[j] > l.plan[j] * 1.05 && l.plan[j] > 0; j--) s++; return s; };
  const adj = all.filter(l => streak(l) >= 2).map(l => { const avg = Math.round(sum(l.act.slice(Math.max(0, i - 2), i + 1)) / Math.min(3, i + 1) / 500) * 500; return { l, n: streak(l), avg }; });
  return `<div class="grid">
   <div class="bar-h" style="border-radius:6px">${monthName()} in review</div>
   ${open.ask + open.sug ? `<div class="callout" style="background:var(--warn-bg);color:var(--warn)">${open.ask + open.sug} transactions in ${MF[i]} still need a look, so these numbers aren’t final. <button class="btn sm" id="rv-go">Review them</button></div>` : ''}
   <div class="grid g2">
    ${panel('Monthly snapshot', `<div class="kpis">${kp.map(([l, v]) => `<div class="kpi"><div class="l">${l}</div><div class="v num">${typeof v === 'number' ? fmt(v) : v}</div></div>`).join('')}</div>
      <div style="margin-top:14px">${donutBlock(pie, fmt(inc))}</div>`)}
    ${panel('Planned vs. actual', `<div class="legend" style="margin-bottom:6px"><span><i style="background:var(--c3)"></i>Planned</span><span><i style="background:var(--c1)"></i>Actual</span></div>
      ${bars(B.buckets.map(b => b.name), [B.buckets.map(b => bT(b, 'plan', i)), B.buckets.map(b => bT(b, 'act', i))], ['var(--c3)', 'var(--c1)'], 200)}
      <div class="bar-s" style="margin:12px -14px 10px">${esc(G.name)} contributions</div>${hbar(G.lines.map(l => [l.name, l.act[i]]), 'var(--c5)')}`)}
   </div>
   <div class="grid g2">
    ${panel('Spending summary', `<div class="grid g2" style="gap:10px"><div class="scroll">${spend(E)}</div><div class="scroll">${spend(N)}</div></div>`)}
    ${panel('Monthly reflection', `<div class="grid" style="gap:14px">
      <div><div class="bar-s" style="margin:0 -14px 10px;text-align:left">Step 1 · Update</div>
       <ul class="check">${['Income', 'Expenses', 'Savings transfers', 'Investments', 'Debt payments', 'Net worth (all accounts)'].map(x => `<li><span class="box">✓</span><span>${x}</span><span class="pill p-auto" style="margin-left:auto">done for you</span></li>`).join('')}
         <li><span class="box" style="${open.ask + open.sug ? 'background:transparent;border-color:var(--warn);color:var(--warn)' : ''}">${open.ask + open.sug ? '!' : '✓'}</span><span>Every transaction sorted</span><span class="pill ${open.ask + open.sug ? 'p-ask' : 'p-ok'}" style="margin-left:auto">${open.ask + open.sug ? (open.ask + open.sug) + ' left' : 'all done'}</span></li></ul>
       <p class="note" style="margin-top:8px">Net worth today: <b class="num">${fmt(nw.totals.at(-1).net)}</b></p></div>
      <div><div class="bar-s" style="margin:0 -14px 10px;text-align:left">Step 2 · Reflect</div>
       <div class="grid" style="gap:10px">
        <div class="q"><label>My top three spending categories this month</label><p>${top3.map(esc).join(' · ') || '—'}</p></div>
        ${over.length ? `<div class="q"><label>Over plan</label><p>${over.map(l => `${esc(l.name)} by ${fmt(l.act[i] - l.plan[i])}`).join(' · ')}</p></div>` : ''}
        <div class="q"><label for="rf1">Which purchases felt worth it, and why?</label><textarea id="rf1" data-r="w">${esc(refl.w || '')}</textarea></div>
        <div class="q"><label for="rf2">Anything I’d do differently? What triggered it?</label><textarea id="rf2" data-r="d">${esc(refl.d || '')}</textarea></div>
       </div></div>
      <div><div class="bar-s" style="margin:0 -14px 10px;text-align:left">Step 3 · Adjust</div>
       <div class="grid" style="gap:8px">${adj.length ? adj.map(({ l, n, avg }) => `<div class="adj"><span style="flex:1 1 220px">${esc(l.name)} has been over plan ${n} months in a row. Plan is ${fmt(l.plan[i])}; you’ve averaged ${fmt(avg)}.</span><div class="btns"><button class="btn" data-raise="${l.id}" data-v="${avg}">Raise to ${fmt(avg)}</button><button class="btn on">Keep ${fmt(l.plan[i])}</button></div></div>`).join('') : '<p class="note">Nothing has run over plan two months in a row. No changes needed.</p>'}</div></div>
    </div>`)}
   </div></div>`;
};
AFTER.review = () => {
  if ($('#rv-go')) $('#rv-go').onclick = () => { state.f = { q: '', status: 'open', account: 0, category: 0, all: false }; state.tab = 'tx'; render(); };
  let timer; const body = {};
  $$('[data-r]').forEach(t => { body[t.dataset.r] = t.value; t.oninput = () => { body[t.dataset.r] = t.value; clearTimeout(timer); timer = setTimeout(() => put('/reflection/' + state.month, body).catch(fail), 600); }; });
  $$('[data-raise]').forEach(b => b.onclick = async () => { let [y, m] = ym(); m++; if (m > 12) { m = 1; y++; } try { await put(`/budget/${y}-${String(m).padStart(2, '0')}`, { [b.dataset.raise]: +b.dataset.v }); toast(`Plan raised to ${fmt(+b.dataset.v)} starting next month.`); await refresh(); } catch (e) { fail(e); } });
};

/* ===== 6. Net Worth ===== */
VIEW.nw = async () => {
  const [d, hold] = await Promise.all([api('/networth?months=12'), api('/holdings')]);
  const idx = d.totals.map((t, i) => t.n ? i : -1).filter(i => i >= 0);
  if (!idx.length) return '<p class="empty">No accounts yet. Connect your bank in Settings.</p>';
  const lab = idx.map(i => M[+d.months[i].slice(5) - 1]), T = idx.map(i => d.totals[i]), last = T.at(-1), first = T[0];
  const mom = (cur, prev) => prev == null || cur == null || !prev ? '' : (() => { const v = Math.round((cur - prev) / Math.abs(prev) * 1000) / 10; return `<span class="${v >= 0 ? 'pos' : 'neg'}" style="padding:1px 5px;border-radius:3px;font-size:11px">${v >= 0 ? '+' : ''}${v}%</span>`; })();
  const trow = (name, vals, cls = '', showMom = true) => `<tr class="${cls}"><td>${esc(name)}</td>${vals.map((v, j) => `<td class="r num sep">${v == null ? '—' : fmt(v)}</td><td>${showMom && j ? mom(v, vals[j - 1]) : ''}</td>`).join('')}</tr>`;
  const grp = g => d.accounts.filter(a => a.grp === g);
  const assets = ['cash', 'investment', 'property'], debts = ['credit', 'loan'];
  const hv = sum(hold.map(h => h.value_cents));
  return `<div class="grid">
   <div class="grid g2">
    ${panel('Net worth', `<div class="kpis"><div class="kpi"><div class="l">Net worth</div><div class="v num">${fmt(last.net)}</div></div><div class="kpi"><div class="l">Since ${lab[0]}</div><div class="v num" style="color:var(--${last.net - first.net >= 0 ? 'good' : 'bad'})">${last.net - first.net >= 0 ? '+' : ''}${fmt(last.net - first.net)}</div></div><div class="kpi"><div class="l">Owed</div><div class="v num">${fmt(-last.debts)}</div></div></div>
      <div style="margin-top:14px">${donutBlock(GROUPS.filter(([g]) => assets.includes(g)).map(([g, n]) => [n.replace(' (your estimates)', ''), sum(grp(g).map(a => a.values[idx.at(-1)] || 0))]), fmt(last.assets))}</div>`)}
    ${panel('Net worth progress', line(lab, [T.map(t => t.net)], ['var(--c2)'], { h: 230, min: Math.min(...T.map(t => t.net)) * .97 }))}
   </div>
   ${panel('Monthly net worth tracker', `<div class="scroll"><table class="ann"><thead><tr><th style="background:var(--navy);color:var(--navy-ink)">Account</th>${lab.map(x => `<th class="m sep" colspan="2">${x}</th>`).join('')}</tr></thead><tbody>
     ${trow('Total assets', T.map(t => t.assets), 'tot')}
     ${assets.flatMap(g => grp(g)).map(a => trow(a.name, idx.map(i => a.values[i]))).join('')}
     ${trow('Total debts', T.map(t => t.debts), 'tot', false)}
     ${debts.flatMap(g => grp(g)).map(a => trow(a.name, idx.map(i => a.values[i]), '', false)).join('')}
     ${trow('Net worth', T.map(t => t.net), 'tot')}
   </tbody></table></div><p class="note" style="padding:10px 14px">Balances are from the last day of each month. Checking and card history before you linked is rebuilt from transactions. Home and car values are estimates you edit in Settings.</p>`, false)}
   ${hold.length ? panel('Investments', `<div class="scroll"><table><thead><tr><th>Holding</th><th>Account</th><th class="r">Shares</th><th class="r">Value</th><th class="r">Gain</th><th class="r">% of total</th></tr></thead><tbody>
     ${hold.map(h => { const g = h.cost_cents == null ? null : h.value_cents - h.cost_cents; return `<tr><td><b>${esc(h.ticker || h.name)}</b> <span class="muted">${h.ticker ? esc(h.name) : ''}</span></td><td class="muted">${esc(h.account)}</td><td class="r num">${(+h.quantity).toLocaleString('en-US', { maximumFractionDigits: 2 })}</td><td class="r num">${fmt(h.value_cents)}</td>
       <td class="r num ${g == null ? '' : g >= 0 ? 'pos' : 'neg'}">${g == null ? '—' : (g >= 0 ? '+' : '') + fmt(g) + ' (' + pct(g, h.cost_cents) + ')'}</td><td class="r num">${pct(h.value_cents, hv)}</td></tr>`; }).join('')}
     <tr class="tot"><td colspan="3">Total</td><td class="r num">${fmt(hv)}</td><td></td><td></td></tr></tbody></table></div>`, false) : ''}
  </div>`;
};

/* ===== 7. Dashboard Summary ===== */
VIEW.dash = async B => {
  const n = B.last + 1, [E, N, G] = B.buckets, mos = M.slice(0, n), merch = await api('/merchants');
  if (n <= 0) return `<div class="callout">Nothing for ${ym()[0]} yet.</div>`;
  const ytd = arr => sum(arr.slice(0, n));
  const inc = mos.map((_, i) => incT(B, 'act', i)), spent = mos.map((_, i) => bT(E, 'act', i) + bT(N, 'act', i)), goal = mos.map((_, i) => bT(G, 'act', i));
  const ty = t => sum(G.lines.filter(l => l.kind === t).map(l => ytd(l.act)));
  const top = b => [...b.lines].sort((x, y) => ytd(y.act) - ytd(x.act)).slice(0, 3);
  const yr = [['Total income', sum(inc)], ['Total spent', sum(spent)], ['  ' + E.name, sum(E.lines.map(l => ytd(l.act)))], ['  ' + N.name, sum(N.lines.map(l => ytd(l.act)))], ['Total saved', ty('save')], ['Total invested', ty('invest')], ['Total debt paid', ty('debt')], ['Savings rate', pct(ty('save') + ty('invest'), sum(inc))]];
  return `<div class="grid">
   <div class="grid g2">
    ${panel(`${ym()[0]} in review (to date)`, `<table><tbody>${yr.map(([nm, v]) => `<tr class="${nm.startsWith('  ') ? '' : 'sub'}"><td style="${nm.startsWith('  ') ? 'padding-left:22px;font-style:italic' : ''}">${esc(nm.trim())}</td><td class="r num">${typeof v === 'number' ? fmt(v) : v}</td></tr>`).join('')}</tbody></table>`)}
    ${panel('Income vs. total expenses', `<div class="legend" style="margin-bottom:6px"><span><i style="background:var(--c1)"></i>Income</span><span><i style="background:var(--c3)"></i>Spending</span></div>${bars(mos, [inc, spent], ['var(--c1)', 'var(--c3)'])}`)}
   </div>
   <div class="grid g3">
    ${panel('Income by type', donutBlock(B.income.map(l => [l.name, ytd(l.act)]).filter(r => r[1] > 0), fmt(sum(inc))))}
    ${panel(`Top 3 · ${esc(E.name)}`, `<table><tbody>${top(E).map((l, i) => `<tr><td>${i + 1}. ${esc(l.name)}</td><td class="r num">${fmt(ytd(l.act))}</td></tr>`).join('')}</tbody></table>
      <div class="bar-s" style="margin:12px -14px 0">Top 3 · ${esc(N.name)}</div><table><tbody>${top(N).map((l, i) => `<tr><td>${i + 1}. ${esc(l.name)}</td><td class="r num">${fmt(ytd(l.act))}</td></tr>`).join('')}</tbody></table>`)}
    ${panel(`${esc(G.name)} so far`, hbar(G.lines.map(l => [l.name, ytd(l.act)]), 'var(--c5)'))}
   </div>
   ${panel('Planned vs. actual spending by month', `<div class="legend" style="margin-bottom:6px"><span><i style="background:var(--c3)"></i>Planned</span><span><i style="background:var(--c1)"></i>Actual</span></div>
     ${bars(mos, [mos.map((_, i) => bT(E, 'plan', i) + bT(N, 'plan', i)), spent], ['var(--c3)', 'var(--c1)'])}`)}
   <div class="grid g2">
    ${panel('Cash flow', `<div class="scroll"><table><thead><tr><th>Month</th><th class="r">In</th><th class="r">Spent</th><th class="r">To goals</th><th class="r">Left over</th></tr></thead><tbody>
      ${mos.map((m, i) => { const l = inc[i] - spent[i] - goal[i]; return `<tr><td>${m}</td><td class="r num">${fmt(inc[i])}</td><td class="r num">${fmt(spent[i])}</td><td class="r num">${fmt(goal[i])}</td><td class="r num ${l >= 0 ? 'pos' : 'neg'}">${fmt(l)}</td></tr>`; }).join('')}
      <tr class="tot"><td>Total</td><td class="r num">${fmt(sum(inc))}</td><td class="r num">${fmt(sum(spent))}</td><td class="r num">${fmt(sum(goal))}</td><td class="r num">${fmt(sum(inc) - sum(spent) - sum(goal))}</td></tr></tbody></table></div>`, false)}
    ${panel('Where it goes · top merchants, all time', merch.length ? hbar(merch.slice(0, 12).map(m => [m.merchant, m.spent]), 'var(--c2)') : '<p class="note">Nothing yet.</p>')}
   </div>
  </div>`;
};

/* ===== 8. Recurring ===== */
VIEW.rec = async () => {
  const { recurring: rec } = await api('/bills'), out = rec.filter(r => !r.income), inn = rec.filter(r => r.income);
  const subs = out.filter(r => r.category === 'Subscriptions');
  const table = rows => `<div class="scroll"><table><thead><tr><th>Next</th><th>Merchant</th><th>How often</th><th>Category</th><th class="r">Amount</th><th class="r">Per year</th><th>Last</th></tr></thead><tbody>
    ${rows.map(r => `<tr><td class="num" style="white-space:nowrap">${r.next < state.boot.today ? '<span class="pill p-ask">due</span> ' : ''}${dshort(r.next)}</td><td><b>${esc(r.merchant)}</b>${r.changed ? ` <span class="pill p-ask">was ${fmt(r.amount_cents, 2)}, now ${fmt(r.last_amount_cents, 2)}</span>` : ''}</td><td class="muted">${esc(r.cadence)}</td><td class="muted">${esc(r.category || 'Not sorted')}</td>
      <td class="r num">${fmt(r.amount_cents, 2)}</td><td class="r num">${fmt(r.per_year_cents)}</td><td class="muted">${dshort(r.last)}</td></tr>`).join('')}
    <tr class="tot"><td colspan="5">Total per year</td><td class="r num">${fmt(sum(rows.map(r => r.per_year_cents)))}</td><td></td></tr></tbody></table></div>`;
  return `<div class="grid">
   <div class="grid g3">
    ${panel('Bills & subscriptions found', `<div style="font-size:30px;font-weight:800" class="num">${out.length}</div><p class="note">Spotted by their rhythm: same merchant, steady gap, similar amount.</p>`)}
    ${panel('They cost, per month', `<div style="font-size:30px;font-weight:800" class="num">${fmt(Math.round(sum(out.map(r => r.per_year_cents)) / 12))}</div><p class="note">${fmt(sum(out.map(r => r.per_year_cents)))} a year.</p>`)}
    ${panel('Subscriptions alone', `<div style="font-size:30px;font-weight:800" class="num">${fmt(sum(subs.map(r => r.per_year_cents)))}<span style="font-size:14px"> / yr</span></div><p class="note">${subs.length} sorted as Subscriptions. Worth a glance for anything you’ve stopped using.</p>`)}
   </div>
   ${panel('Recurring bills & subscriptions', out.length ? table(out) : '<p class="empty">Nothing found yet. It needs about three months of history.</p>', false)}
   ${inn.length ? panel('Recurring income', table(inn), false) : ''}
  </div>`;
};

/* ===== 9. What I've Learned ===== */
VIEW.learn = async () => {
  const [L, rules] = await Promise.all([api('/learned'), api('/rules')]);
  const sure = L.filter(l => l.auto).length;
  return `<div class="grid">
   <div class="callout"><b>How sorting works.</b> Every time you pick or confirm a category, it remembers that merchant. The next time the merchant shows up it sorts it the way you do, and tells you why. Recent answers count more than old ones, so one correction is enough to start changing it. If a merchant really is mixed (Amazon, Venmo), it looks at the amount, and otherwise asks instead of guessing. Your rules always win.</div>
   <div class="grid g3">
    ${panel('Merchants it knows', `<div style="font-size:30px;font-weight:800" class="num">${L.length}</div>`)}
    ${panel('Sorted without asking', `<div style="font-size:30px;font-weight:800" class="num">${sure}</div><p class="note">The other ${L.length - sure} are mixed, so it suggests and you confirm.</p>`)}
    ${panel('Your rules', `<div style="font-size:30px;font-weight:800" class="num">${rules.length}</div><button class="btn sm" id="rule-new" style="margin-top:6px">Make a rule</button>`)}
   </div>
   ${panel('Rules', rules.length ? `<div class="scroll"><table><thead><tr><th>When the description contains</th><th>Sort it as</th><th>Limits</th><th class="r">Used</th><th></th></tr></thead><tbody>
     ${rules.map(r => `<tr><td class="desc">${esc(r.pattern)}</td><td><b>${esc(r.category || '')}</b>${r.rename_to ? ` · rename to ${esc(r.rename_to)}` : ''}${r.hide ? ' · hidden' : ''}</td><td class="muted">${[r.min_cents != null ? '≥ ' + fmt(r.min_cents) : '', r.max_cents != null ? '≤ ' + fmt(r.max_cents) : '', r.account_id ? esc(state.boot.accounts.find(a => a.id === r.account_id)?.name || '') : ''].filter(Boolean).join(' · ') || '—'}</td><td class="r num">${r.hits}</td><td><button class="btn sm warn" data-rdel="${r.id}">Delete</button></td></tr>`).join('')}</tbody></table></div>` : '<p class="empty">No rules yet. You rarely need one: it learns from your answers on its own.</p>', false)}
   <div class="filters"><input type="search" id="l-q" placeholder="Find a merchant" aria-label="Find a merchant"></div>
   ${panel('What it has learned from you', L.length ? `<div class="scroll"><table><thead><tr><th>Merchant</th><th>Goes to</th><th>How sure</th><th>What you’ve chosen</th><th class="r">Times</th><th></th></tr></thead><tbody id="l-body">
     ${L.map(l => `<tr data-name="${esc(l.merchant.toLowerCase())}"><td><b>${esc(l.merchant)}</b></td><td><select class="cat" data-lcat="${esc(l.mkey)}" aria-label="Category for ${esc(l.merchant)}">${catOptions(l.category_id)}</select></td>
       <td><span class="pill ${l.auto ? 'p-ok' : 'p-sug'}">${l.auto ? 'Sorts on its own' : 'Asks you'}</span></td><td class="note">${l.breakdown.map(b => `${esc(b.name)} ×${b.n}`).join(' · ')}</td><td class="r num">${l.times}</td>
       <td><button class="btn sm" data-forget="${esc(l.mkey)}">Forget</button></td></tr>`).join('')}</tbody></table></div>` : '<p class="empty">Nothing yet. Sort a few transactions, or import your Monarch history in Settings to teach it everything at once.</p>', false)}
  </div>`;
};
AFTER.learn = () => {
  $('#rule-new').onclick = () => ruleDialog();
  $$('[data-rdel]').forEach(b => b.onclick = async () => { try { await del('/rules/' + b.dataset.rdel); await refresh(); } catch (e) { fail(e); } });
  $$('[data-forget]').forEach(b => b.onclick = async () => { try { await del('/learned/' + encodeURIComponent(b.dataset.forget)); await refresh(); toast('Forgotten. It will ask next time.'); } catch (e) { fail(e); } });
  $$('[data-lcat]').forEach(s => s.onchange = async () => { if (!+s.value) return; try { const x = await post('/merchants/recategorize', { mkey: s.dataset.lcat, category_id: +s.value, always: true }); await refresh(); toast(`Changed ${x.changed} transactions, and new ones will follow.`); } catch (e) { fail(e); } });
  $('#l-q').oninput = e => { const q = e.target.value.toLowerCase(); $$('#l-body tr').forEach(tr => tr.style.display = tr.dataset.name.includes(q) ? '' : 'none'); };
};

/* ===== 10. Settings ===== */
VIEW.set = () => {
  const b = state.boot;
  return `<div class="grid">
   <div class="grid g2">
   ${panel('Bank connections', `
     ${b.items.length ? `<table><tbody>${b.items.map(i => `<tr><td><b>${esc(i.institution || 'Bank')}</b><div class="note">${i.status === 'ok' ? 'Synced ' + ago(i.last_sync) : esc(i.error || i.status)}</div></td>
        <td><span class="pill ${i.status === 'ok' ? 'p-ok' : 'p-ask'}">${i.status === 'ok' ? 'Connected' : i.status === 'needs_reauth' ? 'Needs you to sign in again' : 'Problem'}</span></td>
        <td class="r"><button class="btn sm" data-relink="${i.id}">${i.status === 'ok' ? 'Update login' : 'Reconnect'}</button> <button class="btn sm warn" data-unlink="${i.id}">Remove</button></td></tr>`).join('')}</tbody></table>` : '<p class="note">No banks linked yet.</p>'}
     <div class="rowx" style="margin-top:12px"><button class="btn pri" id="link" ${b.plaid.configured ? '' : 'disabled'}>Connect a bank</button>${b.items.length ? '<button class="btn" id="set-sync">Sync now</button>' : ''}</div>
     <p class="note" style="margin-top:8px">${b.plaid.configured ? `Uses Plaid (${esc(b.plaid.env)}). You sign in on your bank’s own screen; this app never sees your bank password.` : 'Plaid keys aren’t set on the server yet. See app/README.md for the two values to add.'}</p>`)}
   ${panel('Bring in history · import a CSV', `<p class="note">Export your transactions from Monarch (Transactions → Download CSV) and drop the file here. Every row comes in with the category you gave it, and <b>that history is what teaches the sorter your habits</b>. Bank CSVs work too. Importing the same file twice is harmless.</p>
     <div class="rowx" style="margin-top:10px"><input type="file" id="csv" accept=".csv,text/csv"><button class="btn pri" id="csv-go">Import</button></div>
     <p class="note" style="margin-top:8px">If the file has no Account column: <input class="in" id="csv-acct" value="Imported" style="width:160px" aria-label="Account name for the import"></p>
     <div class="rowx" style="margin-top:12px"><a class="btn" href="/api/export.csv" download>Download everything as CSV</a></div>`)}
   </div>
   ${panel('Accounts', `<div class="scroll"><table><thead><tr><th>Name</th><th>Type</th><th>Purpose</th><th class="r">Balance</th><th>Shown</th><th></th></tr></thead><tbody>
     ${b.accounts.map(a => `<tr><td><input class="txt" data-aname="${a.id}" value="${esc(a.name)}" aria-label="Account name"></td>
       <td><select class="cat" data-agrp="${a.id}" aria-label="Type">${GROUPS.map(([g, n]) => `<option value="${g}" ${a.grp === g ? 'selected' : ''}>${n.replace(' (your estimates)', '')}</option>`).join('')}</select></td>
       <td><input class="txt" data-apurpose="${a.id}" value="${esc(a.purpose)}" placeholder="What it’s for" aria-label="Purpose"></td>
       <td class="r">${a.manual ? `<input class="amt num" data-abal="${a.id}" value="${a.balance_cents / 100}" inputmode="decimal" aria-label="Balance">` : `<span class="num">${fmt(a.balance_cents)}</span>`}</td>
       <td><input type="checkbox" data-ashow="${a.id}" ${a.hidden ? '' : 'checked'} aria-label="Show ${esc(a.name)}"></td>
       <td>${a.manual ? `<button class="btn sm warn" data-adel="${a.id}">Delete</button>` : `<span class="note">${esc(a.institution || '')}</span>`}</td></tr>`).join('')}</tbody></table></div>
     <form class="add pad" id="acct-add" style="grid-template-columns:minmax(0,2fr) minmax(0,1.2fr) minmax(0,1fr) auto"><label>Add an account you update by hand<input id="na-name" required placeholder="e.g. Home (estimated)"></label>
       <label>Type<select id="na-grp" class="in">${GROUPS.map(([g, n]) => `<option value="${g}" ${g === 'property' ? 'selected' : ''}>${n.replace(' (your estimates)', '')}</option>`).join('')}</select></label>
       <label>Balance (debts negative)<input id="na-bal" inputmode="decimal" value="0"></label><button class="btn on" style="padding:8px 14px">Add</button></form>`, false)}
   ${panel('Categories', `<div class="scroll"><table><thead><tr><th>Name</th><th>Bucket</th><th>Counts as</th><th>In use</th><th>Merge into…</th></tr></thead><tbody>
     ${b.categories.map(c => `<tr><td><input class="txt" data-cname="${c.id}" value="${esc(c.name)}" aria-label="Category name"></td>
       <td><select class="cat" data-cbucket="${c.id}" aria-label="Bucket">${b.buckets.map(k => `<option value="${k.key}" ${c.bucket === k.key ? 'selected' : ''}>${esc(k.name)}</option>`).join('')}</select></td>
       <td><select class="cat" data-ckind="${c.id}" aria-label="Kind">${[['income', 'Income'], ['expense', 'Spending'], ['save', 'Saving'], ['invest', 'Investing'], ['debt', 'Debt payoff'], ['transfer', 'Transfer']].map(([v, n]) => `<option value="${v}" ${c.kind === v ? 'selected' : ''}>${n}</option>`).join('')}</select></td>
       <td><input type="checkbox" data-carch="${c.id}" ${c.archived ? '' : 'checked'} aria-label="In use"></td>
       <td><select class="cat" data-cmerge="${c.id}" aria-label="Merge into">${catOptions(null, { blank: '—' })}</select></td></tr>`).join('')}</tbody></table></div>
     <div class="pad"><button class="btn" id="cat-new">Add a category</button></div>`, false)}
   <div class="rowx">${b.demo ? '<button class="btn warn" id="demo-clear">Clear the sample data</button>' : ''}${b.auth ? `<button class="btn" id="logout">Sign out (${esc(b.user)})</button>` : '<span class="note">Sign-in is off (APP_USERS isn’t set). Fine on your own computer; set it before putting this on the internet.</span>'}</div>
  </div>`;
};
function loadPlaid() { return new Promise((ok, no) => { if (window.Plaid) return ok(); const s = document.createElement('script'); s.src = 'https://cdn.plaid.com/link/v2/stable/link-initialize.js'; s.onload = ok; s.onerror = () => no(new Error('Could not load Plaid')); document.head.appendChild(s); }); }
async function plaidLink(itemId) {
  try {
    const [{ link_token }] = await Promise.all([post('/plaid/link-token', itemId ? { item_id: itemId } : {}), loadPlaid()]);
    window.Plaid.create({
      token: link_token,
      onSuccess: async (public_token, meta) => {
        try {
          if (itemId) { await post('/sync', { item_id: itemId }); }
          else { toast('Linked. Pulling in your history…'); const r = await post('/plaid/exchange', { public_token, institution: meta?.institution?.name || '' }); if (!r.ok) throw new Error(r.error); toast(`Connected. ${r.added} transactions came in.`); }
          await refresh();
        } catch (e) { fail(e); }
      },
      onExit: err => { if (err) toast(esc(err.display_message || err.error_message || 'The bank connection was cancelled.')); },
    }).open();
  } catch (e) { fail(e); }
}
AFTER.set = () => {
  const on = (sel, ev, fn) => $$(sel).forEach(x => x[ev] = () => fn(x).then(refresh).catch(fail));
  if ($('#link')) $('#link').onclick = () => plaidLink();
  if ($('#set-sync')) $('#set-sync').onclick = syncNow;
  $$('[data-relink]').forEach(b => b.onclick = () => plaidLink(+b.dataset.relink));
  $$('[data-unlink]').forEach(b => b.onclick = async () => { if (!confirm('Remove this bank? Its accounts and transactions will be removed from this app.')) return; try { await del('/items/' + b.dataset.unlink); await refresh(); } catch (e) { fail(e); } });
  $('#csv-go').onclick = async () => {
    const f = $('#csv').files[0]; if (!f) return toast('Choose a CSV file first.');
    const fd = new FormData(); fd.append('file', f);
    try { const r = await api('/import/csv?account=' + encodeURIComponent($('#csv-acct').value || 'Imported'), { method: 'POST', body: fd }); await refresh();
      toast(`Imported ${r.added} (${r.skipped} skipped). Learned from ${r.taught} categorized rows.${r.new_categories.length ? ' New categories: ' + esc(r.new_categories.join(', ')) + '. Put them in the right bucket below.' : ''}`); } catch (e) { fail(e); }
  };
  on('[data-aname]', 'onchange', x => patch('/accounts/' + x.dataset.aname, { name: x.value }));
  on('[data-apurpose]', 'onchange', x => patch('/accounts/' + x.dataset.apurpose, { purpose: x.value }));
  on('[data-agrp]', 'onchange', x => patch('/accounts/' + x.dataset.agrp, { grp: x.value }));
  on('[data-abal]', 'onchange', x => patch('/accounts/' + x.dataset.abal, { balance_cents: cents(x.value) }));
  on('[data-ashow]', 'onchange', x => patch('/accounts/' + x.dataset.ashow, { hidden: !x.checked }));
  $$('[data-adel]').forEach(b => b.onclick = async () => { if (!confirm('Delete this account and its transactions?')) return; try { await del('/accounts/' + b.dataset.adel); await refresh(); } catch (e) { fail(e); } });
  $('#acct-add').onsubmit = async e => { e.preventDefault(); try { await post('/accounts', { name: $('#na-name').value, grp: $('#na-grp').value, balance_cents: cents($('#na-bal').value) }); await refresh(); } catch (err) { fail(err); } };
  on('[data-cname]', 'onchange', x => patch('/categories/' + x.dataset.cname, { name: x.value }));
  on('[data-cbucket]', 'onchange', x => patch('/categories/' + x.dataset.cbucket, { bucket: x.value }));
  on('[data-ckind]', 'onchange', x => patch('/categories/' + x.dataset.ckind, { kind: x.value }));
  on('[data-carch]', 'onchange', x => patch('/categories/' + x.dataset.carch, { archived: x.checked ? 0 : 1 }));
  $$('[data-cmerge]').forEach(s => s.onchange = async () => { const into = +s.value, from = +s.dataset.cmerge; if (!into || into === from) return; if (!confirm(`Move everything in “${catById(from).name}” into “${catById(into).name}” and remove “${catById(from).name}”?`)) { s.value = ''; return; } try { await patch('/categories/' + from, { merge_into: into }); await refresh(); } catch (e) { fail(e); } });
  $('#cat-new').onclick = () => newCategory();
  if ($('#demo-clear')) $('#demo-clear').onclick = async () => { if (!confirm('Remove all the sample accounts and transactions?')) return; try { await post('/demo/clear'); await refresh(); } catch (e) { fail(e); } };
  if ($('#logout')) $('#logout').onclick = async () => { await post('/logout'); showLogin(); };
};

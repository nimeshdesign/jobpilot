/* JobPilot frontend — plain JS, no build step. */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const LIST_FIELDS = [
  'target_titles', 'must_have_keywords', 'exclude_keywords',
  'preferred_countries', 'authorized_countries',
];

// Same list-of-strings shape, but shown one per line in a textarea.
const LINE_FIELDS = ['greenhouse_boards', 'lever_boards'];

const state = {
  profile: null,
  sources: [],
  jobs: [],
  jobOffset: 0,
  jobTotal: 0,
  pollTimer: null,
  wasOffline: false,
};

/* ------------------------------------------------------------------ utils */
class OfflineError extends Error {}

function setOffline(down) {
  const banner = $('#offline-banner');
  if (!banner) return;
  banner.hidden = !down;
  document.body.classList.toggle('is-offline', down);
}

async function api(path, options = {}) {
  const opts = { headers: {}, ...options };
  if (opts.body && !(opts.body instanceof FormData)) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(opts.body);
  }

  let res;
  try {
    res = await fetch(`/api${path}`, opts);
  } catch {
    // fetch only rejects on a transport failure — the server stopped, was
    // restarted, or the machine slept. That is nothing to do with the request,
    // and reporting it as "Failed to fetch" tells the user nothing.
    setOffline(true);
    throw new OfflineError(
      'Cannot reach JobPilot. The server has stopped or is restarting — '
      + 'check the window running it, then retry.',
    );
  }

  setOffline(false);
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch { /* non-JSON */ }
    throw new Error(detail);
  }
  return res.status === 204 ? null : res.json();
}

let toastTimer;
function toast(message, kind = '') {
  const el = $('#toast');
  el.textContent = message;
  el.className = `toast ${kind}`;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 4200);
}

const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
));

function scoreClass(score) {
  if (score == null) return 's-low';
  if (score >= 75) return 's-high';
  if (score >= 55) return 's-mid';
  return 's-low';
}

function visaPill(status) {
  if (status === 'yes') return '<span class="pill good">visa sponsorship</span>';
  if (status === 'likely') return '<span class="pill warn" title="This employer has a public '
    + 'record of sponsoring visas, but this particular posting does not mention it">'
    + 'likely sponsors</span>';
  if (status === 'global') return '<span class="pill accent" title="Hires worldwide / via an '
    + 'employer of record — no visa needed for a remote role, but not sponsorship either">'
    + 'hires globally</span>';
  if (status === 'no') return '<span class="pill bad">no sponsorship</span>';
  return '<span class="pill">visa not stated</span>';
}

function openModal(html) {
  $('#modal-body').innerHTML = html;
  $('#modal').hidden = false;
}
$('#modal-close').onclick = () => { $('#modal').hidden = true; };
$('#modal').onclick = (e) => { if (e.target.id === 'modal') $('#modal').hidden = true; };
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') $('#modal').hidden = true;
});

/* ------------------------------------------------------------------- tabs */
$$('#tabs button').forEach((btn) => {
  btn.onclick = () => {
    $$('#tabs button').forEach((b) => b.classList.toggle('active', b === btn));
    $$('.tab').forEach((t) => t.classList.toggle('active', t.id === `tab-${btn.dataset.tab}`));
    if (btn.dataset.tab === 'jobs') loadJobs(true);
    if (btn.dataset.tab === 'applications') { loadTracker().then(loadApplications); }
    if (btn.dataset.tab === 'resume') loadResumes();
  };
});

/* --------------------------------------------------------------- dashboard */
async function loadStatus() {
  let status;
  try {
    status = await api('/status');
  } catch {
    state.wasOffline = true;
    return;
  }

  // Came back after a restart — repopulate whatever the user is looking at,
  // so they are not left staring at a stale or empty page.
  if (state.wasOffline) {
    state.wasOffline = false;
    toast('Reconnected to JobPilot', 'ok');
    const active = $('#tabs button.active')?.dataset.tab;
    if (active === 'resume') loadResumes();
    if (active === 'jobs') loadJobs(true);
    if (active === 'applications') loadApplications();
    loadProfile().catch(() => {});
  }

  const c = status.counts;
  $('#stat-grid').innerHTML = [
    ['Jobs cached', c.jobs, ''],
    ['Remote', c.remote_jobs, 'accent'],
    ['Visa sponsorship', c.visa_jobs, 'good'],
    ['Likely sponsors', c.likely_visa_jobs, 'warn'],
    ['Hires globally', c.global_jobs, 'accent'],
    ['Scored matches', c.matches, ''],
    ['Applications', c.applications, 'accent'],
    ['Needs review', c.needs_review, 'warn'],
    ['Submitted', c.submitted, 'good'],
    [`Applied (24h)`, `${status.applied_last_24h}/${status.daily_limit}`, ''],
  ].map(([label, num, kind]) => `
    <div class="stat ${kind}"><div class="num">${esc(num)}</div>
    <div class="label">${esc(label)}</div></div>`).join('');

  hosted = Boolean(status.hosted);
  if (hosted) {
    $('.tagline').textContent = 'Hosted copy · password-protected · form autofill runs on your own machine';
  }

  $('#topbar-status').innerHTML = [
    status.llm.available
      ? `<span class="pill ${status.llm.local ? 'good' : 'accent'}" title="${esc(status.llm.label)}">`
        + `AI · ${esc(status.llm.model)}${status.llm.local ? ' (local)' : ''}</span>`
      : '<span class="pill warn">AI off — rule-based tailoring</span>',
    status.playwright.available
      ? '<span class="pill good">browser ready</span>'
      : hosted
        // Expected, not an error: a serverless function cannot run a browser.
        ? '<span class="pill" title="Form autofill needs a real browser, which cannot run on '
          + 'Vercel. Use your local copy for Start applying.">autofill: local only</span>'
        : '<span class="pill bad">Playwright missing</span>',
    status.default_resume
      ? `<span class="pill accent">${esc(status.default_resume.label)}</span>`
      : '<span class="pill bad">no resume</span>',
  ].join('');

  renderWorker(status.worker);
  renderSystem(status);
}

function renderWorker(worker) {
  const pill = $('#worker-pill');
  pill.textContent = worker.running ? `running · ${worker.mode}` : 'idle';
  pill.className = `pill ${worker.running ? 'accent' : ''}`;

  $('#btn-stop').hidden = !worker.running;
  $('#btn-apply').disabled = worker.running;

  const pct = worker.total ? Math.round((worker.done / worker.total) * 100) : 0;
  $('#worker-progress').style.width = `${pct}%`;

  $('#worker-current').innerHTML = worker.current
    ? `<strong>${esc(worker.current.title)}</strong> — ${esc(worker.current.company)}
       <span class="pill accent">${esc(worker.current.score)}</span>
       <span class="hint">(${worker.done}/${worker.total})</span>`
    : (worker.total ? `<span class="hint">${worker.done}/${worker.total} processed</span>` : '');

  const log = $('#worker-log');
  const wasBottom = log.scrollTop + log.clientHeight >= log.scrollHeight - 30;
  log.textContent = worker.messages?.length ? worker.messages.join('\n') : 'No run yet.';
  if (wasBottom) log.scrollTop = log.scrollHeight;
}

async function loadRuns() {
  let runs;
  try { runs = await api('/runs?limit=6'); } catch { return; }
  $('#runs-list').innerHTML = runs.length ? runs.map((run) => `
    <div class="run">
      <div class="run-head">
        <strong>${esc(run.kind)}</strong>
        <span class="pill ${run.status === 'done' ? 'good' : run.status === 'failed' ? 'bad' : 'accent'}">
          ${esc(run.status)}</span>
      </div>
      <div class="hint">${esc((run.started_at || '').replace('T', ' ').slice(0, 19))}</div>
      ${run.messages?.length
        ? `<ul>${run.messages.slice(-6).map((m) => `<li>${esc(m)}</li>`).join('')}</ul>` : ''}
    </div>`).join('') : '<div class="empty">Nothing has run yet.</div>';
}

function renderSystem(status) {
  const el = $('#system-info');
  if (!el) return;
  el.innerHTML = `<dl class="kv">
    <dt>AI tailoring</dt><dd>${status.llm.available
      ? `${esc(status.llm.label)} · <code>${esc(status.llm.model)}</code>`
      : 'off — using the rule-based engine. Free local options are in the README.'}
      ${status.llm.last_error ? `<br><span class="pill bad">${esc(status.llm.last_error)}</span>` : ''}</dd>
    <dt>Browser automation</dt><dd>${status.playwright.available
      ? 'Playwright ready' : esc(status.playwright.message)}</dd>
    <dt>Applied in last 24h</dt><dd>${status.applied_last_24h} of ${status.daily_limit}</dd>
    <dt>Data location</dt><dd>${status.hosted
      ? 'Hosted Postgres database — resumes and documents are stored in it'
      : '<code>jobpilot/data/</code> — SQLite + your generated documents'}</dd>
  </dl>`;
}

/* ----------------------------------------------------------- pipeline btns */
// Set from /status. The hosted copy fetches inside the request, not in the background.
let hosted = false;

$('#btn-fetch').onclick = async (e) => {
  const btn = e.currentTarget;
  btn.disabled = true;
  try {
    const query = $('#fetch-query').value.trim();
    if (hosted) toast('Fetching jobs — this can take a minute…');
    const result = await api('/jobs/fetch', { method: 'POST', body: { query: query || null, rescore: true } });
    if (result.done) {
      const summary = (result.messages || []).slice(-2)
        .map((m) => m.replace(/^\d\d:\d\d:\d\d\s+/, '')).join(' · ');
      toast(summary || 'Fetch finished', summary.startsWith('Failed') ? 'err' : 'ok');
      loadRuns(); loadStatus();
    } else {
      toast('Fetching jobs in the background…', 'ok');
      setTimeout(loadRuns, 1500);
    }
  } catch (err) { toast(err.message, 'err'); }
  btn.disabled = false;
};

$('#btn-queue').onclick = async (e) => {
  const btn = e.currentTarget;
  btn.disabled = true;
  try {
    const limit = parseInt($('#queue-limit').value, 10) || 10;
    const result = await api('/applications/queue-top', { method: 'POST', body: { limit } });
    const skipped = Object.entries(result.skipped || {})
      .map(([reason, n]) => `${n} ${reason}`).join(', ');
    toast(`Queued ${result.queued} application(s)${skipped ? ` · skipped: ${skipped}` : ''}`,
      result.queued ? 'ok' : '');
    loadStatus();
  } catch (err) { toast(err.message, 'err'); }
  btn.disabled = false;
};

$('#btn-apply').onclick = async (e) => {
  const autoSubmit = $('#run-auto-submit').checked;
  if (autoSubmit && !confirm(
    'Auto-submit is ON.\n\nJobPilot will submit each application without showing it to you '
    + 'first. Screening answers cannot be taken back.\n\nContinue?')) return;

  e.currentTarget.disabled = true;
  try {
    const result = await api('/apply/start', {
      method: 'POST', body: { auto_submit: autoSubmit, limit: 25 },
    });
    toast(`Started — ${result.queued} application(s) in this run`, 'ok');
  } catch (err) { toast(err.message, 'err'); }
  loadStatus();
};

$('#btn-stop').onclick = async () => {
  await api('/apply/stop', { method: 'POST' });
  toast('Stopping after the current application…');
};

/* ----------------------------------------------------------------- resumes */
const dropzone = $('#dropzone');
$('#btn-browse').onclick = () => $('#resume-file').click();
$('#resume-file').onchange = (e) => { if (e.target.files[0]) uploadResume(e.target.files[0]); };

['dragenter', 'dragover'].forEach((evt) => dropzone.addEventListener(evt, (e) => {
  e.preventDefault(); dropzone.classList.add('over');
}));
['dragleave', 'drop'].forEach((evt) => dropzone.addEventListener(evt, (e) => {
  e.preventDefault(); dropzone.classList.remove('over');
}));
dropzone.addEventListener('drop', (e) => {
  const file = e.dataTransfer.files[0];
  if (file) uploadResume(file);
});

async function uploadResume(file) {
  $('#upload-status').textContent = `Reading ${file.name}…`;
  const form = new FormData();
  form.append('file', file);
  try {
    const resume = await api('/resumes', { method: 'POST', body: form });
    $('#upload-status').textContent = '';
    toast(`Parsed ${resume.skill_count} skills from ${file.name}`, 'ok');
    await loadResumes();
    showResumeDetail(resume);
    await loadProfile();
    await api('/match/run', { method: 'POST', body: {} }).catch(() => {});
    loadStatus();
  } catch (err) {
    // Keep the reason on screen. These messages tell you what to do about the
    // file ("export it as .docx"), which is useless if it vanishes in 4s.
    $('#upload-status').innerHTML =
      `<span class="upload-error"><strong>Upload failed.</strong> ${esc(err.message)}</span>`;
    toast('Upload failed — see the message above', 'err');
  } finally {
    $('#resume-file').value = '';   // let the same file be retried
  }
}

async function loadResumes() {
  let resumes;
  try {
    resumes = await api('/resumes');
  } catch (err) {
    // Never let a failed request masquerade as "you have no resume" — that
    // reads as data loss and sends people re-uploading for no reason.
    $('#resume-list').innerHTML =
      `<div class="empty">Could not load your resumes. ${esc(err.message)}</div>`;
    return;
  }
  $('#resume-list').innerHTML = resumes.length ? resumes.map((r) => `
    <div class="resume-card">
      <div>
        <strong>${esc(r.label)}</strong>
        ${r.is_default ? '<span class="pill good">default</span>' : ''}
        <div class="hint">${esc(r.filename)} · ${r.skill_count} skills ·
          ${r.experience_count} roles · ~${Math.round(r.years_experience)} yrs ·
          ${esc(r.seniority || 'unknown')} level</div>
      </div>
      <div class="inline">
        <button class="small" data-view="${r.id}">View</button>
        <button class="small" data-reparse="${r.id}" title="Re-run the parser over this file">Re-parse</button>
        ${r.is_default ? '' : `<button class="small" data-default="${r.id}">Make default</button>`}
        <button class="small danger" data-del="${r.id}">Delete</button>
      </div>
    </div>`).join('') : '<div class="empty">No resume uploaded yet.</div>';

  $$('#resume-list [data-view]').forEach((b) => b.onclick = async () => {
    showResumeDetail(await api(`/resumes/${b.dataset.view}`));
  });
  $$('#resume-list [data-reparse]').forEach((b) => b.onclick = async () => {
    b.disabled = true; b.textContent = 'Parsing…';
    try {
      const updated = await api(`/resumes/${b.dataset.reparse}/reparse`, { method: 'POST' });
      toast(`Re-parsed: ${updated.skill_count} skills, ${updated.experience_count} roles`, 'ok');
      await loadResumes();
      showResumeDetail(updated);
      await api('/match/run', { method: 'POST', body: {} }).catch(() => {});
      loadStatus();
    } catch (err) { toast(err.message, 'err'); b.disabled = false; b.textContent = 'Re-parse'; }
  });
  $$('#resume-list [data-default]').forEach((b) => b.onclick = async () => {
    await api(`/resumes/${b.dataset.default}/default`, { method: 'POST' });
    await loadResumes(); loadStatus();
    toast('Default resume changed — re-run matching', 'ok');
  });
  $$('#resume-list [data-del]').forEach((b) => b.onclick = async () => {
    if (!confirm('Delete this resume and its match scores?')) return;
    await api(`/resumes/${b.dataset.del}`, { method: 'DELETE' });
    await loadResumes(); loadStatus();
  });
}

function showResumeDetail(resume) {
  const parsed = resume.parsed || {};
  const contact = parsed.contact || {};
  $('#resume-detail-panel').hidden = false;
  $('#resume-detail').innerHTML = `
    <dl class="kv">
      <dt>Name</dt><dd>${esc(contact.name || '—')}</dd>
      <dt>Email</dt><dd>${esc(contact.email || '—')}</dd>
      <dt>Phone</dt><dd>${esc(contact.phone || '—')}</dd>
      <dt>Location</dt><dd>${esc(contact.location || '—')}</dd>
      <dt>Seniority</dt><dd>${esc(parsed.seniority || '—')}</dd>
      <dt>Years experience</dt><dd>${Math.round(parsed.years_experience || 0)}</dd>
      <dt>Sections detected</dt><dd>${esc((parsed.sections_found || []).join(', ') || '—')}</dd>
    </dl>
    <h2>Skills detected (${(resume.skills || []).length})</h2>
    <div class="chips">${(resume.skills || []).map((s) => `<span class="chip">${esc(s)}</span>`).join('')}</div>
    <h2>Experience parsed</h2>
    ${(parsed.experience || []).map((role) => `
      <div style="margin-bottom:12px">
        <strong>${esc(role.title || '(no title)')}</strong>
        ${role.company ? ` — ${esc(role.company)}` : ''}
        <span class="hint">${esc(role.start || '')} ${role.end ? `– ${esc(role.end)}` : ''}</span>
        <ul class="hint" style="margin:4px 0 0">${(role.bullets || []).slice(0, 4)
          .map((b) => `<li>${esc(b)}</li>`).join('')}</ul>
      </div>`).join('') || '<p class="hint">No roles parsed — check the raw text below.</p>'}
    <details><summary class="hint" style="cursor:pointer">Raw extracted text</summary>
      <pre style="max-height:300px;overflow:auto">${esc(resume.raw_text || '')}</pre></details>`;
}

/* ----------------------------------------------------------------- profile */
async function loadProfile() {
  state.profile = await api('/profile');
  fillForm($('#profile-form'), state.profile);
  fillForm($('#settings-form'), state.profile);
  renderCustomAnswers(state.profile.custom_answers || {});
}

function fillForm(form, data) {
  $$('input, select, textarea', form).forEach((input) => {
    const name = input.name;
    if (!name || !(name in data)) return;
    if (input.type === 'checkbox') input.checked = !!data[name];
    else if (LINE_FIELDS.includes(name)) input.value = (data[name] || []).join('\n');
    else if (LIST_FIELDS.includes(name)) input.value = (data[name] || []).join(', ');
    else input.value = data[name] ?? '';
  });
}

function readForm(form) {
  const out = {};
  $$('input, select, textarea', form).forEach((input) => {
    const name = input.name;
    if (!name) return;
    if (input.type === 'checkbox') out[name] = input.checked;
    else if (LINE_FIELDS.includes(name)) {
      out[name] = input.value.split(/[\n,]/).map((s) => s.trim().toLowerCase())
        .filter(Boolean);
    } else if (LIST_FIELDS.includes(name)) {
      out[name] = input.value.split(',').map((s) => s.trim()).filter(Boolean);
    } else if (input.type === 'number') {
      out[name] = input.value === '' ? 0 : Number(input.value);
    } else out[name] = input.value;
  });
  return out;
}

$('#profile-form').onsubmit = async (e) => {
  e.preventDefault();
  try {
    state.profile = await api('/profile', { method: 'PUT', body: readForm(e.target) });
    $('#profile-saved').textContent = 'Saved ✓';
    setTimeout(() => { $('#profile-saved').textContent = ''; }, 2500);
    await api('/match/run', { method: 'POST', body: {} }).catch(() => {});
    loadStatus();
  } catch (err) { toast(err.message, 'err'); }
};

/* ---------------------------------------------------------------- settings */
$('#settings-form').onsubmit = async (e) => {
  e.preventDefault();
  const payload = readForm(e.target);
  payload.enabled_sources = $$('#sources-list input:checked').map((i) => i.value);
  payload.salary_expectations = readSalaryOverrides();
  payload.custom_answers = {};
  $$('.answer-row').forEach((row) => {
    const key = $('.answer-key', row).value.trim();
    const value = $('.answer-value', row).value.trim();
    if (key) payload.custom_answers[key] = value;
  });
  try {
    state.profile = await api('/profile', { method: 'PUT', body: payload });
    $('#settings-saved').textContent = 'Saved ✓';
    setTimeout(() => { $('#settings-saved').textContent = ''; }, 2500);
    loadSalaryTable();
  } catch (err) { toast(err.message, 'err'); }
};

async function loadSources() {
  const data = await api('/sources');
  state.sources = data.sources;
  $('#sources-list').innerHTML = data.sources.map((s) => `
    <label class="source-card">
      <input type="checkbox" value="${esc(s.key)}" ${data.enabled.includes(s.key) ? 'checked' : ''}>
      <span><strong>${esc(s.label)}</strong><p>${esc(s.description)}</p></span>
    </label>`).join('');

  const select = $('#filter-source');
  select.innerHTML = '<option value="">Source: all</option>'
    + data.sources.map((s) => `<option value="${esc(s.key)}">${esc(s.label)}</option>`).join('');
}

async function loadSalaryTable() {
  let data;
  try { data = await api('/salary/preview'); } catch { return; }

  $('#salary-band').innerHTML = `Based on <strong>${data.years} years</strong>`
    + `${data.seniority ? ` and a <strong>${esc(data.seniority)}</strong> resume` : ''}`
    + ` &rarr; treated as the <strong>${esc(data.band)}</strong> band.`;

  $('#salary-table').innerHTML = data.rows.map((row) => `
    <div class="salary-row">
      <span class="salary-country">${esc(row.country)}</span>
      <input class="salary-input" data-country="${esc(row.country)}"
             value="${row.overridden ? esc(row.salary) : ''}"
             placeholder="${esc(row.salary)}">
    </div>`).join('');
}

function readSalaryOverrides() {
  const out = {};
  $$('.salary-input').forEach((input) => {
    const value = input.value.trim();
    if (value) out[input.dataset.country] = value;
  });
  return out;
}

function renderCustomAnswers(answers) {
  const entries = Object.entries(answers);
  if (!entries.length) entries.push(['', '']);
  $('#custom-answers').innerHTML = entries.map(([key, value]) => `
    <div class="answer-row">
      <input class="answer-key" placeholder="label contains…" value="${esc(key)}">
      <input class="answer-value" placeholder="answer to type" value="${esc(value)}">
      <button type="button" class="small ghost answer-del">✕</button>
    </div>`).join('');
  $$('.answer-del').forEach((b) => b.onclick = () => b.closest('.answer-row').remove());
}

$('#btn-add-answer').onclick = () => {
  const row = document.createElement('div');
  row.className = 'answer-row';
  row.innerHTML = `<input class="answer-key" placeholder="label contains…">
    <input class="answer-value" placeholder="answer to type">
    <button type="button" class="small ghost answer-del">✕</button>`;
  $('.answer-del', row).onclick = () => row.remove();
  $('#custom-answers').appendChild(row);
};

$('#btn-test-ai').onclick = async (e) => {
  const btn = e.currentTarget;
  const out = $('#ai-test-result');
  btn.disabled = true;
  out.innerHTML = '<span class="hint">Contacting the provider\u2026 '
    + 'a local CPU model can take a few minutes.</span>';
  try {
    const r = await api('/llm/test', { method: 'POST' });
    out.innerHTML = r.ok
      ? `<div class="callout info"><strong>Working.</strong> `
        + `${esc(r.provider)} answered in ${r.ms} ms using `
        + `<code>${esc(r.model || 'default')}</code>.</div>`
      : `<div class="callout warn"><strong>Not working.</strong> `
        + `${esc(r.error)}${r.model ? ` (model <code>${esc(r.model)}</code>)` : ''}`
        + `<br>Tailoring will use the rule-based engine until this is fixed.</div>`;
  } catch (err) {
    out.innerHTML = `<div class="callout warn">${esc(err.message)}</div>`;
  }
  btn.disabled = false;
  loadStatus();
};

$('#btn-clear-jobs').onclick = async () => {
  if (!confirm('Remove all cached jobs that you have not applied to?')) return;
  const result = await api('/jobs', { method: 'DELETE' });
  toast(`Removed ${result.removed} jobs`, 'ok');
  loadStatus();
};

/* -------------------------------------------------------------------- jobs */
function jobQuery(offset) {
  const params = new URLSearchParams({
    limit: '40', offset: String(offset), sort: $('#filter-sort').value,
  });
  const q = $('#job-search').value.trim();
  if (q) params.set('q', q);
  if ($('#filter-visa').value) params.set('visa', $('#filter-visa').value);
  if ($('#filter-remote').value) params.set('remote', $('#filter-remote').value);
  if ($('#filter-source').value) params.set('source', $('#filter-source').value);
  const minScore = Number($('#filter-score').value);
  if (minScore > 0) params.set('min_score', String(minScore));
  return params.toString();
}

async function loadJobs(reset = false) {
  if (reset) { state.jobOffset = 0; state.jobs = []; }
  let data;
  try { data = await api(`/jobs?${jobQuery(state.jobOffset)}`); }
  catch (err) { toast(err.message, 'err'); return; }

  state.jobs = reset ? data.items : state.jobs.concat(data.items);
  state.jobTotal = data.total;
  state.jobOffset = state.jobs.length;

  $('#jobs-count').textContent = `${data.total} job(s) match these filters`;
  $('#btn-more-jobs').hidden = state.jobs.length >= data.total;
  renderJobs();
}

function renderJobs() {
  if (!state.jobs.length) {
    $('#jobs-list').innerHTML =
      '<div class="empty">No jobs yet. Hit “Fetch &amp; score jobs” on the Dashboard.</div>';
    return;
  }
  $('#jobs-list').innerHTML = state.jobs.map((job) => `
    <div class="job-card">
      <div class="job-head">
        <div>
          <p class="job-title">${esc(job.title)}</p>
          <div class="job-meta">${esc(job.company || 'Unknown company')}
            ${job.location ? ` · ${esc(job.location)}` : ''}
            ${job.salary ? ` · ${esc(job.salary)}` : ''}
            · <span class="hint">${esc(job.source)}</span></div>
          <div class="job-tags">
            ${job.is_remote ? '<span class="pill accent">remote</span>' : ''}
            ${visaPill(job.visa_status)}
            ${(job.matched_skills || []).slice(0, 6)
              .map((s) => `<span class="chip match">${esc(s)}</span>`).join('')}
            ${(job.missing_skills || []).slice(0, 3)
              .map((s) => `<span class="chip miss">${esc(s)}</span>`).join('')}
          </div>
          <p class="job-snippet">${esc((job.description || '').slice(0, 240))}</p>
        </div>
        <div class="score-badge ${scoreClass(job.score)}">
          <div class="val">${job.score == null ? '—' : Math.round(job.score)}</div>
          <div class="cap">match</div>
        </div>
      </div>
      <div class="job-actions">
        <button class="small" data-detail="${job.id}">Why this score</button>
        <a href="${esc(job.url || job.apply_url)}" target="_blank" rel="noopener">
          <button class="small ghost">Open posting</button></a>
        ${job.application
          ? `<span class="pill accent">queued · ${esc(job.application.status)}</span>`
          : `<button class="small primary" data-apply="${job.id}">Tailor &amp; queue</button>`}
        <button class="small ghost" data-hide="${job.id}">Hide</button>
      </div>
    </div>`).join('');

  $$('#jobs-list [data-detail]').forEach((b) => b.onclick = () => showJobDetail(b.dataset.detail));
  $$('#jobs-list [data-apply]').forEach((b) => b.onclick = () => queueJob(b.dataset.apply, b));
  $$('#jobs-list [data-hide]').forEach((b) => b.onclick = async () => {
    await api(`/jobs/${b.dataset.hide}/hide`, { method: 'POST' });
    b.closest('.job-card').remove();
  });
}

async function showJobDetail(jobId) {
  const job = await api(`/jobs/${jobId}`);
  const bd = job.breakdown || {};
  openModal(`
    <h1>${esc(job.title)}</h1>
    <p class="hint">${esc(job.company)} · ${esc(job.location || '—')} · via ${esc(job.source)}</p>
    <div class="job-tags" style="margin-bottom:14px">
      ${job.is_remote ? '<span class="pill accent">remote</span>' : ''}
      ${visaPill(job.visa_status)}
      <span class="pill">score ${job.score == null ? '—' : Math.round(job.score)}</span>
    </div>
    ${job.score != null ? `<h2>Score breakdown</h2>
      <dl class="kv">
        <dt>Skill coverage</dt><dd>${bd.skills ?? '—'}%
          ${bd.skills_named !== undefined
            ? `<span class="hint">(${bd.skills_raw}% of the ${bd.skills_named} skill(s)
               this posting names${bd.skills_named < 6
                 ? ' — too few to trust, so damped toward neutral' : ''})</span>` : ''}</dd>
        <dt>Content similarity</dt><dd>${bd.content ?? '—'}%</dd>
        <dt>Title fit</dt><dd>${bd.title ?? '—'}%</dd>
        <dt>Your preferences</dt><dd>${bd.preferences ?? '—'}%</dd>
        <dt>Adjustment</dt><dd>×${bd.modifier ?? 1}</dd>
      </dl>
      <ul class="hint">${(job.reasons || []).map((r) => `<li>${esc(r)}</li>`).join('')}</ul>` : ''}
    ${job.visa_evidence?.length ? `<h2>Visa evidence</h2>
      <ul class="hint">${job.visa_evidence.map((e) => `<li>${esc(e)}</li>`).join('')}</ul>` : ''}
    <h2>Skills you have (${(job.matched_skills || []).length})</h2>
    <div class="chips">${(job.matched_skills || [])
      .map((s) => `<span class="chip match">${esc(s)}</span>`).join('') || '<span class="hint">none</span>'}</div>
    <h2>Skills you are missing (${(job.missing_skills || []).length})</h2>
    <div class="chips">${(job.missing_skills || [])
      .map((s) => `<span class="chip miss">${esc(s)}</span>`).join('') || '<span class="hint">none</span>'}</div>
    <h2>Description</h2>
    <pre>${esc(job.description || '')}</pre>`);
}

async function queueJob(jobId, button) {
  button.disabled = true;
  button.textContent = 'Tailoring…';
  try {
    const app = await api('/applications', { method: 'POST', body: { job_id: Number(jobId) } });
    toast(app.status === 'ready'
      ? 'Tailored resume ready — see the Applications tab'
      : `Application ${app.status}${app.error ? `: ${app.error}` : ''}`,
    app.status === 'ready' ? 'ok' : 'err');
    loadJobs(true);
    loadStatus();
  } catch (err) {
    toast(err.message, 'err');
    button.disabled = false;
    button.textContent = 'Tailor & queue';
  }
}

$('#btn-refresh-jobs').onclick = () => loadJobs(true);
$('#btn-more-jobs').onclick = () => loadJobs(false);
$('#job-search').onkeydown = (e) => { if (e.key === 'Enter') loadJobs(true); };
['#filter-visa', '#filter-remote', '#filter-source', '#filter-sort', '#filter-score']
  .forEach((sel) => { $(sel).onchange = () => loadJobs(true); });

/* ------------------------------------------------------------ applications */
const OUTCOME_PILL = {
  applied: '', acknowledged: 'accent', screening: 'accent', interview: 'accent',
  final: 'accent', offer: 'good', accepted: 'good',
  rejected: 'bad', withdrawn: '', ghosted: 'bad',
};

const STATUS_PILL = {
  queued: '', preparing: 'accent', ready: 'accent',
  needs_review: 'warn', submitted: 'good', failed: 'bad', skipped: '',
};

let OUTCOMES = [];

async function loadTracker() {
  let t;
  try { t = await api('/applications/tracker/summary'); } catch { return; }
  OUTCOMES = t.outcomes;

  const outcomeFilter = $('#filter-app-outcome');
  if (outcomeFilter.options.length <= 2) {
    OUTCOMES.forEach((o) => {
      const opt = document.createElement('option');
      opt.value = o.key; opt.textContent = o.label;
      outcomeFilter.appendChild(opt);
    });
  }

  $('#tracker-rates').innerHTML = [
    ['Submitted', t.submitted, ''],
    ['Still open', t.in_progress, 'accent'],
    ['Reply rate', `${t.response_rate}%`, t.response_rate > 0 ? 'good' : ''],
    ['Interview rate', `${t.interview_rate}%`, t.interview_rate > 0 ? 'good' : ''],
    ['Offer rate', `${t.offer_rate}%`, t.offer_rate > 0 ? 'good' : ''],
    ['Avg days to reply', t.avg_days_to_reply ?? '—', ''],
    ['Needs follow-up', t.needs_follow_up, t.needs_follow_up ? 'warn' : ''],
    ['Likely ghosted', t.possibly_ghosted, t.possibly_ghosted ? 'warn' : ''],
  ].map(([label, value, kind]) => `
    <div class="rate ${kind}"><div class="num">${esc(value)}</div>
    <div class="label">${esc(label)}</div></div>`).join('');

  const max = Math.max(1, ...Object.values(t.counts));
  $('#tracker-funnel').innerHTML = OUTCOMES.map((o) => {
    const n = t.counts[o.key] || 0;
    if (!n) return '';
    return `<div class="funnel-row">
      <span class="funnel-label">${esc(o.label)}</span>
      <span class="funnel-bar"><i style="width:${(n / max) * 100}%"></i></span>
      <span class="funnel-num">${n}</span></div>`;
  }).join('') || '<p class="hint">Nothing submitted yet — the funnel fills in as you apply.</p>';
}

async function setOutcome(id, outcome, note = '') {
  try {
    await api(`/applications/${id}/outcome`, { method: 'POST', body: { outcome, note } });
    toast(`Marked as “${OUTCOMES.find((o) => o.key === outcome)?.label || outcome}”`, 'ok');
    await loadApplications();
    loadTracker();
    loadStatus();
  } catch (err) { toast(err.message, 'err'); }
}

async function loadApplications() {
  const status = $('#filter-app-status').value;
  const outcome = $('#filter-app-outcome').value;
  const qs = new URLSearchParams();
  if (status) qs.set('status', status);
  if (outcome) qs.set('outcome', outcome);
  const apps = await api(`/applications${qs.toString() ? `?${qs}` : ''}`);
  $('#apps-list').innerHTML = apps.length ? apps.map((app) => `
    <div class="app-card">
      <div class="job-head">
        <div>
          <p class="job-title">${esc(app.job?.title || 'Unknown role')}</p>
          <div class="job-meta">${esc(app.job?.company || '')}
            ${app.job?.location ? ` · ${esc(app.job.location)}` : ''}
            ${app.ats ? ` · ${esc(app.ats)}` : ''}</div>
          <div class="job-tags">
            <span class="pill ${STATUS_PILL[app.status] || ''}">${esc(app.status.replace('_', ' '))}</span>
            ${app.outcome ? `<span class="pill ${OUTCOME_PILL[app.outcome] || 'accent'}">${esc(app.outcome_label)}</span>` : ''}
            ${app.days_since_applied != null
              ? `<span class="pill">${app.days_since_applied}d ago</span>` : ''}
            ${app.needs_follow_up ? '<span class="pill warn">⚑ follow up</span>' : ''}
            ${app.possibly_ghosted ? '<span class="pill bad">no reply in 45d</span>' : ''}
            ${app.job ? visaPill(app.job.visa_status) : ''}
            ${app.unfilled_fields?.length
              ? `<span class="pill warn">${app.unfilled_fields.length} field(s) to check</span>` : ''}
          </div>
          ${app.error ? `<p class="hint" style="color:#ff9b9b">${esc(app.error)}</p>` : ''}
        </div>
        <div class="score-badge ${scoreClass(app.score)}">
          <div class="val">${Math.round(app.score || 0)}</div><div class="cap">match</div>
        </div>
      </div>
      <div class="job-actions">
        <button class="small" data-appdetail="${app.id}">Details</button>
        ${app.has_pdf ? `<a href="/api/files/${app.id}/pdf" target="_blank">
          <button class="small ghost">Resume PDF</button></a>` : ''}
        ${app.has_docx ? `<a href="/api/files/${app.id}/docx" target="_blank">
          <button class="small ghost">DOCX</button></a>` : ''}
        ${app.has_cover_pdf ? `<a href="/api/files/${app.id}/cover" target="_blank">
          <button class="small ghost">Cover letter</button></a>` : ''}
        ${app.has_screenshot ? `<a href="/api/files/${app.id}/screenshot" target="_blank">
          <button class="small ghost">Screenshot</button></a>` : ''}
        ${app.job?.apply_url ? `<a href="${esc(app.job.apply_url)}" target="_blank" rel="noopener">
          <button class="small">Open form</button></a>` : ''}
        <button class="small" data-reprep="${app.id}">Re-tailor</button>
        <button class="small danger" data-appdel="${app.id}">Remove</button>
        <label class="outcome-pick">Outcome
          <select data-outcome="${app.id}">
            <option value="">— not set —</option>
            ${OUTCOMES.map((o) => `<option value="${esc(o.key)}"
              ${o.key === app.outcome ? 'selected' : ''}>${esc(o.label)}</option>`).join('')}
          </select>
        </label>
      </div>
    </div>`).join('') : '<div class="empty">No applications match these filters.</div>';

  $$('[data-outcome]').forEach((sel) => sel.onchange = () => {
    if (!sel.value) return;
    const note = prompt('Optional note for this update (dates, interviewer, feedback):', '');
    if (note === null) { loadApplications(); return; }   // cancelled
    setOutcome(sel.dataset.outcome, sel.value, note);
  });
  $$('[data-appdetail]').forEach((b) => b.onclick = () => showApplication(b.dataset.appdetail));
  $$('[data-reprep]').forEach((b) => b.onclick = async () => {
    b.disabled = true; b.textContent = 'Working…';
    try { await api(`/applications/${b.dataset.reprep}/prepare`, { method: 'POST' }); }
    catch (err) { toast(err.message, 'err'); }
    loadApplications();
  });
  $$('[data-appdel]').forEach((b) => b.onclick = async () => {
    if (!confirm('Remove this application?')) return;
    await api(`/applications/${b.dataset.appdel}`, { method: 'DELETE' });
    loadApplications(); loadStatus();
  });
}

async function showApplication(id) {
  const app = await api(`/applications/${id}`);
  const tailored = app.tailored || {};
  openModal(`
    <h1>${esc(app.job?.title || '')}</h1>
    <p class="hint">${esc(app.job?.company || '')} · status
      <span class="pill ${STATUS_PILL[app.status] || ''}">${esc(app.status)}</span></p>

    ${tailored.headline ? `<h2>Tailored headline</h2><p>${esc(tailored.headline)}</p>` : ''}
    ${tailored.summary ? `<h2>Tailored summary</h2><p>${esc(tailored.summary)}</p>` : ''}
    ${tailored.top_skills?.length ? `<h2>Skills, reordered for this job</h2>
      <div class="chips">${tailored.top_skills.map((s) => `<span class="chip">${esc(s)}</span>`).join('')}</div>` : ''}
    ${tailored.gaps?.length ? `<h2>Honest gaps</h2>
      <div class="chips">${tailored.gaps.map((s) => `<span class="chip miss">${esc(s)}</span>`).join('')}</div>` : ''}
    ${app.cover_letter ? `<h2>Cover letter</h2><pre>${esc(app.cover_letter)}</pre>` : ''}

    ${Object.keys(app.filled_fields || {}).length ? `<h2>Fields filled automatically</h2>
      <dl class="kv">${Object.entries(app.filled_fields)
        .map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('')}</dl>` : ''}
    ${app.unfilled_fields?.length ? `<h2>Check these before submitting</h2>
      <p class="hint">Either JobPilot could not answer them, or it made a judgement call
        that you should confirm.</p>
      <ul class="hint">${app.unfilled_fields.map((f) => `<li>${esc(f)}</li>`).join('')}</ul>` : ''}
    ${app.notes ? `<h2>Your notes</h2><pre>${esc(app.notes)}</pre>` : ''}
    ${app.events?.length ? `<h2>History</h2>
      <dl class="kv">${app.events.map((e) => `<dt>${esc((e.at || '').replace('T', ' '))}</dt>
        <dd>${esc(e.outcome)}${e.note ? ` — ${esc(e.note)}` : ''}</dd>`).join('')}</dl>` : ''}
    ${app.log?.length ? `<h2>Automation log</h2><pre>${esc(app.log.join('\n'))}</pre>` : ''}`);
}

$('#btn-refresh-apps').onclick = () => { loadApplications(); loadTracker(); };
$('#filter-app-status').onchange = loadApplications;
$('#filter-app-outcome').onchange = loadApplications;

/* -------------------------------------------------------------------- boot */
async function boot() {
  await Promise.all([loadProfile(), loadSources(), loadSalaryTable(),
                   loadTracker()]).catch(() => {});
  await loadStatus();
  await loadRuns();
  await loadResumes().catch(() => {});
  state.pollTimer = setInterval(() => {
    loadStatus();
    if (document.hidden) return;
    loadRuns();
  }, 3000);
}

boot();

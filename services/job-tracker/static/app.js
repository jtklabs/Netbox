'use strict';

const TERMINAL = new Set(['completed', 'completed_with_warnings', 'failed', 'expired', 'cancelled']);
const JOB_STATES = [
  {kind:'success', label:'Successful', statuses:['completed']},
  {kind:'warning', label:'Warnings', statuses:['completed_with_warnings']},
  {kind:'failed', label:'Failed', statuses:['failed']},
  {kind:'other', label:'Cancelled / expired', statuses:['expired','cancelled']},
  {kind:'running', label:'In progress', statuses:['claimed','running']},
  {kind:'waiting', label:'Waiting / held', statuses:['pending','held']},
  {kind:'recovery', label:'Recovery required', statuses:['recovery_required']}
];
function statusBreakdown(jobs) {
  const segments = JOB_STATES.map(state => ({...state, count:jobs.filter(j => state.statuses.includes(j.status)).length}));
  const unknown = jobs.length - segments.reduce((total,state) => total + state.count,0);
  if (unknown) segments.push({kind:'unknown',label:'Unknown status',count:unknown});
  return segments;
}
const ACTIVE = new Set(['claimed', 'running', 'recovery_required']);
const LABELS = {pending:'Pending', held:'Held', claimed:'Prechecks', running:'Applying', completed:'Completed', completed_with_warnings:'Completed · warnings', failed:'Failed / blocked', recovery_required:'Recovery required', expired:'Window missed', cancelled:'Cancelled'};
const OPERATIONS = {upgrade:'Software upgrade', stage:'Software staging', audit:'Upgrade audit', audit_config:'Standards audit', remediate:'Standards remediation'};
const operationLabel = operation => OPERATIONS[operation] || title(operation);
const operationFamily = operation => ['audit_config','remediate'].includes(operation) ? 'standards' : operation === 'stage' ? 'staging' : operation === 'audit' ? 'audit' : 'upgrade';
const displayText = value => String(value || '').replace(/\bpre[- ]?stag(?:e|ing)\b/gi, 'Software staging');
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const title = value => String(value || '').replace(/_/g, ' ').replace(/^./, c => c.toUpperCase());
const date = value => value ? new Date(value).toLocaleString([], {month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}) : '—';
const age = (value, at=Date.now()) => {
  if (!value) return 'No heartbeat';
  const seconds = Math.max(0, Math.floor((at - new Date(value).getTime()) / 1000));
  if (!Number.isFinite(seconds)) return 'Unknown';
  return seconds < 60 ? `${seconds}s ago` : seconds < 3600 ? `${Math.floor(seconds / 60)}m ago` : `${Math.floor(seconds / 3600)}h ago`;
};
function needsAttention(job) {
  return job.heartbeat_stale || job.needs_recovery || ['held','failed','recovery_required','completed_with_warnings','expired'].includes(job.status);
}
function counts(jobs) {
  return {total: jobs.length, finished: jobs.filter(j => TERMINAL.has(j.status)).length,
    running: jobs.filter(j => ['claimed','running'].includes(j.status)).length,
    waiting: jobs.filter(j => ['pending','held'].includes(j.status)).length,
    issues: jobs.filter(needsAttention).length};
}
function sortJobs(jobs) {
  return [...jobs].sort((a,b) => jobPriority(a) - jobPriority(b) || (a.planned_wave || 1) - (b.planned_wave || 1) ||
    String(a.scheduled_at).localeCompare(String(b.scheduled_at)) || a.id - b.id);
}
function jobPriority(job) {
  if (['running','claimed'].includes(job.status)) return 0;
  if (job.status === 'recovery_required') return 1;
  if (job.status === 'pending') return 2;
  if (job.status === 'held') return 3;
  return TERMINAL.has(job.status) ? 5 : 4;
}
const priority = jobs => jobs.reduce((best, job) => Math.min(best, jobPriority(job)), 6);
function buildGroups(jobs) {
  const groups = new Map();
  for (const job of jobs) {
    for (const name of new Set(job.groups.length ? job.groups : [null])) {
      if (!groups.has(name)) groups.set(name, []);
      groups.get(name).push(job);
    }
  }
  return [...groups].map(([name,members]) => ({name, jobs:members,
    batches:[...groupBy(members, j => j.batch_id)].map(([id,batchJobs]) => ({id,jobs:batchJobs,
      waves:[...groupBy(sortJobs(batchJobs), j => j.planned_wave || 1)].map(([number,waveJobs]) => ({number,jobs:waveJobs}))
        .sort((a,b) => priority(a.jobs) - priority(b.jobs) || a.number - b.number)
    })).sort((a,b) => priority(a.jobs) - priority(b.jobs) || String(a.jobs[0].scheduled_at).localeCompare(String(b.jobs[0].scheduled_at)) || a.id.localeCompare(b.id))
  })).sort((a,b) => priority(a.jobs) - priority(b.jobs) || String(a.name || '\uffff').localeCompare(String(b.name || '\uffff')));
}
function splitJobs(jobs, allJobs=jobs) {
  // Decide from the whole batch, even if filters hide an unfinished sibling.
  const activeBatches = new Set(allJobs.filter(j => !TERMINAL.has(j.status)).map(j => j.batch_id));
  return {live: jobs.filter(j => activeBatches.has(j.batch_id)), finished: jobs.filter(j => !activeBatches.has(j.batch_id))};
}
function groupBy(items, key) {
  const map = new Map();
  for (const item of items) { const k = key(item); if (!map.has(k)) map.set(k, []); map.get(k).push(item); }
  return map;
}
function progress(jobs, label) {
  const n = counts(jobs);
  let x = 0;
  const breakdown = statusBreakdown(jobs);
  const description = `${n.finished} of ${n.total} finished. ` + breakdown.map(s => `${s.count} ${s.label.toLowerCase()}`).join(', ');
  const segments = breakdown.map(({kind,count,label:stateLabel}) => {
    const width = jobs.length ? count / jobs.length * 100 : 0;
    const rect = `<rect class="segment-${kind}" x="${x}" width="${width}" height="7"><title>${count} ${esc(stateLabel.toLowerCase())}</title></rect>`; x += width; return rect;
  }).join('');
  return `<svg class="progress" viewBox="0 0 100 7" preserveAspectRatio="none" role="img" aria-label="${esc(label)}: ${esc(description)}">${segments}</svg>`;
}
function outcomeLegend(jobs) {
  return statusBreakdown(jobs).map(({kind,label,count}) => `<span class="outcome-${kind}">● ${count} ${label.toLowerCase()}</span>`).join('');
}
function stepTiming(job) {
  if (!['running','claimed'].includes(job.status)) return '';
  const seconds = job.step_timing?.elapsed_seconds;
  if (!Number.isFinite(seconds) || seconds < 0) return 'Step timing unavailable';
  return `Time in step${job.step_timing.name ? ` (${job.step_timing.name})` : ''}: ${duration(seconds)} · last reported`;
}
function duration(value) {
  const seconds = Math.floor(value);
  return seconds >= 3600 ? `${Math.floor(seconds / 3600)}h ${Math.floor(seconds % 3600 / 60)}m ${seconds % 60}s` : seconds >= 60 ? `${Math.floor(seconds / 60)}m ${seconds % 60}s` : `${seconds}s`;
}
function totalRuntime(job, now=Date.now()) {
  const reported = job.step_timing?.total_elapsed_seconds;
  if (Number.isFinite(reported) && reported >= 0) return `Total runtime: ${duration(reported)} · last reported`;
  const start = Date.parse(job.started_at);
  const end = job.completed_at ? Date.parse(job.completed_at) : TERMINAL.has(job.status) ? NaN : now;
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return 'Total runtime unavailable';
  return `Total runtime: ${duration((end - start) / 1000)} · since job start`;
}
function filterJobs(jobs, filters) {
  const query = filters.search.trim().toLowerCase();
  return jobs.filter(j => (!filters.batch || j.batch_id === filters.batch) && (!filters.operation || j.operation === filters.operation) &&
    (!filters.site || j.site === filters.site) && (!filters.attention || needsAttention(j)) &&
    (!query || [j.device_name,j.site,j.message,j.held_reason,displayText(j.description),operationLabel(j.operation),...j.groups].map(displayText).join(' ').toLowerCase().includes(query)));
}

if (typeof module !== 'undefined') module.exports = {counts, sortJobs, filterJobs, needsAttention, progress, outcomeLegend, stepTiming, totalRuntime, esc, buildGroups, splitJobs, operationLabel, operationFamily, displayText};

if (typeof document !== 'undefined') {
  const $ = id => document.getElementById(id);
  const params = new URLSearchParams(location.search);
  const state = {data:null, networkError:'', lastReceived:0, selectedJob:null, filters:{batch:params.get('batch') || '',operation:params.get('operation') || '',site:params.get('site') || '',search:params.get('search') || '',attention:params.get('attention') === '1'}};
  for (const key of ['batch','operation','site','search','attention']) {
    const el = $(key);
    if (key === 'attention') el.checked = state.filters[key]; else if (key === 'search') el.value = state.filters[key];
    el.addEventListener(key === 'search' ? 'input' : 'change', () => {
      state.filters[key] = key === 'attention' ? el.checked : el.value;
      const query = new URLSearchParams();
      for (const [k,v] of Object.entries(state.filters)) if (v) query.set(k, v === true ? '1' : v);
      history.replaceState(null, '', location.pathname + (query.size ? '?' + query : ''));
      render();
    });
  }
  function options(id, values, empty) {
    const selected = state.filters[id];
    const entries = [...values];
    if (selected && !entries.some(([value]) => value === selected)) entries.push([selected, `${selected} (not in current feed)`]);
    const html = `<option value="">${esc(empty)}</option>` + entries.map(([value,label]) => `<option value="${esc(value)}">${esc(label)}</option>`).join('');
    if ($(id).innerHTML !== html) $(id).innerHTML = html;
    $(id).value = selected;
  }
  function timeNow() {
    return state.data ? new Date(state.data.server_time).getTime() + Date.now() - state.lastReceived : Date.now();
  }
  function freshness() {
    $('clock').textContent = new Date().toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',second:'2-digit'});
    const data = state.data;
    const tooOld = data?.updated_at && timeNow() - new Date(data.updated_at).getTime() > Math.max(30, data.poll_seconds * 3) * 1000;
    const error = state.networkError || data?.error || (tooOld ? 'The latest NetBox snapshot is overdue.' : '');
    const live = !!data?.updated_at && !error;
    $('connection').textContent = live ? `Live · every ${data.poll_seconds}s` : data?.updated_at ? 'Updates interrupted' : 'Connecting';
    $('connection-dot').className = 'dot ' + (live ? 'live' : 'stale');
    document.body.classList.toggle('disconnected', !live);
    $('error').hidden = !error;
    $('error').textContent = error ? `${error} ${data?.updated_at ? 'Showing the last successful snapshot; status may have changed.' : 'No job data yet.'} Retrying automatically.` : '';
    $('updated').textContent = data?.updated_at ? `Last successful sync ${date(data.updated_at)} · ${age(data.updated_at, timeNow())} · Times shown in ${Intl.DateTimeFormat().resolvedOptions().timeZone}` : 'Waiting for first update';
    document.querySelectorAll('[data-heartbeat]').forEach(el => { el.textContent = age(el.dataset.heartbeat, timeNow()); });
  }
  function operationBadge(operation) {
    return `<span class="operation-badge operation-${operationFamily(operation)}">${esc(operationLabel(operation))}</span>`;
  }
  function operationSummary(jobs) {
    return [...groupBy(jobs, j => j.operation)].map(([operation,members]) => `<span class="operation-count">${operationBadge(operation)}<b>${members.length}</b></span>`).join('');
  }
  function row(job, groupName) {
    const moving = ['running','claimed'].includes(job.status);
    const otherGroups = job.groups.filter(name => name !== groupName);
    return `<tr><td><button class="device-button" data-job="${job.id}">${esc(job.device_name)}</button><small>${esc(job.site)}</small>${otherGroups.length ? `<small>Also in: ${esc(otherGroups.join(' · '))}</small>` : ''}</td>
      <td>${operationBadge(job.operation)}</td>
      <td><span class="badge ${esc(job.status)}">${esc(LABELS[job.status] || title(job.status))}</span>${job.heartbeat_stale ? '<div class="warning-line">Heartbeat stale</div>' : ''}${job.needs_recovery ? '<div class="warning-line">Recovery needs review</div>' : ''}</td>
      <td><div class="stage">${esc(displayText(title(job.stage)) || (job.status === 'pending' ? 'Awaiting worker / dependencies' : LABELS[job.status]))}</div>${moving || job.started_at || job.step_timing ? `<div class="job-timing">${moving ? `<span class="step-timing">${esc(stepTiming(job))}</span>` : ''}<span class="total-runtime">${esc(totalRuntime(job,timeNow()))}</span></div>` : ''}<div class="message">${esc(displayText(job.held_reason || job.message) || (job.status === 'pending' ? `Scheduled ${date(job.scheduled_at)}` : 'No stage message yet'))}</div>${moving ? '<div class="row-progress" aria-label="Job in progress; percentage unavailable"></div>' : ''}</td>
      <td><span class="heartbeat" data-heartbeat="${esc(job.last_seen_at || '')}">${age(job.last_seen_at,timeNow())}</span><small>${esc(job.poller || 'Unassigned')}</small></td><td><small>#${job.id}</small></td></tr>`;
  }
  function wave(waveData, contextJobs, groupName) {
    const jobs = waveData.jobs, n = counts(contextJobs), visible = counts(jobs);
    const pending = jobs.filter(j => j.status === 'pending').length;
    const held = jobs.filter(j => j.status === 'held').length;
    const status = visible.running ? 'In progress' : jobs.some(j => j.status === 'recovery_required') ? 'Recovery required' : pending ? 'Pending' : held ? 'Held' : 'Finished';
    return `<section class="wave"><div class="wave-header"><span class="wave-number">WAVE ${waveData.number}</span><div class="wave-name"><strong>${status}</strong><p>${visible.running} running · ${pending} pending${held ? ` · ${held} held` : ''}${visible.issues ? ` · ${visible.issues} need attention` : ''}</p></div><div class="wave-progress"><span>${n.finished} / ${n.total} finished</span>${progress(contextJobs,`Wave ${waveData.number}`)}</div></div><div class="table-scroll"><table><thead><tr><th>DEVICE / SITE</th><th>JOB TYPE</th><th>STATUS</th><th>CURRENT STAGE</th><th>LAST HEARTBEAT</th><th>JOB</th></tr></thead><tbody>${sortJobs(jobs).map(job => row(job,groupName)).join('')}</tbody></table></div></section>`;
  }
  function renderGroups(jobs, contextJobs) {
    return buildGroups(jobs).map(group => {
      const definition = state.data.groups.find(g => g.name === group.name);
      const members = contextJobs.filter(j => group.name === null ? !j.groups.length : j.groups.includes(group.name));
      const active = state.data.jobs.filter(j => ACTIVE.has(j.status) && j.groups.includes(group.name)).length;
      const limit = definition?.max_concurrent ?? 1;
      const capacity = group.name === null ? 'These jobs have no saved upgrade group.' : `${active} active across batches · ${limit === 0 ? 'No concurrency limit' : `Up to ${limit} active at once`}${definition?.missing ? ' · Definition missing' : definition?.stale ? ' · Stale definition' : ''}`;
      const n = counts(members);
      return `<section class="upgrade-group"><header class="group-header"><div><div class="eyebrow">${group.name === null ? 'UNGROUPED JOBS' : 'UPGRADE GROUP'}</div><h3>${esc(group.name || 'Jobs without an upgrade group')}</h3><p>${esc(capacity)}</p></div><div class="group-total">${n.finished} / ${n.total} finished${progress(members,'Group completion')}</div></header><div class="operation-summary">${operationSummary(group.jobs)}</div><div class="group-waves">${group.batches.map(batch => {
        const description = displayText(batch.jobs.find(j => j.description)?.description);
        return `<section class="batch"><div class="batch-heading"><div><h4>${esc(description || 'Scheduled jobs')}</h4><p>Batch ${esc(batch.id.slice(0,8))} · ${esc(date(batch.jobs[0].scheduled_at))} · ${batch.jobs.length} shown</p></div></div>${batch.waves.map(w => wave(w, members.filter(j => j.batch_id === batch.id && (j.planned_wave || 1) === w.number), group.name)).join('')}</section>`;
      }).join('')}</div></section>`;
    }).join('');
  }
  function render() {
    if (!state.data) return;
    const data = state.data;
    $('demo').hidden = !data.demo;
    const batches = groupBy(data.jobs, j => j.batch_id);
    options('batch', [...batches].map(([id,jobs]) => [id, `${date(jobs[0].scheduled_at)} · ${id.slice(0,8)} · ${jobs.length} jobs`]), 'All tracked batches');
    options('operation', [...new Set(data.jobs.map(j => j.operation))].sort().map(op => [op, OPERATIONS[op] || title(op)]), 'All job types');
    options('site', [...new Set(data.jobs.map(j => j.site))].sort().map(site => [site,site]), 'All sites');
    const jobs = filterJobs(data.jobs, state.filters);
    const n = counts(jobs);
    $('percent').textContent = n.total ? `${Math.round(n.finished / n.total * 100)}%` : '—';
    $('finished').textContent = `${n.finished} of ${n.total} jobs finished`;
    $('running').textContent = n.running; $('waiting').textContent = n.waiting; $('issues').textContent = n.issues;
    $('scope').textContent = Object.values(state.filters).some(Boolean) ? 'Filtered view' : 'All tracked jobs';
    $('overall-bar').innerHTML = progress(jobs, 'Overall job completion');
    $('outcome-legend').innerHTML = outcomeLegend(jobs);
    $('coverage').textContent = `Read-only · Unfinished batches + up to ${data.recent_batches} recent batches from the latest 100 jobs`;
    const sections = splitJobs(jobs, data.jobs);
    const focusedJob = document.activeElement?.dataset.job;
    const liveHtml = renderGroups(sections.live, sections.live) || `<div class="empty">${data.updated_at ? (jobs.length ? 'No active or pending jobs in this view. Finished jobs are below.' : data.jobs.length ? 'No jobs match these filters.' : 'New scheduled jobs will appear automatically.') : 'Waiting for the first successful NetBox response…'}</div>`;
    const finishedHtml = renderGroups(sections.finished, sections.finished);
    if ($('live-plan').innerHTML !== liveHtml) $('live-plan').innerHTML = liveHtml;
    if ($('finished-plan').innerHTML !== finishedHtml) $('finished-plan').innerHTML = finishedHtml;
    if (focusedJob) document.querySelector(`[data-job="${Number(focusedJob)}"]`)?.focus({preventScroll:true});
    $('finished-jobs').hidden = !sections.finished.length;
    $('finished-count').textContent = sections.finished.length;
    const outcomes = [...groupBy(sections.finished, j => j.status)].map(([status,members]) => `${members.length} ${LABELS[status].toLowerCase()}`);
    $('finished-outcomes').textContent = outcomes.join(' · ');
    $('finished-outcomes').classList.toggle('warning-text', sections.finished.some(needsAttention));
    $('shared-note').hidden = !jobs.some(j => j.groups.length > 1);
    if ($('details').open) renderDetail();
    freshness();
  }
  function renderDetail() {
    const job = state.data.jobs.find(j => j.id === state.selectedJob);
    if (!job) { $('detail-content').textContent = 'This job is no longer in the tracked feed.'; return; }
    $('detail-title').textContent = `${job.device_name} · #${job.id}`;
    const dependencies = job.waits_for.map(dep => {
      const peers = state.data.jobs.filter(j => j.batch_id === job.batch_id && j.device_id === dep.id);
      return `${dep.name}: ${peers.length ? peers.map(j => LABELS[j.status] || title(j.status)).join(', ') : 'outside this batch (not a batch prerequisite)'}`;
    }).join('; ') || 'No device prerequisites recorded';
    const info = [['Status',LABELS[job.status] || title(job.status)],['Job type',operationLabel(job.operation)],['Scheduled',date(job.scheduled_at)],['Must start before',date(job.start_before)],['Started',date(job.started_at)],['Completed',date(job.completed_at)],['Worker',job.poller || '—'],['Worker check-in',date(job.poller_last_seen_at)]];
    $('detail-content').innerHTML = `<dl class="detail-grid">${info.map(([key,val]) => `<div><dt>${esc(key)}</dt><dd>${esc(val)}</dd></div>`).join('')}</dl><p class="detail-message">${esc(displayText(job.held_reason || job.message) || 'No stage message yet.')}</p><h3 class="detail-heading">Groups & dependencies</h3><p class="detail-group">${esc(job.groups.join(' · ') || 'No upgrade group')}</p><p>${esc(dependencies)}</p><p class="dialog-note">Group membership, dependencies and wave are saved with the job. Wave numbers belong to their batch. Group limits are current definitions.</p><h3 class="detail-heading">Latest events</h3>${[...job.events].reverse().map(event => `<div class="event"><small>${esc(date(event.timestamp))} · ${esc(displayText(title(event.stage)))}</small><div>${esc(displayText(event.message))}</div></div>`).join('') || '<p>No events reported yet.</p>'}${job.url ? `<a class="url-link" href="${esc(job.url)}" target="_blank" rel="noopener noreferrer">Open job in NetBox ↗</a>` : ''}`;
  }
  $('plan').addEventListener('click', event => { const button = event.target.closest('[data-job]'); if (!button) return; state.selectedJob = Number(button.dataset.job); renderDetail(); $('details').showModal(); });
  $('close-details').addEventListener('click', () => $('details').close());
  $('fullscreen').addEventListener('click', async () => {
    try { if (document.fullscreenElement) await document.exitFullscreen(); else await document.documentElement.requestFullscreen(); } catch { $('fullscreen').textContent = 'Use browser full screen'; }
  });
  document.addEventListener('fullscreenchange', () => { $('fullscreen').textContent = document.fullscreenElement ? '⛶ Exit full screen' : '⛶ Full screen'; });
  async function poll() {
    const controller = new AbortController(), timeout = setTimeout(() => controller.abort(), 10000);
    try {
      const response = await fetch('api/status', {signal:controller.signal, cache:'no-store'});
      if (!response.ok) throw new Error('HTTP error');
      const data = await response.json();
      if (!Array.isArray(data.jobs) || !Array.isArray(data.groups) || !data.server_time) throw new Error('Invalid response');
      state.data = data; state.lastReceived = Date.now(); state.networkError = ''; render();
    } catch {
      state.networkError = 'Cannot reach the tracker server.'; freshness();
    } finally {
      clearTimeout(timeout);
      setTimeout(poll, Math.max(2,state.data?.poll_seconds || 5) * 1000);
    }
  }
  setInterval(freshness,1000);
  poll();
}

const {test} = require('node:test');
const assert = require('node:assert/strict');
const {counts, sortJobs, filterJobs, needsAttention, progress, outcomeLegend, stepTiming, totalRuntime, esc, buildGroups, splitJobs, operationLabel, operationFamily, displayText} = require('../static/app.js');
const job = (id, extras={}) => ({id, batch_id:'batch', status:'pending', planned_wave:1, tier_rank:0, groups:[], scheduled_at:'2026-10-14T10:00:00Z', device_name:`switch-${id}`,site:'NYC',role:'Access',operation:'upgrade',...extras});

test('display order ignores invented tiers and puts running work before pending waves', () => {
  const jobs = [job(1,{planned_wave:2}), job(2,{tier_rank:2}), job(3,{groups:['B']}),job(4,{groups:['A']}),job(5,{planned_wave:1,tier_rank:1})];
  assert.deepEqual(sortJobs(jobs).map(j=>j.id),[2,3,4,5,1]);
  assert.deepEqual(sortJobs([job(1),job(2,{planned_wave:3,status:'running'})]).map(j=>j.id),[2,1]);
  assert.equal(jobs[0].id,1);
});
test('groups contain batches and saved waves; running groups and batches come first', () => {
  const jobs = [job(1,{groups:['A'],status:'pending'}),job(2,{groups:['Z'],planned_wave:3,status:'running',batch_id:'later'}),
    job(3,{groups:['Z'],planned_wave:1,status:'pending',batch_id:'later'}),job(4,{groups:['Z'],planned_wave:1,batch_id:'earlier'})];
  const groups = buildGroups(jobs);
  assert.deepEqual(groups.map(g=>g.name),['Z','A']);
  assert.deepEqual(groups[0].batches.map(b=>b.id),['later','earlier']);
  assert.deepEqual(groups[0].batches[0].waves.map(w=>w.number),[3,1]);
  assert.equal(groups[0].batches[1].waves[0].number,1);
});
test('shared membership is represented in each group but overview counts unique jobs', () => {
  const jobs = [job(1,{groups:['A','B','A']}),job(2)];
  const groups = buildGroups(jobs);
  assert.equal(groups.length,3);
  assert.equal(groups.find(g=>g.name==='A').jobs.length,1);
  assert.equal(groups.find(g=>g.name==='B').jobs[0].id,1);
  assert.equal(groups.find(g=>g.name===null).jobs[0].id,2);
  assert.equal(counts(jobs).total,2);
});
test('finished history excludes recovery and keeps every terminal outcome', () => {
  const jobs = ['running','claimed','pending','held','recovery_required','completed','completed_with_warnings','failed','cancelled','expired'].map((status,id)=>job(id,{status,batch_id:String(id)}));
  const sections = splitJobs(jobs);
  assert.equal(sections.live.length,5);
  assert.equal(sections.finished.length,5);
  assert.equal(sections.live.find(j=>j.status==='recovery_required').id,4);
});
test('software staging and standards terminology is distinct and searchable', () => {
  assert.equal(operationLabel('stage'),'Software staging');
  assert.equal(operationLabel('upgrade'),'Software upgrade');
  assert.equal(operationLabel('audit_config'),'Standards audit');
  assert.equal(operationLabel('remediate'),'Standards remediation');
  assert.equal(operationFamily('remediate'),'standards');
  assert.equal(displayText('Prestaging policy: prestage image'),'Software staging policy: Software staging image');
  const selected = filterJobs([job(1,{operation:'stage'}),job(2)],{batch:'',site:'',operation:'',search:'software staging',attention:false});
  assert.deepEqual(selected.map(j=>j.id),[1]);
});
test('recovery is unfinished; failed, expired, cancelled count as finished', () => {
  const jobs = ['completed','completed_with_warnings','failed','expired','cancelled','recovery_required','held','pending','running','claimed'].map((status,id)=>job(id,{status}));
  assert.deepEqual(counts(jobs),{total:10,finished:5,running:2,waiting:2,issues:5});
  assert.match(progress(jobs,'All jobs'),/5 of 10 finished/);
  assert.match(progress(jobs,'All jobs'),/2 in progress/);
});
test('overlapping groups do not double count jobs; filters use consistent scope', () => {
  const jobs = [job(1,{groups:['Pair A','Site A']}),job(2,{batch_id:'other',groups:['Pair A'],status:'held'})];
  const filtered = filterJobs(jobs,{batch:'batch',operation:'upgrade',site:'NYC',search:'pair a',attention:false});
  assert.deepEqual(filtered.map(j=>j.id),[1]);
  assert.equal(counts(filtered).total,1);
  assert.equal(filterJobs(jobs,{batch:'',operation:'',site:'',search:'',attention:true}).length,1);
});
test('stale workers need attention even while running', () => {
  assert.equal(needsAttention(job(1,{status:'running',heartbeat_stale:true})),true);
});
test('fully finished staging shows both successful and cancelled halves with filtered outcome counts', () => {
  const jobs = [job(1,{operation:'stage',status:'completed'}),job(2,{operation:'stage',status:'completed'}),
    job(3,{operation:'stage',status:'cancelled'}),job(4,{operation:'stage',status:'cancelled'}),job(5)];
  const selected = filterJobs(jobs,{operation:'stage',search:''});
  assert.equal(counts(selected).finished / counts(selected).total,1);
  const bar = progress(selected,'Staging');
  assert.match(bar,/segment-success" x="0" width="50"/);
  assert.match(bar,/segment-other" x="50" width="50"/);
  assert.match(outcomeLegend(selected),/2 successful/);
  assert.match(outcomeLegend(selected),/2 cancelled \/ expired/);
  assert.match(outcomeLegend(selected),/0 failed/);
});
test('API strings cannot inject markup and empty progress remains valid', () => {
  assert.equal(esc('<img src=x onerror="alert(1)">'), '&lt;img src=x onerror=&quot;alert(1)&quot;&gt;');
  assert.match(progress([], 'No jobs'), /0 of 0 finished/);
  assert.doesNotMatch(progress([], 'No jobs'), /NaN|Infinity/);
});

test('status breakdown represents unfinished jobs without increasing finished percentage', () => {
  const jobs = ['completed','running','claimed','pending','held','recovery_required','future_status'].map((status,id)=>job(id,{status}));
  const bar = progress(jobs,'All jobs');
  assert.equal(counts(jobs).finished,1);
  assert.match(bar,/role="img"/);
  assert.match(bar,/1 of 7 finished/);
  assert.match(outcomeLegend(jobs),/2 in progress/);
  assert.match(outcomeLegend(jobs),/2 waiting \/ held/);
  assert.match(outcomeLegend(jobs),/1 recovery required/);
  assert.match(outcomeLegend(jobs),/1 unknown status/);
  for (const kind of ['running','waiting','recovery','unknown']) assert.match(bar,new RegExp('segment-'+kind+'" x="[^" ]+" width="(?!0")'));
  const widths = [...bar.matchAll(/<rect[^>]* width="([^" ]+)"/g)].map(match=>Number(match[1]));
  assert.ok(Math.abs(widths.reduce((a,b)=>a+b,0)-100)<1e-9);
});

test('step timing uses reported phase duration, never job age or a fabricated timer', () => {
  assert.equal(stepTiming(job(1,{status:'running'})),'Step timing unavailable');
  assert.equal(stepTiming(job(1,{status:'completed',step_timing:{elapsed_seconds:60}})),'');
  assert.equal(stepTiming(job(1,{status:'running',step_timing:{name:'Reconnect',elapsed_seconds:3661}})),
    'Time in step (Reconnect): 1h 1m 1s · last reported');
  assert.match(stepTiming(job(1,{status:'claimed',step_timing:{elapsed_seconds:0}})),/0s/);
});

test('completed devices stay with all waves until the entire batch finishes, including across filters', () => {
  const jobs = [job(1,{status:'completed',planned_wave:1}),job(2,{status:'pending',planned_wave:2}),job(3,{batch_id:'done',status:'failed'})];
  assert.deepEqual(splitJobs(jobs).live.map(j=>j.id),[1,2]);
  assert.deepEqual(splitJobs(jobs).finished.map(j=>j.id),[3]);
  assert.deepEqual(splitJobs([jobs[0]],jobs).live.map(j=>j.id),[1]);
  jobs[1].status = 'recovery_required';
  assert.equal(splitJobs(jobs).live.length,2);
  jobs[1].status = 'completed';
  assert.equal(splitJobs(jobs).live.length,0);
  assert.equal(splitJobs(jobs).finished.length,3);
});
test('total runtime prefers poller elapsed time and freezes timestamp fallback on completion', () => {
  assert.equal(totalRuntime(job(1,{step_timing:{total_elapsed_seconds:3661}})), 'Total runtime: 1h 1m 1s · last reported');
  const started_at = '2026-10-15T00:00:00Z';
  assert.match(totalRuntime(job(1,{status:'running',started_at}),Date.parse(started_at)+90000),/1m 30s/);
  assert.match(totalRuntime(job(1,{status:'completed',started_at,completed_at:'2026-10-15T00:02:00Z'})),/2m 0s/);
  assert.equal(totalRuntime(job(1,{status:'completed',started_at})), 'Total runtime unavailable');
  assert.equal(totalRuntime(job(1)), 'Total runtime unavailable');
});

const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// A small DOM model lets the regression checks catch replaced/disconnected
// disclosures and focus without adding a browser package to the service.
function harness() {
  const listeners = new Map(), clipboard = [];
  let selectedNode = null;
  const document = {activeElement: null, addEventListener: (type, listener) => listeners.set(type, listener)};
  const navigator = {clipboard: {writeText: async text => { clipboard.push(text); }}};
  class Element {
    constructor(tag) {
      this.tagName = tag;
      this.children = [];
      this.parentNode = null;
      this.className = '';
      this.textContent = '';
      this.open = false;
      this.scrollTop = 0;
      this.scrollLeft = 0;
      this.value = '';
      this.listeners = new Map();
    }
    contains(node) { return this === node || this.children.some(child => child.contains(node)); }
    remove() {
      if (!this.parentNode) return;
      if (this.contains(document.activeElement)) document.activeElement = null;
      const siblings = this.parentNode.children;
      siblings.splice(siblings.indexOf(this), 1);
      this.parentNode = null;
    }
    insertBefore(node, next) {
      node.remove();
      const index = next === null ? this.children.length : this.children.indexOf(next);
      assert.ok(index >= 0);
      this.children.splice(index, 0, node);
      node.parentNode = this;
    }
    append(...nodes) { for (const node of nodes) this.insertBefore(node, null); }
    replaceChildren(...nodes) {
      for (const child of [...this.children]) child.remove();
      this.scrollTop = 0; this.scrollLeft = 0;
      this.append(...nodes);
    }
    get firstChild() { return this.children[0] || null; }
    querySelector(selector) {
      for (const child of this.children) {
        if (selector.startsWith('.') ? child.className.split(' ').includes(selector.slice(1)) : child.tagName === selector) return child;
        const found = child.querySelector(selector);
        if (found) return found;
      }
      return null;
    }
    addEventListener(type, listener) { this.listeners.set(type, listener); }
    trigger(type) { return this.listeners.get(type)?.({target: this}); }
    setAttribute(name, value) { this[name] = value; }
    focus() { document.activeElement = this; }
    select() { this.selectionStart = 0; this.selectionEnd = this.value.length; }
  }
  const ids = new Map();
  document.getElementById = id => {
    if (!ids.has(id)) ids.set(id, new Element('div'));
    return ids.get(id);
  };
  document.createElement = tag => new Element(tag);
  document.createTextNode = text => { const node = new Element('#text'); node.textContent = text; return node; };
  const context = vm.createContext({document, navigator, URLSearchParams, setInterval: () => {},
    fetch: () => new Promise(() => {}), window: {location: {assign() {}}, addEventListener() {},
      getSelection: () => ({isCollapsed: !selectedNode, rangeCount: selectedNode ? 1 : 0,
        getRangeAt: () => ({intersectsNode: node => node.contains(selectedNode)})})}});
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8'), context);
  const app = vm.runInContext('({state, renderRecords, loadRecords, renderTargets})', context);
  return {document, navigator, clipboard, app, container: document.getElementById('records'),
    select: node => { selectedNode = node; },
    pointer: (type, node) => listeners.get(type)?.({target: node}),
    getNode: document.getElementById,
    reply: items => { context.fetch = async () => ({ok: true, json: async () => ({items, next_before_id: 1})}); }};
}
const event = id => ({id, target: '192.0.2.80', started: 1792281600 + id,
  kind: 'baseline', before: null, after: {hops: {'1': ['192.0.2.1']}, destination_ttl: 1}});
const trace = id => ({id, target: '192.0.2.80', started: 1792281600 + id,
  hops: [{ttl: 1, addresses: ['192.0.2.1'], rtt_ms: [1.2]}], reached: true, duration_ms: 20, error: null});
const find = (node, tag) => node.tagName === tag ? node : node.children.map(child => find(child, tag)).find(Boolean);
const jsonSection = record => record.children[1].children.find(child => child.className === 'raw-json');
const target = (address = '192.0.2.80', name = 'Primary data center') => ({
  address, name, protocol: 'tcp', port: 443, fresh: true, missed_ticks: 0,
  state: {samples: 30, accepted: {}},
  latest: {started: 1792281600, duration_ms: 20, reached: true, error: null,
    hops: Array.from({length: 20}, (_, i) => ({ttl: i + 1, addresses: [`198.51.100.${i + 1}`], annotations: []}))},
});
const snapshot = (...targets) => ({targets, settings: {learning_samples: 30, interval_seconds: 1,
  trace_retention_hours: 24, event_retention_days: 90}});

for (const area of ['address', 'path-address']) test(`selected ${area} stays attached across polls, then resumes with fresh data`, () => {
  const {app, select, clipboard} = harness();
  const first = target(); first.latest.raw_output = 'original trace\n';
  app.renderTargets(snapshot(first, target('192.0.2.81', 'Other host')));
  const view = app.state.targets.get(first.address), selected = view.card.querySelector(`.${area}`);
  select(selected);
  for (let n = 0; n < 4; n++) {
    const fresh = target(); fresh.name = 'Renamed host'; fresh.latest.raw_output = 'new trace\n';
    fresh.latest.hops[0].addresses = ['203.0.113.10'];
    const other = target('192.0.2.81', `Other host ${n}`);
    app.renderTargets(snapshot(fresh, other));
    assert.equal(view.card.querySelector(`.${area}`), selected);
    assert.equal(view.latest.raw_output, 'original trace\n');
    assert.equal(view.selectionNote.hidden, false);
    assert.equal(app.state.targets.get(other.address).header.querySelector('h3').textContent, `Other host ${n}`);
  }
  assert.equal(clipboard.length, 0); // Selecting text never reads or changes the clipboard.
  select(null);
  const fresh = target(); fresh.latest.raw_output = 'new trace\n';
  fresh.latest.hops[0].addresses = ['203.0.113.10'];
  app.renderTargets(snapshot(fresh));
  assert.equal(view.selectionNote.hidden, true);
  assert.equal(view.latest.raw_output, 'new trace\n');
  assert.equal(view.hops.querySelector('.path-address').textContent, '203.0.113.10 ●');
});

test('a drag beginning before a selection exists also protects the panel', () => {
  const {app, pointer} = harness(); app.renderTargets(snapshot(target()));
  const view = app.state.targets.get('192.0.2.80'), address = view.header.querySelector('.address');
  pointer('pointerdown', address); app.renderTargets(snapshot(target()));
  assert.equal(view.header.querySelector('.address'), address);
  pointer('pointerup', address); app.renderTargets(snapshot(target()));
  assert.notEqual(view.header.querySelector('.address'), address);
});

test('copy uses the exact displayed command output and handles clipboard failure with selectable text', async () => {
  const {app, navigator, clipboard, document} = harness();
  const first = target(); first.latest.raw_output = 'traceroute to 192.0.2.80 (192.0.2.80), 30 hops max, 60 byte packets\n 1  192.0.2.1  1.200 ms * 198.51.100.1  3.400 ms\n';
  app.renderTargets(snapshot(first));
  const view = app.state.targets.get(first.address);
  assert.equal(view.copyButton.disabled, false);
  await view.copyButton.trigger('click');
  assert.equal(clipboard[0], first.latest.raw_output);
  assert.equal(view.copyStatus.textContent, 'Trace copied');
  navigator.clipboard.writeText = async () => { throw new Error('denied'); };
  await view.copyButton.trigger('click');
  assert.equal(view.fallback.hidden, false);
  assert.equal(view.copyText.value, first.latest.raw_output);
  assert.equal(view.copyText.selectionEnd, first.latest.raw_output.length);
  assert.equal(document.activeElement, view.copyText);
  assert.match(view.copyStatus.textContent, /Ctrl\+C/);
  const fresh = target(); fresh.latest.raw_output = 'new trace\n';
  app.renderTargets(snapshot(fresh));
  assert.equal(view.latest.raw_output, first.latest.raw_output); // Manual fallback remains stable too.
  await view.fallback.querySelector('button').trigger('click');
  app.renderTargets(snapshot(fresh));
  assert.equal(view.fallback.hidden, true);
  assert.equal(view.latest.raw_output, 'new trace\n');
  navigator.clipboard = undefined;
  await view.copyButton.trigger('click');
  assert.equal(view.copyText.value, 'new trace\n');
});

test('copy is disabled until original output is available and selected history stays on the live page', async () => {
  const {app, select, container, reply} = harness();
  app.renderTargets(snapshot(target()));
  assert.equal(app.state.targets.get('192.0.2.80').copyButton.disabled, true);
  app.state.rows = [event(1)]; app.renderRecords();
  const reading = container.children[0], ip = reading.querySelector('code');
  select(ip);
  app.renderTargets(snapshot(target('192.0.2.80', 'Renamed')));
  assert.equal(reading.querySelector('.record-target-name').textContent, 'Primary data center');
  reply([event(2)]); await app.loadRecords();
  assert.equal(container.children[1], reading);
  select(null); await app.loadRecords();
  assert.equal(reading.parentNode, null);
});

for (const mode of ['events', 'traces']) test(`${mode}: configured names precede IPs and update cached history without closing JSON`, () => {
  const {app, container} = harness();
  app.state.mode = mode;
  app.renderTargets(snapshot(target()));
  const row = mode === 'events' ? event : trace;
  app.state.rows = [row(2), {...row(1), target: '192.0.2.81'}];
  app.renderRecords();
  const record = container.children[0], summary = record.children[0];
  const label = summary.querySelector('.record-target-name'), ip = summary.querySelector('code');
  assert.equal(label.textContent, 'Primary data center');
  assert.equal(label.hidden, false);
  assert.ok(summary.children.indexOf(label) < summary.children.indexOf(ip));
  assert.equal(ip.textContent, '192.0.2.80');
  assert.equal(container.children[1].querySelector('.record-target-name').hidden, true);
  record.open = true;
  const raw = jsonSection(record); raw.open = true; find(raw, 'pre').scrollTop = 90;
  app.state.paged = true;
  app.renderTargets(snapshot(target('192.0.2.80', 'Renamed <office>')));
  assert.equal(container.children[0], record);
  assert.equal(label.textContent, 'Renamed <office>');
  assert.equal(label.children.length, 0); // Names are text, never HTML.
  assert.equal(record.open, true);
  assert.equal(raw.open, true);
  assert.equal(find(raw, 'pre').scrollTop, 90);
  app.renderTargets(snapshot(target('192.0.2.80', '192.0.2.80')));
  assert.equal(label.hidden, true); // Avoid duplicating an unnamed target's IP.
  app.renderTargets(snapshot());
  assert.equal(label.hidden, true);
  assert.equal(ip.textContent, '192.0.2.80');
});

test('live panels preserve independent scroll positions and focus while displaying fresh observations', () => {
  const {app, getNode, document} = harness();
  getNode('target').value = '192.0.2.81';
  app.renderTargets(snapshot(target(), target('192.0.2.81', 'Remote office')));
  const first = app.state.targets.get('192.0.2.80'), second = app.state.targets.get('192.0.2.81');
  first.hops.scrollTop = 155; first.hops.scrollLeft = 12;
  second.hops.scrollTop = 265; second.hops.scrollLeft = 24; second.hops.focus();
  for (let n = 0; n < 5; n++) {
    const fresh = target(); fresh.latest.hops[0].addresses = [`203.0.113.${n + 1}`];
    app.renderTargets(snapshot(fresh, target('192.0.2.81', 'Remote office')));
    assert.equal(app.state.targets.get('192.0.2.80'), first);
    assert.equal(getNode('targets').children[0], first.card);
    assert.equal(getNode('targets').children[1], second.card);
    assert.equal(first.hops.parentNode, first.card);
    assert.equal(first.hops.scrollTop, 155);
    assert.equal(first.hops.scrollLeft, 12);
    assert.equal(second.hops.scrollTop, 265);
    assert.equal(second.hops.scrollLeft, 24);
    assert.equal(document.activeElement, second.hops);
    assert.equal(getNode('target').value, '192.0.2.81');
    assert.equal(first.hops.querySelector('.path-address').textContent, `203.0.113.${n + 1} ●`);
  }
  const failed = target(); failed.latest.error = 'Probe failed';
  app.renderTargets(snapshot(failed, target('192.0.2.81', 'Remote office')));
  assert.equal(first.error.hidden, false);
  assert.equal(first.error.textContent, 'Probe failed');
  app.renderTargets(snapshot(target()));
  assert.equal(first.error.hidden, true);
  assert.equal(second.card.parentNode, null);
  assert.equal(app.state.targets.size, 1);
});

test('adding or removing another live target does not replace the panel being read', () => {
  const {app, getNode, document} = harness();
  app.renderTargets(snapshot(target('192.0.2.81', 'Remote office')));
  const reading = app.state.targets.get('192.0.2.81');
  reading.hops.scrollTop = 185; reading.hops.focus();
  app.renderTargets(snapshot(target(), target('192.0.2.81', 'Remote office')));
  assert.equal(getNode('targets').children[1], reading.card);
  assert.equal(reading.hops.scrollTop, 185);
  assert.equal(document.activeElement, reading.hops);
  app.renderTargets(snapshot(target('192.0.2.81', 'Remote office')));
  assert.equal(getNode('targets').children[0], reading.card);
  assert.equal(reading.hops.scrollTop, 185);
  assert.equal(document.activeElement, reading.hops);
});

for (const mode of ['events', 'traces']) test(`${mode}: nested JSON, scroll and focus survive polling and prepended rows`, () => {
  const {app, container, document} = harness();
  const row = mode === 'events' ? event : trace;
  app.state.mode = mode;
  app.state.rows = [row(2), row(1)];
  app.renderRecords();
  const record = container.children[0], json = jsonSection(record), pre = find(json, 'pre');
  record.open = true;
  json.open = true;
  pre.scrollTop = 123;
  json.children[0].focus();
  for (let n = 0; n < 5; n++) {
    app.state.rows = JSON.parse(JSON.stringify([row(2), row(1)]));
    app.renderRecords();
    assert.equal(container.children[0], record);
    assert.equal(jsonSection(record), json);
    assert.equal(record.open, true);
    assert.equal(json.open, true);
    assert.equal(pre.scrollTop, 123);
    assert.equal(document.activeElement, json.children[0]);
  }
  app.state.rows.unshift(row(3));
  app.renderRecords();
  assert.equal(container.children[1], record);
  assert.equal(json.open, true);
  assert.equal(document.activeElement, json.children[0]);
  json.open = false;
  app.renderRecords();
  assert.equal(jsonSection(record).open, false);
});

test('an expanded row stays available after leaving the live page, and is released after closing', async () => {
  const {app, container, reply} = harness();
  app.state.rows = [trace(1)]; app.state.mode = 'traces'; app.renderRecords();
  const reading = container.children[0]; reading.open = true; jsonSection(reading).open = true;
  reply(Array.from({length: 100}, (_, i) => trace(101 - i)));
  await app.loadRecords();
  assert.equal(app.state.rows.length, 101);
  assert.equal(app.state.paged, false);
  assert.equal(container.children[100], reading);
  assert.equal(jsonSection(reading).open, true);
  // Polling can continue with a pinned record, even when the DOM has >100 rows.
  reply(Array.from({length: 100}, (_, i) => trace(102 - i)));
  await app.loadRecords();
  assert.equal(app.state.rows[0].id, 102);
  assert.equal(container.children[100], reading);
  reading.open = false;
  await app.loadRecords();
  assert.equal(app.state.rows.length, 100);
  assert.equal(reading.parentNode, null);
  assert.equal(app.state.records.size, 100);
});

test('loading older pages deduplicates pinned rows and preserves open disclosures', async () => {
  const {app, container, reply} = harness();
  app.state.rows = [event(5), event(1)]; app.renderRecords();
  const reading = container.children[1]; reading.open = true; jsonSection(reading).open = true;
  reply([event(4), event(3), event(2), event(1)]);
  await app.loadRecords(true);
  assert.equal(app.state.paged, true);
  assert.deepEqual(Array.from(app.state.rows, row => row.id), [5, 4, 3, 2, 1]);
  assert.equal(container.children[4], reading);
  assert.equal(jsonSection(reading).open, true);
});

test('removed rows are released without disconnecting a surviving focused entry', () => {
  const {app, container, document} = harness();
  app.state.rows = [event(2), event(1)]; app.renderRecords();
  const reading = container.children[1]; reading.open = true; jsonSection(reading).children[0].focus();
  app.state.rows = [event(1)]; app.renderRecords();
  assert.equal(container.children[0], reading);
  assert.equal(document.activeElement, jsonSection(reading).children[0]);
  assert.equal(app.state.records.size, 1);
  app.state.rows = []; app.renderRecords();
  assert.equal(app.state.records.size, 0);
  assert.equal(container.children.length, 1);
  assert.equal(container.children[0].className, 'empty');
});

test('different history types and reused database IDs do not share expansion state', () => {
  const {app, container} = harness();
  app.state.rows = [event(1)]; app.renderRecords();
  const previous = container.children[0]; previous.open = true;
  app.state.mode = 'traces'; app.state.rows = [trace(1)]; app.renderRecords();
  assert.notEqual(container.children[0], previous);
  assert.equal(container.children[0].open, false);
  const traceRecord = container.children[0]; traceRecord.open = true;
  app.state.rows = [{...trace(1), started: 1800000000}]; app.renderRecords();
  assert.notEqual(container.children[0], traceRecord);
  assert.equal(container.children[0].open, false);
});

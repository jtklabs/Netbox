const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// A small DOM model lets the regression checks catch replaced/disconnected
// disclosures and focus without adding a browser package to the service.
function harness() {
  const document = {activeElement: null};
  class Element {
    constructor(tag) {
      this.tagName = tag;
      this.children = [];
      this.parentNode = null;
      this.className = '';
      this.textContent = '';
      this.open = false;
      this.scrollTop = 0;
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
    replaceChildren(...nodes) { for (const child of [...this.children]) child.remove(); this.append(...nodes); }
    addEventListener() {}
    setAttribute(name, value) { this[name] = value; }
    focus() { document.activeElement = this; }
  }
  const ids = new Map();
  document.getElementById = id => {
    if (!ids.has(id)) ids.set(id, new Element('div'));
    return ids.get(id);
  };
  document.createElement = tag => new Element(tag);
  const context = vm.createContext({document, URLSearchParams, setInterval: () => {},
    fetch: () => new Promise(() => {}), window: {location: {assign() {}}}});
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8'), context);
  const app = vm.runInContext('({state, renderRecords, loadRecords})', context);
  return {document, app, container: document.getElementById('records'),
    reply: items => { context.fetch = async () => ({ok: true, json: async () => ({items, next_before_id: 1})}); }};
}
const event = id => ({id, target: '192.0.2.80', started: 1792281600 + id,
  kind: 'baseline', before: null, after: {hops: {'1': ['192.0.2.1']}, destination_ttl: 1}});
const trace = id => ({id, target: '192.0.2.80', started: 1792281600 + id,
  hops: [{ttl: 1, addresses: ['192.0.2.1'], rtt_ms: [1.2]}], reached: true, duration_ms: 20, error: null});
const find = (node, tag) => node.tagName === tag ? node : node.children.map(child => find(child, tag)).find(Boolean);
const jsonSection = record => record.children[1].children.find(child => child.className === 'raw-json');

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

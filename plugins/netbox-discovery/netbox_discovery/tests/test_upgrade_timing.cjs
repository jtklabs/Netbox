const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const {test} = require('node:test');
const {runInNewContext} = require('node:vm');

const script = readFileSync(join(__dirname, '../static/netbox_discovery/upgrade_timing.js'), 'utf8');

function setup({browserZone = 'America/Chicago', selected = 'America/New_York',
                auto = true, startValue = '', endValue = '', instant = '2030-07-08T14:30:45Z',
                failDetection = false, enhanced = false} = {}) {
  const events = [];
  const zone = {value: selected, options: ['America/New_York', 'America/Chicago', 'UTC'].map(value => ({value})),
    dispatchEvent: event => events.push(['zone', event.type])};
  if (enhanced) zone.tomselect = {setValue: value => {zone.value = value;}};
  const start = {value: startValue, dispatchEvent: event => events.push(['start', event.type])};
  const end = {value: endValue};
  let click;
  const button = {addEventListener: (type, handler) => {assert.equal(type, 'click'); click = handler;}};
  const elements = {'[name="time_zone"]': zone, '[name="scheduled_at"]': start,
    '[name="start_before"]': end, '[data-schedule-now]': button};
  const form = {dataset: {browserTimeZone: String(auto)}, querySelector: selector => elements[selector]};
  const document = {readyState: 'complete', querySelectorAll: () => [form]};
  const browserIntl = {DateTimeFormat: function (locale, options) {
    if (options) return new Intl.DateTimeFormat(locale, options);
    if (failDetection) throw new Error('Unavailable');
    return {resolvedOptions: () => ({timeZone: browserZone})};
  }};
  class Clock extends Date { constructor() { super(instant); } }
  runInNewContext(script, {document, Intl: browserIntl, Date: Clock, Event});
  return {zone, start, end, events, click};
}

test('new forms use browser time zone, including enhanced selectors', () => {
  for (const enhanced of [false, true]) assert.equal(setup({enhanced}).zone.value, 'America/Chicago');
});
test('unavailable or unsupported browser zones keep New York', () => {
  assert.equal(setup({failDetection: true}).zone.value, 'America/New_York');
  assert.equal(setup({browserZone: 'Unknown/Zone'}).zone.value, 'America/New_York');
});
test('preview, errors and prefilled timing keep their selected zone and values', () => {
  for (const values of [{auto: false}, {startValue: '2030-08-01T10:00'}, {endValue: '2030-08-01T14:00'}]) {
    const form = setup({selected: 'UTC', ...values});
    assert.equal(form.zone.value, 'UTC');
    assert.equal(form.start.value, values.startValue || '');
    assert.equal(form.end.value, values.endValue || '');
  }
});
test('Now uses the selected zone, including daylight saving and midnight', () => {
  for (const [instant, selected, expected] of [
    ['2030-07-08T14:30:45Z', 'America/New_York', '2030-07-08T10:30:45'],
    ['2030-01-08T14:30:45Z', 'America/New_York', '2030-01-08T09:30:45'],
    ['2030-07-08T04:00:00Z', 'America/New_York', '2030-07-08T00:00:00'],
    ['2030-07-08T00:10:00Z', 'America/Chicago', '2030-07-07T19:10:00'],
    ['2030-07-08T14:30:45Z', 'UTC', '2030-07-08T14:30:45'],
  ]) {
    const form = setup({auto: false, instant, selected, endValue: '2030-07-09T18:00'});
    form.click();
    assert.equal(form.start.value, expected);
    assert.equal(form.end.value, '2030-07-09T18:00');
    assert.deepEqual(form.events, [['start', 'input'], ['start', 'change']]);
  }
});
test('Now honors a zone changed by the user after initialization', () => {
  const form = setup();
  form.zone.value = 'UTC';
  form.click();
  assert.equal(form.start.value, '2030-07-08T14:30:45');
});

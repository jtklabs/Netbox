"use strict";
const $ = (id) => document.getElementById(id);
const state = {mode: "events", rows: [], cursor: null, limit: 100, records: new Map(), targets: new Map(), targetNames: new Map(), pointerTarget: null, paged: false, busy: false, revision: 0, filters: new URLSearchParams()};
function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}
const date = (seconds) => new Date(seconds * 1000).toLocaleString();
function badge(text, style = "") { return el("span", text, `badge ${style}`); }
async function fetchJSON(url) {
  const result = await fetch(url, {cache: "no-store"});
  if (result.status === 401) {
    window.location.assign("login");
    throw new Error("Session expired. Sign in again.");
  }
  if (!result.ok) {
    const body = await result.json().catch(() => ({}));
    throw new Error(body.error || `HTTP ${result.status}`);
  }
  return result.json();
}
function pathTable(before, after, labels = ["Before", "After"]) {
  const table = el("table", undefined, "path-table");
  const head = el("tr");
  ["Hop", ...labels].forEach((text) => head.append(el("th", text)));
  const thead = el("thead"); thead.append(head); table.append(thead);
  const body = el("tbody");
  const keys = [...new Set([...Object.keys(before?.hops || {}), ...Object.keys(after?.hops || {})])].sort((a,b) => Number(a)-Number(b));
  for (const ttl of keys) {
    const previous = before?.hops?.[ttl] || [], next = after?.hops?.[ttl] || [];
    const row = el("tr", undefined, JSON.stringify(previous) === JSON.stringify(next) ? "" : "changed");
    row.append(el("td", ttl), el("td", previous.join(" / ") || "—"), el("td", next.join(" / ") || "—"));
    body.append(row);
  }
  table.append(body); return table;
}
function observedPath(observation) {
  return {hops: Object.fromEntries((observation?.hops || []).map((hop) => [hop.ttl, hop.addresses]))};
}
const recordKey = (row) => `${state.mode}:${row.id}:${row.started}`;
function selectionIn(node) {
  if (state.pointerTarget && node.contains(state.pointerTarget)) return true;
  const active = document.activeElement;
  if (active && node.contains(active) && typeof active.selectionStart === "number" && active.selectionStart !== active.selectionEnd) return true;
  const selection = window.getSelection();
  if (!selection || selection.isCollapsed) return false;
  for (let i = 0; i < selection.rangeCount; i++) if (selection.getRangeAt(i).intersectsNode(node)) return true;
  return false;
}
async function copyTrace(view) {
  const text = view.latest?.raw_output;
  if (!text) return;
  view.copying = true;
  view.copyButton.disabled = true;
  try {
    await navigator.clipboard.writeText(text);
    view.copyStatus.textContent = "Trace copied";
    view.fallback.hidden = true;
  } catch {
    view.fallback.hidden = false;
    view.copyText.value = text;
    view.copyText.focus();
    view.copyText.select();
    view.copyStatus.textContent = "Press Ctrl+C / ⌘C to copy the selected trace.";
  } finally {
    view.copying = false;
    view.copyButton.disabled = !view.latest?.raw_output;
  }
}
function updateRecordName(record, address) {
  if (selectionIn(record)) return;
  const label = record.querySelector(".record-target-name");
  const name = state.targetNames.get(address);
  const text = name && name !== address ? name : "";
  if (label.textContent !== text) label.textContent = text;
  label.hidden = !label.textContent;
}
function renderTargets(snapshot) {
  state.targetNames = new Map(snapshot.targets.map((target) => [target.address, target.name]));
  // Refresh labels even while browsing older pages without refetching history.
  for (const row of state.rows) {
    const record = state.records.get(recordKey(row));
    if (record) updateRecordName(record, row.target);
  }
  const selected = $("target").value;
  $("target").replaceChildren(el("option", "All destinations"));
  $("target").firstChild.value = "";
  const container = $("targets"), active = new Set(snapshot.targets.map((target) => target.address));
  for (const [address, view] of state.targets) {
    if (!active.has(address) && !selectionIn(view.card)) {
      view.card.remove();
      state.targets.delete(address);
    }
  }
  snapshot.targets.forEach((target, index) => {
    const option = el("option", `${target.name} · ${target.address}`); option.value = target.address;
    $("target").append(option);
    let view = state.targets.get(target.address);
    if (!view) {
      const card = el("article", undefined, "target-card"), header = el("div", undefined, "card-head");
      const hops = el("div", undefined, "hop-list"), error = el("p", undefined, "error-text"), meta = el("div", undefined, "card-meta");
      const actions = el("div", undefined, "card-actions"), copyButton = el("button", "Copy traceroute", "secondary");
      const copyStatus = el("span", undefined, "copy-status"), selectionNote = el("span", "Updates paused while selecting text", "selection-note");
      copyButton.type = "button";
      copyStatus.setAttribute("role", "status");
      selectionNote.hidden = true;
      actions.append(copyButton, copyStatus, selectionNote);
      const fallback = el("div", undefined, "trace-copy-fallback"), copyText = el("textarea"), closeText = el("button", "Close trace text", "secondary");
      copyText.readOnly = true;
      copyText.rows = 8;
      closeText.type = "button";
      closeText.addEventListener("click", () => { fallback.hidden = true; copyText.value = ""; copyStatus.textContent = ""; copyButton.focus(); });
      fallback.append(copyText, closeText);
      fallback.hidden = true;
      hops.tabIndex = 0;
      hops.setAttribute("role", "region");
      card.append(header, actions, hops, error, meta, fallback);
      view = {card, header, hops, error, meta, copyButton, copyStatus, selectionNote, fallback, copyText, latest: null, copying: false};
      copyButton.addEventListener("click", () => copyTrace(view));
      state.targets.set(target.address, view);
    }
    view.selectionNote.hidden = !selectionIn(view.card);
    if (!view.selectionNote.hidden) return;
    const {card, header, hops, error, meta} = view;
    const title = el("div"); title.append(el("h3", target.name), el("p", target.address, "address"));
    const latest = target.latest, settings = snapshot.settings, learned = (target.state?.samples || 0) >= settings.learning_samples;
    view.latest = latest;
    view.copyButton.disabled = !latest?.raw_output || view.copying;
    view.copyButton.title = latest?.raw_output ? "Copy the latest displayed traceroute output" : "Waiting for original output from a new trace";
    view.copyButton.setAttribute("aria-label", `Copy traceroute for ${target.name}`);
    view.copyText.setAttribute("aria-label", `Traceroute output for ${target.name}`);
    let status = !target.fresh ? "Stale / starting" : latest?.error ? "Probe error" : !learned ? "Learning" : latest.reached ? "Reached" : "No destination reply";
    header.replaceChildren(title, badge(status, !target.fresh || latest?.error ? "error" : !learned || !latest?.reached ? "warn" : ""));
    // Keep the scroll container attached and restore its position after updating
    // the hop rows. Each destination keeps an independent scroll position.
    const scrollTop = hops.scrollTop, scrollLeft = hops.scrollLeft;
    hops.setAttribute("aria-label", `${target.name} live traceroute`);
    hops.replaceChildren();
    for (const hop of latest?.hops || []) {
      const row = el("div", undefined, "hop"); row.append(el("span", hop.ttl, "ttl"));
      const ips = el("div");
      const known = Object.keys(target.state?.accepted?.[String(hop.ttl)] || {});
      const all = [...new Set([...known, ...hop.addresses])].sort();
      for (const ip of all) ips.append(el("span", `${ip}${hop.addresses.includes(ip) ? " ●" : ""}`, `path-address${known.includes(ip) ? "" : " unseen"}`));
      if (!hop.addresses.length) ips.append(el("span", "* no reply", "muted"));
      if (hop.annotations.length) ips.append(el("span", ` ${hop.annotations.join(" ")}`, "unseen"));
      row.append(ips); hops.append(row);
    }
    if (!latest?.hops?.length) hops.append(el("p", "Waiting for hop observations…", "muted"));
    error.textContent = latest?.error || "";
    error.hidden = !latest?.error;
    meta.replaceChildren();
    meta.append(el("span", target.protocol === "tcp" ? `TCP :${target.port ?? 443}` : "ICMP"));
    meta.append(el("span", latest ? `Last trace ${date(latest.started)}` : "No traces yet"), el("span", latest ? `${Math.round(latest.duration_ms)} ms` : ""), el("span", `${target.missed_ticks} missed slots`));
    if (!learned) meta.append(el("span", `Learning ${target.state?.samples || 0}/${settings.learning_samples}`));
    if (container.children[index] !== card) container.insertBefore(card, container.children[index] || null);
    hops.scrollTop = scrollTop;
    hops.scrollLeft = scrollLeft;
  });
  $("target").value = selected;
  $("target-count").textContent = `${snapshot.targets.length} configured · ● observed in latest trace`;
  $("cadence").replaceChildren(document.createTextNode(`${snapshot.settings.interval_seconds}s`), el("span", "trace start interval"));
  $("retention").textContent = `Raw traces: ${snapshot.settings.trace_retention_hours}h · Events: ${snapshot.settings.event_retention_days} days`;
}
function createRecord(row) {
  const details = el("details", undefined, "record");
  const summary = el("summary"); summary.append(el("time", date(row.started)));
  const kind = row.kind || (row.error ? "Probe error" : row.reached ? "Reached" : "No destination reply");
  summary.append(badge(kind.replaceAll("_", " "), row.kind === "path_change" || row.error ? "warn" : ""), el("span", undefined, "record-target-name"), el("code", row.target)); details.append(summary);
  updateRecordName(details, row.target);
  const body = el("div", undefined, "record-body");
  if (row.kind === "path_change" || row.kind === "baseline") {
    for (const move of row.hop_moves || []) {
      body.append(el("p", `${move.address} moved from hop ${move.from_hops.join(" / ")} to hop ${move.to_hops.join(" / ")} · first observed ${date(move.first_observed_at)}`));
    }
    body.append(pathTable(row.before, row.after));
    body.append(el("p", `Destination hop: ${row.before?.destination_ttl ?? "unknown"} → ${row.after?.destination_ttl ?? "unknown"}`, "muted"));
    body.append(el("p", "Accepted sets include alternatives retained during the multipath grace period. The confirming trace and evidence window are below.", "muted"));
  } else if (row.kind === "reachability") {
    body.append(el("p", `${row.before || "Unknown"} → ${row.after}. No reply can mean filtering or rate limiting.`));
    if (row.first_observed_at != null) body.append(el("p", `First observed ${date(row.first_observed_at)} · confirmed ${date(row.started)}`, "muted"));
    if (row.no_reply_since != null) body.append(el("p", `Confirmed loss-of-reply interval began with the trace at ${date(row.no_reply_since)}. Collection gaps may fall within this interval.`, "muted"));
    if (row.last_reached_observation) {
      body.append(el("p", `Last destination reply before this transition: ${date(row.last_reached_observation.started)}`, "muted"));
      body.append(pathTable(observedPath(row.last_reached_observation), observedPath(row.observed), ["Last destination reply", "At event"]));
    }
    if (row.last_responding_hop) body.append(el("p", `Farthest responding hop in this trace: ${row.last_responding_hop.ttl} · ${row.last_responding_hop.addresses.join(" / ")}`, "muted"));
    body.append(el("p", "A blank hop is unknown, not proof of the failure location. Raw observations continue while the destination is not replying.", "muted"));
  } else {
    const path = {hops: Object.fromEntries(row.hops.map((h) => [h.ttl, h.addresses]))};
    body.append(pathTable(null, path));
    body.append(el("p", `${row.duration_ms} ms${row.error ? ` · ${row.error}` : ""}`, "muted"));
  }
  const raw = el("details"), rawSummary = el("summary", "Observation & evidence JSON");
  raw.className = "raw-json";
  raw.append(rawSummary, el("pre", JSON.stringify(row, null, 2)));
  body.append(raw); details.append(body);
  return details;
}
function renderRecords() {
  const container = $("records"), active = new Set(state.rows.map(recordKey));
  for (const [key, record] of state.records) {
    if (!active.has(key)) {
      record.remove();
      state.records.delete(key);
    }
  }
  for (const child of [...container.children]) if (child.className === "empty") child.remove();
  state.rows.forEach((row, index) => {
    // History rows are immutable. Keep their DOM so nested disclosures, focus
    // and JSON scroll positions survive polling and newly prepended records.
    const key = recordKey(row);
    let record = state.records.get(key);
    if (!record) {
      record = createRecord(row);
      state.records.set(key, record);
    }
    if (container.children[index] !== record) container.insertBefore(record, container.children[index] || null);
  });
  if (!state.rows.length) container.append(el("p", state.mode === "events" ? "No change events in this range. Baselines appear after learning completes." : "No traces in this range.", "empty"));
  $("older").hidden = state.cursor === null;
}
async function loadRecords(older = false) {
  const revision = state.revision;
  const query = new URLSearchParams(state.filters); query.set("limit", state.limit);
  if (older && state.cursor) query.set("before_id", state.cursor);
  const data = await fetchJSON(`api/${state.mode}?${query}`);
  if (revision !== state.revision) return;
  if (older) {
    state.paged = true;
    state.rows = [...new Map([...state.rows, ...data.items].map((row) => [recordKey(row), row])).values()]
      .sort((a, b) => b.id - a.id || b.started - a.started);
  } else {
    const incoming = new Set(data.items.map(recordKey));
    const reading = state.rows.filter((row) => {
      const record = state.records.get(recordKey(row));
      return !incoming.has(recordKey(row)) && record && (record.open || selectionIn(record));
    });
    state.rows = data.items.concat(reading);
  }
  state.cursor = data.next_before_id; renderRecords();
}
function applyFilters() {
  const query = new URLSearchParams();
  if ($("target").value) query.set("target", $("target").value);
  for (const field of ["since", "until"]) if ($(field).value) query.set(field, new Date($(field).value).getTime() / 1000);
  state.filters = query; state.limit = 100;
  const exportQuery = new URLSearchParams(query); exportQuery.set("limit", 1000);
  $("export").href = `api/events.csv?${exportQuery}`;
}
async function refresh(older = false) {
  if (state.busy) return;
  state.busy = true;
  try {
    const snapshot = await fetchJSON("api/status");
    renderTargets(snapshot);
    // Preserve paged results until filters change; auto-refresh the first page.
    if (older || !state.paged) await loadRecords(older);
    $("connection").textContent = `${snapshot.healthy ? "Collecting" : "Needs attention"} · ${date(snapshot.now)}`;
    const messages = [];
    if (snapshot.demo) messages.push("SIMULATED DATA — demo probes only; no network traffic.");
    if (snapshot.collector_error) messages.push(`Collector stopped: ${snapshot.collector_error}`);
    if (!snapshot.healthy) messages.push("Some collection results are stale, unavailable, or have probe errors. See destination cards.");
    $("banner").textContent = messages.join(" "); $("banner").hidden = !messages.length;
  } catch (error) {
    $("connection").textContent = "Disconnected";
    $("banner").textContent = `Could not refresh: ${error.message}. Displayed data may be stale.`;
    $("banner").hidden = false;
  } finally { state.busy = false; }
}
function resetHistory() { state.revision += 1; state.rows = []; state.cursor = null; state.paged = false; state.records.clear(); $("records").replaceChildren(); refresh(); }
$("filters").addEventListener("submit", (event) => { event.preventDefault(); applyFilters(); resetHistory(); });
$("reset").addEventListener("click", () => { $("filters").reset(); applyFilters(); resetHistory(); });
$("older").addEventListener("click", () => refresh(true));
for (const mode of ["events", "traces"]) $(mode + "-tab").addEventListener("click", () => {
  state.mode = mode;
  for (const tab of ["events", "traces"]) $(tab + "-tab").setAttribute("aria-pressed", String(mode === tab));
  $("history-note").textContent = mode === "events" ? "Expand an event to compare the accepted path before and after." : "Every trace is retained, including timeouts, partial replies and execution errors.";
  resetHistory();
});
document.addEventListener("pointerdown", (event) => { state.pointerTarget = event.target; });
for (const event of ["pointerup", "pointercancel"]) document.addEventListener(event, () => { state.pointerTarget = null; });
window.addEventListener("blur", () => { state.pointerTarget = null; });
refresh(); setInterval(() => refresh(), 2000);

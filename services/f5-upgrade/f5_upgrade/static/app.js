"use strict";
const $ = (id) => document.getElementById(id);
let token = "",
  selected = null,
  polling = false,
  pendingBody = "",
  pendingKey = "";
const bools = [
  "transfer",
  "install",
  "reboot",
  "create_ucs",
  "ucs_unencrypted",
  "images_on_device",
  "drain",
  "reboot_on_drain_timeout",
];
const numbers = [
  "drain_connections",
  "drain_percent",
  "drain_timeout",
  "install_timeout",
  "reboot_timeout",
  "validation_timeout",
];
function message(text, error = false) {
  $("message").textContent = text;
  $("message").className = error ? "error" : "";
}
async function api(path, method = "GET", body, key) {
  const headers = {
    Authorization: `Bearer ${token}`,
    "Content-Type": "application/json",
  };
  if (key) headers["Idempotency-Key"] = key;
  const r = await fetch("/api/v1/" + path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!r.ok) {
    const error = await r.json();
    throw new Error(JSON.stringify(error.detail));
  }
  return r.json();
}
function request() {
  const r = {
    device: $("device").value,
    images: JSON.parse($("images").value),
    allowed_source_versions: $("allowed_source_versions")
      .value.split(",")
      .map((x) => x.trim())
      .filter(Boolean),
  };
  bools.forEach((k) => (r[k] = $(k).checked));
  numbers.forEach((k) => {
    if ($(k).value !== "") r[k] = Number($(k).value);
  });
  [
    "target_volume",
    "expected_version",
    "expected_build",
    "backup_directory",
  ].forEach((k) => {
    if ($(k).value.trim()) r[k] = $(k).value.trim();
  });
  return r;
}
async function refresh() {
  if (!token || polling) return;
  polling = true;
  try {
    const jobs = await api("jobs");
    $("jobs").replaceChildren();
    if (!jobs.length)
      $("jobs").textContent =
        "No jobs yet. Preview a job to see its inherited steps.";
    jobs.forEach((j) => {
      const b = document.createElement("button");
      b.className = "job";
      const title = document.createElement("strong");
      title.textContent = `${j.device} · ${j.kind}`;
      const detail = document.createElement("span");
      detail.className = "status";
      detail.textContent = `${j.status.replaceAll("_", " ")} · ${new Date(j.created * 1000).toLocaleString()} · ${j.id.slice(0, 8)}`;
      b.append(title, detail);
      b.onclick = () => select(j.id).catch((e) => message(e.message, true));
      $("jobs").append(b);
    });
    if (selected) await select(selected);
  } catch (e) {
    message(e.message, true);
  } finally {
    polling = false;
  }
}
async function select(id) {
  selected = id;
  const j = await api("jobs/" + id),
    events = await api("jobs/" + id + "/events?limit=1000");
  $("detail").hidden = false;
  $("detail-title").textContent = j.device + " · " + j.kind;
  $("detail-status").textContent =
    j.status +
    (j.lock_held ? " (device/group locked)" : "") +
    " · " +
    j.id +
    (j.result.error ? " · " + j.result.error : "");
  $("cancel").disabled = j.status !== "queued";
  $("rollback").disabled =
    !j.result.rollback ||
    !["succeeded", "failed", "resolved"].includes(j.status);
  $("backup").disabled = !j.result.backup;
  $("report").textContent = JSON.stringify(j, null, 2);
  $("events").textContent = events
    .map(
      (e) =>
        `${new Date(e.time * 1000).toLocaleTimeString()}  ${e.step}  ${JSON.stringify(e.data)}`,
    )
    .join("\n");
  $("counts").replaceChildren();
  const table = document.createElement("table");
  const rows = [["Validation", "Before (up / total)", "After (up / total)"]];
  for (const [key, label] of [
    ["virtual_servers", "Virtual servers"],
    ["pools", "Pools"],
    ["nodes", "Nodes"],
    ["virtual_ips", "Virtual IPs"],
  ]) {
    const format = (s) => (s?.[key] ? `${s[key].up} / ${s[key].total}` : "—");
    rows.push([
      label,
      format(j.result.pre_validation),
      format(j.result.post_validation),
    ]);
  }
  rows.push([
    "WAF policies",
    j.result.pre_validation?.waf_policies ?? "—",
    j.result.post_validation?.waf_policies ?? "—",
  ]);
  rows.forEach((row, i) => {
    const tr = document.createElement("tr");
    row.forEach((v) => {
      const td = document.createElement(i ? "td" : "th");
      td.textContent = v;
      tr.append(td);
    });
    table.append(tr);
  });
  $("counts").append(table);
}
$("auth").onsubmit = async (e) => {
  e.preventDefault();
  token = $("token").value;
  try {
    const ds = await api("devices");
    $("device").replaceChildren();
    ds.forEach((d) => {
      const o = document.createElement("option");
      o.value = d.id;
      o.textContent = d.id + " · " + d.host;
      $("device").append(o);
    });
    $("token").value = "";
    $("login").hidden = true;
    $("workspace").hidden = false;
    message("Connected");
    await refresh();
  } catch (err) {
    token = "";
    message(err.message, true);
  }
};
$("preview").onclick = async () => {
  try {
    $("plan").textContent = JSON.stringify(
      await api("plan", "POST", request()),
      null,
      2,
    );
    message("Plan only: no device commands executed.");
  } catch (e) {
    message(e.message, true);
  }
};
$("new-job").onsubmit = async (e) => {
  e.preventDefault();
  const button = e.submitter;
  button.disabled = true;
  try {
    const body = request(),
      serialized = JSON.stringify(body);
    if (serialized !== pendingBody) {
      pendingBody = serialized;
      pendingKey = crypto.randomUUID();
    }
    const j = await api("jobs", "POST", body, pendingKey);
    pendingBody = "";
    pendingKey = "";
    selected = j.id;
    message("Job queued: " + j.id);
    await refresh();
  } catch (err) {
    message(err.message, true);
  } finally {
    button.disabled = false;
  }
};
$("refresh").onclick = refresh;
$("cancel").onclick = async () => {
  try {
    await api("jobs/" + selected + "/cancel", "POST", {});
    await refresh();
  } catch (e) {
    message(e.message, true);
  }
};
$("rollback").onclick = async () => {
  if (
    !confirm("Reboot this device to the original volume recorded by this job?")
  )
    return;
  try {
    const j = await api(
      "jobs/" + selected + "/rollback",
      "POST",
      {},
      "rollback-" + selected,
    );
    selected = j.id;
    message("Rollback queued");
    await refresh();
  } catch (e) {
    message(e.message, true);
  }
};
$("backup").onclick = async () => {
  try {
    const r = await fetch("/api/v1/jobs/" + selected + "/backup", {
      headers: { Authorization: "Bearer " + token },
    });
    if (!r.ok) throw new Error("Backup download failed");
    const url = URL.createObjectURL(await r.blob());
    const a = document.createElement("a");
    a.href = url;
    a.download = selected + ".ucs";
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (e) {
    message(e.message, true);
  }
};
setInterval(refresh, 5000);

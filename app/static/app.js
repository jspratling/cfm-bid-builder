const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const money = (n) => "$" + Number(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const TYPES = ["office","open_office","conference","lobby","corridor","break_room","restroom","classroom","medical_exam","warehouse","stairwell","other"];
const SURF = ["carpet","vct","tile","sealed_concrete","wood","other"];
const SOIL = ["light","normal","heavy"];
let photoFiles = [];
let photoURLs = {};
let current = null;
let walk = null;   // selected saved walkthrough {slug, photos}
let RATES = null;
let serviceType = "janitorial";
const FREQ = [["one_time","One time",1],["annual","Once a year",1],["semiannual","Twice a year",2],["quarterly","Quarterly",4],["monthly","Monthly",12]];
const FCS = [["strip_wax","Strip and wax VCT"],["carpet_clean","Carpet cleaning"]];

function show(view) {
  for (const v of ["formView", "runView", "resultView"]) $(v).hidden = v !== view;
}

async function loadStatus() {
  const s = await fetch("/api/status").then((r) => r.json());
  $("status").innerHTML =
    (s.demo_mode ? `<span class="demo">DEMO MODE: no AI calls</span>` : `<span class="live">LIVE: ${esc(s.model)}${s.provider === "openai" ? " (OpenAI key)" : ""}</span>`) +
    `<span>Tokens today: ${s.tokens_today.toLocaleString()} / ${s.limits.max_tokens_per_day.toLocaleString()}</span>`;
}

async function loadBids() {
  const list = await fetch("/api/bids").then((r) => r.json());
  $("bidList").innerHTML = list.length ? list.map((b) =>
    `<li data-id="${esc(b.id)}" class="${current && current.id === b.id ? "on" : ""}"><b>${esc(b.client)}</b>
     <small>${esc(b.building)}<br>${({janitorial:"Janitorial",floor_care:"Floor care",both:"Jan + floor"})[b.service_type] || ""} &middot; ${money(b.first_year_value)} yr 1 &middot; ${esc(b.status.replace("_", " "))}${b.demo_mode ? " &middot; demo" : ""}</small></li>`).join("")
    : `<li><small>No bids yet.</small></li>`;
  document.querySelectorAll("#bidList li[data-id]").forEach((li) => li.onclick = () => openBid(li.dataset.id));
}

// ---------- area table ----------
const opts = (list, v) => list.map((o) => `<option ${o === v ? "selected" : ""}>${o}</option>`).join("");
function addRow(a = {}) {
  const tr = document.createElement("tr");
  tr.innerHTML = `<td><input class="an" value="${esc(a.name || "")}" placeholder="e.g. Floor 1 offices"></td>
    <td><select class="at">${opts(TYPES, a.area_type || "office")}</select></td>
    <td><select class="as">${opts(SURF, a.surface || "carpet")}</select></td>
    <td><input class="aq" type="number" min="1" value="${a.sq_ft || ""}" placeholder="sq ft"></td>
    <td><select class="al">${opts(SOIL, a.soil_level || "normal")}</select></td>
    <td><button class="x" title="Remove">&times;</button></td>`;
  tr.dataset.fixtures = JSON.stringify(a.fixtures || {});
  tr.dataset.source = a.source || "estimator table";
  tr.querySelector(".x").onclick = () => tr.remove();
  $("areaTable").querySelector("tbody").appendChild(tr);
}
function readAreas() {
  return [...$("areaTable").querySelectorAll("tbody tr")].map((tr) => ({
    name: tr.querySelector(".an").value.trim(), area_type: tr.querySelector(".at").value,
    surface: tr.querySelector(".as").value, sq_ft: Number(tr.querySelector(".aq").value),
    soil_level: tr.querySelector(".al").value, fixtures: JSON.parse(tr.dataset.fixtures || "{}"), source: tr.dataset.source || "estimator table",
  })).filter((a) => a.name && a.sq_ft > 0);
}

// ---------- services ----------
function setService(v) {
  serviceType = v;
  document.querySelectorAll("#serviceSeg button").forEach((b) => b.classList.toggle("on", b.dataset.v === v));
  document.querySelectorAll(".jan").forEach((el) => (el.hidden = v === "floor_care"));
  document.querySelectorAll(".fc").forEach((el) => (el.hidden = v === "janitorial"));
  if (v !== "janitorial" && !$("fcTable").querySelector("tbody tr")) addFc();
}
document.querySelectorAll("#serviceSeg button").forEach((b) => (b.onclick = () => setService(b.dataset.v)));

// ---------- floor care ----------
const fcPrice = (svc) => Number(svc === "strip_wax" ? $("pStrip").value : $("pCarpet").value) || 0;
function addFc(f = {}) {
  const tr = document.createElement("tr");
  tr.innerHTML = `<td><input class="fn" value="${esc(f.name || "")}" placeholder="e.g. Break room"></td>
    <td><select class="fs">${FCS.map(([v, l]) => `<option value="${v}" ${v === (f.service || "strip_wax") ? "selected" : ""}>${l}</option>`).join("")}</select></td>
    <td><input class="fq" type="number" min="1" value="${f.sq_ft || ""}" placeholder="sq ft"></td>
    <td><select class="ff">${FREQ.map(([v, l]) => `<option value="${v}" ${v === (f.frequency || "one_time") ? "selected" : ""}>${l}</option>`).join("")}</select></td>
    <td class="n fp">$0.00</td><td><button class="x" title="Remove">&times;</button></td>`;
  tr.querySelector(".x").onclick = () => { tr.remove(); fcRecalc(); };
  tr.querySelectorAll("input,select").forEach((el) => (el.oninput = fcRecalc));
  $("fcTable").querySelector("tbody").appendChild(tr);
  fcRecalc();
}
function readFc() {
  return [...$("fcTable").querySelectorAll("tbody tr")].map((tr) => ({
    name: tr.querySelector(".fn").value.trim(), service: tr.querySelector(".fs").value,
    sq_ft: Number(tr.querySelector(".fq").value), frequency: tr.querySelector(".ff").value,
  })).filter((f) => f.name && f.sq_ft > 0);
}
function fcRecalc() {
  let total = 0;
  $("fcTable").querySelectorAll("tbody tr").forEach((tr) => {
    const per = (Number(tr.querySelector(".fq").value) || 0) * fcPrice(tr.querySelector(".fs").value);
    const times = FREQ.find((x) => x[0] === tr.querySelector(".ff").value)[2];
    tr.querySelector(".fp").textContent = money(per);
    total += per * times;
  });
  $("fcTotal").textContent = money(total);
}
$("pStrip").oninput = $("pCarpet").oninput = fcRecalc;
$("addFc").onclick = () => addFc();
$("copyFc").onclick = () => {
  const have = new Set(readFc().map((f) => f.name.toLowerCase()));
  readAreas().forEach((a) => {
    if (have.has(a.name.toLowerCase())) return;
    if (a.surface === "vct") addFc({ name: a.name, service: "strip_wax", sq_ft: a.sq_ft, frequency: "semiannual" });
    if (a.surface === "carpet") addFc({ name: a.name, service: "carpet_clean", sq_ft: a.sq_ft, frequency: "annual" });
  });
  [...$("fcTable").querySelectorAll("tbody tr")].forEach((tr) => { if (!tr.querySelector(".fn").value && !tr.querySelector(".fq").value) tr.remove(); });
  fcRecalc();
};

// ---------- photos ----------
function renderThumbs() {
  const srv = walk ? walk.photos.map((n) => `<figure class="srv"><img src="${photoURLs[n]}" title="${esc(n)}" loading="lazy"><figcaption>${esc(n.split("__")[0].replace(/-/g, " "))}</figcaption></figure>`).join("") : "";
  $("walkInfo").textContent = walk ? `Saved walkthrough "${walk.title || walk.slug}": ${walk.photos.length} photos from ${walk.rooms.length} rooms will be surveyed.` : "";
  $("thumbs").innerHTML = srv + photoFiles.map((f, i) =>
    `<figure><img src="${photoURLs[f.name]}" title="${esc(f.name)}"><button data-i="${i}">&times;</button></figure>`).join("");
  $("thumbs").querySelectorAll("button").forEach((b) => b.onclick = () => { photoFiles.splice(+b.dataset.i, 1); renderThumbs(); });
}
function addPhotos(files) {
  for (const f of files) {
    if (!/image\/(jpeg|png|webp)/.test(f.type)) continue;
    if (photoFiles.length >= 60) break;
    photoFiles.push(f);
    photoURLs[f.name] = URL.createObjectURL(f);
  }
  renderThumbs();
}
const drop = $("drop");
drop.onclick = () => $("photos").click();
$("photos").onchange = (e) => addPhotos(e.target.files);
drop.ondragover = (e) => { e.preventDefault(); drop.classList.add("over"); };
drop.ondragleave = () => drop.classList.remove("over");
drop.ondrop = (e) => { e.preventDefault(); drop.classList.remove("over"); addPhotos(e.dataTransfer.files); };

// ---------- form ----------
function resetForm() {
  current = null;
  ["client", "building", "notes"].forEach((id) => ($(id).value = ""));
  $("visits").value = "5";
  $("wage").value = RATES ? RATES.labor.default_hourly_wage.toFixed(2) : "13.00";
  $("pStrip").value = RATES ? RATES.floor_care.services.strip_wax.price_per_sqft.toFixed(2) : "1.25";
  $("pCarpet").value = RATES ? RATES.floor_care.services.carpet_clean.price_per_sqft.toFixed(2) : "0.85";
  $("areaTable").querySelector("tbody").innerHTML = "";
  $("fcTable").querySelector("tbody").innerHTML = "";
  addRow();
  setService("janitorial");
  photoFiles = []; walk = null; $("walkSel").value = ""; renderThumbs(); $("plan").value = ""; $("formMsg").textContent = "";
  show("formView"); loadBids();
}
$("newBid").onclick = resetForm;
$("addRow").onclick = () => addRow();
async function loadWalkList() {
  const list = await fetch("/api/walkthroughs").then((r) => r.json());
  $("walkSel").innerHTML = `<option value="">Saved walkthrough…</option>` +
    list.map((w) => `<option value="${esc(w.slug)}">${esc(w.title)} (${w.photos} photos)</option>`).join("");
}
$("walkSel").onchange = async () => {
  const slug = $("walkSel").value;
  if (!slug) { walk = null; renderThumbs(); return; }
  walk = await fetch(`/api/walkthroughs/${encodeURIComponent(slug)}`).then((r) => r.json());
  walk.photos.forEach((n) => (photoURLs[n] = `/api/walkthroughs/${encodeURIComponent(slug)}/photos/${encodeURIComponent(n)}`));
  if (walk.client_name) $("client").value = walk.client_name;
  if (walk.building_name) $("building").value = walk.building_name;
  if (walk.estimator_notes) $("notes").value = walk.estimator_notes;
  if (walk.visits_per_week) $("visits").value = String(walk.visits_per_week);
  if (walk.service_type) setService(walk.service_type);
  if (walk.areas && walk.areas.length) { $("areaTable").querySelector("tbody").innerHTML = ""; walk.areas.forEach(addRow); }
  if (walk.floor_care && walk.floor_care.length) { $("fcTable").querySelector("tbody").innerHTML = ""; walk.floor_care.forEach(addFc); }
  renderThumbs();
};
$("loadSample").onclick = async () => {
  const s = await fetch("/api/sample").then((r) => r.json());
  $("client").value = s.client_name; $("building").value = s.building_name;
  $("visits").value = String(s.visits_per_week); $("notes").value = s.estimator_notes;
  $("areaTable").querySelector("tbody").innerHTML = "";
  s.areas.forEach(addRow);
  if (serviceType !== "janitorial") { $("fcTable").querySelector("tbody").innerHTML = ""; $("copyFc").onclick(); }
};

function animateSteps() {
  const items = [...document.querySelectorAll("#steps li")];
  items.forEach((li) => (li.className = ""));
  let i = 0;
  items[0].className = "run";
  return setInterval(() => {
    if (i < items.length - 1) { items[i].className = "done"; i++; items[i].className = "run"; }
  }, 1600);
}

$("build").onclick = async () => {
  const payload = {
    client_name: $("client").value.trim(), building_name: $("building").value.trim(),
    service_type: serviceType, visits_per_week: Number($("visits").value), hourly_wage: Number($("wage").value) || null,
    estimator_notes: $("notes").value, areas: serviceType === "floor_care" ? [] : readAreas(),
    floor_care: serviceType === "janitorial" ? [] : readFc(),
    walkthrough: walk ? walk.slug : null,
    floor_care_prices: { strip_wax: fcPrice("strip_wax"), carpet_clean: fcPrice("carpet_clean") },
  };
  if (!payload.client_name || !payload.building_name) { $("formMsg").textContent = "Enter a client and building name."; return; }
  if (serviceType !== "floor_care" && !payload.areas.length && !photoFiles.length && !walk && !$("plan").files.length) {
    $("formMsg").textContent = "For janitorial, add photos, a floor plan, or at least one area with square footage."; return;
  }
  if (serviceType !== "janitorial" && !payload.floor_care.length) {
    $("formMsg").textContent = "Add at least one floor care line (area, service, square feet)."; return;
  }
  if (serviceType !== "floor_care" && (payload.hourly_wage < 7.25 || payload.hourly_wage > 60)) {
    $("formMsg").textContent = "Hourly wage must be between $7.25 and $60."; return;
  }
  const fd = new FormData();
  fd.append("payload", JSON.stringify(payload));
  photoFiles.forEach((f) => fd.append("photos", f));
  if ($("plan").files[0]) fd.append("floor_plan", $("plan").files[0]);
  show("runView");
  const timer = animateSteps();
  try {
    const r = await fetch("/api/bids", { method: "POST", body: fd });
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || "Something went wrong.");
    clearInterval(timer);
    document.querySelectorAll("#steps li").forEach((li) => (li.className = "done"));
    renderBid(data); loadBids(); loadStatus();
  } catch (e) {
    clearInterval(timer); show("formView"); $("formMsg").textContent = e.message;
  }
};

// ---------- result ----------
async function openBid(id) {
  const b = await fetch(`/api/bids/${encodeURIComponent(id)}`).then((r) => r.json());
  const slug = b.request && b.request.walkthrough;   // show the walkthrough photos on the survey cards of a saved bid
  if (slug) (b.surveys || []).forEach((s) => { if (!photoURLs[s.photo_name]) photoURLs[s.photo_name] = `/api/walkthroughs/${encodeURIComponent(slug)}/photos/${encodeURIComponent(s.photo_name)}`; });
  renderBid(b); loadBids();
}

function renderBid(b) {
  current = b;
  const p = b.pricing, f = b.floor_care, rq = b.request, tot = b.totals;
  const label = { janitorial: "Janitorial", floor_care: "Floor care only", both: "Janitorial + floor care" }[b.service_type || "janitorial"];
  $("rTitle").textContent = rq.client_name;
  $("rSub").textContent = `${rq.building_name} · ${label}${p ? ` · ${rq.visits_per_week} visits/week · $${p.hourly_wage.toFixed(2)}/hr` : ""} · ${b.created.replace("T", " ")}${b.demo_mode ? " · demo mode (saved sample agent outputs)" : ""}`;
  $("rStatus").textContent = b.status.replace("_", " ").toUpperCase();
  $("rStatus").className = "pill " + b.status;
  const k = (t, v, main) => `<div class="kpi${main ? " main" : ""}"><small>${t}</small><b>${v}</b></div>`;
  const tiles = [];
  if (p) tiles.push(k("Janitorial / month", money(p.monthly_price), true));
  if (f) {
    if (f.one_time_total) tiles.push(k("Floor care, one-time", money(f.one_time_total), !p));
    if (f.recurring_annual_total) tiles.push(k("Floor care / year", money(f.recurring_annual_total), !p && !f.one_time_total));
  }
  tiles.push(k("First-year total", money(tot.first_year_value)));
  if (p) tiles.push(k("Per sq ft / month", "$" + p.price_per_sqft_month.toFixed(3)), k("Labor hours / visit", p.hours_per_visit));
  if (!p && f) tiles.push(k("Floor care sq ft", f.lines.reduce((a, l) => a + l.sq_ft, 0).toLocaleString()));
  $("kpis").innerHTML = tiles.slice(0, 4).join("");

  const flags = b.flags.map((x) => `<div class="alert ${x.level}"><b>${x.level === "stop" ? "STOP" : "Check"}</b>${esc(x.msg)}</div>`);
  const sec = b.injection_alerts.map((a) => `<div class="alert sec"><b>Security</b>Blocked instruction-like text in ${esc(a.source)}:
      <i>${a.phrases.map(esc).join(", ")}</i>. ${esc(a.action)}. The price was not affected.</div>`);
  $("alerts").innerHTML = flags.concat(sec).join("") || `<div class="alert ok"><b>All checks passed</b>No pricing or security problems found.</div>`;

  $("fcResultCard").hidden = !f;
  if (f) {
    $("fcLines").innerHTML = `<tr><th>Area</th><th>Service</th><th class="n">Sq ft</th><th>How often</th><th class="n">Per sq ft</th><th class="n">Per service</th><th class="n">First year</th></tr>` +
      f.lines.map((l) => `<tr><td>${esc(l.name)}</td><td>${esc(l.service_label)}</td><td class="n">${l.sq_ft.toLocaleString()}</td><td>${esc(l.frequency_label)}</td>
        <td class="n">$${l.price_per_sqft.toFixed(2)}</td><td class="n">${money(l.price_per_service)}</td><td class="n">${money(l.annual_price)}</td></tr>`).join("") +
      `<tr class="tot"><td colspan="6">First-year floor care</td><td class="n">${money(f.first_year_total)}</td></tr>`;
  }

  $("janCard").hidden = !p; $("costCard").hidden = !p;
  if (p) {
    $("linesTable").innerHTML = `<tr><th>Area</th><th>Type</th><th>Floor</th><th>Soil</th><th class="n">Sq ft</th><th class="n">Sq ft/hr</th><th class="n">Hrs/visit</th><th>Source</th></tr>` +
      p.lines.map((l) => `<tr><td>${esc(l.name)}</td><td>${esc(l.area_type.replace("_", " "))}</td><td>${esc(l.surface)}</td><td>${esc(l.soil_level)}</td>
        <td class="n">${l.sq_ft.toLocaleString()}</td><td class="n">${l.rate_sqft_per_hour.toLocaleString()}</td><td class="n">${l.hours_per_visit}</td><td>${esc(l.source)}</td></tr>`).join("") +
      `<tr class="tot"><td colspan="4">Total</td><td class="n">${p.total_sq_ft.toLocaleString()}</td><td></td><td class="n">${p.hours_per_visit}</td><td></td></tr>`;
    $("assumptions").innerHTML = b.takeoff.assumptions.map((x) => `<li>${esc(x)}</li>`).join("") || "<li>None</li>";
    $("questions").innerHTML = b.takeoff.questions_for_estimator.map((x) => `<li>${esc(x)}</li>`).join("") || "<li>None</li>";
    const sug = b.takeoff.floor_care_suggestions || [];
    $("fcSuggestBox").hidden = !sug.length;
    $("fcSuggest").innerHTML = sug.map((x) => `<li>${esc(x)}</li>`).join("");
    $("costTable").innerHTML = [
      [`Labor (${p.monthly_hours} hrs at $${p.hourly_wage.toFixed(2)})`, p.labor], [`Payroll taxes (${(p.burden_rates.payroll_taxes * 100).toFixed(1)}%: FICA, FUTA, AL unemployment)`, p.payroll_taxes],
      [`Workers' comp (${(p.burden_rates.workers_comp * 100).toFixed(2)}%, class 9014)`, p.workers_comp],
      ...(p.other_benefits ? [["Other benefits", p.other_benefits]] : []),
      ...(p.equipment ? [[`Equipment (${pct(p.costs.equipment_pct_of_labor)} of labor)`, p.equipment]] : []),
      ...(p.insurance ? [[`Liability insurance (${pct(p.costs.insurance_pct_of_labor)} of labor)`, p.insurance]] : []), ["Overhead", p.overhead],
      [`Supplies (${pct(p.costs ? p.costs.supplies_pct_of_price : 0.05, 0)} of price)`, p.supplies], ["Profit", p.profit]].map(([kk, v]) => `<tr><td>${kk}</td><td class="n">${money(v)}</td></tr>`).join("") +
      `<tr class="tot"><td>Monthly price</td><td class="n">${money(p.monthly_price)}</td></tr>`;
  }

  $("surveyCard").hidden = !b.surveys.length;
  $("surveys").innerHTML = b.surveys.map((s) => {
    const fx = Object.entries(s.fixtures_visible || {}).filter(([, v]) => v).map(([kk, v]) => `${v} ${kk.replace("_", " ")}`).join(", ");
    return `<div class="survey">${photoURLs[s.photo_name] ? `<img src="${photoURLs[s.photo_name]}" style="width:100%;border-radius:6px;margin-bottom:6px">` : ""}
      <h5>${esc(s.photo_name)}</h5>
      <span class="tag">${esc(s.area_type.replace("_", " "))}</span><span class="tag">${esc(s.surface)}</span><span class="tag">${esc(s.soil_level)} soil</span>
      <p>${esc(s.observations)}</p>${fx ? `<p><b>Fixtures:</b> ${esc(fx)}</p>` : ""}
      ${s.special_services.length ? `<p><b>Periodic work:</b> ${s.special_services.map(esc).join(", ")}</p>` : ""}
      ${s.text_seen_in_photo ? `<p class="hint">Text in photo (quarantined): "${esc(s.text_seen_in_photo)}"</p>` : ""}
      <div class="conf" title="Confidence ${Math.round(s.confidence * 100)}%"><i style="width:${s.confidence * 100}%"></i></div></div>`;
  }).join("");

  const u = b.usage, t = b.usage_total;
  $("usageTable").innerHTML = `<tr><th>Agent</th><th class="n">Input</th><th class="n">Cached</th><th class="n">Output</th><th class="n">Cost</th></tr>` +
    (u.length ? u.map((r) => `<tr><td>${esc(r.agent.replace("_", " "))}</td><td class="n">${r.input_tokens.toLocaleString()}</td>
      <td class="n">${r.cached_input_tokens.toLocaleString()}</td><td class="n">${r.output_tokens.toLocaleString()}</td><td class="n">$${r.cost_usd.toFixed(4)}</td></tr>`).join("")
      : `<tr><td colspan="5" class="hint">No AI calls (demo mode).</td></tr>`) +
    `<tr class="tot"><td>Total (${t.calls} calls)</td><td class="n">${t.input_tokens.toLocaleString()}</td><td class="n">${t.cached_input_tokens.toLocaleString()}</td>
     <td class="n">${t.output_tokens.toLocaleString()}</td><td class="n">$${t.cost_usd.toFixed(4)}</td></tr>`;
  $("usageNote").textContent = `${t.requests} model requests, ${t.tool_calls} tool calls, ${t.seconds}s of model time.`;

  renderRevisions(b); renderBench(b); wiStart(b);
  $("approveCard").hidden = b.status === "approved";
  $("decisionMsg").textContent = ""; $("decisionNote").value = "";
  $("proposalLink").href = `/api/bids/${encodeURIComponent(b.id)}/proposal`;
  $("proposalLink").textContent = b.status === "approved" ? "Open proposal" : "Preview proposal (draft)";
  $("audit").textContent = JSON.stringify(b.audit, null, 2); $("audit").hidden = true;
  show("resultView");
}

async function decide(decision) {
  const body = { decision, approver: $("approver").value.trim(), note: $("decisionNote").value.trim() };
  if (body.approver.length < 2) { $("decisionMsg").textContent = "Enter your name."; return; }
  const r = await fetch(`/api/bids/${encodeURIComponent(current.id)}/decision`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await r.json();
  if (!r.ok) { $("decisionMsg").textContent = data.detail; return; }
  renderBid(data); loadBids();
}
$("approve").onclick = () => decide("approve");
$("sendBack").onclick = () => decide("send_back");
$("showAudit").onclick = () => ($("audit").hidden = !$("audit").hidden);

fetch("/api/rates").then((r) => r.json()).then((r) => { RATES = r; loadStatus(); resetForm(); loadWalkList(); });

// ---------- Revision agent ----------
function renderRevisions(b) {
  const r = (b.revisions || []).slice().reverse();
  $("revisions").innerHTML = r.map((x) => `<li><b>"${esc(x.message)}"</b><br>${esc(x.summary)}
    ${x.changes.length ? `<br><small>${x.changes.map(esc).join(" · ")}</small>` : ""}
    <br><span class="delta">First year ${money(x.before.first_year_value)} → ${money(x.after.first_year_value)}</span>
    ${x.injection_hits && x.injection_hits.length ? `<br><small class="msg">Security: instruction-like text screened (${x.injection_hits.map(esc).join(", ")})</small>` : ""}</li>`).join("");
}
$("reviseBtn").onclick = async () => {
  const message = $("reviseMsg").value.trim();
  if (message.length < 3) { $("reviseStatus").textContent = "Type the change you want."; return; }
  $("reviseBtn").disabled = true; $("reviseStatus").className = "msg ok"; $("reviseStatus").textContent = "Revision agent working...";
  try {
    const r = await fetch(`/api/bids/${encodeURIComponent(current.id)}/revise`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message }) });
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || "Revision failed");
    $("reviseMsg").value = ""; renderBid(data); loadBids(); loadStatus();
    $("reviseStatus").className = "msg ok"; $("reviseStatus").textContent = "Updated. Bid is back to draft for approval.";
  } catch (e) { $("reviseStatus").className = "msg"; $("reviseStatus").textContent = e.message; }
  $("reviseBtn").disabled = false;
};

// ---------- Past-Bid Comparison ----------
function renderBench(b) {
  const x = b.benchmark;
  if (!x) { $("bench").innerHTML = ""; return; }
  const rows = (x.details || []).map((d) => `<tr><td>${esc(d.past_name)}</td>
    <td class="n">$${(d.price_per_sqft_month.this || 0).toFixed(3)} vs $${d.price_per_sqft_month.past.toFixed(3)}</td>
    <td class="n">${d.hours_per_1000_sqft_per_visit.this} vs ${d.hours_per_1000_sqft_per_visit.past}</td></tr>`).join("");
  $("bench").innerHTML = `<span class="verdict ${esc(x.verdict)}">${esc(x.verdict.replace(/_/g, " "))}</span>
    <p>${esc(x.explanation)}</p>
    ${rows ? `<table class="data"><tr><th>Past bid</th><th class="n">$/sq ft/mo (this vs past)</th><th class="n">Hrs per 1,000 sq ft/visit</th></tr>${rows}</table>` : ""}
    ${x.suggestions.length ? `<ul>${x.suggestions.map((s) => `<li>${esc(s)}</li>`).join("")}</ul>` : ""}`;
}
$("benchBtn").onclick = async () => {
  $("benchBtn").disabled = true; $("benchStatus").className = "msg ok"; $("benchStatus").textContent = "Comparing...";
  try {
    const r = await fetch(`/api/bids/${encodeURIComponent(current.id)}/benchmark`, { method: "POST" });
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || "Comparison failed");
    renderBid(data); loadStatus(); $("benchStatus").textContent = "";
  } catch (e) { $("benchStatus").className = "msg"; $("benchStatus").textContent = e.message; }
  $("benchBtn").disabled = false;
};


// ---------- What-if pricing (calls /whatif: Python does every number, nothing is saved until "Apply") ----------
let wiState = null, wiTimer = null, wiFields = null;
const pct = (v, d = 1) => (v * 100).toFixed(v > 0 && v < 0.01 ? 2 : d) + "%";
function wiStart(b) {
  $("whatIfCard").hidden = !b.pricing;
  if (!b.pricing) return;
  // bids priced before Nov 23 had no equipment or insurance line: start the chart from what they were actually priced with
  const legacy = b.pricing.costs ? {} : { equipment_pct_of_labor: 0, insurance_pct_of_labor: 0 };
  wiState = { hourly_wage: b.pricing.hourly_wage, visits_per_week: b.request.visits_per_week, costs: { ...legacy, ...(b.request.costs || {}), ...(b.pricing.costs || {}) } };
  wiFields = null; $("wiControls").innerHTML = ""; $("wiMsg").textContent = "";
  wiFetch();
}
function wiBuildControls(fields) {
  const saved = current.pricing.costs || { equipment_pct_of_labor: 0, insurance_pct_of_labor: 0 };
  const row = (id, label, min, max, step, val, fmt, base) => `<label>${esc(label)}
    <div class="row"><input type="range" id="${id}" min="${min}" max="${max}" step="${step}" value="${val}"><span id="${id}V">${fmt(val)}</span></div>
    <small class="hint">Bid: ${fmt(base)}</small></label>`;
  let h = row("wi_wage", "Hourly pay", 7.25, 30, 0.25, wiState.hourly_wage, (v) => "$" + (+v).toFixed(2), current.pricing.hourly_wage);
  h += row("wi_visits", "Visits per week", 1, 7, 1, wiState.visits_per_week, (v) => v, current.request.visits_per_week);
  for (const [k, f] of Object.entries(fields)) {
    const isFactor = k === "labor_hours_factor";
    const fmt = isFactor ? (v) => (+v).toFixed(2) + "x" : (v) => pct(+v, 1);
    h += row("wi_" + k, f.label, f.min, f.max, isFactor ? 0.05 : 0.0025, wiState.costs[k] ?? f.default, fmt, saved[k] ?? f.default);
  }
  $("wiControls").innerHTML = h;
  $("wiControls").querySelectorAll("input[type=range]").forEach((inp) => inp.oninput = () => {
    const k = inp.id.slice(3), v = +inp.value;
    if (k === "wage") wiState.hourly_wage = v; else if (k === "visits") wiState.visits_per_week = v; else wiState.costs[k] = v;
    const isF = k === "labor_hours_factor";
    $(inp.id + "V").textContent = k === "wage" ? "$" + v.toFixed(2) : k === "visits" ? v : isF ? v.toFixed(2) + "x" : pct(v, 1);
    clearTimeout(wiTimer); wiTimer = setTimeout(wiFetch, 120);
  });
}
async function wiFetch() {
  const r = await fetch(`/api/bids/${encodeURIComponent(current.id)}/whatif`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(wiState) });
  const d = await r.json();
  if (!r.ok) { $("wiMsg").textContent = d.detail || "What-if failed"; return; }
  $("wiMsg").textContent = "";
  if (!wiFields) { wiFields = d.fields; wiBuildControls(d.fields); }
  const p = d.priced, base = current.pricing.monthly_price, diff = p.monthly_price - base;
  $("wiPrice").textContent = money(p.monthly_price);
  $("wiDelta").textContent = Math.abs(diff) < 0.005 ? "same as bid" : `${diff > 0 ? "+" : "−"}${money(Math.abs(diff))} vs bid (${diff > 0 ? "+" : "−"}${Math.abs(diff / base * 100).toFixed(1)}%)`;
  wiCurve(d.wage_curve, wiState.hourly_wage, p.monthly_price, base, current.pricing.hourly_wage);
  const parts = [["Labor", p.labor], ["Payroll taxes", p.payroll_taxes], ["Workers' comp", p.workers_comp], ["Benefits / PTO", p.other_benefits],
    ["Equipment", p.equipment], ["Liability insurance", p.insurance], ["Overhead", p.overhead], ["Supplies", p.supplies], ["Profit", p.profit]];
  const mx = Math.max(...parts.map((x) => x[1]), 1);
  $("wiBreak").innerHTML = `<div class="bars">${parts.map(([l, v]) => `<span class="lb">${l}</span><span class="bt" title="${l}: ${money(v)} (${(v / p.monthly_price * 100).toFixed(1)}% of fee)"><i style="width:${v / mx * 100}%"></i></span><span class="v">${money(v)}</span>`).join("")}</div>`;
  const sm = Math.max(...d.sensitivity.map((x) => Math.abs(x.delta)), 1);
  $("wiSens").innerHTML = `<div class="bars">${d.sensitivity.map((x) => `<span class="lb">${esc(x.label)}</span><span class="bt" title="${esc(x.label)}: +${money(x.delta)} per month"><i style="width:${Math.abs(x.delta) / sm * 100}%"></i></span><span class="v">+${money(x.delta)}</span>`).join("")}</div>`;
}
function wiCurve(pts, wage, price, basePrice, baseWage) {
  const W = 640, H = 240, L = 64, R = 16, T = 14, B = 40;
  const xs = pts.map((p) => p.wage), ys = pts.map((p) => p.monthly_price).concat([price, basePrice]);
  const x0 = Math.min(...xs, wage), x1 = Math.max(...xs, wage);
  let y0 = Math.min(...ys), y1 = Math.max(...ys);
  const raw = (y1 - y0) / 4 || 1, mag = 10 ** Math.floor(Math.log10(raw)), step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((m) => m >= raw);
  y0 = Math.max(0, Math.floor(y0 / step) * step); y1 = Math.ceil(y1 / step) * step;
  const X = (v) => L + (v - x0) / (x1 - x0) * (W - L - R), Y = (v) => T + (1 - (v - y0) / (y1 - y0)) * (H - T - B);
  const ticksY = []; for (let v = y0; v <= y1 + 1e-6; v += step) ticksY.push(v);
  const grid = ticksY.map((v) => `<line x1="${L}" x2="${W - R}" y1="${Y(v)}" y2="${Y(v)}" stroke="#2a2f36"/><text x="${L - 8}" y="${Y(v) + 4}" text-anchor="end" fill="#9aa6b2" font-size="11">$${Math.round(v).toLocaleString()}</text>`).join("");
  const tx = [10, 13, 15, 20, 25].filter((v) => v >= x0 && v <= x1).map((v) => `<text x="${X(v)}" y="${H - 22}" text-anchor="middle" fill="#9aa6b2" font-size="11">$${v}</text>`).join("");
  const path = pts.map((p, i) => `${i ? "L" : "M"}${X(p.wage).toFixed(1)},${Y(p.monthly_price).toFixed(1)}`).join("");
  $("wiCurve").innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Monthly fee by hourly pay">${grid}${tx}
    <text x="${L}" y="${H - 1}" fill="#9aa6b2" font-size="11">hourly pay →</text>
    <path d="${path}" fill="none" stroke="#6cb4ee" stroke-width="2"/>
    <circle cx="${X(baseWage)}" cy="${Y(basePrice)}" r="5" fill="#121417" stroke="#9aa6b2" stroke-width="2"/>
    <text x="${X(baseWage) + 8}" y="${Y(basePrice) + 16}" fill="#9aa6b2" font-size="11" stroke="#121417" stroke-width="4" paint-order="stroke">saved bid ${money(basePrice)}</text>
    <circle cx="${X(wage)}" cy="${Y(price)}" r="6" fill="#6cb4ee" stroke="#121417" stroke-width="2"/>
    <text x="${X(wage) + 9}" y="${Y(price) - 8}" fill="#e9edf2" font-size="12" font-weight="700" stroke="#121417" stroke-width="4" paint-order="stroke">${money(price)} at $${wage.toFixed(2)}</text>
    <line id="wiX" x1="0" x2="0" y1="${T}" y2="${H - B}" stroke="#9aa6b2" stroke-dasharray="3 3" visibility="hidden"/>
    <rect x="${L}" y="${T}" width="${W - L - R}" height="${H - T - B}" fill="transparent" id="wiHit"/></svg><div class="wi-tip" id="wiTip" hidden></div>`;
  const svg = $("wiCurve").querySelector("svg"), tip = $("wiTip"), cross = svg.querySelector("#wiX");
  svg.querySelector("#wiHit").onmousemove = (e) => {
    const r = svg.getBoundingClientRect(), sx = (e.clientX - r.left) / r.width * W;
    const p = pts.reduce((a, q) => Math.abs(X(q.wage) - sx) < Math.abs(X(a.wage) - sx) ? q : a);
    cross.setAttribute("x1", X(p.wage)); cross.setAttribute("x2", X(p.wage)); cross.setAttribute("visibility", "visible");
    tip.hidden = false; tip.style.left = (X(p.wage) / W * 100) + "%"; tip.style.top = (Y(p.monthly_price) / H * r.height) + "px";
    tip.innerHTML = `<b>$${p.wage.toFixed(2)}/hr</b> → ${money(p.monthly_price)}/month`;
  };
  svg.querySelector("#wiHit").onmouseleave = () => { tip.hidden = true; cross.setAttribute("visibility", "hidden"); };
}
$("wiReset").onclick = () => wiStart(current);
$("wiApply").onclick = async () => {
  const r = await fetch(`/api/bids/${encodeURIComponent(current.id)}/costs`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(wiState) });
  const d = await r.json();
  if (!r.ok) { $("wiMsg").textContent = d.detail || "Could not apply"; return; }
  renderBid(d); loadBids();
  $("wiMsg").className = "msg ok"; $("wiMsg").textContent = "Saved to the bid. It is back to draft for manager approval.";
};

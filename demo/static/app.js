"use strict";
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const safeUrl = (u) => (/^(https:\/\/|\/gallery\/[a-f0-9]{12}\.jpg$)/.test(String(u || "")) ? esc(u) : "");
const key = (v) => ({"CONFIRMED": "CONFIRMED", "POSSIBLE MATCH": "POSSIBLE", "NO MATCH FOUND": "NOMATCH"}[v] || "NOMATCH");
const STICKER = {"CONFIRMED": "Recalled", "POSSIBLE MATCH": "Check the label", "NO MATCH FOUND": "No match found"};
const UNAVAILABLE = "Scanning is temporarily unavailable. Recall lookup still works.";
const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let recallCount = 0;

function fmtDate(d) {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(d || ""));
  if (!m) return "";
  return new Date(Date.UTC(+m[1], +m[2] - 1, +m[3])).toLocaleDateString("en-US", { year: "numeric", month: "long", day: "numeric", timeZone: "UTC" });
}

// Free hosting sleeps when idle; onSlow lets the caller show the wake-up note in its own place.
async function api(url, opts = {}, onSlow = () => {}) {
  const slow = setTimeout(onSlow, 3500);
  const ctrl = new AbortController();
  const kill = setTimeout(() => ctrl.abort(), 90000);
  try {
    const r = await fetch(url, { ...opts, signal: ctrl.signal });
    let body = {};
    try { body = await r.json(); } catch (e) { /* not JSON */ }
    if (!r.ok) { const err = new Error(body.error || "The server couldn't handle that request. Try again in a minute."); err.status = r.status; throw err; }
    return body;
  } catch (e) {
    if (e.name === "AbortError") { const err = new Error("The server took too long to answer. Try again in a minute."); err.status = 504; throw err; }
    throw e;
  } finally { clearTimeout(slow); clearTimeout(kill); }
}

// --- file pickers --------------------------------------------------------------------------------------------
const urls = {};
function showPreview(which) {
  const input = $("#" + which), zone = input.closest(".drop"), img = $(`#${which}Preview`);
  const f = input.files[0];
  if (urls[which]) URL.revokeObjectURL(urls[which]);
  if (!f) { zone.classList.remove("has-file"); img.hidden = true; return; }
  urls[which] = URL.createObjectURL(f);
  img.src = urls[which]; img.alt = which === "photo" ? "Your item photo" : "Your label photo"; img.hidden = false;
  zone.classList.add("has-file");
  if (which === "photo") clearError();
}
function wireDrop(which) {
  const input = $("#" + which), zone = input.closest(".drop");
  input.addEventListener("change", () => showPreview(which));
  zone.addEventListener("dragover", (e) => { e.preventDefault(); zone.classList.add("over"); });
  zone.addEventListener("dragleave", () => zone.classList.remove("over"));
  zone.addEventListener("drop", (e) => {
    e.preventDefault(); zone.classList.remove("over");
    if (!e.dataTransfer.files.length) return;
    const dt = new DataTransfer(); dt.items.add(e.dataTransfer.files[0]); input.files = dt.files; showPreview(which);
  });
}

// --- errors --------------------------------------------------------------------------------------------------
function clearError() {
  $("#formErr").hidden = true; $("#inviteErr").hidden = true; $("#photoErr").hidden = true; $("#invite").removeAttribute("aria-invalid");
}
function formError(msg) {
  const e = $("#formErr"); e.textContent = msg; e.hidden = false;
  if (/free scans are used up/i.test(msg)) {
    const a = document.createElement("a"); a.href = "#invite-request"; a.textContent = "Request an invite code";
    e.append(" ", a, ".");
  }
}
function inviteError(msg) {
  const e = $("#inviteErr"); e.textContent = msg; e.hidden = false;
  $("#invite").setAttribute("aria-invalid", "true"); $("#invite").focus();
}

// --- results -------------------------------------------------------------------------------------------------
function show(html) {
  const r = $("#result"); r.innerHTML = html; r.hidden = false;
  r.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "start" });
}
function loading(wake) {
  const n = recallCount ? `about ${(Math.round(recallCount / 100) * 100).toLocaleString("en-US")}` : "all the";
  return `<div class="panel loading"><span class="pulse" aria-hidden="true"></span><div><p><strong>Checking ${n} recalls...</strong></p>
    <p class="muted">Reading your photo, finding the closest recall photos, and checking any model number. Usually 10 to 30 seconds.</p>
    ${wake ? `<p class="wake">The free server was asleep and is waking up. The first scan after a quiet spell can take up to a minute.</p>` : ""}</div></div>`;
}
function unavailable(msg) {
  return `<div class="panel notice"><span class="dot" aria-hidden="true"></span><p>${esc(msg || UNAVAILABLE)}</p></div>`;
}
function sticker(verdict, small = false, animate = false) {
  return `<span class="sticker s-${key(verdict)}${small ? " small" : ""}${animate ? " slap" : ""}">${esc(STICKER[verdict])}</span>`;
}
function noteText(n, res) {
  const lr = res.label_read || {};
  const codes = [lr.model_number, lr.upc].filter(Boolean).join(", ");
  return {
    label_no_code: "We couldn't read a model number or UPC on the label. Try a sharper, closer photo of the sticker.",
    label_code_unlisted: `We read ${codes || "a code"} on the label. It isn't listed in any CPSC recall in our data.`,
    label_other_recall: "The label matches a different recall from the closest photos. The label is the stronger evidence.",
    car_seat: "Car seats are regulated by NHTSA, not CPSC, and aren't covered here. Check nhtsa.gov/recalls.",
  }[n] || "";
}
function recallCard(m, photo) {
  return `<article class="recall">
    <div class="pair"><figure><img src="${safeUrl(m.image_urls[0])}" alt="CPSC recall photo"><figcaption>Recall photo</figcaption></figure>
      <figure><img src="${photo}" alt="Your item photo"><figcaption>Your photo</figcaption></figure></div>
    <div><h3>${esc(m.title)}</h3>
      <dl class="facts">
        ${m.codes && m.codes.length ? `<dt>Matched</dt><dd class="codes">${esc(m.codes.map((c) => c.replace(/^(model|upc):/, (x, k) => (k === "upc" ? "UPC " : "Model "))).join(", "))}</dd>` : ""}
        <dt>Recalled</dt><dd>${esc(fmtDate(m.date))}</dd>
        <dt>Hazard</dt><dd>${esc(m.hazards.join(" ") || "See the recall page.")}</dd>
        <dt>Remedy</dt><dd>${esc(m.remedies.join(" ") || "See the recall page.")}</dd>
      </dl>
      <p><a href="${safeUrl(m.url)}" target="_blank" rel="noopener">Read the recall on cpsc.gov</a></p></div>
  </article>`;
}
function strip(recalls, photo) {
  return `<div class="strip"><figure><img src="${photo}" alt="Your item photo"><figcaption>Your photo</figcaption></figure>
    ${recalls.map((m) => `<figure><img src="${safeUrl(m.image_urls[0])}" alt="Recall photo"><a href="${safeUrl(m.url)}" target="_blank" rel="noopener">${esc(m.title)}</a></figure>`).join("")}</div>`;
}
function renderResult(res, hadLabel) {
  const photo = urls.photo || "";
  const v = res.verdict;
  const copy = {
    "CONFIRMED": ["Recalled. Don't list it.", "The model number or UPC on your item matches a CPSC recall."],
    "POSSIBLE MATCH": ["Check the label.", hadLabel ? "Your item looks like these recalls, but the label didn't settle it. Compare the photos and check the recall pages." : "Your item looks like these recalls. That alone doesn't mean it's recalled."],
    "NO MATCH FOUND": ["No match found.", "No match in CPSC recalls. This does not mean the item is safe. Check cpsc.gov if unsure."],
  }[v];
  const notes = res.notes.map((n) => noteText(n, res)).filter(Boolean);
  const r = res.read || {};
  const read = [r.category, r.brand, r.model_number, r.upc].filter(Boolean).join(", ");
  let h = `<div class="panel verdict">
    <div class="shot"><img src="${photo}" alt="Your item photo">${sticker(v, false, true)}</div>
    <div class="verdict-copy"><h2>${esc(copy[0])}</h2><p>${esc(copy[1])}</p>
      ${notes.map((n) => `<p>${esc(n)}</p>`).join("")}
      <p class="muted">Read from your photo: ${esc(read || "nothing legible")}. Scan took ${esc(res.seconds)} seconds.</p>
      ${v === "POSSIBLE MATCH" && !hadLabel ? `<p><button type="button" class="secondary" id="addLabel">Add a label photo</button></p>` : ""}</div>`;
  if (v === "CONFIRMED") h += res.recalls.map((m) => recallCard(m, photo)).join("");
  if (v === "POSSIBLE MATCH") h += `<div class="recall wide"><div><h3>Compare these and add a label photo to confirm.</h3>${strip(res.recalls, photo)}</div></div>`;
  if (v === "NO MATCH FOUND" && res.closest && res.recalls.length) {
    h += `<div class="recall wide"><div class="closest"><h3>Closest recalls to check</h3>
      <p class="muted">Suggestions, not matches: none of these met the bar for a possible match. If your item is one of these products, check its label against the recall page.</p>
      ${strip(res.recalls, photo)}</div></div>`;
  }
  h += `</div>`;
  show(h);
  const add = $("#addLabel");
  if (add) add.addEventListener("click", () => { $("#label").click(); $("#scanner").scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth" }); });
}

async function pollJob(job, hadLabel) {
  let woke = false;
  const t0 = Date.now();
  while (Date.now() - t0 < 300000) {
    const s = await api(`/api/jobs/${encodeURIComponent(job)}`, {}, () => { woke = true; $("#result").innerHTML = loading(true); });
    if (s.state === "done") { renderResult(s.result, hadLabel); return; }
    if (s.state === "unavailable") { show(unavailable()); return; }
    if (!woke) $("#result").innerHTML = loading(false);
    await sleep(2000);
  }
  show(unavailable());
}

function scanFailed(err) {
  $("#result").hidden = true;
  if (err.status === 403 && /invite/i.test(err.message)) inviteError("That invite code isn't valid. Check it for typos, or leave it empty to use today's free scans.");
  else if (err.status === 503 && /unavailable/i.test(err.message)) show(unavailable());
  else formError(err.message);
}

async function runScan(e) {
  e.preventDefault();
  clearError();
  const photo = $("#photo").files[0], label = $("#label").files[0], invite = $("#invite").value.trim();
  if (!photo) { const el = $("#photoErr"); el.textContent = "Add a photo of the item first, or try a sample above."; el.hidden = false; $("#photo").focus(); return; }
  if (photo.size > 8 * 1024 * 1024 || (label && label.size > 8 * 1024 * 1024)) { formError("That photo is larger than 8 MB. Use a smaller photo or a screenshot of it."); return; }
  try { localStorage.setItem("ctl-invite", invite); } catch (err) { /* storage unavailable */ }
  const fd = new FormData();
  if (invite) fd.append("invite", invite);
  fd.append("photo", photo);
  if (label) fd.append("label", label);
  $("#scanBtn").disabled = true;
  show(loading(false));
  try {
    const { job } = await api("/api/jobs", { method: "POST", body: fd }, () => { $("#result").innerHTML = loading(true); });
    await pollJob(job, Boolean(label));
  } catch (err) { scanFailed(err); } finally { $("#scanBtn").disabled = false; }
}

async function runSample(btn) {
  clearError();
  if (urls.photo && urls.photo.startsWith("blob:")) URL.revokeObjectURL(urls.photo);
  urls.photo = btn.dataset.photo;
  document.querySelectorAll(".sample").forEach((b) => { b.disabled = true; });
  show(loading(false));
  try {
    const { job } = await api(`/api/samples/${encodeURIComponent(btn.dataset.sample)}`, { method: "POST" }, () => { $("#result").innerHTML = loading(true); });
    await pollJob(job, btn.dataset.sample === "recalled");
  } catch (err) { scanFailed(err); } finally { document.querySelectorAll(".sample").forEach((b) => { b.disabled = false; }); }
}

// --- batch ---------------------------------------------------------------------------------------------------
const MAX_BATCH = 10;
let batchUrls = [];
function batchError(msg) { const e = $("#batchErr"); e.textContent = msg; e.hidden = !msg; }
function showBatchThumbs() {
  batchUrls.forEach((u) => URL.revokeObjectURL(u));
  const files = [...$("#batchPhotos").files];
  batchUrls = files.map((f) => URL.createObjectURL(f));
  $("#batchThumbs").innerHTML = batchUrls.slice(0, MAX_BATCH).map((u, i) => `<img src="${u}" alt="Photo ${i + 1}">`).join("");
  batchError(files.length > MAX_BATCH ? `That's ${files.length} photos. Choose up to ${MAX_BATCH}, or split them into two batches.` : "");
}
// CSV for spreadsheets: quote every field and neutralise cells a spreadsheet would run as a formula.
const csvCell = (v) => { let t = String(v ?? ""); if (/^[=+\-@\t\r]/.test(t)) t = "'" + t; return `"${t.replace(/"/g, '""')}"`; };
function batchCsv(files, items) {
  const rows = [["photo", "verdict", "recalls", "recall links", "closest recalls to check"]];
  items.forEach((r, i) => {
    const shown = r.verdict !== "NO MATCH FOUND" ? r.recalls : [];
    const closest = r.verdict === "NO MATCH FOUND" && r.closest ? r.recalls : [];
    rows.push([files[i] ? files[i].name : `photo ${i + 1}`, r.verdict, shown.map((m) => m.title).join(" | "),
      shown.map((m) => m.url).join(" | "), closest.map((m) => m.title).join(" | ")]);
  });
  rows.push(["Screening aid only, not legal advice. No match found means no match in CPSC recalls, not that the item is safe."]);
  return rows.map((r) => r.map(csvCell).join(",")).join("\r\n");
}
function renderBatch(files, items) {
  const n = (v) => items.filter((r) => r.verdict === v).length;
  const pull = n("CONFIRMED"), check = n("POSSIBLE MATCH"), none = n("NO MATCH FOUND");
  const line = (r) => {
    if (r.verdict === "CONFIRMED") return `<p>Matched ${esc((r.recalls[0].codes || []).map((c) => c.replace(/^(model|upc):/, (x, k) => (k === "upc" ? "UPC " : "model "))).join(", "))}: <a href="${safeUrl(r.recalls[0].url)}" target="_blank" rel="noopener">${esc(r.recalls[0].title)}</a></p>`;
    if (r.verdict === "POSSIBLE MATCH") return `<p>Looks like: ${r.recalls.map((m) => `<a href="${safeUrl(m.url)}" target="_blank" rel="noopener">${esc(m.title)}</a>`).join("; ")}</p><p class="muted">Check its label in the scanner above.</p>`;
    if (r.closest && r.recalls.length) return `<p class="muted">Closest recalls to check (suggestions, not matches): ${r.recalls.map((m) => `<a href="${safeUrl(m.url)}" target="_blank" rel="noopener">${esc(m.title)}</a>`).join("; ")}</p>`;
    return `<p class="muted">No match in CPSC recalls. This does not mean the item is safe.</p>`;
  };
  $("#batchOut").innerHTML = `<p class="summary">${pull} to pull, ${check} to check with a label photo, ${none} with no match found.</p>
    <div class="report">${items.map((r, i) => `<div class="item"><img src="${batchUrls[i] || ""}" alt="Photo ${i + 1}">
      <div><span class="tag t-${key(r.verdict)}">${esc(STICKER[r.verdict])}</span>${line(r)}</div></div>`).join("")}</div>
    <p class="report-actions"><button type="button" class="secondary" id="csvBtn">Download the report (CSV)</button></p>`;
  $("#csvBtn").addEventListener("click", () => {
    const url = URL.createObjectURL(new Blob([batchCsv(files, items)], { type: "text/csv" }));
    const a = document.createElement("a"); a.href = url; a.download = "cleartolist-report.csv"; a.click();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
  });
}
async function runBatch(e) {
  e.preventDefault();
  batchError("");
  const files = [...$("#batchPhotos").files], invite = $("#invite").value.trim();
  if (!files.length) { batchError("Choose at least one photo."); $("#batchPhotos").focus(); return; }
  if (files.length > MAX_BATCH) { batchError(`Choose up to ${MAX_BATCH} photos. You chose ${files.length}.`); return; }
  const big = files.findIndex((f) => f.size > 8 * 1024 * 1024);
  if (big >= 0) { batchError(`Photo ${big + 1} is larger than 8 MB. Use a smaller photo.`); return; }
  const fd = new FormData();
  if (invite) fd.append("invite", invite);
  files.forEach((f) => fd.append("photos", f));
  $("#batchBtn").disabled = true;
  const out = $("#batchOut");
  const wait = (woke) => `<div class="panel loading"><span class="pulse" aria-hidden="true"></span><div><p><strong>Checking ${files.length} photo${files.length > 1 ? "s" : ""}...</strong></p><p class="muted">About 10 seconds a photo.</p>${woke ? `<p class="wake">The free server was asleep and is waking up. This can take up to a minute.</p>` : ""}</div></div>`;
  out.innerHTML = wait(false);
  try {
    const { job } = await api("/api/batches", { method: "POST", body: fd }, () => { out.innerHTML = wait(true); });
    const t0 = Date.now();
    while (Date.now() - t0 < 480000) {
      const s = await api(`/api/jobs/${encodeURIComponent(job)}`);
      if (s.state === "done") { renderBatch(files, s.result.items); return; }
      if (s.state === "unavailable") { out.innerHTML = unavailable(); return; }
      await sleep(3000);
    }
    out.innerHTML = unavailable();
  } catch (err) {
    out.innerHTML = "";
    if (err.status === 403 && /invite/i.test(err.message)) { batchError("That invite code isn't valid. Check it for typos, or ask for a new one."); }
    else if (err.status === 503 && /unavailable/i.test(err.message)) out.innerHTML = unavailable();
    else batchError(err.message);
  } finally {
    $("#batchBtn").disabled = false;
  }
}

// --- invite request ------------------------------------------------------------------------------------------
async function requestInvite(e) {
  e.preventDefault();
  const err = $("#emailErr"), ok = $("#inviteOk");
  err.hidden = true; ok.hidden = true;
  const email = $("#email").value.trim(), alerts = $("#alerts").checked;
  const categories = alerts ? [...document.querySelectorAll('#cats input[name="cat"]:checked')].map((c) => c.value) : [];
  const fail = (m) => { err.textContent = m; err.hidden = false; };
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) { fail("Enter your email address, like name@example.com."); $("#email").focus(); return; }
  if (alerts && !categories.length) { fail("Pick at least one category for recall alerts, or untick the alerts box."); return; }
  if (!$("#consent").checked) { fail("Tick the box to agree that we can store your email address for this."); $("#consent").focus(); return; }
  $("#inviteBtn").disabled = true;
  try {
    const d = await api("/api/invite-requests", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, consent: true, alerts, categories, website: $("#website").value }) });
    ok.textContent = d.message || "Thanks. We'll email you an invite code."; ok.hidden = false;
    $("#inviteForm").reset(); $("#cats").hidden = true;
  } catch (x) { fail(x.message); } finally { $("#inviteBtn").disabled = false; }
}

// --- lookup --------------------------------------------------------------------------------------------------
async function search(q) {
  const out = $("#lookupOut");
  q = q.trim();
  if (q.length < 2) { out.innerHTML = `<p class="lookup-msg">Type at least two characters.</p>`; return; }
  out.innerHTML = `<p class="lookup-msg">Searching...</p>`;
  try {
    const d = await api(`/api/lookup?q=${encodeURIComponent(q)}&children=${$("#kids").checked}`, {},
      () => { out.innerHTML = `<p class="lookup-msg">Waking up the free server. This can take up to a minute.</p>`; });
    if (!d.results.length) { out.innerHTML = `<p class="lookup-msg">No recalls found for "${esc(q)}". That doesn't mean the item is safe. Try the brand alone, or untick children's products only.</p>`; return; }
    out.innerHTML = d.results.map((x) => `<div class="row">
      ${x.image_urls[0] ? `<img src="${safeUrl(x.image_urls[0])}" alt="" loading="lazy">` : `<span class="noimg" aria-hidden="true"></span>`}
      <div><h3><a href="${safeUrl(x.url)}" target="_blank" rel="noopener">${esc(x.title)}</a></h3>
      <p>Recalled ${esc(fmtDate(x.date))}</p><p>${esc(x.hazards.join(" "))}</p></div></div>`).join("");
  } catch (e) { out.innerHTML = `<p class="lookup-msg">${esc(e.message)}</p>`; }
}

// --- examples ------------------------------------------------------------------------------------------------
const OUTCOME = {
  "confirmed the right recall": "The label confirmed the right recall.",
  "confirmed a different recall": "The label confirmed a different recall.",
  "right recall in the shortlist": "The right recall was among the closest recalls shown.",
  "right recall not in the shortlist": "The right recall wasn't among the closest recalls shown.",
  "missed": "Missed: the scan didn't show the right recall.",
};
async function gallery() {
  const g = $("#gallery");
  try {
    const items = (await api("/api/gallery")).filter((s) => s.item);
    if (!items.length) { g.innerHTML = `<p class="muted">No example scans yet.</p>`; return; }
    const selected = (s) => String(s.photo_source).startsWith("Selected");
    items.sort((a, b) => (selected(b) - selected(a)) || ((b.kind === "pair") - (a.kind === "pair")));
    g.innerHTML = items.map((s) => {
      const fin = s.final || s.item;
      const m = fin.recalls[0];
      return `<article class="example">
        <div class="shot"><img src="${safeUrl(s.photo_url)}" alt="Item photo">${sticker(fin.verdict, true)}</div>
        <div>
          ${s.kind === "pair" ? `<div class="label-thumb"><img src="${safeUrl(s.label_photo_url)}" alt="Label photo"><span>Item photo plus this label photo</span></div>` : ""}
          <h3>${m ? `<a href="${safeUrl(m.url)}" target="_blank" rel="noopener">${esc(m.title)}</a>` : "No recall shown"}</h3>
          ${m && m.code ? `<p>Matched ${esc(m.code.replace(/model:/g, "model ").replace(/upc:/g, "UPC "))}</p>` : ""}
          <p>${esc(s.truth ? OUTCOME[s.truth.outcome] || "" : "")}</p>
          <p class="kind">${selected(s) ? "Selected example: picked because its label confirmed a recall." : "Random sample from the test photos."} Simulated from held-out CPSC photos.</p>
        </div></article>`;
    }).join("");
  } catch (e) { g.innerHTML = `<p class="muted">Example scans couldn't be loaded.</p>`; }
}

document.addEventListener("DOMContentLoaded", () => {
  try { $("#invite").value = localStorage.getItem("ctl-invite") || ""; } catch (e) { /* storage unavailable */ }
  wireDrop("photo"); wireDrop("label");
  $("#invite").addEventListener("input", clearError);
  $("#scanForm").addEventListener("submit", runScan);
  $("#inviteForm").addEventListener("submit", requestInvite);
  ["#email", "#consent", "#alerts"].forEach((id) => $(id).addEventListener("input", () => { $("#emailErr").hidden = true; }));
  $("#alerts").addEventListener("change", () => { $("#cats").hidden = !$("#alerts").checked; });
  document.querySelectorAll(".sample").forEach((b) => b.addEventListener("click", () => runSample(b)));
  $("#batchPhotos").addEventListener("change", showBatchThumbs);
  $("#batchForm").addEventListener("submit", runBatch);
  $("#lookupForm").addEventListener("submit", (e) => { e.preventDefault(); search($("#q").value); });
  document.querySelectorAll(".chip").forEach((b) => b.addEventListener("click", () => { $("#q").value = b.dataset.q; search(b.dataset.q); }));
  gallery();
  api("/api/meta").then((m) => { recallCount = Number(m.recall_count) || 0; }).catch(() => {});
});

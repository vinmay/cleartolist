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
function formError(msg) { const e = $("#formErr"); e.textContent = msg; e.hidden = false; }
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

async function runScan(e) {
  e.preventDefault();
  clearError();
  const photo = $("#photo").files[0], label = $("#label").files[0], invite = $("#invite").value.trim();
  if (!photo) { const e = $("#photoErr"); e.textContent = "Add a photo of the item first."; e.hidden = false; $("#photo").focus(); return; }
  if (photo.size > 8 * 1024 * 1024 || (label && label.size > 8 * 1024 * 1024)) { formError("That photo is larger than 8 MB. Use a smaller photo or a screenshot of it."); return; }
  if (!invite) { inviteError("Enter your invite code. Photo scans are invite-only during the beta."); return; }
  try { localStorage.setItem("ctl-invite", invite); } catch (err) { /* storage unavailable */ }
  const fd = new FormData();
  fd.append("invite", invite); fd.append("photo", photo);
  if (label) fd.append("label", label);
  $("#scanBtn").disabled = true;
  let woke = false;
  show(loading(false));
  const onSlow = () => { woke = true; $("#result").innerHTML = loading(true); };
  try {
    const { job } = await api("/api/jobs", { method: "POST", body: fd }, onSlow);
    const t0 = Date.now();
    while (Date.now() - t0 < 180000) {
      const s = await api(`/api/jobs/${encodeURIComponent(job)}`);
      if (s.state === "done") { renderResult(s.result, Boolean(label)); return; }
      if (s.state === "unavailable") { show(unavailable()); return; }
      if (!woke) $("#result").innerHTML = loading(false);
      await sleep(2000);
    }
    show(unavailable());
  } catch (err) {
    $("#result").hidden = true;
    if (err.status === 403 && /invite/i.test(err.message)) inviteError("That invite code isn't valid. Check it for typos, or ask for a new one.");
    else if (err.status === 503 && /unavailable/i.test(err.message)) show(unavailable());
    else formError(err.message);
  } finally {
    $("#scanBtn").disabled = false;
  }
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
  $("#lookupForm").addEventListener("submit", (e) => { e.preventDefault(); search($("#q").value); });
  document.querySelectorAll(".chip").forEach((b) => b.addEventListener("click", () => { $("#q").value = b.dataset.q; search(b.dataset.q); }));
  gallery();
  api("/api/meta").then((m) => { recallCount = Number(m.recall_count) || 0; }).catch(() => {});
});

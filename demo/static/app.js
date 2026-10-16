"use strict";
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const safeUrl = (u) => (/^(https:\/\/|\/gallery\/[a-f0-9]{12}\.jpg$|\/static\/samples\/[a-z-]+\.jpg$)/.test(String(u || "")) ? esc(u) : "");
const key = (v) => ({"CONFIRMED": "CONFIRMED", "POSSIBLE MATCH": "POSSIBLE", "NO MATCH FOUND": "NOMATCH"}[v] || "NOMATCH");
const STICKER = {"CONFIRMED": "Recalled", "POSSIBLE MATCH": "Check the label", "NO MATCH FOUND": "No match found"};
const UNAVAILABLE = "Scanning is temporarily unavailable. Recall lookup still works.";
const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

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
  $("details.invite").open = true;
  const e = $("#inviteErr"); e.textContent = msg; e.hidden = false;
  $("#invite").setAttribute("aria-invalid", "true"); $("#invite").focus();
}

// --- results -------------------------------------------------------------------------------------------------
// Results render right below the scanner. While checking, the clicked control shows the state and the page stays
// put; when the result arrives it scrolls into view (instantly if the device asks for reduced motion).
function show(html, scroll = true) {
  const r = $("#result"); r.innerHTML = html; r.hidden = false;
  if (scroll) requestAnimationFrame(() => r.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "start" }));
}
function busy(btn, on, text = "Checking...") {
  if (!btn) return;
  if (on) { btn.dataset.label = btn.textContent; btn.textContent = text; btn.disabled = true; btn.setAttribute("aria-busy", "true"); btn.classList.add("is-busy"); }
  else { if (btn.dataset.label) btn.textContent = btn.dataset.label; btn.disabled = false; btn.removeAttribute("aria-busy"); btn.classList.remove("is-busy"); }
}
function loading(wake) {
  return `<div class="panel loading"><span class="pulse" aria-hidden="true"></span><div><p><strong>Checking for recalls...</strong></p>
    <p class="muted">This usually takes 10 to 30 seconds.</p>
    ${wake ? `<p class="wake">Just a moment, the site is starting up. This can take up to a minute.</p>` : ""}</div></div>`;
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
// Short recall card: brand + product heading, one-word hazard, date, affected models, what to do, small link.
const codeText = (codes) => (codes || []).map((c) => c.replace(/^(model|upc):/, (x, k) => (k === "upc" ? "UPC " : "model "))).join(", ");
function modelsText(m) {
  if (!m.models || !m.models.length) return "Not listed. Check the full notice.";
  return m.models.join(", ") + (m.models_more ? `, and ${m.models_more} more` : "");
}
function rcard(m, { compact = false, codes = [], owners = false } = {}) {
  const title = m.heading || m.title;
  return `<div class="rcard${compact ? " compact" : ""}">
    <h3 class="rcard-title">${esc(title)}</h3>
    <p><span class="hazard">${esc(m.hazard_tag || "Hazard")}</span>${m.date ? ` <span class="muted">Recalled ${esc(fmtDate(m.date))}</span>` : ""}</p>
    ${codes.length ? `<p><strong>Matched:</strong> ${esc(codeText(codes))}</p>` : ""}
    ${compact ? "" : `<p><strong>Affected models:</strong> ${esc(modelsText(m))}</p>${m.what_to_do ? `<p><strong>${owners ? "For owners" : "What to do"}:</strong> ${esc(m.what_to_do)}</p>` : ""}`}
    <p class="small"><a href="${safeUrl(m.url)}" target="_blank" rel="noopener">Full CPSC notice</a>${m.recall_id ? ` <button type="button" class="linkbtn" data-copy="${Number(m.recall_id)}">Copy link to this recall</button>` : ""}</p>
  </div>`;
}

// Anonymous usage events: a click becomes a daily total on the server. Nothing identifies who clicked.
function track(body) {
  fetch("/api/events", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).catch(() => {});
}

// "Copy link to this recall": the link carries the recall id only, never a photo.
async function copyRecallLink(btn) {
  const url = `${location.origin}/recall/${Number(btn.dataset.copy)}`;
  let ok = false;
  try { await navigator.clipboard.writeText(url); ok = true; } catch (e) {
    const t = document.createElement("textarea"); t.value = url; t.setAttribute("readonly", ""); t.className = "sr-only";
    document.body.append(t); t.select();
    try { ok = document.execCommand("copy"); } catch (x) { ok = false; }
    t.remove();
  }
  const label = btn.textContent;
  track({ event: "copy_link" });
  btn.textContent = ok ? "Link copied" : url;
  if (ok) setTimeout(() => { btn.textContent = label; }, 2000);
}

async function showSharedRecall() {
  const m = /^\/recall\/(\d+)$/.exec(location.pathname);
  if (!m) return;
  const box = $("#shared");
  try {
    const r = await api(`/api/recall/${m[1]}`);
    box.innerHTML = `<div class="panel shared-card"><p class="rcard-kicker">A recall someone shared with you</p>
      <div class="shared-body">${r.image_urls[0] ? `<img src="${safeUrl(r.image_urls[0])}" alt="CPSC recall photo">` : ""}${rcard(r)}</div>
      <p class="muted small">Have one of these, or something like it? Check it with the scanner below.</p></div>`;
  } catch (e) {
    box.innerHTML = `<div class="panel"><p>That recall link didn't work. Search for it on the Model number tab, or check an item with a photo.</p></div>`;
  }
  box.hidden = false;
}

function recallCard(m, photo) {
  return `<article class="recall">
    <div class="pair"><figure><img src="${safeUrl(m.image_urls[0])}" alt="CPSC recall photo"><figcaption>Recall photo</figcaption></figure>
      <figure><img src="${photo}" alt="Your item photo"><figcaption>Your photo</figcaption></figure></div>
    ${rcard(m, { codes: m.codes, owners: true })}
  </article>`;
}
function strip(recalls, photo) {
  return `<div class="strip"><figure><img src="${photo}" alt="Your item photo"><figcaption>Your photo</figcaption></figure>
    ${recalls.map((m) => `<figure><img src="${safeUrl(m.image_urls[0])}" alt="Recall photo">${rcard(m, { compact: true })}</figure>`).join("")}</div>`;
}
function renderResult(res, hadLabel) {
  const photo = urls.photo || "";
  const v = res.verdict;
  // The sticker carries the verdict; the text says what it means and what to do, without repeating it.
  const lead = {
    "CONFIRMED": "The model number or barcode on this item matches a CPSC recall.",
    "POSSIBLE MATCH": hadLabel ? "It looks like these recalled products, but the label didn't settle it. Compare the photos and check the recall pages." : "It looks like these recalled products. That alone doesn't mean it's recalled: the label decides.",
    "NO MATCH FOUND": res.closest ? "No match in CPSC recalls. No match isn't proof: check the model number with the manufacturer or CPSC before you buy or sell." : "No match in CPSC recalls. This doesn't mean the item is safe. Check cpsc.gov if unsure.",
  }[v];
  const notes = res.notes.map((n) => noteText(n, res)).filter(Boolean);
  const r = res.read || {};
  const thing = [r.brand, r.category].filter(Boolean).join(" ");
  const article = /^[aeiou]/i.test(thing) ? "an" : "a";
  const code = r.model_number ? `, model ${r.model_number}` : (r.upc ? `, barcode ${r.upc}` : "");
  const saw = thing ? `We saw: ${article} ${thing}${code}.` : "We couldn't tell what the item is from the photo.";
  let h = `<div class="panel verdict">
    <div class="shot"><img src="${photo}" alt="Your item photo">${sticker(v, false, true)}</div>
    <div class="verdict-copy"><p class="lead">${esc(lead)}</p>
      ${notes.map((n) => `<p>${esc(n)}</p>`).join("")}
      ${v === "CONFIRMED" && res.recalls[0] ? nextSteps(res.recalls[0]) : ""}
      <p class="muted">${esc(saw)}</p>
      ${v === "POSSIBLE MATCH" ? `<div class="inline-label"><label class="secondary file-btn">Add a label photo<input type="file" class="sr-only" id="inlineLabel" accept="image/jpeg,image/png,image/webp"></label>
        <p class="muted small">We'll check it together with the photo you already added.</p><p class="field-error" id="inlineErr" role="alert" hidden></p></div>` : ""}</div>`;
  if (v === "CONFIRMED") { h += res.recalls.map((m) => recallCard(m, photo)).join(""); lastRecall = res.recalls[0]; }
  if (v === "POSSIBLE MATCH") h += `<div class="recall wide"><div><h3>Compare these and add a label photo to confirm.</h3>${strip(res.recalls, photo)}
    <div class="ask"><p><strong>Buying, not holding it?</strong> Ask the seller for a photo of the label.</p>
    <button type="button" class="secondary" data-ask>Ask the seller for the label</button><span class="ask-msg" aria-live="polite"></span></div></div></div>`;
  if (v === "NO MATCH FOUND" && res.closest && res.recalls.length) {
    h += `<div class="recall wide"><div class="closest"><h3>Closest recalls to check</h3>
      <p class="muted">Suggestions, not matches: none of these met the bar for a possible match. If your item is one of these products, check its label against the recall page.</p>
      ${strip(res.recalls, photo)}</div></div>`;
  }
  h += reportButtons();
  report = { checkedAt: new Date(), items: [{ name: (lastItem && lastItem.file && lastItem.file.name) || (lastItem && lastItem.name) || "item photo", photo, result: res }] };
  h += invitePrompt();
  h += `<div class="helpful" data-tier="${esc(v)}"><span>Was this helpful?</span><button type="button" class="secondary" data-helpful="yes">Yes</button><button type="button" class="secondary" data-helpful="no">No</button></div>`;
  h += `</div>`;
  show(h);
  const inl = $("#inlineLabel");
  if (inl) inl.addEventListener("change", () => recheckWithLabel(inl));
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
  lastItem = { file: photo };
  lastWasScreenshot = photo.type === "image/png" || /screen ?shot|screenshot|scr[_-]/i.test(photo.name || "");
  busy($("#scanBtn"), true);
  show(loading(false), false);
  try {
    const { job } = await api("/api/jobs", { method: "POST", body: fd }, () => { $("#result").innerHTML = loading(true); });
    await pollJob(job, Boolean(label));
  } catch (err) { scanFailed(err); } finally { busy($("#scanBtn"), false); }
}

// What to do next after Recalled, for a seller or a buyer. The default comes from the upload: a screenshot
// (a PNG, or a file named like one) is most likely a listing, so it starts on buying.
let lastWasScreenshot = false, lastRecall = null;
const BUY_MSG = (m) => `Hi! Before I buy: this looks like it's covered by a U.S. product recall (${m.heading || m.title}, ${m.url}). Selling recalled products isn't allowed, so I'll pass. You may be able to get a refund or fix from the maker.`;
function nextSteps(m) {
  const buying = lastWasScreenshot;
  return `<div class="next" data-recall="${Number(m.recall_id) || 0}">
    <div class="seg" role="group" aria-label="Are you selling or buying?">
      <button type="button" data-role="sell" aria-pressed="${!buying}">I'm selling</button>
      <button type="button" data-role="buy" aria-pressed="${buying}">I'm buying</button>
    </div>
    <div class="next-sell"${buying ? " hidden" : ""}>
      <p class="next-h">Don't list it.</p>
      <p>${m.what_to_do ? `Follow the recall: ${esc(m.what_to_do)}` : "Follow the remedy in the CPSC notice below."}</p>
      <p class="muted">If there's no refund, repair or replacement, don't sell, donate or give it away. Before you throw it out, make it unusable (for example, cut straps or remove parts) so no one picks it up and uses it.</p>
    </div>
    <div class="next-buy"${buying ? "" : " hidden"}>
      <p class="next-h">Don't buy it. Let the seller know it's recalled.</p>
      <p><button type="button" class="secondary" data-tell="${Number(m.recall_id) || 0}">Copy a message to the seller</button><span class="ask-msg" aria-live="polite"></span></p>
    </div>
  </div>`;
}

// Inline label step on a Check the label result: the item photo already checked plus a new label photo.
let lastItem = null;
async function itemFile() {
  if (lastItem && lastItem.file) return lastItem.file;
  if (lastItem && lastItem.url) {
    const blob = await (await fetch(lastItem.url)).blob();
    return new File([blob], lastItem.name || "item.jpg", { type: blob.type || "image/jpeg" });
  }
  return null;
}
async function recheckWithLabel(input) {
  const label = input.files[0], btn = input.closest(".file-btn"), err = $("#inlineErr");
  if (!label) return;
  err.hidden = true;
  if (label.size > 8 * 1024 * 1024) { err.textContent = "That photo is larger than 8 MB. Use a smaller photo."; err.hidden = false; return; }
  const item = await itemFile();
  if (!item) { err.textContent = "Add the item photo again in the scanner above."; err.hidden = false; return; }
  const fd = new FormData(), invite = $("#invite").value.trim();
  if (invite) fd.append("invite", invite);
  fd.append("photo", item); fd.append("label", label);
  btn.classList.add("is-busy"); btn.firstChild.textContent = "Checking..."; input.disabled = true;
  try {
    const { job } = await api("/api/jobs", { method: "POST", body: fd });
    await pollJob(job, true);
  } catch (x) {
    btn.classList.remove("is-busy"); btn.firstChild.textContent = "Add a label photo"; input.disabled = false;
    err.textContent = x.message; err.hidden = false;
  }
}

async function runSample(btn) {
  clearError();
  if (urls.photo && urls.photo.startsWith("blob:")) URL.revokeObjectURL(urls.photo);
  urls.photo = btn.dataset.photo;
  lastItem = { url: btn.dataset.photo, name: btn.dataset.sample + ".jpg" };
  lastWasScreenshot = false;
  document.querySelectorAll(".sample").forEach((b) => { b.disabled = true; });
  btn.classList.add("is-loading"); btn.setAttribute("aria-busy", "true");
  show(loading(false), false);
  try {
    const { job } = await api(`/api/samples/${encodeURIComponent(btn.dataset.sample)}`, { method: "POST" }, () => { $("#result").innerHTML = loading(true); });
    await pollJob(job, btn.dataset.sample === "recalled");
  } catch (err) { scanFailed(err); } finally {
    document.querySelectorAll(".sample").forEach((b) => { b.disabled = false; });
    btn.classList.remove("is-loading"); btn.removeAttribute("aria-busy");
  }
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
// --- reports: built in the browser from the result on screen; nothing new is stored on the server ------------
let report = null;  // { checkedAt: Date, items: [{ name, photo, result }] }
function reportRow(it) {
  const r = it.result, matched = r.verdict === "CONFIRMED" ? r.recalls.slice(0, 1) : [];
  const looks = r.verdict === "POSSIBLE MATCH" ? r.recalls : [];
  const closest = r.verdict === "NO MATCH FOUND" && r.closest ? r.recalls : [];
  return { matched, looks, closest };
}
function reportCsv() {
  const when = report.checkedAt.toLocaleString("en-US");
  const rows = [["photo", "verdict", "matched recall", "recall date", "recall link", "looks like (check the label)",
    "closest recalls to check", "checked at"]];
  report.items.forEach((it) => {
    const { matched, looks, closest } = reportRow(it), m = matched[0];
    rows.push([it.name, STICKER[it.result.verdict], m ? (m.heading || m.title) : "", m ? m.date : "", m ? m.url : "",
      looks.map((x) => x.heading || x.title).join(" | "), closest.map((x) => x.heading || x.title).join(" | "), when]);
  });
  rows.push(["Screening aid only, not legal advice. No match found means no match in CPSC recalls, not that the item is safe."]);
  return rows.map((r) => r.map(csvCell).join(",")).join("\r\n");
}
function downloadCsv() {
  const url = URL.createObjectURL(new Blob([reportCsv()], { type: "text/csv" }));
  const a = document.createElement("a"); a.href = url; a.download = "cleartolist-report.csv"; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}
function printReport() {
  const when = report.checkedAt.toLocaleString("en-US", { dateStyle: "long", timeStyle: "short" });
  $("#printReport").innerHTML = `<h1>ClearToList report</h1><p>Checked ${esc(when)}. ${report.items.length} item${report.items.length > 1 ? "s" : ""}.</p>
    <table><thead><tr><th>Photo</th><th>Result</th><th>Recall</th><th>Recall date</th></tr></thead><tbody>
    ${report.items.map((it) => {
      const { matched, looks, closest } = reportRow(it), m = matched[0];
      const recall = m ? `${esc(m.heading || m.title)}<br><span class="url">${esc(m.url)}</span>`
        : looks.length ? `Looks like: ${looks.map((x) => esc(x.heading || x.title)).join("; ")}. Check the label.`
        : closest.length ? `Closest to check (not matches): ${closest.map((x) => esc(x.heading || x.title)).join("; ")}` : "None";
      return `<tr><td><img src="${it.photo}" alt=""><div class="name">${esc(it.name)}</div></td><td><strong>${esc(STICKER[it.result.verdict])}</strong></td>
        <td>${recall}</td><td>${m ? esc(fmtDate(m.date)) : ""}</td></tr>`;
    }).join("")}</tbody></table>
    <p class="fine">Screening aid only, not legal advice. "No match found" means no match in U.S. CPSC recalls, not that the item is safe. Check cpsc.gov.</p>`;
  window.print();
}
// A short invite ask under results, only for people without a code (the daily limit is what a code skips).
function invitePrompt() {
  if ($("#invite").value.trim()) return "";
  return `<p class="invite-prompt">Checking a whole haul? <a href="#invite-request">Get an invite code</a> to skip the daily limit.</p>`;
}

function reportButtons() {
  return `<div class="report-actions"><button type="button" class="secondary" data-report="csv">Download report (CSV)</button>
    <button type="button" class="secondary" data-report="print">Print or save as PDF</button></div>`;
}

function renderBatch(files, items) {
  const n = (v) => items.filter((r) => r.verdict === v).length;
  const pull = n("CONFIRMED"), check = n("POSSIBLE MATCH"), none = n("NO MATCH FOUND");
  const names = (rs) => rs.map((m) => `${esc(m.heading || m.title)} (<a href="${safeUrl(m.url)}" target="_blank" rel="noopener">notice</a>)`).join("; ");
  const line = (r) => {
    if (r.verdict === "CONFIRMED") return `<p>Matched ${esc(codeText(r.recalls[0].codes))}: ${names(r.recalls.slice(0, 1))}</p>`;
    if (r.verdict === "POSSIBLE MATCH") return `<p>Looks like: ${names(r.recalls)}</p><p class="muted">Check its label on the Photo tab.</p>`;
    if (r.closest && r.recalls.length) return `<p class="muted">Closest recalls to check (suggestions, not matches): ${names(r.recalls)}</p>`;
    return `<p class="muted">No match in CPSC recalls. This does not mean the item is safe.</p>`;
  };
  show(`<div class="panel batch-report"><p class="summary">${pull} to pull, ${check} to check with a label photo, ${none} with no match found.</p>
    <div class="report">${items.map((r, i) => `<div class="item"><img src="${batchUrls[i] || ""}" alt="Photo ${i + 1}">
      <div><span class="tag t-${key(r.verdict)}">${esc(STICKER[r.verdict])}</span>${line(r)}</div></div>`).join("")}</div>
    ${reportButtons()}${invitePrompt()}</div>`);
  report = { checkedAt: new Date(), items: items.map((r, i) => ({ name: files[i] ? files[i].name : `Photo ${i + 1}`, photo: batchUrls[i] || "", result: r })) };
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
  busy($("#scanBtn"), true);
  const out = $("#result");
  const wait = (woke) => `<div class="panel loading"><span class="pulse" aria-hidden="true"></span><div><p><strong>Checking ${files.length} photo${files.length > 1 ? "s" : ""}...</strong></p><p class="muted">About 10 seconds a photo.</p>${woke ? `<p class="wake">Just a moment, the site is starting up. This can take up to a minute.</p>` : ""}</div></div>`;
  show(wait(false), false);
  try {
    const { job } = await api("/api/batches", { method: "POST", body: fd }, () => { out.innerHTML = wait(true); });
    const t0 = Date.now();
    while (Date.now() - t0 < 480000) {
      const s = await api(`/api/jobs/${encodeURIComponent(job)}`);
      if (s.state === "done") { renderBatch(files, s.result.items); return; }
      if (s.state === "unavailable") { show(unavailable()); return; }
      await sleep(3000);
    }
    show(unavailable());
  } catch (err) {
    out.innerHTML = "";
    out.hidden = true;
    if (err.status === 403 && /invite/i.test(err.message)) inviteError("That invite code isn't valid. Check it for typos, or leave it empty to use today's free checks.");
    else if (err.status === 503 && /unavailable/i.test(err.message)) show(unavailable());
    else batchError(err.message);
  } finally {
    busy($("#scanBtn"), false);
  }
}

// --- slim bar with "Check another item", shown only once the scanner has scrolled out of view ------------------
function setupMiniBar() {
  const bar = $("#miniBar"), scanner = $("#scanForm");
  if (!bar || !scanner || !("IntersectionObserver" in window)) return;
  new IntersectionObserver(([e]) => {
    const above = !e.isIntersecting && e.boundingClientRect.bottom < 0;  // scrolled past it, not before it
    bar.hidden = !above; document.body.classList.toggle("has-minibar", above);
  }, { threshold: 0 }).observe(scanner);
  $("#miniCheck").addEventListener("click", () => {
    $("#scanner").scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "start" });
    const focusTab = () => { const tab = $("[role=tab][aria-selected=true]"); if (tab) tab.focus({ preventScroll: true }); };
    if (reduceMotion || !("onscrollend" in window)) setTimeout(focusTab, reduceMotion ? 0 : 600);
    else window.addEventListener("scrollend", focusTab, { once: true });
  });
}

// --- collapsed label step and photo policy ----------------------------------------------------------------
function openLabelStep() {
  $("#labelZone").hidden = false; $("#addLabelStep").hidden = true; $("#addLabelStep").setAttribute("aria-expanded", "true");
}
function closeLabelStep() {  // anything you open, you can close: this also drops a chosen label photo
  $("#label").value = ""; showPreview("label");
  $("#labelZone").hidden = true; $("#addLabelStep").hidden = false; $("#addLabelStep").setAttribute("aria-expanded", "false");
  $("#addLabelStep").focus();
}

// --- tabs: one item or a whole haul ---------------------------------------------------------------------------
const TABS = [["#tabOne", "#panelOne", "Check for recalls"], ["#tabHaul", "#panelHaul", "Check all photos"],
  ["#tabModel", "#panelModel", "Search recalls"]];
const currentTab = () => TABS.find(([t]) => $(t).getAttribute("aria-selected") === "true")[0];
const haulTab = () => currentTab() === "#tabHaul";
function selectTab(tab) {
  const id = "#" + tab.id;
  TABS.forEach(([t, panel, label]) => {
    const on = t === id;
    $(t).setAttribute("aria-selected", String(on)); $(t).tabIndex = on ? 0 : -1; $(panel).hidden = !on;
    if (on) $("#scanBtn").textContent = label;
  });
  const model = id === "#tabModel";
  $("#inviteBox").hidden = model; $("#privacyLine").hidden = model; $("#policy").hidden = true;  // photo-only details
  clearError(); batchError(""); $("#qErr").hidden = true;
}
function setupTabs() {
  const tabs = TABS.map(([t]) => $(t));
  tabs.forEach((t, i) => {
    t.addEventListener("click", () => selectTab(t));
    t.addEventListener("keydown", (e) => {
      if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
      const next = tabs[(i + (e.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length];
      selectTab(next); next.focus(); e.preventDefault();
    });
  });
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
async function search(q, btn) {
  q = (q || "").trim();
  if (q.length < 2) { const e = $("#qErr"); e.textContent = "Type a model number, UPC or brand (at least two characters)."; e.hidden = false; $("#q").focus(); return; }
  $("#qErr").hidden = true;
  busy(btn, true, "Searching...");
  try {
    const d = await api(`/api/lookup?q=${encodeURIComponent(q)}`);
    if (!d.results.length) {
      show(`<div class="panel"><p class="lead">No recalls found for "${esc(q)}".</p><p class="muted">That doesn't mean the item is safe. Check the number on the label, try the brand alone, or take a photo instead.</p></div>`);
      return;
    }
    show(`<div class="panel"><p class="summary">${d.results.length === 1 ? "1 recall" : `${d.results.length} recalls`} for "${esc(q)}"</p>
      <div class="lookup-out">${d.results.map((x) => `<div class="row">
      ${x.image_urls[0] ? `<img src="${safeUrl(x.image_urls[0])}" alt="" loading="lazy">` : `<span class="noimg" aria-hidden="true"></span>`}
      ${rcard(x, { compact: true })}</div>`).join("")}</div></div>`);
  } catch (e) { show(`<div class="panel"><p>${esc(e.message)}</p></div>`); } finally { busy(btn, false); }
}

// --- examples ------------------------------------------------------------------------------------------------
async function gallery() {
  const g = $("#gallery");
  try {
    const set = g.dataset.set || "curated";
    const items = (await api(`/api/gallery?set=${encodeURIComponent(set)}`)).filter((s) => s.item);
    if (!items.length) { g.innerHTML = `<p class="muted">No example scans yet.</p>`; return; }
    g.innerHTML = items.map((s) => {
      const fin = s.final || s.item;
      const matched = fin.verdict !== "NO MATCH FOUND";  // a no-match card never presents a recall as its match
      const m = matched ? fin.recalls[0] : null;
      const suggestions = !matched && fin.recalls.length ? `<p class="muted">Closest recalls to check (suggestions, not matches): ${fin.recalls.map((x) => esc(x.heading || x.title)).join("; ")}</p>` : "";
      return `<article class="example${s.lane === "buying" ? " buying" : ""}">
        <div class="shot"><img src="${safeUrl(s.photo_url)}" alt="Item photo">${sticker(fin.verdict, true)}</div>
        <div>
          ${s.lane === "buying" ? `<p class="lane">Buying: a listing photo</p>` : ""}
          ${s.kind === "pair" ? `<div class="label-thumb"><img src="${safeUrl(s.label_photo_url)}" alt="Label photo"><span>With a photo of its label</span></div>` : ""}
          ${m ? rcard(m, { compact: true, codes: m.code ? m.code.split(", ") : [] }) : `<h3 class="rcard-title">No match in CPSC recalls</h3>${suggestions}`}
          ${s.lane === "buying" ? `<p class="muted small">Then ask the seller for a photo of the label before you pay.</p>` : ""}
          ${s.credit ? `<p class="muted small">${esc(s.credit)}</p>` : ""}
        </div></article>`;
    }).join("");
  } catch (e) { g.innerHTML = `<p class="muted">Example scans couldn't be loaded.</p>`; }
}

const ASK_SELLER = "Hi! Before I buy, could you send a photo of the label with the model number and date code?";
async function copyText(text) {
  try { await navigator.clipboard.writeText(text); return true; } catch (e) {
    const t = document.createElement("textarea"); t.value = text; t.setAttribute("readonly", ""); t.className = "sr-only";
    document.body.append(t); t.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch (x) { ok = false; }
    t.remove();
    return ok;
  }
}
document.addEventListener("click", async (e) => {
  const b = e.target.closest("[data-copy]");
  if (b) copyRecallLink(b);
  const rep = e.target.closest("[data-report]");
  if (rep && report) (rep.dataset.report === "csv" ? downloadCsv : printReport)();
  const role = e.target.closest(".seg [data-role]");
  if (role) {
    const box = role.closest(".next"), buy = role.dataset.role === "buy";
    box.querySelectorAll(".seg [data-role]").forEach((b) => b.setAttribute("aria-pressed", String(b === role)));
    box.querySelector(".next-sell").hidden = buy; box.querySelector(".next-buy").hidden = !buy;
  }
  const tell = e.target.closest("[data-tell]");
  if (tell && lastRecall) {
    const msg = BUY_MSG(lastRecall), ok = await copyText(msg);
    track({ event: "ask_seller" });
    tell.nextElementSibling.textContent = ok ? " Copied. Paste it into your message to the seller." : ` Copy this: "${msg}"`;
  }
  const h = e.target.closest("[data-helpful]");
  if (h) {
    const box = h.closest(".helpful");
    track({ event: "helpful", answer: h.dataset.helpful, tier: box.dataset.tier });
    box.innerHTML = "<span>Thanks for the feedback.</span>";
  }
  const a = e.target.closest("[data-ask]");
  if (a) {
    const ok = await copyText(ASK_SELLER);
    track({ event: "ask_seller" });
    a.nextElementSibling.textContent = ok ? ` Copied: "${ASK_SELLER}"` : ` Copy this: "${ASK_SELLER}"`;
  }
});

document.addEventListener("DOMContentLoaded", () => {
  if ($("#gallery")) gallery();
  if ($("#shared")) showSharedRecall();
  if (!$("#scanForm")) return;  // pages without the scanner only need the gallery
  try { $("#invite").value = localStorage.getItem("ctl-invite") || ""; } catch (e) { /* storage unavailable */ }
  wireDrop("photo"); wireDrop("label");
  // "Use the camera" opens the camera directly; the drop zones open the phone's normal chooser (library or camera).
  document.querySelectorAll("input[data-for]").forEach((cam) => cam.addEventListener("change", () => {
    if (!cam.files.length) return;
    const dt = new DataTransfer(); dt.items.add(cam.files[0]);
    const target = $("#" + cam.dataset.for); target.files = dt.files; showPreview(cam.dataset.for); cam.value = "";
  }));
  $("#invite").addEventListener("input", clearError);
  $("#scanForm").addEventListener("submit", (e) => {
    if (currentTab() === "#tabModel") { e.preventDefault(); return search($("#q").value, $("#scanBtn")); }
    return haulTab() ? runBatch(e) : runScan(e);
  });
  $("#inviteForm").addEventListener("submit", requestInvite);
  ["#email", "#consent", "#alerts"].forEach((id) => $(id).addEventListener("input", () => { $("#emailErr").hidden = true; }));
  $("#alerts").addEventListener("change", () => { $("#cats").hidden = !$("#alerts").checked; });
  document.querySelectorAll(".sample").forEach((b) => b.addEventListener("click", () => runSample(b)));
  $("#batchPhotos").addEventListener("change", showBatchThumbs);
  setupTabs();
  setupMiniBar();
  window.addEventListener("afterprint", () => { $("#printReport").innerHTML = ""; });
  $("#removeLabel").addEventListener("click", closeLabelStep);
  $("#addLabelStep").addEventListener("click", () => { openLabelStep(); $("#dropLabel").scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "nearest" }); $("#label").focus(); });
  $("#policyLink").addEventListener("click", (e) => {
    e.preventDefault();
    const box = $("#policy"); box.hidden = !box.hidden; $("#policyLink").setAttribute("aria-expanded", String(!box.hidden));
  });
  document.querySelectorAll(".chip").forEach((b) => b.addEventListener("click", () => { $("#q").value = b.dataset.q; search(b.dataset.q, b); }));
  api("/api/meta").then((m) => {
    if (m.invite_requests === false) {
      $("#inviteBtn").disabled = true;
      const ok = $("#inviteOk"); ok.textContent = "Invite requests open soon. Free scans and the recall lookup work now."; ok.hidden = false;
    }
  }).catch(() => {});
});

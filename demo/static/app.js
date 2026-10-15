"use strict";
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const safeUrl = (u) => (/^(https:\/\/|\/gallery\/[a-f0-9]{12}\.jpg$)/.test(String(u || "")) ? esc(u) : "#");
const cls = (v) => "v-" + ({"NO MATCH FOUND": "NOMATCH", "POSSIBLE MATCH": "POSSIBLE"}[v] || v);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Free hosting sleeps when idle: if a request is slow, say so instead of looking broken.
async function api(url, opts = {}) {
  const t = setTimeout(() => { $("#wake").hidden = false; }, 3000);
  const ctrl = new AbortController();
  const kill = setTimeout(() => ctrl.abort(), 90000);
  try {
    const r = await fetch(url, { ...opts, signal: ctrl.signal });
    let body = {};
    try { body = await r.json(); } catch (e) { /* non-JSON error page */ }
    if (!r.ok) throw new Error(body.error || "The server couldn't handle that request. Please try again.");
    return body;
  } catch (e) {
    if (e.name === "AbortError") throw new Error("The server took too long to respond. Please try again.");
    throw e;
  } finally {
    clearTimeout(t); clearTimeout(kill); $("#wake").hidden = true;
  }
}

// --- scan -------------------------------------------------------------------------------------------------
const HEAD = {
  "CONFIRMED": ["Recalled: do not list", "A model number or UPC on the item matches a CPSC recall."],
  "POSSIBLE MATCH": ["Possible match: check the label", "Your item looks similar to these recalled products. That alone doesn't mean it's recalled: the model number on its label decides."],
  "NO MATCH FOUND": ["No match in CPSC recalls", "This does not mean the item is safe. Recalls without photos or unusual items can be missed."],
};
function noteText(n, res) {
  const codes = [res.label_read && res.label_read.model_number, res.label_read && res.label_read.upc].filter(Boolean).join(", ");
  return {
    label_ask: "Next: photograph the label (model number, date code or barcode; look under the seat, on the frame or on the base), add it above and check again.",
    label_no_code: "No model number or UPC could be read on the label. Try a sharper, closer photo, or compare the label with the recall pages by hand.",
    label_code_unlisted: `Read ${codes || "a code"} on the label. That isn't listed in any CPSC recall in this database. Check the recall pages if the item still looks like one of them.`,
    label_other_recall: "The label matches a different recall from the photo shortlist; the label is the stronger evidence.",
    car_seat: "Car seats are regulated by NHTSA, not CPSC, and aren't covered here. Check the seat's label at nhtsa.gov/recalls.",
  }[n] || "";
}
let photoUrl = null;
function renderResult(res, hadLabel) {
  const [title, sub] = HEAD[res.verdict];
  let h = `<div class="banner ${cls(res.verdict)}">${esc(title)}<small>${esc(sub)}</small></div>`;
  if (res.verdict === "CONFIRMED") {
    const m = res.recalls[0];
    h += `<div class="result">${m.image_urls[0] ? `<img src="${safeUrl(m.image_urls[0])}" alt="">` : "<div></div>"}
      <div><h3><a href="${safeUrl(m.url)}" target="_blank" rel="noopener">${esc(m.title)}</a></h3>
      <div class="meta">Matched: ${esc(m.codes.join(", "))}</div>
      <div class="meta">Hazard: ${esc(m.hazards.join("; ") || "see recall")}</div>
      <div class="meta">Remedy: ${esc(m.remedies.join("; ") || "see recall")}</div></div></div>`;
  }
  if (res.verdict === "POSSIBLE MATCH") {
    h += `<div class="short"><figure><img src="${photoUrl}" alt="Your photo"><figcaption class="meta">Your photo</figcaption></figure>
      ${res.recalls.map((m) => `<figure><img src="${safeUrl(m.image_urls[0])}" alt="Recall photo"><figcaption><a href="${safeUrl(m.url)}" target="_blank" rel="noopener">${esc(m.title)}</a></figcaption></figure>`).join("")}</div>`;
  }
  const notes = res.notes.filter((n) => !(n === "label_ask" && hadLabel));
  if (res.verdict === "POSSIBLE MATCH" && !hadLabel && !notes.includes("label_ask")) notes.unshift("label_ask");
  h += notes.map((n) => `<p class="meta">${esc(noteText(n, res))}</p>`).join("");
  const r = res.read || {};
  h += `<p class="meta">Read from the photo: ${esc([r.category, r.brand, r.model_number, r.upc].filter(Boolean).join(", ") || "nothing legible")}. Scan time ${esc(res.seconds)} s.</p>`;
  $("#scanOut").innerHTML = h;
}

async function runScan(e) {
  e.preventDefault();
  const photo = $("#photo").files[0], label = $("#label").files[0], invite = $("#invite").value.trim();
  if (!photo) return;
  try { localStorage.setItem("ctl-invite", invite); } catch (err) { /* storage unavailable */ }
  if (photoUrl) URL.revokeObjectURL(photoUrl);
  photoUrl = URL.createObjectURL(photo);
  const fd = new FormData();
  fd.append("invite", invite);
  fd.append("photo", photo);
  if (label) fd.append("label", label);
  $("#scanBtn").disabled = true;
  $("#scanOut").innerHTML = `<p class="meta"><span class="spinner"></span>Uploading...</p>`;
  try {
    const { job } = await api("/api/jobs", { method: "POST", body: fd });
    const t0 = Date.now();
    while (Date.now() - t0 < 180000) {
      const s = await api(`/api/jobs/${encodeURIComponent(job)}`);
      if (s.state === "done") { renderResult(s.result, Boolean(label)); return; }
      if (s.state === "unavailable") throw new Error(s.message);
      $("#scanOut").innerHTML = `<p class="meta"><span class="spinner"></span>${s.state === "queued" ? "Waiting for the scanner..." : "Reading the photo and checking recalls (usually 10-30 s)..."}</p>`;
      await sleep(2000);
    }
    throw new Error("Scanning is temporarily unavailable. Please try again later. The recall lookup still works.");
  } catch (err) {
    $("#scanOut").innerHTML = `<div class="err">${esc(err.message)}</div>`;
  } finally {
    $("#scanBtn").disabled = false;
  }
}

// --- lookup -----------------------------------------------------------------------------------------------
async function search(q) {
  $("#out").innerHTML = `<p class="meta">Searching...</p>`;
  try {
    const d = await api(`/api/lookup?q=${encodeURIComponent(q)}&children=${$("#kids").checked}`);
    if (!d.results.length) { $("#out").innerHTML = `<p class="meta">No recalls found for "${esc(q)}". That does not mean the item is safe; try the brand alone, or check cpsc.gov.</p>`; return; }
    $("#out").innerHTML = d.results.map((x) => `<div class="result">
      ${x.image_urls[0] ? `<img src="${safeUrl(x.image_urls[0])}" alt="" loading="lazy">` : "<div></div>"}
      <div><h3><a href="${safeUrl(x.url)}" target="_blank" rel="noopener">${esc(x.title)}</a></h3>
      <div class="meta">${esc(x.date)} · ${esc(x.hazards.join("; "))}</div>
      <div class="meta">Remedy: ${esc(x.remedies.join("; ") || "see recall")}</div>
      <span class="tag">${esc(x.match)}</span><span class="tag">${esc(String(x.category).replaceAll("_", " "))}</span></div></div>`).join("");
  } catch (e) { $("#out").innerHTML = `<div class="err">${esc(e.message)}</div>`; }
}

// --- gallery ----------------------------------------------------------------------------------------------
function step(title, photo, c) {
  const thumbs = [`<img src="${safeUrl(photo)}" alt="Scanned photo">`].concat(c.recalls.map((m) => `<img src="${safeUrl(m.image_url)}" alt="Recall photo">`));
  return `<div class="step"><h4>${esc(title)} <span class="pill ${cls(c.verdict)}">${esc(c.verdict)}</span></h4>
    <div class="thumbs">${thumbs.join("")}</div>
    ${c.recalls.map((m) => `<div><a href="${safeUrl(m.url)}" target="_blank" rel="noopener">${esc(m.title)}</a>${m.code ? ` <strong>(${esc(m.code)} matches)</strong>` : ""}</div>`).join("")}
    <div class="meta">Read: ${esc(c.read_summary || "nothing legible")}</div>${c.note ? `<div class="meta">${esc(c.note)}</div>` : ""}</div>`;
}
async function gallery() {
  try {
    const items = await api("/api/gallery");
    if (!items.length) { $("#gallery").innerHTML = `<p class="meta">No example scans yet.</p>`; return; }
    const rank = (s) => (String(s.photo_source).startsWith("Selected") ? 0 : s.kind === "pair" ? 1 : 2);
    items.sort((a, b) => rank(a) - rank(b));
    $("#gallery").innerHTML = items.filter((s) => s.item).map((s) => {
      const fin = s.final || s.item;
      return `<div class="scan"><div class="v ${cls(fin.verdict)}">${esc(fin.verdict)}${s.kind === "pair" ? " after the label photo" : ""}</div>
      ${step(s.kind === "pair" ? "1. Item photo" : "Item photo", s.photo_url, s.item)}
      ${s.kind === "pair" ? step("2. Label photo", s.label_photo_url, s.final) : ""}
      <div class="body"><div class="meta">${esc(s.photo_source)}</div>
      ${s.truth ? `<div class="truth">Photos actually from: <a href="${safeUrl(s.truth.url)}" target="_blank" rel="noopener">${esc(s.truth.title)}</a>. Result: <strong>${esc(s.truth.outcome)}</strong>.</div>` : ""}</div></div>`;
    }).join("");
  } catch (e) { $("#gallery").innerHTML = `<p class="meta">Examples couldn't be loaded.</p>`; }
}

document.addEventListener("DOMContentLoaded", () => {
  try { $("#invite").value = localStorage.getItem("ctl-invite") || ""; } catch (e) { /* storage unavailable */ }
  $("#scanForm").addEventListener("submit", runScan);
  $("#f").addEventListener("submit", (e) => { e.preventDefault(); search($("#q").value); });
  document.querySelectorAll("[data-q]").forEach((a) => a.addEventListener("click", (e) => { e.preventDefault(); $("#q").value = a.dataset.q; search(a.dataset.q); }));
  gallery();
  api("/api/meta").then((m) => {
    if (m.recall_count) $("#meta").textContent = `Recall data: U.S. Consumer Product Safety Commission (saferproducts.gov API). ${m.recall_count} recalls, newest ${m.latest_recall_date}. Refreshed nightly. Not affiliated with or endorsed by CPSC.`;
  }).catch(() => {});
});

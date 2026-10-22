/* Éditeur de bulletin - client (vanilla JS, no build step). */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const S = {
  meta: null,
  draft: null,
  sections: null,      // GeoJSON of the reporting points with levels
  secByName: {},
  map: null, layers: {},
  frameScroll: 0,
  previewTimer: null,
};

// ------------------------------------------------------------------ utils
function toast(msg, error = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "show" + (error ? " error" : "");
  clearTimeout(t._h);
  t._h = setTimeout(() => (t.className = ""), error ? 6000 : 2500);
}

async function api(method, url, body, isForm = false) {
  const opt = { method, headers: {} };
  if (body !== undefined) {
    if (isForm) opt.body = body;
    else { opt.body = JSON.stringify(body); opt.headers["Content-Type"] = "application/json"; }
  }
  const r = await fetch(url, opt);
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch (e) { /* not json */ }
    const err = new Error(msg); err.status = r.status;
    throw err;
  }
  return r.json();
}

const levelColor = (l) => (l >= 0 && l <= 3 ? S.meta.levels.colors[l] : S.meta.levels.nodata);
const levelLabel = (l) => (l >= 0 && l <= 3 ? S.meta.levels.labels[l] : "Pas de données");
const base = () => `/api/bulletins/${S.draft.issue}`;
const LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";
const fmtDate = (iso) => new Date(iso + (iso.length === 10 ? "T00:00" : "")).toLocaleDateString("fr-FR", { weekday: "long", day: "numeric", month: "long", year: "numeric" });
const fmtTime = (iso) => iso ? new Date(iso).toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "short" }) : "";

function busy(on, msg = "Calcul de l'aperçu…") {
  $("#overlay").classList.toggle("show", on);
  $("#overlayMsg").textContent = msg;
  $("#status .dot")?.classList.toggle("busy", on);
}

// ------------------------------------------------------------------ draft
async function patch(body, { preview = true } = {}) {
  try {
    S.draft = await api("PATCH", base(), body);
    renderStatus();
    if (preview) schedulePreview();
  } catch (e) { toast("Enregistrement impossible : " + e.message, true); }
}

function renderStatus() {
  const d = S.draft;
  if (!d) { $("#status").innerHTML = ""; return; }
  const exp = d.exported && d.exported.rev === d.rev ? " · PDF à jour" : d.exported ? " · modifié depuis le dernier PDF" : "";
  $("#status").innerHTML = `<span class="dot ok"></span>Bulletin <b>${esc(d.number)}</b> du ${esc(fmtDate(d.issue))}
    · rév. ${d.rev} enregistrée ${esc(fmtTime(d.updated))} par ${esc(d.updated_by)}${exp}`;
}

async function openBulletin(issue, reset = false) {
  if (!issue) return;
  busy(true, "Préparation du bulletin…");
  try {
    S.draft = await api("POST", `/api/bulletins/${issue}${reset ? "?reset=true" : ""}`);
  } catch (e) {
    busy(false);
    toast(e.status === 404 ? "Aucune donnée FloodProofs pour cette date : " + e.message : e.message, true);
    return;
  }
  history.replaceState(null, "", "#" + issue);
  $("#issue").value = issue;
  document.body.classList.remove("noDraft");
  renderStatus();
  await loadSections();
  renderAll();
  refreshMeta();
  refreshPreview();
}

async function loadSections() {
  try {
    S.sections = await api("GET", base() + "/sections");
  } catch (e) { toast(e.message, true); return; }
  S.secByName = Object.fromEntries(S.sections.features.map((f) => [f.properties.name, f.properties]));
  $("#secList").innerHTML = S.sections.features
    .map((f) => f.properties)
    .sort((a, b) => a.label.localeCompare(b.label))
    .map((p) => `<option value="${esc(p.name)}">${esc(p.label)} - ${esc(levelLabel(p.level))}</option>`).join("");
  renderRunSummary();
  updateMap();
}

async function refreshMeta() {
  S.meta = await api("GET", "/api/meta");
  renderRecent();
  renderBulletinList();
}

function renderAll() {
  renderParams();
  renderZones();
  renderLayout();
  renderImages();
  renderExport();
}

// ---------------------------------------------------------------- preview
function schedulePreview(delay = 350) {
  clearTimeout(S.previewTimer);
  S.previewTimer = setTimeout(refreshPreview, delay);
}

function refreshPreview() {
  if (!S.draft) return;
  const f = $("#frame");
  try { S.frameScroll = f.contentWindow.scrollY || S.frameScroll; } catch (e) { /* ignore */ }
  busy(true);
  f.src = `${base()}/preview?rev=${S.draft.rev}&t=${Date.now()}`;
}

function onFrameLoad() {
  const f = $("#frame");
  const doc = f.contentDocument;
  if (!doc || !S.draft || !doc.body) { busy(false); return; }
  f.contentWindow.scrollTo(0, S.frameScroll);
  busy(false);
  // page labels
  $$(".page", doc).forEach((p, i, all) => (p.dataset.label = `Page ${i + 1} / ${all.length}`));
  // in-place text editing
  $$("[data-edit]", doc).forEach((el) => {
    el.setAttribute("contenteditable", "plaintext-only");
    if (el.contentEditable !== "plaintext-only") el.setAttribute("contenteditable", "true");
    el.spellcheck = true;
    el.addEventListener("focus", () => (el._orig = el.innerText));
    el.addEventListener("keydown", (ev) => {
      if (ev.key === "Escape") { el.innerText = el._orig; el.blur(); }
    });
    el.addEventListener("blur", () => {
      const v = el.innerText.replace(/ /g, " ").replace(/\n+$/, "");
      if (v !== el._orig) saveText(el.dataset.edit, v, "frame");
    });
  });
  // clicks on table rows / figures
  doc.addEventListener("click", (ev) => {
    const tr = ev.target.closest("tr[data-section]");
    if (tr) {
      const p = S.secByName[tr.dataset.section];
      if (p && confirm(`Ajouter « ${p.label} » comme zone de focus ?`)) addZone(tr.dataset.section);
      return;
    }
    const page = ev.target.closest(".page");
    if (ev.target.closest(".zonemap, .hydro") && page?.dataset.page?.startsWith("zone:")) {
      showTab("zones");
      flashZone(page.dataset.page.slice(5));
    } else if (ev.target.closest("[data-image]")) {
      showTab("images");
    } else if (ev.target.closest(".mainmap")) {
      showTab("layout");
    }
  });
  buildTextList(doc);
}

function scrollFrameTo(pageId) {
  const doc = $("#frame").contentDocument;
  const el = doc && doc.querySelector(`.page[data-page="${CSS.escape(pageId)}"]`);
  if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
}

// ------------------------------------------------------------------ texts
const TEXT_LABELS = {
  subtitle: "Sous-titre (page 1)",
  headline: "Message principal (page 1)",
  additional: "Texte complémentaire (page 1)",
  river_risk: "Évaluation - crues fluviales",
  ffgs_text: "Évaluation - crues soudaines (FFGS)",
  flash: "Texte du prévisionniste - crues soudaines",
  alert_note: "Note sous le tableau des points",
  no_alert: "Texte si aucun point en vigilance",
  zones_note: "Note sous la liste des zones",
  method: "Méthode",
  trend_note: "Note sur la tendance",
  flash_note: "Note sur les crues soudaines locales",
  next_issue: "Prochain bulletin",
  contact: "Contact",
};

function textLabel(key) {
  if (TEXT_LABELS[key]) return TEXT_LABELS[key];
  let m = key.match(/^part\.(\d+)$/);
  if (m) return `Points en vigilance - ligne ${+m[1] + 1}`;
  m = key.match(/^impact\.(\d)$/);
  if (m) return `Impacts - ${levelLabel(+m[1])}`;
  m = key.match(/^zone\.(.+)\.(title|comment)$/);
  if (m) {
    const i = (S.draft.zones || []).findIndex((z) => z.section === m[1]);
    return `Zone ${LETTERS[i] ?? "?"} - ${m[2] === "title" ? "titre" : "commentaire"}`;
  }
  return key;
}

function isCustom(key) {
  const m = key.match(/^zone\.(.+)\.(title|comment)$/);
  if (m) {
    const z = (S.draft.zones || []).find((z) => z.section === m[1]);
    return !!(z && z[m[2]]);
  }
  return S.draft.texts && key in S.draft.texts;
}

function buildTextList(doc) {
  const seen = new Set();
  const items = [];
  $$("[data-edit]", doc).forEach((el) => {
    const k = el.dataset.edit;
    if (seen.has(k)) return;
    seen.add(k);
    items.push({ key: k, value: el.innerText });
  });
  $("#textList").innerHTML = items.map((it) => `
    <div class="textItem${isCustom(it.key) ? " custom" : ""}" data-key="${esc(it.key)}">
      <div class="th"><b>${esc(textLabel(it.key))}</b>
        <span>
          <button class="icon" data-act="goto" title="Voir dans l'aperçu">⌖</button>
          ${!it.key.startsWith("zone.") && isCustom(it.key) ? `<button class="icon" data-act="auto" title="Revenir au texte automatique">↺</button>` : ""}
        </span></div>
      <textarea rows="${Math.min(8, Math.max(2, Math.ceil(it.value.length / 55)))}">${esc(it.value)}</textarea>
    </div>`).join("");
}

async function saveText(key, value, from) {
  const zoneKey = key.startsWith("zone.");
  await patch({ texts: { [key]: value } }, { preview: zoneKey });
  if (zoneKey) renderZones();
  const doc = $("#frame").contentDocument;
  if (from !== "frame" && doc) $$(`[data-edit="${CSS.escape(key)}"]`, doc).forEach((el) => (el.innerText = value));
  if (from !== "panel") {
    const ta = $(`.textItem[data-key="${CSS.escape(key)}"] textarea`);
    if (ta) ta.value = value;
  }
  $(`.textItem[data-key="${CSS.escape(key)}"]`)?.classList.add("custom");
}

$("#textList").addEventListener("change", (ev) => {
  const item = ev.target.closest(".textItem");
  if (item && ev.target.tagName === "TEXTAREA") saveText(item.dataset.key, ev.target.value, "panel");
});
$("#textList").addEventListener("click", (ev) => {
  const btn = ev.target.closest("button");
  const item = ev.target.closest(".textItem");
  if (!btn || !item) return;
  const key = item.dataset.key;
  if (btn.dataset.act === "auto") patch({ texts: { [key]: null } });
  if (btn.dataset.act === "goto") {
    const el = $("#frame").contentDocument?.querySelector(`[data-edit="${CSS.escape(key)}"]`);
    if (el) { el.scrollIntoView({ behavior: "smooth", block: "center" }); el.focus(); }
  }
});

// ----------------------------------------------------------------- params
const OPTION_INPUTS = ["issue_time", "min_models", "label_cities_national", "label_places_zoom",
  "zoom_margin_deg", "network_min_cells", "past_days"];

function renderParams() {
  const d = S.draft, o = d.options || {}, def = S.meta.defaults;
  $("#o-number").value = d.number || "";
  for (const k of OPTION_INPUTS) $("#o-" + k).value = o[k] ?? def[k] ?? "";
  $("#o-show_villages_zoom").checked = o.show_villages_zoom ?? def.show_villages_zoom;
  // runs
  const runs = S.meta.runs.filter((r) => r.slice(0, 10) <= d.issue);
  $("#o-force_run").innerHTML = `<option value="">Automatique (le plus récent ≤ date d'émission)</option>` +
    runs.map((r) => `<option value="${r.slice(0, 10)}">${esc(fmtTime(r))} UTC</option>`).join("");
  $("#o-force_run").value = o.force_run || "";
  // models
  const enabled = o.models;
  $("#o-models").innerHTML = Object.entries(S.meta.models).map(([k, name]) =>
    `<label class="check"><input type="checkbox" value="${esc(k)}" ${!enabled || enabled.includes(k) ? "checked" : ""}> ${esc(name)}</label>`).join("");
  // ffgs
  const isFile = d.ffgs && d.ffgs !== "demo";
  $("#ffgsFileOpt").disabled = !isFile;
  $("#ffgsFileOpt").textContent = isFile ? "Fichier importé : " + d.ffgs.split("/").pop() : "Fichier importé";
  $("#o-ffgs").value = !d.ffgs ? "" : d.ffgs === "demo" ? "demo" : "file";
  $("#autoLevel").value = d.auto?.min_level ?? 2;
  $("#autoMax").value = d.auto?.max_zones ?? 4;
}

function renderRunSummary() {
  const s = S.sections;
  if (!s) return;
  const c = s.counts;
  const models = s.models.map((m) => S.meta.models[m] || m).join(", ");
  $("#runSummary").innerHTML = `
    <div><b>Run FloodProofs :</b> ${esc(fmtTime(s.run))} UTC</div>
    <div class="small muted">Modèles disponibles : ${esc(models) || "aucun"}</div>
    <div class="small" style="margin-top:6px">Points de rapport par niveau maximal (5 jours) :</div>
    <div class="levels">${[0, 1, 2, 3].map((l) => `<span style="background:${levelColor(l)};color:${l === 0 || l === 3 ? "#fff" : "#000"}">${c[l] ?? 0}</span>`).join("")}
      ${c["-1"] ? `<span style="background:${S.meta.levels.nodata}">${c["-1"]} n.d.</span>` : ""}</div>`;
}

$("#tab-params").addEventListener("change", async (ev) => {
  const t = ev.target;
  if (t.id === "o-number") return patch({ number: t.value.trim() });
  if (t.id === "o-ffgs") {
    if (t.value === "file") return;
    return patch({ ffgs: t.value || null });
  }
  if (t.id === "ffgsUpload") {
    if (!t.files[0]) return;
    const fd = new FormData();
    fd.append("file", t.files[0]); fd.append("kind", "ffgs");
    busy(true, "Import du fichier FFGS…");
    try { S.draft = await api("POST", base() + "/upload", fd, true); renderParams(); refreshPreview(); }
    catch (e) { busy(false); toast(e.message, true); }
    t.value = "";
    return;
  }
  if (t.closest("#o-models")) {
    const all = $$("#o-models input");
    const sel = all.filter((x) => x.checked).map((x) => x.value);
    if (!sel.length) { t.checked = true; return toast("Au moins un modèle est nécessaire", true); }
    await patch({ options: { models: sel.length === all.length ? null : sel } });
    return loadSections();
  }
  const m = t.id.match(/^o-(.+)$/);
  if (!m) return;
  const key = m[1];
  let v = t.type === "checkbox" ? t.checked : t.value.trim();
  if (t.type === "number") v = v === "" ? null : Number(v);
  if (v === "") v = null;
  await patch({ options: { [key]: v } });
  if (["min_models", "force_run"].includes(key)) loadSections();
});

$("#btnResetOptions").onclick = async () => {
  const reset = Object.fromEntries(Object.keys(S.draft.options || {}).map((k) => [k, null]));
  await patch({ options: reset });
  renderParams();
  loadSections();
};
$("#btnReload").onclick = async () => {
  await api("POST", "/api/reload");
  await refreshMeta();
  renderParams();
  await loadSections();
  refreshPreview();
  toast("Données relues");
};
$("#btnRecreate").onclick = () => {
  if (confirm("Recréer le bulletin automatique ? Les textes, zones, images et réglages de ce bulletin seront perdus.")) {
    openBulletin(S.draft.issue, true);
  }
};

// ------------------------------------------------------------------ zones
function zoneLevel(sec) { return S.secByName[sec]?.level ?? -1; }

function renderZones() {
  const zones = S.draft.zones || [];
  $("#zoneCount").textContent = zones.length ? `(${zones.length})` : "";
  if (!zones.length) {
    $("#zoneList").innerHTML = `<p class="muted small">Aucune zone : le bulletin n'a pas de page détaillée.</p>`;
  } else {
    $("#zoneList").innerHTML = zones.map((z, i) => {
      const p = S.secByName[z.section] || { label: z.section };
      const extras = (z.extra_hydrographs || []).map((e) =>
        `<span class="chip">${esc(S.secByName[e]?.label || e)}<button data-act="rmExtra" data-extra="${esc(e)}" title="Retirer">✕</button></span>`).join("");
      return `
      <div class="zone" data-i="${i}" data-section="${esc(z.section)}">
        <div class="zh">
          <span class="letter">${LETTERS[i]}</span>
          <span class="lvl" style="background:${levelColor(zoneLevel(z.section))}" title="${esc(levelLabel(zoneLevel(z.section)))}"></span>
          <span class="zt">${esc(z.title || p.label)}</span>
          <button class="icon" data-act="goto" title="Voir la page">⌖</button>
          <button class="icon" data-act="up" title="Monter" ${i === 0 ? "disabled" : ""}>↑</button>
          <button class="icon" data-act="down" title="Descendre" ${i === zones.length - 1 ? "disabled" : ""}>↓</button>
          <button class="icon" data-act="rm" title="Supprimer la zone">✕</button>
        </div>
        <div class="zs">${esc(p.label)} · ${esc(p.region || "")} · ${p.area ? Math.round(p.area).toLocaleString("fr-FR") + " km²" : ""}${z.auto ? " · sélection automatique" : ""}</div>
        <label>Titre <input data-f="title" value="${esc(z.title || "")}" placeholder="${esc(p.label)}"></label>
        <label>Commentaire du prévisionniste <textarea data-f="comment" rows="2">${esc(z.comment || "")}</textarea></label>
        <div class="extras">Hydrogrammes complémentaires : ${extras || '<span class="muted">aucun</span>'}
          ${(z.extra_hydrographs || []).length < 2 ? `<input data-f="addExtra" list="secList" placeholder="ajouter…" style="width:150px">` : ""}</div>
        <label style="margin-top:6px">Marge de la carte (°) <input data-f="margin_deg" type="number" step="0.05" min="0" max="3" style="width:6em;display:inline-block"
          value="${z.margin_deg ?? ""}" placeholder="${S.draft.options?.zoom_margin_deg ?? S.meta.defaults.zoom_margin_deg}"></label>
      </div>`;
    }).join("");
  }
  renderPlaceOptions();
  updateZoneLayers();
}

function flashZone(sec) {
  const el = $(`.zone[data-section="${CSS.escape(sec)}"]`);
  if (el) { el.scrollIntoView({ behavior: "smooth", block: "center" }); el.classList.remove("flash"); void el.offsetWidth; el.classList.add("flash"); }
}

function saveZones(zones) {
  return patch({ zones }).then(() => renderZones());
}

function addZone(sec) {
  if (!S.secByName[sec]) return toast("Point de rapport inconnu : " + sec, true);
  const zones = [...(S.draft.zones || [])];
  if (zones.some((z) => z.section === sec)) { showTab("zones"); flashZone(sec); return toast("Cette zone existe déjà"); }
  zones.push({ section: sec });
  saveZones(zones).then(() => { showTab("zones"); flashZone(sec); });
  toast("Zone ajoutée : " + S.secByName[sec].label);
}

function addExtra(zoneSec, sec) {
  if (!S.secByName[sec]) return toast("Point de rapport inconnu : " + sec, true);
  const zones = structuredClone(S.draft.zones || []);
  const z = zones.find((z) => z.section === zoneSec);
  z.extra_hydrographs = z.extra_hydrographs || [];
  if (sec === zoneSec || z.extra_hydrographs.includes(sec)) return;
  if (z.extra_hydrographs.length >= 2) return toast("Deux hydrogrammes complémentaires au maximum", true);
  z.extra_hydrographs.push(sec);
  saveZones(zones);
}

$("#zoneList").addEventListener("click", (ev) => {
  const btn = ev.target.closest("button");
  const card = ev.target.closest(".zone");
  if (!btn || !card) return;
  const i = +card.dataset.i;
  const zones = structuredClone(S.draft.zones || []);
  switch (btn.dataset.act) {
    case "rm": zones.splice(i, 1); break;
    case "up": [zones[i - 1], zones[i]] = [zones[i], zones[i - 1]]; break;
    case "down": [zones[i + 1], zones[i]] = [zones[i], zones[i + 1]]; break;
    case "rmExtra": zones[i].extra_hydrographs = zones[i].extra_hydrographs.filter((e) => e !== btn.dataset.extra); break;
    case "goto": return scrollFrameTo("zone:" + card.dataset.section);
    default: return;
  }
  saveZones(zones);
});
$("#zoneList").addEventListener("change", (ev) => {
  const card = ev.target.closest(".zone");
  const f = ev.target.dataset.f;
  if (!card || !f) return;
  const i = +card.dataset.i;
  const zones = structuredClone(S.draft.zones || []);
  if (f === "addExtra") return addExtra(zones[i].section, ev.target.value.trim());
  if (f === "margin_deg") zones[i].margin_deg = ev.target.value === "" ? null : Number(ev.target.value);
  else zones[i][f] = ev.target.value;
  saveZones(zones);
});
$("#btnAddSec").onclick = () => { addZone($("#secSearch").value.trim()); $("#secSearch").value = ""; };
$("#secSearch").addEventListener("keydown", (ev) => { if (ev.key === "Enter") $("#btnAddSec").click(); });
$("#btnSuggest").onclick = async () => {
  const body = { min_level: +$("#autoLevel").value, max_zones: +$("#autoMax").value };
  try {
    const r = await api("POST", base() + "/suggest", body);
    if (!r.sections.length) return toast("Aucun point supplémentaire à ce niveau");
    const zones = [...(S.draft.zones || []), ...r.sections.map((s) => ({ section: s, auto: true }))];
    await patch({ zones, auto: body });
    renderZones();
    toast(`${r.sections.length} zone(s) ajoutée(s)`);
  } catch (e) { toast(e.message, true); }
};

// ------------------------------------------------------------------- map
function initMap() {
  if (S.map) return;
  S.map = L.map("map", { zoomSnap: 0.25 }).fitBounds([[9.3, -5.6], [15.1, 2.5]]);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "© OpenStreetMap", maxZoom: 13, opacity: 0.55,
  }).addTo(S.map);
  S.layers.rivers = L.layerGroup().addTo(S.map);
  S.layers.basins = L.layerGroup().addTo(S.map);
  S.layers.points = L.layerGroup().addTo(S.map);
  $("#mapLegend").innerHTML = [0, 1, 2, 3, -1].map((l) => `<span><i style="background:${levelColor(l)}"></i>${esc(levelLabel(l))}</span>`).join("")
    + `<span><i style="background:#fff;border-style:dashed"></i>bassin d'une zone</span>`;
  api("GET", "/api/rivers").then((g) => { S.rivers = g; updateMap(); });
  S.map.on("popupopen", (e) => {
    const el = e.popup.getElement();
    el.querySelectorAll("button[data-act]").forEach((b) => b.addEventListener("click", () => {
      if (b.dataset.act === "zone") addZone(b.dataset.sec);
      if (b.dataset.act === "extra") addExtra(b.dataset.zone, b.dataset.sec);
      S.map.closePopup();
    }));
  });
}

function popupHtml(p) {
  const zones = S.draft.zones || [];
  const isZone = zones.some((z) => z.section === p.name);
  const days = (p.days || []).map((l) => `<span style="background:${levelColor(l)}"></span>`).join("");
  const thr = p.thr && p.thr.length ? `Seuils : ${p.thr.map((x) => x ?? "-").join(" / ")} m³/s<br>` : "";
  return `<div class="pop-title">${esc(p.label)}</div>
    <div class="small muted">${esc(p.basin)} · ${esc(p.region)} · ${p.area ? Math.round(p.area).toLocaleString("fr-FR") + " km²" : ""}</div>
    <div class="pop-days">${days}</div>
    <div class="small"><b>${esc(levelLabel(p.level))}</b><br>Q max prévu : ${p.peak_q ?? "-"} m³/s<br>${thr}Modèles ≥ jaune : ${esc(p.models)}</div>
    ${isZone ? '<div class="small muted">Déjà zone de focus</div>' : `<button data-act="zone" data-sec="${esc(p.name)}">Ajouter comme zone</button>`}
    ${zones.filter((z) => z.section !== p.name && (z.extra_hydrographs || []).length < 2 && !(z.extra_hydrographs || []).includes(p.name))
      .map((z) => `<button data-act="extra" data-zone="${esc(z.section)}" data-sec="${esc(p.name)}">Hydrogramme → zone ${LETTERS[zones.indexOf(z)]}</button>`).join("")}`;
}

function updateMap() {
  if (!S.map || !S.sections) return;
  S.layers.points.clearLayers();
  S.layers.rivers.clearLayers();
  if (S.rivers) {
    L.geoJSON(S.rivers, {
      interactive: false,
      style: (f) => ({ color: levelColor(S.secByName[f.properties.section]?.level ?? -1), weight: 0.6 + 0.25 * Math.max(0, f.properties.log_ups - 7), opacity: 0.9 }),
    }).addTo(S.layers.rivers);
  }
  const feats = [...S.sections.features].sort((a, b) => a.properties.level - b.properties.level);
  for (const f of feats) {
    const p = f.properties;
    const [x, y] = f.geometry.coordinates;
    const r = Math.min(7, 2 + Math.max(0, Math.log10(p.area || 100) - 2) * 1.2) + (p.level > 0 ? 2 : 0);
    L.circleMarker([y, x], { radius: r, color: "#333", weight: p.level > 0 ? 1 : 0.5, fillColor: levelColor(p.level),
      fillOpacity: p.level > 0 ? 1 : 0.75 })
      .bindTooltip(`${p.label}`, { direction: "top" })
      .bindPopup(() => popupHtml(p), { maxWidth: 280 })
      .addTo(S.layers.points);
  }
  updateZoneLayers();
}

S.basinCache = {};
async function updateZoneLayers() {
  if (!S.map) return;
  const zones = S.draft?.zones || [];
  const groups = [];
  for (const [i, z] of zones.entries()) {
    if (!S.basinCache[z.section]) {
      try { S.basinCache[z.section] = await api("GET", "/api/basin/" + encodeURIComponent(z.section)); } catch (e) { continue; }
    }
    const g = L.geoJSON(S.basinCache[z.section], { interactive: false, style: { color: "#1d2125", weight: 1.5, dashArray: "4 3", fill: true, fillOpacity: 0.04 } });
    const p = S.secByName[z.section];
    const m = p ? L.marker([...S.sections.features.find((f) => f.properties.name === z.section).geometry.coordinates].reverse(), {
      icon: L.divIcon({ className: "", html: `<div style="width:20px;height:20px;border:2px solid #1d2125;border-radius:50%;background:#fff;font:800 12px/16px sans-serif;text-align:center">${LETTERS[i]}</div>`, iconSize: [20, 20], iconAnchor: [10, 10] }),
      interactive: false,
    }) : null;
    groups.push(g, ...(m ? [m] : []));
  }
  S.layers.basins.clearLayers();
  groups.forEach((g) => g.addTo(S.layers.basins));
}

// ----------------------------------------------------------------- layout
const LAYOUT_KEYS = ["main_map_width", "show_parts", "show_days", "show_alert_table", "alert_max_rows", "show_zone_list",
  "zone_map_height", "hydro_height", "show_zone_table", "show_extra_hydros", "show_guide"];

function renderLayout() {
  const L_ = { ...S.meta.layout, ...(S.draft.layout || {}) };
  for (const k of LAYOUT_KEYS) {
    const el = $("#l-" + k);
    if (el.type === "checkbox") el.checked = !!L_[k];
    else el.value = String(L_[k]);
  }
  $("#mainMapW").textContent = L_.main_map_width;
  $$('input[name="zone_layout"]').forEach((r) => (r.checked = r.value === L_.zone_layout));
}

$("#tab-layout").addEventListener("input", (ev) => {
  if (ev.target.id === "l-main_map_width") $("#mainMapW").textContent = ev.target.value;
});
$("#tab-layout").addEventListener("change", (ev) => {
  const t = ev.target;
  if (t.name === "zone_layout") return patch({ layout: { zone_layout: t.value } });
  const m = t.id.match(/^l-(.+)$/);
  if (!m) return;
  let v = t.type === "checkbox" ? t.checked : Number(t.value);
  patch({ layout: { [m[1]]: v } });
});
$("#btnResetLayout").onclick = async () => {
  await patch({ layout: Object.fromEntries(Object.keys(S.draft.layout || {}).map((k) => [k, null])) });
  renderLayout();
};

// ----------------------------------------------------------------- images
function placeOptions(selected) {
  const opts = [["page1", "Page 1 (sous le message principal)"], ["page2", "Page 2 (évaluation)"]];
  (S.draft.zones || []).forEach((z, i) => opts.push(["zone:" + z.section, `Page de la zone ${LETTERS[i]} - ${z.title || S.secByName[z.section]?.label || z.section}`]));
  opts.push(["annex", "Page « Informations complémentaires »"]);
  return opts.map(([v, l]) => `<option value="${esc(v)}" ${v === selected ? "selected" : ""}>${esc(l)}</option>`).join("");
}
function renderPlaceOptions() {
  const cur = $("#imgPlace").value || "annex";
  $("#imgPlace").innerHTML = placeOptions(cur);
  renderImages();
}

function renderImages() {
  const ims = S.draft.images || [];
  $("#imageList").innerHTML = ims.map((im, i) => `
    <div class="imgItem" data-i="${i}">
      <img src="${base()}/uploads/${encodeURIComponent(im.file)}" alt="">
      <div class="f">
        <label>Légende <input data-f="caption" value="${esc(im.caption || "")}"></label>
        <label>Emplacement <select data-f="place">${placeOptions(im.place)}</select></label>
        <div class="row" style="margin:0">
          <label class="inline">Largeur <input data-f="width" type="number" min="20" max="100" value="${im.width || 100}" style="width:4.5em">%</label>
          <button class="icon" data-act="up" ${i === 0 ? "disabled" : ""}>↑</button>
          <button class="icon" data-act="down" ${i === ims.length - 1 ? "disabled" : ""}>↓</button>
          <button class="icon" data-act="rm" title="Retirer l'image">✕</button>
        </div>
      </div>
    </div>`).join("") || `<p class="muted small">Aucune image ajoutée.</p>`;
}

$("#imageList").addEventListener("change", (ev) => {
  const it = ev.target.closest(".imgItem");
  if (!it || !ev.target.dataset.f) return;
  const ims = structuredClone(S.draft.images || []);
  const f = ev.target.dataset.f;
  ims[+it.dataset.i][f] = f === "width" ? Math.max(20, Math.min(100, +ev.target.value)) : ev.target.value;
  patch({ images: ims });
});
$("#imageList").addEventListener("click", (ev) => {
  const btn = ev.target.closest("button");
  const it = ev.target.closest(".imgItem");
  if (!btn || !it) return;
  const i = +it.dataset.i;
  const ims = structuredClone(S.draft.images || []);
  if (btn.dataset.act === "rm") ims.splice(i, 1);
  if (btn.dataset.act === "up") [ims[i - 1], ims[i]] = [ims[i], ims[i - 1]];
  if (btn.dataset.act === "down") [ims[i + 1], ims[i]] = [ims[i], ims[i + 1]];
  patch({ images: ims }).then(renderImages);
});
$("#btnUpload").onclick = async () => {
  const f = $("#imgFile").files[0];
  if (!f) return toast("Choisissez d'abord un fichier image", true);
  const fd = new FormData();
  fd.append("file", f); fd.append("kind", "image");
  fd.append("caption", $("#imgCaption").value); fd.append("place", $("#imgPlace").value);
  fd.append("width", $("#imgWidth").value || 100);
  busy(true, "Envoi de l'image…");
  try {
    S.draft = await api("POST", base() + "/upload", fd, true);
    $("#imgFile").value = ""; $("#imgCaption").value = "";
    renderImages(); renderStatus(); refreshPreview();
    const place = S.draft.images[S.draft.images.length - 1].place;
    setTimeout(() => scrollFrameTo(place === "page1" ? "p1" : place === "page2" ? "p2" : place), 1200);
  } catch (e) { busy(false); toast(e.message, true); }
};

// ----------------------------------------------------------------- export
function renderExport() {
  const d = S.draft, e = d.exported;
  const files = e ? e.files.map((f) => `<a href="${base()}/files/${encodeURIComponent(f)}" target="_blank">${esc(f)}</a>`).join("<br>") : "";
  $("#exportInfo").innerHTML = e
    ? `<div><b>${e.rev === d.rev ? "✓ PDF à jour" : "⚠ Le bulletin a été modifié depuis le dernier PDF"}</b></div>
       <div class="small muted">Généré le ${esc(fmtTime(e.at))} par ${esc(e.by)}</div><div style="margin-top:6px">${files}</div>`
    : `<div class="muted">Le PDF n'a pas encore été généré pour ce bulletin.</div>`;
}

async function doExport() {
  busy(true, "Génération du PDF…");
  try {
    S.draft = await api("POST", base() + "/export");
    renderStatus(); renderExport(); refreshMeta();
    showTab("export");
    toast("PDF généré");
    const pdf = S.draft.exported.files.find((f) => f.endsWith(".pdf"));
    if (pdf) window.open(`${base()}/files/${encodeURIComponent(pdf)}`, "_blank");
  } catch (e) { toast("Erreur : " + e.message, true); }
  busy(false);
}
$("#btnExport").onclick = doExport;
$("#btnExport2").onclick = doExport;
$("#btnRefresh").onclick = () => refreshPreview();

function renderBulletinList() {
  const list = S.meta.bulletins;
  $("#bulletinList").innerHTML = `<table class="blist">${list.map((b) => `
    <tr><td><a href="#${b.issue}" data-issue="${b.issue}">${esc(new Date(b.issue + "T00:00").toLocaleDateString("fr-FR", { weekday: "short", day: "2-digit", month: "2-digit", year: "numeric" }))}</a></td>
      <td>${esc(b.number || "")}</td>
      <td class="small muted">${b.draft ? "brouillon rév. " + b.rev : "ligne de commande"}</td>
      <td>${b.pdf ? `<a href="/api/bulletins/${b.issue}/files/${encodeURIComponent(b.pdf)}" target="_blank">PDF</a>` : ""}</td></tr>`).join("")}</table>`;
}
$("#bulletinList").addEventListener("click", (ev) => {
  const a = ev.target.closest("a[data-issue]");
  if (a) { ev.preventDefault(); openBulletin(a.dataset.issue); }
});

function renderRecent() {
  $("#recent").innerHTML = `<option value="">Bulletins existants…</option>` + S.meta.bulletins.map((b) =>
    `<option value="${b.issue}">${esc(b.issue)} ${b.number ? "· n° " + esc(b.number) : ""} ${b.pdf ? "· PDF" : ""}</option>`).join("");
}

// ------------------------------------------------------------------- tabs
function showTab(name) {
  $$(".tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  $$(".tab").forEach((t) => t.classList.toggle("active", t.id === "tab-" + name));
  if (name === "zones") { initMap(); setTimeout(() => { S.map.invalidateSize(); updateMap(); }, 50); }
  try { localStorage.setItem("bulletinTab", name); } catch (e) { /* ignore */ }
}
$$(".tabs button").forEach((b) => (b.onclick = () => showTab(b.dataset.tab)));

// ---------------------------------------------------- polling (co-editing)
function editingNow() {
  const a = document.activeElement;
  if (a && ["INPUT", "TEXTAREA", "SELECT"].includes(a.tagName)) return true;
  const fa = $("#frame").contentDocument?.activeElement;
  return !!(fa && fa.isContentEditable);
}
setInterval(async () => {
  if (!S.draft || document.hidden || editingNow()) return;
  try {
    const d = await api("GET", base());
    if (d.rev > S.draft.rev) {
      S.draft = d;
      renderStatus(); renderAll(); loadSections(); refreshPreview();
      toast(`Bulletin modifié par ${d.updated_by}`);
    }
  } catch (e) { /* offline: retry later */ }
}, 8000);

// ------------------------------------------------------------------- init
(async function init() {
  document.body.classList.add("noDraft");
  $("#frame").addEventListener("load", onFrameLoad);
  try { S.meta = await api("GET", "/api/meta"); }
  catch (e) { toast("Serveur injoignable : " + e.message, true); return; }
  $("#user").textContent = S.meta.user !== "local" ? "👤 " + S.meta.user : "";
  renderRecent();
  const last = S.meta.runs[0];
  $("#runsInfo").innerHTML = last ? `Dernier run FloodProofs disponible : <b>${esc(fmtTime(last))} UTC</b> (${S.meta.runs.length} runs).` : "Aucun run FloodProofs trouvé.";
  $("#issue").value = location.hash.slice(1) || S.meta.today;
  $("#btnOpen").onclick = () => openBulletin($("#issue").value);
  $("#recent").onchange = (e) => { if (e.target.value) openBulletin(e.target.value); e.target.value = ""; };
  window.addEventListener("hashchange", () => {
    const h = location.hash.slice(1);
    if (h && h !== S.draft?.issue) openBulletin(h);
  });
  let tab = "params";
  try { tab = localStorage.getItem("bulletinTab") || tab; } catch (e) { /* ignore */ }
  showTab(tab === "zones" ? "params" : tab);
  if (location.hash.length > 1) openBulletin(location.hash.slice(1));
})();

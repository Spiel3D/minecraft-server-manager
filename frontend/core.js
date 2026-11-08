"use strict";
/* Noyau de l'interface : helpers DOM (jamais d'innerHTML : tout est inséré en texte), icônes, API, modales, routeur, mise en page par rôle. */
const API = window.MCPANEL_API || "";
const APP_VERSION = "4.1.0";
const state = { csrf: null, user: null, username: null, role: null, snap: null, alerts: [], view: null, statsWs: null, route: "" };
const LABELS = { stopped: "Arrêté", starting: "Démarrage", running: "En fonctionnement", stopping: "Arrêt en cours", installing: "Installation", awaiting_jar: "Jar à fournir", error: "Erreur" };
const TYPES = { vanilla: "Vanilla", forge: "Forge", neoforge: "NeoForge", fabric: "Fabric", quilt: "Quilt", paper: "Paper", purpur: "Purpur", spigot: "Spigot", bukkit: "CraftBukkit", mohist: "Mohist", magma: "Magma", arclight: "Arclight", custom: "Jar personnalisé" };
const isAdmin = () => state.role === "admin";
/* Type avant version (V4 §2) : « Paper — Minecraft 1.21.1 ». */
const typeLine = (s) => `${TYPES[s.server_type] || s.server_type} — Minecraft ${s.mc_version}`;

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "style") { if (typeof v === "object") Object.assign(el.style, v); }   /* CSP : pas d'attribut style en texte */
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k === "value") el.value = v;
    else if (k === "checked") el.checked = !!v;
    else if (k === "disabled") el.disabled = !!v;
    else el.setAttribute(k, v === true ? "" : String(v));
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return el;
}
/* append() accepte aussi des tableaux imbriqués et ignore null/false (comme h()) */
const _append = Element.prototype.append;
Element.prototype.append = function (...kids) { return _append.apply(this, kids.flat(Infinity).filter((k) => k !== null && k !== undefined && k !== false)); };
const enc = encodeURIComponent;
const clear = (el) => { while (el.firstChild) el.removeChild(el.firstChild); return el; };
const fmtBytes = (n) => { n = Number(n) || 0; const u = ["o", "Kio", "Mio", "Gio", "Tio"]; let i = 0; while (n >= 1024 && i < 4) { n /= 1024; i++; } return (i ? n.toFixed(n < 10 ? 1 : 0) : n) + " " + u[i]; };
const fmtMb = (n) => fmtBytes((Number(n) || 0) * 1048576);
const fmtDur = (s) => { s = Math.floor(s || 0); const d = Math.floor(s / 86400), hh = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60); return d ? `${d} j ${hh} h` : hh ? `${hh} h ${m} min` : m ? `${m} min` : `${s} s`; };
const fmtDate = (t) => (t ? new Date(t * 1000).toLocaleString("fr-FR") : "—");
const fmtAgo = (t) => { if (!t) return "jamais"; const s = Date.now() / 1000 - t; return s < 60 ? "à l'instant" : "il y a " + fmtDur(s); };

/* ---------- icônes (SVG construits par le DOM, trait uniforme) ---------- */
const SVG_NS = "http://www.w3.org/2000/svg";
const C = (x, y, r) => `M${x - r} ${y}a${r} ${r} 0 1 0 ${2 * r} 0a${r} ${r} 0 1 0 ${-2 * r} 0`;
const R = (x, y, w, hh, r = 0) => `M${x + r} ${y}h${w - 2 * r}a${r} ${r} 0 0 1 ${r} ${r}v${hh - 2 * r}a${r} ${r} 0 0 1 ${-r} ${r}h${-(w - 2 * r)}a${r} ${r} 0 0 1 ${-r} ${-r}v${-(hh - 2 * r)}a${r} ${r} 0 0 1 ${r} ${-r}z`;
const ICONS = {
  dashboard: [R(3, 3, 7, 9, 1.5), R(14, 3, 7, 5, 1.5), R(14, 12, 7, 9, 1.5), R(3, 16, 7, 5, 1.5)],
  server: [R(3, 4, 18, 6, 2), R(3, 14, 18, 6, 2), "M7 7h.01M7 17h.01"],
  plus: ["M12 5v14M5 12h14"],
  folder: ["M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"],
  box: ["M12 3l8 4.5v9L12 21l-8-4.5v-9z", "M12 12l8-4.5M12 12v9M12 12L4 7.5"],
  archive: [R(3, 4, 18, 4, 1), "M5 8v10a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8M10 12h4"],
  gauge: ["M3.5 17.5a9 9 0 1 1 17 0", "M12 17l4-5"],
  activity: ["M3 12h4l3-8 4 16 3-8h4"],
  users: [C(9, 8, 3.5), "M2.5 20a6.5 6.5 0 0 1 13 0", "M16 5a3 3 0 0 1 0 6M18 20a6 6 0 0 0-3-5.2"],
  user: [C(12, 8, 4), "M4.5 20a7.5 7.5 0 0 1 15 0"],
  key: [C(8, 15, 4), "M11 12l8-8M16 7l3 3"],
  code: ["M8 8l-4 4 4 4M16 8l4 4-4 4M13.5 5l-3 14"],
  bell: ["M6 16v-5a6 6 0 0 1 12 0v5l2 2H4z", "M10 21h4"],
  sliders: ["M4 7h8M18 7h2M4 17h2M12 17h8", C(15, 7, 2), C(9, 17, 2)],
  play: ["M7 4.5v15l12-7.5z"],
  star: ["M12 3.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z"],
  stop: [R(6, 6, 12, 12, 2)],
  restart: ["M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7"],
  terminal: [R(3, 4, 18, 16, 2), "M7 9l3 3-3 3M13 15h4"],
  copy: [R(9, 9, 11, 11, 2), "M5 15a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h7a2 2 0 0 1 2 2"],
  check: ["M5 12.5l4.5 4.5L19 7.5"],
  x: ["M6 6l12 12M18 6L6 18"],
  menu: ["M4 7h16M4 12h16M4 17h16"],
  logout: ["M10 4H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h4M15 8l4 4-4 4M19 12H9"],
  alert: ["M12 3l10 18H2z", "M12 10v4M12 17.5h.01"],
  info: [C(12, 12, 9), "M12 11v5M12 7.8h.01"],
  download: ["M12 4v11M7.5 11l4.5 4.5 4.5-4.5M5 20h14"],
  upload: ["M12 15V4M7.5 8L12 3.5 16.5 8M5 20h14"],
  trash: ["M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 11v5M14 11v5"],
  edit: ["M4 20h4L19 9l-4-4L4 16z", "M13.5 6.5l4 4"],
  thermo: ["M10 14V5a2 2 0 0 1 4 0v9a4 4 0 1 1-4 0z"],
  network: [C(12, 5, 2), C(5, 19, 2), C(19, 19, 2), "M12 7v4M12 11L6 17.2M12 11l6 6.2"],
  shield: ["M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z", "M9 12l2 2 4-4"],
  file: ["M6 3h8l4 4v14H6z", "M14 3v4h4"],
  cpu: [R(6, 6, 12, 12, 2), "M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4"],
  disk: [R(3, 13, 18, 7, 2), "M3 13l3-8h12l3 8M7 16.5h.01"],
  ram: [R(3, 8, 18, 8, 1.5), "M7 8v8M12 8v8M17 8v8"],
  clock: [C(12, 12, 9), "M12 7v5l3 2"],
  power: ["M12 3v8M6.3 6.8a8 8 0 1 0 11.4 0"],
  link: ["M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3A4 4 0 0 0 11 18.7l1-1"],
  search: [C(11, 11, 7), "M20 20l-4-4"],
};
function icon(name, extra) {
  const s = document.createElementNS(SVG_NS, "svg");
  s.setAttribute("viewBox", "0 0 24 24"); s.setAttribute("class", "ic" + (extra ? " " + extra : "")); s.setAttribute("aria-hidden", "true");
  for (const d of ICONS[name] || ICONS.info) { const p = document.createElementNS(SVG_NS, "path"); p.setAttribute("d", d); s.appendChild(p); }
  return s;
}
/* bouton avec icône : ibtn("play", "Démarrer", {class:"primary"}) */
const ibtn = (ic, label, attrs) => h("button", { type: "button", ...(attrs || {}), "aria-label": label || (attrs && attrs.title) || null }, icon(ic), label ? h("span", null, label) : null);
const ilink = (ic, label, href, cls, onclick) => h("a", { class: cls || "", href, onclick: onclick || null }, icon(ic), h("span", null, label));

function errText(data, status) {
  const d = data && data.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d)) return d.map((x) => x.msg || JSON.stringify(x)).join(" ; ");
  return `Erreur ${status}`;
}

async function api(method, path, body, opts = {}) {
  const init = { method, headers: {}, credentials: "same-origin" };
  if (state.csrf) init.headers["X-CSRF-Token"] = state.csrf;
  if (body !== undefined) { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(body); }
  const r = await fetch(API + path, init);
  let data = null;
  try { data = await r.json(); } catch (e) { /* corps vide */ }
  if (r.status === 401 && !opts.quiet) { showLogin(); throw new Error("Session expirée : reconnectez-vous."); }
  if (!r.ok) { const e = new Error(errText(data, r.status)); e.status = r.status; e.code = data && data.code; e.retryAfter = +r.headers.get("Retry-After") || 0; throw e; }
  return data;
}

/* ---------- presse-papiers : navigator.clipboard n'existe qu'en HTTPS ; le panneau est en HTTP sur le réseau local ---------- */
async function copyText(text) {
  try { if (navigator.clipboard && window.isSecureContext) { await navigator.clipboard.writeText(text); return true; } } catch (e) { /* repli ci-dessous */ }
  const ta = h("textarea", { readonly: true, "aria-hidden": "true" }); ta.value = text;
  Object.assign(ta.style, { position: "fixed", left: "-9999px", top: "0" });
  document.body.append(ta); ta.select(); ta.setSelectionRange(0, text.length);
  let ok = false; try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
  ta.remove(); return ok;
}
function copyBtn(text, label, cls) {
  const b = ibtn("copy", label || null, { class: "iconbtn sm ghost " + (cls || ""), title: "Copier" });
  b.onclick = async () => { const ok = await copyText(text); toast(ok ? "Copié : " + text : "Copie impossible : sélectionnez le texte à la main.", ok ? "ok" : "error"); };
  return b;
}
const copyLine = (text) => h("div", { class: "copyline" }, h("span", { class: "mono" }, text), copyBtn(text));

function toast(msg, kind) {
  let box = document.getElementById("toasts");
  if (!box) { box = h("div", { id: "toasts", role: "status", "aria-live": "polite" }); document.body.append(box); }
  const t = h("div", { class: "toast " + (kind || ""), onclick: () => t.remove() }, icon(kind === "error" ? "alert" : kind === "ok" ? "check" : "info"), h("div", null, msg));
  box.append(t);
  setTimeout(() => t.remove(), kind === "error" ? 9000 : 5000);
}
const fail = (e) => toast(e.message || String(e), "error");
async function guard(fn) { try { return await fn(); } catch (e) { fail(e); } }

function modal(title, content, actions) {
  const bg = h("div", { class: "modal-bg" });
  const onKey = (e) => { if (e.key === "Escape") close(); };
  const close = () => { document.removeEventListener("keydown", onKey); bg.remove(); };
  const bar = h("div", { class: "row" }, (actions || []).map((a) =>
    h("button", { type: "button", class: a.kind || "", disabled: a.disabled, onclick: async (ev) => { const r = await a.onclick(close, ev); if (r !== false && a.close !== false) close(); } }, a.label)));
  const box = h("div", { class: "modal", role: "dialog", "aria-modal": "true", "aria-label": title },
    h("h2", null, title, h("button", { type: "button", class: "iconbtn sm ghost", "aria-label": "Fermer", onclick: close }, icon("x"))), content, bar);
  bg.append(box);
  bg.addEventListener("mousedown", (e) => { if (e.target === bg) close(); });
  document.addEventListener("keydown", onKey);
  document.body.append(bg);
  const first = box.querySelector("input:not([type=hidden]),select,textarea");
  if (first) first.focus();
  return { close, el: box };
}
function confirmBox(title, text, okLabel, danger) {
  return new Promise((res) => {
    modal(title, h("p", null, text), [
      { label: "Annuler", onclick: () => res(false) },
      { label: okLabel || "Confirmer", kind: danger ? "danger primary" : "primary", onclick: () => res(true) },
    ]);
  });
}

const badge = (st, text) => h("span", { class: "badge " + st }, text || LABELS[st] || st);
const lamp = (st) => h("span", { class: "lamp " + st, role: "img", "aria-label": LABELS[st] || st, title: LABELS[st] || st });
function bar(pct) { const i = h("i"); i.style.width = Math.min(100, Math.max(0, pct)) + "%"; return h("div", { class: "bar " + (pct > 90 ? "crit" : pct > 75 ? "warn" : "") }, i); }
function setBar(el, pct) { el.firstChild.style.width = Math.min(100, Math.max(0, pct)) + "%"; el.className = "bar " + (pct > 90 ? "crit" : pct > 75 ? "warn" : ""); }
const pageHead = (title, sub, ...actions) => h("div", { class: "page-head" }, h("div", null, h("h1", null, title), sub ? h("p", null, sub) : null), actions.length ? h("div", { class: "row" }, actions) : null);
const emptyState = (ic, title, text, ...actions) => h("div", { class: "card empty" }, icon(ic), h("h3", null, title), text ? h("p", null, text) : null, actions.length ? h("div", { class: "row" }, actions) : null);
const skeleton = (n = 3) => h("div", { "aria-busy": "true", "aria-label": "Chargement" }, h("div", { class: "skel h1" }), h("div", { class: "grid" }, Array.from({ length: n }, () => h("div", { class: "skel card-s" }))));
function setTitle(t) { const el = document.getElementById("ptitle"); if (el) el.textContent = t; document.title = t + " — Minecraft Server Manager"; }

async function trackJob(jid, onUpdate) {
  for (;;) {
    const j = await api("GET", "/api/jobs/" + jid);
    if (onUpdate) onUpdate(j);
    if (j.status === "done" || j.status === "error") return j;
    await new Promise((r) => setTimeout(r, 1000));
  }
}

/* ---------- routeur ---------- */
const routes = [];
function addRoute(rx, fn) { routes.push([rx, fn]); }
function go(hash) { if (location.hash === hash) route(); else location.hash = hash; }
const USER_ROUTES = /^#\/(home|api|account)(\/|$)/;
let routeSeq = 0;
async function route() {
  if (!state.user) return;
  const home = isAdmin() ? "#/dashboard" : "#/home";
  const hash = location.hash || home;
  if (!isAdmin() && !USER_ROUTES.test(hash)) { go(home); return; }          /* confort : le serveur refuse de toute façon (403) */
  state.route = hash;
  const my = ++routeSeq;
  if (state.view && state.view.destroy) { try { state.view.destroy(); } catch (e) { /* ignore */ } }
  state.view = null;
  let label = "";
  document.querySelectorAll(".nav a").forEach((a) => { const on = a.dataset.match.split("|").some((m) => hash.startsWith(m)); a.classList.toggle("active", on); if (on) label = a.dataset.label; });
  setTitle(label || "Minecraft Server Manager");
  document.querySelector(".side").classList.remove("open"); document.querySelector(".scrim").classList.remove("open");
  const main = document.getElementById("main");
  window.scrollTo(0, 0);
  for (const [rx, fn] of routes) {
    const m = hash.match(rx);
    if (!m) continue;
    const holder = h("div");
    const timer = setTimeout(() => { if (my === routeSeq) clear(main).append(skeleton()); }, 140);   /* le squelette n'apparaît que si le chargement dure */
    let view = null, err = null;
    try { view = (await fn(holder, ...m.slice(1))) || null; } catch (e) { err = e; }
    clearTimeout(timer);
    if (my !== routeSeq) { if (view && view.destroy) { try { view.destroy(); } catch (e) { /* ignore */ } } return; }
    clear(main);
    if (err) {
      main.append(emptyState("alert", "Impossible d'afficher cette page", err.message, ibtn("restart", "Réessayer", { class: "primary", onclick: route })));
      return;
    }
    main.append([...holder.childNodes]);
    state.view = view;
    if (view && view.onStats && state.snap) { try { view.onStats(state.snap); } catch (e) { /* ignore */ } }
    return;
  }
  go(home);
}
window.addEventListener("hashchange", route);

/* ---------- connexion / mise en page ---------- */
const brandMark = () => h("div", { class: "brand" }, h("div", { class: "mark", "aria-hidden": "true" }, h("i"), h("i"), h("i"), h("i")), h("div", null, "Server Manager", h("small", null, "Minecraft")));

function showLogin() {
  state.user = null; state.csrf = null; state.role = null;
  if (state.statsWs) { state.statsWs.onclose = null; state.statsWs.close(); state.statsWs = null; }
  let last = "admin"; try { last = localStorage.getItem("mcpanel_user") || "admin"; } catch (e) { /* stockage indisponible */ }
  const u = h("input", { type: "text", id: "u", autocomplete: "username", value: last, autocapitalize: "none", spellcheck: "false" });
  const p = h("input", { type: "password", id: "p", autocomplete: "current-password" });
  const err = h("div", { class: "alert critical", role: "alert", hidden: true });
  const btn = h("button", { class: "primary big", type: "submit" }, "Se connecter");
  const submit = async (ev) => {
    ev.preventDefault();
    err.hidden = true; btn.disabled = true;
    try {
      const r = await api("POST", "/api/auth/login", { username: u.value.trim(), password: p.value }, { quiet: true });
      try { localStorage.setItem("mcpanel_user", u.value.trim()); } catch (e) { /* ignoré */ }
      state.csrf = r.csrf; state.username = r.username; state.user = r.display_name || r.username; state.role = r.role || "admin"; p.value = "";
      boot();
    } catch (e) { err.textContent = e.message; err.hidden = false; btn.disabled = false; p.select(); }
  };
  clear($app).append(h("div", { class: "login-wrap" }, h("form", { class: "card login stack", onsubmit: submit },
    brandMark(), h("h1", null, "Connexion"), h("p", { class: "muted" }, "Panneau privé de gestion de serveurs Minecraft."), err,
    h("div", { class: "field" }, h("label", { for: "u" }, "Identifiant"), u),
    h("div", { class: "field" }, h("label", { for: "p" }, "Mot de passe"), p), btn)));
  (last !== "admin" || u.value ? p : u).focus();
}

const NAV_ADMIN = [
  ["Pilotage", [["#/dashboard", "dashboard", "Tableau de bord"], ["#/servers|#/server/", "server", "Mes serveurs"], ["#/create", "plus", "Créer un serveur"]]],
  ["Gestion", [["#/files", "folder", "Gestion des fichiers"], ["#/mods", "box", "Gestion des mods"], ["#/backups", "archive", "Sauvegardes"]]],
  ["Système", [["#/resources", "gauge", "Ressources système"], ["#/supervision", "activity", "Supervision Debian"]]],
  ["Administration", [["#/users", "users", "Comptes"], ["#/api", "code", "API & tokens"], ["#/notifications", "bell", "Notifications"], ["#/settings", "sliders", "Paramètres"]]],
];
const NAV_USER = [["Serveurs", [["#/home", "server", "Mes serveurs"]]], ["Mon espace", [["#/api", "code", "API & tokens"], ["#/account", "user", "Mon compte"]]]];

function layout() {
  const nav = isAdmin() ? NAV_ADMIN : NAV_USER;
  const side = h("aside", { class: "side" }, brandMark(),
    h("nav", { class: "nav", "aria-label": "Navigation principale" }, nav.map(([grp, items]) => [h("h4", null, grp),
      items.map(([href, ic, label]) => h("a", { href: href.split("|")[0], "data-match": href, "data-label": label, title: label }, icon(ic), h("span", null, label)))])),
    h("div", { class: "side-foot" }, "Version " + APP_VERSION));
  const bell = isAdmin() ? h("button", { id: "bell", class: "iconbtn bell", onclick: showAlerts, title: "Notifications", "aria-label": "Notifications" }, icon("bell")) : null;
  const scrim = h("div", { class: "scrim", onclick: () => { side.classList.remove("open"); scrim.classList.remove("open"); } });
  clear($app).append(h("div", { class: "shell" }, side, scrim,
    h("div", { class: "content" },
      h("header", { class: "top" },
        h("button", { class: "menu-btn iconbtn", "aria-label": "Menu", onclick: () => { side.classList.toggle("open"); scrim.classList.toggle("open", side.classList.contains("open")); } }, icon("menu")),
        h("div", { class: "title", id: "ptitle" }), isAdmin() ? h("span", { id: "rtt", class: "rtt muted small", title: "Temps de réponse entre ce navigateur et le panneau (aller-retour HTTP). Ce n'est pas le ping Minecraft d'un joueur." }) : null, bell,
        h("div", { class: "who" }, h("div", { class: "av" }, (state.user || "?").slice(0, 1)), h("div", { class: "txt" }, h("span", { class: "nm" }, state.user, h("small", null, isAdmin() ? "Administrateur" : "Utilisateur"))) ),
        h("button", { class: "iconbtn", onclick: logout, title: "Déconnexion", "aria-label": "Déconnexion" }, icon("logout"))),
      h("main", { class: "main", id: "main" }))));
}
async function logout() { await guard(() => api("POST", "/api/auth/logout")); showLogin(); }

/* ---- centre de notifications (V4 §5) : alertes actives + événements, notification immédiate des incidents ---- */
const notif = { items: [], unread: 0, critical: 0, lastId: null, timer: null };
function updateBell() {
  const b = document.getElementById("bell");
  if (!b) return;
  const active = state.alerts.filter((a) => !a.ack).length;
  b.querySelectorAll("b").forEach((x) => x.remove());
  const n = notif.critical + active;
  if (n) b.append(h("b", null, String(n > 99 ? "99+" : n)));
  else if (notif.unread) b.append(h("b", { class: "soft" }, String(notif.unread > 99 ? "99+" : notif.unread)));
  b.title = `Notifications : ${notif.unread} non lue(s), ${active} alerte(s) active(s)`;
}
/* temps de réponse navigateur ↔ panneau (V4 §6 : mesure faite depuis CE PC, pas depuis le serveur) */
const rttHist = [];
async function measureRtt() {
  const el = document.getElementById("rtt"); if (!el || document.hidden) return;
  const t0 = performance.now();
  try { const r = await fetch(`${API}/api/ping?t=${Date.now()}`, { cache: "no-store", credentials: "same-origin" }); await r.text(); } catch (e) { el.textContent = "Panneau injoignable"; return; }
  const ms = Math.round(performance.now() - t0); rttHist.push(ms); if (rttHist.length > 30) rttHist.shift();
  const med = [...rttHist].sort((a, b) => a - b)[Math.floor(rttHist.length / 2)];
  el.textContent = `Ce PC ↔ panneau : ${ms} ms`; el.title = `Temps de réponse entre ce navigateur et le panneau : ${ms} ms (médiane des ${rttHist.length} dernières mesures : ${med} ms). Ce n'est pas le ping Minecraft d'un joueur.`;
}
/* ---- graphiques (SVG construit par le DOM : compatible CSP) ---- */
const SVGNS = "http://www.w3.org/2000/svg";
function svg(tag, attrs, ...kids) { const el = document.createElementNS(SVGNS, tag); for (const [k, v] of Object.entries(attrs || {})) if (v !== null && v !== undefined) el.setAttribute(k, String(v)); for (const k of kids.flat()) if (k) el.append(k); return el; }
function lineChart(rows, key, o) {
  const W = 560, H = 150, L = 64, R = 10, T = 10, B = 22;
  const pts = rows.filter((r) => r[key] !== null && r[key] !== undefined).map((r) => [r.ts, +r[key]]);
  const box = h("div", { class: "chart" }, h("div", { class: "chart-h" }, h("b", null, o.title), h("span", { class: "muted small" }, pts.length ? `actuel ${o.fmt(pts[pts.length - 1][1])} · min ${o.fmt(Math.min(...pts.map((p) => p[1])))} · max ${o.fmt(Math.max(...pts.map((p) => p[1])))}` : "")));
  if (pts.length < 2) { box.append(h("div", { class: "chart-empty muted small" }, o.empty || "Pas encore assez de mesures (une par minute quand le serveur tourne).")); return box; }
  const x0 = pts[0][0], x1 = pts[pts.length - 1][0];
  let y0 = o.min ?? Math.min(...pts.map((p) => p[1])), y1 = o.max ?? Math.max(...pts.map((p) => p[1]));
  if (y1 === y0) { y1 += 1; y0 = Math.max(0, y0 - 1); }
  const X = (t) => L + (t - x0) / (x1 - x0 || 1) * (W - L - R), Y = (v) => T + (1 - (v - y0) / (y1 - y0)) * (H - T - B);
  const d = pts.map((p, i) => (i ? "L" : "M") + X(p[0]).toFixed(1) + " " + Y(p[1]).toFixed(1)).join(" ");
  const tm = (t) => new Date(t * 1000).toLocaleString("fr-FR", { hour: "2-digit", minute: "2-digit", day: x1 - x0 > 86400 ? "2-digit" : undefined, month: x1 - x0 > 86400 ? "2-digit" : undefined });
  const cross = svg("line", { class: "cross", y1: T, y2: H - B, x1: -10, x2: -10 }), dot = svg("circle", { class: "dot", r: 4, cx: -10, cy: -10 });
  const tip = h("div", { class: "chart-tip", hidden: true });
  const g = svg("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart-svg", role: "img", "aria-label": `${o.title} : ${pts.length} mesures, de ${o.fmt(Math.min(...pts.map((p) => p[1])))} à ${o.fmt(Math.max(...pts.map((p) => p[1])))}` },
    [y0, (y0 + y1) / 2, y1].map((v) => [svg("line", { class: "grid", x1: L, x2: W - R, y1: Y(v), y2: Y(v) }), svg("text", { class: "ax", x: L - 6, y: Y(v) + 4, "text-anchor": "end" }, document.createTextNode((o.axfmt || o.fmt)(v)))]),
    o.ref !== undefined ? svg("line", { class: "ref", x1: L, x2: W - R, y1: Y(o.ref), y2: Y(o.ref) }) : null,
    svg("text", { class: "ax", x: L, y: H - 6 }, document.createTextNode(tm(x0))), svg("text", { class: "ax", x: W - R, y: H - 6, "text-anchor": "end" }, document.createTextNode(tm(x1))),
    svg("path", { class: "line", d }), cross, dot, svg("rect", { class: "hit", x: L, y: T, width: W - L - R, height: H - T - B }));
  const hit = g.querySelector(".hit");
  hit.addEventListener("pointermove", (e) => {
    const r = g.getBoundingClientRect(), t = x0 + ((e.clientX - r.left) / r.width * W - L) / (W - L - R) * (x1 - x0);
    let best = pts[0]; for (const p of pts) if (Math.abs(p[0] - t) < Math.abs(best[0] - t)) best = p;
    cross.setAttribute("x1", X(best[0])); cross.setAttribute("x2", X(best[0])); dot.setAttribute("cx", X(best[0])); dot.setAttribute("cy", Y(best[1]));
    tip.hidden = false; tip.textContent = `${new Date(best[0] * 1000).toLocaleString("fr-FR")} — ${o.fmt(best[1])}`;
    tip.style.left = Math.min(r.width - 170, Math.max(0, X(best[0]) / W * r.width - 80)) + "px";
  });
  hit.addEventListener("pointerleave", () => { tip.hidden = true; cross.setAttribute("x1", -10); cross.setAttribute("x2", -10); dot.setAttribute("cx", -10); });
  box.append(h("div", { class: "chart-wrap" }, g, tip));
  return box;
}

async function pollNotifications() {
  if (!isAdmin() || !state.user) return;
  try {
    const r = await api("GET", "/api/notifications/center");
    const fresh = notif.lastId === null ? [] : r.items.filter((x) => x.id > notif.lastId && (x.severity === "critical" || x.type === "server.auto_restart"));
    notif.items = r.items; notif.unread = r.unread; notif.critical = r.critical_unread;
    notif.lastId = r.items.length ? r.items[0].id : (notif.lastId || 0);
    fresh.reverse().forEach((x) => toastAction(`${x.server_name ? "« " + x.server_name + " » " : ""}${x.label}`, x.data && x.data.crash_id ? ["Voir le rapport", () => showCrash(x.data.crash_id)] : null, x.severity === "critical" ? "error" : "warn"));
    updateBell();
  } catch (e) { /* hors ligne : on réessaie au prochain tour */ }
}
function toastAction(msg, action, kind) {
  toast(msg, kind);
  const last = document.querySelector("#toasts .toast:last-child");
  if (last && action) { last.append(h("button", { class: "sm", onclick: (ev) => { ev.stopPropagation(); last.remove(); action[1](); } }, action[0])); setTimeout(() => last.remove(), 15000); }
}
const SEV_ICON = { critical: "alert", warning: "alert", ok: "check", info: "info" };
function notifSummary(x) {
  const d = x.data || {};
  return d.reason || (d.confirmed && d.confirmed[0]) || (d.possible && d.possible[0] && "Cause possible : " + d.possible[0]) || d.line || d.message || (d.attempt ? `Tentative ${d.attempt}/3` : "");
}
function showAlerts() {
  const list = h("div", { class: "notif-list" }, notif.items.length ? notif.items.map((x) => h("div", { class: "notif " + x.severity + (x.unread ? " unread" : "") },
    icon(SEV_ICON[x.severity] || "info"),
    h("div", { class: "grow" }, h("div", null, h("b", null, x.server_name || "Machine"), " ", x.label), notifSummary(x) ? h("div", { class: "small muted" }, notifSummary(x)) : null,
      h("div", { class: "row small" }, h("span", { class: "muted" }, fmtDate(x.ts)), x.data && x.data.crash_id ? h("button", { class: "sm", onclick: () => showCrash(x.data.crash_id) }, "Voir le rapport") : null,
        x.server_id ? h("a", { href: `#/server/${x.server_id}`, onclick: () => document.querySelector(".modal-bg")?.remove() }, "Ouvrir le serveur") : null)))) : emptyState("check", "Aucune notification", "Les démarrages, arrêts, plantages et alertes apparaîtront ici."));
  const alerts = state.alerts.length ? h("div", { class: "stack" }, h("h3", null, "Alertes actives"), state.alerts.map((a) => h("div", { class: "alert " + a.level },
    h("div", null, h("div", null, a.message), h("div", { class: "row between small" }, h("span", { class: "muted" }, fmtDate(a.created_at)),
      a.ack ? h("span", { class: "muted" }, "Vue") : h("button", { class: "sm", onclick: async (ev) => { await guard(() => api("POST", `/api/alerts/${a.id}/ack`)); a.ack = 1; ev.currentTarget.replaceWith(h("span", { class: "muted" }, "Vue")); updateBell(); } }, "Marquer comme vue")))))) : null;
  modal("Notifications", h("div", { class: "stack" }, alerts, h("h3", null, "Derniers événements"), list), [{ label: "Tout marquer comme lu", onclick: async () => { await guard(() => api("POST", "/api/notifications/seen", {})); await pollNotifications(); } }, { label: "Fermer", onclick: () => {} }]);
}
async function showCrash(id) {
  let r;
  try { r = await api("GET", "/api/crashes/" + id); } catch (e) { fail(e); return; }
  const a = r.analysis || {};
  const KIND = { "server.crashed": "Plantage", "server.unexpected_stop": "Arrêt inattendu", "server.start_failed": "Échec de démarrage" };
  const dl = (name, text) => h("button", { class: "sm", onclick: () => { const u = URL.createObjectURL(new Blob([text], { type: "text/plain" })); const x = h("a", { href: u, download: name }); document.body.append(x); x.click(); x.remove(); setTimeout(() => URL.revokeObjectURL(u), 2000); } }, "Télécharger");
  const sec = (t, kids) => (kids && kids.length ? h("div", { class: "stack" }, h("h3", null, t), kids) : null);
  modal(`${KIND[r.kind] || "Incident"} — ${r.server_name}`, h("div", { class: "stack crash" },
    h("div", { class: "muted" }, fmtDate(r.ts) + (r.report_name ? ` · fichier ${r.report_name}` : " · aucun crash-report écrit par le serveur")),
    sec("Constaté dans les fichiers", (a.confirmed || []).map((x) => h("div", { class: "alert critical small" }, x))),
    sec("Causes possibles (non certaines)", (a.possible || []).map((x) => h("div", { class: "alert warning small" }, x))),
    sec("Mods ou composants à vérifier", (a.suspects || []).map((x) => h("div", { class: "small" }, h("b", null, x.name), h("span", { class: "muted" }, " — " + x.evidence)))),
    !(a.suspects || []).length ? h("p", { class: "hint" }, "Aucun mod n'est désigné par un indice fiable.") : null,
    sec("Dernières erreurs", (a.errors || []).length ? [h("pre", { class: "joblog" }, a.errors.join("\n"))] : []),
    sec("Trace d'exception", a.trace ? [h("pre", { class: "joblog" }, a.trace)] : []),
    h("details", null, h("summary", null, "Console avant l'incident (200 dernières lignes)"), h("pre", { class: "joblog" }, r.console || "—"), dl(`console-${r.id}.txt`, r.console || "")),
    r.report ? h("details", null, h("summary", null, "Crash-report complet"), h("pre", { class: "joblog" }, r.report), dl(r.report_name || `crash-${r.id}.txt`, r.report)) : null),
    [{ label: "Fermer", onclick: () => {} }]);
}

function connectStats() {
  if (!state.user) return;
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws/stats`);
  state.statsWs = ws;
  ws.onmessage = (e) => {
    state.snap = JSON.parse(e.data);
    state.alerts = state.snap.alerts || [];
    updateBell();
    if (state.view && state.view.onStats) state.view.onStats(state.snap);
  };
  ws.onclose = () => { if (state.user) setTimeout(connectStats, 3000); };
}

async function boot() {
  layout();
  connectStats();
  if (isAdmin()) {
    try { const l = await api("GET", "/api/alerts"); state.alerts = l.alerts; updateBell(); } catch (e) { /* ignoré */ }
    await pollNotifications();
    clearInterval(notif.timer); notif.timer = setInterval(() => { pollNotifications(); measureRtt(); }, 5000); measureRtt();
  }
  route();
}

async function start() {
  try {
    const me = await api("GET", "/api/auth/me", undefined, { quiet: true });
    state.csrf = me.csrf; state.username = me.username; state.user = me.display_name || me.username; state.role = me.role || "admin";
    boot();
  } catch (e) { showLogin(); }
}
const $app = document.getElementById("app");

"use strict";
/* Écrans : tableau de bord, serveurs, création, détail d'un serveur (console, fichiers, mods, sauvegardes, configuration), ressources, paramètres. */

function srvOf(id) { return state.snap && state.snap.servers.find((s) => s.id === id); }
async function loadServers() { return (await api("GET", "/api/servers")).servers; }
const connInfo = (s) => `${location.hostname}:${s.port}`;

async function serverAction(s, action) {
  await guard(async () => {
    const r = await api("POST", `/api/servers/${s.id}/${action}`);
    if (r && r.job) { trackStartJob(s, r.job); return; }
    toast(`« ${s.name} » : ${action === "start" ? "démarrage demandé" : action === "stop" ? "arrêt demandé" : "redémarrage demandé"}`, "ok");
  });
}
async function trackStartJob(s, jid) {
  toast(`« ${s.name} » : son monde passe d'abord sur le SSD (copie vérifiée), puis le serveur démarre.`, "ok");
  const j = await trackJob(jid);
  toast(j.status === "done" ? `« ${s.name} » est sur le SSD et démarre.` : `« ${s.name} » : ${j.error}`, j.status === "done" ? "ok" : "error");
}
async function startWithEula(s) {
  if (s.eula_accepted) return serverAction(s, "start");
  const chk = h("input", { type: "checkbox", id: "eulachk" });
  const m = modal("Contrat de licence Minecraft (EULA)", h("div", { class: "stack" },
    h("p", null, "Avant le premier démarrage, vous devez accepter le contrat de licence utilisateur final de Minecraft publié par Mojang / Microsoft."),
    h("p", null, h("a", { href: "https://aka.ms/MinecraftEULA", target: "_blank", rel: "noopener noreferrer" }, "Lire le texte officiel : https://aka.ms/MinecraftEULA")),
    h("label", { class: "inline", for: "eulachk" }, chk, "J'ai lu et j'accepte le contrat de licence Minecraft (EULA).")),
    [{ label: "Annuler", onclick: () => {} },
     { label: "Accepter et démarrer", kind: "primary", onclick: async () => {
        if (!chk.checked) { toast("Cochez la case pour accepter.", "error"); return false; }
        try { await api("POST", `/api/servers/${s.id}/eula`, { accept: true }); const r = await api("POST", `/api/servers/${s.id}/start`); if (r && r.job) trackStartJob(s, r.job); else toast("Serveur en démarrage.", "ok"); } catch (e) { fail(e); }
      } }]);
  return m;
}

function serverCard(s) {
  const ramBar = bar(0);
  const cpu = h("b", null, "—"), ram = h("b", null, "—"), up = h("b", null, "—"), pl = h("b", null, "—"), st = h("span"), lp = h("span");
  const tps = h("b", null, "—"), mspt = h("b", null, "—"), avail = h("b", null, "—");
  const prim = h("span"), addr = h("div", null, copyLine(connInfo(s)));
  let lastPort = s.port;
  const primBtn = ilink("star", "Rendre principal", "#", "", (e) => { e.preventDefault(); setPrimary(srvOf(s.id) || s); });
  const stat = (label, el) => h("div", null, h("small", null, label), el);
  const card = h("div", { class: "card srv", "data-id": s.id },
    h("div", { class: "head" }, lp, h("h3", { class: "grow" }, h("a", { href: "#/server/" + s.id }, s.name)), prim, st),
    h("div", { class: "meta" }, h("span", { class: "chip strong" }, (TYPES[s.server_type] || s.server_type) + (s.loader_version ? " " + s.loader_version : "")), h("span", { class: "chip" }, "Minecraft " + s.mc_version), h("span", { class: "chip" }, "Java " + (s.java_major || "?"))),
    s.description ? h("div", { class: "desc" }, s.description) : null,
    addr,
    h("div", { class: "stats six" }, stat("Disponibilité", avail), stat("Joueurs", pl), stat("Actif depuis", up), stat("TPS", tps), stat("Latence serveur", mspt), stat("Processeur", cpu)),
    h("div", { class: "stats one" }, stat("Mémoire", ram)), ramBar,
    s.error ? h("div", { class: "alert critical small" }, icon("alert"), h("div", null, s.error)) : null,
    h("div", { class: "row" },
      ibtn("play", "Démarrer", { class: "primary sm", "data-act": "start", onclick: () => startWithEula(srvOf(s.id) || s) }),
      ibtn("stop", "Arrêter", { class: "sm", "data-act": "stop", onclick: async () => { if (await confirmBox("Arrêter le serveur", `Arrêter « ${s.name} » proprement ?`, "Arrêter")) serverAction(s, "stop"); } }),
      ibtn("restart", "Redémarrer", { class: "sm", "data-act": "restart", onclick: async () => { if (await confirmBox("Redémarrer le serveur", `Redémarrer « ${s.name} » ?`, "Redémarrer")) serverAction(s, "restart"); } })),
    h("div", { class: "links" },
      [["console", "terminal", "Console"], ["files", "folder", "Fichiers"], ["config", "sliders", "Configuration"], ["backups", "archive", "Sauvegarder"]].map(([t, ic, l]) => ilink(ic, l, `#/server/${s.id}/${t}`)),
      primBtn, ilink("trash", "Supprimer", `#/server/${s.id}/config`, "danger")));
  card.update = (n) => {
    clear(prim).append(n.primary ? h("span", { class: "badge primary-port", title: "Ce serveur utilise le port principal : les joueurs s'y connectent sans préciser de port." }, "★ Principal") : "");
    primBtn.hidden = !!n.primary || n.status !== "installed" || !isAdmin();
    if (n.port !== lastPort) { lastPort = n.port; clear(addr).append(copyLine(connInfo(n))); }
    const r = ["running", "starting", "stopping"].includes(n.state);
    clear(st).append(badge(n.state)); clear(lp).append(lamp(n.state));
    ram.textContent = r ? `${fmtMb(n.rss_mb)} / ${fmtMb(n.ram_max_mb)}` : "—";
    cpu.textContent = r ? n.cpu.toFixed(0) + " %" : "—";
    up.textContent = r ? fmtDur(n.uptime) : "—";
    avail.textContent = n.state === "running" ? (n.players !== null && n.players !== undefined ? "Ouvert" : "Ne répond pas") : n.state === "starting" ? "Démarrage" : "Fermé";
    avail.className = n.state === "running" && n.players !== null && n.players !== undefined ? "ok-t" : n.state === "running" ? "bad-t" : "";
    const na = !n.perf_available;
    tps.textContent = n.state !== "running" ? "—" : na ? "n/d" : n.tps !== null && n.tps !== undefined ? n.tps.toFixed(1) : "mesure…";
    tps.title = na ? "Non disponible : " + (n.perf_method || "") : "TPS : capacité à tenir 20 ticks par seconde (" + (n.perf_method || "") + ")";
    tps.className = n.state === "running" && !na && n.tps !== null && n.tps !== undefined ? (n.tps >= 19 ? "ok-t" : n.tps >= 15 ? "warn-t" : "bad-t") : "";
    mspt.textContent = n.state !== "running" ? "—" : na ? "n/d" : n.mspt !== null && n.mspt !== undefined ? n.mspt.toFixed(1) + " ms" : n.tps !== null && n.tps !== undefined ? "non mesurée" : "mesure…";
    mspt.title = na ? "Non disponible : " + (n.perf_method || "") : "Temps de calcul moyen d'une tick (au-delà de 50 ms le serveur ralentit)";
    pl.textContent = n.players !== null && n.players !== undefined ? `${n.players} / ${n.max_players}` : "—";
    setBar(ramBar, n.ram_max_mb ? (n.rss_mb / n.ram_max_mb) * 100 : 0);
    const b = (a) => card.querySelector(`[data-act=${a}]`);
    b("start").disabled = r || ["installing", "error", "awaiting_jar"].includes(n.state) && n.status !== "installed";
    b("stop").disabled = !(n.state === "running" || n.state === "starting");
    b("restart").disabled = n.state !== "running";
  };
  card.update(s);
  return card;
}

/* ---- port principal (V4 §4) ---- */
async function setPrimary(s) {
  let plan;
  try { plan = await api("GET", `/api/servers/${s.id}/primary/plan`); } catch (e) { fail(e); return; }
  const rows = plan.changes.map((c) => h("tr", null, h("td", null, c.name), h("td", { class: "mono" }, String(c.from)), h("td", { class: "mono" }, String(c.to) + (c.to === plan.primary_port ? " ★" : "")),
    h("td", null, c.running ? h("span", { class: "badge running" }, "redémarrera") : h("span", { class: "muted small" }, "arrêté")), h("td", { class: "small" }, c.running ? (c.players_online ? `${c.players_online} joueur(s)${c.players.length ? " : " + c.players.join(", ") : ""}` : "aucun joueur") : "—")));
  const content = h("div", { class: "stack" },
    h("p", null, `« ${s.name} » va recevoir le port principal ${plan.primary_port}. Les autres serveurs gardent leur port, sauf l'ancien principal qui reçoit un port secondaire libre.`),
    h("div", { class: "tablewrap" }, h("table", null, h("thead", null, h("tr", null, ["Serveur", "Port actuel", "Nouveau port", "État", "Joueurs connectés"].map((t) => h("th", null, t)))), h("tbody", null, rows))),
    plan.restart.length ? h("div", { class: "alert warning small" }, `Redémarrage automatique de : ${plan.restart.join(", ")}.` + (plan.players_affected ? ` ${plan.players_affected} joueur(s) seront déconnectés.` : "")) : null,
    h("p", { class: "hint" }, "Si le nouveau serveur principal ne répond pas après son redémarrage, l'ancienne configuration est automatiquement remise en place."));
  modal("Changer de serveur principal", content, [{ label: "Annuler", onclick: () => true }, { label: plan.restart.length ? "Confirmer et redémarrer" : "Confirmer", kind: "primary", onclick: async () => {
    try { const r = await api("POST", `/api/servers/${s.id}/primary`, { confirm: true }); const j = await trackJob(r.job); toast(j.status === "done" ? (j.result && j.result.verified === "joignable" ? `« ${s.name} » est le serveur principal et répond sur le port ${plan.primary_port}.` : `« ${s.name} » est le serveur principal (port ${plan.primary_port} au prochain démarrage).`) : "Échec, configuration précédente restaurée : " + j.error, j.status === "done" ? "ok" : "error"); route(); } catch (e) { fail(e); }
  } }]);
}

/* liste de cartes synchronisée avec l'instantané (création, mise à jour, retrait) */
function syncCards(grid, cards, servers, empty) {
  if (!servers.length) { if (!cards.__empty) { clear(grid).append(empty); cards.__empty = true; } } else if (cards.__empty) { clear(grid); cards.__empty = false; }
  for (const s of servers) { if (!cards[s.id]) { cards[s.id] = serverCard(s); grid.append(cards[s.id]); } cards[s.id].update(s); }
  for (const id of Object.keys(cards)) if (id !== "__empty" && !servers.find((s) => s.id === id)) { cards[id].remove(); delete cards[id]; }
}
/* deux catégories horizontales : serveurs allumés (cases un peu plus grandes) puis serveurs arrêtés */
const LIVE_STATES = ["starting", "running", "stopping"];
function splitGrids() {
  const liveG = h("div", { class: "grid live" }), offG = h("div", { class: "grid" });
  const liveH = h("h2", { class: "cat" }), offH = h("h2", { class: "cat" }), all = h("div");
  const liveC = {}, offC = {};
  const el = h("div", { class: "srvcats" }, h("section", null, liveH, liveG), h("section", null, offH, offG), all);
  const sync = (servers) => {
    const live = servers.filter((x) => LIVE_STATES.includes(x.state)), off = servers.filter((x) => !LIVE_STATES.includes(x.state));
    const none = !servers.length;
    el.querySelectorAll("section").forEach((sec) => { sec.hidden = none; });
    if (none) { if (!all.firstChild) all.append(noServer()); } else clear(all);
    liveH.textContent = `En ligne (${live.length})`; offH.textContent = `Arrêtés (${off.length})`;
    syncCards(liveG, liveC, live, h("p", { class: "muted small catempty" }, "Aucun serveur allumé pour l'instant."));
    syncCards(offG, offC, off, h("p", { class: "muted small catempty" }, "Tous les serveurs sont allumés."));
  };
  return { el, sync };
}
const noServer = () => emptyState("server", "Aucun serveur pour l'instant", "Créez votre premier serveur : il sera prêt en quelques minutes.", ilink("plus", "CRÉER UN SERVEUR", "#/create", "btn primary"));

/* ---------------- tableau de bord ---------------- */
addRoute(/^#\/dashboard$/, async (main) => {
  const tiles = {};
  const tile = (key, ic, title) => { const v = h("div", { class: "big-num" }, "—"), sub = h("div", { class: "muted small" }), b = bar(0); tiles[key] = { v, sub, b }; return h("div", { class: "card tile" }, h("div", { class: "t" }, icon(ic), title), v, b, sub); };
  const alertsBox = h("div"), cats = splitGrids(), summary = h("p");
  main.append(pageHead("Tableau de bord", summary, ilink("plus", "CRÉER UN SERVEUR", "#/create", "btn primary")), alertsBox,
    h("div", { class: "grid small" }, tile("cpu", "cpu", "Processeur"), tile("ram", "ram", "Mémoire"), tile("ssd", "disk", "Disque système"), tile("hdd", "disk", "Disque de données")),
    cats.el);
  const render = (snap) => {
    const sy = snap.system;
    tiles.cpu.v.textContent = sy.cpu_percent.toFixed(0) + " %"; setBar(tiles.cpu.b, sy.cpu_percent); tiles.cpu.sub.textContent = `Charge : ${sy.load.join(" / ")} · ${sy.cpus} threads`;
    const mp = sy.mem.total_mb ? (sy.mem.used_mb / sy.mem.total_mb) * 100 : 0;
    tiles.ram.v.textContent = fmtMb(sy.mem.used_mb); setBar(tiles.ram.b, mp); tiles.ram.sub.textContent = `${fmtMb(sy.mem.available_mb)} disponibles sur ${fmtMb(sy.mem.total_mb)}`;
    for (const [k, d] of [["ssd", sy.disk_ssd], ["hdd", sy.disk_data]]) { tiles[k].v.textContent = d.percent + " %"; setBar(tiles[k].b, d.percent); tiles[k].sub.textContent = `${fmtBytes(d.free)} libres sur ${fmtBytes(d.total)}`; }
    const up = snap.servers.filter((x) => x.state === "running"), pl = up.reduce((a, x) => a + (x.players || 0), 0);
    summary.textContent = snap.servers.length ? `${up.length} serveur(s) en ligne sur ${snap.servers.length} · ${pl} joueur(s) connecté(s) · machine allumée depuis ${fmtDur(sy.uptime)}` + ((p) => (p ? ` · serveur principal : ${p.name} (port ${p.port})` : " · aucun serveur sur le port principal"))(snap.servers.find((x) => x.primary)) : "Bienvenue. Aucun serveur n'est encore créé.";
    clear(alertsBox).append((snap.alerts || state.alerts).slice(0, 4).map((a) => h("div", { class: "alert " + a.level }, icon(a.level === "info" ? "info" : "alert"), h("div", null, a.message))));
    cats.sync(snap.servers);
  };
  if (state.snap) render(state.snap); else render((await api("GET", "/api/system")));
  return { onStats: render };
});

/* ---------------- mes serveurs ---------------- */
addRoute(/^#\/servers$/, async (main) => {
  const cats = splitGrids();
  main.append(pageHead("Mes serveurs", "Démarrez, arrêtez et ouvrez chacun de vos serveurs.", ilink("plus", "CRÉER UN SERVEUR", "#/create", "btn primary")), cats.el);
  const render = (snap) => cats.sync(snap.servers);
  render(state.snap || (await api("GET", "/api/system")));
  return { onStats: render };
});

/* ---------------- sélecteur de serveur pour les pages globales ---------------- */
function picker(tab, title, text) {
  return async (main) => {
    const list = await loadServers();
    main.append(pageHead(title, text),
      list.length ? h("div", { class: "grid" }, list.map((s) => h("a", { class: "card stack", href: `#/server/${s.id}/${tab}` }, h("div", { class: "row" }, lamp(s.state || "stopped"), h("h3", null, s.name)), h("div", { class: "muted small" }, typeLine(s))))) : noServer());
  };
}
addRoute(/^#\/files$/, picker("files", "Gestion des fichiers", "Choisissez un serveur pour parcourir et modifier ses fichiers."));
addRoute(/^#\/mods$/, picker("mods", "Gestion des mods", "Choisissez un serveur pour gérer ses mods et importer un modpack."));
addRoute(/^#\/backups$/, picker("backups", "Sauvegardes", "Choisissez un serveur pour créer, restaurer ou télécharger ses sauvegardes."));

/* ---------------- création d'un serveur ---------------- */
/* ---- RAM (V4 §1) : propositions 10 Go / 12 Go / maximum et résumé utilisée / attribuée / disponible / restante ---- */
function ramSummary(b) {
  const it = (l, v, t) => h("div", { class: "ramsum-i", title: t }, h("div", { class: "muted small" }, l), h("strong", null, fmtMb(v)));
  return h("div", { class: "ramsum" }, it("Utilisée", b.used_mb, "Mémoire réellement occupée par les serveurs en marche"), it("Attribuée", b.assigned_mb, "Somme des RAM max des serveurs en marche"),
    it("Disponible", b.available_mb, "Mémoire libre de la machine selon Debian"), it("Restante", b.remaining_mb, "Budget serveurs encore libre : RAM totale − réserve − serveurs en marche (marge Java comprise)"));
}
function ramPresets(b, xmx, xms, after) {
  const btns = (b.presets || []).map((p) => h("button", { type: "button", class: "sm" + (+xmx.value === p.mb ? " primary" : ""), disabled: !p.ok, title: p.ok ? "" : "Non proposé : " + p.reason,
    onclick: () => { xmx.value = p.mb; xms.value = Math.max(512, Math.floor(p.mb / 2 / 256) * 256); sync(); if (after) after(); } }, p.label));
  const sync = () => btns.forEach((x, i) => x.classList.toggle("primary", +xmx.value === b.presets[i].mb));
  xmx.addEventListener("input", sync);
  const off = (b.presets || []).filter((p) => !p.ok);
  return h("div", { class: "stack" }, h("div", { class: "row" }, btns), off.length ? h("div", { class: "hint" }, off.map((p) => `${p.label} non proposé (${p.reason}).`).join(" ")) : null, ramSummary(b));
}
/* ---- champs server.properties génériques (création) ---- */
function propForm(schema, mcGetter) {
  const by = Object.fromEntries(schema.fields.map((x) => [x.key, x])), ctl = {}, rows = {};
  const vt = (v) => (String(v || "").match(/\d+/g) || ["0"]).slice(0, 3).map(Number);
  const ge = (a, b) => { for (let i = 0; i < 3; i++) { if ((a[i] || 0) !== (b[i] || 0)) return (a[i] || 0) > (b[i] || 0); } return true; };
  const field = (k) => {
    const x = by[k]; if (!x) return null;
    let el;
    if (x.type === "bool") el = h("input", { type: "checkbox", checked: x.default === "true" });
    else if (x.type === "enum") el = h("select", null, x.options.map((o) => h("option", { value: o, selected: o === x.default }, PROP_LABELS[o] || o)));
    else el = h("input", { type: x.type === "int" ? "number" : "text", value: x.default, min: x.min ?? null, max: x.max ?? null, maxlength: x.type === "int" ? null : x.maxlen, autocomplete: "off", list: k === "level-type" ? "lvtypes" : null });
    ctl[k] = el;
    const r = h("div", { class: "field" }, x.type === "bool" ? h("label", { class: "inline" }, el, x.label) : [h("label", null, x.label), el], h("div", { class: "hint" }, x.description), k === "level-type" ? h("datalist", { id: "lvtypes" }, ["minecraft:normal", "minecraft:flat", "minecraft:large_biomes", "minecraft:amplified", "minecraft:single_biome_surface"].map((v) => h("option", { value: v }))) : null);
    rows[k] = r;
    return r;
  };
  const values = () => {
    const out = {};
    for (const [k, el] of Object.entries(ctl)) {
      if (rows[k].hidden) continue;
      const v = by[k].type === "bool" ? String(el.checked) : el.value;
      if (v !== by[k].default) out[k] = v;
    }
    return out;
  };
  const refreshVersion = () => { const mc = vt(mcGetter()); for (const [k, r] of Object.entries(rows)) r.hidden = !!by[k].min_mc && !ge(mc, vt(by[k].min_mc)); };
  return { field, values, refreshVersion };
}
const PROP_LABELS = { survival: "Survie", creative: "Créatif", adventure: "Aventure", spectator: "Spectateur", peaceful: "Paisible", easy: "Facile", normal: "Normale", hard: "Difficile" };

addRoute(/^#\/create$/, async (main) => {
  const lim = await api("GET", "/api/meta/limits");
  const f = { type: "vanilla", mc: "", loader: "", snaps: false };
  const name = h("input", { type: "text", id: "n", maxlength: 48, placeholder: "Survie entre amis" }), desc = h("input", { type: "text", id: "d", maxlength: 500 });
  const slugPrev = h("span", { class: "mono" });
  const nameHint = h("div", { class: "hint" }, "Nom libre : accents, espaces et emojis acceptés. Dossier créé sur le disque : ", slugPrev);
  const slugify = (t) => { const x = t.normalize("NFKD").replace(/[\u0300-\u036f]/g, "").replace(/[^A-Za-z0-9_.-]+/g, "-").replace(/^[-._]+|[-._]+$/g, "").slice(0, 48); return x || "serveur"; };
  name.oninput = () => { slugPrev.textContent = slugify(name.value); }; name.oninput();
  const adapters = Object.fromEntries((lim.adapters || []).map((a) => [a.key, a]));
  (lim.adapters || []).forEach((a) => { TYPES[a.key] = a.label; });
  const FAM = [["vanilla", "Officiel"], ["loader", "Mods (chargeurs)"], ["plugins", "Plugins"], ["hybrid", "Hybrides mods + plugins"], ["custom", "Autres"]];
  const typeSel = h("select", { id: "t" }, FAM.map(([fk, fl]) => { const items = (lim.adapters || []).filter((a) => a.family === fk); return items.length ? h("optgroup", { label: fl }, items.map((a) => h("option", { value: a.key }, a.label + (a.auto_install ? "" : "  (jar à fournir)")))) : null; }));
  const typeNote = h("div", { class: "hint" });
  const mcSel = h("select", { id: "mc" }), snap = h("input", { type: "checkbox", id: "sn" });
  const ldSel = h("select", { id: "ld" }), ldRow = h("div", { class: "field" });
  const mcHint = h("div", { class: "hint" }), javaHint = h("div", { class: "hint" });
  const maxX = lim.ram.max_xmx_mb;
  const firstOk = (lim.ram.presets || []).find((p) => p.ok);
  const xmx = h("input", { type: "number", id: "xmx", min: 512, max: maxX, step: 256, value: firstOk ? firstOk.mb : Math.min(4096, maxX) });
  const xms = h("input", { type: "number", id: "xms", min: 256, step: 256, value: Math.max(512, Math.floor((+xmx.value) / 2 / 256) * 256) });
  const port = h("input", { type: "number", id: "port", min: 1024, max: 65535, value: lim.suggested_port });
  const presets = ramPresets(lim.ram, xmx, xms, () => refreshForecast());
  const forecast = h("div", { class: "alert warning", hidden: true });
  let fcTimer = null;
  const refreshForecast = () => {
    clearTimeout(fcTimer);
    fcTimer = setTimeout(async () => {
      try {
        const r = await api("GET", "/api/meta/ram-forecast?xmx=" + (+xmx.value || 0));
        forecast.hidden = !r.overcommit; forecast.textContent = r.message;
      } catch (e) { forecast.hidden = true; }
    }, 250);
  };
  xmx.oninput = refreshForecast; xmx.onchange = refreshForecast;
  const status = h("div", { class: "hint" }), go_ = h("button", { class: "primary big", type: "submit" }, "INSTALLER LE SERVEUR");
  const prog = h("div", { class: "card", hidden: true });
  const schema = await api("GET", "/api/meta/mcsettings-schema");
  const pf = propForm(schema, () => mcSel.value);
  const MAIN_GAME = ["gamemode", "difficulty", "hardcore", "pvp", "max-players", "motd"], MAIN_WORLD = ["level-seed", "level-type", "generate-structures", "spawn-protection", "view-distance", "simulation-distance"];
  const wlOn = h("input", { type: "checkbox", id: "wlon" }), wlPlayers = h("textarea", { id: "wlp", rows: 2, placeholder: "Pseudos séparés par des virgules ou des espaces (facultatif)" });
  wlPlayers.oninput = () => { if (wlPlayers.value.trim()) wlOn.checked = true; };
  const cracked = h("input", { type: "checkbox", id: "crk" });
  const others = schema.fields.filter((x) => !x.managed && !MAIN_GAME.includes(x.key) && !MAIN_WORLD.includes(x.key) && x.key !== "online-mode");

  const loadMc = async () => {
    status.textContent = "Chargement des versions depuis les sources officielles…";
    try {
      const r = await api("GET", `/api/meta/minecraft?type=${f.type}&snapshots=${snap.checked}`);
      clear(mcSel).append(r.versions.map((v) => h("option", { value: v.id }, v.id + (v.stable ? "" : "  (snapshot — expérimental)"))));
      mcHint.textContent = `${r.versions.length} versions compatibles avec ${TYPES[f.type]}.`;
      status.textContent = "";
      await loadLoader();
    } catch (e) { status.textContent = e.message; clear(mcSel); }
  };
  const loadLoader = async () => {
    const mc = mcSel.value; f.mc = mc;
    const ad = adapters[f.type] || {};
    ldRow.hidden = !ad.needs_loader;
    typeNote.textContent = (ad.notes || "") + (ad.auto_install ? "" : " Après la création, vous déposerez le jar ; il est analysé sans être exécuté.");
    javaHint.textContent = "";
    if (!mc) return;
    try {
      const j = await api("GET", "/api/meta/java-need?mc=" + enc(mc));
      javaHint.textContent = `Java ${j.runtime} sera utilisé (${j.installed ? "déjà installé" : "téléchargé automatiquement depuis Adoptium"}).`;
    } catch (e) { javaHint.textContent = e.message; }
    if (!(adapters[f.type] || {}).needs_loader) { clear(ldSel); return; }
    try {
      const r = await api("GET", `/api/meta/loaders?type=${f.type}&mc=${enc(mc)}`);
      clear(ldSel).append(r.versions.map((v, i) => h("option", { value: v.version }, v.version + (v.recommended ? "  ★ recommandée" : v.latest ? "  (dernière)" : v.stable === false ? "  (instable)" : v.beta ? "  (bêta)" : ""))));
      const rec = r.versions.find((v) => v.recommended) || r.versions.find((v) => v.stable !== false && !v.beta);
      if (rec) ldSel.value = rec.version;
      if (!r.versions.length) ldSel.append(h("option", { value: "" }, "Aucune version disponible"));
    } catch (e) { clear(ldSel).append(h("option", { value: "" }, e.message)); }
  };
  typeSel.onchange = () => { f.type = typeSel.value; loadMc(); };
  snap.onchange = loadMc; mcSel.onchange = () => { loadLoader(); pf.refreshVersion(); };

  const submit = async (ev) => {
    ev.preventDefault();
    go_.disabled = true;
    try {
      const body = { name: name.value.trim(), description: desc.value, server_type: f.type, mc_version: mcSel.value, loader_version: !(adapters[f.type] || {}).needs_loader ? null : ldSel.value || null,
        ram_max_mb: +xmx.value, ram_min_mb: +xms.value, port: +port.value, properties: { ...pf.values(), ...(cracked.checked ? { "online-mode": "false" } : {}) },
        whitelist: { enabled: wlOn.checked, players: wlPlayers.value } };
      if (cracked.checked) {
        if (!(await confirmBox("Accès cracké (online-mode désactivé)", "Sans vérification Mojang, n'importe qui peut se connecter sous le pseudo d'un autre joueur, y compris celui d'un opérateur. Activez la whitelist et n'utilisez ce mode que sur un réseau de confiance.", "Je comprends, continuer", true))) { go_.disabled = false; return; }
        body.confirm_offline = true;
      }
      const r = await api("POST", "/api/servers", body);
      if (!r.job) { location.hash = `#/server/${r.server.id}`; toast("Serveur créé : il attend maintenant son jar.", "ok"); return; }
      prog.hidden = false;
      const stepBox = h("pre", { class: "joblog" }), pb = bar(0), title = h("h3", null, "Installation en cours…");
      clear(prog).append(title, pb, h("div", { class: "muted small", id: "stepname" }), stepBox);
      const j = await trackJob(r.job, (j) => { setBar(pb, j.progress); prog.querySelector("#stepname").textContent = j.step; stepBox.textContent = j.log; stepBox.scrollTop = stepBox.scrollHeight; });
      if (j.status === "done") {
        clear(prog).append(h("h3", null, "Installation terminée"), h("pre", { class: "joblog" }, j.log.split("\n").slice(-12).join("\n")),
          h("div", { class: "row" }, h("a", { class: "btn primary", href: `#/server/${r.server.id}/mods` }, "Importer un modpack"), h("a", { class: "btn", href: `#/server/${r.server.id}` }, "Ouvrir le serveur")));
      } else {
        clear(prog).append(h("div", { class: "alert critical" }, "Échec de l'installation : " + j.error), h("pre", { class: "joblog" }, j.log), h("p", { class: "muted small" }, "Le journal complet est conservé sur le disque. Corrigez le problème puis relancez l'installation depuis la page du serveur."),
          h("a", { class: "btn", href: `#/server/${r.server.id}` }, "Ouvrir le serveur"));
      }
    } catch (e) { fail(e); go_.disabled = false; }
  };

  const sec = (n, t, ...k) => h("div", { class: "section form-sec" }, h("h2", null, h("span", { class: "step" }, String(n)), t), k);
  main.append(pageHead("Créer un serveur", `Maximum par serveur : ${fmtMb(maxX)} (RAM totale ${fmtMb(lim.ram.total_mb)} − réserve système ${fmtMb(lim.ram.reserve_mb)}, marge Java de ${lim.ram.overhead_pct} % comprise). Les serveurs en marche ne réduisent pas ce plafond.`),
    h("form", { class: "card", onsubmit: submit },
      sec(1, "Informations générales", h("div", { class: "field" }, h("label", { for: "n" }, "Nom du serveur"), name, nameHint), h("div", { class: "field" }, h("label", { for: "d" }, "Description (facultative)"), desc)),
      sec(2, "Type et version", h("div", { class: "field" }, h("label", { for: "t" }, "Type de serveur"), typeSel, typeNote),
        h("div", { class: "field" }, h("label", { for: "mc" }, "Version de Minecraft"), mcSel, h("label", { class: "inline small", for: "sn" }, snap, "Afficher les snapshots (expérimental)"), mcHint, javaHint),
        (ldRow.append(h("label", { for: "ld" }, "Version du chargeur / build"), ldSel, h("div", { class: "hint" }, "Seules les versions réellement compatibles avec la version Minecraft choisie sont proposées.")), ldRow)),
      sec(3, "Mémoire et réseau", presets, forecast, h("div", { class: "row" }, h("div", { class: "field" }, h("label", { for: "xmx" }, "RAM maximale (-Xmx, Mo)"), xmx), h("div", { class: "field" }, h("label", { for: "xms" }, "RAM minimale (-Xms, Mo)"), xms), h("div", { class: "field" }, h("label", { for: "port" }, "Port"), port)),
        h("div", { class: "hint" }, "Le port proposé est le premier libre à partir de 25565.")),
      sec(4, "Partie", h("div", { class: "propgrid" }, MAIN_GAME.map((k) => pf.field(k)))),
      sec(5, "Monde", h("div", { class: "propgrid" }, MAIN_WORLD.map((k) => pf.field(k))), h("div", { class: "hint" }, "La graine et le type de monde ne s'appliquent qu'à la création du monde, au premier démarrage.")),
      sec(6, "Accès des joueurs", h("label", { class: "inline" }, wlOn, "Activer la whitelist (seuls les joueurs listés pourront se connecter)"), h("div", { class: "field" }, h("label", { for: "wlp" }, "Joueurs autorisés"), wlPlayers),
        h("label", { class: "inline" }, cracked, "Accès cracké : désactiver la vérification des comptes Mojang (online-mode)"), h("div", { class: "hint" }, "Déconseillé : n'importe qui pourrait prendre le pseudo d'un autre joueur. Une confirmation sera demandée.")),
      sec(7, "Autres réglages", h("details", null, h("summary", { class: "muted" }, `Afficher les ${others.length} autres réglages de server.properties (facultatif)`), h("div", { class: "propgrid" }, others.map((x) => pf.field(x.key)))),
        h("div", { class: "hint" }, "Les fichiers propres au serveur (paper-global.yml, configs de mods…) sont créés au premier démarrage : ils seront modifiables ensuite.")),
      sec(8, "Installation", status, go_)), prog);
  loadMc().then(() => pf.refreshVersion());
});

/* ---------------- détail d'un serveur ---------------- */
const TABS = [["", "Vue d'ensemble", "dashboard"], ["console", "Console", "terminal"], ["files", "Fichiers", "folder"], ["mods", "Mods & modpack", "box"], ["stats", "Statistiques", "activity"], ["whitelist", "Whitelist", "users"], ["mcsettings", "Paramètres Minecraft", "sliders"], ["backups", "Sauvegardes", "archive"], ["config", "Configuration", "key"]];
addRoute(/^#\/server\/([^/]+)\/?([a-z]*)$/, async (main, sid, tab) => {
  const s = await api("GET", "/api/servers/" + sid);
  const hl = h("span"), hb = h("div");
  const head = h("div", { class: "page-head" }, h("div", { class: "row" }, hl, h("h1", null, s.name), hb, s.primary ? h("span", { class: "badge primary-port" }, "★ Principal") : null), h("div", { class: "row" }, h("span", { class: "chip" }, typeLine(s)), copyLine(connInfo(s))));
  main.append(head, h("nav", { class: "tabs", "aria-label": "Sections du serveur" }, TABS.map(([t, l, ic]) => h("a", { href: `#/server/${sid}${t ? "/" + t : ""}`, class: tab === t ? "active" : "" }, icon(ic), l))));
  const body = h("div"); main.append(body);
  const upd = (n) => { clear(hb).append(badge(n.state)); clear(hl).append(lamp(n.state)); };
  setTitle(s.name);
  upd(s);
  const tabFn = { "": tabOverview, console: tabConsole, files: tabFiles, mods: tabMods, stats: tabStats, whitelist: tabWhitelist, mcsettings: tabMcSettings, backups: tabBackups, config: tabConfig }[tab];
  if (!tabFn) { go(`#/server/${sid}`); return; }
  const v = (await tabFn(body, s)) || {};
  return { destroy: v.destroy, onStats: (snap) => { const n = snap.servers.find((x) => x.id === sid); if (n) { upd(n); if (v.onServer) v.onServer(n); } } };
});

async function tabOverview(body, s) {
  const card = serverCard(s);
  const info = h("div", { class: "card stack" }, h("h3", null, "Informations"),
    h("dl", { class: "kv" }, [["Adresse de connexion", connInfo(s)], ["Dossier", s.path], ["Java", `${s.java_major} (${s.java_path || "—"})`], ["Mémoire", `${fmtMb(s.ram_min_mb)} – ${fmtMb(s.ram_max_mb)}`], ["Créé le", fmtDate(s.created_at)]].map(([k, v]) => [h("dt", null, k), h("dd", { class: "mono" }, v)])));
  const extra = [];
  if (s.status === "awaiting_jar") {
    const jarName = h("input", { type: "text", value: "server.jar", maxlength: 100 }), out = h("div", { class: "stack" });
    const inspect = () => guard(async () => {
      const i = await api("GET", `/api/servers/${s.id}/import/inspect?jar=${enc(jarName.value)}`);
      clear(out).append(h("dl", { class: "kv" }, [["Fichier", `${i.jar} (${fmtBytes(i.size)})`], ["Jar valide", i.valid ? "oui" : "non"], ["Classe principale", i.main_class || "—"], ["Version Minecraft détectée", i.mc_version || "—"], ["Type probable", TYPES[i.guess] || i.guess || "—"]].map(([k, v]) => [h("dt", null, k), h("dd", { class: "mono" }, v)])),
        i.warnings.map((w) => h("div", { class: "alert warning small" }, w)),
        h("div", null, h("button", { class: "primary", disabled: !i.valid, onclick: () => guard(async () => { const r = await api("POST", `/api/servers/${s.id}/import/finalize`, { jar: jarName.value }); toast("Installation lancée.", "ok"); const j = await trackJob(r.job); toast(j.status === "done" ? "Installation terminée." : "Échec : " + j.error, j.status === "done" ? "ok" : "error"); route(); }) }, "Installer ce jar")));
    });
    const dz = dropzone("Déposez ici le jar du serveur (il sera enregistré sous son nom d'origine)", async (files) => { await guard(async () => { for (const f of files) { await upload(s.id, f.path, f.file, false); } jarName.value = files[0].file.name; toast("Jar déposé.", "ok"); }); await inspect(); }, { accept: ".jar" });
    extra.push(h("div", { class: "card stack" }, h("h3", null, `Jar ${TYPES[s.server_type] || s.server_type} à fournir`), h("p", { class: "muted small" }, "Ce type n'est pas téléchargeable automatiquement. Déposez le jar : il est examiné sans être exécuté, puis installé avec le Java adapté à Minecraft " + s.mc_version + "."),
      dz, h("div", { class: "field" }, h("label", null, "Nom du jar dans le dossier du serveur"), jarName), h("div", null, h("button", { onclick: inspect }, "Analyser le jar")), out));
  }
  if (s.status === "error" || (s.status === "installing" && s.state === "error")) {
    extra.push(h("div", { class: "alert critical stack" }, h("div", null, "L'installation de ce serveur est incomplète : " + (s.error || "erreur inconnue")),
      h("button", { class: "primary", onclick: () => guard(async () => { const r = await api("POST", `/api/servers/${s.id}/reinstall`); toast("Installation relancée.", "ok"); const j = await trackJob(r.job); toast(j.status === "done" ? "Installation terminée." : "Échec : " + j.error, j.status === "done" ? "ok" : "error"); route(); }) }, "Relancer l'installation")));
  }
  const killBtn = h("button", { class: "danger sm", onclick: async () => { if (await confirmBox("Arrêt forcé", `Tuer immédiatement le processus de « ${s.name} » ? Les dernières modifications du monde peuvent être perdues. À n'utiliser que si l'arrêt normal ne fonctionne pas.`, "Forcer l'arrêt", true)) guard(async () => { await api("POST", `/api/servers/${s.id}/kill?confirm=true`); toast("Arrêt forcé effectué.", "ok"); }); } }, "Arrêt forcé…");
  const setKill = (n) => { killBtn.disabled = !["running", "starting", "stopping"].includes(n.state); };
  setKill(s);
  if (s.status === "installed") extra.push(await storageCard(s));
  try {
    const cr = (await api("GET", "/api/crashes?server=" + s.id)).crashes;
    const KIND = { "server.crashed": "Plantage", "server.unexpected_stop": "Arrêt inattendu", "server.start_failed": "Échec de démarrage" };
    extra.push(h("div", { class: "card stack" }, h("h3", null, "Incidents récents"), cr.length ? h("div", { class: "stack" }, cr.slice(0, 8).map((c) => h("div", { class: "row between incident" },
      h("div", { class: "grow" }, h("div", null, h("span", { class: "badge error" }, KIND[c.kind] || c.kind), " ", h("span", { class: "muted small" }, fmtDate(c.ts))),
        h("div", { class: "small" }, (c.analysis.confirmed || [])[0] || ((c.analysis.possible || [])[0] ? "Cause possible : " + c.analysis.possible[0] : "Aucune erreur écrite dans les journaux."))),
      h("button", { class: "sm", onclick: () => showCrash(c.id) }, "Voir le rapport")))) : h("p", { class: "muted small" }, "Aucun plantage enregistré pour ce serveur.")));
  } catch (e) { /* non bloquant */ }
  extra.push(h("div", { class: "card stack" }, h("h3", null, "Arrêt forcé"), h("p", { class: "muted small" }, "Dernier recours si le serveur ne répond plus à « Arrêter » (qui reste la méthode normale et sûre)."), killBtn));
  body.append(h("div", { class: "grid" }, card, info), extra);
  return { onServer: (n) => { card.update(n); setKill(n); } };
}

/* ---- console ---- */
async function tabConsole(body, s) {
  const log = h("div", { class: "log", id: "log", tabindex: 0 }), filt = h("input", { type: "text", placeholder: "Filtrer (texte)…", "aria-label": "Filtrer" });
  const lvl = h("select", { "aria-label": "Niveau" }, [["", "Tous les messages"], ["warn", "Avertissements"], ["err", "Erreurs"]].map(([v, l]) => h("option", { value: v }, l)));
  const cmd = h("input", { type: "text", class: "grow mono", placeholder: "Commande Minecraft (ex. list, say Bonjour, save-all)…", autocomplete: "off", maxlength: 1000 });
  const lines = []; let follow = true, hist = [], hi = 0;
  const cls = (t) => /\b(ERROR|FATAL|Exception|SEVERE)\b/.test(t) ? "err" : /\bWARN/.test(t) ? "warn" : t.startsWith("[panneau]") ? "sys" : "";
  const visible = (l) => (!filt.value || l.t.toLowerCase().includes(filt.value.toLowerCase())) && (!lvl.value || l.c === lvl.value);
  const draw = (l) => { const d = h("div", { class: "l " + l.c }, l.t); log.append(d); };
  const redraw = () => { clear(log); lines.filter(visible).slice(-1500).forEach(draw); log.scrollTop = log.scrollHeight; };
  const add = (text) => { for (const t of text.split("\n")) { if (!t && text.endsWith("\n") && t === "") continue; const l = { t, c: cls(t) }; lines.push(l); if (visible(l)) { draw(l); } } if (lines.length > 4000) lines.splice(0, 1000); while (log.childNodes.length > 1600) log.removeChild(log.firstChild); if (follow) log.scrollTop = log.scrollHeight; };
  log.onscroll = () => { follow = log.scrollTop + log.clientHeight >= log.scrollHeight - 30; };
  filt.oninput = redraw; lvl.onchange = redraw;
  let ws, dead = false;
  const open = () => {
    ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/servers/${s.id}/console`);
    ws.onmessage = (e) => { const m = JSON.parse(e.data); if (m.type === "history") { lines.length = 0; clear(log); add(m.text); } else if (m.type === "log") add(m.text); else if (m.type === "error") toast(m.text, "error"); };
    ws.onclose = () => { if (!dead) setTimeout(open, 2000); };
  };
  open();
  const send = () => { const t = cmd.value.trim(); if (!t) return; if (!ws || ws.readyState !== 1) return toast("Console déconnectée, reconnexion…", "error"); ws.send(JSON.stringify({ type: "command", text: t })); hist.push(t); hi = hist.length; cmd.value = ""; };
  cmd.onkeydown = (e) => { if (e.key === "Enter") send(); else if (e.key === "ArrowUp" && hi > 0) { cmd.value = hist[--hi]; e.preventDefault(); } else if (e.key === "ArrowDown") { cmd.value = hist[++hi] || ""; if (hi > hist.length) hi = hist.length; } };
  const quick = ["list", "save-all", "time set day", "weather clear"].map((c) => h("button", { class: "sm", onclick: () => { cmd.value = c; send(); } }, c));
  body.append(h("div", { class: "row" }, filt, lvl, h("button", { class: "sm", onclick: () => { lines.length = 0; clear(log); } }, "Effacer l'affichage"), h("span", { class: "muted small" }, "(les journaux restent sur le disque)")), log,
    h("div", { class: "row" }, cmd, h("button", { class: "primary", onclick: send }, "Envoyer")), h("div", { class: "row" }, quick));
  return { destroy: () => { dead = true; if (ws) ws.close(); } };
}

/* ---- statistiques : performances + statistiques Minecraft (V4 §6) ---- */
async function tabStats(body, s) {
  const charts = h("div", { class: "charts" }), mc = h("div", { class: "stack" }), cur = h("div", { class: "hint" });
  let hours = 24, dead = false;
  const ranges = h("div", { class: "row" }, [[1, "1 heure"], [24, "24 heures"], [168, "7 jours"]].map(([v, l]) => h("button", { class: "sm" + (v === hours ? " primary" : ""), "data-h": v, onclick: (e) => { hours = v; ranges.querySelectorAll("button").forEach((b) => b.classList.toggle("primary", +b.dataset.h === v)); loadPerf(); } }, l)));
  const n1 = (v) => (+v).toFixed(1), loadPerf = async () => {
    try {
      const r = await api("GET", `/api/servers/${s.id}/perf?hours=${hours}`);
      cur.textContent = r.current.available ? `Mesure du TPS : ${r.current.method}, une fois par minute quand le serveur tourne (ces commandes apparaissent dans la console).` : "TPS et latence serveur non disponibles : " + r.current.method;
      clear(charts).append(
        lineChart(r.history, "tps", { title: "TPS", fmt: n1, axfmt: (v) => String(Math.round(v)), min: 0, max: 20, ref: 20, empty: r.current.available ? null : "Mesure non disponible pour ce serveur." }),
        lineChart(r.history, "mspt", { title: "Latence serveur (ms par tick)", fmt: (v) => n1(v) + " ms", axfmt: (v) => Math.round(v) + " ms", min: 0, ref: 50, empty: r.current.available ? null : "Mesure non disponible pour ce serveur." }),
        lineChart(r.history, "rss_mb", { title: "Mémoire utilisée", fmt: (v) => fmtMb(v), min: 0 }),
        lineChart(r.history, "players", { title: "Joueurs connectés", fmt: (v) => String(Math.round(v)), min: 0 }),
        lineChart(r.history, "cpu", { title: "Processeur", fmt: (v) => Math.round(v) + " %", min: 0 }));
    } catch (e) { clear(charts).append(h("div", { class: "alert critical" }, e.message)); }
  };
  const hm = (sec) => { const hh = Math.floor(sec / 3600), mm = Math.floor((sec % 3600) / 60); return hh ? `${hh} h ${String(mm).padStart(2, "0")}` : `${mm} min`; };
  const loadMc = async () => {
    try {
      const r = await api("GET", `/api/servers/${s.id}/mcstats`);
      const k = (l, v, t) => h("div", { class: "kpi", title: t || "" }, h("span", { class: "muted small" }, l), h("strong", null, v));
      const S = r.summary;
      let sortKey = "play_time_s";
      const tbody = h("tbody");
      const cols = [["name", "Joueur"], ["play_time_s", "Temps de jeu"], ["deaths", "Morts"], ["mob_kills", "Créatures tuées"], ["player_kills", "Joueurs tués"], ["distance_km", "Distance"], ["blocks_mined", "Blocs minés"], ["items_crafted", "Objets fabriqués"], ["advancements", "Progrès"], ["last_seen", "Dernière activité"]];
      const fmtc = (c, p) => c === "play_time_s" ? hm(p[c]) : c === "distance_km" ? p[c].toLocaleString("fr-FR") + " km" : c === "last_seen" ? fmtAgo(p[c]) : c === "name" ? [p.name, p.online ? h("span", { class: "badge running" }, "en ligne") : null] : (p[c] ?? 0).toLocaleString("fr-FR");
      const fill = () => { const ps = [...r.players].sort((a, b) => sortKey === "name" ? a.name.localeCompare(b.name) : (b[sortKey] || 0) - (a[sortKey] || 0)); clear(tbody).append(ps.map((p) => h("tr", null, cols.map(([c]) => h("td", { class: c === "name" ? "" : "num" }, fmtc(c, p)))))); };
      fill();
      clear(mc).append(h("div", { class: "kpis" }, k("Joueurs différents", String(S.players)), k("Temps de jeu cumulé", hm(S.play_time_s)), k("Pic de joueurs", S.peak_players ?? "—", "Depuis que le panneau mesure (une fois par minute)"),
          k("Sessions", String(S.sessions), "Connexions vues par le panneau dans la console"), k("Morts", S.deaths.toLocaleString("fr-FR")), k("Créatures tuées", S.mob_kills.toLocaleString("fr-FR")), k("Blocs minés", S.blocks_mined.toLocaleString("fr-FR")), k("Distance parcourue", S.distance_km.toLocaleString("fr-FR") + " km")),
        r.players.length ? h("div", { class: "tablewrap" }, h("table", null, h("thead", null, h("tr", null, cols.map(([c, l]) => h("th", { class: "sortable", onclick: () => { sortKey = c; fill(); } }, l)))), tbody))
          : h("p", { class: "muted" }, r.world_found ? "Aucun joueur ne s'est encore connecté à ce monde." : `Le monde « ${r.world} » n'a pas encore été créé (premier démarrage).`),
        h("p", { class: "hint" }, `Lu dans les fichiers du monde « ${r.world} » (Minecraft met à jour ces statistiques régulièrement et à la déconnexion des joueurs).`));
    } catch (e) { clear(mc).append(h("div", { class: "alert critical" }, e.message)); }
  };
  body.append(h("div", { class: "stack" }, h("div", { class: "row between" }, h("h2", null, "Performances"), ranges), cur, charts, h("h2", null, "Statistiques Minecraft"), mc));
  await Promise.all([loadPerf(), loadMc()]);
  const iv = setInterval(() => { if (!dead && !document.hidden) loadPerf(); }, 60000);
  return { destroy: () => { dead = true; clearInterval(iv); } };
}

/* ---- mods : onglet central (V4 §3) ---- */
async function tabMods(body, s) {
  const pane = h("div"), seg = h("div", { class: "seg", role: "tablist" });
  let cur = null, dead = false;
  const views = [["search", "Rechercher des mods", modSearchPanel], ["installed", "Installés et modpacks", tabModsInstalled], ["configs", "Configurations", modConfigPanel]];
  const show = async (k) => {
    if (cur && cur.destroy) cur.destroy();
    seg.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b.dataset.k === k));
    clear(pane); cur = (await views.find((v) => v[0] === k)[2](pane, s)) || {};
    try { localStorage.setItem("mcp-mods-tab", k); } catch (e) { /* stockage indisponible */ }
  };
  views.forEach(([k, l]) => seg.append(h("button", { type: "button", "data-k": k, onclick: () => show(k) }, l)));
  body.append(seg, pane);
  let start = "search"; try { start = localStorage.getItem("mcp-mods-tab") || "search"; } catch (e) { /* idem */ }
  if (s.status !== "installed") start = "installed";
  await show(views.some((v) => v[0] === start) ? start : "search");
  return { destroy: () => { dead = true; if (cur && cur.destroy) cur.destroy(); } };
}

async function modSearchPanel(body, s) {
  if (s.server_type === "vanilla") {
    body.append(h("div", { class: "card stack" }, h("p", null, "Un serveur Vanilla ne charge ni mods ni plugins."), h("p", { class: "muted small" }, "Pour en installer, créez un serveur Forge, NeoForge, Fabric ou Quilt (mods), Paper ou Purpur (plugins), ou un hybride comme Mohist (les deux).")));
    return {};
  }
  const src =h("select", null, h("option", { value: "modrinth" }, "Modrinth"), h("option", { value: "curseforge" }, "CurseForge"));
  const kindSel = h("select", null, h("option", { value: "mod" }, "Mods"), h("option", { value: "plugin" }, "Plugins"));
  const q = h("input", { type: "search", placeholder: "Nom ou mot-clé (vide : les plus populaires)", autocomplete: "off" }), cat = h("select", null, h("option", { value: "" }, "Toutes catégories"));
  const anyV = h("input", { type: "checkbox" }), results = h("div", { class: "modgrid" }), more = h("div", { class: "row" }), detail = h("div", { class: "moddetail", hidden: true }), info = h("div", { class: "hint" });
  const hybrid = ["mohist", "magma", "arclight"].includes(s.server_type);
  let offset = 0, total = 0, timer = null;
  const stack = [];
  const kind = () => (hybrid ? kindSel.value : "");
  const loadCats = async () => { try { const r = await api("GET", `/api/servers/${s.id}/modsearch/categories?source=${src.value}&kind=${kind()}`); clear(cat).append(h("option", { value: "" }, "Toutes catégories"), r.categories.map((c) => h("option", { value: c }, c))); } catch (e) { clear(cat).append(h("option", { value: "" }, "Toutes catégories")); } };
  const avatar = (t) => h("div", { class: "modav" }, (t || "?").trim().charAt(0).toUpperCase());
  const search = async (append) => {
    if (!append) { offset = 0; clear(results); }
    info.textContent = "Recherche…";
    try {
      const r = await api("GET", `/api/servers/${s.id}/modsearch?source=${src.value}&q=${enc(q.value.trim())}&category=${enc(cat.value)}&offset=${offset}&kind=${kind()}&any=${anyV.checked}`);
      total = r.total; offset += r.hits.length;
      info.textContent = `${total.toLocaleString("fr-FR")} résultat(s) compatibles avec ${TYPES[s.server_type]} ${anyV.checked ? "(toutes versions de Minecraft)" : "— Minecraft " + s.mc_version} · dossier « ${r.target.folder} ».`;
      results.append(...r.hits.map((m) => h("button", { type: "button", class: "modcard", onclick: () => { stack.length = 0; openMod(m.source, m.id); } }, avatar(m.title),
        h("div", { class: "grow" }, h("div", null, h("b", null, m.title), h("span", { class: "muted small" }, m.author ? " par " + m.author : "")), h("div", { class: "small muted clamp" }, m.description),
          h("div", { class: "row small" }, h("span", { class: "muted" }, `${m.downloads.toLocaleString("fr-FR")} téléchargements`), m.server_side === "unsupported" ? h("span", { class: "badge error" }, "client uniquement") : null, m.distribution === false ? h("span", { class: "badge starting" }, "import manuel") : null)))));
      clear(more).append(offset < total ? h("button", { onclick: () => search(true) }, "Plus de résultats") : null);
      if (!r.hits.length && !append) results.append(h("div", { class: "empty" }, "Aucun résultat : essayez un autre mot-clé ou cochez « toutes versions »."));
    } catch (e) { info.textContent = ""; clear(results).append(h("div", { class: "alert critical" }, e.message)); }
  };
  const openMod = async (source, id) => {
    detail.hidden = false; clear(detail).append(h("div", { class: "muted" }, "Chargement de la fiche…"));
    detail.scrollIntoView({ block: "nearest" });
    let p;
    try { p = await api("GET", `/api/servers/${s.id}/modsearch/project?source=${source}&id=${enc(id)}&kind=${kind()}`); } catch (e) { clear(detail).append(h("div", { class: "alert critical" }, e.message)); return; }
    stack.push([source, id]);
    const m = p.info, vSel = h("select", null, p.versions.map((v) => h("option", { value: v.id }, `${v.name}${v.type !== "release" ? " (" + v.type + ")" : ""} — ${(v.date || "").slice(0, 10)}${v.distributable ? "" : " — import manuel"}`)));
    const REL = { required: "Obligatoire", optional: "Optionnel", incompatible: "Incompatible", embedded: "Inclus" };
    clear(detail).append(
      h("div", { class: "row between" }, h("div", { class: "row" }, stack.length > 1 ? h("button", { class: "sm", onclick: () => { stack.pop(); const [a, b] = stack.pop(); openMod(a, b); } }, "← Retour") : null, avatar(m.title), h("div", null, h("h3", null, m.title), h("div", { class: "muted small" }, (m.author ? "par " + m.author + " · " : "") + `${m.downloads.toLocaleString("fr-FR")} téléchargements · ${source === "modrinth" ? "Modrinth" : "CurseForge"}`))),
        h("div", { class: "row" }, p.installed ? h("span", { class: "badge running" }, "installé") : null, h("a", { href: m.url, target: "_blank", rel: "noopener noreferrer", class: "btn sm" }, "Page officielle"), h("button", { class: "sm ghost", onclick: () => { detail.hidden = true; stack.length = 0; } }, "Fermer"))),
      h("p", null, m.description), m.categories.length ? h("div", { class: "row" }, m.categories.map((c) => h("span", { class: "chip" }, c))) : null,
      m.server_side === "unsupported" ? h("div", { class: "alert critical small" }, "Mod côté client uniquement : il ne sert à rien sur un serveur et peut l'empêcher de démarrer.") : null,
      h("h3", null, "Relations"), p.relations.length ? h("div", { class: "rels" }, p.relations.map((r) => h("button", { type: "button", class: "rel " + r.type, title: "Ouvrir la fiche", onclick: () => openMod(r.source, r.id) },
        h("span", { class: "reltype" }, REL[r.type] || r.type), h("span", { class: "relname" }, r.title || r.id), r.installed ? h("span", { class: "badge running" }, "installé") : null))) : h("p", { class: "muted small" }, "Aucune dépendance déclarée pour la version la plus récente."),
      h("h3", null, "Installer"), p.versions.length ? h("div", { class: "row" }, h("div", { class: "field grow" }, h("label", null, `Version compatible (Minecraft ${s.mc_version}, ${p.target.loaders.join("/")})`), vSel),
        h("button", { class: "primary", onclick: () => planInstall(source, vSel.value, m.id, m.title) }, "Installer…")) : h("div", { class: "alert warning small" }, `Aucune version de ce ${p.target.kind === "plugin" ? "plugin" : "mod"} pour Minecraft ${s.mc_version} et ${p.target.loaders.join("/")}.`),
      m.body ? h("details", null, h("summary", null, "Description complète"), h("pre", { class: "modbody" }, m.body)) : null);
  };
  const planInstall = async (source, version, project, title) => {
    let plan;
    try { plan = await api("GET", `/api/servers/${s.id}/modsearch/plan?source=${source}&version=${enc(version)}&project=${enc(project)}&kind=${kind()}`); } catch (e) { fail(e); return; }
    const manual = plan.items.filter((i) => !i.distributable);
    modal(`Installer ${title}`, h("div", { class: "stack" },
      h("div", { class: "tablewrap" }, h("table", null, h("thead", null, h("tr", null, ["Fichier", "Raison", "Taille", ""].map((t) => h("th", null, t)))), h("tbody", null, plan.items.map((i) => h("tr", null, h("td", { class: "mono small" }, i.filename), h("td", { class: "small" }, i.reason), h("td", { class: "small" }, fmtBytes(i.size)),
        h("td", null, i.distributable ? (i.exists ? h("span", { class: "muted small" }, "déjà présent") : "") : h("a", { href: i.page, target: "_blank", rel: "noopener noreferrer" }, "import manuel"))))))),
      plan.conflicts.map((c) => h("div", { class: "alert critical small" }, c)), plan.warnings.map((c) => h("div", { class: "alert warning small" }, c)),
      manual.length ? h("div", { class: "alert warning small" }, "Certains auteurs n'autorisent pas le téléchargement par des outils tiers : téléchargez ces fichiers depuis leur page puis déposez-les dans l'onglet « Installés ».") : null,
      plan.already_installed.length ? h("p", { class: "hint" }, `${plan.already_installed.length} dépendance(s) déjà installée(s).`) : null,
      s.state === "running" ? h("p", { class: "hint" }, "Le serveur est en marche : redémarrez-le pour charger les nouveaux fichiers.") : null),
      [{ label: "Annuler", onclick: () => true }, { label: "Installer", kind: "primary", disabled: plan.conflicts.length > 0, onclick: () => { runInstall(source, version, project, title); } }]);
  };
  const runInstall = async (source, version, project, title) => {
    let r;
    try { r = await api("POST", `/api/servers/${s.id}/modsearch/install`, { source, version, project, kind: kind() || null }); } catch (e) { fail(e); return; }
    const pb = bar(0), step = h("div", { class: "muted small" }), cancel = h("button", { class: "sm danger", onclick: () => guard(() => api("POST", `/api/jobs/${r.job}/cancel`)) }, "Annuler le téléchargement");
    const m = modal(`Installation de ${title}`, h("div", { class: "stack" }, pb, step, cancel), []);
    const j = await trackJob(r.job, (j) => { setBar(pb, j.progress); step.textContent = j.step; });
    document.querySelector(".modal-bg")?.remove();
    if (j.status === "done") { toast(`${title} : ${j.result.installed.length} fichier(s) installé(s)` + (j.result.manual.length ? `, ${j.result.manual.length} à importer à la main` : "") + (j.result.restart_needed ? ". Redémarrez le serveur pour les charger." : "."), "ok"); openMod(source, project); }
    else toast(j.error, "error");
  };
  q.oninput = () => { clearTimeout(timer); timer = setTimeout(() => search(false), 350); };
  [cat, anyV, kindSel].forEach((x) => { x.onchange = () => search(false); });
  src.onchange = async () => { await loadCats(); search(false); };
  kindSel.onchange = async () => { await loadCats(); search(false); };
  body.append(h("div", { class: "card stack" },
    h("div", { class: "searchbar" }, h("div", { class: "field grow" }, h("label", null, "Rechercher"), q), h("div", { class: "field" }, h("label", null, "Source"), src), hybrid ? h("div", { class: "field" }, h("label", null, "Contenu"), kindSel) : null, h("div", { class: "field" }, h("label", null, "Catégorie"), cat)),
    h("label", { class: "inline small" }, anyV, "Afficher aussi les mods qui n'annoncent pas Minecraft " + s.mc_version), info), detail, results, more);
  await loadCats();
  await search(false);
  return { destroy: () => clearTimeout(timer) };
}

async function modConfigPanel(body, s) {
  const list = h("div", { class: "cfglist" }), ed = h("div", { class: "cfged" }), filt = h("input", { type: "search", placeholder: "Filtrer les fichiers…" });
  let files = [];
  try { files = (await api("GET", `/api/servers/${s.id}/configs`)).files; } catch (e) { body.append(h("div", { class: "alert critical" }, e.message)); return; }
  const renderList = () => {
    const f = filt.value.trim().toLowerCase(), groups = {};
    files.filter((x) => !f || x.path.toLowerCase().includes(f)).forEach((x) => (groups[x.group] = groups[x.group] || []).push(x));
    clear(list).append(Object.keys(groups).length ? Object.keys(groups).sort().map((g) => h("div", { class: "cfggrp" }, h("div", { class: "muted small" }, g), groups[g].map((x) => h("button", { type: "button", class: "cfgfile", onclick: () => openFile(x.path) }, x.path.split("/").pop()))))
      : h("p", { class: "muted small" }, files.length ? "Aucun fichier ne correspond." : "Aucun fichier de configuration pour l'instant : les mods créent les leurs au premier démarrage du serveur."));
  };
  const openFile = async (path) => {
    list.querySelectorAll(".cfgfile").forEach((b) => b.classList.toggle("active", b.textContent === path.split("/").pop()));
    let r;
    try { r = await api("GET", `/api/servers/${s.id}/configs/file?path=${enc(path)}`); } catch (e) { clear(ed).append(h("div", { class: "alert critical" }, e.message)); return; }
    const edits = {}, ctl = {}, rowsByKey = {};
    const bar_ = h("div", { class: "savebar", hidden: true });
    const refresh = () => { const n = Object.keys(edits).length; bar_.hidden = !n; clear(bar_).append(h("span", null, `${n} modification(s)`), h("div", { class: "row" }, h("button", { class: "ghost", onclick: () => openFile(path) }, "Annuler"), h("button", { class: "primary", onclick: save }, "Enregistrer"))); applyConds(); };
    const save = () => guard(async () => { const x = await api("PUT", `/api/servers/${s.id}/configs/file?path=${enc(path)}`, { changes: edits, mtime: r.mtime }); toast("Configuration enregistrée (copie de l'ancienne version conservée)." + (x.restart_required ? " Redémarrez le serveur pour l'appliquer." : ""), "ok"); openFile(path); });
    const byKey = Object.fromEntries(r.entries.map((e) => [e.key, e]));
    const control = (e) => {
      let el;
      const set = (v) => { if (String(v) === String(e.value)) delete edits[e.key]; else edits[e.key] = v; rowsByKey[e.key]?.classList.toggle("changed", e.key in edits); refresh(); };
      if (e.type === "bool") { el = h("input", { type: "checkbox", checked: String(e.value).toLowerCase() === "true" }); el.onchange = () => set(String(el.checked)); }
      else if (e.type === "enum") { el = h("select", null, e.options.map((o) => h("option", { value: o, selected: o === e.value }, o))); el.onchange = () => set(el.value); }
      else { el = h("input", { type: e.type === "int" || e.type === "float" ? "number" : "text", value: e.value, step: e.type === "float" ? "any" : null, min: e.min ?? null, max: e.max ?? null, class: e.type === "list" || e.type === "raw" ? "mono" : null }); el.oninput = () => set(el.value); }
      if (e.readonly) el.disabled = true;
      ctl[e.key] = { el, set };
      return el;
    };
    const resetTo = (e, v) => { const c = ctl[e.key]; if (!c) return; if (e.type === "bool") c.el.checked = String(v).toLowerCase() === "true"; else c.el.value = v; c.set(v); };
    const row = (e, label, desc) => {
      const el = control(e);
      const def = e.default !== undefined ? e.default : null;
      const r_ = h("div", { class: "mcrow" }, h("div", { class: "mclabel" }, h("label", null, label || e.name), h("code", { class: "muted small" }, e.key)),
        h("div", { class: "mcctl" }, e.type === "bool" ? h("label", { class: "inline" }, el, "Activé") : el,
          def !== null ? h("button", { class: "sm ghost", disabled: !!e.readonly, title: "Valeur par défaut déclarée par le mod : " + def, onclick: () => resetTo(e, def) }, "Défaut") : null,
          e.original !== null && e.original !== undefined && String(e.original) !== String(e.value) ? h("button", { class: "sm ghost", title: "Valeur d'origine : " + e.original, onclick: () => resetTo(e, e.original) }, "Origine") : null),
        h("div", { class: "hint mcdesc" }, [desc || e.description || "", e.min !== undefined && e.min !== null || e.max !== undefined && e.max !== null ? `Plage : ${e.min ?? "−∞"} à ${e.max ?? "+∞"}.` : "", e.readonly ? "Structure complexe : modifiable dans l'onglet Fichiers." : ""].filter(Boolean).map((x) => (/[.!?]$/.test(x) ? x : x + ".")).join(" ")));
      rowsByKey[e.key] = r_;
      return r_;
    };
    const conds = [];
    const applyConds = () => conds.forEach(([elr, c]) => { const cur = c.key in edits ? edits[c.key] : (byKey[c.key] || {}).value; elr.hidden = String(cur) !== String(c.equals); });
    const parts = [];
    if (r.ui && r.ui.length) {
      const u = r.ui[0];
      parts.push(h("div", { class: "alert info small" }, `Interface personnalisée : ${u.title}` + (u.description ? " — " + u.description : "")));
      if (u.presets && u.presets.length) parts.push(h("div", { class: "row" }, h("span", { class: "muted small" }, "Préréglages :"), u.presets.map((p) => h("button", { class: "sm", title: p.description || "", onclick: () => Object.entries(p.values || {}).forEach(([k, v]) => byKey[k] && resetTo(byKey[k], v)) }, p.name))));
      u.categories.forEach((c) => parts.push(h("details", { class: "card mcsec", open: true }, h("summary", null, h("strong", null, c.title)), c.description ? h("p", { class: "hint" }, c.description) : null,
        (c.fields || []).filter((fd) => byKey[fd.key]).map((fd) => { const elr = row(byKey[fd.key], fd.label, fd.description); if (fd.show_if) conds.push([elr, fd.show_if]); return elr; }))));
      parts.push(h("details", { class: "card mcsec" }, h("summary", null, "Tous les paramètres du fichier"), r.entries.map((e) => row(e))));
    } else {
      const secs = {};
      r.entries.forEach((e) => (secs[e.section || "Général"] = secs[e.section || "Général"] || []).push(e));
      Object.entries(secs).forEach(([k, es], i) => parts.push(h("details", { class: "card mcsec", open: i < 3 }, h("summary", null, h("strong", null, k), h("span", { class: "muted small" }, ` ${es.length} paramètre(s)`)), es.map((e) => row(e)))));
    }
    const hist = () => modal("Copies de " + path.split("/").pop(), h("div", { class: "stack" }, r.backups.length ? r.backups.map((b) => h("div", { class: "row between" }, h("span", null, b.original ? "Version d'origine (première vue par le panneau)" : fmtDate(b.ts)),
      h("button", { class: "sm", onclick: () => guard(async () => { if (await confirmBox("Restaurer cette copie ?", "Le fichier actuel est d'abord copié : l'opération reste annulable.", "Restaurer")) { await api("POST", `/api/servers/${s.id}/configs/restore?path=${enc(path)}`, { id: b.id }); document.querySelector(".modal-bg")?.remove(); toast("Copie restaurée.", "ok"); openFile(path); } }) }, "Restaurer"))) : h("p", null, "Aucune copie pour l'instant.")), [{ label: "Fermer", onclick: () => true }]);
    const regen = () => guard(async () => { if (await confirmBox("Valeurs par défaut du mod", "Le fichier est copié puis retiré : le mod le recréera avec ses valeurs par défaut au prochain démarrage. Le serveur doit être arrêté.", "Régénérer", true)) { await api("POST", `/api/servers/${s.id}/configs/regenerate?path=${enc(path)}`); toast("Fichier retiré : il sera recréé au prochain démarrage.", "ok"); files = files.filter((x) => x.path !== path); renderList(); clear(ed); } });
    clear(ed).append(h("div", { class: "row between" }, h("div", null, h("h3", null, path.split("/").pop()), h("div", { class: "muted small mono" }, path + " · " + r.format.toUpperCase())),
      h("div", { class: "row" }, h("button", { class: "sm", onclick: hist }, `Copies (${r.backups.length})`), h("button", { class: "sm", onclick: regen }, "Valeurs par défaut du mod…"))),
      r.running ? h("div", { class: "alert warning small" }, "Serveur en marche : les modifications s'appliqueront au prochain redémarrage (sauf si le mod recharge sa configuration à chaud).") : null,
      r.error ? h("div", { class: "alert critical" }, r.error) : null, parts, bar_);
    applyConds();
  };
  filt.oninput = renderList;
  body.append(h("div", { class: "cfgwrap" }, h("div", { class: "card stack" }, filt, list), h("div", { class: "card stack" }, ed)));
  renderList();
  ed.append(h("p", { class: "muted" }, "Choisissez un fichier : ses paramètres s'affichent avec des contrôles adaptés (interrupteurs, listes, nombres…)."));
}

/* ---- whitelist ---- */
const notInstalled = (body, s, what) => { if (s.status === "installed") return false; body.append(h("div", { class: "card empty" }, h("h3", null, what + " : disponible après l'installation"), h("p", null, s.status === "awaiting_jar" ? "Déposez d'abord le jar du serveur (onglet Vue d'ensemble)." : "Le serveur doit d'abord être installé correctement (voir Vue d'ensemble)."))); return true; };
async function tabWhitelist(body, s) {
  if (notInstalled(body, s, "La whitelist")) return;
  const base = `/api/servers/${s.id}/whitelist`;
  const nameIn = h("input", { type: "text", maxlength: 17, placeholder: "Pseudo Minecraft", autocomplete: "off", spellcheck: false });
  const search = h("input", { type: "search", placeholder: "Rechercher un joueur…", autocomplete: "off" });
  const toggle = h("input", { type: "checkbox" }), info = h("div", { class: "hint" }), list = h("div", { class: "tablewrap" }), count = h("span", { class: "muted small" });
  let busy = false, dead = false, timer = null;
  const load = async () => {
    if (busy) return;
    try {
      const r = await api("GET", base + "?q=" + enc(search.value.trim()));
      toggle.checked = r.enabled;
      info.textContent = r.running ? "Le serveur est en marche : les changements passent par ses commandes." : "Le serveur est arrêté : les changements sont écrits directement et pris en compte au prochain démarrage." + (r.online_mode ? " Les pseudos sont vérifiés auprès de Mojang." : " Mode hors ligne : l'identifiant est calculé localement.");
      count.textContent = `${r.total} joueur${r.total > 1 ? "s" : ""} autorisé${r.total > 1 ? "s" : ""}`;
      clear(list).append(r.entries.length ? h("table", null, h("thead", null, h("tr", null, ["Joueur", "Identifiant (UUID)", "Statut", ""].map((t) => h("th", null, t)))),
        h("tbody", null, r.entries.map((e) => h("tr", null, h("td", null, e.name), h("td", { class: "mono small" }, e.uuid || "—"),
          h("td", null, [e.online ? h("span", { class: "badge running" }, "En ligne") : null, e.op ? h("span", { class: "badge admin" }, "Opérateur") : null, e.banned ? h("span", { class: "badge error" }, "Banni") : null]),
          h("td", null, h("button", { class: "sm danger", onclick: () => guard(async () => { if (await confirmBox("Retirer de la whitelist", `Retirer « ${e.name} » de la liste blanche ?`, "Retirer", true)) { await api("DELETE", `${base}/${enc(e.name)}`); toast(`${e.name} retiré.`, "ok"); load(); } }) }, "Retirer")))))) : h("div", { class: "empty" }, search.value ? "Aucun joueur ne correspond." : "La liste est vide. Ajoutez un pseudo ci-dessus."));
    } catch (e) { clear(list).append(h("div", { class: "alert critical" }, e.message)); }
  };
  const add = () => guard(async () => { const n = nameIn.value.trim(); if (!n) return; busy = true; try { const r = await api("POST", base, { name: n }); toast(`${r.added} ajouté.`, "ok"); nameIn.value = ""; } finally { busy = false; } await load(); });
  nameIn.onkeydown = (e) => { if (e.key === "Enter") add(); };
  search.oninput = () => { clearTimeout(timer); timer = setTimeout(load, 250); };
  toggle.onchange = () => guard(async () => { busy = true; try { await api("PUT", base + "/enabled", { enabled: toggle.checked }); toast(toggle.checked ? "Whitelist activée." : "Whitelist désactivée.", "ok"); } catch (e) { toggle.checked = !toggle.checked; throw e; } finally { busy = false; } await load(); });
  body.append(h("div", { class: "card stack" },
    h("label", { class: "inline" }, toggle, "Whitelist activée sur ce serveur (seuls les joueurs listés peuvent se connecter)"), info,
    h("div", { class: "row" }, h("div", { class: "field grow" }, h("label", null, "Ajouter un joueur"), nameIn), h("button", { class: "primary", onclick: add }, "Ajouter")),
    h("div", { class: "row" }, h("div", { class: "field grow" }, h("label", null, "Rechercher"), search), count), list));
  await load();
  const iv = setInterval(() => { if (!dead && !document.hidden) load(); }, 5000);
  return { destroy: () => { dead = true; clearInterval(iv); clearTimeout(timer); } };
}

/* ---- paramètres Minecraft (server.properties) ---- */
async function tabMcSettings(body, s) {
  if (notInstalled(body, s, "Les paramètres Minecraft")) return;
  const base = `/api/servers/${s.id}/mcsettings`;
  const [schema, cur] = await Promise.all([api("GET", "/api/meta/mcsettings-schema"), api("GET", base)]);
  const edits = {}, controls = {}, removals = new Set();
  const search = h("input", { type: "search", placeholder: "Rechercher un réglage…", autocomplete: "off" });
  const saveBar = h("div", { class: "savebar", hidden: true }), sections = h("div", { class: "stack" });
  const curVal = (f) => (cur.values[f.key].set ? cur.values[f.key].value : f.default);
  const refreshBar = () => {
    const n = Object.keys(edits).length + removals.size;
    saveBar.hidden = n === 0;
    clear(saveBar).append(h("span", null, `${n} modification${n > 1 ? "s" : ""} non enregistrée${n > 1 ? "s" : ""}`),
      h("div", { class: "row" }, h("button", { class: "ghost", onclick: () => route() }, "Annuler"), h("button", { class: "primary", onclick: () => save(false) }, "Enregistrer")));
  };
  const save = (confirmOffline) => guard(async () => {
    try {
      const r = await api("PUT", base, { changes: edits, hash: cur.hash, confirm_offline: confirmOffline, delete: [...removals] });
      r.warnings.forEach((w) => toast(w, "warn"));
      toast(r.changed.length || r.removed.length ? (r.restart_required.length ? "Enregistré. Redémarrage nécessaire pour : " + r.restart_required.join(", ") + "." : "Réglages enregistrés.") : "Rien à modifier.", "ok");
      route();
    } catch (e) {
      if (/online-mode/.test(e.message) && /Confirmez/.test(e.message)) {
        if (await confirmBox("Désactiver l'authentification Mojang ?", "Sans online-mode, n'importe qui peut se connecter sous le pseudo d'un autre joueur, y compris celui d'un opérateur. À ne faire que derrière un proxy d'authentification ou sur un réseau de confiance.", "Je comprends, désactiver", true)) return save(true);
        return;
      }
      throw e;
    }
  });
  const control = (f) => {
    const v = curVal(f);
    let el;
    if (f.type === "bool") { el = h("input", { type: "checkbox", checked: String(v).toLowerCase() === "true" }); el.onchange = () => mark(f, String(el.checked)); }
    else if (f.type === "enum") { el = h("select", null, f.options.map((o) => h("option", { value: o, selected: o === v }, o))); el.onchange = () => mark(f, el.value); }
    else { el = h("input", { type: f.secret ? "password" : f.type === "int" ? "number" : "text", value: f.secret ? "" : v, placeholder: f.secret && cur.values[f.key].secret_set ? "(enregistré — laisser vide pour conserver)" : "", min: f.min ?? null, max: f.max ?? null, maxlength: f.type === "int" ? null : f.maxlen, autocomplete: "off" }); el.oninput = () => mark(f, el.value); }
    if (f.managed) el.disabled = true;
    controls[f.key] = el;
    return el;
  };
  const mark = (f, val) => {
    const orig = cur.values[f.key].set ? cur.values[f.key].value : f.default;
    if (f.secret ? val === "" : String(val) === String(orig)) delete edits[f.key]; else edits[f.key] = val;
    const row = document.getElementById("mcs-" + f.key.replace(/\./g, "_")); if (row) row.classList.toggle("changed", f.key in edits);
    refreshBar();
  };
  const row = (f) => {
    const el = control(f);
    const chips = [f.restart && cur.running ? h("span", { class: "chip" }, "redémarrage requis") : null, f.new_world ? h("span", { class: "chip" }, "nouveau monde") : null, f.managed ? h("span", { class: "chip" }, f.managed === "whitelist" ? "onglet Whitelist" : "onglet Configuration") : null, f.min_mc ? h("span", { class: "chip" }, "depuis " + f.min_mc) : null];
    const reset = h("button", { type: "button", class: "sm ghost", disabled: !!f.managed, title: "Remettre la valeur par défaut de Minecraft", onclick: () => { if (f.type === "bool") el.checked = f.default === "true"; else el.value = f.default; mark(f, f.default); } }, "Défaut");
    return h("div", { class: "mcrow", id: "mcs-" + f.key.replace(/\./g, "_"), "data-s": (f.label + " " + f.key + " " + f.description).toLowerCase() },
      h("div", { class: "mclabel" }, h("label", null, f.label), h("code", { class: "muted small" }, f.key), h("div", { class: "row" }, chips)),
      h("div", { class: "mcctl" }, f.type === "bool" ? h("label", { class: "inline" }, el, "Activé") : el, reset), h("div", { class: "hint mcdesc" }, f.description + (f.type !== "bool" && f.type !== "enum" ? ` Défaut : ${f.default === "" ? "vide" : f.default}.` : "")));
  };
  schema.categories.forEach((c) => {
    const fs = schema.fields.filter((f) => f.category === c.key && cur.values[f.key]);
    if (fs.length) sections.append(h("details", { class: "card mcsec", open: c.key === "general" }, h("summary", null, h("strong", null, c.label), h("span", { class: "muted small" }, ` ${fs.length} réglages`)), fs.map(row)));
  });
  const unk = Object.entries(cur.unknown);
  const nk = h("input", { type: "text", placeholder: "nom.de.la.propriété", maxlength: 64 }), nv = h("input", { type: "text", placeholder: "valeur", maxlength: 200 });
  sections.append(h("details", { class: "card mcsec" }, h("summary", null, h("strong", null, "Autres propriétés"), h("span", { class: "muted small" }, ` ${unk.length} non reconnue${unk.length > 1 ? "s" : ""} — conservées telles quelles`)),
    unk.length ? unk.map(([k, v]) => { const i = h("input", { type: "text", value: v, maxlength: 200 }); i.oninput = () => { if (i.value === v) delete edits[k]; else edits[k] = i.value; refreshBar(); };
      return h("div", { class: "mcrow" }, h("div", { class: "mclabel" }, h("code", null, k)), h("div", { class: "mcctl" }, i, h("button", { class: "sm danger", onclick: () => { removals.add(k); i.disabled = true; refreshBar(); } }, "Supprimer")), h("div", { class: "hint mcdesc" }, "Propriété de mod, de plugin ou d'une autre version : le panneau n'en connaît pas le rôle.")); }) : h("p", { class: "muted small" }, "Aucune."),
    h("div", { class: "row" }, nk, nv, h("button", { class: "sm", onclick: () => { if (!nk.value.trim()) return; edits[nk.value.trim()] = nv.value; refreshBar(); toast("Propriété prête à être enregistrée.", "ok"); } }, "Ajouter"))));
  search.oninput = () => { const q = search.value.trim().toLowerCase(); sections.querySelectorAll(".mcrow").forEach((r) => { r.hidden = !!q && !(r.dataset.s || "").includes(q); }); if (q) sections.querySelectorAll("details").forEach((d) => { d.open = [...d.querySelectorAll(".mcrow")].some((r) => !r.hidden); }); };
  const history = () => guard(async () => {
    const r = await api("GET", base + "/history");
    modal("Historique de server.properties", r.history.length ? h("div", { class: "stack" }, r.history.map((x) => h("div", { class: "row between" }, h("div", null, h("div", null, fmtDate(x.ts) + " — " + (x.user || "?")), h("div", { class: "muted small" }, (x.changed.concat(x.removed.map((k) => "− " + k)).join(", ") || x.note || "—"))),
      h("button", { class: "sm", onclick: () => guard(async () => { if (await confirmBox("Revenir à cette version ?", "Le fichier actuel est d'abord sauvegardé dans l'historique : le retour reste annulable." + (cur.running ? " Le serveur est en marche : redémarrez-le pour appliquer." : ""), "Revenir")) { await api("POST", base + "/revert", { id: x.id }); toast("Version restaurée.", "ok"); document.querySelector(".modal-bg")?.remove(); route(); } }) }, "Revenir ici")))) : h("p", null, "Aucune modification enregistrée pour l'instant."), [{ label: "Fermer", onclick: () => true }]);
  });
  body.append(h("div", { class: "card stack" }, cur.running ? h("div", { class: "alert warning small" }, "Le serveur est en marche : les réglages sont écrits tout de suite mais la plupart ne s'appliquent qu'au prochain démarrage.") : null,
    h("div", { class: "row between" }, h("div", { class: "field grow" }, search), h("button", { class: "sm", onclick: history }, `Historique (${cur.history_count})`)),
    h("div", { class: "hint" }, `Fichier server.properties de Minecraft ${cur.mc_version}. Les réglages inconnus, l'ordre et les commentaires du fichier sont conservés ; chaque enregistrement est historisé.`)), sections, saveBar);
}

/* ---- fichiers ---- */
async function upload(sid, rel, file, overwrite) {
  const r = await fetch(`${API}/api/servers/${sid}/files/upload?path=${enc(rel)}&overwrite=${overwrite ? "true" : "false"}`, { method: "PUT", headers: { "X-CSRF-Token": state.csrf }, credentials: "same-origin", body: file });
  if (!r.ok) { let d = null; try { d = await r.json(); } catch (e) { /* vide */ } throw new Error(`${file.name} : ${errText(d, r.status)}`); }
}
function dropzone(label, onFiles, opts = {}) {
  const input = h("input", { type: "file", multiple: true, hidden: true, accept: opts.accept || null });
  const dirInput = h("input", { type: "file", multiple: true, hidden: true, webkitdirectory: true });
  const z = h("div", { class: "dropzone" }, h("div", null, label), h("div", { class: "row", style: null }, h("button", { type: "button", class: "sm", onclick: () => input.click() }, "Choisir des fichiers"), opts.dir ? h("button", { type: "button", class: "sm", onclick: () => dirInput.click() }, "Choisir un dossier") : null), input, dirInput);
  const handle = (fl) => { if (fl.length) onFiles(fl); };
  input.onchange = () => { handle([...input.files].map((f) => ({ file: f, path: f.name }))); input.value = ""; };
  dirInput.onchange = () => { handle([...dirInput.files].map((f) => ({ file: f, path: f.webkitRelativePath || f.name }))); dirInput.value = ""; };
  z.ondragover = (e) => { e.preventDefault(); z.classList.add("over"); };
  z.ondragleave = () => z.classList.remove("over");
  z.ondrop = async (e) => {
    e.preventDefault(); z.classList.remove("over");
    const out = [];
    const walk = async (entry, pre) => {
      if (entry.isFile) await new Promise((res) => entry.file((f) => { out.push({ file: f, path: pre + f.name }); res(); }, res));
      else if (entry.isDirectory && opts.dir) { const rd = entry.createReader(); let batch; do { batch = await new Promise((res) => rd.readEntries(res, () => res([]))); for (const c of batch) await walk(c, pre + entry.name + "/"); } while (batch.length); }
    };
    const items = [...(e.dataTransfer.items || [])].map((i) => i.webkitGetAsEntry && i.webkitGetAsEntry()).filter(Boolean);
    if (items.length) for (const it of items) await walk(it, ""); else [...e.dataTransfer.files].forEach((f) => out.push({ file: f, path: f.name }));
    handle(out);
  };
  return z;
}

async function tabFiles(body, s) {
  let cur = "";
  const crumbs = h("div", { class: "crumbs row" }), table = h("div", { class: "tablewrap" }), editor = h("div");
  const join = (a, b) => (a ? a + "/" + b : b);
  const load = async (p) => {
    const r = await api("GET", `/api/servers/${s.id}/files?path=${enc(p)}`);
    cur = r.path;
    const parts = cur ? cur.split("/") : [];
    clear(crumbs).append(h("a", { onclick: () => load("") }, s.name), parts.map((x, i) => [" / ", h("a", { onclick: () => load(parts.slice(0, i + 1).join("/")) }, x)]));
    clear(table).append(h("table", null, h("thead", null, h("tr", null, ["Nom", "Taille", "Modifié", "Actions"].map((t) => h("th", null, t)))),
      h("tbody", null, cur ? h("tr", null, h("td", { colspan: 4 }, h("a", { onclick: () => load(parts.slice(0, -1).join("/")) }, icon("folder"), " Dossier parent"))) : null,
        r.entries.map((e) => { const rel = join(cur, e.name);
          return h("tr", null, h("td", null, e.type === "dir" ? h("a", { onclick: () => load(rel) }, icon("folder"), " " + e.name) : e.type === "link" ? [icon("link"), " " + e.name] : e.editable ? h("a", { onclick: () => edit(rel) }, icon("file"), " " + e.name) : [icon("file"), " " + e.name]),
            h("td", null, e.type === "dir" ? "—" : fmtBytes(e.size)), h("td", { class: "small" }, fmtDate(e.mtime)),
            h("td", { class: "actions" }, h("div", { class: "row" },
              e.type === "file" ? h("a", { class: "btn sm", href: `${API}/api/servers/${s.id}/files/download?path=${enc(rel)}` }, icon("download"), "Télécharger") : null,
              h("button", { class: "sm", onclick: () => rename(rel, e) }, "Renommer / déplacer"),
              e.type !== "link" ? h("button", { class: "sm", onclick: () => copy(rel) }, "Copier") : null,
              h("button", { class: "sm danger", onclick: () => del(rel, e) }, "Supprimer")))); }))));
  };
  const rename = (rel) => { const i = h("input", { type: "text", value: rel, style: null, class: "mono", "aria-label": "Nouveau chemin" }); modal("Renommer ou déplacer", h("div", { class: "field" }, h("label", null, "Nouveau chemin (relatif au serveur)"), i), [{ label: "Annuler", onclick: () => {} }, { label: "Valider", kind: "primary", onclick: async () => { try { await api("POST", `/api/servers/${s.id}/files/rename`, { src: rel, dst: i.value.trim() }); load(cur); } catch (e) { fail(e); return false; } } }]); };
  const copy = (rel) => { const i = h("input", { type: "text", value: rel + ".copie", class: "mono" }); modal("Copier", h("div", { class: "field" }, h("label", null, "Chemin de la copie"), i), [{ label: "Annuler", onclick: () => {} }, { label: "Copier", kind: "primary", onclick: async () => { try { await api("POST", `/api/servers/${s.id}/files/copy`, { src: rel, dst: i.value.trim() }); load(cur); } catch (e) { fail(e); return false; } } }]); };
  const del = async (rel, e) => { if (await confirmBox("Supprimer", `Supprimer définitivement « ${rel} »${e.type === "dir" ? " et tout son contenu" : ""} ?`, "Supprimer", true)) await guard(async () => { await api("DELETE", `/api/servers/${s.id}/files?path=${enc(rel)}`); load(cur); }); };
  const edit = async (rel) => {
    const r = await api("GET", `/api/servers/${s.id}/files/read?path=${enc(rel)}`);
    const ta = h("textarea", { class: "editor", spellcheck: "false", "aria-label": rel }); ta.value = r.content;
    let mtime = r.mtime;
    const warn = s.state === "running" ? h("div", { class: "alert warning small" }, "Le serveur est en marche : certaines modifications ne seront prises en compte qu'après redémarrage.") : null;
    clear(editor).append(h("div", { class: "card stack" }, h("div", { class: "row between" }, h("h3", { class: "mono" }, rel), h("div", { class: "row" },
      h("button", { class: "primary", onclick: () => guard(async () => { const w = await api("PUT", `/api/servers/${s.id}/files/write`, { path: rel, content: ta.value, mtime }); mtime = w.mtime; toast("Fichier enregistré.", "ok"); }) }, "Enregistrer"),
      h("button", { onclick: () => clear(editor) }, "Fermer"))), warn, ta));
    editor.scrollIntoView();
  };
  const dz = dropzone("Glissez ici des fichiers ou un dossier à envoyer dans le dossier courant", async (files) => {
    let ok = 0;
    for (const { file, path } of files) { try { await upload(s.id, join(cur, path), file, false); ok++; } catch (e) { if (/existe déjà/.test(e.message) && await confirmBox("Fichier existant", `« ${path} » existe déjà. Le remplacer ?`, "Remplacer", true)) { try { await upload(s.id, join(cur, path), file, true); ok++; } catch (e2) { fail(e2); } } else if (!/existe déjà/.test(e.message)) fail(e); } }
    toast(`${ok} fichier(s) envoyé(s).`, "ok"); load(cur);
  }, { dir: true });
  const quick = ["server.properties", "eula.txt", "ops.json", "whitelist.json", "banned-players.json", "banned-ips.json"].map((n) => h("button", { class: "sm", onclick: () => guard(() => edit(n)) }, n));
  body.append(h("div", { class: "row" }, h("span", { class: "muted small" }, "Accès rapide :"), quick), crumbs,
    h("div", { class: "row" }, h("button", { class: "sm", onclick: () => { const i = h("input", { type: "text", placeholder: "nom-du-dossier" }); modal("Nouveau dossier", h("div", { class: "field" }, i), [{ label: "Annuler", onclick: () => {} }, { label: "Créer", kind: "primary", onclick: async () => { try { await api("POST", `/api/servers/${s.id}/files/mkdir`, { path: join(cur, i.value.trim()) }); load(cur); } catch (e) { fail(e); return false; } } }]); } }, "+ Nouveau dossier"),
      h("button", { class: "sm", onclick: () => load(cur) }, "Actualiser")), table, dz, editor);
  await load("");
}

/* ---- mods & modpack ---- */
async function tabModsInstalled(body, s) {
  const list = h("div", { class: "tablewrap" }), summary = h("div", { class: "muted small" });
  const load = async () => {
    const r = await api("GET", `/api/servers/${s.id}/mods`);
    summary.textContent = `${r.count} mod(s) · ${Object.keys(r.duplicates).length} doublon(s)`;
    clear(list).append(r.mods.length ? h("table", null, h("thead", null, h("tr", null, ["Mod", "Version", "Chargeur", "Taille", "Remarques", "Actions"].map((t) => h("th", null, t)))), h("tbody", null, r.mods.map((m) =>
      h("tr", null, h("td", null, h("div", null, m.name || m.file, m.disabled ? h("span", { class: "badge stopped" }, " désactivé") : null), h("div", { class: "muted small mono" }, m.file)), h("td", null, m.version || "—"), h("td", null, m.loader || "?"), h("td", null, fmtBytes(m.size)),
        h("td", null, m.issues.map((i) => h("div", { class: "issue " + i.level }, (i.level === "error" ? "⛔ " : "⚠ ") + i.text))),
        h("td", { class: "row" }, h("button", { class: "sm", onclick: () => guard(async () => { await api("POST", `/api/servers/${s.id}/mods/${enc(m.file)}/toggle`); load(); }) }, m.disabled ? "Activer" : "Désactiver"),
          h("button", { class: "sm danger", onclick: async () => { if (await confirmBox("Supprimer le mod", `Supprimer « ${m.file} » ?`, "Supprimer", true)) guard(async () => { await api("DELETE", `/api/servers/${s.id}/mods/${enc(m.file)}?confirm=true`); load(); }); } }, "Supprimer")))))) : h("p", { class: "muted" }, "Aucun mod dans le dossier « mods »."));
  };
  const modDz = dropzone("Glissez ici un ou plusieurs mods (.jar)", async (files) => {
    let ok = 0;
    for (const { file, path } of files) { if (!path.toLowerCase().endsWith(".jar")) { toast(`${path} : seuls les fichiers .jar sont acceptés ici.`, "error"); continue; }
      try { await upload(s.id, "mods/" + file.name, file, false); ok++; } catch (e) { if (/existe déjà/.test(e.message)) { if (await confirmBox("Remplacer le mod", `« ${file.name} » existe déjà. Le remplacer ?`, "Remplacer", true)) { try { await upload(s.id, "mods/" + file.name, file, true); ok++; } catch (e2) { fail(e2); } } } else fail(e); } }
    if (ok) toast(`${ok} mod(s) envoyé(s).`, "ok"); load();
  }, { accept: ".jar" });
  const packDz = dropzone("IMPORTER UN MODPACK — glissez un fichier ZIP / .mrpack (pack serveur, CurseForge ou Modrinth)", async (files) => {
    const f = files[0].file;
    if (s.state !== "stopped") return toast("Arrêtez le serveur avant d'importer un modpack.", "error");
    const t = toast("Envoi et analyse de l'archive…");
    try {
      const r = await fetch(`${API}/api/servers/${s.id}/modpack/upload?filename=${enc(f.name)}`, { method: "PUT", headers: { "X-CSRF-Token": state.csrf }, credentials: "same-origin", body: f });
      const d = await r.json().catch(() => null); if (!r.ok) throw new Error(errText(d, r.status));
      analysisModal(s, d.upload_id, d.analysis, load);
    } catch (e) { fail(e); }
  }, { accept: ".zip,.mrpack" });
  body.append(h("div", { class: "card stack" }, h("h3", null, "Importer un modpack"), h("p", { class: "muted small" }, "L'archive est analysée sans rien extraire : vous voyez ce qui sera installé, ignoré ou remplacé avant de valider. Les packs CurseForge/Modrinth sont installés depuis leur manifeste lorsque c'est autorisé."), packDz),
    h("h2", null, "Mods installés"), summary, modDz, list);
  await load();
}
function analysisModal(s, uid, a, reload) {
  const kind = { modrinth: "Modrinth (.mrpack)", curseforge: "CurseForge", serverpack: "Pack serveur (fichiers inclus)", unknown: "Inconnu" }[a.kind] || a.kind;
  const policy = h("select", null, h("option", { value: "skip" }, "Conserver les fichiers existants"), h("option", { value: "replace" }, "Remplacer les fichiers existants (avec sauvegarde)"));
  const ignore = h("input", { type: "checkbox", id: "ign" });
  const body = h("div", { class: "stack" },
    a.errors.length ? h("div", { class: "alert critical" }, a.errors.map((e) => h("div", null, e))) : null,
    h("div", null, h("b", null, "Type : "), kind, a.name ? " — " + a.name : "", a.root_prefix ? h("div", { class: "muted small" }, "Dossier racine ignoré : " + a.root_prefix) : null),
    h("div", { class: "muted small" }, `Minecraft : ${a.mc_version || "non précisé"} · Chargeur : ${a.loader ? a.loader + " " + (a.loader_version || "") : "non précisé"} · Serveur : ${s.mc_version} / ${s.server_type}`),
    a.mismatch.length ? h("div", { class: "alert warning stack" }, a.mismatch.map((m) => h("div", null, "⚠ " + m)), h("label", { class: "inline", for: "ign" }, ignore, "Je veux quand même importer ce pack malgré l'incompatibilité.")) : h("div", { class: "alert info" }, "Compatible avec ce serveur."),
    a.warnings.map((w) => h("div", { class: "alert warning small" }, w)),
    h("div", null, `${a.entries.length} fichier(s) à extraire · ${a.downloads.length} à télécharger (Modrinth) · ${a.cf_files.length} mod(s) CurseForge · ${a.totals.conflicts} conflit(s) · ${fmtBytes(a.totals.bytes)}`),
    a.cf_files.length ? h("div", { class: "alert info small" }, "Les mods CurseForge ne sont téléchargés qu'avec une clé API (Paramètres), et jamais si leur auteur interdit le téléchargement par des outils tiers : ceux-là sont listés pour un ajout manuel.") : null,
    a.totals.conflicts ? h("div", null, h("label", null, "En cas de conflit"), policy) : null,
    a.entries.length ? h("details", null, h("summary", null, "Détail des fichiers"), h("pre", { class: "joblog" }, a.entries.map((e) => (e.conflict ? "⚠ existe déjà  " : "+ ") + e.dest).join("\n"))) : null,
    a.skipped.length ? h("details", null, h("summary", null, `${a.skipped.length} élément(s) ignoré(s)`), h("pre", { class: "joblog" }, a.skipped.map((x) => `${x.name} — ${x.reason}`).join("\n"))) : null);
  modal("Analyse du modpack", body, [
    { label: "Annuler", onclick: () => guard(() => api("DELETE", `/api/servers/${s.id}/modpack/${uid}`)) },
    { label: "Importer", kind: "primary", disabled: a.errors.length > 0, onclick: async () => {
      if (a.mismatch.length && !ignore.checked) { toast("Cochez la case pour confirmer l'import malgré l'incompatibilité.", "error"); return false; }
      try { const r = await api("POST", `/api/servers/${s.id}/modpack/${uid}/apply`, { policy: policy.value, ignore_mismatch: ignore.checked, backup_first: true });
        const box = h("pre", { class: "joblog" }, "Import en cours…"); const pb = bar(0); const m2 = modal("Import en cours", h("div", null, pb, box), []);
        const j = await trackJob(r.job, (j) => { setBar(pb, j.progress); box.textContent = j.log; });
        m2.close();
        if (j.status === "done") { const o = j.result; modal("Import terminé", h("div", { class: "stack" }, h("div", null, `${o.extracted} extraits · ${o.downloaded} téléchargés · ${o.kept} conservés`), o.failed.length ? h("div", { class: "alert critical" }, h("b", null, "Échecs :"), o.failed.map((f) => h("div", { class: "small" }, `${f.name} — ${f.reason}`))) : null, o.manual.length ? h("div", { class: "alert warning" }, h("b", null, "À ajouter manuellement :"), o.manual.map((f) => h("div", { class: "small" }, `${f.name} — ${f.reason}`))) : null), [{ label: "Fermer", onclick: () => {} }]); }
        else toast("Échec de l'import : " + j.error, "error");
        reload();
      } catch (e) { fail(e); return false; } } }]);
}

/* ---- sauvegardes ---- */
async function tabBackups(body, s) {
  const list = h("div", { class: "tablewrap" }), arch = h("div", { class: "tablewrap" });
  const loadArch = async () => {
    let r;
    try { r = await api("GET", `/api/archives?server=${s.id}`); } catch (e) { clear(arch).append(e.message); return; }
    clear(arch).append(r.archives.length ? h("table", null, h("thead", null, h("tr", null, ["Archive", "Monde", "Taille", "Contenu", "Actions"].map((t) => h("th", null, t)))), h("tbody", null, r.archives.map((a) => h("tr", null,
      h("td", null, h("div", null, fmtDate(a.archived_at)), h("div", { class: "muted small mono" }, a.file), a.present ? null : h("div", { class: "issue error" }, "Fichier absent")),
      h("td", { class: "small" }, `${a.worlds.join(", ")} — ${TYPES[a.server_type] || a.server_type} ${a.mc_version}`),
      h("td", { class: "small" }, fmtBytes(a.zip_size), h("div", { class: "muted" }, "monde : " + fmtBytes(a.size))),
      h("td", { class: "small" }, a.stats ? `${a.stats.players} joueur(s), ${Math.round((a.stats.play_time_s || 0) / 3600)} h de jeu` : "—"),
      h("td", { class: "row" }, h("button", { class: "sm", disabled: !a.present, onclick: () => guard(async () => { const v = await api("POST", `/api/archives/${a.id}/verify`); toast(v.ok ? "Archive intacte (contenu et SHA-256 vérifiés)." : "Archive ABÎMÉE : " + (!v.sha256_ok ? "empreinte différente" : "contenu illisible"), v.ok ? "ok" : "error"); }) }, "Vérifier"),
        h("button", { class: "sm", disabled: !a.present, onclick: async () => { const st = (state.snap && srvOf(s.id)) || s; if (st.state !== "stopped") return toast("Arrêtez le serveur avant de restaurer un monde.", "error");
          if (await confirmBox("Restaurer ce monde", "L'archive est vérifiée puis extraite dans un dossier temporaire. Le monde actuel (s'il existe) est renommé et conservé, jamais supprimé.", "Restaurer")) await runJob(() => api("POST", `/api/archives/${a.id}/restore`, { server_id: s.id }), "Restauration du monde", loadArch); } }, "Restaurer ici"))))))
      : h("p", { class: "muted small" }, "Aucune archive. Utilisez « Archiver le monde… » dans la Vue d'ensemble (serveur arrêté)."));
  };
  const load = async () => {
    const r = await api("GET", `/api/servers/${s.id}/backups`);
    clear(list).append(r.backups.length ? h("table", null, h("thead", null, h("tr", null, ["Fichier", "Type", "Taille", "Date", "Actions"].map((t) => h("th", null, t)))), h("tbody", null, r.backups.map((b) =>
      h("tr", null, h("td", { class: "mono small" }, b.filename, b.exists ? null : h("div", { class: "issue error" }, "Fichier absent du disque")), h("td", null, { manual: "Manuelle", auto: "Automatique", "pre-restore": "Avant restauration", "pre-import": "Avant import" }[b.kind] || b.kind), h("td", null, fmtBytes(b.size)), h("td", { class: "small" }, fmtDate(b.created_at)),
        h("td", { class: "row" }, b.exists ? h("a", { class: "btn sm", href: `${API}/api/servers/${s.id}/backups/${b.id}/download` }, "Télécharger") : null,
          h("button", { class: "sm", disabled: !b.exists, onclick: async () => {
            const st = (state.snap && srvOf(s.id)) || s; if (st.state !== "stopped") return toast("Arrêtez le serveur avant de restaurer.", "error");
            if (await confirmBox("Restaurer la sauvegarde", `Restaurer « ${b.filename} » ? Le monde et les fichiers actuels seront remplacés (une sauvegarde de sécurité de l'état actuel est créée avant).`, "Restaurer", true)) await runJob(() => api("POST", `/api/servers/${s.id}/backups/${b.id}/restore`, { confirm: true }), "Restauration", load); } }, "Restaurer"),
          h("button", { class: "sm danger", onclick: async () => { if (await confirmBox("Supprimer la sauvegarde", `Supprimer définitivement « ${b.filename} » ?`, "Supprimer", true)) guard(async () => { await api("DELETE", `/api/servers/${s.id}/backups/${b.id}?confirm=true`); load(); }); } }, "Supprimer")))))) : h("p", { class: "muted" }, "Aucune sauvegarde."));
  };
  body.append(h("div", { class: "row between" }, h("h2", null, "Sauvegardes"), h("button", { class: "primary", onclick: () => runJob(() => api("POST", `/api/servers/${s.id}/backups`), "Sauvegarde", load) }, "Sauvegarder maintenant")),
    h("p", { class: "muted small" }, "Serveur en marche : les écritures du monde sont gelées (save-off / save-all) pendant l'archivage, puis réactivées. Chaque archive est vérifiée avant de remplacer les anciennes. Les planifications se règlent dans Configuration."), list,
    h("h2", null, "Archives de mondes (disque dur)"), arch);
  await load();
  await loadArch();
}
async function runJob(starter, label, after) {
  try {
    const r = await starter(); const pb = bar(0), box = h("pre", { class: "joblog" }, label + " en cours…");
    const m = modal(label, h("div", null, pb, box), []);
    const j = await trackJob(r.job, (j) => { setBar(pb, j.progress); box.textContent = j.log; });
    m.close(); toast(j.status === "done" ? `${label} : terminé.` : `${label} : échec — ${j.error}`, j.status === "done" ? "ok" : "error"); if (after) after();
  } catch (e) { fail(e); }
}

/* ---- configuration ---- */
/* ---- stockage SSD / HDD et archives du monde (V4 §7) ---- */
async function storageCard(s) {
  let st;
  try { st = await api("GET", "/api/storage"); } catch (e) { return h("div", { class: "card" }, e.message); }
  const me = st.servers.find((x) => x.id === s.id) || {};
  const running = ["running", "starting", "stopping"].includes(s.state);
  const move = (tier) => guard(async () => {
    let msg = tier === "ssd" ? "Le serveur est copié sur le SSD, chaque fichier est vérifié, puis l'original est supprimé du disque dur." : "Le serveur est copié sur le disque dur, vérifié, puis retiré du SSD.";
    if (tier === "ssd") { const pl = await api("GET", `/api/servers/${s.id}/storage/plan`); if (!pl.possible) { toast(pl.reason, "error"); return; } if (pl.evict.length) msg += ` Pour respecter le quota, ${pl.evict.map((e) => "« " + e.name + " »").join(", ")} repartira sur le disque dur.`; }
    if (!(await confirmBox(tier === "ssd" ? "Déplacer sur le SSD" : "Déplacer sur le disque dur", msg, "Déplacer"))) return;
    const r = await api("POST", `/api/servers/${s.id}/storage/move`, { tier });
    const j = await runJob(() => Promise.resolve(r), tier === "ssd" ? "Déplacement vers le SSD" : "Déplacement vers le disque dur", () => route());
  });
  const archive = () => guard(async () => {
    const rem = h("input", { type: "checkbox" });
    modal("Archiver le monde sur le disque dur", h("div", { class: "stack" }, h("p", null, "Le monde est compressé dans une archive vérifiée (contenu et empreinte SHA-256), avec ses statistiques et ses métadonnées. Le serveur doit être arrêté."),
      h("label", { class: "inline" }, rem, "Retirer ensuite le monde du serveur (uniquement après vérification de l'archive ; un nouveau monde sera créé au prochain démarrage)")),
      [{ label: "Annuler", onclick: () => true }, { label: "Archiver", kind: "primary", onclick: async () => { try { const r = await api("POST", `/api/servers/${s.id}/world/archive`, { remove_after: rem.checked, confirm: rem.checked }); runJob(() => Promise.resolve(r), "Archivage du monde", () => route()); } catch (e) { fail(e); return false; } } }]);
  });
  const quotaPct = st.quota ? st.ssd_used / st.quota * 100 : 0;
  if (st.tiering === false) {
    return h("div", { class: "card stack" }, h("h3", null, "Stockage du monde"),
      h("dl", { class: "kv" }, [["Dossier", me.path], ["Taille", fmtBytes(me.size)], ["Dernière sauvegarde", me.last_backup ? fmtDate(me.last_backup) : "aucune"]].map(([k, v]) => [h("dt", null, k), h("dd", null, v)])),
      h("p", { class: "hint" }, "Cette machine n'a qu'un seul disque pour les données : il n'y a pas de niveau SSD séparé."),
      running ? h("p", { class: "hint" }, "Arrêtez le serveur pour archiver son monde.") : null,
      h("div", { class: "row" }, h("button", { class: "sm", disabled: running, onclick: archive }, "Archiver le monde…")));
  }
  return h("div", { class: "card stack" }, h("h3", null, "Stockage du monde"),
    h("dl", { class: "kv" }, [["Emplacement", me.tier === "ssd" ? "SSD (rapide)" : "Disque dur (HDD)"], ["Dossier", me.path], ["Taille", fmtBytes(me.size)], ["Dernière sauvegarde", me.last_backup ? fmtDate(me.last_backup) : "aucune"]].map(([k, v]) => [h("dt", null, k), h("dd", { class: k === "Dossier" ? "mono" : "" }, v)])),
    h("div", null, h("div", { class: "muted small" }, `SSD : ${fmtBytes(st.ssd_used)} utilisés sur un quota de ${fmtBytes(st.quota)}` + (st.auto ? " · mise sur SSD automatique au démarrage" : "")), bar(quotaPct)),
    running ? h("p", { class: "hint" }, "Arrêtez le serveur pour le déplacer ou archiver son monde.") : null,
    h("div", { class: "row" }, me.tier === "ssd" ? h("button", { class: "sm", disabled: running, onclick: () => move("hdd") }, "Mettre sur le disque dur") : h("button", { class: "sm", disabled: running, onclick: () => move("ssd") }, "Déplacer sur le SSD"),
      h("button", { class: "sm", disabled: running, onclick: archive }, "Archiver le monde…")));
}

function notifPrefsCard(s) {
  const muted = new Set(((s.notify_prefs || {}).muted) || []);
  const kinds = ["server.started", "server.start_failed", "server.stopped", "server.unexpected_stop", "server.crashed", "server.auto_restart", "server.unreachable", "server.reachable", "backup.failed", "log.critical"];
  const boxes = kinds.map((k) => ({ k, c: h("input", { type: "checkbox", checked: !muted.has(k) }) }));
  return h("div", { class: "card stack" }, h("h3", null, "Notifications externes (Discord) pour ce serveur"),
    h("p", { class: "hint" }, "Décochez les événements à ne jamais envoyer pour ce serveur. Les notifications internes au panneau restent toujours visibles."),
    h("div", { class: "check-grid" }, boxes.map(({ k, c }) => h("label", { class: "inline" }, c, EVENT_LABELS[k] || k))),
    h("div", null, h("button", { onclick: () => guard(async () => { await api("PUT", `/api/servers/${s.id}/notify`, { muted: boxes.filter((x) => !x.c.checked).map((x) => x.k) }); toast("Préférences de notification enregistrées.", "ok"); }) }, "Enregistrer les notifications")));
}
async function tabConfig(body, s) {
  const bp = s.backup_prefs || {};
  const bStop = h("input", { type: "checkbox", checked: !!bp.on_stop });
  const desc = h("input", { type: "text", value: s.description || "", maxlength: 500 }), xmx = h("input", { type: "number", value: s.ram_max_mb, min: 512, step: 256 }), xms = h("input", { type: "number", value: s.ram_min_mb, min: 128, step: 128 }), port = h("input", { type: "number", value: s.port, min: 1024, max: 65535 });
  const jvm = h("input", { type: "text", value: s.jvm_extra || "", class: "mono", placeholder: "-XX:+UseG1GC" });
  const auto = h("input", { type: "checkbox", checked: s.autostart }), ar = h("input", { type: "checkbox", checked: s.auto_restart });
  const bEn = h("input", { type: "checkbox", checked: bp.enabled }), bInt = h("input", { type: "number", value: bp.interval_hours || 24, min: 1, max: 720 }), bKeep = h("input", { type: "number", value: bp.keep || 7, min: 1, max: 100 });
  const bMode = h("select", null, h("option", { value: "hot" }, "À chaud (save-off / save-all, serveur en marche)"), h("option", { value: "stop" }, "Arrêt propre pendant l'archive, puis redémarrage")); bMode.value = bp.mode || "hot";
  const dname = h("input", { type: "text", value: s.name, maxlength: 48 });
  const limC = await api("GET", "/api/meta/limits");
  xmx.max = limC.ram.max_xmx_mb;
  const fc = h("div", { class: "alert warning", hidden: true });
  const upd = async () => { try { const r = await api("GET", `/api/meta/ram-forecast?xmx=${+xmx.value || 0}&server=${s.id}`); fc.hidden = !r.overcommit; fc.textContent = r.message; } catch (e) { fc.hidden = true; } };
  xmx.addEventListener("input", upd); upd();
  const presetsC = ramPresets(limC.ram, xmx, xms, upd);
  const save = () => guard(async () => { await api("PATCH", `/api/servers/${s.id}`, { display_name: dname.value, description: desc.value, ram_max_mb: +xmx.value, ram_min_mb: +xms.value, port: +port.value, jvm_extra: jvm.value, autostart: auto.checked, auto_restart: ar.checked, backup_prefs: { enabled: bEn.checked, interval_hours: +bInt.value, keep: +bKeep.value, mode: bMode.value, on_stop: bStop.checked } }); toast("Configuration enregistrée.", "ok"); });
  const running = s.state !== "stopped" && s.state !== "error";
  if (running) { [xmx, xms, port].forEach((e) => { e.disabled = true; }); presetsC.querySelectorAll("button").forEach((b) => { b.disabled = true; }); }
  body.append(h("div", { class: "card stack" }, running ? h("div", { class: "alert warning small" }, "Le serveur est actif : la mémoire et le port ne peuvent être modifiés qu'à l'arrêt.") : null,
    h("div", { class: "field" }, h("label", null, "Nom du serveur"), dname, h("div", { class: "hint" }, `Changer le nom ne modifie rien sur le disque (dossier : ${s.slug || s.name}).`)),
    h("div", { class: "field" }, h("label", null, "Description"), desc),
    h("h3", null, "Mémoire"), h("div", { class: "hint" }, `Maximum par serveur : ${fmtMb(limC.ram.max_xmx_mb)} (RAM totale − réserve système, marge Java de ${limC.ram.overhead_pct} % comprise).`), presetsC, fc, h("div", { class: "row" }, h("div", { class: "field" }, h("label", null, "RAM max (Mo)"), xmx), h("div", { class: "field" }, h("label", null, "RAM min (Mo)"), xms), h("div", { class: "field" }, h("label", null, "Port"), port)),
    h("div", { class: "field" }, h("label", null, "Arguments JVM supplémentaires"), jvm, h("div", { class: "hint" }, "Chaque argument commence par « - ». Les caractères de contrôle du shell sont refusés.")),
    h("label", { class: "inline" }, auto, "Démarrer automatiquement avec la machine (s'il était en marche)"), h("label", { class: "inline" }, ar, "Redémarrer automatiquement après un plantage (3 essais maximum par 10 min)"),
    h("h3", null, "Sauvegardes automatiques"), h("label", { class: "inline" }, bEn, "Activer"), h("label", { class: "inline" }, bStop, "Sauvegarder aussi à chaque arrêt normal du serveur"),
    h("div", { class: "row" }, h("div", { class: "field" }, h("label", null, "Toutes les (heures)"), bInt), h("div", { class: "field" }, h("label", null, "Garder les N dernières"), bKeep)), h("div", { class: "field" }, h("label", null, "Méthode"), bMode),
    h("div", null, h("button", { class: "primary", onclick: save }, "Enregistrer"))),
    notifPrefsCard(s),
    h("div", { class: "card stack", style: null }, h("h3", null, "Zone de danger"), h("p", { class: "muted small" }, "La suppression retire le dossier de ce serveur uniquement (jamais les autres). Ses sauvegardes sont conservées sauf si vous cochez l'option."),
      h("button", { class: "danger", onclick: () => { const nm = h("input", { type: "text", placeholder: s.name }), bk = h("input", { type: "checkbox", id: "dbk" });
        modal("Supprimer le serveur", h("div", { class: "stack" }, h("p", null, `Cette action est définitive : le monde, les mods et la configuration de « ${s.name} » seront supprimés.`), h("div", { class: "field" }, h("label", null, "Tapez exactement le nom du serveur pour confirmer"), nm), h("label", { class: "inline", for: "dbk" }, bk, "Supprimer aussi ses sauvegardes")), [{ label: "Annuler", onclick: () => {} }, { label: "Supprimer définitivement", kind: "danger", onclick: async () => { try { await api("DELETE", `/api/servers/${s.id}`, { confirm_name: nm.value, delete_backups: bk.checked }); toast("Serveur supprimé.", "ok"); go("#/servers"); } catch (e) { fail(e); return false; } } }]); } }, "Supprimer ce serveur…")));
}

/* ---------------- ressources système ---------------- */
addRoute(/^#\/resources$/, async (main) => {
  const sys = h("div", { class: "grid small" }), srvT = h("div", { class: "tablewrap" }), usage = {};
  main.append(pageHead("Ressources système", "Valeurs en direct de la machine et de chaque serveur."), sys, h("h2", null, "Serveurs"), srvT, h("p", { class: "muted small" }, "Valeurs actualisées toutes les 2 s. L'espace disque par serveur est recalculé toutes les 2 minutes."));
  const refreshUsage = async () => { try { Object.assign(usage, (await api("GET", "/api/usage")).usage); } catch (e) { /* ignoré */ } };
  await refreshUsage();
  const t = setInterval(refreshUsage, 120000);
  const render = (snap) => {
    const y = snap.system, b = y.ram_budget;
    const card = (t, v, sub, pct) => h("div", { class: "card" }, h("div", { class: "muted small" }, t), h("div", { class: "big-num" }, v), pct !== null ? bar(pct) : null, h("div", { class: "muted small" }, sub));
    clear(sys).append(card("CPU global", y.cpu_percent.toFixed(0) + " %", `${y.cpus} threads`, y.cpu_percent), card("Charge (1/5/15 min)", y.load.join(" / "), `Allumé depuis ${fmtDur(y.uptime)}`, null),
      card("RAM utilisée", fmtMb(y.mem.used_mb), `${fmtMb(y.mem.available_mb)} disponibles / ${fmtMb(y.mem.total_mb)}`, y.mem.used_mb / y.mem.total_mb * 100),
      card("RAM attribuée aux serveurs", fmtMb(b.assigned_mb ?? 0), `Utilisée ${fmtMb(b.used_mb ?? 0)} · restante ${fmtMb(b.remaining_mb ?? b.free_budget_mb)} (réserve système ${fmtMb(b.reserve_mb)}, marge Java ${b.overhead_pct} %)`, b.budget_mb ? b.reserved_mb / b.budget_mb * 100 : 0),
      card("Swap", fmtMb(y.mem.swap_used_mb), `sur ${fmtMb(y.mem.swap_total_mb)}`, y.mem.swap_total_mb ? y.mem.swap_used_mb / y.mem.swap_total_mb * 100 : null),
      card("Disque système", y.disk_ssd.percent + " %", `${fmtBytes(y.disk_ssd.free)} libres sur ${fmtBytes(y.disk_ssd.total)}`, y.disk_ssd.percent),
      card("Disque de données", y.disk_data.percent + " %", `${fmtBytes(y.disk_data.free)} libres sur ${fmtBytes(y.disk_data.total)}${y.data_mounted ? "" : " — NON MONTÉ"}`, y.disk_data.percent));
    clear(srvT).append(h("table", null, h("thead", null, h("tr", null, ["Serveur", "État", "PID", "RAM", "CPU", "Actif", "Joueurs", "Espace disque"].map((t) => h("th", null, t)))),
      h("tbody", null, snap.servers.map((s) => h("tr", null, h("td", null, h("a", { href: "#/server/" + s.id }, s.name)), h("td", null, badge(s.state)), h("td", { class: "mono" }, s.pid || "—"), h("td", null, s.pid ? `${fmtMb(s.rss_mb)} / ${fmtMb(s.ram_max_mb)}` : "—"), h("td", null, s.pid ? s.cpu.toFixed(0) + " %" : "—"), h("td", null, s.pid ? fmtDur(s.uptime) : "—"), h("td", null, s.players ?? "—"), h("td", null, usage[s.id] !== undefined ? fmtBytes(usage[s.id]) : "…"))))));
  };
  render(state.snap || (await api("GET", "/api/system")));
  return { onStats: render, destroy: () => clearInterval(t) };
});

/* ---------------- paramètres ---------------- */
function storageSettingsCard(stg) {
  if (stg.tiering === false) {
    return h("div", { class: "card stack" }, h("h3", null, "Stockage des mondes"),
      h("p", { class: "muted small" }, `Machine à disque unique : les mondes restent dans ${stg.hdd_dir}. Le niveau SSD (quota, déplacement automatique) est désactivé (MCPANEL_SSD_TIER=0 dans panel.env).`),
      h("div", { class: "muted small" }, `${fmtBytes(stg.hdd_free_disk)} libres sur le disque`));
  }
  const auto = h("input", { type: "checkbox", checked: stg.auto }), q = h("input", { type: "number", min: 5, max: 2000, step: 5, value: Math.round(stg.quota / 1073741824) });
  const rows = stg.servers.map((x) => h("tr", null, h("td", null, x.name), h("td", null, h("span", { class: "badge plain " + (x.tier === "ssd" ? "running" : "stopped") }, x.tier === "ssd" ? "SSD" : "HDD")), h("td", { class: "num" }, fmtBytes(x.size)), h("td", { class: "small" }, x.last_started ? fmtAgo(x.last_started) : "jamais")));
  return h("div", { class: "card stack" }, h("h3", null, "Stockage des mondes (SSD / disque dur)"),
    h("div", null, h("div", { class: "muted small" }, `SSD : ${fmtBytes(stg.ssd_used)} utilisés sur ${fmtBytes(stg.quota)} · ${fmtBytes(stg.ssd_free_disk)} libres sur le disque`), bar(stg.quota ? stg.ssd_used / stg.quota * 100 : 0)),
    h("label", { class: "inline" }, auto, "Mettre automatiquement le monde sur le SSD au démarrage d'un serveur"),
    h("div", { class: "field" }, h("label", null, "Quota SSD (Go)"), q, h("div", { class: "hint" }, "Au-delà, les serveurs arrêtés démarrés il y a le plus longtemps repartent sur le disque dur. Un serveur allumé n'est jamais déplacé ; 10 Go restent toujours libres sur le SSD.")),
    h("div", null, h("button", { class: "primary", onclick: () => guard(async () => { await api("PUT", "/api/storage/settings", { quota_gb: +q.value, auto: auto.checked }); toast("Réglages de stockage enregistrés.", "ok"); }) }, "Enregistrer")),
    stg.servers.length ? h("div", { class: "tablewrap" }, h("table", null, h("thead", null, h("tr", null, ["Serveur", "Emplacement", "Taille", "Dernier démarrage"].map((t) => h("th", null, t)))), h("tbody", null, rows))) : null);
}

addRoute(/^#\/settings$/, async (main) => {
  const st = await api("GET", "/api/settings"), jv = await api("GET", "/api/java"), au = await api("GET", "/api/audit?limit=60"), stg = await api("GET", "/api/storage");
  const cur = h("input", { type: "password", autocomplete: "current-password" }), nw = h("input", { type: "password", autocomplete: "new-password" });
  const cool = h("input", { type: "number", value: st.start_cooldown_s ?? 180, min: 0, max: 3600, step: 30 }), perh = h("input", { type: "number", value: st.max_starts_per_hour ?? 6, min: 1, max: 60 });
  const ovh = h("input", { type: "number", value: st.ram_overhead_pct ?? 20, min: 5, max: 50, step: 1 });
  const reserve = h("input", { type: "number", value: st.ram_reserve_mb, min: 512, max: 8192, step: 256 }), cf = h("input", { type: "password", placeholder: st.curseforge_key_set ? "Clé enregistrée (laisser vide pour la conserver)" : "Clé API CurseForge (facultative)", autocomplete: "off" });
  main.append(pageHead("Paramètres", "Mémoire, sécurité des démarrages, runtimes Java et journal d'activité."), h("div", { class: "grid" },
    h("div", { class: "card stack" }, h("h3", null, "Mot de passe administrateur"), h("div", { class: "field" }, h("label", null, "Mot de passe actuel"), cur), h("div", { class: "field" }, h("label", null, "Nouveau mot de passe (12 caractères minimum)"), nw),
      h("button", { class: "primary", onclick: () => guard(async () => { await api("POST", "/api/auth/password", { current: cur.value, new: nw.value }); cur.value = nw.value = ""; toast("Mot de passe modifié. Les autres sessions ont été fermées.", "ok"); }) }, "Changer le mot de passe")),
    h("div", { class: "card stack" }, h("h3", null, "Mémoire et sources"), h("div", { class: "field" }, h("label", null, "Réserve RAM pour le système (Mo)"), reserve, h("div", { class: "hint" }, "Mémoire jamais attribuée aux serveurs (Debian, KDE, panneau).")),
      h("div", { class: "field" }, h("label", null, "Marge Java (%)"), ovh, h("div", { class: "hint" }, "Java occupe plus que sa RAM maximale (-Xmx). Cette marge est comptée pour qu'un serveur réglé au maximum puisse vraiment démarrer. Plus basse = plus de RAM proposée, mais plus de risque de saturer la machine (20 % conseillé).")),
      h("div", { class: "field" }, h("label", null, "Clé API CurseForge"), cf, h("div", { class: "hint" }, "Nécessaire uniquement pour installer les modpacks CurseForge depuis leur manifeste.")),
      h("h3", null, "Démarrages par les utilisateurs"), h("p", { class: "hint" }, "Protège contre les démarrages répétés. Ne concerne pas les administrateurs."),
      h("div", { class: "row" }, h("div", { class: "field" }, h("label", null, "Pause entre deux démarrages (secondes)"), cool), h("div", { class: "field" }, h("label", null, "Démarrages maximum par heure et par serveur"), perh)),
      h("button", { class: "primary", onclick: () => guard(async () => { const b = { ram_reserve_mb: +reserve.value, ram_overhead_pct: +ovh.value, start_cooldown_s: +cool.value, max_starts_per_hour: +perh.value }; if (cf.value.trim()) b.curseforge_key = cf.value.trim(); await api("PUT", "/api/settings", b); cf.value = ""; toast("Paramètres enregistrés.", "ok"); }) }, "Enregistrer")),
    storageSettingsCard(stg),
    h("div", { class: "card stack" }, h("h3", null, "Runtimes Java"), jv.installed.length ? jv.installed.map((j) => h("div", { class: "small" }, `Java ${j.major} — ${j.source} — `, h("span", { class: "mono" }, j.path))) : h("div", { class: "muted small" }, "Aucun runtime installé : ils sont téléchargés automatiquement lors de la création d'un serveur."),
      h("div", { class: "row" }, [8, 17, 21, 25].map((m) => h("button", { class: "sm", onclick: () => runJob(() => api("POST", "/api/java/" + m), "Installation de Java " + m, () => route()) }, "Installer Java " + m)))),
    h("div", { class: "card stack" }, h("h3", null, "Informations"), ...[["Version", st.version], ["Données", st.data_root], ["Port du panneau", st.port], ["Réseaux autorisés", st.allowed_nets.join(", ")], ["Envoi maximal", st.max_upload_mb + " Mo"]].map(([k, v]) => h("div", { class: "small" }, h("span", { class: "muted" }, k + " : "), h("span", { class: "mono" }, v))))),
    h("h2", null, "Journal des actions administratives"), h("div", { class: "tablewrap" }, h("table", null, h("thead", null, h("tr", null, ["Date", "Utilisateur", "IP", "Action", "Cible", "Détail"].map((t) => h("th", null, t)))),
      h("tbody", null, au.entries.map((e) => h("tr", null, h("td", { class: "small" }, fmtDate(e.ts)), h("td", null, e.user || "—"), h("td", { class: "mono small" }, e.ip), h("td", null, e.action), h("td", null, e.target || ""), h("td", { class: "small" }, e.detail || "")))))));
});

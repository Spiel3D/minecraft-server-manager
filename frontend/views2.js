"use strict";
/* Écrans de la v1.1 : espace utilisateur, comptes, API & tokens (+ documentation), notifications, supervision Debian. */

const EVENT_LABELS = { "server.starting": "Démarrage", "server.started": "Ouvert aux joueurs", "server.start_failed": "Échec de démarrage", "server.stopping": "Arrêt en cours", "server.stopped": "Arrêt normal",
  "server.unexpected_stop": "Arrêt inattendu", "server.crashed": "Plantage", "server.auto_restart": "Redémarrage automatique", "server.unreachable": "Ne répond plus", "server.reachable": "Répond de nouveau",
  "system.ram_low": "RAM saturée", "system.disk_low": "Disque plein", "backup.failed": "Sauvegarde en échec", "log.critical": "Erreurs critiques (journaux)" };
const EVENT_BADGE = { "server.starting": "starting", "server.started": "running", "server.stopping": "stopping", "server.stopped": "stopped", "server.crashed": "error", "server.start_failed": "error",
  "server.unexpected_stop": "error", "server.auto_restart": "starting", "server.unreachable": "error", "server.reachable": "running", "system.ram_low": "error", "system.disk_low": "error", "backup.failed": "error", "log.critical": "starting" };
const field = (label, input, hint) => h("div", { class: "field" }, h("label", null, label), input, hint ? h("div", { class: "hint" }, hint) : null);
const fmtWait = (s) => (s >= 120 ? Math.ceil(s / 60) + " minutes" : s + " secondes");
function randomPassword(n = 16) {
  const al = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789";
  const buf = new Uint32Array(n); crypto.getRandomValues(buf);
  return Array.from(buf, (x) => al[x % al.length]).join("");
}

/* ======================================================================= espace utilisateur ======================================================================= */
function userCard(s, limits) {
  const lp = h("span"), st = h("span"), pl = h("b", null, "—"), note = h("div", { class: "hint", role: "status" });
  const btn = ibtn("play", "Démarrer", { class: "primary", "data-act": "user-start" });
  const card = h("div", { class: "card srv", "data-id": s.id },
    h("div", { class: "head" }, lp, h("h3", { class: "grow" }, s.name), st),
    h("div", { class: "meta" }, h("span", { class: "chip" }, typeLine(s))),
    s.description ? h("div", { class: "desc" }, s.description) : null,
    h("div", { class: "field" }, h("label", null, "Adresse à copier dans Minecraft"), copyLine(s.address)),
    h("div", { class: "stats one" }, h("div", null, h("small", null, "Joueurs connectés"), pl)),
    h("div", { class: "row" }, btn), note);
  btn.onclick = async () => {
    btn.disabled = true; note.textContent = "";
    try {
      await api("POST", `/api/me/servers/${s.id}/start`);
      toast(`« ${s.name} » démarre : patientez une minute avant de vous connecter.`, "ok");
      note.textContent = "Démarrage demandé.";
    } catch (e) {
      note.textContent = e.status === 429 ? `Trop de démarrages rapprochés. Réessayez dans ${fmtWait(e.retryAfter || 60)}.` : e.message;
      toast(note.textContent, "error");
    }
  };
  card.update = (n) => {
    s = { ...s, ...n };
    clear(lp).append(lamp(n.state)); clear(st).append(badge(n.state));
    pl.textContent = n.state === "running" && n.players !== null && n.players !== undefined ? `${n.players} / ${n.max_players}` : "—";
    btn.disabled = n.state !== "stopped";
    btn.hidden = n.state === "running";
    btn.querySelector("span").textContent = n.state === "starting" || n.state === "installing" ? "Démarrage…" : "Démarrer";
    if (n.state === "running") note.textContent = "Le serveur est prêt : copiez l'adresse et rejoignez-le.";
  };
  card.update(s);
  return card;
}

addRoute(/^#\/home$/, async (main) => {
  const r = await api("GET", "/api/me/servers");
  const grid = h("div", { class: "grid" }), cards = {};
  const lim = r.limits;
  main.append(pageHead("Mes serveurs", "Les serveurs auxquels l'administrateur vous a donné accès."),
    lim ? h("div", { class: "alert info small" }, icon("info"), h("div", null, `Pour éviter les abus, un serveur ne peut être relancé qu'après ${fmtWait(lim.start_cooldown_s)}, et au plus ${lim.max_starts_per_hour} fois par heure.`)) : null, grid);
  const render = (list) => {
    if (!list.length) { if (!cards.__empty) { clear(grid).append(emptyState("server", "Aucun serveur attribué", "Demandez à l'administrateur de vous donner accès à un serveur.")); cards.__empty = true; } return; }
    if (cards.__empty) { clear(grid); cards.__empty = false; }
    for (const s of list) { if (!cards[s.id]) { cards[s.id] = userCard(s, lim); grid.append(cards[s.id]); } cards[s.id].update(s); }
    for (const id of Object.keys(cards)) if (id !== "__empty" && !list.find((s) => s.id === id)) { cards[id].remove(); delete cards[id]; }
  };
  render(r.servers);
  return { onStats: (snap) => { if (!isAdmin()) render(snap.servers); } };
});

addRoute(/^#\/account$/, async (main) => {
  const cur = h("input", { type: "password", autocomplete: "current-password" }), nw = h("input", { type: "password", autocomplete: "new-password" });
  main.append(pageHead("Mon compte", `Connecté en tant que ${state.username} (${isAdmin() ? "administrateur" : "utilisateur"}).`),
    h("div", { class: "card stack", id: "pwcard" }, h("h3", null, "Mot de passe"), field("Mot de passe actuel", cur), field("Nouveau mot de passe", nw, "12 caractères minimum, ni trop courant ni trop simple."),
      h("div", null, ibtn("check", "Changer le mot de passe", { class: "primary", onclick: () => guard(async () => { await api("POST", "/api/auth/password", { current: cur.value, new: nw.value }); cur.value = nw.value = ""; toast("Mot de passe modifié. Les autres sessions ont été fermées.", "ok"); }) }))));
});

/* ======================================================================= comptes (administrateur) ======================================================================= */
addRoute(/^#\/users$/, async (main) => {
  const wrap = h("div");
  let data;
  const load = async () => {
    data = await api("GET", "/api/users");
    const me = data.users.find((u) => u.username === state.username);
    const nameOf = (id) => (data.servers.find((x) => x.id === id) || {}).name || "?";
    clear(wrap).append(h("div", { class: "tablewrap" }, h("table", null,
      h("thead", null, h("tr", null, ["Compte", "Rôle", "État", "Serveurs accessibles", "Dernière connexion", ""].map((t) => h("th", null, t)))),
      h("tbody", null, data.users.map((u) => {
        const self = me && me.id === u.id;
        return h("tr", null,
          h("td", null, h("div", null, h("b", null, u.display_name), self ? h("span", { class: "muted" }, " (vous)") : null), h("div", { class: "muted small mono" }, u.username)),
          h("td", null, h("span", { class: "badge plain " + u.role }, u.role === "admin" ? "Administrateur" : "Utilisateur")),
          h("td", null, h("span", { class: "badge plain " + (u.active ? "running" : "off") }, u.active ? "Actif" : "Désactivé")),
          h("td", { class: "small" }, u.role === "admin" ? "Tous (administrateur)" : u.servers.length ? u.servers.map((id) => nameOf(id)).join(", ") : h("span", { class: "muted" }, "Aucun")),
          h("td", { class: "small" }, u.last_login ? fmtDate(u.last_login) : "jamais"),
          h("td", { class: "actions" }, h("div", { class: "row" },
            ibtn("edit", "Modifier", { class: "sm", onclick: () => editor(u) }),
            ibtn("power", u.active ? "Désactiver" : "Activer", { class: "sm", disabled: self, onclick: () => guard(async () => { await api("PATCH", `/api/users/${u.id}`, { active: !u.active }); toast(u.active ? "Compte désactivé : ses sessions et tokens sont coupés." : "Compte activé.", "ok"); load(); }) }),
            ibtn("trash", "Supprimer", { class: "sm danger", disabled: self, onclick: async () => { if (await confirmBox("Supprimer le compte", `Supprimer définitivement « ${u.username} » ? Ses tokens sont révoqués. Aucun serveur n'est touché.`, "Supprimer", true)) guard(async () => { await api("DELETE", `/api/users/${u.id}`); load(); }); } }))));
      })))));
  };
  const editor = (u) => {
    const isNew = !u;
    const un = h("input", { type: "text", value: u ? u.username : "", disabled: !isNew, autocomplete: "off", placeholder: "alice" });
    const dn = h("input", { type: "text", value: u ? (u.display_name === u.username ? "" : u.display_name) : "", placeholder: "Nom affiché (facultatif)", maxlength: 60 });
    const role = h("select", null, h("option", { value: "user" }, "Utilisateur : voir et démarrer ses serveurs"), h("option", { value: "admin" }, "Administrateur : accès complet")); role.value = u ? u.role : "user";
    const pw = h("input", { type: "text", autocomplete: "off", class: "mono", placeholder: isNew ? "12 caractères minimum" : "Laisser vide pour ne pas changer" });
    const boxes = data.servers.map((s) => ({ s, c: h("input", { type: "checkbox", checked: u && u.servers.includes(s.id) }) }));
    const pick = h("div", null, h("label", null, "Serveurs accessibles"),
      boxes.length ? h("div", { class: "check-grid" }, boxes.map(({ s, c }) => h("label", { class: "inline" }, c, s.name))) : h("p", { class: "hint" }, "Aucun serveur n'existe encore."),
      h("div", { class: "hint" }, "L'utilisateur ne voit, ne démarre et ne peut interroger que ces serveurs. Les autres n'existent pas pour lui."));
    const sync = () => { pick.hidden = role.value === "admin"; }; role.onchange = sync; sync();
    modal(isNew ? "Nouveau compte" : `Modifier « ${u.username} »`, h("div", { class: "stack" },
      field("Identifiant", un, isNew ? "Lettres, chiffres, « . », « _ » ou « - »." : null), field("Nom affiché", dn), field("Rôle", role),
      field(isNew ? "Mot de passe" : "Nouveau mot de passe", h("div", { class: "row" }, pw, h("button", { type: "button", class: "sm", onclick: () => { pw.value = randomPassword(); } }, "Générer")), isNew ? "Communiquez-le à la personne : elle pourra le changer ensuite." : "Changer le mot de passe ferme toutes les sessions de ce compte."), pick),
      [{ label: "Annuler", onclick: () => {} },
       { label: isNew ? "Créer le compte" : "Enregistrer", kind: "primary", onclick: async () => {
          const body = { display_name: dn.value.trim(), role: role.value, servers: role.value === "admin" ? [] : boxes.filter((x) => x.c.checked).map((x) => x.s.id) };
          if (isNew) body.username = un.value.trim(); if (pw.value) body.password = pw.value;
          try { await api(isNew ? "POST" : "PATCH", isNew ? "/api/users" : `/api/users/${u.id}`, body); toast(isNew ? "Compte créé." : "Compte mis à jour.", "ok"); load(); } catch (e) { fail(e); return false; }
        } }]);
  };
  main.append(pageHead("Comptes", "Créez des comptes, choisissez les serveurs que chacun peut voir et démarrer.", ibtn("plus", "Nouveau compte", { class: "primary", onclick: () => editor(null) })),
    h("div", { class: "alert info small" }, icon("shield"), h("div", null, "Un utilisateur ne peut ni ouvrir la console, ni arrêter, ni modifier fichiers, mods ou paramètres : ces droits sont refusés par le serveur, pas seulement masqués dans l'interface.")), wrap);
  await load();
});

/* ======================================================================= API & tokens ======================================================================= */
const apiTabs = (active) => h("nav", { class: "tabs" }, [["#/api", "Tokens", "key"], ["#/api/docs", "Documentation", "file"]].map(([href, l, ic]) => h("a", { href, class: active === href ? "active" : "" }, icon(ic), l)));
const SCOPE_NAMES = { "servers:read": "Serveurs", "players:read": "Joueurs", "servers:start": "Démarrage", "events:read": "Événements", "system:read": "Système" };

addRoute(/^#\/api$/, async (main) => {
  const wrap = h("div"); let all = false, meta = { scopes: {} };
  const load = async () => {
    meta = await api("GET", "/api/tokens" + (all && isAdmin() ? "?all=1" : ""));
    clear(wrap).append(meta.tokens.length ? h("div", { class: "tablewrap" }, h("table", null,
      h("thead", null, h("tr", null, ["Nom", all ? "Compte" : null, "Préfixe", "Permissions", "Dernière utilisation", "Expire", "État", ""].filter((x) => x !== null).map((t) => h("th", null, t)))),
      h("tbody", null, meta.tokens.map((t) => h("tr", null,
        h("td", null, h("b", null, t.name), h("div", { class: "muted small" }, "créé " + fmtAgo(t.created_at))),
        all ? h("td", null, t.username) : null,
        h("td", { class: "mono small" }, t.prefix + "…"),
        h("td", null, h("div", { class: "row" }, t.scopes.map((x) => h("span", { class: "chip", title: x }, SCOPE_NAMES[x] || x)))),
        h("td", { class: "small" }, t.last_used ? [fmtAgo(t.last_used), h("div", { class: "muted mono" }, t.last_ip || "")] : "jamais"),
        h("td", { class: "small" }, t.expires_at ? fmtDate(t.expires_at) : "jamais"),
        h("td", null, h("span", { class: "badge plain " + (t.revoked || t.expired ? "off" : "running") }, t.revoked ? "Révoqué" : t.expired ? "Expiré" : "Actif")),
        h("td", { class: "actions" }, t.revoked ? null : ibtn("x", "Révoquer", { class: "sm danger", onclick: async () => { if (await confirmBox("Révoquer le token", `« ${t.name} » cessera immédiatement de fonctionner. Les programmes qui l'utilisent seront refusés.`, "Révoquer", true)) guard(async () => { await api("DELETE", `/api/tokens/${t.id}`); load(); }); } })))))))
      : emptyState("key", "Aucun token", "Un token permet à un programme (par exemple un futur bot Discord) d'utiliser l'API avec vos droits, sans votre mot de passe.", ibtn("plus", "Créer un token", { class: "primary", onclick: create })));
  };
  const create = () => {
    const name = h("input", { type: "text", maxlength: 60, placeholder: "Bot Discord" });
    const exp = h("select", null, [["", "Jamais"], ["7", "7 jours"], ["30", "30 jours"], ["90", "90 jours"], ["365", "1 an"]].map(([v, l]) => h("option", { value: v }, l)));
    const defaults = ["servers:read", "players:read", "events:read"];
    const boxes = Object.entries(meta.scopes).map(([k, d]) => ({ k, c: h("input", { type: "checkbox", checked: defaults.includes(k) }), d }));
    modal("Nouveau token d'API", h("div", { class: "stack" }, field("Nom", name, "Pour reconnaître son usage plus tard."),
      h("div", null, h("label", null, "Permissions"), h("div", { class: "stack check-grid" }, boxes.map(({ k, c, d }) => h("label", { class: "inline" }, c, h("span", null, h("b", null, SCOPE_NAMES[k] || k), h("span", { class: "muted small" }, " — " + d))))),
        h("div", { class: "hint" }, isAdmin() ? "Un token ne peut jamais dépasser les droits du compte." : "Un token ne voit que les serveurs qui vous sont attribués.")), field("Expiration", exp)),
      [{ label: "Annuler", onclick: () => {} }, { label: "Créer le token", kind: "primary", onclick: async () => {
        const scopes = boxes.filter((x) => x.c.checked).map((x) => x.k);
        if (!scopes.length) { toast("Cochez au moins une permission.", "error"); return false; }
        try { const r = await api("POST", "/api/tokens", { name: name.value.trim(), scopes, expires_days: exp.value ? +exp.value : null }); load(); reveal(r.secret); } catch (e) { fail(e); return false; }
      } }]);
  };
  const reveal = (secret) => modal("Votre token", h("div", { class: "stack" },
    h("div", { class: "alert warning" }, icon("alert"), h("div", null, "Copiez-le maintenant : il ne sera plus jamais affiché. En cas de perte, révoquez-le et créez-en un autre.")),
    h("div", { class: "secret mono" }, secret), h("div", { class: "row" }, ibtn("copy", "Copier le token", { class: "primary", onclick: async () => toast((await copyText(secret)) ? "Token copié." : "Copie impossible : sélectionnez le texte.", "ok") })),
    h("p", { class: "muted small" }, "Exemple d'utilisation :"), h("pre", { class: "joblog" }, `curl -H "Authorization: Bearer ${secret}" ${location.origin}/api/v1/servers`)), [{ label: "J'ai copié mon token", kind: "primary", onclick: () => {} }]);
  const allBox = h("input", { type: "checkbox", onchange: () => { all = allBox.checked; load(); } });
  main.append(pageHead("API & tokens", "Accès programmatique sécurisé : tokens à permissions limitées, révocables à tout moment.", ibtn("plus", "Nouveau token", { class: "primary", onclick: create })), apiTabs("#/api"),
    h("div", { class: "card stack" }, h("div", { class: "kv" }, h("dt", null, "Adresse de l'API"), h("dd", null, copyLine(`${location.origin}/api/v1`)), h("dt", null, "Documentation"), h("dd", null, h("a", { href: "#/api/docs" }, "Voir tous les points d'accès")))),
    isAdmin() ? h("label", { class: "inline" }, allBox, "Afficher les tokens de tous les comptes") : null, wrap);
  await load();
});

addRoute(/^#\/api\/docs$/, async (main) => {
  const spec = await (await fetch(API + "/api/v1/openapi.json", { credentials: "same-origin" })).json();
  const base = location.origin + "/api/v1";
  const groups = {};
  for (const [path, ops] of Object.entries(spec.paths)) for (const [m, op] of Object.entries(ops)) { const g = (op.tags && op.tags[0]) || "Autres"; (groups[g] = groups[g] || []).push({ path, m, op }); }
  const endpoint = ({ path, m, op }) => {
    const ps = (op.parameters || []).filter((p) => p.in !== "header");
    const codes = Object.keys(op.responses || {});
    const url = base + path.replace(/\{[^}]+\}/g, (x) => `<${x.slice(1, -1)}>`);
    return h("details", { class: "endpoint" }, h("summary", null, h("span", { class: "method " + m }, m.toUpperCase()), h("code", { class: "mono" }, "/api/v1" + path), h("span", { class: "muted grow" }, op.summary || "")),
      h("div", { class: "body stack" }, op.description ? h("p", null, op.description) : null,
        ps.length ? h("table", null, h("thead", null, h("tr", null, ["Paramètre", "Où", "Description"].map((t) => h("th", null, t)))), h("tbody", null, ps.map((p) => h("tr", null, h("td", { class: "mono" }, p.name + (p.required ? " *" : "")), h("td", null, p.in === "path" ? "chemin" : "requête"), h("td", { class: "small" }, p.description || ""))))) : null,
        h("div", { class: "small" }, "Réponses : ", codes.map((c) => h("span", { class: "chip" }, c))),
        h("pre", { class: "joblog" }, `curl ${m === "post" ? "-X POST " : ""}${path === "/ping" ? "" : '-H "Authorization: Bearer mcp_…" '}${url}`)));
  };
  main.append(pageHead("API & tokens", "Documentation de l'API REST v1."), apiTabs("#/api/docs"),
    h("div", { class: "grid" },
      h("div", { class: "card stack" }, h("h3", null, "Authentification"), h("p", { class: "small" }, "Ajoutez l'en-tête ", h("code", { class: "inl" }, "Authorization: Bearer mcp_…"), " avec un token créé dans l'onglet Tokens. Les droits d'un token sont l'intersection de ses permissions et de ceux de son compte.")),
      h("div", { class: "card stack" }, h("h3", null, "Format des réponses"), h("pre", { class: "joblog" }, '{ "ok": true, "data": …, "meta": { "api_version": "v1" } }\n{ "ok": false, "error": { "code": "not_found", "message": "…" } }')),
      h("div", { class: "card stack" }, h("h3", null, "Limites"), h("p", { class: "small" }, "120 requêtes par minute et par token, 6 démarrages par minute. Au-delà : HTTP 429 avec l'en-tête ", h("code", { class: "inl" }, "Retry-After"), ". Les démarrages restent soumis à la pause configurée pour les utilisateurs."))),
    Object.entries(groups).map(([g, list]) => [h("h2", null, g), list.map(endpoint)]),
    h("h2", null, "Temps réel (WebSocket)"),
    h("div", { class: "card stack" }, h("p", { class: "small" }, "Connectez-vous à ", h("code", { class: "inl" }, base.replace(/^http/, "ws") + "/ws"), " en envoyant l'en-tête Authorization, ou en envoyant le premier message ", h("code", { class: "inl" }, '{"type":"auth","token":"mcp_…"}'), " dans les 10 secondes."),
      h("pre", { class: "joblog" }, '← {"type":"ready","scopes":[…]}\n← {"type":"servers","data":[ …état de chaque serveur… ]}   (à chaque changement)\n← {"type":"event","data":{"type":"server.started","server_name":"Survie",…}}')),
    h("h2", null, "Préparer un bot Discord"),
    h("div", { class: "card stack small" }, h("p", null, "Un bot n'a besoin d'aucune modification du panneau : créez un token (Serveurs, Joueurs, Démarrage, Événements), puis :"),
      h("ul", null, h("li", null, "liste et statut : ", h("code", { class: "inl" }, "GET /servers"), " ; joueurs : ", h("code", { class: "inl" }, "GET /servers/{id}/players")),
        h("li", null, "démarrage d'un serveur autorisé : ", h("code", { class: "inl" }, "POST /servers/{id}/start")),
        h("li", null, "notifications démarrage / arrêt / plantage : le WebSocket, ou ", h("code", { class: "inl" }, "GET /events?since=<dernier id>"), ", ou les webhooks Discord de la page Notifications (sans aucun bot)."))));
});

/* ======================================================================= notifications (webhooks Discord) ======================================================================= */
addRoute(/^#\/notifications$/, async (main) => {
  const list = h("div"), evs = h("div");
  let kinds = {}, srvs = [];
  const load = async () => {
    const r = await api("GET", "/api/webhooks"); kinds = r.kinds;
    srvs = (await api("GET", "/api/servers")).servers;
    clear(list).append(r.webhooks.length ? h("div", { class: "tablewrap" }, h("table", null,
      h("thead", null, h("tr", null, ["Nom", "Destination", "Événements", "Serveurs", "Dernier envoi", "État", ""].map((t) => h("th", null, t)))),
      h("tbody", null, r.webhooks.map((w) => h("tr", null,
        h("td", null, h("b", null, w.name), w.discord ? h("div", { class: "muted small" }, "Discord") : null),
        h("td", { class: "mono small" }, w.url_hint),
        h("td", null, w.kinds.length ? h("div", { class: "row" }, w.kinds.map((k) => h("span", { class: "chip" }, EVENT_LABELS[k] || k))) : h("span", { class: "muted small" }, "Tous")),
        h("td", { class: "small" }, w.server_ids.length ? w.server_ids.map((i) => (srvs.find((x) => x.id === i) || { name: "?" }).name).join(", ") : h("span", { class: "muted" }, "Tous"), h("div", { class: "muted" }, w.cooldown_s ? `anti-spam : ${fmtWait(w.cooldown_s)}` : "sans anti-spam")),
        h("td", { class: "small" }, w.last_at ? [fmtAgo(w.last_at), h("div", { class: "muted" }, w.last_status)] : "—"),
        h("td", null, h("span", { class: "badge plain " + (w.enabled ? "running" : "off") }, w.enabled ? "Actif" : "En pause")),
        h("td", { class: "actions" }, h("div", { class: "row" },
          ibtn("sliders", "Modifier", { class: "sm", onclick: () => add(w) }),
          ibtn("check", "Tester", { class: "sm", onclick: () => guard(async () => { const t = await api("POST", `/api/webhooks/${w.id}/test`); toast("Test envoyé : " + (t.status || "ok"), String(t.status).startsWith("HTTP 2") || t.status === "ok" ? "ok" : "error"); load(); }) }),
          ibtn("power", w.enabled ? "Mettre en pause" : "Activer", { class: "sm", onclick: () => guard(async () => { await api("PATCH", `/api/webhooks/${w.id}`, { enabled: !w.enabled }); load(); }) }),
          ibtn("trash", "Supprimer", { class: "sm danger", onclick: async () => { if (await confirmBox("Supprimer la notification", `Supprimer « ${w.name} » ?`, "Supprimer", true)) guard(async () => { await api("DELETE", `/api/webhooks/${w.id}`); load(); }); } }))))))))
      : emptyState("bell", "Aucune notification configurée", "Recevez dans un salon Discord un message quand un serveur démarre, s'arrête ou plante. Aucun bot nécessaire.", ibtn("plus", "Ajouter une notification", { class: "primary", onclick: () => add() })));
    const e = (await api("GET", "/api/events?limit=30")).events;
    clear(evs).append(e.length ? h("div", { class: "tablewrap" }, h("table", null, h("thead", null, h("tr", null, ["Date", "Serveur", "Événement"].map((t) => h("th", null, t)))),
      h("tbody", null, e.slice().reverse().map((x) => h("tr", null, h("td", { class: "small" }, fmtDate(x.ts)), h("td", null, x.server_name || "—"), h("td", null, badge(EVENT_BADGE[x.type] || "stopped", EVENT_LABELS[x.type] || x.type)))))))
      : h("p", { class: "muted" }, "Aucun événement enregistré pour l'instant."));
  };
  const add = (w) => {
    const name = h("input", { type: "text", maxlength: 60, placeholder: "Salon #serveurs", value: w ? w.name : "" }), url = h("input", { type: "url", placeholder: "https://discord.com/api/webhooks/…", autocomplete: "off", class: "mono" });
    const boxes = Object.keys(kinds).map((k) => ({ k, c: h("input", { type: "checkbox", checked: w ? (!w.kinds.length || w.kinds.includes(k)) : !["server.starting", "server.stopping", "log.critical"].includes(k) }) }));
    const sboxes = srvs.map((x) => ({ id: x.id, c: h("input", { type: "checkbox", checked: w ? (!w.server_ids.length || w.server_ids.includes(x.id)) : true }), n: x.name }));
    const cd = h("select", null, [[0, "Aucun"], [60, "1 minute"], [300, "5 minutes"], [900, "15 minutes"], [3600, "1 heure"]].map(([v, l]) => h("option", { value: v, selected: (w ? w.cooldown_s : 60) === v }, l)));
    modal(w ? "Modifier la notification" : "Nouvelle notification", h("div", { class: "stack" }, field("Nom", name), w ? null : field("Adresse du webhook", url, "Discord : paramètres du salon → Intégrations → Webhooks → Copier l'URL. Adresse en https uniquement ; elle ne sera plus affichée en entier."),
      h("div", null, h("label", null, "Événements à envoyer"), h("div", { class: "check-grid" }, boxes.map(({ k, c }) => h("label", { class: "inline" }, c, EVENT_LABELS[k] || k)))),
      h("div", null, h("label", null, "Serveurs concernés"), srvs.length ? h("div", { class: "check-grid" }, sboxes.map(({ c, n }) => h("label", { class: "inline" }, c, n))) : h("p", { class: "muted small" }, "Aucun serveur pour l'instant (tous seront concernés).")),
      field("Anti-spam", cd, "Délai minimal entre deux messages identiques (même événement, même serveur).")),
      [{ label: "Annuler", onclick: () => {} }, { label: w ? "Enregistrer" : "Ajouter", kind: "primary", onclick: async () => {
        const sel = boxes.filter((x) => x.c.checked).map((x) => x.k), ss = sboxes.filter((x) => x.c.checked).map((x) => x.id);
        const body = { name: name.value.trim(), kinds: sel.length === boxes.length ? [] : sel, server_ids: ss.length === sboxes.length ? [] : ss, cooldown_s: +cd.value };
        if (!sel.length) { toast("Choisissez au moins un événement.", "error"); return false; }
        try {
          if (w) await api("PATCH", `/api/webhooks/${w.id}`, body); else await api("POST", "/api/webhooks", { ...body, url: url.value.trim() });
          toast(w ? "Notification modifiée." : "Notification ajoutée : utilisez « Tester » pour vérifier.", "ok"); load();
        } catch (e) { fail(e); return false; }
      } }]);
  };
  main.append(pageHead("Notifications", "Messages automatiques vers Discord (ou tout service acceptant un webhook). Les notifications internes au panneau sont dans la cloche en haut à droite.", ibtn("plus", "Ajouter une notification", { class: "primary", onclick: () => add() })), list, h("h2", null, "Derniers événements"), evs);
  await load();
});

/* ======================================================================= supervision Debian (lecture seule) ======================================================================= */
addRoute(/^#\/supervision$/, async (main) => {
  const body = h("div"), logOut = h("pre", { class: "log" }, "Choisissez un journal puis « Afficher ».");
  const unit = h("select", { "aria-label": "Journal" }, [["minecraft-panel.service", "Panneau (minecraft-panel)"], ["minecraft-panel-backup.service", "Sauvegarde du panneau"], ["ssh.service", "SSH"]].map(([v, l]) => h("option", { value: v }, l)));
  const lines = h("select", { "aria-label": "Lignes" }, [50, 100, 200].map((n) => h("option", { value: n }, n + " lignes"))); lines.value = 100;
  const stateBadge = (a) => badge(a === "active" ? "running" : a === "failed" ? "error" : a === "activating" ? "starting" : "stopped", { active: "Actif", failed: "En échec", inactive: "Inactif", activating: "Démarrage" }[a] || a);
  const tile = (ic, t, v, sub, pct) => h("div", { class: "card tile" }, h("div", { class: "t" }, icon(ic), t), h("div", { class: "big-num" }, v), pct !== null ? bar(pct) : null, h("div", { class: "muted small" }, sub));
  const tbl = (head, rows) => h("div", { class: "tablewrap" }, h("table", null, h("thead", null, h("tr", null, head.map((t) => h("th", null, t)))), h("tbody", null, rows)));
  const render = (o) => {
    const y = o.system;
    clear(body).append(
      h("div", { class: "grid small" }, tile("cpu", "Processeur", y.cpu_percent.toFixed(0) + " %", `Charge ${y.load.join(" / ")}`, y.cpu_percent), tile("ram", "Mémoire", fmtMb(y.mem.used_mb), `${fmtMb(y.mem.available_mb)} disponibles`, y.mem.used_mb / y.mem.total_mb * 100),
        tile("disk", "Disque de données", y.disk_data.percent + " %", `${fmtBytes(y.disk_data.free)} libres`, y.disk_data.percent), tile("clock", "Machine allumée depuis", fmtDur(y.uptime), `Panneau : ${o.panel.rss_mb} Mo, Python ${o.panel.python}`, null)),
      h("h2", null, "Températures"),
      o.temperatures.length ? h("div", { class: "grid small" }, o.temperatures.map((t) => h("div", { class: "card tile" }, h("div", { class: "t" }, icon("thermo"), t.label), h("div", { class: "big-num" }, t.celsius.toFixed(0) + " °C"), bar(t.celsius)))) : h("p", { class: "muted" }, "Aucune sonde de température exposée par cette machine."),
      h("h2", null, "Réseau"),
      o.network.length ? tbl(["Interface", "État", "Adresses", "Réception", "Émission", "Total reçu / émis"], o.network.map((n) => h("tr", null, h("td", { class: "mono" }, n.name), h("td", null, h("span", { class: "badge plain " + (n.state === "up" ? "running" : "off") }, n.state || "?")), h("td", { class: "mono small" }, n.addresses.join(", ") || "—"), h("td", null, fmtBytes(n.rx_per_s) + "/s"), h("td", null, fmtBytes(n.tx_per_s) + "/s"), h("td", { class: "small" }, `${fmtBytes(n.rx_bytes)} / ${fmtBytes(n.tx_bytes)}`)))) : h("p", { class: "muted" }, "Aucune interface détectée."),
      h("h2", null, "Services"),
      o.services.length ? tbl(["Service", "État", "Détail", "Démarrage au boot"], o.services.map((s) => h("tr", null, h("td", { class: "mono" }, s.unit), h("td", null, stateBadge(s.active)), h("td", { class: "small" }, s.sub), h("td", { class: "small" }, s.enabled || "—")))) : h("p", { class: "muted" }, "Services non lisibles depuis le panneau."),
      h("h2", null, "Processus Minecraft"),
      o.processes.length ? tbl(["Serveur", "PID", "Mémoire", "Processeur", "Actif depuis"], o.processes.map((p) => h("tr", null, h("td", null, p.server), h("td", { class: "mono" }, p.pid), h("td", null, fmtMb(p.rss_mb)), h("td", null, p.cpu.toFixed(0) + " %"), h("td", null, fmtDur(p.uptime))))) : h("p", { class: "muted" }, "Aucun serveur Minecraft en cours d'exécution."));
  };
  const showLog = async () => {
    logOut.textContent = "Chargement…";
    try { const r = await api("GET", `/api/supervision/logs?unit=${enc(unit.value)}&lines=${lines.value}`); logOut.textContent = r.available ? r.lines.join("\n") || "(journal vide)" : r.hint; logOut.scrollTop = logOut.scrollHeight; } catch (e) { logOut.textContent = e.message; }
  };
  main.append(pageHead("Supervision Debian", "État de la machine en lecture seule : aucune action système n'est possible depuis cette page."), body,
    h("h2", null, "Journaux système"), h("div", { class: "row" }, unit, lines, ibtn("restart", "Afficher", { class: "primary", onclick: showLog })), logOut);
  const load = async () => { try { render(await api("GET", "/api/supervision")); } catch (e) { clear(body).append(h("div", { class: "alert critical" }, icon("alert"), h("div", null, e.message))); } };
  await load();
  const t = setInterval(load, 5000);
  return { destroy: () => clearInterval(t) };
});

/* JobRadar page logic.
 *
 * Everything runs client-side over one jobs.json. Tracking lives in
 * localStorage keyed by dedupe_key rather than by posting id, so that when a
 * company closes a requisition and reposts it under a new id, "applied on the
 * 4th" survives. Every storage call is wrapped: a private window throws on
 * access, and the page has to render correctly with nothing stored.
 */
"use strict";

const STORE_KEY = "jobradar.tracker.v1";
const SPONSOR_LABELS = {
  strong: "Sponsors often",
  says_yes: "Posting offers sponsorship",
  some: "Has sponsored",
  never_filed: "Never filed",
  unknown: "No filing record",
  explicit_no: "No sponsorship",
};
const STATUSES = ["interested", "applied", "interviewing", "offer", "rejected", "dismissed"];

let CARDS = [];
let META = {};
let VIEW = [];
let selected = -1;
let tracker = loadTracker();

const $ = (s) => document.querySelector(s);
const el = (t, c, txt) => { const n = document.createElement(t); if (c) n.className = c; if (txt != null) n.textContent = txt; return n; };

/* ---- storage ---- */
function loadTracker() {
  try { return JSON.parse(localStorage.getItem(STORE_KEY) || "{}"); }
  catch { return {}; }
}
function saveTracker() {
  try { localStorage.setItem(STORE_KEY, JSON.stringify(tracker)); }
  catch { banner("This browser is not saving your tracking. Private windows block storage."); }
  renderTrackCount();
}
function track(key, patch) {
  tracker[key] = Object.assign({}, tracker[key], patch, { updated: new Date().toISOString().slice(0, 10) });
  if (!tracker[key].status) delete tracker[key];
  saveTracker();
}

/* ---- filter state, mirrored into the URL hash so a view is bookmarkable ---- */
const state = {
  q: "", minscore: 55, age: "", sponsor: new Set(), source: new Set(),
  remoteonly: false, trackedonly: false, showclosed: false, showdropped: false,
};

function readHash() {
  const p = new URLSearchParams(location.hash.slice(1));
  if (p.has("q")) state.q = p.get("q");
  if (p.has("min")) state.minscore = +p.get("min");
  if (p.has("age")) state.age = p.get("age");
  if (p.has("sp")) state.sponsor = new Set(p.get("sp").split(",").filter(Boolean));
  if (p.has("src")) state.source = new Set(p.get("src").split(",").filter(Boolean));
  for (const k of ["remoteonly", "trackedonly", "showclosed", "showdropped"])
    if (p.has(k)) state[k] = p.get(k) === "1";
}
function writeHash() {
  const p = new URLSearchParams();
  if (state.q) p.set("q", state.q);
  if (state.minscore !== 55) p.set("min", state.minscore);
  if (state.age) p.set("age", state.age);
  if (state.sponsor.size) p.set("sp", [...state.sponsor].join(","));
  if (state.source.size) p.set("src", [...state.source].join(","));
  for (const k of ["remoteonly", "trackedonly", "showclosed", "showdropped"])
    if (state[k]) p.set(k, "1");
  history.replaceState(null, "", p.toString() ? "#" + p : location.pathname);
}

/* ---- filtering ---- */
function ageDays(card) {
  if (!card.posted_at) return null;
  return Math.floor((Date.now() - Date.parse(card.posted_at)) / 86400000);
}
function matches(card) {
  if (card.status === "dropped" && !state.showdropped) return false;
  if (card.status === "closed" && !state.showclosed) return false;
  if (card.status === "open" && card.score < state.minscore) return false;
  if (state.age) { const a = ageDays(card); if (a === null || a > +state.age) return false; }
  if (state.sponsor.size && !state.sponsor.has(card.sponsorship.signal)) return false;
  if (state.source.size && !state.source.has(card.source)) return false;
  if (state.remoteonly && !card.is_remote) return false;
  if (state.trackedonly && !tracker[card.dedupe_key]) return false;
  if (state.q) {
    const hay = (card.title + " " + card.company + " " + card.matched_skills.join(" ") + " " + card.locations.join(" ")).toLowerCase();
    if (!state.q.toLowerCase().split(/\s+/).every((t) => hay.includes(t))) return false;
  }
  return true;
}

/* ---- rendering ---- */
function render() {
  VIEW = CARDS.filter(matches);
  const list = $("#list");
  list.textContent = "";

  if (!VIEW.length) {
    const e = el("div", "empty muted");
    e.style.padding = "40px 20px";
    e.style.textAlign = "center";
    e.textContent = CARDS.length
      ? "Nothing matches these filters. Try lowering the minimum score."
      : "No data yet. Run the refresh workflow to fill this in.";
    list.appendChild(e);
  }

  const frag = document.createDocumentFragment();
  VIEW.forEach((card, i) => frag.appendChild(cardNode(card, i)));
  list.appendChild(frag);

  renderCounts();
  writeHash();
  if (selected >= VIEW.length) selected = VIEW.length - 1;
  if (selected >= 0) highlight();
}

function cardNode(card, i) {
  const node = el("article", "card" + (card.status !== "open" ? " is-dropped" : ""));
  node.dataset.i = i;

  const score = el("div", "score" + (card.score < 55 ? " low" : ""), card.score);
  node.appendChild(score);

  const main = el("div", "cardmain");
  main.appendChild(el("div", "cardtitle", card.title));

  const sub = el("div", "cardsub");
  sub.appendChild(el("span", null, card.company));
  sub.appendChild(el("span", null, card.locations[0] || (card.is_remote ? "Remote" : "Location not stated")));
  const a = ageDays(card);
  if (a !== null) sub.appendChild(el("span", null, a === 0 ? "today" : a + "d ago"));

  const sig = card.sponsorship.signal;
  sub.appendChild(el("span", "chip " + sig, SPONSOR_LABELS[sig] || sig));

  const t = tracker[card.dedupe_key];
  if (t && t.status) sub.appendChild(el("span", "chip " + (t.status === "applied" ? "applied" : "state"), t.status));
  if (card.status === "dropped") sub.appendChild(el("span", "chip state", "filtered: " + (card.drop_reason || "")));
  if (card.status === "closed") sub.appendChild(el("span", "chip state", "closed"));
  main.appendChild(sub);

  if (card.matched_skills.length)
    main.appendChild(el("div", "cardskills", card.matched_skills.slice(0, 9).join(" · ")));

  node.appendChild(main);
  node.addEventListener("click", () => select(i));
  return node;
}

function renderCounts() {
  const by = (fn) => CARDS.filter(fn).length;
  document.querySelectorAll("#sponsor .count").forEach((n) => {
    n.textContent = by((c) => c.status === "open" && c.sponsorship.signal === n.dataset.v);
  });
  document.querySelectorAll("#source .count").forEach((n) => {
    n.textContent = by((c) => c.status === "open" && c.source === n.dataset.v);
  });
  $("#dropcount").textContent = "(" + by((c) => c.status === "dropped") + ")";
}

function renderTrackCount() {
  const n = Object.keys(tracker).length;
  $("#trackcount").textContent = n + (n === 1 ? " tracked" : " tracked");
}

/* ---- detail ---- */
function select(i) {
  selected = i;
  highlight();
  renderDetail(VIEW[i]);
  const node = $(`.card[data-i="${i}"]`);
  if (node) node.scrollIntoView({ block: "nearest" });
}
function highlight() {
  document.querySelectorAll(".card.sel").forEach((n) => n.classList.remove("sel"));
  const node = $(`.card[data-i="${selected}"]`);
  if (node) node.classList.add("sel");
}

function renderDetail(card) {
  const d = $("#detail");
  d.textContent = "";
  if (!card) return;

  d.appendChild(el("h2", null, card.title));
  const sub = el("div", "dsub");
  sub.textContent = [card.company, card.locations.join(" · ") || (card.is_remote ? "Remote" : ""), card.source]
    .filter(Boolean).join("  ·  ");
  d.appendChild(sub);

  const apply = el("a", "applylink", "Open the posting");
  apply.href = card.url; apply.target = "_blank"; apply.rel = "noopener";
  d.appendChild(apply);

  if (card.also_at && card.also_at.length) {
    const also = el("p", "note");
    also.textContent = "Also posted on " + card.also_at.map((o) => o.source).join(", ") + ".";
    d.appendChild(also);
  }

  /* why this score */
  const why = section(d, "Why " + card.score);
  const table = el("table", "explain");
  card.explain.forEach((row) => {
    const tr = el("tr");
    tr.appendChild(el("td", "lbl", row.label));
    tr.appendChild(el("td", "det", row.detail));
    const pts = el("td", "pts" + (row.points < 0 ? " neg" : ""),
      (row.points > 0 ? "+" : "") + row.points + (row.max ? " / " + row.max : ""));
    tr.appendChild(pts);
    table.appendChild(tr);
  });
  why.appendChild(table);

  /* sponsorship */
  const sp = card.sponsorship;
  const spSec = section(d, "Sponsorship");
  const chip = el("span", "chip " + sp.signal, SPONSOR_LABELS[sp.signal] || sp.signal);
  spSec.appendChild(chip);
  spSec.appendChild(el("p", "note", sp.detail));
  if (sp.evidence) {
    const q = el("div", "evidence bad", "“" + sp.evidence.trim() + "”");
    spSec.appendChild(q);
  }
  if (sp.positive_evidence) {
    spSec.appendChild(el("p", "note", "Mixed signals. The posting also says:"));
    spSec.appendChild(el("div", "evidence good", "“" + sp.positive_evidence.trim() + "”"));
  }
  if (sp.match_method && sp.match_method !== "no-data" && sp.certified != null) {
    spSec.appendChild(el("p", "note",
      "Employer matched by " + sp.match_method + (sp.match_score ? " (" + sp.match_score + "%)" : "") + "."));
  }

  /* skills */
  if (card.matched_skills.length) {
    const s = section(d, "Skills in this posting");
    const tags = el("div", "taglist");
    card.matched_skills.forEach((k) => tags.appendChild(el("span", "tag", k)));
    s.appendChild(tags);
  }
  if (card.missing_skills.length) {
    const s = section(d, "Gaps to expect in a screen");
    const tags = el("div", "taglist");
    card.missing_skills.forEach((k) => tags.appendChild(el("span", "tag gap", k)));
    s.appendChild(tags);
  }

  /* tracking */
  const t = tracker[card.dedupe_key] || {};
  const ts = section(d, "Your status");
  const row = el("div", "statusrow");
  STATUSES.forEach((st) => {
    const b = el("button", t.status === st ? "on" : null, st);
    b.addEventListener("click", () => {
      track(card.dedupe_key, { status: t.status === st ? null : st, title: card.title, company: card.company, url: card.url });
      renderDetail(CARDS.find((c) => c.dedupe_key === card.dedupe_key));
      render();
    });
    row.appendChild(b);
  });
  ts.appendChild(row);

  const note = el("textarea");
  note.id = "note"; note.placeholder = "Notes, referrals, who you spoke to";
  note.value = t.note || "";
  note.addEventListener("change", () => track(card.dedupe_key, { note: note.value, status: t.status || "interested", title: card.title, company: card.company, url: card.url }));
  ts.appendChild(note);

  /* description */
  if (card.snippet) {
    const s = section(d, "From the posting");
    s.appendChild(el("div", "snippet", card.snippet + "…"));
  }
}

function section(parent, title) {
  const s = el("div", "dsec");
  s.appendChild(el("h3", null, title));
  parent.appendChild(s);
  return s;
}

function banner(msg) {
  const b = $("#banner");
  b.textContent = msg;
  b.hidden = false;
}

/* ---- controls ---- */
function buildChecks(id, values, labels) {
  const box = $("#" + id);
  values.forEach((v) => {
    const label = el("label");
    const cb = el("input"); cb.type = "checkbox"; cb.value = v;
    cb.checked = state[id].has(v);
    cb.addEventListener("change", () => {
      cb.checked ? state[id].add(v) : state[id].delete(v);
      render();
    });
    label.appendChild(cb);
    label.appendChild(el("span", null, labels ? labels[v] || v : v));
    const c = el("span", "count"); c.dataset.v = v;
    label.appendChild(c);
    box.appendChild(label);
  });
}

function wire() {
  $("#minscore").addEventListener("input", (e) => {
    state.minscore = +e.target.value;
    $("#minscoreout").textContent = state.minscore;
    render();
  });
  $("#search").addEventListener("input", (e) => { state.q = e.target.value; render(); });
  $("#age").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    state.age = b.dataset.v;
    document.querySelectorAll("#age button").forEach((n) => n.classList.toggle("on", n === b));
    render();
  });
  ["remoteonly", "trackedonly", "showclosed", "showdropped"].forEach((k) => {
    $("#" + k).addEventListener("change", (e) => { state[k] = e.target.checked; render(); });
  });
  $("#reset").addEventListener("click", () => { location.hash = ""; location.reload(); });

  $("#export").addEventListener("click", () => {
    const blob = new Blob([JSON.stringify(tracker, null, 1)], { type: "application/json" });
    const a = el("a");
    a.href = URL.createObjectURL(blob);
    a.download = "jobradar-tracker-" + new Date().toISOString().slice(0, 10) + ".json";
    a.click();
    URL.revokeObjectURL(a.href);
  });
  $("#importbtn").addEventListener("click", () => $("#importfile").click());
  $("#importfile").addEventListener("change", (e) => {
    const file = e.target.files[0]; if (!file) return;
    file.text().then((text) => {
      try {
        const incoming = JSON.parse(text);
        // Merge rather than replace: importing a laptop backup on a phone
        // should not wipe what the phone already knows.
        tracker = Object.assign({}, incoming, tracker);
        saveTracker(); render();
        banner("Imported " + Object.keys(incoming).length + " tracked roles.");
      } catch { banner("That file is not a JobRadar export."); }
    });
  });

  const help = $("#help");
  $("#helpclose").addEventListener("click", () => help.close());
  document.addEventListener("keydown", (e) => {
    // e.target is not guaranteed to be an Element. If it is not, calling
    // .matches on it throws and takes every keyboard shortcut down with it,
    // silently, for the rest of the session.
    const t = e.target;
    if (t && typeof t.matches === "function" && t.matches("input, textarea")) {
      if (e.key === "Escape") t.blur();
      return;
    }
    if (e.key === "j" || e.key === "ArrowDown") { e.preventDefault(); select(Math.min(selected + 1, VIEW.length - 1)); }
    else if (e.key === "k" || e.key === "ArrowUp") { e.preventDefault(); select(Math.max(selected - 1, 0)); }
    else if (e.key === "Enter" && VIEW[selected]) window.open(VIEW[selected].url, "_blank", "noopener");
    else if (e.key === "/") { e.preventDefault(); $("#search").focus(); }
    else if (e.key === "?") help.showModal();
    else if (e.key === "Escape") help.close();
    else if (VIEW[selected] && "asx".includes(e.key)) {
      const map = { a: "applied", s: "interested", x: "dismissed" };
      track(VIEW[selected].dedupe_key, {
        status: map[e.key], title: VIEW[selected].title,
        company: VIEW[selected].company, url: VIEW[selected].url,
      });
      renderDetail(VIEW[selected]); render();
    }
  });
}

/* ---- boot ---- */
async function boot() {
  readHash();
  $("#minscore").value = state.minscore;
  $("#minscoreout").textContent = state.minscore;
  $("#search").value = state.q;
  ["remoteonly", "trackedonly", "showclosed", "showdropped"].forEach((k) => { $("#" + k).checked = state[k]; });
  document.querySelectorAll("#age button").forEach((n) => n.classList.toggle("on", n.dataset.v === state.age));

  try {
    const [cards, meta] = await Promise.all([
      fetch("data/jobs.json").then((r) => r.json()),
      fetch("data/meta.json").then((r) => r.json()),
    ]);
    CARDS = cards; META = meta;
  } catch {
    banner("Could not load the job data. If this is a fresh checkout, run the refresh workflow first.");
    CARDS = []; META = {};
  }

  buildChecks("sponsor", ["strong", "says_yes", "some", "never_filed", "unknown", "explicit_no"], SPONSOR_LABELS);
  buildChecks("source", META.sources || []);
  wire();

  if (META.updated_at) {
    const mins = Math.floor((Date.now() - Date.parse(META.updated_at)) / 60000);
    const when = mins < 60 ? mins + "m ago"
      : mins < 1440 ? Math.floor(mins / 60) + "h ago"
      : Math.floor(mins / 1440) + "d ago";
    $("#updated").textContent = META.open + " open · updated " + when;
  }
  if (META.actions_url) $("#refresh").href = META.actions_url;
  if (META.has_sponsorship_data === false) {
    banner("Sponsorship history is not loaded yet, so every employer reads as “No filing record”. Run the sponsorship workflow once to fill it in.");
  }

  renderTrackCount();
  render();
  if (VIEW.length) select(0);
}
boot();

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
const ADDED_KEY = "jobradar.added.v1";
// The command the Tailor button copies. The page cannot fetch a posting itself
// (every ATS blocks cross-origin reads and there is no server to proxy through)
// so the link is carried to the local CLI, which does the work.
const TAILOR_CMD = "python -m jobradar tailor brief --url ";

// The local helper. The page cannot reach your disk or a Claude session, so a
// button that triggers real work has to post to something listening locally.
// If it is not running, everything below stays hidden and the copy-command
// button is the whole experience, which is also the phone experience.
const HELPER = "http://127.0.0.1:8777";
let HELPER_UP = false;
// Keyed by dedupe_key. renderDetail rebuilds the pane from scratch on every
// selection and every status change, so anything held in a closure is lost.
const QUEUE_STATUS = {};
let POLL = null;
const SPONSOR_LABELS = {
  strong: "Sponsors often",
  says_yes: "Posting offers sponsorship",
  some: "Has sponsored",
  never_filed: "Never filed",
  unknown: "No filing record",
  explicit_no: "No sponsorship",
};
const STATUSES = ["interested", "applied", "interviewing", "offer", "rejected", "dismissed"];
// Where you are with the role overall, as opposed to the application stage.
// Pending is the default and is never stored: absence means pending.
const PROCESS = ["pending", "complete", "dismissed"];

let CARDS = [];
let ADDED = [];           // jobs pasted in by hand, stored in this browser only
let FILTERED = null;      // fetched lazily; see loadFiltered
let META = {};
let VIEW = [];
let selected = -1;
let tracker = loadTracker();

const $ = (s) => document.querySelector(s);
const el = (t, c, txt) => { const n = document.createElement(t); if (c) n.className = c; if (txt != null) n.textContent = txt; return n; };

/* ---- storage ---- */
function loadTracker() {
  let raw;
  try { raw = JSON.parse(localStorage.getItem(STORE_KEY) || "{}"); }
  catch { return {}; }
  // Drop entries a bug wrote under a missing key. They render as a phantom row
  // with a dropdown and no job attached to it, and they cannot be cleared from
  // the page because there is nothing to click.
  let healed = false;
  for (const key of Object.keys(raw)) {
    const v = raw[key] || {};
    const empty = !v.company && !v.title && !v.url && !v.status;
    if (key === "undefined" || key === "null" || key === "" || empty) {
      delete raw[key];
      healed = true;
    }
  }
  if (healed) {
    try { localStorage.setItem(STORE_KEY, JSON.stringify(raw)); } catch { /* ignore */ }
  }
  return raw;
}
function saveTracker() {
  try { localStorage.setItem(STORE_KEY, JSON.stringify(tracker)); }
  catch { banner("This browser is not saving your tracking. Private windows block storage."); }
  renderTrackCount();
}
function loadAdded() {
  try { return JSON.parse(localStorage.getItem(ADDED_KEY) || "[]"); }
  catch { return []; }
}
function saveAdded() {
  try { localStorage.setItem(ADDED_KEY, JSON.stringify(ADDED)); }
  catch { banner("This browser is not saving added links."); }
}

/* A pasted link becomes a card immediately, before the CLI has seen it. It has
   no score or sponsorship yet because working those out means fetching the
   posting, which only the local command can do. */
function addedCard(url) {
  let host = url, path = "";
  try { const u = new URL(url); host = u.hostname.replace(/^(www|jobs|job-boards|boards|apply|careers)\./, ""); path = u.pathname; }
  catch { return null; }
  const company = host.split(".")[0];
  const bits = path.split("/").filter(Boolean);
  const guess = bits.length > 1 ? bits[0] : company;
  return {
    id: "added:" + url,
    dedupe_key: "added:" + url,
    company: guess.charAt(0).toUpperCase() + guess.slice(1),
    title: "Pasted link",
    url: url,
    source: "link",
    locations: [],
    is_remote: false,
    score: null,
    matched_skills: [],
    missing_skills: [],
    sponsorship: null,
    status: "open",
    drop_reason: null,
    added: true,
    posted_at: new Date().toISOString(),
  };
}

function track(key, patch) {
  tracker[key] = Object.assign({}, tracker[key], patch, { updated: new Date().toISOString().slice(0, 10) });
  // Clearing the status used to delete the whole entry, which would silently
  // discard a process you had set on a row you never marked interested.
  if (!tracker[key].status && !tracker[key].process) delete tracker[key];
  saveTracker();
}

async function probeHelper() {
  // Silent on failure: most of the time you are on a phone with no helper
  // running, and an error banner for the expected case is just noise.
  try {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 1500);
    const r = await fetch(HELPER + "/api/health", { signal: ctrl.signal });
    clearTimeout(timer);
    HELPER_UP = r.ok;
  } catch { HELPER_UP = false; }
  // Chrome refuses a cross-origin call from this HTTPS page to the helper on
  // loopback. Private Network Access is enforced in current Chrome and the
  // preflight headers are not enough. So "off" here does not mean the helper
  // is not running, it means it cannot be reached FROM THIS URL, and telling
  // someone to start a helper they already started is the wrong advice.
  const local = location.hostname === "localhost" || location.hostname === "127.0.0.1";
  const dot = $("#helper");
  if (dot) {
    if (HELPER_UP) {
      dot.textContent = "helper on";
      dot.title = "The local helper is running, so Tailor works from this page.";
    } else if (local) {
      dot.textContent = "helper off";
      dot.title = "Run `python -m jobradar serve` to enable the Tailor button.";
    } else {
      dot.textContent = "tailor on localhost";
      dot.title = "Chrome will not let this page reach a local helper. "
        + "Open http://localhost:8777 for the same page with a working Tailor button.";
    }
    dot.className = "helperdot" + (HELPER_UP ? " on" : local ? "" : " elsewhere");
    dot.onclick = HELPER_UP || local ? null : () => {
      window.open("http://localhost:8777" + location.hash, "_blank", "noopener");
    };
    dot.style.cursor = HELPER_UP || local ? "" : "pointer";
  }
}

async function queueTailor(card) {
  const r = await fetch(HELPER + "/api/tailor", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url: card.url, company: card.company,
                           title: card.title, dedupe_key: card.dedupe_key }),
  });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || r.status);
  const entry = await r.json();
  QUEUE_STATUS[card.dedupe_key] = entry;
  return entry;
}

function pollQueue(card) {
  if (POLL) clearInterval(POLL);
  POLL = setInterval(async () => {
    // A backgrounded tab should cost nothing; the next foreground tick catches up.
    if (document.hidden) return;
    const entry = QUEUE_STATUS[card.dedupe_key];
    if (!entry) return;
    try {
      const r = await fetch(HELPER + "/api/queue/" + entry.slug);
      if (!r.ok) return;
      const fresh = await r.json();
      QUEUE_STATUS[card.dedupe_key] = fresh;
      paintQueueStatus(card);
      if (fresh.status === "ready" || fresh.status === "failed") {
        clearInterval(POLL); POLL = null;
        render();
      }
    } catch { /* helper went away; leave the last state on screen */ }
  }, 2000);
}

const RUN_WORDS = {
  queued: "queued", in_progress: "running", completed: "done",
};

/* With the helper running, Refresh starts the workflow instead of sending you
   to the Actions tab to press a second button. The helper can do it because
   `gh` is already authenticated on this machine, so no token ever reaches the
   browser. Without the helper the link is the only option and stays. */
async function startRefresh(btn) {
  const original = btn.textContent;
  btn.textContent = "Starting…";
  try {
    const r = await fetch(HELPER + "/api/refresh", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(body.error || r.status);
    btn.textContent = "Running…";
    pollRefresh(btn, original);
  } catch (e) {
    btn.textContent = original;
    banner("Could not start the refresh: " + e.message);
  }
}

function pollRefresh(btn, original) {
  const tick = setInterval(async () => {
    if (document.hidden) return;
    try {
      const r = await fetch(HELPER + "/api/refresh");
      const s = await r.json();
      if (s.status === "completed") {
        clearInterval(tick);
        btn.textContent = s.conclusion === "success" ? "Reload for new jobs" : "Refresh failed";
        if (s.conclusion === "success") {
          btn.onclick = (e) => { e.preventDefault(); location.reload(); };
        }
      } else {
        btn.textContent = (RUN_WORDS[s.status] || s.status) + "…";
      }
    } catch { clearInterval(tick); btn.textContent = original; }
  }, 15000);
}

const QUEUE_WORDS = {
  queued: "Queued. Fetching the posting.",
  briefed: "Posting fetched. Waiting for Claude to write it.",
  writing: "Claude is writing the resume.",
  ready: "Ready.",
  failed: "Failed.",
};

function paintQueueStatus(card) {
  const line = $("#queuestatus");
  if (!line) return;
  const entry = QUEUE_STATUS[card.dedupe_key];
  if (!entry) { line.textContent = ""; return; }
  let text = QUEUE_WORDS[entry.status] || entry.status;
  if (entry.status === "ready" && entry.pdf) text += " " + entry.pdf.split("/").pop();
  if (entry.status === "failed" && entry.error) text += " " + entry.error;
  line.textContent = text;
  line.className = "note queue " + entry.status;
}

/* ---- filter state, mirrored into the URL hash so a view is bookmarkable ---- */
const state = {
  // Three days by default. Anything older has usually been seen by hundreds of
  // applicants already, and the whole point of running this daily is being
  // early. Everything older is still stored and one click away.
  q: "", minscore: 55, age: "3", sponsor: new Set(), source: new Set(),
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
  if (state.age !== "3") p.set("age", state.age);
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
  // An added link has no score until the local command fetches the posting, so
  // the score slider must not hide it. Filtering out the thing you just pasted
  // reads as the Add button being broken.
  if (card.status === "open" && !card.added && card.score < state.minscore) return false;
  if (state.age && !card.added) { const a = ageDays(card); if (a === null || a > +state.age) return false; }
  if (state.sponsor.size && !card.added && !state.sponsor.has((card.sponsorship || {}).signal)) return false;
  if (state.source.size && !card.added && !state.source.has(card.source)) return false;
  if (state.remoteonly && !card.is_remote) return false;
  if (state.trackedonly && !tracker[card.dedupe_key]) return false;
  if (state.q) {
    const hay = [card.title, card.company, (card.matched_skills || []).join(" "),
                 (card.locations || []).join(" ")].join(" ").toLowerCase();
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
    if (!CARDS.length) {
      e.textContent = "No data yet. Run the refresh workflow to fill this in.";
    } else if (state.age) {
      const wider = CARDS.filter((c) => c.status === "open" && c.score >= state.minscore).length;
      e.textContent = "Nothing posted in the last " + state.age + " days. "
        + (wider ? wider + " older roles are still here: widen Posted within, or Refresh for new ones."
                 : "Try lowering the minimum score.");
    } else {
      e.textContent = "Nothing matches these filters. Try lowering the minimum score.";
    }
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

  const score = el("div", "score" + (card.score == null || card.score < 55 ? " low" : ""),
                   card.score == null ? "+" : card.score);
  node.appendChild(score);

  const main = el("div", "cardmain");
  main.appendChild(el("div", "cardtitle", card.title));

  const sub = el("div", "cardsub");
  sub.appendChild(el("span", null, card.company));
  sub.appendChild(el("span", null, card.locations[0] || (card.is_remote ? "Remote" : "Location not stated")));
  const a = ageDays(card);
  if (a !== null) sub.appendChild(el("span", null, a === 0 ? "today" : a + "d ago"));

  if (card.sponsorship) sub.appendChild(sponsorChip(card.sponsorship));
  if (card.added) sub.appendChild(el("span", "chip mine", "added by you"));
  const qs = QUEUE_STATUS[card.dedupe_key];
  if (qs) sub.appendChild(el("span", "chip q-" + qs.status, "resume " + qs.status));

  const t = tracker[card.dedupe_key];
  if (t && t.status) sub.appendChild(el("span", "chip " + (t.status === "applied" ? "applied" : "state"), t.status));
  if (card.status === "dropped") sub.appendChild(el("span", "chip state", "filtered: " + (card.drop_reason || "")));
  if (card.status === "closed") sub.appendChild(el("span", "chip state", "closed"));
  main.appendChild(sub);

  if (card.matched_skills && card.matched_skills.length)
    main.appendChild(el("div", "cardskills", card.matched_skills.slice(0, 9).join(" · ")));

  node.appendChild(main);
  node.addEventListener("click", () => select(i));
  return node;
}

function sponsorChip(sp) {
  let text = SPONSOR_LABELS[sp.signal] || sp.signal;
  // Depth matters more than the tier once a company clearly sponsors at all.
  if ((sp.signal === "strong" || sp.signal === "some") && sp.analyst_certified != null) {
    text += " · " + sp.analyst_certified + " analyst";
  }
  const chip = el("span", "chip " + sp.signal, text);
  if (sp.certified != null) {
    chip.title = sp.certified.toLocaleString() + " certified H-1B filings, "
      + sp.analyst_certified + " in analyst occupations"
      + (sp.match_method ? " (matched " + sp.match_method + ")" : "");
  }
  return chip;
}

function renderCounts() {
  const by = (fn) => CARDS.filter(fn).length;
  document.querySelectorAll("#sponsor .count").forEach((n) => {
    n.textContent = by((c) => c.status === "open" && (c.sponsorship || {}).signal === n.dataset.v);
  });
  document.querySelectorAll("#source .count").forEach((n) => {
    n.textContent = by((c) => c.status === "open" && c.source === n.dataset.v);
  });
  const dropped = FILTERED === null ? (META.dropped || 0)
                                    : by((c) => c.status === "dropped");
  $("#dropcount").textContent = "(" + dropped + ")";
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
  sub.textContent = [card.company, (card.locations || []).join(" · ") || (card.is_remote ? "Remote" : ""), card.source]
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

  if (card.drop_reason) {
    const w = section(d, "Filtered out");
    w.appendChild(el("p", "note", "Reason: " + card.drop_reason
      + ". Filtered roles are kept so the filters can be audited, not hidden."));
  }

  /* why this score */
  const why = card.explain ? section(d, "Why " + card.score) : null;
  const table = el("table", "explain");
  (card.explain || []).forEach((row) => {
    const tr = el("tr");
    tr.appendChild(el("td", "lbl", row.label));
    tr.appendChild(el("td", "det", row.detail));
    const pts = el("td", "pts" + (row.points < 0 ? " neg" : ""),
      (row.points > 0 ? "+" : "") + row.points + (row.max ? " / " + row.max : ""));
    tr.appendChild(pts);
    table.appendChild(tr);
  });
  if (why) why.appendChild(table);

  /* sponsorship */
  const sp = card.sponsorship || {};
  const spSec = section(d, "Sponsorship");
  if (sp.signal) spSec.appendChild(sponsorChip(sp));
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
  if (card.matched_skills && card.matched_skills.length) {
    const s = section(d, "Skills in this posting");
    const tags = el("div", "taglist");
    card.matched_skills.forEach((k) => tags.appendChild(el("span", "tag", k)));
    s.appendChild(tags);
  }
  if (card.missing_skills && card.missing_skills.length) {
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

  // The command that builds a resume for this posting. Copying it is the whole
  // bridge between a page that cannot reach your disk and a CLI that can.
  const tailor = section(d, "Tailor a resume");
  const cmd = TAILOR_CMD + card.url;
  const copy = el("button", "btn", "Copy the tailor command");
  const shown = el("input", "cmdbox");
  shown.value = cmd; shown.readOnly = true; shown.hidden = true;
  copy.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(cmd);
      copy.textContent = "Copied. Paste it in your terminal.";
    } catch {
      // clipboard needs a secure context; file:// and old browsers fall back.
      shown.hidden = false; shown.focus(); shown.select();
      copy.textContent = "Select and copy:";
    }
    if (!t.status) {
      track(card.dedupe_key, { status: "interested", title: card.title,
                               company: card.company, url: card.url });
      render();
    }
  });
  if (HELPER_UP) {
    const go = el("button", "btn primary", "Tailor this resume");
    go.addEventListener("click", async () => {
      go.disabled = true;
      go.textContent = "Queueing…";
      try {
        await queueTailor(card);
        go.textContent = "Queued";
        if (!t.status) {
          track(card.dedupe_key, { status: "interested", title: card.title,
                                   company: card.company, url: card.url });
        }
        paintQueueStatus(card);
        pollQueue(card);
        render();
      } catch (e) {
        go.disabled = false;
        go.textContent = "Tailor this resume";
        const line = $("#queuestatus");
        if (line) line.textContent = "Could not queue it: " + e.message;
      }
    });
    tailor.appendChild(go);
  }
  const status = el("p", "note queue");
  status.id = "queuestatus";
  tailor.appendChild(status);

  if (!HELPER_UP && location.hostname !== "localhost"
      && location.hostname !== "127.0.0.1") {
    const hint = el("p", "note");
    const a = el("a", null, "http://localhost:8777");
    a.href = "http://localhost:8777" + location.hash;
    a.target = "_blank"; a.rel = "noopener";
    hint.appendChild(document.createTextNode("Chrome will not let this page reach a "
      + "local helper. For a one-click Tailor button, open "));
    hint.appendChild(a);
    hint.appendChild(document.createTextNode(" instead. Same page, same data."));
    tailor.appendChild(hint);
  }

  tailor.appendChild(copy);
  tailor.appendChild(shown);
  if (card.added) {
    tailor.appendChild(el("p", "note",
      "Added by you, so it has no score yet. The command above fetches the posting, "
      + "scores it and writes the brief."));
  }

  if (card.added) {
    const remove = el("button", "linkbtn", "Remove this link");
    remove.style.marginTop = "8px";
    remove.addEventListener("click", () => {
      ADDED = ADDED.filter((a) => a.url !== card.url);
      saveAdded();
      CARDS = CARDS.filter((c) => c.id !== card.id);
      $("#detail").textContent = "";
      render();
    });
    tailor.appendChild(remove);
  }

  const note = el("textarea");
  note.id = "note"; note.placeholder = "Notes, referrals, who you spoke to";
  note.value = t.note || "";
  note.addEventListener("change", () => track(card.dedupe_key, { note: note.value, status: t.status || "interested", title: card.title, company: card.company, url: card.url }));
  ts.appendChild(note);

  /* description */
  paintQueueStatus(card);

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
  ["remoteonly", "trackedonly"].forEach((k) => {
    $("#" + k).addEventListener("change", (e) => { state[k] = e.target.checked; render(); });
  });
  // Filtered and closed cards live in a second file, fetched the first time
  // somebody asks to see them. They outnumber open roles about five to one.
  ["showclosed", "showdropped"].forEach((k) => {
    $("#" + k).addEventListener("change", async (e) => {
      state[k] = e.target.checked;
      if (state[k]) await loadFiltered();
      render();
    });
  });
  $("#addform").addEventListener("submit", (e) => {
    e.preventDefault();
    const input = $("#addurl");
    const url = input.value.trim();
    if (!url) return;
    if (!/^https?:\/\/.+\..+/.test(url)) {
      $("#addnote").textContent = "That does not look like a link. Paste the full URL.";
      return;
    }
    if (ADDED.some((a) => a.url === url) || CARDS.some((c) => c.url === url)) {
      $("#addnote").textContent = "Already on the list.";
      input.value = "";
      return;
    }
    const card = addedCard(url);
    if (!card) { $("#addnote").textContent = "Could not read that link."; return; }
    ADDED.unshift(card);
    saveAdded();
    CARDS = ADDED.concat(CARDS.filter((c) => !c.added));
    input.value = "";
    $("#addnote").textContent = "Added. Open it and copy the tailor command.";
    render();
    const i = VIEW.findIndex((c) => c.url === url);
    if (i >= 0) select(i);
  });

  $("#viewjobs").addEventListener("click", () => showView("jobs"));
  $("#viewtracker").addEventListener("click", () => showView("tracker"));
  document.querySelectorAll("#trackertable th[data-sort]").forEach((th) => {
    th.addEventListener("click", () => {
      const key = th.dataset.sort;
      TRACK_SORT = { key, dir: TRACK_SORT.key === key ? -TRACK_SORT.dir : 1 };
      renderTracker();
    });
  });
  $("#trackercsv").addEventListener("click", () => {
    const blob = new Blob([trackerCsv()], { type: "text/csv" });
    const a = el("a");
    a.href = URL.createObjectURL(blob);
    a.download = "jobradar-tracker-" + new Date().toISOString().slice(0, 10) + ".csv";
    a.click();
    URL.revokeObjectURL(a.href);
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

async function loadFiltered() {
  if (FILTERED !== null) return;
  try {
    FILTERED = await fetch("data/filtered.json", { cache: "no-cache" }).then((r) => r.json());
    CARDS = CARDS.concat(FILTERED);
  } catch {
    FILTERED = [];
    banner("Could not load the filtered roles.");
  }
}

/* ---- tracker ----
   One row per job you have taken an interest in, merged from two places that
   each know half the story. localStorage knows what you marked; the helper
   knows which resumes actually exist on disk. Neither alone is the tracker.

   City and state come from location_primary, which the pipeline already stores
   as "city|ST", so they are split rather than re-parsed. */

let QUEUE_ALL = {};        // slug -> entry, from the helper
let TRACK_SORT = { key: "company", dir: 1 };

async function loadQueueAll() {
  if (!HELPER_UP) return;
  try {
    const r = await fetch(HELPER + "/api/queue");
    if (!r.ok) return;
    const body = await r.json();
    QUEUE_ALL = {};
    for (const e of body.entries || []) QUEUE_ALL[e.slug] = e;
  } catch { /* helper went away; the browser half still renders */ }
}

function splitPlace(card) {
  const lp = (card && card.location_primary) || "";
  if (lp.includes("|")) {
    const [city, state] = lp.split("|");
    return { city: title_case(city), state: state.toUpperCase() };
  }
  if (lp.startsWith("remote-")) {
    const tail = lp.slice(7);
    return { city: "Remote", state: tail === "us" ? "" : tail.toUpperCase() };
  }
  if (lp && lp !== "unknown") return { city: title_case(lp), state: "" };
  const first = (card && card.locations && card.locations[0]) || "";
  return { city: first || "", state: "" };
}

function title_case(s) {
  return String(s).replace(/\b[a-z]/g, (m) => m.toUpperCase());
}

/* "Resume" is deliberately about the artifact, not the application: produced
   when a PDF exists, skipped when you decided against it, and blank when
   neither has happened yet. */
function resumeState(card, entry) {
  if (entry) {
    if (entry.status === "ready") return "produced";
    if (entry.status === "failed") {
      return /skipped/i.test(entry.error || "") ? "skipped" : "failed";
    }
    return entry.status;
  }
  const t = tracker[card ? card.dedupe_key : ""] || {};
  if (t.status === "dismissed") return "skipped";
  return "";
}

const PLACEHOLDERS = /^(pasted link|role|link|untitled)$/i;

function pickName(...candidates) {
  for (const c of candidates) {
    if (c && !PLACEHOLDERS.test(String(c).trim())) return c;
  }
  return candidates.find((c) => c) || "";
}

function trackerRows() {
  const byKey = {};
  for (const c of CARDS) byKey[c.dedupe_key] = c;
  const byUrl = {};
  for (const c of CARDS) byUrl[c.url] = c;

  const rows = {};
  // Everything you marked in this browser.
  for (const [key, t] of Object.entries(tracker)) {
    const card = byKey[key];
    const place = splitPlace(card);
    rows[key] = {
      key: key,
      company: (card && card.company) || t.company || "",
      title: (card && card.title) || t.title || "",
      city: place.city, state: place.state,
      url: (card && card.url) || t.url || "",
      status: t.status || "", resume: resumeState(card, null),
      process: t.process || "pending",
      updated: t.updated || "",
    };
  }
  // Everything the helper has a queue entry for, which is the authority on
  // whether a resume exists.
  for (const entry of Object.values(QUEUE_ALL)) {
    const card = byUrl[entry.url];
    // Explicit, because `a || b ? x : y` parses as `(a || b) ? x : y` and the
    // previous one-liner only looked like it said what it meant.
    const key = entry.dedupe_key || (card && card.dedupe_key) || entry.slug;
    const place = splitPlace(card);
    const existing = rows[key] || {};
    rows[key] = {
      // `key` and `process` are load-bearing and were missing here, which is
      // what produced a phantom row: the dropdown wrote to tracker[undefined],
      // and every real row reported an undefined process that the summary then
      // counted as neither pending nor complete.
      key: key,
      process: existing.process || (tracker[key] || {}).process || "pending",
      // A pasted link's card carries the placeholder "Pasted link" until a
      // refresh picks the posting up properly, so the helper's title, which
      // came from the posting itself, wins over it.
      company: pickName(existing.company, card && card.company, entry.company),
      title: pickName(existing.title, card && card.title, entry.title),
      city: existing.city || place.city, state: existing.state || place.state,
      url: existing.url || entry.url || "",
      status: existing.status || "",
      resume: resumeState(card, entry),
      pdf: entry.pdf || "",
      updated: existing.updated || (entry.updated_at || "").slice(0, 10),
    };
  }
  return Object.values(rows);
}

function renderTracker() {
  const rows = trackerRows();
  const dir = TRACK_SORT.dir, key = TRACK_SORT.key;
  rows.sort((a, b) => String(a[key] || "").localeCompare(String(b[key] || "")) * dir);

  const body = $("#trackertable tbody");
  body.textContent = "";
  for (const r of rows) {
    const tr = el("tr");
    tr.appendChild(el("td", "co", r.company));
    tr.appendChild(el("td", null, r.title));
    tr.appendChild(el("td", null, r.city));
    tr.appendChild(el("td", "st", r.state));
    const res = el("td");
    if (r.resume) res.appendChild(el("span", "chip q-" + (r.resume === "produced" ? "ready" : r.resume), r.resume));
    tr.appendChild(res);
    const st = el("td");
    if (r.status) st.appendChild(el("span", "chip " + (r.status === "applied" ? "applied" : "state"), r.status));
    tr.appendChild(st);
    const proc = el("td");
    const sel = el("select", "procsel");
    for (const value of PROCESS) {
      const opt = el("option", null, value);
      opt.value = value;
      if (value === r.process) opt.selected = true;
      sel.appendChild(opt);
    }
    sel.className = "procsel p-" + r.process;
    if (!r.key) {
      // Never write to an undefined key again. A row with no key is a bug
      // upstream, and it should be visible rather than silently persisted.
      sel.disabled = true;
      sel.title = "This row has no stable key, so a process cannot be saved against it.";
    }
    sel.addEventListener("change", () => {
      if (!r.key) return;
      // "pending" is the default, so it is stored as absence rather than as a
      // value. That keeps an untouched row out of the tracker entirely.
      track(r.key, { process: sel.value === "pending" ? null : sel.value });
      renderTracker();
    });
    proc.appendChild(sel);
    tr.appendChild(proc);

    const link = el("td");
    if (r.url) {
      const a = el("a", null, "open");
      a.href = r.url; a.target = "_blank"; a.rel = "noopener";
      link.appendChild(a);
    }
    tr.appendChild(link);
    body.appendChild(tr);
  }
  const produced = rows.filter((r) => r.resume === "produced").length;
  const done = rows.filter((r) => r.process === "complete").length;
  const pending = rows.filter((r) => r.process === "pending").length;
  $("#trackersummary").textContent =
    `${rows.length} tracked · ${produced} resume${produced === 1 ? "" : "s"} produced`
    + ` · ${done} complete · ${pending} pending`;
  $("#trackerempty").hidden = rows.length > 0;
  $("#trackertable").hidden = rows.length === 0;
  document.querySelectorAll("#trackertable th").forEach((th) => {
    th.classList.toggle("sorted", th.dataset.sort === key);
  });
}

function trackerCsv() {
  const rows = trackerRows();
  const head = ["Company", "Job Title", "City", "State", "Resume", "Status",
                "Process", "Link"];
  const esc = (v) => '"' + String(v == null ? "" : v).replace(/"/g, '""') + '"';
  const lines = [head.map(esc).join(",")];
  for (const r of rows) {
    lines.push([r.company, r.title, r.city, r.state, r.resume, r.status,
                r.process, r.url].map(esc).join(","));
  }
  return lines.join("\n");
}

async function showView(which) {
  const jobs = which === "jobs";
  $("#viewjobs").classList.toggle("on", jobs);
  $("#viewtracker").classList.toggle("on", !jobs);
  $("#list").hidden = !jobs;
  $("#detail").hidden = !jobs;
  $("#rail").hidden = !jobs;
  $("#tracker").hidden = jobs;
  if (!jobs) {
    await loadQueueAll();
    renderTracker();
  }
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
    // GitHub Pages serves these with cache-control: max-age=600, so a plain
    // reload inside ten minutes shows the browser's stale copy without ever
    // asking the server. On a page whose whole job is displaying data that a
    // workflow just rewrote, that is a bug: the reader refreshes, sees the old
    // counts and the "no sponsorship data" banner, and concludes the refresh
    // did nothing. `cache: "no-cache"` forces a conditional request, so an
    // unchanged file still costs only a 304.
    const opts = { cache: "no-cache" };
    const [cards, meta] = await Promise.all([
      fetch("data/jobs.json", opts).then((r) => r.json()),
      fetch("data/meta.json", opts).then((r) => r.json()),
    ]);
    CARDS = cards; META = meta;
  } catch {
    banner("Could not load the job data. If this is a fresh checkout, run the refresh workflow first.");
    CARDS = []; META = {};
  }

  ADDED = loadAdded();
  CARDS = ADDED.concat(CARDS);

  await probeHelper();

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
  const refreshBtn = $("#refresh");
  if (META.actions_url) refreshBtn.href = META.actions_url;
  if (HELPER_UP) {
    // Trigger it here rather than handing off to the Actions tab.
    refreshBtn.removeAttribute("target");
    refreshBtn.href = "#";
    refreshBtn.title = "Start the refresh workflow now";
    refreshBtn.addEventListener("click", (e) => {
      e.preventDefault();
      startRefresh(refreshBtn);
    });
  } else {
    refreshBtn.title = "Opens the Actions tab, where one more click runs it";
  }
  if (META.has_sponsorship_data === false) {
    banner("Sponsorship history is not loaded yet, so every employer reads as “No filing record”. Run the sponsorship workflow once to fill it in.");
  }

  renderTrackCount();
  // A bookmarked URL can arrive with the filtered view already on.
  if (state.showdropped || state.showclosed) await loadFiltered();
  render();
  if (VIEW.length) select(0);
}
boot();

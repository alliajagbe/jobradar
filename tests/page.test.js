const fs = require("fs"), path = require("path");
const { JSDOM } = require("jsdom");
const ROOT = "/Users/alli/dev/jobCollections/jobradar/docs";

const cards    = JSON.parse(fs.readFileSync(path.join(ROOT, "data/jobs.json")));
const filtered = JSON.parse(fs.readFileSync(path.join(ROOT, "data/filtered.json")));
const meta     = JSON.parse(fs.readFileSync(path.join(ROOT, "data/meta.json")));

const dom = new JSDOM(fs.readFileSync(path.join(ROOT, "index.html"), "utf8"), {
  runScripts: "outside-only", url: "https://alliajagbe.github.io/jobradar/",
  pretendToBeVisual: true,
});
const w = dom.window;
let fetched = [];
// Flip to true to simulate `jobradar serve` running.
let helperUp = process.env.HELPER_UP === "1";
w.fetch = (u, opts) => {
  fetched.push(u);
  if (u.includes("/api/health")) {
    return helperUp ? Promise.resolve({ ok: true, json: () => Promise.resolve({ok:true}) })
                    : Promise.reject(new Error("ECONNREFUSED"));
  }
  if (u.includes("/api/tailor")) {
    return Promise.resolve({ ok: true, json: () => Promise.resolve(
      {slug: "AcmeAnalyst", status: "queued", url: "x", dedupe_key: "k"}) });
  }
  if (u.endsWith("/api/queue")) {
    return Promise.resolve({ ok: true, json: () => Promise.resolve({entries: [
      {slug:"AcmeAnalyst", status:"ready", url:"https://job-boards.greenhouse.io/acme/jobs/123456",
       company:"Acme", title:"Data Analyst", dedupe_key:"added:https://job-boards.greenhouse.io/acme/jobs/123456",
       pdf:"/x/AlliAjagbeAcmeAnalystResume.pdf", updated_at:"2026-09-17T00:00:00Z"},
      {slug:"SkipCo", status:"failed", error:"skipped by Alli: not a fit",
       company:"SkipCo", title:"Senior Analyst", url:"https://example.com/skip", dedupe_key:"skipco"},
    ]}) });
  }
  if (u.includes("/api/queue/")) {
    return Promise.resolve({ ok: true, json: () => Promise.resolve(
      {slug: "AcmeAnalyst", status: "ready", pdf: "/x/AlliAjagbeAcmeAnalystResume.pdf"}) });
  }
  const body = u.includes("meta") ? meta : u.includes("filtered") ? filtered : cards;
  return Promise.resolve({ json: () => Promise.resolve(body) });
};
w.AbortController = class { constructor(){ this.signal = {}; } abort(){} };
const store = {};
Object.defineProperty(w, "localStorage", { value: {
  getItem: k => store[k] ?? null, setItem: (k,v) => { store[k]=String(v); },
  removeItem: k => { delete store[k]; }, clear: () => {},
}});
w.HTMLDialogElement.prototype.showModal = function(){ this.open = true; };
w.HTMLDialogElement.prototype.close = function(){ this.open = false; };
w.URL.createObjectURL = () => "blob:x"; w.URL.revokeObjectURL = () => {};
// jsdom does not implement scrollIntoView; every real browser does.
w.Element.prototype.scrollIntoView = function(){};

const errors = [];
w.addEventListener("error", e => errors.push(String(e.error || e.message)));

w.eval(fs.readFileSync(path.join(ROOT, "app.js"), "utf8"));

setTimeout(() => {
  const $ = s => w.document.querySelector(s);
  const q = s => [...w.document.querySelectorAll(s)];
  let fail = 0;
  const check = (name, cond, extra="") => {
    if (!cond) { fail++; console.log("FAIL " + name + " " + extra); }
    else console.log("ok   " + name + (extra ? " — " + extra : ""));
  };

  check("no uncaught errors", errors.length === 0, errors.join("; "));
  const rendered = q(".card").length;
  check("cards rendered", rendered > 0, rendered + " cards at default min score 55");
  check("header updated", $("#updated").textContent.includes("open"), $("#updated").textContent);
  // With a helper the button triggers the workflow in place; without one it
  // links to the Actions tab, where a second click runs it.
  check("refresh either triggers in place or links to Actions",
        helperUp ? $("#refresh").getAttribute("href") === "#"
                 : $("#refresh").href.includes("actions/workflows"),
        $("#refresh").getAttribute("href"));
  check("first card auto-selected", q(".card.sel").length === 1);
  check("detail pane filled", $("#detail").textContent.includes("Why"));
  check("score breakdown rows", q("table.explain tr").length >= 5,
        q("table.explain tr").length + " components");
  check("sponsorship filter built", q("#sponsor label").length === 6);
  check("source filter built", q("#source label").length === meta.sources.length);
  check("dropped count shown", $("#dropcount").textContent.trim().length > 2, $("#dropcount").textContent);
  // The banner should appear only when sponsorship data is absent.
  check("banner matches sponsorship data state",
        meta.has_sponsorship_data ? $("#banner").hidden
                                  : $("#banner").textContent.includes("filing record"),
        meta.has_sponsorship_data ? "data loaded, banner hidden" : "banner shown");

  // Keyboard: j moves selection, a marks applied and persists.
  const press = (key) => w.document.dispatchEvent(new w.KeyboardEvent("keydown", {key, bubbles:true}));
  press("j");
  check("j moves selection", q(".card")[1].classList.contains("sel"));
  press("a");
  const saved = JSON.parse(store["jobradar.tracker.v1"] || "{}");
  check("a marks applied and persists", Object.values(saved).some(v => v.status === "applied"),
        JSON.stringify(Object.values(saved)[0] || {}).slice(0,80));
  check("applied chip appears", q(".chip.applied").length >= 1);
  check("track count updated", $("#trackcount").textContent.startsWith("1"), $("#trackcount").textContent);

  // Filters
  $("#minscore").value = 95;
  $("#minscore").dispatchEvent(new w.Event("input", {bubbles:true}));
  const high = q(".card").length;
  check("min score filter narrows", high < rendered, `${rendered} -> ${high} at min 95`);
  $("#minscore").value = 0;
  $("#minscore").dispatchEvent(new w.Event("input", {bubbles:true}));
  check("min score filter widens back", q(".card").length >= rendered);

  // Filtered cards must NOT be in the initial payload: they outnumber open
  // roles about five to one and nobody sees them by default.
  check("initial load skips filtered.json",
        !fetched.some((u) => u.includes("filtered")), fetched.join(" "));
  check("dropped count still shown from meta", $("#dropcount").textContent.includes(String(meta.dropped)),
        $("#dropcount").textContent);

  $("#search").value = "analyst";
  $("#search").dispatchEvent(new w.Event("input", {bubbles:true}));
  check("search filters", q(".card").length > 0 && q(".card").length < cards.length);
  check("url hash reflects state", w.location.hash.includes("q=analyst"), w.location.hash);

  const chips = q(".cardsub .chip");
  check("sponsor chip rendered", chips.length > 0, chips[0] ? chips[0].textContent : "");
  const titled = chips.filter(c => c.title && c.title.includes("certified"));
  check("sponsor chip carries a tooltip when data exists",
        titled.length > 0 || !meta.has_sponsorship_data,
        titled[0] ? titled[0].title.slice(0,60) : "no sponsorship data loaded");

  // The three-day default is now the first thing anyone sees. If it matches
  // nothing it must say so and point at the wider window, not render an empty
  // list that reads as a broken refresh.
  $("#search").value = "";
  $("#search").dispatchEvent(new w.Event("input", {bubbles:true}));
  $("#showdropped").checked = false;
  $("#showdropped").dispatchEvent(new w.Event("change", {bubbles:true}));
  $("#minscore").value = 55;
  $("#minscore").dispatchEvent(new w.Event("input", {bubbles:true}));
  [...w.document.querySelectorAll("#age button")].find(b => b.dataset.v === "3").click();
  const threeDay = q(".card").length;
  const emptyMsg = $("#list .empty") ? $("#list .empty").textContent : "";
  check("three-day default renders or explains itself",
        threeDay > 0 || /widen Posted within|lowering the minimum/.test(emptyMsg),
        threeDay > 0 ? threeDay + " cards in 3d" : emptyMsg.slice(0, 80));

  // Now actually ask for them, and confirm they arrive lazily.
  $("#search").value = "";
  $("#search").dispatchEvent(new w.Event("input", {bubbles:true}));
  $("#showdropped").checked = true;
  $("#showdropped").dispatchEvent(new w.Event("change", {bubbles:true}));
  setTimeout(() => {
    check("filtered.json fetched only on demand",
          fetched.some((u) => u.includes("filtered")));
    check("filtered cards render once loaded", q(".card.is-dropped").length > 0,
          q(".card.is-dropped").length + " shown");

    // --- pasting a job link ---
    $("#showdropped").checked = false;
    $("#showdropped").dispatchEvent(new w.Event("change", {bubbles:true}));
    $("#minscore").value = 55;
    $("#minscore").dispatchEvent(new w.Event("input", {bubbles:true}));
    const URL_IN = "https://job-boards.greenhouse.io/acme/jobs/123456";
    $("#addurl").value = URL_IN;
    $("#addform").dispatchEvent(new w.Event("submit", {bubbles:true, cancelable:true}));
    const mine = () => q(".card").filter(n => n.textContent.includes("added by you"));
    check("a pasted link becomes a card", mine().length === 1,
          mine()[0] ? mine()[0].textContent.replace(/\s+/g," ").slice(0,46) : "none");
    check("the pasted link persists",
          JSON.parse(store["jobradar.added.v1"] || "[]").some(a => a.url === URL_IN));
    // It has no score until the CLI fetches it, so the score slider must not hide it.
    check("a pasted link survives the score filter", mine().length === 1,
          "min score " + $("#minscore").value);
    check("the detail pane offers the tailor command",
          /Copy the tailor command/.test($("#detail").textContent));
    const boxes = q(".cmdbox");
    check("the command carries the pasted url",
          boxes.some(b => b.value.includes(URL_IN) && b.value.includes("tailor brief --url")),
          boxes[0] ? boxes[0].value.slice(0, 58) : "none");

    $("#addurl").value = "not a url";
    $("#addform").dispatchEvent(new w.Event("submit", {bubbles:true, cancelable:true}));
    check("a non-url is refused with a reason",
          /does not look like a link/.test($("#addnote").textContent), $("#addnote").textContent);

    $("#addurl").value = URL_IN;
    $("#addform").dispatchEvent(new w.Event("submit", {bubbles:true, cancelable:true}));
    check("a duplicate link is not added twice", mine().length === 1);

    // --- the local helper ---
    const dot = $("#helper");
    const hasButton = q(".dsec button").some(b => b.textContent === "Tailor this resume");
    if (helperUp) {
      check("helper dot reads on", /helper on/.test(dot.textContent), dot.textContent);
      check("Tailor button present when the helper answers", hasButton);
      check("Refresh triggers in place rather than linking out",
            !/actions\/workflows/.test($("#refresh").href), $("#refresh").href);
      check("the copy fallback is still there",
            q(".dsec button").some(b => /Copy the tailor command/.test(b.textContent)));
    } else {
      // The jsdom origin is the github.io one, where Chrome refuses the call to
      // loopback. "helper off" would be a lie: the helper may well be running,
      // it just cannot be reached FROM HERE, and telling someone to start a
      // helper they already started is the wrong advice.
      check("Refresh still links to Actions without a helper",
          /actions\/workflows/.test($("#refresh").href), $("#refresh").href);
    check("dot points at localhost rather than claiming the helper is off",
            /tailor on localhost/.test(dot.textContent), dot.textContent);
      check("Tailor button absent when unreachable", !hasButton);
      check("the copy fallback carries the whole experience",
            q(".dsec button").some(b => /Copy the tailor command/.test(b.textContent)));
      check("the detail pane says where the button lives",
            /localhost:8777/.test($("#detail").textContent));
      check("and links there",
            q("#detail a").some(a => a.href.includes("localhost:8777")));
    }
    // --- tracker view ---
    $("#viewtracker").click();
    setTimeout(() => {
      check("tracker view replaces the job list", $("#list").hidden && !$("#tracker").hidden);
      const rows = q("#trackertable tbody tr");
      check("tracker has rows", rows.length > 0, rows.length + " rows");
      const cells = rows.map(r => [...r.children].map(c => c.textContent));
      const flat = cells.map(c => c.join("|")).join("  ");
      check("seven columns per row", cells.every(c => c.length === 7),
            cells[0] ? cells[0].join(" | ") : "none");
      if (helperUp) {
        // The helper is the authority on whether a PDF exists; the browser only
        // knows what you marked.
        check("a produced resume shows as produced", /produced/.test(flat));
        check("a skipped one shows as skipped", /skipped/.test(flat));
      }
      const csv = q("#trackercsv").length === 1;
      check("CSV export button present", csv);
      check("summary counts something", /tracked/.test($("#trackersummary").textContent),
            $("#trackersummary").textContent);

      // sorting
      const before = q("#trackertable tbody tr")[0].children[0].textContent;
      w.document.querySelector('#trackertable th[data-sort="company"]').click();
      const after = q("#trackertable tbody tr")[0].children[0].textContent;
      check("clicking a header re-sorts", rows.length < 2 || before !== after
            || q("#trackertable th.sorted").length === 1);

      $("#viewjobs").click();
      check("switching back restores the job list", !$("#list").hidden && $("#tracker").hidden);

      console.log(fail ? `\n${fail} FAILURES` : "\nAll page checks passed");
      process.exit(fail ? 1 : 0);
    }, 150);
  }, 200);
}, 600);

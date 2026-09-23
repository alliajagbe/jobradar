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
let exported = null;
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
      // No dedupe_key at all. This is the shape that produced a phantom row:
      // the queue branch had no `key`, so the dropdown wrote tracker[undefined].
      {slug:"NoKeyCo", status:"ready", company:"NoKeyCo", title:"Data Analyst",
       url:"https://example.com/nokey", pdf:"/x/nokey.pdf"},
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
let lastBlob = null;
w.URL.createObjectURL = (b) => { lastBlob = b; return "blob:x"; };
w.URL.revokeObjectURL = () => {};
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

  // Newest first, by Alli's instruction: score only breaks ties. The card shows
  // its own age, so the ages down the list must never decrease.
  const ages = q(".card").map(n => {
    // The age has its own span; textContent runs the fields together, so a
    // regex over the whole card matched nothing and the check passed vacuously.
    const span = [...n.querySelectorAll(".cardsub span")]
      .find(e => /^(today|\d+d ago)$/.test(e.textContent.trim()));
    if (!span) return null;
    const t = span.textContent.trim();
    return t === "today" ? 0 : Number(t.replace("d ago", ""));
  }).filter(v => v !== null);
  check("the feed is ordered newest first",
        ages.length > 5 && ages.every((v, i) => i === 0 || ages[i - 1] <= v),
        ages.length ? ages.slice(0, 8).join(", ") : "no ages read");

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
    // On an ATS the company is the path segment, not the host. Reading the host
    // here labels every Greenhouse posting "Greenhouse".
    check("an ATS link is named for the company, not the ATS",
          /Acme/.test(mine()[0].textContent) && !/Greenhouse/i.test(mine()[0].textContent),
          mine()[0].textContent.replace(/\s+/g," ").slice(0, 40));
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
    // ...and off an ATS it is the host, not the path, which is where "En_US"
    // came from: a careers site whose first path segment is a locale.
    const URL_CO = "https://jobs.acmecorp.com/en_US/careers/JobDetail/Data-Analyst-Role/81563";
    $("#addurl").value = URL_CO;
    $("#addform").dispatchEvent(new w.Event("submit", {bubbles:true, cancelable:true}));
    const direct = mine().find(n => n.textContent.includes("Data Analyst Role"));
    check("a company-hosted link is named for the host, not the locale",
          !!direct && /Acmecorp/i.test(direct.textContent) && !/En_US/i.test(direct.textContent),
          direct ? direct.textContent.replace(/\s+/g," ").slice(0, 40) : "no card");
    // The Greenhouse embed link puts the employer in ?for= and its path is
    // /embed/job_app, so neither the host nor the path rule finds it.
    const URL_EMBED = "https://job-boards.greenhouse.io/embed/job_app?for=widgetco&token=5220191007";
    $("#addurl").value = URL_EMBED;
    $("#addform").dispatchEvent(new w.Event("submit", {bubbles:true, cancelable:true}));
    const embed = mine().find(n => /Widgetco/i.test(n.textContent));
    check("an embed link is named from its for= parameter",
          !!embed && !/Embed|Greenhouse/i.test(embed.textContent),
          embed ? embed.textContent.replace(/\s+/g," ").slice(0, 40) : "no card");

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
    setTimeout(async () => {
      check("tracker view replaces the job list", $("#list").hidden && !$("#tracker").hidden);
      const rows = q("#trackertable tbody tr");
      check("tracker has rows", rows.length > 0, rows.length + " rows");
      const cells = rows.map(r => [...r.children].map(c => c.textContent));
      const flat = cells.map(c => c.join("|")).join("  ");
      check("eight columns per row", cells.every(c => c.length === 8),
            cells[0] ? cells[0].join(" | ") : "none");

      // --- the Process column ---
      const sels = q("#trackertable select.procsel");
      check("every row has a process selector", sels.length === rows.length,
            sels.length + " of " + rows.length);
      check("process defaults to pending",
            sels.every(s => s.value === "pending"), sels[0] ? sels[0].value : "none");
      // The summary counted 0 pending while every dropdown displayed pending,
      // because an undefined process still shows the first option.
      check("the summary agrees with the dropdowns",
            new RegExp(rows.length + " pending").test($("#trackersummary").textContent),
            $("#trackersummary").textContent);
      check("every row has a stable key, so none is read-only",
            sels.every(s => !s.disabled));
      check("options are pending, complete, dismissed",
            sels[0] && [...sels[0].options].map(o => o.value).join(",")
              === "pending,complete,dismissed",
            sels[0] ? [...sels[0].options].map(o => o.value).join(",") : "none");

      // Setting a process on a row with no application status must persist. The
      // old track() deleted any entry without a status, which would have thrown
      // this away silently.
      const rowsBefore = q("#trackertable tbody tr").length;
      const target = q("#trackertable select.procsel")[0];
      target.value = "complete";
      target.dispatchEvent(new w.Event("change", {bubbles:true}));
      const saved = JSON.parse(store["jobradar.tracker.v1"] || "{}");
      check("process persists to storage",
            Object.values(saved).some(v => v.process === "complete"),
            JSON.stringify(Object.values(saved).map(v => v.process)));
      check("summary counts complete", /1 complete/.test($("#trackersummary").textContent),
            $("#trackersummary").textContent);

      // A completed row leaves the table. This is the whole point of marking it:
      // finished work should stop competing for attention with work that is not.
      // Counted relative to the rows actually present, because the helper adds
      // its own queue rows and hardcoding 1 made this pass only when it was off.
      check("a completed row is cleared from the table",
            q("#trackertable tbody tr").length === rowsBefore - 1,
            `${rowsBefore} rows, ${q("#trackertable tbody tr").length} drawn after completing one`);
      check("the tracker claims to be empty only when it actually is",
            rowsBefore === 1 ? ($("#trackerempty").hidden && !$("#trackerdone").hidden)
                             : $("#trackerdone").hidden);
      // Cleared means hidden, never deleted: the tracker is the record of what
      // Alli applied to, and the export is that record.
      $("#trackercsv").click();
      exported = lastBlob;

      // The toggle brings them back.
      const toggle = $("#trackershowdone");
      check("the toggle offers to show them", !toggle.hidden && /Show 1 completed/.test(toggle.textContent),
            toggle.textContent);
      toggle.dispatchEvent(new w.Event("click", {bubbles:true}));
      check("showing completed restores the row",
            q("#trackertable tbody tr").length === rowsBefore,
            q("#trackertable tbody tr").length + " of " + rowsBefore + " rows");

      // Back to pending must clear it rather than storing the default.
      const again = q("#trackertable select.procsel").find(s => s.value === "complete");
      again.value = "pending";
      again.dispatchEvent(new w.Event("change", {bubbles:true}));
      const cleared = JSON.parse(store["jobradar.tracker.v1"] || "{}");
      check("pending is stored as absence, not as a value",
            !Object.values(cleared).some(v => v.process === "pending"));
      // The phantom row: an entry under a missing key, rendering as a dropdown
      // with no job attached and no way to clear it from the page.
      check("no entry is written under a missing key",
            !Object.keys(cleared).some(k => !k || k === "undefined" || k === "null"),
            Object.keys(cleared).join(","));
      check("every rendered row has a company",
            q("#trackertable tbody tr").every(
              tr => tr.children[0].textContent.trim().length > 0),
            q("#trackertable tbody tr").map(tr => tr.children[0].textContent).join("|"));

      check("the table header carries Process",
            q("#trackertable th").some(t => t.textContent === "Process"
                                            && t.dataset.sort === "process"));
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

      // Cleared means hidden, never deleted: the tracker is the record of what
      // Alli applied to, so the export must still carry a completed row.
      const csvText = exported ? await exported.text() : "";
      check("a cleared row is still in the CSV export", /complete/.test(csvText),
            csvText ? csvText.split("\n").length - 1 + " data rows exported"
                    : "no export captured");

      // A RELOAD, which is where the tracker actually lost its work. Marking a
      // job complete writes {process} and nothing else, and loadTracker's heal
      // treated an entry with no company, title, url or status as a phantom row
      // and deleted it. The row cleared, and came back pending on reload. The
      // existing "process persists to storage" check read the store straight
      // after writing it, so it passed the whole time the bug was live.
      // Evaluating app.js in a second window over the SAME store is the reload.
      const priorTracker = JSON.parse(store["jobradar.tracker.v1"] || "{}");
      store["jobradar.tracker.v1"] = JSON.stringify(
        Object.assign({}, priorTracker, {"survives-a-reload": {process: "complete", updated: "2026-09-23"}}));
      const dom2 = new JSDOM(fs.readFileSync(path.join(ROOT, "index.html"), "utf8"), {
        runScripts: "outside-only", url: "https://alliajagbe.github.io/jobradar/",
        pretendToBeVisual: true,
      });
      const w2 = dom2.window;
      w2.fetch = w.fetch;
      w2.AbortController = w.AbortController;
      Object.defineProperty(w2, "localStorage", { value: w.localStorage });
      w2.HTMLDialogElement.prototype.showModal = function(){ this.open = true; };
      w2.HTMLDialogElement.prototype.close = function(){ this.open = false; };
      w2.URL.createObjectURL = () => "blob:x"; w2.URL.revokeObjectURL = () => {};
      w2.Element.prototype.scrollIntoView = function(){};
      try { w2.eval(fs.readFileSync(path.join(ROOT, "app.js"), "utf8")); } catch { /* boot noise */ }
      const reloaded = JSON.parse(store["jobradar.tracker.v1"] || "{}");
      check("a completed job survives a reload",
            !!reloaded["survives-a-reload"] && reloaded["survives-a-reload"].process === "complete",
            reloaded["survives-a-reload"] ? JSON.stringify(reloaded["survives-a-reload"])
                                          : "the entry was deleted on load");
      // The heal must still do its job.
      store["jobradar.tracker.v1"] = JSON.stringify({"": {}, "undefined": {}, "ok": {process: "complete"}});
      const dom3 = new JSDOM(fs.readFileSync(path.join(ROOT, "index.html"), "utf8"), {
        runScripts: "outside-only", url: "https://alliajagbe.github.io/jobradar/", pretendToBeVisual: true });
      const w3 = dom3.window;
      w3.fetch = w.fetch; w3.AbortController = w.AbortController;
      Object.defineProperty(w3, "localStorage", { value: w.localStorage });
      w3.HTMLDialogElement.prototype.showModal = function(){ this.open = true; };
      w3.HTMLDialogElement.prototype.close = function(){ this.open = false; };
      w3.URL.createObjectURL = () => "blob:x"; w3.URL.revokeObjectURL = () => {};
      w3.Element.prototype.scrollIntoView = function(){};
      try { w3.eval(fs.readFileSync(path.join(ROOT, "app.js"), "utf8")); } catch { /* boot noise */ }
      const healed = JSON.parse(store["jobradar.tracker.v1"] || "{}");
      check("phantom rows are still healed away",
            !("" in healed) && !("undefined" in healed) && !!healed.ok,
            Object.keys(healed).join(",") || "empty");

      console.log(fail ? `\n${fail} FAILURES` : "\nAll page checks passed");
      process.exit(fail ? 1 : 0);
    }, 150);
  }, 200);
}, 600);

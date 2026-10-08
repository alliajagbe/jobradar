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
  if (u.endsWith("/api/contacts")) {
    // Shaped exactly as serve._contacts returns it: the CSV's own column names.
    return Promise.resolve({ ok: true, json: () => Promise.resolve({ files: ["contacts-x.csv"], contacts: [
      {"Author ID": "https://openalex.org/A1", "Person": "Ada Reviewer",
       "Their role": "PI", "Organisation": "Fred Hutch", "Sector": "nonprofit",
       "H-1B cap": "check", "Pool": "capexempt",
       "Hook": "Gold standards, asymmetric scoring, and clinical extraction (2026)",
       "Link": "https://doi.org/10.1/abc", "Published": "2026-09-01",
       "Certified filings": "81", "In analyst roles": "16", "Counted from": "FRED HUTCH"},
      {"Author ID": "https://openalex.org/A2", "Person": "Bo Engineer",
       "Their role": "first author", "Organisation": "Deloitte (United States)",
       "Sector": "company", "H-1B cap": "subject", "Pool": "author",
       "Hook": "Closing the tax gap: fraud-risk detection (2026)",
       "Link": "https://doi.org/10.1/def", "Published": "2026-08-01",
       "Certified filings": "128", "In analyst roles": "14", "Counted from": "DELOITTE"},
    ]}) });
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

      /* ---- outreach ----
         The point of each check is the hole it closes, so they are named for
         the failure rather than the feature. */
      const iso = (n) => {
        const d = new Date(); d.setUTCDate(d.getUTCDate() + n);
        return d.toISOString().slice(0, 10);
      };

      $("#viewoutreach").click();
      check("outreach view opens and the other two close",
            !$("#outreach").hidden && $("#tracker").hidden && $("#list").hidden,
            `outreach hidden=${$("#outreach").hidden} tracker hidden=${$("#tracker").hidden}`);
      check("pool and channel dropdowns built from the vocabularies",
            q("#o_pool option").length === 5 && q("#o_channel option").length === 3,
            `${q("#o_pool option").length} pools, ${q("#o_channel option").length} channels`);
      check("the sent date defaults to today", $("#o_sent").value === iso(0), $("#o_sent").value);
      check("empty state shown before anything is logged", !$("#outreachempty").hidden);

      const logOne = (person, org, pool, sent, hook) => {
        $("#o_person").value = person; $("#o_org").value = org;
        $("#o_pool").value = pool; $("#o_sent").value = sent;
        $("#o_hook").value = hook || "";
        $("#outreachform").dispatchEvent(new w.Event("submit", {bubbles:true, cancelable:true}));
      };

      // Sent 10 days ago, so its follow-up (sent + 7) is 3 days overdue.
      logOne("A Reviewer", "Duke University", "capexempt", iso(-10), "their extraction paper");
      let saved2 = JSON.parse(store["jobradar.outreach.v1"] || "{}");
      check("a logged contact persists", Object.keys(saved2).length === 1,
            JSON.stringify(Object.values(saved2)[0] || {}).slice(0, 90));
      const first = Object.values(saved2)[0] || {};
      check("one follow-up is scheduled on the way in, not left to be set later",
            first.followup === iso(-3), `sent ${first.sent} -> follow up ${first.followup}`);
      check("a new contact starts awaiting a reply", first.stage === "sent", first.stage);
      check("the hook is recorded", first.hook === "their extraction paper", first.hook);

      check("an overdue follow-up is marked on the row",
            q("#outreachtable tr.due").length === 1, q("#outreachtable tr.due").length + " due rows");
      check("the summary counts what is due",
            $("#outreachsummary").textContent.includes("1 due now"), $("#outreachsummary").textContent);
      check("the due filter only appears when something is due",
            !$("#outreachdueonly").hidden && $("#outreachdueonly").textContent.includes("1 due"),
            $("#outreachdueonly").textContent);

      // Per-person fields clear, per-session ones are kept: logging four
      // contacts from one pool in a row should not mean re-picking the pool.
      check("name clears after logging but the pool is kept",
            $("#o_person").value === "" && $("#o_pool").value === "capexempt",
            `person="${$("#o_person").value}" pool=${$("#o_pool").value}`);

      // A reply makes the follow-up moot. If a replied row still counted as
      // due, the due list would fill up with people who already answered.
      const stageSel = q("#outreachtable .stagesel")[0];
      stageSel.value = "replied";
      stageSel.dispatchEvent(new w.Event("change", {bubbles:true}));
      check("a replied contact is no longer due however old the date",
            q("#outreachtable tr.due").length === 0
            && !$("#outreachsummary").textContent.includes("due now"),
            $("#outreachsummary").textContent);
      check("the reply shows in the summary",
            $("#outreachsummary").textContent.includes("1 replied (100%)"),
            $("#outreachsummary").textContent);

      // Reply rate per pool is the reason the pool field exists. A pool needs
      // three sent before it can be called best, or one lucky reply out of one
      // message would win it.
      logOne("B Recruiter", "Acme", "recruiter", iso(0));
      logOne("C Recruiter", "Acme", "recruiter", iso(0));
      check("a pool with one reply from one message is not yet called best",
            q("#outreachpools .poolstat.best").length === 0,
            q("#outreachpools .poolstat").length + " pools shown");
      logOne("D Reviewer", "Duke University", "capexempt", iso(0));
      logOne("E Reviewer", "UNC", "capexempt", iso(0));
      check("the pool with the best rate is marked once it has enough sent",
            q("#outreachpools .poolstat.best").length === 1,
            [...q("#outreachpools .poolstat")].map(n => n.textContent).join(" | "));

      // Dead ends sort last: they need nothing and would otherwise sit among
      // the rows that do.
      const stageSels = q("#outreachtable .stagesel");
      stageSels[stageSels.length - 1].value = "dead";
      stageSels[stageSels.length - 1].dispatchEvent(new w.Event("change", {bubbles:true}));
      const lastRow = q("#outreachtable tbody tr").slice(-1)[0];
      check("a dead end sorts to the bottom", lastRow.classList.contains("closed"),
            lastRow.textContent.slice(0, 40));

      $("#outreachcsv").click();
      const ocsv = lastBlob ? await lastBlob.text() : "";
      check("outreach csv carries the hook and the pool",
            ocsv.includes("Hook") && ocsv.includes("their extraction paper")
            && ocsv.includes("Research group"),
            ocsv.split("\n").length - 1 + " data rows");

      // The backup carries both stores now. An export taken before outreach
      // existed is a bare tracker object, and it still has to restore.
      $("#export").click();
      const backup = lastBlob ? JSON.parse(await lastBlob.text()) : {};
      check("the backup carries both stores",
            backup.v === 1 && !!backup.tracker && Object.keys(backup.outreach || {}).length === 5,
            `v=${backup.v} tracker=${Object.keys(backup.tracker||{}).length} outreach=${Object.keys(backup.outreach||{}).length}`);

      // The older bare-tracker export must still import. This is the shape
      // every backup taken before today has, and sniffing it wrong would
      // silently throw the whole file away. jsdom will not let us build a
      // FileList, so the property is defined with an object carrying the one
      // method the handler calls.
      const legacy = JSON.stringify({"legacy-key": {status: "applied", company: "Old Co"}});
      Object.defineProperty($("#importfile"), "files", {
        value: [{ text: () => Promise.resolve(legacy) }], configurable: true });
      $("#importfile").dispatchEvent(new w.Event("change", {bubbles:true}));
      await new Promise((r) => setTimeout(r, 0));
      const afterLegacy = JSON.parse(store["jobradar.tracker.v1"] || "{}");
      check("a pre-outreach backup still imports",
            !!afterLegacy["legacy-key"] && afterLegacy["legacy-key"].status === "applied",
            Object.keys(afterLegacy).length + " tracked roles after import");
      check("importing a bare tracker leaves the contacts alone",
            Object.keys(JSON.parse(store["jobradar.outreach.v1"] || "{}")).length === 5,
            Object.keys(JSON.parse(store["jobradar.outreach.v1"] || "{}")).length + " contacts");

      // A select must not leak keystrokes into the jobs shortcuts. Typing "a"
      // with a dropdown focused used to mark the job selected in the JOBS view
      // as applied, which is not even the row being looked at.
      const beforeKeys = JSON.stringify(JSON.parse(store["jobradar.tracker.v1"] || "{}"));
      const sel0 = q("#outreachtable .stagesel")[0];
      sel0.dispatchEvent(new w.KeyboardEvent("keydown", {key:"a", bubbles:true}));
      check("typing in a dropdown does not fire the jobs shortcuts",
            JSON.stringify(JSON.parse(store["jobradar.tracker.v1"] || "{}")) === beforeKeys,
            "tracker unchanged");

      /* ---- candidates ----
         The panel feeding the outreach table. Every check names the thing that
         would go wrong without it. */

      // The parser is exercised through the file picker rather than called
      // directly: app.js is strict-mode and evaluated with w.eval, so its
      // declarations never leave that scope. Going through the picker also
      // covers the whole path, which is what actually has to work.
      //
      // The row is deliberately nasty. Paper titles contain commas and the
      // writer quotes them, so a naive split(",") shifts every later column by
      // one: the link lands in Published, the filing counts move left, and the
      // row still looks plausible enough not to notice.
      const candCsv = 'Author ID,Person,Their role,Organisation,Sector,H-1B cap,Pool,Hook,Link,Published,Certified filings,In analyst roles,Counted from\r\n'
        + 'A9,Cy Author,PI,"Penn, Trustees of",education,exempt,capexempt,'
        + '"AI for detection, grading, and prognostication",https://doi.org/x,2026-07-01,481,43,"TRUSTEES OF THE UNIVERSITY OF PENNSYLVANIA; UNIVERSITY OF PENNSYLVANIA"\r\n';

      $("#viewoutreach").click();
      await new Promise((r) => setTimeout(r, 0));
      const candRows = () => q("#candidatestable tbody tr").length;
      const rowFor = (name) => [...q("#candidatestable tbody tr")]
        .find((tr) => tr.textContent.includes(name));

      if (helperUp) {
        check("candidates load from the helper on opening Outreach", candRows() === 2,
              candRows() + " rows");
        check("the cap status is shown per contact",
              q("#candidatestable .chip.cap-check").length === 1
              && q("#candidatestable .chip.cap-subject").length === 1,
              [...q("#candidatestable .chip")].map(n => n.textContent).join(", "));
      } else {
        check("no helper means no candidates, and the explainer shows",
              candRows() === 0 && !$("#candidatesempty").hidden, candRows() + " rows");
      }

      const pick = (name, text) => {
        Object.defineProperty($("#candidatesfile"), "files",
          { value: [{ name, text: () => Promise.resolve(text) }], configurable: true });
        $("#candidatesfile").dispatchEvent(new w.Event("change", {bubbles:true}));
        return new Promise((r) => setTimeout(r, 0));
      };

      const beforePick = candRows();
      await pick("contacts-x.csv", candCsv);
      check("the file picker loads a CSV", candRows() === beforePick + 1,
            candRows() + " rows");

      const cy = rowFor("Cy Author");
      check("a quoted comma does not shift the columns",
            !!cy && cy.textContent.includes("AI for detection, grading, and prognostication")
            && cy.textContent.includes("Penn, Trustees of")
            && cy.textContent.includes("481 / 43"),
            cy ? cy.textContent.replace(/\s+/g, " ").slice(0, 110) : "row missing");
      check("the cap column renders from the CSV",
            !!cy && cy.querySelector(".chip.cap-exempt"),
            cy ? (cy.querySelector(".chip") || {}).textContent : "no row");

      await pick("quotes.csv", 'Author ID,Person,Hook\nA8,Dee Quoted,"say ""hi"" now"\n');
      check("an escaped quote survives",
            (rowFor("Dee Quoted") || {}).textContent?.includes('say "hi" now'),
            (rowFor("Dee Quoted") || {}).textContent || "row missing");

      await pick("notrailing.csv", "Author ID,Person,Hook\nA7,Eve NoNewline,last row");
      check("a file with no trailing newline keeps its last row", !!rowFor("Eve NoNewline"));

      await pick("blanks.csv", "Author ID,Person,Hook\nA6,Fay Blanks,x\n\n\n");
      check("blank lines do not become empty rows", !!rowFor("Fay Blanks")
            && [...q("#candidatestable tbody tr")].every((tr) => tr.textContent.trim()));

      await pick("bom.csv", "\uFEFFAuthor ID,Person,Hook\nA5,Gus Bom,y\n");
      check("a BOM does not corrupt the first header", !!rowFor("Gus Bom"));

      // THE invariant. A candidate is somebody suggested, not somebody written
      // to. If loading a CSV moved the outreach numbers, then "sent this week",
      // the due list and every per-pool reply rate would be counting messages
      // that were never sent.
      const summaryBefore = $("#outreachsummary").textContent;
      const outreachBefore = Object.keys(JSON.parse(store["jobradar.outreach.v1"] || "{}")).length;
      check("loading candidates does not touch the outreach stats",
            summaryBefore === $("#outreachsummary").textContent
            && outreachBefore === Object.keys(JSON.parse(store["jobradar.outreach.v1"] || "{}")).length,
            summaryBefore);
      check("candidates are stored under their own key",
            Object.keys(JSON.parse(store["jobradar.candidates.v1"] || "{}")).length === candRows(),
            Object.keys(JSON.parse(store["jobradar.candidates.v1"] || "{}")).join(","));

      // Promote: the one place a candidate becomes outreach.
      const beforePromote = candRows();
      q("#candidatestable .logbtn")[0].click();
      const afterOutreach = JSON.parse(store["jobradar.outreach.v1"] || "{}");
      const promoted = Object.values(afterOutreach).filter(v => v.candidateId);
      check("logging a candidate creates exactly one outreach row",
            promoted.length === 1, promoted.length + " promoted rows");
      check("it carries the hook and the pool from the candidate",
            !!promoted[0].hook && promoted[0].pool
            && promoted[0].stage === "sent",
            JSON.stringify(promoted[0]).slice(0, 120));
      check("the follow-up is scheduled seven days out, by the same code path",
            promoted[0].followup === iso(7), `${promoted[0].sent} -> ${promoted[0].followup}`);
      check("a logged candidate leaves the panel", candRows() === beforePromote - 1,
            candRows() + " of " + beforePromote + " remain");
      check("and now counts in the outreach stats",
            $("#outreachsummary").textContent !== summaryBefore,
            $("#outreachsummary").textContent);

      // Skip: gone for good, because the tool is meant to be re-run.
      if (candRows() > 0) {
        const beforeSkip = candRows();
        q("#candidatestable .rmbtn")[0].click();
        check("skipping removes a candidate", candRows() === beforeSkip - 1,
              candRows() + " rows");
      }
      const skipped = JSON.parse(store["jobradar.candidates.dismissed.v1"] || "[]");
      check("promoted and skipped ids are both remembered",
            skipped.length >= 1, skipped.length + " dismissed");

      // Re-loading the same CSV must not resurrect them. Without this every
      // run of the tool would bring back everyone already dealt with.
      const nowShowing = candRows();
      const reimport = { name: "contacts-x.csv", text: () => Promise.resolve(candCsv) };
      Object.defineProperty($("#candidatesfile"), "files", { value: [reimport], configurable: true });
      $("#candidatesfile").dispatchEvent(new w.Event("change", {bubbles:true}));
      await new Promise((r) => setTimeout(r, 0));
      check("re-importing does not resurrect a handled contact",
            candRows() === nowShowing, candRows() + " vs " + nowShowing);

      // A non-contacts CSV should be refused rather than making empty rows.
      const wrong = { name: "jobs.csv", text: () => Promise.resolve("Company,Title\nAcme,Analyst\n") };
      Object.defineProperty($("#candidatesfile"), "files", { value: [wrong], configurable: true });
      $("#candidatesfile").dispatchEvent(new w.Event("change", {bubbles:true}));
      await new Promise((r) => setTimeout(r, 0));
      check("a file that is not a contacts CSV is refused",
            $("#banner").textContent.includes("not a jobradar contacts CSV"),
            $("#banner").textContent);

      // The backup must carry the panel too, dismissals included.
      $("#export").click();
      const backup2 = lastBlob ? JSON.parse(await lastBlob.text()) : {};
      check("the backup carries candidates and their dismissals",
            !!backup2.candidates && Array.isArray(backup2.candidatesDismissed)
            && backup2.candidatesDismissed.length >= 1,
            `candidates=${Object.keys(backup2.candidates||{}).length} skipped=${(backup2.candidatesDismissed||[]).length}`);

      // Contacts have to survive a reload, same as the tracker did not at
      // first. A third window over the same store is the reload.
      //
      // Counted rather than hardcoded: promoting a candidate above legitimately
      // adds a contact, and an absolute number here would have to be edited
      // every time a check earlier in the file logs one more.
      const beforeReload = Object.keys(JSON.parse(store["jobradar.outreach.v1"] || "{}")).length;
      const dom4 = new JSDOM(fs.readFileSync(path.join(ROOT, "index.html"), "utf8"), {
        runScripts: "outside-only", url: "https://alliajagbe.github.io/jobradar/", pretendToBeVisual: true });
      const w4 = dom4.window;
      w4.fetch = w.fetch; w4.AbortController = w.AbortController;
      Object.defineProperty(w4, "localStorage", { value: w.localStorage });
      w4.HTMLDialogElement.prototype.showModal = function(){ this.open = true; };
      w4.HTMLDialogElement.prototype.close = function(){ this.open = false; };
      w4.URL.createObjectURL = () => "blob:x"; w4.URL.revokeObjectURL = () => {};
      w4.Element.prototype.scrollIntoView = function(){};
      try { w4.eval(fs.readFileSync(path.join(ROOT, "app.js"), "utf8")); } catch { /* boot noise */ }
      const afterReload = JSON.parse(store["jobradar.outreach.v1"] || "{}");
      check("contacts survive a reload",
            Object.keys(afterReload).length === beforeReload && beforeReload > 0,
            Object.keys(afterReload).length + " of " + beforeReload + " after reload");

      // And a contact with neither a name nor an organisation is not a contact.
      store["jobradar.outreach.v1"] = JSON.stringify({
        empty: {stage: "sent"}, real: {person: "X", org: "Y", stage: "sent"} });
      const dom5 = new JSDOM(fs.readFileSync(path.join(ROOT, "index.html"), "utf8"), {
        runScripts: "outside-only", url: "https://alliajagbe.github.io/jobradar/", pretendToBeVisual: true });
      const w5 = dom5.window;
      w5.fetch = w.fetch; w5.AbortController = w.AbortController;
      Object.defineProperty(w5, "localStorage", { value: w.localStorage });
      w5.HTMLDialogElement.prototype.showModal = function(){ this.open = true; };
      w5.HTMLDialogElement.prototype.close = function(){ this.open = false; };
      w5.URL.createObjectURL = () => "blob:x"; w5.URL.revokeObjectURL = () => {};
      w5.Element.prototype.scrollIntoView = function(){};
      try { w5.eval(fs.readFileSync(path.join(ROOT, "app.js"), "utf8")); } catch { /* boot noise */ }
      // The heal is asserted against storage, not the table: these secondary
      // windows do not finish booting, so nothing is wired up to render.
      const healedOut = JSON.parse(store["jobradar.outreach.v1"] || "{}");
      check("a contact with no name and no organisation is dropped",
            !("empty" in healedOut) && !!healedOut.real,
            Object.keys(healedOut).join(",") || "empty");

      console.log(fail ? `\n${fail} FAILURES` : "\nAll page checks passed");
      process.exit(fail ? 1 : 0);
    }, 150);
  }, 200);
}, 600);

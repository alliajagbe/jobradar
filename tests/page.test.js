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
w.fetch = (u) => {
  fetched.push(u);
  const body = u.includes("meta") ? meta : u.includes("filtered") ? filtered : cards;
  return Promise.resolve({ json: () => Promise.resolve(body) });
};
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
  check("refresh link points at Actions", $("#refresh").href.includes("actions/workflows"));
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
    console.log(fail ? `\n${fail} FAILURES` : "\nAll page checks passed");
    process.exit(fail ? 1 : 0);
  }, 200);
}, 600);

# JobRadar

Analytics roles from five applicant tracking systems, filtered to entry level,
scored against a skill profile, and annotated with each employer's H-1B filing
history from the Department of Labor.

Built for a job search on a student visa, where the expensive mistake is not a
weak application but a strong application to a company that has never sponsored
anyone.

**The page:** https://alliajagbe.github.io/jobradar

## How it works

GitHub Actions is the backend, GitHub Pages is the frontend, and git is the
database. Nothing runs on your laptop and there is no API key anywhere.

```
Actions tab -> "Run workflow"
      |
      v
python -m jobradar refresh          (on GitHub's runner)
  pulls every board in seeds/
  filters, scores, matches sponsorship
  writes data/jobs.jsonl  +  docs/data/jobs.json
  commits if anything changed
      |
      v
GitHub Pages serves docs/
      |
      v
Your browser. Tracking lives in localStorage.
```

## Where the boards come from

1,760 boards, of which 1,610 were found rather than curated.

A hand-written seed list has a flaw no amount of curation fixes: it contains the
companies whoever wrote it already knew. The first version of this list held 128
boards and missed 29 of the 30 largest analyst H-1B sponsors in the country.

The manual workaround people use is a search engine, `site:greenhouse.io
"data analyst"`. Scraping Google is not an option, but the useful half of that
query does not need Google: what it really supplies is **an index of which boards
exist**, and Common Crawl publishes that free and without a key. Read the board
tokens out of that index, then fetch them through the ordinary adapters. A token
yields the whole board with full descriptions, real posting dates and structured
locations; a search result yields a stale snippet.

**Keeping it current.** Common Crawl indexes what it crawled weeks ago, so a
board that launched last week is not in it yet. Re-enumerate roughly monthly:

```bash
.venv/bin/python -m jobradar crawl --enumerate   # writes data/cc_tokens.csv
git add data/cc_tokens.csv && git commit -m "Re-enumerate boards" && git push
```

Then run the **Crawl for boards** workflow, which validates the new tokens and
keeps the ones hiring analysts.

Enumeration runs locally, not in Actions, and that split is deliberate: Common
Crawl throttles cloud IP ranges hard enough that a runner reliably gets 503
where a laptop sails through. Validation only touches the ATS APIs, which do not
care, so it runs on the runner.

## Sources

| Source | Cost per board | Notes |
|---|---|---|
| Greenhouse | 1 request | Whole board with descriptions |
| Ashby | 1 request | Whole board with descriptions |
| Lever | 1 request | Descriptions already plain text |
| SmartRecruiters | paginated + 1 per posting | Hydrated only after the title gate |
| Workday | paginated search + 1 per posting | Per-tenant, needs tenant + wd number + site |

## Using it

Open the page. It defaults to roles posted in the **last three days**, because
anything older has been seen by hundreds of applicants and being early is the
point of running this daily. Older postings are still stored, so skipping a few
days loses nothing; widen **Posted within** and they are there.

Filter, read, and mark what you apply to. Statuses save in that
browser and never reach the repository. Export writes a JSON backup; Import
merges one back in.

`j` and `k` move, `Enter` opens the posting, `a` marks applied, `s` interested,
`x` dismisses, `/` searches, `?` lists the keys.

## Running it yourself

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt

.venv/bin/python -m jobradar verify-boards      # are the seeded slugs alive
.venv/bin/python -m jobradar -v refresh         # pull, score, publish
.venv/bin/python -m jobradar stats --histogram  # score distribution
cd docs && python -m http.server 8777           # read it at localhost:8777
```

One-time, and better run as workflows because of the download size:

```bash
.venv/bin/python -m jobradar sponsorship        # ~250MB of DOL data per year
.venv/bin/python -m jobradar discover --apply   # probe top sponsors for boards
.venv/bin/python -m jobradar crawl --enumerate  # board tokens from Common Crawl
.venv/bin/python -m jobradar crawl --from-file --apply   # validate them
```

## Retargeting it

- `profile/profile.yaml` — target cities, claimed skills, Workday search terms
- `seeds/titles.csv` — which titles count, and which are rejected outright
- `seeds/skills.csv` — the skill lexicon and its weights
- `seeds/boards.csv` — companies to watch
- `jobradar/config.py` — every scoring constant, each with a comment saying what
  breaks if you change it

## What is in this repository

Public job postings, a skill keyword list, and the scraper. **Not** here: which
jobs you applied to, your notes, or your resume. Those stay in your browser and
on your disk.

## Reading the sponsorship signal

| Chip | Means |
|---|---|
| Sponsors often | 25+ certified filings, at least 3 in analyst occupations |
| Has sponsored | At least one certified filing |
| Posting offers sponsorship | The posting itself says so |
| Never filed | Found in the data, no certified filings |
| No filing record | Not matched to any employer in the data |
| No sponsorship | The posting rules it out |

LCA filings are an employer-level history, not a promise about a specific role.
Every card shows how the employer name was matched, so a fuzzy match can be
discounted on sight.

## Tests

```bash
.venv/bin/python -m pytest tests/ -q
```

The gate tests matter most. The single most important assertion is that
"Applicants must be authorized to work in the United States" does **not** filter
a posting out: someone on OPT is authorized to work. Only the longer form, the
one adding "without sponsorship" or "now or in the future", disqualifies.

### Page tests

`tests/page.test.js` runs the published page in a real DOM and checks render,
selection, tracking persistence, filters and the URL hash. It needs jsdom, which
is not a project dependency:

```bash
npm install jsdom && node tests/page.test.js
```

## The Tailor button

Clicking Tailor on a job card needs a local helper running, because a static page
cannot reach your disk or a Claude Code session:

```bash
.venv/bin/python -m jobradar serve
```

Then open **http://localhost:8777**, which serves the same page and the same data. The
header shows **helper on** and every card gets a **Tailor this resume** button. Clicking
it queues the job, the helper fetches the full posting, and a Claude Code session writes
and renders the resume. The card reports queued → briefed → writing → ready, and macOS
notifies you when the PDF lands.

**The button does not work from the github.io URL.** Chrome refuses a cross-origin call
from an HTTPS page to a server on loopback: Private Network Access is enforced in current
Chrome and the preflight headers are not sufficient. Tested and confirmed, not assumed.
The github.io page detects this, says "tailor on localhost" in the header, and links you
across. Its copy-the-command path still works, which is also the phone experience.

This turned out fine. Same-origin was the safer design anyway, because the entire class of
"another website talks to your helper" disappears rather than being defended against.

`python -m jobradar tailor queue` lists what the page has asked for, and flags anything
that has been `writing` for over 30 minutes as stalled, which usually means a session
died mid-job.

**This needs a Claude Code session running and watching the queue.** Entries persist, so
clicking Tailor with no session open is not lost work; it waits.

### Security

The helper binds to `127.0.0.1`, which the kernel will not route external packets to, so
it is not reachable from the network. It exits after 30 idle minutes. The only thing that
can reach it is a page in your browser, which is why three checks run on every request:

- **Origin** allowlist. CORS controls who may *read* a response, not who may *send* a
  request, so without this any site you visit could queue jobs.
- **Host** must be loopback, which is what stops a malicious domain pointing its own DNS
  at 127.0.0.1 and being treated as same-origin.
- **Content-Type** must be `application/json` on POST, which forces a preflight so the
  Origin check gets to run before anything executes.

Supporting guards: slugs from a request must match `^[A-Za-z0-9]{1,64}$` before they
reach a filesystem path, `ingest.fetch` refuses non-public addresses and non-http
schemes, and notification text is passed as an argument rather than concatenated into an
AppleScript string.

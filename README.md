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

## Sources

| Source | Cost per board | Notes |
|---|---|---|
| Greenhouse | 1 request | Whole board with descriptions |
| Ashby | 1 request | Whole board with descriptions |
| Lever | 1 request | Descriptions already plain text |
| SmartRecruiters | paginated + 1 per posting | Hydrated only after the title gate |
| Workday | paginated search + 1 per posting | Per-tenant, needs tenant + wd number + site |

## Using it

Open the page. Filter, read, and mark what you apply to. Statuses save in that
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
.venv/bin/python -m jobradar discover --apply   # probe for more boards
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

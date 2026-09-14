"""Paths and every tunable constant, in one place.

Constants live here rather than next to their use so that retuning the pipeline
is one file to read. Anything with a number in it that a future edit might want
to change belongs here, and anything here that is load-bearing carries a comment
naming the symptom you would see if it were wrong.

There is deliberately no environment-variable layer. This project has no secrets
and no deploy targets; it runs from a checkout on a laptop and from a GitHub
Actions runner, and both should behave identically.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SEEDS_DIR = ROOT / "seeds"
PROFILE_DIR = ROOT / "profile"
DATA_DIR = ROOT / "data"
DOCS_DIR = ROOT / "docs"
DOCS_DATA_DIR = DOCS_DIR / "data"

BOARDS_CSV = SEEDS_DIR / "boards.csv"
TITLES_CSV = SEEDS_DIR / "titles.csv"
SKILLS_CSV = SEEDS_DIR / "skills.csv"
ALIASES_CSV = SEEDS_DIR / "aliases.csv"
PROFILE_YAML = PROFILE_DIR / "profile.yaml"

JOBS_JSONL = DATA_DIR / "jobs.jsonl"
PROBES_CSV = DATA_DIR / "probes.csv"
SPONSORS_CSV_GZ = DATA_DIR / "sponsors.csv.gz"
LCA_DIR = DATA_DIR / "lca"

PUBLISH_JOBS_JSON = DOCS_DATA_DIR / "jobs.json"
PUBLISH_META_JSON = DOCS_DATA_DIR / "meta.json"

# ---- HTTP ---- #

USER_AGENT = "jobradar/0.1 (personal job search)"

# Several Workday tenants 403 a bare library User-Agent. They serve the same
# public JSON to a browser-shaped one.
WORKDAY_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

HTTP_TIMEOUT = 30.0
HTTP_ATTEMPTS = 4

# Minimum seconds between requests to one host. The four simple APIs are CDN
# fronted and tolerant. Workday is a per-tenant enterprise deployment with a
# real WAF in front of it, and it is the one that actually blocks.
HOST_MIN_INTERVAL = {"default": 0.4, "myworkdayjobs.com": 1.5}

# Consecutive failures on a single host before the rest of that host's boards
# are skipped for the run. Without this a rate-limited host produces one error
# line per board and a 300-board run spends its time sleeping in backoff.
CIRCUIT_BREAKER_FAILURES = 5

RETRY_AFTER_CAP = 120.0  # Above this, give up on the board rather than stall the run.
FETCH_WORKERS = 4

# ---- Pipeline ---- #

SNIPPET_CHARS = 400

# Workday is a paginated *search*, not a full board listing, so a job missing
# from a run means "did not match this run's search terms", not "closed". These
# age out instead. See refresh.close_missing.
WORKDAY_STALE_DAYS = 21
WORKDAY_MAX_PAGES = 10  # 20 results per page is Workday's hard cap; see sources/workday.py

# ---- Scoring ---- #

# Component ceilings. They sum to 100 before penalties.
MAX_TITLE = 35
MAX_SKILLS = 25
MAX_LOCATION = 15
MAX_SPONSORSHIP = 15
MAX_RECENCY = 10

# Weighted skill hits needed to earn full skill points. A genuine Data Analyst
# posting naming SQL + Python + Excel + Tableau + dashboards + ETL scores
# 3+3+3+3+2+2 = 16, about 22 of 25 points. Drop this to 12 and everything
# saturates at 25; raise it to 30 and nothing clears 15. Retune with
# `jobradar score --histogram`.
SKILL_SATURATION = 18.0

# ---- Sponsorship ---- #

# An employer needs this many certified LCAs in the window AND this many in an
# analyst SOC to read as "strong". Drop CERTIFIED to 5 and every body-shop
# staffing agency in the country reads as a strong sponsor.
SPONSOR_STRONG_CERTIFIED = 25
SPONSOR_STRONG_ANALYST_SOC = 3

# SOC codes that count as analyst work. Widen this and the top-sponsor list
# fills with software engineers, so LCA-driven board discovery starts pointing
# at companies that never post analyst roles.
ANALYST_SOC_CODES = frozenset({
    "15-2051",  # Data Scientists
    "15-2041",  # Statisticians
    "13-1111",  # Management Analysts
    "13-2051",  # Financial and Investment Analysts
    "15-1211",  # Computer Systems Analysts
    "15-2031",  # Operations Research Analysts
    "13-1161",  # Market Research Analysts
    "15-1243",  # Database Architects
})

# ---- Employer matching ---- #

FUZZY_ACCEPT = 88
# Refuse a match when the top two candidates are this close. "ACME SOLUTIONS"
# scoring 94 against ACME SOLUTIONS LLC and 93 against ACME SOLUTIONS GROUP
# means we do not know which, and a confident wrong sponsorship number is worse
# than an honest "no record".
FUZZY_AMBIGUITY_MARGIN = 3
# Short normalized names fuzzy-match nearly everything.
FUZZY_MIN_NAME_LEN = 5

# ---- Publishing ---- #

# Where the page's Refresh link sends you. Overridden by publish.py when the
# repo slug is known from the Actions environment.
ACTIONS_URL = "https://github.com/alliajagbe/jobradar/actions/workflows/refresh.yml"

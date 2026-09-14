"""The 0-100 fit score, and the record of how it was reached.

The score is worth less than the explanation. A number on its own gives you no
way to tell a good ranking from a broken taxonomy, so every component writes an
`explain` row carrying its points, its ceiling and the evidence that earned
them, and the page renders those rows verbatim. If a job is ranked oddly, the
reason is one click away rather than a debugging session.

Scoring is pure: a Job dict and a Profile in, a score and explanation out. That
makes it the cheapest part of the pipeline to test and the part most worth
testing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from . import config, taxonomy

_TITLE_POINTS = {
    "A": config.MAX_TITLE,          # exact core title
    "A-partial": 28,                # core title among other words
    # New-grad programs sit ABOVE adjacent roles on purpose: a rotational
    # analyst programme fits a recent masters graduate better than a generic
    # risk analyst requisition does.
    "C": 30,
    "B": 18,
}


@dataclass(frozen=True)
class Component:
    label: str
    points: float
    max: float
    detail: str

    def as_dict(self) -> dict:
        return {"label": self.label, "points": round(self.points, 1),
                "max": self.max, "detail": self.detail}


@dataclass(frozen=True)
class Scored:
    score: int
    components: list[dict]
    matched_skills: list[str]
    missing_skills: list[str]
    min_years: int | None


# Core skills worth naming as a gap when a posting wants them and the profile
# has no claim on them. Anything the lexicon knows about is fair game, but the
# gap list is only useful if it is short.
_GAP_CANDIDATES = {
    "Snowflake", "Redshift", "Spark", "Airflow", "dbt", "Looker", "Power BI",
    "Alteryx", "SAS", "Databricks", "Azure", "GCP", "AWS", "BigQuery",
}


def score_job(
    *,
    title_tier: str | None,
    title_term: str | None,
    description_text: str,
    location,
    posted_at: str | None,
    sponsorship_signal: str,
    sponsorship_detail: str,
    level_penalty: int,
    level_label: str | None,
    profile_skills: set[str],
    now: datetime | None = None,
) -> Scored:
    now = now or datetime.now(timezone.utc)
    components: list[Component] = []

    # Title
    points = _TITLE_POINTS.get(title_tier or "", 0)
    label = {"A": "tier A exact", "A-partial": "tier A", "C": "new grad programme",
             "B": "adjacent"}.get(title_tier or "", "no match")
    components.append(Component("Title", points, config.MAX_TITLE,
                                f'{label}: "{title_term}"' if title_term else label))

    # Skills
    matched, weight = taxonomy.match_skills(description_text)
    skill_points = config.MAX_SKILLS * min(1.0, weight / config.SKILL_SATURATION)
    components.append(Component(
        "Skills", skill_points, config.MAX_SKILLS,
        ", ".join(matched[:8]) + (f" +{len(matched) - 8} more" if len(matched) > 8 else "")
        or "none matched",
    ))
    missing = sorted((set(matched) & _GAP_CANDIDATES) - profile_skills)

    # Location
    if not location.is_us:
        loc_points, loc_detail = 0.0, "not US"
    elif location.confidence == "low":
        loc_points, loc_detail = 6.0, "US, location unclear"
    elif location.is_remote:
        loc_points, loc_detail = 15.0, "Remote (US)"
    elif location.preferred:
        loc_points, loc_detail = 15.0, location.locations[0] if location.locations else "US"
    elif location.is_hybrid:
        loc_points, loc_detail = 12.0, "hybrid, US"
    else:
        loc_points, loc_detail = 12.0, location.locations[0] if location.locations else "US"
    components.append(Component("Location", loc_points, config.MAX_LOCATION, loc_detail))

    # Sponsorship
    sponsor_points = {"strong": 15.0, "says_yes": 15.0, "some": 10.0,
                      "unknown": 5.0, "never_filed": 2.0}.get(sponsorship_signal, 5.0)
    components.append(Component("Sponsorship", sponsor_points, config.MAX_SPONSORSHIP,
                                sponsorship_detail))

    # Recency
    age_days = _age_days(posted_at, now)
    if age_days is None:
        rec_points, rec_detail = 3.0, "posting date unknown"
    else:
        # Day-level granularity at the short end. The page defaults to a
        # three-day window, so buckets that all resolve to 10 inside that window
        # contribute nothing to the ordering.
        for threshold, pts in ((0, 10.0), (1, 9.0), (2, 7.5), (3, 6.0),
                               (7, 4.0), (14, 2.0), (30, 1.0)):
            if age_days <= threshold:
                rec_points = pts
                break
        else:
            rec_points = 0.0
        rec_detail = ("posted today" if age_days <= 0
                      else "posted yesterday" if age_days == 1
                      else f"posted {age_days} days ago")
    components.append(Component("Recency", rec_points, config.MAX_RECENCY, rec_detail))

    # Penalties
    penalty = 0.0
    details: list[str] = []
    min_years = taxonomy.min_years_required(description_text)
    if min_years is not None:
        if min_years == 5:
            penalty -= 12
            details.append("asks for 5 years")
        elif min_years in (3, 4):
            penalty -= 6
            details.append(f"asks for {min_years} years")
    if level_penalty:
        penalty += level_penalty
        details.append(f"level {level_label}")
    if penalty:
        components.append(Component("Penalties", penalty, 0, "; ".join(details)))

    total = sum(c.points for c in components)
    return Scored(
        score=max(0, min(100, round(total))),
        components=[c.as_dict() for c in components],
        matched_skills=matched,
        missing_skills=missing,
        min_years=min_years,
    )


def _age_days(posted_at: str | None, now: datetime) -> int | None:
    if not posted_at:
        return None
    try:
        dt = datetime.fromisoformat(posted_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0, (now - dt).days)

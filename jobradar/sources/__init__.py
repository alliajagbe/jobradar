"""Registry of ATS adapters, keyed by the name used in seeds/boards.csv."""

from __future__ import annotations

from .ashby import Ashby
from .base import Board, ProbeResult, RawPosting, Source, SourceError
from .greenhouse import Greenhouse
from .lever import Lever
from .smartrecruiters import SmartRecruiters
from .workday import Workday

__all__ = [
    "Board", "ProbeResult", "RawPosting", "Source", "SourceError",
    "Ashby", "Greenhouse", "Lever", "SmartRecruiters", "Workday",
    "build_sources", "PROBEABLE",
]

# Sources whose boards can be found by guessing a slug. Workday needs three
# unknowns and is curated by hand instead.
PROBEABLE = ("greenhouse", "ashby", "lever", "smartrecruiters")


def build_sources(workday_terms: list[str] | None = None) -> dict[str, Source]:
    return {
        "greenhouse": Greenhouse(),
        "ashby": Ashby(),
        "lever": Lever(),
        "smartrecruiters": SmartRecruiters(),
        "workday": Workday(workday_terms),
    }

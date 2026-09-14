"""DOL data handling."""

from __future__ import annotations

import pytest

from jobradar import config
from jobradar.lca import latest_per_year, soc_base


@pytest.mark.parametrize("raw,base", [
    # DOL reports the DETAILED occupation code. Comparing these against bare
    # six-digit codes matches nothing, and the failure is silent: every analyst
    # count lands at zero and the "sponsors often" tier never fires.
    ("15-2051.00", "15-2051"),   # Data Scientists
    ("15-2051.01", "15-2051"),   # Business Intelligence Analysts
    ("15-1211.00", "15-1211"),   # Computer Systems Analysts
    ("13-1111.00", "13-1111"),   # Management Analysts
    ("15-2051", "15-2051"),      # already bare
    (" 15-2041.00 ", "15-2041"),
    (None, ""),
])
def test_soc_base(raw, base):
    assert soc_base(raw) == base


@pytest.mark.parametrize("raw", ["15-2051.00", "15-2051.01", "15-1211.00", "13-1111.00"])
def test_detailed_codes_reach_the_analyst_set(raw):
    assert soc_base(raw) in config.ANALYST_SOC_CODES


def test_one_file_per_fiscal_year():
    """DOL quarterly files are cumulative year to date. Ingesting Q1, Q2 and Q3
    separately triples every count and makes every employer look three times the
    sponsor it is."""
    files = [
        ("LCA_Disclosure_Data_FY2026_Q1.xlsx", "u1"),
        ("LCA_Disclosure_Data_FY2026_Q2.xlsx", "u2"),
        ("LCA_Disclosure_Data_FY2026_Q3.xlsx", "u3"),
        ("LCA_Disclosure_Data_FY2025_Q4.xlsx", "u4"),
        ("LCA_Disclosure_Data_FY2024_Q4.xlsx", "u5"),
    ]
    chosen = latest_per_year(files, years=2)
    assert [n for n, _ in chosen] == [
        "LCA_Disclosure_Data_FY2026_Q3.xlsx",   # highest quarter of FY2026
        "LCA_Disclosure_Data_FY2025_Q4.xlsx",
    ]

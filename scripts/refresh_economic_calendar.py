#!/usr/bin/env python3
"""
refresh_economic_calendar.py — Refresh scripts/economic_calendar.json for a target year.

NFP dates are auto-computed (first Friday of each month — approximates BLS release schedule).
FOMC and CPI dates must be provided via --fomc / --cpi: they are published annually by the
Fed and BLS and don't follow a simple algorithmic rule.

After getting official dates from BLS (https://www.bls.gov/schedule/) and the Fed
(https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm), run:

    python3 scripts/refresh_economic_calendar.py --year 2027 \\
      --fomc 2027-01-28,2027-03-18,2027-04-29,2027-06-16,2027-07-28,2027-09-15,2027-10-27,2027-12-15 \\
      --cpi  2027-01-15,2027-02-11,2027-03-11,2027-04-13,2027-05-12,2027-06-10, \\
             2027-07-14,2027-08-11,2027-09-09,2027-10-13,2027-11-10,2027-12-10

Output: patches scripts/economic_calendar.json in-place, preserving all other years.

Note: NFP first-Friday approximation may differ from BLS official dates by 0-7 days.
      Verify against https://www.bls.gov/schedule/news_release/empsit.htm before running.
"""

import argparse
import json
import os
import sys
from datetime import date, timedelta

CALENDAR_PATH = os.path.join(os.path.dirname(__file__), "economic_calendar.json")


def _first_friday(year: int, month: int) -> date:
    """Return the first Friday of the given month."""
    d = date(year, month, 1)
    # weekday(): Monday=0, Friday=4
    days_until_friday = (4 - d.weekday()) % 7
    return d + timedelta(days=days_until_friday)


def generate_nfp_dates(year: int) -> list[date]:
    """Generate first-Friday-of-month NFP approximations for all 12 months."""
    return [_first_friday(year, month) for month in range(1, 13)]


def _parse_dates(value: str | None) -> list[date]:
    """Parse a comma-separated string of YYYY-MM-DD dates. Returns [] for None/empty."""
    if not value:
        return []
    return [date.fromisoformat(s.strip()) for s in value.split(",") if s.strip()]


def build_entries(
    nfp: list[date],
    cpi: list[date],
    fomc: list[date],
) -> list[dict]:
    """Convert date lists into calendar entry dicts."""
    return [
        {"date": d.isoformat(), "event": event}
        for dates, event in ((nfp, "NFP"), (cpi, "CPI"), (fomc, "FOMC"))
        for d in dates
    ]


def merge_entries(
    existing: list[dict],
    new_entries: list[dict],
    year: int,
) -> list[dict]:
    """Replace all entries for `year` in existing with new_entries. Preserve other years."""
    other_years = [e for e in existing if not e["date"].startswith(str(year))]
    merged = other_years + new_entries
    merged.sort(key=lambda e: e["date"])
    return merged


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--year", type=int, required=True, help="Target year to refresh (e.g. 2027)")
    parser.add_argument("--nfp", type=str, default=None, help="Comma-separated NFP dates (YYYY-MM-DD). Auto-computed if omitted.")
    parser.add_argument("--cpi", type=str, default=None, help="Comma-separated CPI dates (YYYY-MM-DD).")
    parser.add_argument("--fomc", type=str, default=None, help="Comma-separated FOMC dates (YYYY-MM-DD).")
    args = parser.parse_args(argv)

    nfp_dates = _parse_dates(args.nfp) if args.nfp else generate_nfp_dates(args.year)
    cpi_dates = _parse_dates(args.cpi)
    fomc_dates = _parse_dates(args.fomc)

    if args.nfp is None:
        print(f"[NFP] Auto-computed first-Friday-of-month for {args.year}. Verify against BLS official schedule.")

    new_entries = build_entries(nfp_dates, cpi_dates, fomc_dates)

    if os.path.exists(CALENDAR_PATH):
        with open(CALENDAR_PATH) as f:
            existing = json.load(f)
    else:
        existing = []

    merged = merge_entries(existing, new_entries, args.year)

    with open(CALENDAR_PATH, "w") as f:
        json.dump(merged, f, indent=2)
        f.write("\n")

    added = len(new_entries)
    removed = sum(1 for e in existing if e["date"].startswith(str(args.year)))
    print(f"[OK] {CALENDAR_PATH} updated: replaced {removed} entries for {args.year} with {added} new entries.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

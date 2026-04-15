"""
Tests for refresh_economic_calendar.py — 100% coverage target.

Run from repo root:
    PYTHONPATH=scripts pytest scripts/test_refresh_economic_calendar.py -v
"""
import json
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, "scripts")
import refresh_economic_calendar


# ── _first_friday ────────────────────────────────────────────

class TestFirstFriday:
    def test_january_2026_is_jan_2(self):
        # Jan 1 2026 = Thursday → first Friday = Jan 2
        assert refresh_economic_calendar._first_friday(2026, 1) == date(2026, 1, 2)

    def test_february_2026_is_feb_6(self):
        # Feb 1 2026 = Sunday → first Friday = Feb 6
        assert refresh_economic_calendar._first_friday(2026, 2) == date(2026, 2, 6)

    def test_march_2026_is_mar_6(self):
        # Mar 1 2026 = Sunday → first Friday = Mar 6
        assert refresh_economic_calendar._first_friday(2026, 3) == date(2026, 3, 6)

    def test_result_is_always_friday(self):
        for month in range(1, 13):
            d = refresh_economic_calendar._first_friday(2026, month)
            assert d.weekday() == 4, f"Month {month}: {d} is not a Friday"

    def test_result_is_always_in_first_week(self):
        for month in range(1, 13):
            d = refresh_economic_calendar._first_friday(2026, month)
            assert d.day <= 7, f"Month {month}: {d.day} > 7"

    def test_result_is_in_correct_month_and_year(self):
        d = refresh_economic_calendar._first_friday(2027, 6)
        assert d.year == 2027
        assert d.month == 6


# ── generate_nfp_dates ───────────────────────────────────────

class TestGenerateNfpDates:
    def test_returns_12_dates(self):
        assert len(refresh_economic_calendar.generate_nfp_dates(2026)) == 12

    def test_all_in_target_year(self):
        dates = refresh_economic_calendar.generate_nfp_dates(2027)
        assert all(d.year == 2027 for d in dates)

    def test_all_are_fridays(self):
        dates = refresh_economic_calendar.generate_nfp_dates(2026)
        assert all(d.weekday() == 4 for d in dates)

    def test_one_per_month(self):
        dates = refresh_economic_calendar.generate_nfp_dates(2026)
        months = [d.month for d in dates]
        assert months == list(range(1, 13))


# ── _parse_dates ─────────────────────────────────────────────

class TestParseDates:
    def test_valid_comma_separated(self):
        result = refresh_economic_calendar._parse_dates("2027-01-15,2027-02-11")
        assert result == [date(2027, 1, 15), date(2027, 2, 11)]

    def test_single_date(self):
        result = refresh_economic_calendar._parse_dates("2027-03-18")
        assert result == [date(2027, 3, 18)]

    def test_empty_string_returns_empty(self):
        assert refresh_economic_calendar._parse_dates("") == []

    def test_none_returns_empty(self):
        assert refresh_economic_calendar._parse_dates(None) == []

    def test_whitespace_stripped(self):
        result = refresh_economic_calendar._parse_dates("2027-01-15, 2027-02-11")
        assert result == [date(2027, 1, 15), date(2027, 2, 11)]

    def test_invalid_date_raises_value_error(self):
        with pytest.raises(ValueError):
            refresh_economic_calendar._parse_dates("2027-13-01")


# ── build_entries ────────────────────────────────────────────

class TestBuildEntries:
    def test_nfp_entries_tagged_correctly(self):
        nfp = [date(2027, 1, 8)]
        entries = refresh_economic_calendar.build_entries(nfp, [], [])
        assert {"date": "2027-01-08", "event": "NFP"} in entries

    def test_cpi_entries_tagged_correctly(self):
        cpi = [date(2027, 1, 15)]
        entries = refresh_economic_calendar.build_entries([], cpi, [])
        assert {"date": "2027-01-15", "event": "CPI"} in entries

    def test_fomc_entries_tagged_correctly(self):
        fomc = [date(2027, 1, 28)]
        entries = refresh_economic_calendar.build_entries([], [], fomc)
        assert {"date": "2027-01-28", "event": "FOMC"} in entries

    def test_all_three_included(self):
        nfp = [date(2027, 1, 8)]
        cpi = [date(2027, 1, 15)]
        fomc = [date(2027, 1, 28)]
        entries = refresh_economic_calendar.build_entries(nfp, cpi, fomc)
        events = {(e["date"], e["event"]) for e in entries}
        assert ("2027-01-08", "NFP") in events
        assert ("2027-01-15", "CPI") in events
        assert ("2027-01-28", "FOMC") in events

    def test_empty_lists_returns_empty(self):
        assert refresh_economic_calendar.build_entries([], [], []) == []


# ── merge_entries ────────────────────────────────────────────

class TestMergeEntries:
    def test_preserves_other_years(self):
        existing = [
            {"date": "2026-01-07", "event": "NFP"},
            {"date": "2027-01-03", "event": "NFP"},
        ]
        new_entries = [{"date": "2027-02-07", "event": "NFP"}]
        result = refresh_economic_calendar.merge_entries(existing, new_entries, 2027)
        dates = [e["date"] for e in result]
        assert "2026-01-07" in dates

    def test_replaces_existing_target_year_entries(self):
        existing = [
            {"date": "2027-01-03", "event": "NFP"},
            {"date": "2027-01-15", "event": "CPI"},
        ]
        new_entries = [{"date": "2027-02-07", "event": "NFP"}]
        result = refresh_economic_calendar.merge_entries(existing, new_entries, 2027)
        dates = [e["date"] for e in result]
        assert "2027-01-03" not in dates
        assert "2027-02-07" in dates

    def test_output_sorted_by_date(self):
        existing = [{"date": "2026-06-01", "event": "NFP"}]
        new_entries = [
            {"date": "2027-03-07", "event": "NFP"},
            {"date": "2027-01-02", "event": "NFP"},
        ]
        result = refresh_economic_calendar.merge_entries(existing, new_entries, 2027)
        result_dates = [e["date"] for e in result]
        assert result_dates == sorted(result_dates)

    def test_empty_existing_with_new_entries(self):
        new_entries = [{"date": "2027-01-08", "event": "NFP"}]
        result = refresh_economic_calendar.merge_entries([], new_entries, 2027)
        assert result == new_entries


# ── main ─────────────────────────────────────────────────────

class TestMain:
    def test_auto_nfp_no_existing_file(self, tmp_path, monkeypatch, capsys):
        cal_path = str(tmp_path / "calendar.json")
        monkeypatch.setattr(refresh_economic_calendar, "CALENDAR_PATH", cal_path)
        ret = refresh_economic_calendar.main(["--year", "2027"])
        assert ret == 0
        data = json.loads(Path(cal_path).read_text())
        assert len(data) == 12
        assert all(e["event"] == "NFP" for e in data)

    def test_existing_calendar_preserved(self, tmp_path, monkeypatch, capsys):
        cal_path = str(tmp_path / "calendar.json")
        Path(cal_path).write_text(json.dumps([{"date": "2026-01-07", "event": "NFP"}]))
        monkeypatch.setattr(refresh_economic_calendar, "CALENDAR_PATH", cal_path)
        refresh_economic_calendar.main(["--year", "2027"])
        data = json.loads(Path(cal_path).read_text())
        assert any(e["date"] == "2026-01-07" for e in data)

    def test_custom_nfp_replaces_auto(self, tmp_path, monkeypatch, capsys):
        cal_path = str(tmp_path / "calendar.json")
        monkeypatch.setattr(refresh_economic_calendar, "CALENDAR_PATH", cal_path)
        ret = refresh_economic_calendar.main(["--year", "2027", "--nfp", "2027-01-08"])
        assert ret == 0
        data = json.loads(Path(cal_path).read_text())
        assert {"date": "2027-01-08", "event": "NFP"} in data

    def test_fomc_and_cpi_included(self, tmp_path, monkeypatch, capsys):
        cal_path = str(tmp_path / "calendar.json")
        monkeypatch.setattr(refresh_economic_calendar, "CALENDAR_PATH", cal_path)
        refresh_economic_calendar.main([
            "--year", "2027",
            "--fomc", "2027-01-28",
            "--cpi", "2027-01-15",
        ])
        data = json.loads(Path(cal_path).read_text())
        events = {(e["date"], e["event"]) for e in data}
        assert ("2027-01-28", "FOMC") in events
        assert ("2027-01-15", "CPI") in events

    def test_auto_nfp_prints_bls_warning(self, tmp_path, monkeypatch, capsys):
        cal_path = str(tmp_path / "calendar.json")
        monkeypatch.setattr(refresh_economic_calendar, "CALENDAR_PATH", cal_path)
        refresh_economic_calendar.main(["--year", "2027"])
        assert "BLS" in capsys.readouterr().out

    def test_prints_ok_summary(self, tmp_path, monkeypatch, capsys):
        cal_path = str(tmp_path / "calendar.json")
        monkeypatch.setattr(refresh_economic_calendar, "CALENDAR_PATH", cal_path)
        refresh_economic_calendar.main(["--year", "2027"])
        assert "[OK]" in capsys.readouterr().out

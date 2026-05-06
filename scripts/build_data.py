#!/usr/bin/env python3
"""Build data/data.json from a layoffs CSV source.

Source priority (first one that yields a usable CSV wins):
  1. $LAYOFFS_CSV_URL                     (override; set via repo Variable or Secret)
  2. URLs in scripts/sources.txt          (one per line, '#' comments OK)
  3. Local fallback at data/raw_layoffs.csv (committed seed)

Output schema (data/data.json):
{
  "generated_at":     "2026-05-06T12:34:56Z",
  "source_url":       "...",
  "source_rows":      3487,
  "industries":       ["Hardware", "Retail", ...],          # sorted by total desc
  "months":           ["2020-03", "2020-04", ..., "2026-04"],
  "monthly_total":    [123, 456, ...],                       # parallel to months
  "cumulative_by_industry": {
      "Hardware": [0, 12, 12, 350, ...],                     # parallel to months
      ...
  },
  "totals_by_industry": {"Hardware": 50000, ...}
}

The chart consumes ONLY data.json; markers are loaded separately from markers.json.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import os
import pathlib
import sys
import urllib.request
from collections import defaultdict
from typing import Iterable

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DATA_OUT = DATA_DIR / "data.json"
SOURCES_FILE = ROOT / "scripts" / "sources.txt"
LOCAL_SEED = DATA_DIR / "raw_layoffs.csv"


# ---------- fetching --------------------------------------------------------

def candidate_urls() -> list[str]:
    urls: list[str] = []
    env = os.environ.get("LAYOFFS_CSV_URL", "").strip()
    if env:
        urls.append(env)
    if SOURCES_FILE.exists():
        for line in SOURCES_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                urls.append(line)
    return urls


def fetch(url: str, timeout: int = 30) -> str | None:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "llm-layoffs-tracker/1.0 (+https://github.com/)",
            "Accept": "text/csv,application/csv,text/plain,*/*",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except Exception as exc:  # noqa: BLE001 - we want broad fallback
        print(f"  ! fetch failed: {exc}", file=sys.stderr)
        return None
    text = raw.decode("utf-8", errors="replace")
    # crude sanity check: must look like CSV with a header
    if "," not in text.splitlines()[0]:
        print("  ! response did not look like CSV", file=sys.stderr)
        return None
    return text


def load_csv_text() -> tuple[str, str]:
    """Return (csv_text, source_label). Tries network first, then seed."""
    for url in candidate_urls():
        print(f"-> trying {url}", file=sys.stderr)
        text = fetch(url)
        if text:
            print(f"   OK ({len(text):,} bytes)", file=sys.stderr)
            return text, url
    if LOCAL_SEED.exists():
        print(f"-> falling back to local seed {LOCAL_SEED}", file=sys.stderr)
        return LOCAL_SEED.read_text(), str(LOCAL_SEED.relative_to(ROOT))
    raise SystemExit("No layoffs CSV could be fetched and no local seed exists.")


# ---------- normalising -----------------------------------------------------

# Maps various column names found in different mirrors to a canonical name.
COLUMN_ALIASES = {
    "company": ["company", "Company"],
    "industry": ["industry", "Industry"],
    "date": ["date", "Date", "date_layoff", "Date_layoff"],
    "total_laid_off": [
        "total_laid_off", "Laid_Off", "Laid_Off_Count",
        "# Laid Off", "Number Laid Off", "laid_off",
    ],
    "location": ["location", "Location_HQ", "Location"],
    "country": ["country", "Country"],
}


def pick_columns(header: list[str]) -> dict[str, str | None]:
    lookup = {h.strip(): h for h in header}
    chosen: dict[str, str | None] = {}
    for canonical, names in COLUMN_ALIASES.items():
        chosen[canonical] = next((lookup[n] for n in names if n in lookup), None)
    return chosen


def parse_date(s: str) -> dt.date | None:
    s = (s or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def parse_count(s: str) -> int | None:
    s = (s or "").strip().replace(",", "")
    if not s:
        return None
    try:
        n = int(float(s))
    except ValueError:
        return None
    return n if n > 0 else None


def normalise_industry(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return "Other"
    # Title-case with a few hand-mapped fixes for consistent labels.
    fixes = {
        "Ai": "AI",
        "Hr": "HR",
        "Saas": "SaaS",
        "It": "IT",
    }
    out = " ".join(w.capitalize() for w in raw.split())
    return fixes.get(out, out)


# ---------- aggregation -----------------------------------------------------

def month_key(d: dt.date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def month_range(start: str, end: str) -> list[str]:
    sy, sm = map(int, start.split("-"))
    ey, em = map(int, end.split("-"))
    out = []
    y, m = sy, sm
    while (y, m) <= (ey, em):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            m = 1
            y += 1
    return out


def aggregate(rows: Iterable[dict]) -> dict:
    by_month_industry: dict[tuple[str, str], int] = defaultdict(int)
    n_rows_used = 0
    n_rows_total = 0
    for row in rows:
        n_rows_total += 1
        d = parse_date(row.get("date") or "")
        n = parse_count(row.get("total_laid_off") or "")
        if not d or not n:
            continue
        ind = normalise_industry(row.get("industry") or "")
        by_month_industry[(month_key(d), ind)] += n
        n_rows_used += 1

    if not by_month_industry:
        raise SystemExit("No usable rows after parsing — check source columns.")

    months_present = sorted({mk for (mk, _) in by_month_industry})
    months = month_range(months_present[0], months_present[-1])

    industries_total: dict[str, int] = defaultdict(int)
    for (mk, ind), n in by_month_industry.items():
        industries_total[ind] += n
    industries_sorted = sorted(industries_total, key=lambda k: -industries_total[k])

    cumulative: dict[str, list[int]] = {ind: [] for ind in industries_sorted}
    monthly_total: list[int] = []
    running: dict[str, int] = {ind: 0 for ind in industries_sorted}
    for mk in months:
        m_total = 0
        for ind in industries_sorted:
            n = by_month_industry.get((mk, ind), 0)
            running[ind] += n
            cumulative[ind].append(running[ind])
            m_total += n
        monthly_total.append(m_total)

    return {
        "industries": industries_sorted,
        "months": months,
        "monthly_total": monthly_total,
        "cumulative_by_industry": cumulative,
        "totals_by_industry": dict(industries_total),
        "_rows_used": n_rows_used,
        "_rows_total": n_rows_total,
    }


# ---------- main ------------------------------------------------------------

def main() -> int:
    csv_text, source_label = load_csv_text()
    reader = csv.reader(io.StringIO(csv_text))
    try:
        header = next(reader)
    except StopIteration:
        raise SystemExit("Empty CSV.")
    cols = pick_columns(header)
    missing = [k for k in ("date", "total_laid_off") if not cols.get(k)]
    if missing:
        raise SystemExit(f"Source CSV missing required columns: {missing}. Header was: {header}")

    rows = []
    name_to_index = {h: i for i, h in enumerate(header)}
    for record in reader:
        if not record:
            continue
        out = {}
        for canonical, src_name in cols.items():
            if src_name and src_name in name_to_index:
                idx = name_to_index[src_name]
                out[canonical] = record[idx] if idx < len(record) else ""
        rows.append(out)

    agg = aggregate(rows)
    payload = {
        "generated_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0, tzinfo=None).isoformat() + "Z",
        "source_url": source_label,
        "source_rows": agg.pop("_rows_total"),
        "rows_used": agg.pop("_rows_used"),
        **agg,
    }
    DATA_OUT.write_text(json.dumps(payload, separators=(",", ":")))
    print(
        f"OK: {payload['rows_used']:,}/{payload['source_rows']:,} rows -> "
        f"{len(payload['industries'])} industries, "
        f"{len(payload['months'])} months "
        f"({payload['months'][0]}..{payload['months'][-1]})",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

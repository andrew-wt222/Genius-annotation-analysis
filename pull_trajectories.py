"""Pull monthly trajectories per artist for the survivability experiment.

Runs three Mixpanel segmentation queries broken down by Primary Artist + month:
  1. song:load          (total events -> page views)
  2. song:load          (unique users)
  3. song:open_annotation (total events)

Window: May 2024 (when Primary Artist instrumentation began) -> today.
Chunks into 2-month windows to avoid 504 gateway timeouts.

Output: trajectories.json
  {
    "built_at": "...",
    "from_date": "2024-05-01",
    "to_date": "...",
    "artists": {
      "Artist Name": {
        "views_by_month":  {"2024-05": 12345, ...},
        "users_by_month":  {"2024-05": 6789,  ...},
        "annos_by_month":  {"2024-05": 234,   ...}
      },
      ...
    }
  }

Run once, ~20 min. Output feeds survivability_test.py.
"""

import argparse
import json
import logging
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("trajectories")

BASE_DIR = Path(__file__).parent
OUT_FILE = BASE_DIR / "trajectories.json"
CHECKPOINT_FILE = BASE_DIR / "trajectories.checkpoint.json"

MIXPANEL_SEGMENTATION_URL = "https://mixpanel.com/api/2.0/segmentation"
DEFAULT_FROM = date(2024, 5, 1)
CHUNK_DAYS = 60
MAX_RETRIES = 3


def _query_chunk(event, from_date, to_date, type_="general", attempt=0):
    params = {
        "event": event,
        "from_date": from_date.isoformat(),
        "to_date": to_date.isoformat(),
        "on": 'properties["Primary Artist"]',
        "type": type_,
        "limit": 50000,
        "unit": "month",
    }

    logger.info("  [%s/%s] %s -> %s ...", event, type_, from_date, to_date)
    try:
        resp = requests.get(
            MIXPANEL_SEGMENTATION_URL,
            params=params,
            auth=(config.MIXPANEL_API_SECRET, ""),
            timeout=300,
        )
    except requests.Timeout:
        if attempt < MAX_RETRIES:
            wait = 2 ** (attempt + 1)
            logger.warning("    Timeout. Retry in %ds", wait)
            time.sleep(wait)
            return _query_chunk(event, from_date, to_date, type_, attempt + 1)
        raise

    if resp.status_code in (502, 503, 504):
        if attempt < MAX_RETRIES:
            wait = 2 ** (attempt + 1)
            logger.warning("    %d. Retry in %ds", resp.status_code, wait)
            time.sleep(wait)
            return _query_chunk(event, from_date, to_date, type_, attempt + 1)

    if resp.status_code != 200:
        logger.error("    Mixpanel returned %d: %s", resp.status_code, resp.text[:300])
    resp.raise_for_status()

    data = resp.json().get("data", {})
    values = data.get("values", {})

    out = defaultdict(dict)  # artist -> {month_str: count}
    for name, per_period in values.items():
        if not name or name in ("$overall", "undefined"):
            continue
        if not isinstance(per_period, dict):
            continue
        for date_str, count in per_period.items():
            if not count:
                continue
            month_key = date_str[:7]  # "2024-05-01T..." -> "2024-05"
            out[name][month_key] = out[name].get(month_key, 0) + int(count)
    logger.info("    -> %d artists", len(out))
    return dict(out)


def _make_chunks(from_date, to_date, chunk_days):
    chunks = []
    cs = from_date
    while cs <= to_date:
        ce = min(cs + timedelta(days=chunk_days - 1), to_date)
        chunks.append((cs, ce))
        cs = ce + timedelta(days=1)
    return chunks


def _merge_into(dest, src):
    for artist, months in src.items():
        bucket = dest.setdefault(artist, {})
        for m, c in months.items():
            bucket[m] = bucket.get(m, 0) + c


def _save_checkpoint(state):
    with open(CHECKPOINT_FILE, "w") as f:
        json.dump(state, f)


def _load_checkpoint():
    if CHECKPOINT_FILE.exists():
        with open(CHECKPOINT_FILE) as f:
            return json.load(f)
    return None


def pull(from_date=DEFAULT_FROM, to_date=None, min_total_views=500):
    if to_date is None:
        to_date = datetime.utcnow().date()

    chunks = _make_chunks(from_date, to_date, CHUNK_DAYS)
    logger.info("Pulling %s -> %s in %d chunks of %d days",
                from_date, to_date, len(chunks), CHUNK_DAYS)

    pulls = [
        ("song:load", "general", "views_by_month"),
        ("song:load", "unique",  "users_by_month"),
        ("song:open_annotation", "general", "annos_by_month"),
    ]

    # Resume from checkpoint
    state = _load_checkpoint() or {"completed": [], "data": {}}
    completed_set = set(tuple(c) for c in state["completed"])
    data = {key: state["data"].get(key, {}) for _, _, key in pulls}

    for event, type_, key in pulls:
        for cs, ce in chunks:
            tag = (event, type_, cs.isoformat(), ce.isoformat())
            if tag in completed_set:
                logger.info("[skip] %s %s %s..%s (cached)", event, type_, cs, ce)
                continue

            chunk = _query_chunk(event, cs, ce, type_)
            _merge_into(data[key], chunk)
            completed_set.add(tag)
            state["completed"] = [list(t) for t in completed_set]
            state["data"] = data
            _save_checkpoint(state)
            time.sleep(1)

    # Build per-artist trajectory
    artists = {}
    all_artists = set()
    for _, _, key in pulls:
        all_artists.update(data[key].keys())

    for name in all_artists:
        views = data["views_by_month"].get(name, {})
        users = data["users_by_month"].get(name, {})
        annos = data["annos_by_month"].get(name, {})
        total_views = sum(views.values())
        if total_views < min_total_views:
            continue
        artists[name] = {
            "views_by_month": views,
            "users_by_month": users,
            "annos_by_month": annos,
            "total_views": total_views,
        }

    out = {
        "built_at": datetime.now(timezone.utc).isoformat(),
        "from_date": from_date.isoformat(),
        "to_date": to_date.isoformat(),
        "min_total_views": min_total_views,
        "total_artists": len(artists),
        "artists": artists,
    }

    with open(OUT_FILE, "w") as f:
        json.dump(out, f, indent=2)

    logger.info("Wrote %d artists (filtered to >=%d total views) to %s",
                len(artists), min_total_views, OUT_FILE)

    if CHECKPOINT_FILE.exists():
        CHECKPOINT_FILE.unlink()
        logger.info("Cleared checkpoint")

    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-date", default=DEFAULT_FROM.isoformat())
    parser.add_argument("--to-date", default=None)
    parser.add_argument("--min-total-views", type=int, default=500)
    args = parser.parse_args()

    fd = date.fromisoformat(args.from_date)
    td = date.fromisoformat(args.to_date) if args.to_date else None
    pull(from_date=fd, to_date=td, min_total_views=args.min_total_views)

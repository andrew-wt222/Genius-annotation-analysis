"""Build the Artist Universe index.

Queries Mixpanel's segmentation endpoint to aggregate song:load events by
Primary Artist. Splits the time window into 30-day chunks to avoid
gateway timeouts, then merges results.

Output: artist_universe.json — the lookup table powering the Signing Board.

Usage:
    python build_universe.py                 # default: 90 days, min 50 views
    python build_universe.py --days 30
    python build_universe.py --min-views 200
"""

import argparse
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("universe")

BASE_DIR = Path(__file__).parent
UNIVERSE_FILE = BASE_DIR / "artist_universe.json"

MIXPANEL_SEGMENTATION_URL = "https://mixpanel.com/api/2.0/segmentation"
CHUNK_DAYS = 30
MAX_RETRIES = 3


def _query_chunk(from_date, to_date, attempt=0):
    params = {
        "event": "song:load",
        "from_date": from_date.isoformat(),
        "to_date": to_date.isoformat(),
        "on": 'properties["Primary Artist"]',
        "type": "general",
        "limit": 50000,
        "unit": "month",
    }

    logger.info("  Chunk %s to %s ...", from_date, to_date)
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
            logger.warning("  Timeout, retrying in %ds...", wait)
            time.sleep(wait)
            return _query_chunk(from_date, to_date, attempt + 1)
        raise

    if resp.status_code == 429:
        wait = 30 * (attempt + 1)
        logger.warning("  Rate limited (429). Waiting %ds...", wait)
        time.sleep(wait)
        if attempt < MAX_RETRIES + 2:
            return _query_chunk(from_date, to_date, attempt + 1)

    if resp.status_code in (502, 503, 504):
        if attempt < MAX_RETRIES:
            wait = 2 ** (attempt + 1)
            logger.warning("  Got %d, retrying in %ds...", resp.status_code, wait)
            time.sleep(wait)
            return _query_chunk(from_date, to_date, attempt + 1)

    if resp.status_code != 200:
        logger.error("  Mixpanel returned %d: %s", resp.status_code, resp.text[:500])
    resp.raise_for_status()

    data = resp.json().get("data", {})
    values = data.get("values", {})

    chunk = {}
    for name, per_period in values.items():
        if not name or name in ("$overall", "undefined"):
            continue
        total = sum(per_period.values()) if isinstance(per_period, dict) else int(per_period or 0)
        if total > 0:
            chunk[name] = total

    logger.info("  Got %d artists in this chunk", len(chunk))
    return chunk


CHECKPOINT = BASE_DIR / "universe_checkpoint.json"


def _load_checkpoint():
    if CHECKPOINT.exists():
        with open(CHECKPOINT) as f:
            return json.load(f)
    return {"done": [], "totals": {}}


def _save_checkpoint(state):
    with open(CHECKPOINT, "w") as f:
        json.dump(state, f)


def build_universe(days=90, min_views=50):
    to_date = datetime.utcnow().date()
    from_date = to_date - timedelta(days=days)

    chunks = []
    chunk_start = from_date
    while chunk_start < to_date:
        chunk_end = min(chunk_start + timedelta(days=CHUNK_DAYS), to_date)
        chunks.append((chunk_start, chunk_end))
        chunk_start = chunk_end + timedelta(days=1)

    logger.info("Querying Mixpanel in %d chunks of %d days each (%s to %s)",
                len(chunks), CHUNK_DAYS, from_date, to_date)

    state = _load_checkpoint()
    done_set = set(state["done"])
    totals = state["totals"]

    if totals:
        logger.info("Resuming from checkpoint (%d chunks done, %d artists so far)",
                    len(done_set), len(totals))

    for i, (cs, ce) in enumerate(chunks, 1):
        tag = f"{cs}_{ce}"
        if tag in done_set:
            logger.info("Chunk %d/%d: %s..%s (cached, skip)", i, len(chunks), cs, ce)
            continue
        logger.info("Chunk %d/%d:", i, len(chunks))
        chunk = _query_chunk(cs, ce)
        for name, count in chunk.items():
            totals[name] = totals.get(name, 0) + count
        done_set.add(tag)
        state["done"] = list(done_set)
        state["totals"] = totals
        _save_checkpoint(state)
        time.sleep(5)

    # Filter and sort
    artists = [
        {"artist_name": name, "views_90d": count}
        for name, count in totals.items()
        if count >= min_views
    ]
    artists.sort(key=lambda x: x["views_90d"], reverse=True)

    out = {
        "built_at": datetime.now(timezone.utc).isoformat(),
        "days": days,
        "min_views_filter": min_views,
        "total_artists": len(artists),
        "artists": artists,
    }

    with open(UNIVERSE_FILE, "w") as f:
        json.dump(out, f, indent=2)

    logger.info("Wrote %d artists to %s", len(artists), UNIVERSE_FILE)

    if CHECKPOINT.exists():
        CHECKPOINT.unlink()
        logger.info("Cleared checkpoint")

    if artists:
        buckets = [
            ("mainstream (>100K views)", sum(1 for a in artists if a["views_90d"] > 100_000)),
            ("mid-tier (10K-100K)", sum(1 for a in artists if 10_000 <= a["views_90d"] <= 100_000)),
            ("emerging (1K-10K)", sum(1 for a in artists if 1_000 <= a["views_90d"] < 10_000)),
            ("micro (<1K)", sum(1 for a in artists if a["views_90d"] < 1_000)),
        ]
        logger.info("Distribution:")
        for label, count in buckets:
            logger.info("  %-24s %5d", label, count)

    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--min-views", type=int, default=50)
    args = parser.parse_args()
    build_universe(days=args.days, min_views=args.min_views)

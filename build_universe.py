"""Build the Artist Universe index.

Runs ONE Mixpanel segmentation query that aggregates song:load events by
`Primary Artist` over the last N days. Output: artist_universe.json — the
"who exists at what volume" lookup table that powers on-demand
classification in the Signing Board dashboard.

This is a cheap query (single aggregation, not event export) that should
finish in a couple of minutes even for a 90-day window.

Usage:
    python build_universe.py                 # default: 90 days, min 50 views
    python build_universe.py --days 30
    python build_universe.py --min-views 200
"""

import argparse
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("universe")

BASE_DIR = Path(__file__).parent
UNIVERSE_FILE = BASE_DIR / "artist_universe.json"

MIXPANEL_SEGMENTATION_URL = "https://mixpanel.com/api/2.0/segmentation"


def build_universe(days=90, min_views=50):
    to_date = datetime.utcnow().date()
    from_date = to_date - timedelta(days=days)

    params = {
        "event": "song:load",
        "from_date": from_date.isoformat(),
        "to_date": to_date.isoformat(),
        "on": 'properties["Primary Artist"]',
        "type": "general",
        "limit": 50000,
        "unit": "day",
    }

    logger.info("Querying Mixpanel segmentation for song:load by Primary Artist")
    logger.info("Window: %s to %s (%d days)", from_date, to_date, days)
    resp = requests.get(
        MIXPANEL_SEGMENTATION_URL,
        params=params,
        auth=(config.MIXPANEL_API_SECRET, ""),
        timeout=600,
    )

    if resp.status_code != 200:
        logger.error("Mixpanel returned %d: %s", resp.status_code, resp.text[:500])
    resp.raise_for_status()

    payload = resp.json()
    data = payload.get("data", {})
    values = data.get("values", {})

    artists = []
    for name, per_day in values.items():
        if not name:
            continue
        if name in ("$overall", "undefined"):
            continue
        total = sum(per_day.values()) if isinstance(per_day, dict) else int(per_day or 0)
        if total < min_views:
            continue
        artists.append({"artist_name": name, "views_90d": int(total)})

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

    # Quick distribution summary
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

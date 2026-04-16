"""Batch-classify artists for the A&R Signing Board.

Runs _aggregate_artist_data for the artists in data_cache.json and writes
their full quadrant classification to classifications.json.

Resumable: re-running skips artists already in the file unless --force is
passed. Checkpoints every 10 artists.

Usage:
    python classify_all.py                    # all cached artists, 90 days
    python classify_all.py --limit 50         # first 50 only
    python classify_all.py --days 30 --force  # recompute everything
"""

import argparse
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from app import _aggregate_artist_data

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
)
logger = logging.getLogger("classify_all")

BASE_DIR = Path(__file__).parent
DATA_CACHE = BASE_DIR / "data_cache.json"
CLASSIFICATIONS = BASE_DIR / "classifications.json"


def _load_existing():
    if CLASSIFICATIONS.exists():
        with open(CLASSIFICATIONS) as f:
            return json.load(f)
    return {"classified_at": None, "days": None, "artists": []}


def _save(state):
    state["classified_at"] = datetime.now(timezone.utc).isoformat()
    with open(CLASSIFICATIONS, "w") as f:
        json.dump(state, f, indent=2)


def classify_all(days=90, limit=None, force=False, sleep_between=1.0):
    with open(DATA_CACHE) as f:
        cache = json.load(f)

    artists = cache.get("artists", [])
    if limit:
        artists = artists[:limit]

    state = _load_existing()
    state["days"] = days
    if force:
        state["artists"] = []
    done = {a["artist"] for a in state["artists"]}

    total = len(artists)
    logger.info("Classifying %d artists over %d-day window (already done: %d)",
                total, days, len(done))

    for i, a in enumerate(artists, 1):
        name = a["artist_name"]
        if name in done:
            logger.info("[%d/%d] %s — cached, skip", i, total, name)
            continue

        logger.info("[%d/%d] %s — classifying...", i, total, name)
        try:
            result = _aggregate_artist_data(name, days)
        except Exception as exc:
            logger.exception("  FAILED %s: %s", name, exc)
            continue

        if result.get("error"):
            logger.warning("  skipped: %s", result["error"])
            continue

        c = result["classification"]
        songs = result.get("songs") or []
        entry = {
            "artist": name,
            "quadrant": c["quadrant"],
            "quadrant_name": c["name"],
            "deal": c["deal"],
            "thesis": c["thesis"],
            "vpu": c["vpu"],
            "spu": c["spu"],
            "aor": c["aor"],
            "unique_users": c["unique_users"],
            "total_page_views": result["total_page_views"],
            "total_annotation_opens": result["total_annotation_opens"],
            "unique_songs": result["unique_songs"],
            "unique_annotations": result["unique_annotations"],
            "top_song": songs[0]["song_title"] if songs else "",
            "engagement_score": round(c["vpu"] * c["aor"], 4),
        }

        state["artists"] = [x for x in state["artists"] if x["artist"] != name]
        state["artists"].append(entry)

        logger.info("  %s: VPU=%.2f SPU=%.2f AOR=%.3f users=%s views=%s",
                    c["quadrant"], c["vpu"], c["spu"], c["aor"],
                    f"{c['unique_users']:,}", f"{result['total_page_views']:,}")

        if i % 10 == 0:
            _save(state)
            logger.info("  checkpoint — %d classified", len(state["artists"]))

        time.sleep(sleep_between)

    _save(state)
    logger.info("Done. %d artists classified in total.", len(state["artists"]))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=90, help="lookback window")
    parser.add_argument("--limit", type=int, default=None, help="classify only top N from cache")
    parser.add_argument("--force", action="store_true", help="recompute everything")
    parser.add_argument("--sleep", type=float, default=1.0, help="seconds between artist queries")
    args = parser.parse_args()
    classify_all(days=args.days, limit=args.limit, force=args.force, sleep_between=args.sleep)

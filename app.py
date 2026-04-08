"""Flask application serving the Genius Annotation Analysis dashboard."""

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, render_template

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

CACHE_FILE = Path(__file__).parent / "data_cache.json"

# In-memory cache so we don't re-fetch on every page load
_cache = {
    "data": None,
    "fetched_at": None,
    "ttl_seconds": 600,  # 10-minute cache
}


def _load_from_cache_file():
    """Load pre-fetched data from the JSON cache file."""
    if CACHE_FILE.exists():
        with open(CACHE_FILE) as f:
            return json.load(f)
    return None


def _build_dashboard_data():
    """Try live API fetch, fall back to pre-built cache file."""
    # First try the cache file (works when live APIs are unreachable)
    cached = _load_from_cache_file()
    if cached:
        logger.info("Loaded data from cache file (%d artists, %d songs)",
                     len(cached.get("artists", [])),
                     len(cached.get("top_lyrics", [])))
        return cached

    # Fall back to live API fetch
    try:
        import mixpanel_client
        import genius_client

        events = mixpanel_client.fetch_annotation_events()
        if not events:
            return {
                "error": None,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "total_events": 0,
                "unique_annotations": 0,
                "annotations": [],
                "artists": [],
                "top_lyrics": [],
            }

        counts = mixpanel_client.aggregate_annotation_counts(events)
        top_annotation_ids = list(counts.keys())[:200]
        genius_data = genius_client.fetch_annotations_batch(top_annotation_ids)

        annotations = []
        for aid, open_count in counts.items():
            meta = genius_data.get(aid)
            annotations.append({
                "annotation_id": aid,
                "open_count": open_count,
                "lyric_fragment": meta["lyric_fragment"] if meta else "",
                "annotation_text": meta["annotation_text"] if meta else "",
                "song_title": meta["song_title"] if meta else "Unknown",
                "song_url": meta["song_url"] if meta else "",
                "artist_name": meta["artist_name"] if meta else "Unknown",
                "votes_total": meta["votes_total"] if meta else 0,
                "verified": meta["verified"] if meta else False,
            })

        artist_map = {}
        for a in annotations:
            name = a["artist_name"]
            if name not in artist_map:
                artist_map[name] = {
                    "artist_name": name,
                    "total_opens": 0,
                    "annotation_count": 0,
                    "top_song": "",
                }
            artist_map[name]["total_opens"] += a["open_count"]
            artist_map[name]["annotation_count"] += 1
            if not artist_map[name]["top_song"] or a["open_count"] > 0:
                artist_map[name]["top_song"] = a["song_title"]

        artists = sorted(
            artist_map.values(), key=lambda x: x["total_opens"], reverse=True
        )
        top_lyrics = sorted(annotations, key=lambda x: x["open_count"], reverse=True)[:100]

        return {
            "error": None,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "total_events": len(events),
            "unique_annotations": len(counts),
            "annotations": annotations,
            "artists": artists[:100],
            "top_lyrics": top_lyrics,
        }
    except Exception as exc:
        logger.warning("Live API fetch failed: %s", exc)
        return {
            "error": f"Live API unavailable and no cache file found: {exc}",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "total_events": 0,
            "unique_annotations": 0,
            "annotations": [],
            "artists": [],
            "top_lyrics": [],
        }


def _get_data(force_refresh=False):
    """Return cached data or fetch fresh data."""
    now = time.time()
    if (
        not force_refresh
        and _cache["data"]
        and _cache["fetched_at"]
        and (now - _cache["fetched_at"]) < _cache["ttl_seconds"]
    ):
        return _cache["data"]

    data = _build_dashboard_data()
    _cache["data"] = data
    _cache["fetched_at"] = now
    return data


@app.route("/")
def dashboard():
    return render_template("dashboard.html")


@app.route("/api/data")
def api_data():
    data = _get_data()
    return jsonify(data)


@app.route("/api/refresh")
def api_refresh():
    data = _get_data(force_refresh=True)
    return jsonify(data)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)

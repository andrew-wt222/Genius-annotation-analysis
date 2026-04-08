"""Flask application serving the Genius Annotation Analysis dashboard."""

import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask, jsonify, render_template, request

import config
import genius_client
import mixpanel_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

BASE_DIR = Path(__file__).parent
MIXPANEL_CACHE = BASE_DIR / "data_cache.json"
ENRICHED_CACHE = BASE_DIR / "enriched_cache.json"

# Mixpanel JQL / Insights API for live queries
MIXPANEL_INSIGHTS_URL = "https://mixpanel.com/api/2.0/insights"

_data = {
    "mixpanel": None,
    "enriched": {},
    "enriching": False,
    "enrich_progress": 0,
    "enrich_total": 0,
}


def _load_mixpanel_cache():
    if MIXPANEL_CACHE.exists():
        with open(MIXPANEL_CACHE) as f:
            _data["mixpanel"] = json.load(f)
        logger.info("Loaded Mixpanel cache")


def _load_enriched_cache():
    if ENRICHED_CACHE.exists():
        with open(ENRICHED_CACHE) as f:
            _data["enriched"] = json.load(f)
        logger.info("Loaded enriched cache: %d annotations", len(_data["enriched"]))


def _save_enriched_cache():
    with open(ENRICHED_CACHE, "w") as f:
        json.dump(_data["enriched"], f, indent=2)


def _enrich_annotations_background(annotation_ids, max_fetch=150):
    _data["enriching"] = True
    ids_to_fetch = [aid for aid in annotation_ids[:max_fetch] if aid not in _data["enriched"]]
    _data["enrich_total"] = len(ids_to_fetch)
    _data["enrich_progress"] = 0

    if not ids_to_fetch:
        _data["enriching"] = False
        return

    for i, aid in enumerate(ids_to_fetch):
        try:
            meta = genius_client.fetch_annotation(aid)
            if meta:
                _data["enriched"][aid] = meta
        except Exception as exc:
            logger.warning("Failed to fetch annotation %s: %s", aid, exc)
        _data["enrich_progress"] = i + 1
        if (i + 1) % 25 == 0:
            _save_enriched_cache()
        if i < len(ids_to_fetch) - 1:
            time.sleep(0.3)

    _save_enriched_cache()
    _data["enriching"] = False


def _build_response():
    mx = _data["mixpanel"]
    if not mx:
        return {"error": "No data loaded yet."}

    enriched = _data["enriched"]
    annotations = []
    for a in mx.get("top_lyrics", []):
        aid = a.get("annotation_id", "")
        genius = enriched.get(aid, {})
        annotations.append({
            **a,
            "lyric_fragment": genius.get("lyric_fragment", ""),
            "annotation_text": genius.get("annotation_text", ""),
            "song_url": genius.get("song_url", a.get("song_url", "")),
            "votes_total": genius.get("votes_total", 0),
            "verified": genius.get("verified", False),
            "genius_song_title": genius.get("song_title", ""),
            "genius_artist": genius.get("artist_name", ""),
        })

    return {
        "error": None,
        "fetched_at": mx.get("fetched_at"),
        "total_events": mx.get("total_events", 0),
        "unique_annotations": mx.get("unique_annotations", 0),
        "unique_songs": mx.get("unique_songs", 0),
        "artists": mx.get("artists", []),
        "songs": mx.get("songs", []),
        "annotations": annotations,
        "enriching": _data["enriching"],
        "enrich_progress": _data["enrich_progress"],
        "enrich_total": _data["enrich_total"],
        "enriched_count": len(enriched),
    }


def _query_mixpanel_artist(artist, days, event_name):
    """Query Mixpanel Raw Export API for a specific artist and event."""
    import requests as req

    to_date = datetime.utcnow().date()
    from_date = to_date - timedelta(days=days)

    params = {
        "from_date": from_date.isoformat(),
        "to_date": to_date.isoformat(),
        "event": json.dumps([event_name]),
        "where": f'properties["Primary Artist"] == "{artist}"',
    }

    resp = req.get(
        config.MIXPANEL_EXPORT_URL,
        params=params,
        auth=(config.MIXPANEL_API_SECRET, ""),
        timeout=120,
    )
    resp.raise_for_status()

    events = []
    for line in resp.text.strip().splitlines():
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        events.append(ev.get("properties", {}))

    return events


def _aggregate_artist_data(artist, days):
    """Fetch page views and annotation opens for an artist, return dashboard data."""
    # Fetch page views
    page_views = _query_mixpanel_artist(artist, days, "song:load")

    # Fetch annotation opens
    anno_events = _query_mixpanel_artist(artist, days, "song:open_annotation")

    # Aggregate page views by song
    song_counts = {}
    geo_counts = {}
    referrer_counts = {}
    daily_counts = {}
    total_views = len(page_views)

    for p in page_views:
        title = p.get("Title", "Unknown")
        song_counts[title] = song_counts.get(title, 0) + 1

        country = p.get("mp_country_code", "Unknown")
        geo_counts[country] = geo_counts.get(country, 0) + 1

        ref = p.get("$referrer") or p.get("$initial_referrer") or "Direct"
        referrer_counts[ref] = referrer_counts.get(ref, 0) + 1

        ts = p.get("time")
        if ts:
            day = datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")
            daily_counts[day] = daily_counts.get(day, 0) + 1

    songs = sorted(
        [{"song_title": t, "open_count": c} for t, c in song_counts.items()],
        key=lambda x: x["open_count"], reverse=True,
    )
    geos = sorted(
        [{"country": g, "count": c} for g, c in geo_counts.items()],
        key=lambda x: x["count"], reverse=True,
    )
    referrers = sorted(
        [{"referrer": r, "count": c} for r, c in referrer_counts.items()],
        key=lambda x: x["count"], reverse=True,
    )[:20]
    daily = sorted(daily_counts.items())

    # Aggregate annotation opens
    anno_by_song = {}
    anno_by_id = {}
    for p in anno_events:
        title = p.get("Title", "Unknown")
        anno_by_song[title] = anno_by_song.get(title, 0) + 1

        aid = p.get("annotation_id") or p.get("Annotation ID")
        if aid:
            aid = str(aid)
            if aid not in anno_by_id:
                anno_by_id[aid] = {"annotation_id": aid, "song_title": title, "open_count": 0}
            anno_by_id[aid]["open_count"] += 1

    annotations = sorted(anno_by_id.values(), key=lambda x: x["open_count"], reverse=True)

    # Enrich annotations with Genius data
    enriched = _data["enriched"]
    for a in annotations:
        genius = enriched.get(a["annotation_id"], {})
        a["lyric_fragment"] = genius.get("lyric_fragment", "")
        a["annotation_text"] = genius.get("annotation_text", "")
        a["song_url"] = genius.get("song_url", "")
        a["artist_name"] = artist

    return {
        "error": None,
        "artist": artist,
        "days": days,
        "total_page_views": total_views,
        "total_annotation_opens": len(anno_events),
        "unique_songs": len(songs),
        "unique_annotations": len(annotations),
        "songs": songs,
        "annotations": annotations[:100],
        "geos": geos,
        "referrers": referrers,
        "daily": [{"date": d, "views": v} for d, v in daily],
        "enriched_count": len(enriched),
    }


# --- Routes ---

@app.route("/")
def dashboard():
    return render_template("dashboard.html")


@app.route("/api/data")
def api_data():
    return jsonify(_build_response())


@app.route("/api/search")
def api_search():
    """Live query Mixpanel for a specific artist over a timeframe."""
    artist = request.args.get("artist", "").strip()
    days = int(request.args.get("days", 30))

    if not artist:
        return jsonify({"error": "artist parameter is required"}), 400
    if days < 1 or days > 365:
        return jsonify({"error": "days must be between 1 and 365"}), 400

    try:
        result = _aggregate_artist_data(artist, days)
        return jsonify(result)
    except Exception as exc:
        logger.exception("Search failed for artist=%s days=%d", artist, days)
        return jsonify({"error": str(exc)}), 500


@app.route("/api/enrich", methods=["POST"])
def api_enrich():
    if _data["enriching"]:
        return jsonify({"status": "already_running"})

    body = request.get_json(silent=True) or {}
    annotation_ids = body.get("annotation_ids", [])

    if not annotation_ids:
        mx = _data["mixpanel"]
        if mx:
            annotation_ids = [a["annotation_id"] for a in mx.get("top_lyrics", []) if a.get("annotation_id")]

    count = int(request.args.get("count", 100))
    thread = threading.Thread(target=_enrich_annotations_background, args=(annotation_ids, count), daemon=True)
    thread.start()
    return jsonify({"status": "started", "total": min(len(annotation_ids), count)})


@app.route("/api/enrich/status")
def api_enrich_status():
    return jsonify({
        "enriching": _data["enriching"],
        "progress": _data["enrich_progress"],
        "total": _data["enrich_total"],
        "enriched_count": len(_data["enriched"]),
    })


@app.route("/api/annotation/<annotation_id>")
def api_annotation_detail(annotation_id):
    if annotation_id in _data["enriched"]:
        return jsonify(_data["enriched"][annotation_id])
    try:
        meta = genius_client.fetch_annotation(annotation_id)
        if meta:
            _data["enriched"][annotation_id] = meta
            _save_enriched_cache()
            return jsonify(meta)
        return jsonify({"error": "Not found"}), 404
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# --- Startup ---
_load_mixpanel_cache()
_load_enriched_cache()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)

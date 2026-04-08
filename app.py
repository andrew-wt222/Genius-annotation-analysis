"""Flask application serving the Genius Annotation Analysis dashboard."""

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, render_template, request

import genius_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

BASE_DIR = Path(__file__).parent
MIXPANEL_CACHE = BASE_DIR / "data_cache.json"
ENRICHED_CACHE = BASE_DIR / "enriched_cache.json"

_data = {
    "mixpanel": None,         # raw Mixpanel aggregated data
    "enriched": {},           # annotation_id -> Genius metadata
    "enriching": False,       # True while background enrichment runs
    "enrich_progress": 0,
    "enrich_total": 0,
}


def _load_mixpanel_cache():
    """Load the pre-fetched Mixpanel data."""
    if MIXPANEL_CACHE.exists():
        with open(MIXPANEL_CACHE) as f:
            _data["mixpanel"] = json.load(f)
        logger.info("Loaded Mixpanel cache: %d artists, %d annotations",
                     len(_data["mixpanel"].get("artists", [])),
                     len(_data["mixpanel"].get("top_lyrics", [])))


def _load_enriched_cache():
    """Load previously enriched Genius annotation data."""
    if ENRICHED_CACHE.exists():
        with open(ENRICHED_CACHE) as f:
            _data["enriched"] = json.load(f)
        logger.info("Loaded enriched cache: %d annotations", len(_data["enriched"]))


def _save_enriched_cache():
    """Persist enriched data to disk."""
    with open(ENRICHED_CACHE, "w") as f:
        json.dump(_data["enriched"], f, indent=2)


def _enrich_annotations_background(annotation_ids, max_fetch=150):
    """Background thread: fetch Genius annotation details."""
    _data["enriching"] = True
    _data["enrich_total"] = min(len(annotation_ids), max_fetch)
    _data["enrich_progress"] = 0

    ids_to_fetch = [
        aid for aid in annotation_ids[:max_fetch]
        if aid not in _data["enriched"]
    ]
    _data["enrich_total"] = len(ids_to_fetch)

    if not ids_to_fetch:
        _data["enriching"] = False
        return

    logger.info("Enriching %d annotations from Genius API...", len(ids_to_fetch))

    for i, aid in enumerate(ids_to_fetch):
        try:
            meta = genius_client.fetch_annotation(aid)
            if meta:
                _data["enriched"][aid] = meta
        except Exception as exc:
            logger.warning("Failed to fetch annotation %s: %s", aid, exc)

        _data["enrich_progress"] = i + 1

        # Save every 25 annotations
        if (i + 1) % 25 == 0:
            _save_enriched_cache()

        # Rate limit
        if i < len(ids_to_fetch) - 1:
            time.sleep(0.3)

    _save_enriched_cache()
    _data["enriching"] = False
    logger.info("Enrichment complete: %d annotations", len(_data["enriched"]))


def _build_response():
    """Merge Mixpanel data with Genius enrichment."""
    mx = _data["mixpanel"]
    if not mx:
        return {"error": "No Mixpanel data loaded. Place data_cache.json in the project root."}

    enriched = _data["enriched"]

    # Merge enrichment into annotations
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


# --- Routes ---

@app.route("/")
def dashboard():
    return render_template("dashboard.html")


@app.route("/api/data")
def api_data():
    return jsonify(_build_response())


@app.route("/api/enrich", methods=["POST"])
def api_enrich():
    """Trigger Genius API enrichment for top annotations."""
    if _data["enriching"]:
        return jsonify({"status": "already_running",
                        "progress": _data["enrich_progress"],
                        "total": _data["enrich_total"]})

    mx = _data["mixpanel"]
    if not mx:
        return jsonify({"status": "error", "message": "No Mixpanel data loaded"})

    count = int(request.args.get("count", 100))
    annotation_ids = [a["annotation_id"] for a in mx.get("top_lyrics", [])
                      if a.get("annotation_id")]

    thread = threading.Thread(
        target=_enrich_annotations_background,
        args=(annotation_ids, count),
        daemon=True,
    )
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
    """Fetch a single annotation from Genius on demand."""
    if annotation_id in _data["enriched"]:
        return jsonify(_data["enriched"][annotation_id])

    try:
        meta = genius_client.fetch_annotation(annotation_id)
        if meta:
            _data["enriched"][annotation_id] = meta
            _save_enriched_cache()
            return jsonify(meta)
        return jsonify({"error": "Annotation not found"}), 404
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# --- Startup ---

_load_mixpanel_cache()
_load_enriched_cache()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)

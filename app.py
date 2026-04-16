"""Flask application serving the Genius Annotation Analysis dashboard."""

import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone  # noqa: F401 (timezone used)
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
CLASSIFICATIONS_FILE = BASE_DIR / "classifications.json"
UNIVERSE_FILE = BASE_DIR / "artist_universe.json"

# Mixpanel JQL / Insights API for live queries
MIXPANEL_INSIGHTS_URL = "https://mixpanel.com/api/2.0/insights"

_data = {
    "mixpanel": None,
    "enriched": {},
    "enriching": False,
    "enrich_progress": 0,
    "enrich_total": 0,
}

# Batch classification state (on-demand, triggered from the dashboard)
_batch = {
    "running": False,
    "total": 0,
    "done": 0,
    "current": None,
    "errors": [],
    "days": 90,
    "started_at": None,
    "finished_at": None,
}
_batch_lock = threading.Lock()


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

    # Escape double quotes in artist name for the where expression
    safe_artist = artist.replace('\\', '\\\\').replace('"', '\\"')
    where_expr = f'properties["Primary Artist"] == "{safe_artist}"'

    params = {
        "from_date": from_date.isoformat(),
        "to_date": to_date.isoformat(),
        "event": json.dumps([event_name]),
        "where": where_expr,
    }

    logger.info("Mixpanel query: event=%s artist=%s from=%s to=%s",
                event_name, artist, from_date, to_date)

    resp = req.get(
        config.MIXPANEL_EXPORT_URL,
        params=params,
        auth=(config.MIXPANEL_API_SECRET, ""),
        timeout=120,
    )

    # Log the actual URL for debugging
    if resp.status_code != 200:
        logger.error("Mixpanel returned %d: %s", resp.status_code, resp.text[:500])
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


QUADRANTS = {
    "Q1": {
        "name": "Full Catalog Fanbase",
        "thesis": "Deep users + heavy annotation engagement. Fans consume catalog and read lyrics. Highest signing priority.",
        "deal": "Full artist deal (360, catalog partnership, premium editorial).",
    },
    "Q2": {
        "name": "Single Deep Cut",
        "thesis": "Shallow users but high annotation rate. One song drives intense lyric scrutiny. Moment-driven.",
        "deal": "Sync / single-song partnership / EP-scoped deal. Don't overpay for catalog.",
    },
    "Q3": {
        "name": "Lyrics Utility",
        "thesis": "Deep catalog browsing but low annotation intent. Users treat page as lyrics lookup, not context.",
        "deal": "Distribution / ad-revenue deal. Monetize traffic, not editorial.",
    },
    "Q4": {
        "name": "One-Hit Lookup",
        "thesis": "Shallow traffic + no annotation engagement. Drive-by lyric searches.",
        "deal": "Pass on artist deal. Aggregate into playlist/utility monetization.",
    },
}

VPU_THRESHOLD = 1.8
AOR_THRESHOLD = 0.15


def _classify(vpu, aor):
    """Return quadrant key (Q1-Q4) given VPU and AOR."""
    high_vpu = vpu >= VPU_THRESHOLD
    high_aor = aor >= AOR_THRESHOLD
    if high_vpu and high_aor:
        return "Q1"
    if not high_vpu and high_aor:
        return "Q2"
    if high_vpu and not high_aor:
        return "Q3"
    return "Q4"


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
    user_songs = {}  # distinct_id -> set(song_title) for SPU
    unique_users = set()
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

        did = p.get("distinct_id")
        if did:
            unique_users.add(did)
            user_songs.setdefault(did, set()).add(title)

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

    # Metrics + classification
    total_annos = len(anno_events)
    u_count = len(unique_users)
    vpu = (total_views / u_count) if u_count else 0.0
    spu = (sum(len(s) for s in user_songs.values()) / u_count) if u_count else 0.0
    aor = (total_annos / total_views) if total_views else 0.0

    quad_key = _classify(vpu, aor)
    quad = QUADRANTS[quad_key]
    classification = {
        "quadrant": quad_key,
        "name": quad["name"],
        "thesis": quad["thesis"],
        "deal": quad["deal"],
        "vpu": round(vpu, 3),
        "spu": round(spu, 3),
        "aor": round(aor, 3),
        "unique_users": u_count,
        "vpu_threshold": VPU_THRESHOLD,
        "aor_threshold": AOR_THRESHOLD,
    }

    return {
        "error": None,
        "artist": artist,
        "days": days,
        "total_page_views": total_views,
        "total_annotation_opens": total_annos,
        "unique_users": u_count,
        "unique_songs": len(songs),
        "unique_annotations": len(annotations),
        "vpu": round(vpu, 3),
        "spu": round(spu, 3),
        "aor": round(aor, 3),
        "classification": classification,
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


def _load_signing_board():
    if CLASSIFICATIONS_FILE.exists():
        with open(CLASSIFICATIONS_FILE) as f:
            return json.load(f)
    return {"classified_at": None, "days": None, "artists": []}


def _load_universe():
    if UNIVERSE_FILE.exists():
        with open(UNIVERSE_FILE) as f:
            return json.load(f)
    return {"built_at": None, "days": None, "total_artists": 0, "artists": []}


def _save_classifications(state):
    with open(CLASSIFICATIONS_FILE, "w") as f:
        json.dump(state, f, indent=2)


@app.route("/api/signing-board")
def api_signing_board():
    """Return artists matching the filter set.

    JOINs the universe index (who exists at what volume) with the
    classifications cache (who's been classified so far). Artists matching
    the volume filters but not yet classified come back as pending rows
    the user can batch-classify.

    Query params:
      quadrant: Q1|Q2|Q3|Q4  (only returns classified artists if set)
      max_views: filter out artists above this 90-day page-view count
      min_views: filter out artists below this 90-day page-view count
      min_users: min unique-user count (only applies to classified rows)
      classified: all|yes|no  — default all
      sort: engagement_score (default) | vpu | aor | spu | unique_users |
            total_page_views | views_90d
      limit: cap the returned row count (default 200)
    """
    board = _load_signing_board()
    universe = _load_universe()

    classified = board.get("artists", [])
    classified_by_name = {a["artist"]: a for a in classified}

    quadrant = request.args.get("quadrant", "").strip()
    max_views = request.args.get("max_views", type=int)
    min_views = request.args.get("min_views", type=int)
    min_users = request.args.get("min_users", type=int)
    classified_filter = request.args.get("classified", "all").lower()
    limit = request.args.get("limit", default=200, type=int)

    rows = []

    # Start from the universe so we can surface unclassified candidates.
    universe_artists = universe.get("artists", [])
    for u in universe_artists:
        name = u["artist_name"]
        views = u.get("views_90d", 0)

        if max_views is not None and views > max_views:
            continue
        if min_views is not None and views < min_views:
            continue

        c = classified_by_name.get(name)
        if c:
            if quadrant and c.get("quadrant") != quadrant:
                continue
            if min_users is not None and c.get("unique_users", 0) < min_users:
                continue
            if classified_filter == "no":
                continue
            rows.append({**c, "views_90d": views, "classified": True})
        else:
            if quadrant or classified_filter == "yes":
                continue
            rows.append({
                "artist": name,
                "views_90d": views,
                "classified": False,
                "quadrant": None,
                "engagement_score": 0,
            })

    # If no universe file exists yet, fall back to showing only classifications.
    if not universe_artists:
        for c in classified:
            if quadrant and c.get("quadrant") != quadrant:
                continue
            if min_users is not None and c.get("unique_users", 0) < min_users:
                continue
            if max_views is not None and c.get("total_page_views", 0) > max_views:
                continue
            if min_views is not None and c.get("total_page_views", 0) < min_views:
                continue
            rows.append({**c, "views_90d": c.get("total_page_views", 0), "classified": True})

    sort_by = request.args.get("sort", "engagement_score")
    if sort_by not in {"engagement_score", "vpu", "aor", "spu",
                       "unique_users", "total_page_views",
                       "total_annotation_opens", "views_90d"}:
        sort_by = "engagement_score"

    rows.sort(key=lambda x: x.get(sort_by, 0) or 0, reverse=True)

    # Quadrant count over ALL classified artists (not just filtered)
    stats = {q: sum(1 for a in classified if a.get("quadrant") == q)
             for q in ("Q1", "Q2", "Q3", "Q4")}

    truncated_count = len(rows)
    rows = rows[:limit]

    return jsonify({
        "classified_at": board.get("classified_at"),
        "days": board.get("days"),
        "universe_built_at": universe.get("built_at"),
        "universe_total": universe.get("total_artists", 0),
        "total_classified": len(classified),
        "stats": stats,
        "filtered_count": truncated_count,
        "returned_count": len(rows),
        "limit": limit,
        "artists": rows,
    })


def _classify_artist_inline(name, days):
    """Run the per-artist classification and append to classifications.json.

    Returns the new entry on success, or None on failure.
    """
    try:
        result = _aggregate_artist_data(name, days)
    except Exception as exc:
        logger.exception("classify %s failed: %s", name, exc)
        return None

    if result.get("error"):
        return None

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

    state = _load_signing_board()
    state["days"] = days
    state["artists"] = [a for a in state.get("artists", []) if a.get("artist") != name]
    state["artists"].append(entry)
    state["classified_at"] = datetime.now(timezone.utc).isoformat()
    _save_classifications(state)
    return entry


def _batch_classify_worker(artists, days):
    with _batch_lock:
        _batch["running"] = True
        _batch["total"] = len(artists)
        _batch["done"] = 0
        _batch["current"] = None
        _batch["errors"] = []
        _batch["days"] = days
        _batch["started_at"] = datetime.now(timezone.utc).isoformat()
        _batch["finished_at"] = None

    for name in artists:
        with _batch_lock:
            _batch["current"] = name

        entry = _classify_artist_inline(name, days)
        if entry is None:
            _batch["errors"].append(name)

        with _batch_lock:
            _batch["done"] += 1

        time.sleep(0.8)  # rate-limit

    with _batch_lock:
        _batch["running"] = False
        _batch["current"] = None
        _batch["finished_at"] = datetime.now(timezone.utc).isoformat()


@app.route("/api/classify-batch", methods=["POST"])
def api_classify_batch():
    """Kick off background classification of a list of artists.

    Body: {"artists": ["Artist1", ...], "days": 90}
    """
    with _batch_lock:
        if _batch["running"]:
            return jsonify({
                "status": "already_running",
                "total": _batch["total"],
                "done": _batch["done"],
            }), 409

    body = request.get_json(silent=True) or {}
    artists = [a for a in (body.get("artists") or []) if a]
    days = int(body.get("days", 90))

    if not artists:
        return jsonify({"error": "artists list is required"}), 400
    if len(artists) > 500:
        return jsonify({"error": "max 500 artists per batch"}), 400

    thread = threading.Thread(
        target=_batch_classify_worker,
        args=(artists, days),
        daemon=True,
    )
    thread.start()
    return jsonify({"status": "started", "total": len(artists), "days": days})


@app.route("/api/classify-batch/status")
def api_classify_batch_status():
    with _batch_lock:
        return jsonify({
            "running": _batch["running"],
            "total": _batch["total"],
            "done": _batch["done"],
            "current": _batch["current"],
            "errors": list(_batch["errors"]),
            "days": _batch["days"],
            "started_at": _batch["started_at"],
            "finished_at": _batch["finished_at"],
        })


@app.route("/api/universe")
def api_universe():
    u = _load_universe()
    return jsonify({
        "built_at": u.get("built_at"),
        "days": u.get("days"),
        "total_artists": u.get("total_artists", 0),
    })


@app.route("/api/classify")
def api_classify():
    """Classify an artist by VPU and AOR into one of the four quadrants."""
    try:
        vpu = float(request.args.get("vpu", ""))
        aor = float(request.args.get("aor", ""))
    except (TypeError, ValueError):
        return jsonify({"error": "vpu and aor query params (floats) are required"}), 400

    key = _classify(vpu, aor)
    quad = QUADRANTS[key]
    return jsonify({
        "quadrant": key,
        "name": quad["name"],
        "thesis": quad["thesis"],
        "deal": quad["deal"],
        "vpu": vpu,
        "aor": aor,
        "vpu_threshold": VPU_THRESHOLD,
        "aor_threshold": AOR_THRESHOLD,
    })


@app.route("/api/quadrants")
def api_quadrants():
    """Return the quadrant definitions and thresholds."""
    return jsonify({
        "thresholds": {"vpu": VPU_THRESHOLD, "aor": AOR_THRESHOLD},
        "quadrants": QUADRANTS,
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

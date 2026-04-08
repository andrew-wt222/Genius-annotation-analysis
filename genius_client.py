"""Fetch annotation and song metadata from the Genius API."""

import logging
import time

import requests

import config

logger = logging.getLogger(__name__)

_access_token = None


def _get_access_token():
    """Obtain a client-credentials access token from Genius."""
    global _access_token
    if _access_token:
        return _access_token

    resp = requests.post(
        config.GENIUS_OAUTH_TOKEN_URL,
        data={
            "client_id": config.GENIUS_CLIENT_ID,
            "client_secret": config.GENIUS_CLIENT_SECRET,
            "grant_type": "client_credentials",
        },
        timeout=30,
    )
    resp.raise_for_status()
    _access_token = resp.json()["access_token"]
    logger.info("Obtained Genius access token")
    return _access_token


def _api_get(path, retries=2):
    """Make an authenticated GET to the Genius API."""
    token = _get_access_token()
    url = f"{config.GENIUS_API_BASE}{path}"
    headers = {"Authorization": f"Bearer {token}"}

    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=30)
            if resp.status_code == 429:
                wait = 2 ** attempt
                logger.warning("Rate-limited by Genius, waiting %ds", wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json().get("response", {})
        except requests.RequestException as exc:
            if attempt < retries:
                time.sleep(1)
                continue
            logger.error("Genius API error for %s: %s", path, exc)
            return None
    return None


def fetch_annotation(annotation_id):
    """Fetch a single annotation by ID.

    Returns a dict with annotation text, referent fragment (lyric),
    song title, song URL, and primary artist info.
    """
    data = _api_get(f"/annotations/{annotation_id}?text_format=plain")
    if not data:
        return None

    annotation = data.get("annotation", {})
    referent = data.get("referent", {})

    # The lyric fragment the annotation is attached to
    fragment = referent.get("fragment", "")

    # Song / annotatable metadata
    annotatable = referent.get("annotatable", {})
    song_title = annotatable.get("title", "Unknown")
    song_url = annotatable.get("url", "")
    artist_name = annotatable.get("context", "Unknown")

    # Annotation body (plain text)
    body_parts = annotation.get("body", {}).get("plain", "")

    # Votes / community data
    votes_total = annotation.get("votes_total", 0)
    verified = annotation.get("verified", False)

    return {
        "annotation_id": str(annotation_id),
        "lyric_fragment": fragment,
        "annotation_text": body_parts,
        "song_title": song_title,
        "song_url": song_url,
        "artist_name": artist_name,
        "votes_total": votes_total,
        "verified": verified,
    }


def fetch_annotations_batch(annotation_ids, max_fetch=200):
    """Fetch metadata for a list of annotation IDs.

    Caps at max_fetch to avoid excessive API calls.
    Returns a dict mapping annotation_id -> metadata.
    """
    results = {}
    ids_to_fetch = list(annotation_ids)[:max_fetch]
    total = len(ids_to_fetch)

    for i, aid in enumerate(ids_to_fetch):
        logger.info("Fetching annotation %s (%d/%d)", aid, i + 1, total)
        meta = fetch_annotation(aid)
        if meta:
            results[aid] = meta
        # Small delay to respect rate limits
        if i < total - 1:
            time.sleep(0.25)

    logger.info("Fetched %d/%d annotations from Genius", len(results), total)
    return results

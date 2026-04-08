"""Fetch raw annotation events from Mixpanel's Data Export API."""

import json
import logging
from datetime import datetime, timedelta

import requests

import config

logger = logging.getLogger(__name__)


def fetch_annotation_events(days=None):
    """Pull song:open_annotation events for the last N days.

    Returns a list of dicts, each containing at minimum an annotation_id
    and the event timestamp.
    """
    days = days or config.LOOKBACK_DAYS
    to_date = datetime.utcnow().date()
    from_date = to_date - timedelta(days=days)

    params = {
        "from_date": from_date.isoformat(),
        "to_date": to_date.isoformat(),
        "event": json.dumps([config.EVENT_NAME]),
    }

    logger.info(
        "Fetching Mixpanel events from %s to %s", from_date, to_date
    )

    resp = requests.get(
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
            event = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("Skipping malformed line: %s", line[:120])
            continue

        props = event.get("properties", {})
        annotation_id = props.get("annotation_id") or props.get("Annotation ID")
        if annotation_id is None:
            continue

        events.append({
            "annotation_id": str(annotation_id),
            "time": props.get("time"),
            "distinct_id": props.get("distinct_id"),
        })

    logger.info("Fetched %d annotation events", len(events))
    return events


def aggregate_annotation_counts(events):
    """Return {annotation_id: open_count} sorted descending by count."""
    counts = {}
    for ev in events:
        aid = ev["annotation_id"]
        counts[aid] = counts.get(aid, 0) + 1
    return dict(sorted(counts.items(), key=lambda x: x[1], reverse=True))

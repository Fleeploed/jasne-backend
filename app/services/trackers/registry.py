from __future__ import annotations

from app.services.trackers.base import BaseTracker
from app.services.trackers.oamp import OampTracker

TRACKER_REGISTRY: dict[str, BaseTracker] = {
    "oamp_status": OampTracker(),
}


def get_tracker(tracking_type: str) -> BaseTracker:
    try:
        return TRACKER_REGISTRY[tracking_type]
    except KeyError as exc:
        raise ValueError(f"Unsupported tracking_type: {tracking_type}") from exc

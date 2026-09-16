import itertools
import queue
import threading
from datetime import datetime, timezone


SEVERITY_LEVELS = {
    "minor": {
        "speed_multiplier": 1.3,
        "blocked": False
    },

    "moderate": {
        "speed_multiplier": 2.0,
        "blocked": False
    },

    "severe": {
        "speed_multiplier": 5.0,
        "blocked": True
    },
}


INCIDENT_TYPES = [
    "landslide",
    "flood",
    "roadblock",
    "accident",
    "snow_closure",
    "weather_alert",
]


_lock = threading.Lock()

_incidents = {}

_subscribers = []

_id_counter = itertools.count(1)


def _edge_key(a, b):

    if not a or not b:
        return None

    return tuple(
        sorted([a, b])
    )


def _now_iso():

    return datetime.now(
        timezone.utc
    ).isoformat()


def subscribe():

    q = queue.Queue()

    with _lock:
        _subscribers.append(q)

    return q


def unsubscribe(q):

    with _lock:

        if q in _subscribers:
            _subscribers.remove(q)


def _broadcast(
    event_type,
    payload
):

    with _lock:
        subscribers = list(
            _subscribers
        )

    for q in subscribers:

        try:

            q.put_nowait({
                "event": event_type,
                "data": payload
            })

        except queue.Full:

            pass


def list_active_incidents():

    with _lock:

        return [
            incident
            for incident
            in _incidents.values()
            if incident["active"]
        ]


def get_edge_condition(
    node_a,
    node_b
):

    key = _edge_key(
        node_a,
        node_b
    )

    if key is None:
        return None

    with _lock:

        active = [
            incident
            for incident
            in _incidents.values()

            if (
                incident["active"]
                and
                incident["edge_key"]
                == key
            )
        ]

    if not active:
        return None

    active.sort(
        key=lambda incident:
        SEVERITY_LEVELS[
            incident["severity"]
        ]["speed_multiplier"],

        reverse=True
    )

    return active[0]


def report_incident(
    node_a,
    node_b,
    incident_type,
    severity,
    description=None,
    ttl_minutes=None,
    source="system",
    external_id=None,
    area=None,
    lat=None,
    lon=None,
    blocks_route=None
):

    if incident_type not in INCIDENT_TYPES:

        raise ValueError(
            f"Unknown incident_type "
            f"'{incident_type}'"
        )

    if severity not in SEVERITY_LEVELS:

        raise ValueError(
            f"Unknown severity "
            f"'{severity}'"
        )

    incident_id = str(
        next(_id_counter)
    )

    edge = (
        [node_a, node_b]
        if node_a and node_b
        else []
    )

    if blocks_route is None:

        blocks_route = (
            SEVERITY_LEVELS[
                severity
            ]["blocked"]
        )

    incident = {

        "id": incident_id,

        "external_id": external_id,

        "edge": edge,

        "edge_key":
            _edge_key(
                node_a,
                node_b
            ),

        "type": incident_type,

        "severity": severity,

        "description":
            description
            or
            f"{incident_type.replace('_', ' ').title()} alert",

        "reported_at":
            _now_iso(),

        "source": source,

        "area": area,

        "lat": lat,

        "lon": lon,

        "active": True,

        "ttl_minutes":
            ttl_minutes,

        "blocks_route":
            bool(blocks_route),
    }

    with _lock:

        _incidents[
            incident_id
        ] = incident

    _broadcast(
        "incident_new",
        incident
    )

    if ttl_minutes:

        timer = threading.Timer(

            ttl_minutes * 60,

            _auto_clear,

            args=(incident_id,)
        )

        timer.daemon = True

        timer.start()

    return incident


def _auto_clear(
    incident_id
):

    clear_incident(

        incident_id,

        reason="government alert expired"
    )


def clear_incident(
    incident_id,
    reason="cleared"
):

    with _lock:

        incident = _incidents.get(
            incident_id
        )

        if (
            not incident
            or
            not incident["active"]
        ):
            return None

        incident["active"] = False

        incident["cleared_at"] = (
            _now_iso()
        )

        incident["clear_reason"] = (
            reason
        )

    _broadcast(
        "incident_cleared",
        incident
    )

    return incident
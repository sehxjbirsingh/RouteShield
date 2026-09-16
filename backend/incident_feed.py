import html
import re
import threading
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests

import incident_store
from graph_data import NODES, EDGES


FEED_URL = "https://sachet.ndma.gov.in/cap_public_website/rss/rss_india.xml"

POLL_SECONDS = 60
REQUEST_TIMEOUT = 20

_session = requests.Session()
_session.headers.update({
    "User-Agent": "NE-SLAI/1.0 government-alert-consumer"
})

_etag = None
_thread = None
_stop_event = threading.Event()

_known = {}


def _local_name(tag):
    return tag.rsplit("}", 1)[-1].lower()


def _text(element):
    if element is None:
        return ""

    return " ".join(
        "".join(element.itertext()).split()
    )


def _first(item, names):
    names = {name.lower() for name in names}

    for element in item.iter():
        if _local_name(element.tag) in names:
            value = _text(element)

            if value:
                return value

    return ""


def _clean_html(value):
    value = html.unescape(value or "")
    return re.sub(r"<[^>]+>", " ", value)


def _parse_time(value):
    if not value:
        return None

    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(
                value.replace("Z", "+00:00")
            )
        except ValueError:
            return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(timezone.utc)


def _extract_coordinates(item):
    for element in item.iter():

        name = _local_name(element.tag)
        value = _text(element)

        if not value:
            continue

        if name == "circle":

            match = re.search(
                r"(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)",
                value
            )

            if match:
                return (
                    float(match.group(1)),
                    float(match.group(2))
                )

        if name == "polygon":

            pairs = re.findall(
                r"(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)",
                value
            )

            if pairs:
                lat = sum(
                    float(a) for a, _ in pairs
                ) / len(pairs)

                lon = sum(
                    float(b) for _, b in pairs
                ) / len(pairs)

                return lat, lon

    return None


def _nearest_edge(lat, lon):

    best = None
    best_distance = float("inf")

    for a, b, *_ in EDGES:

        lat_a = NODES[a]["lat"]
        lon_a = NODES[a]["lon"]

        lat_b = NODES[b]["lat"]
        lon_b = NODES[b]["lon"]

        mid_lat = (lat_a + lat_b) / 2
        mid_lon = (lon_a + lon_b) / 2

        distance = (
            (lat - mid_lat) ** 2
            +
            (lon - mid_lon) ** 2
        )

        if distance < best_distance:
            best_distance = distance
            best = (a, b)

    return best


def _edge_from_text(text):

    lowered = text.lower()

    matches = []

    for node in NODES:

        if node.lower() in lowered:
            matches.append(node)

    matches.sort(
        key=len,
        reverse=True
    )

    if len(matches) >= 2:

        wanted = {
            matches[0],
            matches[1]
        }

        for a, b, *_ in EDGES:

            if {a, b} == wanted:
                return a, b

    if matches:

        node = matches[0]

        connected = []

        for a, b, *_ in EDGES:

            if a == node or b == node:

                other = (
                    b
                    if a == node
                    else a
                )

                connected.append(
                    (
                        a,
                        b,
                        NODES[other]["lat"],
                        NODES[other]["lon"]
                    )
                )

        if connected:

            connected.sort(
                key=lambda x:
                (
                    (x[2] - NODES[node]["lat"]) ** 2
                    +
                    (x[3] - NODES[node]["lon"]) ** 2
                )
            )

            return (
                connected[0][0],
                connected[0][1]
            )

    return None


def _classify_type(event, title, description):

    text = (
        f"{event} "
        f"{title} "
        f"{description}"
    ).lower()

    if (
        "landslide" in text
        or "mudslide" in text
    ):
        return "landslide"

    if (
        "flood" in text
        or "inundation" in text
    ):
        return "flood"

    if (
        "snow" in text
        or "avalanche" in text
    ):
        return "snow_closure"

    if (
        "road closure" in text
        or "road block" in text
        or "roadblock" in text
    ):
        return "roadblock"

    if (
        "accident" in text
        or "crash" in text
    ):
        return "accident"

    return "weather_alert"


def _severity(value, title, description):

    text = (
        f"{value} "
        f"{title} "
        f"{description}"
    ).lower()

    if (
        "extreme" in text
        or "severe" in text
    ):
        return "severe"

    if (
        "moderate" in text
        or "warning" in text
        or "alert" in text
    ):
        return "moderate"

    return "minor"


def _parse_items(xml_bytes):

    root = ET.fromstring(xml_bytes)

    items = []

    for item in root.iter():

        if _local_name(item.tag) != "item":
            continue

        title = _first(
            item,
            {"title"}
        )

        description = _clean_html(
            _first(
                item,
                {"description"}
            )
        )

        event = _first(
            item,
            {"event"}
        )

        area = _first(
            item,
            {"areadesc", "areaDesc"}
        )

        identifier = _first(
            item,
            {
                "identifier",
                "guid",
                "id",
                "link"
            }
        )

        severity_raw = _first(
            item,
            {"severity"}
        )

        published = _first(
            item,
            {
                "pubdate",
                "published",
                "effective"
            }
        )

        expires = _first(
            item,
            {"expires"}
        )

        coordinates = _extract_coordinates(item)

        if not identifier:

            identifier = (
                f"{title}|"
                f"{published}|"
                f"{area}"
            )

        incident_type = _classify_type(
            event,
            title,
            description
        )

        severity = _severity(
            severity_raw,
            title,
            description
        )

        edge = None

        if coordinates:

            edge = _nearest_edge(
                coordinates[0],
                coordinates[1]
            )

        combined = (
            f"{title} "
            f"{description} "
            f"{event} "
            f"{area}"
        )

        if edge is None:
            edge = _edge_from_text(combined)

        reported_dt = (
            _parse_time(published)
            or datetime.now(timezone.utc)
        )

        expires_dt = _parse_time(expires)

        ttl_minutes = None

        if expires_dt:

            ttl_minutes = max(
                1,
                int(
                    (
                        expires_dt
                        -
                        datetime.now(timezone.utc)
                    ).total_seconds()
                    / 60
                )
            )

        items.append({
            "external_id": identifier,
            "title": (
                title
                or event
                or "Government disaster alert"
            ),
            "description": (
                description
                or title
                or event
                or "Government disaster alert"
            ),
            "event": event,
            "area": area,
            "type": incident_type,
            "severity": severity,
            "edge": edge,
            "lat": (
                coordinates[0]
                if coordinates
                else None
            ),
            "lon": (
                coordinates[1]
                if coordinates
                else None
            ),
            "reported_at": reported_dt.isoformat(),
            "expires_at": (
                expires_dt.isoformat()
                if expires_dt
                else None
            ),
            "ttl_minutes": ttl_minutes,
        })

    return items


def _fingerprint(item):

    return "|".join([
        item["external_id"],
        item["title"],
        item["description"],
        item["severity"],
        item["area"],
        str(item["edge"]),
        str(item["expires_at"]),
    ])


def _ingest(items):

    for item in items:

        fingerprint = _fingerprint(item)

        old = _known.get(
            item["external_id"]
        )

        if (
            old
            and old["fingerprint"]
            == fingerprint
        ):
            continue

        if old is not None:

            incident_store.clear_incident(
                old["internal_id"],
                reason="government alert updated"
            )

        edge = (
            item["edge"]
            or (None, None)
        )

        incident = incident_store.report_incident(

            edge[0],
            edge[1],

            item["type"],
            item["severity"],

            description=item["description"],

            ttl_minutes=item["ttl_minutes"],

            source="NDMA SACHET",

            external_id=item["external_id"],

            area=item["area"],

            lat=item["lat"],

            lon=item["lon"],

            blocks_route=False
        )

        _known[
            item["external_id"]
        ] = {
            "internal_id": incident["id"],
            "fingerprint": fingerprint
        }


def _poll_once():

    global _etag

    headers = {}

    if _etag:

        headers["If-None-Match"] = _etag

    response = _session.get(

        FEED_URL,

        headers=headers,

        timeout=REQUEST_TIMEOUT
    )

    if response.status_code == 304:
        return

    response.raise_for_status()

    _etag = (
        response.headers.get("ETag")
        or _etag
    )

    items = _parse_items(
        response.content
    )

    _ingest(items)


def _loop():

    while not _stop_event.is_set():

        try:

            _poll_once()

            print(
                "[incident_feed] "
                "NDMA SACHET feed checked"
            )

        except Exception as exc:

            print(
                "[incident_feed] ERROR:",
                exc
            )

        _stop_event.wait(
            POLL_SECONDS
        )


def start():

    global _thread

    if (
        _thread
        and _thread.is_alive()
    ):
        return _thread

    _stop_event.clear()

    _thread = threading.Thread(
        target=_loop,
        name="ndma-sachet-feed",
        daemon=True
    )

    _thread.start()

    return _thread


def stop():

    _stop_event.set()
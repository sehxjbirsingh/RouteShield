"""
routing_engine.py
=================
Builds a graph of the real NE India road network (graph_data.py) and, on
every request, re-weights every edge using CURRENT live conditions from
incident_store.py before computing routes. This is what makes the "best
route" answer change the instant an incident is reported — the graph
itself is rebuilt with fresh weights on every call, nothing is cached.

Algorithm:
  - networkx.shortest_simple_paths (Yen's algorithm) to get several
    loopless candidate paths ordered by weighted travel time.
  - Each candidate is scored on: total time, total distance, a 0-100 risk
    score (based on terrain difficulty + active incidents along it), and
    whether it currently contains a severe/blocked edge.
  - The first candidate with no blocked edge and the lowest weighted time
    is marked "recommended". Others are "alternate". Any route containing
    a currently blocked edge is marked "not_advisable" instead of being
    silently dropped, so the user can still see why to avoid it.
"""

import networkx as nx
import requests


from graph_data import NODES, EDGES, TERRAIN_BASE_SPEED_KMPH
import incident_store
import weather_service

OSRM_URL = "https://router.project-osrm.org/route/v1/driving"
OSRM_TIMEOUT = 10

def build_live_graph():
    """Rebuild the graph from scratch with current live incident weights."""
    G = nx.Graph()
    for name, attrs in NODES.items():
        G.add_node(name, **attrs)

    for a, b, highway, distance_km, terrain, landslide_prone in EDGES:
        base_speed = TERRAIN_BASE_SPEED_KMPH[terrain]
        base_time_hr = distance_km / base_speed

        condition = incident_store.get_edge_condition(a, b)
        multiplier = 1.0
        blocked = False
        active_incident = None
        weather_risk=0

        if condition is not None:
            sev = incident_store.SEVERITY_LEVELS[condition["severity"]]

            multiplier = sev["speed_multiplier"]

            blocked = condition.get("blocks_route",sev["blocked"])
            active_incident = condition
        live_time_hr = base_time_hr * multiplier
        if blocked:
            # Don't fully delete the edge (we still want to be able to show
            # "here's why this route is currently not advisable"), but make
            # it extremely unattractive to the shortest-path search.
            weight = live_time_hr * 1000
        else:
            weight = live_time_hr

        risk_points = 0
        if landslide_prone:
            risk_points += 20
        if terrain == "mountainous":
            risk_points += 15
        elif terrain == "hilly":
            risk_points += 7
        if active_incident:
            risk_points += {"minor": 10, "moderate": 25, "severe": 50}[active_incident["severity"]]
            weather_a = weather_service.get_weather(
            NODES[a]["lat"],
            NODES[a]["lon"]
            )

            weather_b = weather_service.get_weather(
            NODES[b]["lat"],
            NODES[b]["lon"]
            )

            weather_risk_a = weather_service.calculate_weather_risk(
            weather_a
            )

            weather_risk_b = weather_service.calculate_weather_risk(
            weather_b
            )

            weather_risk = round(
            (weather_risk_a + weather_risk_b) / 2
            )

            risk_points += weather_risk

        G.add_edge(
            a, b,
            highway=highway,
            distance_km=distance_km,
            terrain=terrain,
            landslide_prone=landslide_prone,
            base_time_hr=base_time_hr,
            live_time_hr=live_time_hr,
            weight=weight,
            blocked=blocked,
            active_incident=active_incident,
            weather_risk=weather_risk,
            risk_points=risk_points,
        )
    return G

def get_real_road_route(path, G):
    """
    Get real road geometry, distance and duration from the free
    public OSRM routing service.

    Returns None if OSRM is temporarily unavailable.
    """

    coordinates = ";".join(
        f"{G.nodes[name]['lon']},{G.nodes[name]['lat']}"
        for name in path
    )

    url = f"{OSRM_URL}/{coordinates}"

    params = {
        "overview": "full",
        "geometries": "geojson",
        "steps": "false",
    }

    try:
        response = requests.get(
            url,
            params=params,
            timeout=OSRM_TIMEOUT,
        )

        response.raise_for_status()
        data = response.json()

        if data.get("code") != "Ok":
            return None

        if not data.get("routes"):
            return None

        route = data["routes"][0]

        geometry = route.get("geometry", {})
        geometry_coordinates = geometry.get("coordinates", [])

        leaflet_coords = [
            [lat, lon]
            for lon, lat in geometry_coordinates
        ]

        return {
            "distance_km": route["distance"] / 1000,
            "duration_min": route["duration"] / 60,
            "geometry": leaflet_coords,
        }

    except (requests.RequestException, ValueError, KeyError, TypeError):
        return None

def _summarize_path(G, path):
    total_distance = 0.0
    total_time_hr = 0.0
    total_risk = 0
    total_weather_risk = 0
    blocked_any = False
    segments = []
    incidents_on_route = []

    for u, v in zip(path[:-1], path[1:]):
        edge = G[u][v]

        total_distance += edge["distance_km"]
        total_time_hr += edge["live_time_hr"]
        total_risk += edge["risk_points"]
        total_weather_risk += edge["weather_risk"]
        if edge["blocked"]:
            blocked_any = True

        if edge["active_incident"]:
            incidents_on_route.append({
                **edge["active_incident"],
                "location": f"{u} ↔ {v}",
            })

        segments.append({
            "from": u,
            "to": v,
            "highway": edge["highway"],
            "distance_km": edge["distance_km"],
            "terrain": edge["terrain"],
            "landslide_prone": edge["landslide_prone"],
            "estimated_time_min": round(edge["live_time_hr"] * 60),
            "status": (
                "blocked"
                if edge["blocked"]
                else (
                    "disrupted"
                    if edge["active_incident"]
                    else "clear"
                )
            ),
            "active_incident": edge["active_incident"],
            "coords": [
                [G.nodes[u]["lat"], G.nodes[u]["lon"]],
                [G.nodes[v]["lat"], G.nodes[v]["lon"]],
            ],
        })

    risk_score = min(
        100,
        round(total_risk / max(1, len(path) - 1))
    )

    # ---------------------------------------------------------
    # REAL ROAD ROUTING USING OSRM
    # ---------------------------------------------------------

    real_route = get_real_road_route(path, G)

    if real_route:
        distance_km = round(real_route["distance_km"], 1)
        estimated_time_min = round(real_route["duration_min"])
        geometry = real_route["geometry"]
        geometry_source = "OSRM + OpenStreetMap"
    else:
        # Fallback to existing graph if OSRM is unavailable
        distance_km = round(total_distance, 1)
        estimated_time_min = round(total_time_hr * 60)

        geometry = [
            coord
            for segment in segments
            for coord in segment["coords"]
        ]

        geometry_source = "Internal graph fallback"

    return {
        "path": path,
        "distance_km": distance_km,
        "estimated_time_min": estimated_time_min,
        "risk_score": risk_score,
        "weather_risk": round(  total_weather_risk / max(1, len(path) - 1)),
        "has_blocked_segment": blocked_any,
        "incidents_on_route": incidents_on_route,
        "segments": segments,

        # NEW
        "geometry": geometry,
        "geometry_source": geometry_source,
    }

def find_routes(source, destination, k=3):
    if source not in NODES:
        raise ValueError(f"Unknown source location '{source}'")
    if destination not in NODES:
        raise ValueError(f"Unknown destination location '{destination}'")
    if source == destination:
        raise ValueError("Source and destination must be different")

    G = build_live_graph()

    if not nx.has_path(G, source, destination):
        return []

    try:
        gen = nx.shortest_simple_paths(G, source, destination, weight="weight")
        candidates = []
        for i, path in enumerate(gen):
            candidates.append(path)
            if i + 1 >= k:
                break
    except nx.NetworkXNoPath:
        return []

    # Also surface the route that would normally be fastest under NORMAL
    # (incident-free) conditions, even if it's currently blocked. Users
    # should be able to see "this is usually the way, but it's shut right
    # now" rather than have it silently vanish from the results.
    try:
        normal_best = nx.shortest_path(G, source, destination, weight="base_time_hr")
        if normal_best not in candidates:
            candidates.append(normal_best)
    except nx.NetworkXNoPath:
        pass

    summaries = [_summarize_path(G, p) for p in candidates]

    # Sort clear/disrupted routes by live weighted time; blocked-containing
    # routes sink to the bottom regardless of raw time.
    summaries.sort(key=lambda s: (s["has_blocked_segment"], s["estimated_time_min"]))

    labels_assigned = False
    for s in summaries:
        if s["has_blocked_segment"]:
            s["label"] = "not_advisable"
        elif not labels_assigned:
            s["label"] = "recommended"
            labels_assigned = True
        else:
            s["label"] = "alternate"

    return summaries

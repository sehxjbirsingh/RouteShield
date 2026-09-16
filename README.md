# NE-SLAI — Smart Logistics & Accessibility Intelligence Platform
### Northeast India · Smart India Hackathon prototype

A working, self-contained web app that routes people/goods between towns in
Northeast India across a real road network, scores each candidate route on
live conditions instead of raw distance, and updates every connected
browser **the instant** an incident (landslide, flood, roadblock, snow
closure) is reported — no page refresh, no polling delay.

---

## 1. Run it

```bash
cd ne-logistics
pip install -r requirements.txt      # Flask, networkx — nothing else needed
cd backend
python app.py
```

Open **http://localhost:5000**. That's it — one process serves both the API
and the frontend, and starts a background live-incident feed automatically.

---

## 2. What's real here, and what's simulated (read this before your demo/report)

Judges will ask this. Be upfront about it — it's a strength of the
architecture, not a weakness.

| Layer | Status | Detail |
|---|---|---|
| **Town/city coordinates** | Real | 26 real locations across all 8 NE states + Siliguri, actual lat/lon. |
| **Highway network** | Real, grounded | NH-40 (Guwahati–Shillong), the new NH-6 greenfield corridor (Shillong–Silchar), NH-37 (Silchar–Imphal via Jiribam), NH-2 (Kohima–Imphal–Churachandpur), NH-202 (Mokokchung–Imphal), NH-306 (Silchar–Aizawl), NH-10 (Siliguri–Gangtok), etc. — cross-checked against MoRTH/PIB/state PWD sources. A few minor connector stretches are honestly labelled "State Road" rather than inventing an NH number for them. See `backend/graph_data.py` for full provenance notes and exact distances/terrain used. **Verify current distances against MoRTH GIS before final submission if precision matters for judging.**
| **High-risk corridor bias** | Real | The Siliguri–Gangtok (NH-10), Tezpur–Tawang (Sela Pass, NH-13), and Silchar–Aizawl (NH-306) stretches are among India's most reported landslide-prone highways — this is public record, not a guess, and the simulator is biased toward these real corridors. |
| **Routing algorithm** | Real | Dijkstra / Yen's k-shortest-paths (via `networkx`) over live-weighted edges — genuinely computes alternatives and re-ranks them, not hardcoded. |
| **Live incidents right now** | **Simulated** | No government API in this build streams real GSI/IMD/SDMA data — see below for why and how to fix that. |
| **Accessibility score** | Heuristic | Transparent, explainable formula (see `backend/accessibility.py`) — not a certified government isolation index. |

### Why the live layer is simulated, and how to make it real

Real live feeds — GSI Bhukosh landslide susceptibility/alerts, IMD weather
nowcasts, State Disaster Management Authority bulletins, NHAI traffic data —
require registered API access, MoUs, or paid data agreements that are outside
what a prototype can wire up. **What this project does instead is build the
exact pipe those feeds would plug into**, so swapping simulation for
reality is a one-function change, not a rewrite:

```python
# backend/incident_store.py
def report_incident(node_a, node_b, incident_type, severity, description=None, ...):
    ...
```

Right now this is called by:
1. A background thread (`start_simulator`) that mimics a live feed, biased
   toward the real high-risk corridors listed above.
2. `POST /api/incidents` — a manual "control room" endpoint, used in the
   demo to let a judge trigger a landslide live and watch the map react.

In production you add a **third caller**: a poller/webhook that ingests the
real GSI/IMD/SDMA feed, maps the reported location to the nearest graph
edge, and calls this same `report_incident()`. Nothing downstream —
routing, the map, the live feed to browsers — needs to change. That
plug-and-play boundary is the actual engineering contribution to point to
in your SIH report.

---

## 3. Architecture

```
Browser (Leaflet map + vanilla JS)
   |
   |  REST: GET /api/locations, POST /api/route, GET/POST /api/incidents,
   |        GET /api/accessibility
   |  SSE:  GET /api/stream  (live push, no polling)
   v
Flask app (backend/app.py)
   |
   +-- graph_data.py       real towns + real NH corridors
   +-- routing_engine.py   networkx graph, rebuilt with live weights on
   |                       every request; Yen's k-shortest-paths
   +-- incident_store.py   live incident state + pub/sub for SSE +
   |                       background simulator (== production plug-point)
   +-- accessibility.py    explainable 0-100 isolation-risk scoring
```

**Why Server-Sent Events instead of WebSockets?** SSE needs zero extra
dependencies (it's plain HTTP + Flask's `Response` streaming), survives
proxies/firewalls better for a one-directional server→client feed, and is
exactly the right tool for "push incidents to everyone watching" — a
logistics dashboard doesn't need bidirectional low-latency messaging, so
this is the simpler-is-better choice.

**Why rebuild the graph on every request instead of caching it?** Live
weights depend on active incidents, which can change at any second. Caching
would risk serving stale "safe" routes during exactly the moment it
matters most (an active landslide). NE India's ~30-edge, 26-node graph
rebuilds in well under a millisecond, so there's no performance reason to
cache it.

---

## 4. How the "best route" decision actually works

For every request, each road segment's travel time is:

```
live_time = (distance_km / terrain_base_speed) × condition_multiplier
```

`condition_multiplier` comes from any active incident on that segment
(1.3× for minor, 2× for moderate, 5× — effectively closed — for severe).
The app then finds several loopless alternative paths (Yen's algorithm) and
ranks them:

1. Any route through a currently-**severe/blocked** segment sinks to
   **"not advisable"** regardless of raw distance — but is still *shown*,
   with the specific incident, so the user understands why to avoid it
   instead of it silently vanishing.
2. Among the rest, the lowest live-weighted-time route is **"recommended"**.
3. Remaining candidates are **"alternate"** — you can tap any of them on
   the map; nothing is hidden from you, unlike a black-box "best route
   only" tool.

---

## 5. Extending this

- **Real live data**: wire GSI/IMD/SDMA/NHAI feeds into `report_incident()`
  as described above.
- **More granular network**: add intermediate villages/junctions as nodes
  if you need finer-grained "which exact 5km stretch is blocked" reporting
  — the data model already supports it, just extend `graph_data.py`.
- **Real road geometry**: currently each edge is drawn as a straight line
  between two towns for clarity/performance. For a production map, replace
  `seg.coords` with an actual OSRM/GraphHopper polyline per edge.
- **Persistence**: incidents currently live in memory (reset on restart).
  Swap `incident_store.py`'s dict for SQLite/Postgres for a real deployment.
- **Mobile app / SMS fallback**: many of these towns have patchy data
  connectivity — a genuinely NE-India-appropriate accessibility feature
  would be an SMS-based route/alert query for low-connectivity users.

---

## 6. File guide

```
ne-logistics/
├── requirements.txt
├── README.md
├── backend/
│   ├── app.py              Flask app + all REST/SSE endpoints
│   ├── graph_data.py       real node/edge dataset + provenance notes
│   ├── routing_engine.py   live-weighted graph + k-shortest-paths
│   ├── incident_store.py   live incident pub/sub + simulator
│   └── accessibility.py    accessibility scoring
└── frontend/
    ├── index.html
    └── static/
        ├── style.css
        └── app.js          Leaflet map, SSE client, all UI logic
```

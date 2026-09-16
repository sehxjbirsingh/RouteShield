"""
app.py
=================
Flask backend for the NE India Smart Logistics & Accessibility
Intelligence Platform.

Run:
    pip install -r requirements.txt
    python app.py
Then open http://localhost:5000 in a browser.

Endpoints:
    GET  /                          -> serves the frontend
    GET  /api/locations             -> all real locations (for dropdowns/map)
    POST /api/route                 -> {source, destination} -> ranked routes
    GET  /api/incidents             -> currently active incidents
    POST /api/incidents             -> report a new incident (control room / demo)
    POST /api/incidents/<id>/clear  -> manually clear an incident
    GET  /api/accessibility         -> accessibility score for every location
    GET  /api/accessibility/<name>  -> accessibility score for one location
    GET  /api/stream                -> Server-Sent Events: live incident feed
"""

import json
import time

from flask import Flask, jsonify, request, Response, send_from_directory

import accessibility
import incident_store
import incident_feed
import routing_engine
import ai_service
from graph_data import NODES

app = Flask(__name__, static_folder="../frontend/static", static_url_path="/static")


# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return send_from_directory("../frontend", "index.html")


# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------
@app.route("/api/locations")
def get_locations():
    return jsonify([
        {"name": name, **attrs} for name, attrs in NODES.items()
    ])


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------
@app.route("/api/route", methods=["POST"])
def post_route():
    body = request.get_json(force=True, silent=True) or {}
    source = body.get("source")
    destination = body.get("destination")

    if not source or not destination:
        return jsonify({"error": "Both 'source' and 'destination' are required"}), 400

    try:
        routes = routing_engine.find_routes(source, destination, k=3)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    if not routes:
        return jsonify({"error": f"No route currently exists between {source} and {destination}"}), 404

    return jsonify({"source": source, "destination": destination, "routes": routes})
@app.route("/api/live-risk", methods=["POST"])
def live_risk():

    body = request.get_json(
        force=True,
        silent=True
    ) or {}

    source = body.get("source")
    destination = body.get("destination")

    if not source or not destination:
        return jsonify({
            "error": "Source and destination are required"
        }), 400

    try:

        routes = routing_engine.find_routes(
            source,
            destination,
            k=3
        )

        return jsonify({
            "source": source,
            "destination": destination,
            "routes": routes,
            "updated_at": time.time()
        })

    except Exception as e:

        return jsonify({
            "error": str(e)
        }), 500
# ---------------------------------------------------------------------------
# Gemini AI Route & Risk Analysis
# ---------------------------------------------------------------------------
@app.route("/api/ai-analysis", methods=["POST"])
def ai_analysis():
    body = request.get_json(force=True, silent=True) or {}

    source = body.get("source")
    destination = body.get("destination")
    selected_route = body.get("selected_route")
    routes = body.get("routes", [])

    if not source or not destination or not selected_route:
        return jsonify({
            "error": "Source, destination and selected route are required"
        }), 400

    try:
        incidents = incident_store.list_active_incidents()

        route_data = {
            "source": source,
            "destination": destination,
            "selected_route": selected_route,
            "alternative_routes": routes,
            "active_incidents": incidents
        }

        analysis = ai_service.analyze_route(route_data)

        return jsonify({
            "analysis": analysis
        })

    except Exception as e:
        return jsonify({
            "error": str(e)
        }), 500

# ---------------------------------------------------------------------------
# Incidents (the live layer)
# ---------------------------------------------------------------------------
@app.route("/api/incidents", methods=["GET"])
def get_incidents():
    return jsonify(incident_store.list_active_incidents())


@app.route("/api/incidents", methods=["POST"])
def post_incident():

    return jsonify({
        "error": "Manual incident reporting is disabled. NE-SLAI uses live government alerts."
    }), 403


@app.route("/api/incidents/<incident_id>/clear", methods=["POST"])
def clear_incident(incident_id):
    incident = incident_store.clear_incident(incident_id)
    if not incident:
        return jsonify({"error": "Incident not found or already cleared"}), 404
    return jsonify(incident)


# ---------------------------------------------------------------------------
# Accessibility Intelligence
# ---------------------------------------------------------------------------
@app.route("/api/accessibility")
def get_all_accessibility():
    return jsonify(accessibility.score_all_locations())


@app.route("/api/accessibility/<location>")
def get_one_accessibility(location):
    try:
        return jsonify(accessibility.score_location(location))
    except ValueError as e:
        return jsonify({"error": str(e)}), 404


# ---------------------------------------------------------------------------
# Real-time stream (Server-Sent Events)
# ---------------------------------------------------------------------------
@app.route("/api/stream")
def stream():
    def event_stream():
        q = incident_store.subscribe()
        try:
            # Send a hello + current snapshot so a freshly-opened tab is in sync
            yield f"event: snapshot\ndata: {json.dumps(incident_store.list_active_incidents())}\n\n"
            last_ping = time.time()
            while True:
                try:
                    item = q.get(timeout=15)
                    yield f"event: {item['event']}\ndata: {json.dumps(item['data'])}\n\n"
                except Exception:
                    pass
                if time.time() - last_ping > 15:
                    yield "event: ping\ndata: {}\n\n"
                    last_ping = time.time()
        finally:
            incident_store.unsubscribe(q)

    return Response(event_stream(), mimetype="text/event-stream")

# Start the NDMA live incident feed when the app is loaded.
# This is required for Gunicorn/Render production deployment.
incident_feed.start()


if __name__ == "__main__":
    app.run(
        debug=True,
        threaded=True,
        port=5000,
        use_reloader=False
    )

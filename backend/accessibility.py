"""
accessibility.py
=================
"Accessibility Intelligence" for each location: a transparent, explainable
0-100 score describing how reliably that town can be reached, independent
of any single trip. This is the piece that goes beyond point-to-point
routing and answers the SIH brief's "accessibility" half.

Scoring methodology (kept simple and defensible on purpose — a judge should
be able to follow exactly why a place scored what it scored):

  +40  base score
  +20  if the location has all-weather plains connectivity to the network
        (terrain == "plains")
  +10  if the location itself has a hospital/major health facility
  + up to 20 for "road redundancy": more independent road connections to
        the rest of the network = lower risk of total isolation if any
        single road is cut. (min(degree, 4) * 5)
  -  5 per currently-active incident touching any edge connected to this
        node (live signal — an isolated town during an active landslide
        scores lower right now, not just in general)
  - 15 if terrain is "mountainous" (steep terrain = higher structural risk
        of monsoon disruption, independent of live incidents)
  -  7 if terrain is "hilly"

Score is clamped to [0, 100]. This is a heuristic, not a certified
government isolation index — the README says so explicitly.
"""

from graph_data import NODES, EDGES
import incident_store


def _degree(node_name):
    return sum(1 for a, b, *_ in EDGES if a == node_name or b == node_name)


def _active_incidents_touching(node_name):
    neighbors = set()
    for a, b, *_ in EDGES:
        if a == node_name:
            neighbors.add(b)
        elif b == node_name:
            neighbors.add(a)
    count = 0
    for n in neighbors:
        if incident_store.get_edge_condition(node_name, n):
            count += 1
    return count


def score_location(node_name):
    if node_name not in NODES:
        raise ValueError(f"Unknown location '{node_name}'")
    attrs = NODES[node_name]

    score = 40
    reasons = ["Base connectivity score: +40"]

    if attrs["terrain"] == "plains":
        score += 20
        reasons.append("All-weather plains terrain: +20")
    elif attrs["terrain"] == "hilly":
        score -= 7
        reasons.append("Hilly terrain, seasonal risk: -7")
    elif attrs["terrain"] == "mountainous":
        score -= 15
        reasons.append("Mountainous terrain, high structural risk: -15")

    if attrs["hospital"]:
        score += 10
        reasons.append("Has hospital / major health facility: +10")
    else:
        reasons.append("No major health facility recorded: +0")

    deg = _degree(node_name)
    redundancy_bonus = min(deg, 4) * 5
    score += redundancy_bonus
    reasons.append(f"Road redundancy ({deg} connecting roads): +{redundancy_bonus}")

    active = _active_incidents_touching(node_name)
    if active:
        penalty = active * 5
        score -= penalty
        reasons.append(f"{active} currently-active incident(s) on connecting roads: -{penalty}")

    score = max(0, min(100, score))

    if score >= 75:
        tier = "High accessibility"
    elif score >= 50:
        tier = "Moderate accessibility"
    elif score >= 30:
        tier = "Vulnerable — monitor closely"
    else:
        tier = "High isolation risk right now"

    return {
        "location": node_name,
        "state": attrs["state"],
        "score": score,
        "tier": tier,
        "connecting_roads": deg,
        "active_incidents_nearby": active,
        "reasons": reasons,
    }


def score_all_locations():
    return [score_location(n) for n in NODES.keys()]

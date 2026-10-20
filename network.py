"""Scammer network: calls are nodes, and two calls are joined when they share a routing number, account number or phone number."""
import json
from itertools import combinations
import db

GREEN, ORANGE, RED = "#2e9e5b", "#f79009", "#d92d20"


def risk_color(score):
    """green < 40, orange 40-70, red > 70."""
    score = score or 0
    return GREEN if score < 40 else ORANGE if score <= 70 else RED


def _shared_keys(entities):
    """{(type, normalised value): display value} for every routing / account / phone in a call's entities."""
    keys = {}
    for e in entities:
        if e.get("type") == "bank_account":
            for etype, field in (("routing", "routing_number"), ("account", "account_number")):
                v = db.digits(e.get(field))
                if v:
                    keys[(etype, v)] = e[field]
        elif e.get("type") == "phone_number":
            v = db.normalize_phone(e.get("value"))
            if v:
                keys[("phone", v)] = str(e["value"])
    return keys


def build_network():
    """{nodes, edges} for every call that has an extraction. Calls with no links still become (isolated) nodes."""
    nodes, keysets = [], {}
    for c in db.rows("SELECT id,started_at,extraction_json FROM calls WHERE extraction_json IS NOT NULL ORDER BY started_at ASC, rowid ASC"):
        try:
            ex = json.loads(c["extraction_json"]) or {}
        except ValueError:
            continue
        risk = ex.get("risk_score") or 0
        date = (c["started_at"] or "")[:10]
        nodes.append({"id": c["id"], "call_id": c["id"], "label": f"{date}\n{ex.get('scam_type') or 'unknown'}",
                      "color": risk_color(risk), "risk_score": risk, "summary": ex.get("summary") or "",
                      "scam_type": ex.get("scam_type"), "date": date,
                      "repeat_caller": "repeat_caller" in (ex.get("red_flags") or [])})
        keysets[c["id"]] = _shared_keys(ex.get("entities") or [])

    edges = []
    for a, b in combinations(keysets, 2):
        for key in keysets[a].keys() & keysets[b].keys():
            etype, _ = key
            shown = keysets[a][key]
            edges.append({"from": a, "to": b, "shared_entity_type": etype,
                          "label": f"{etype}: {shown}"})
    edges.sort(key=lambda e: (e["from"], e["to"], e["shared_entity_type"]))
    return {"nodes": nodes, "edges": edges}

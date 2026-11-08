"""Deterministic S21/F01 evidence for the single-qubit simulator.

These curves are examples for workflow/audit integration, not physical fits or
hardware measurements. No candidate from this module may be written to a real
device parameter store.
"""

from __future__ import annotations

import hashlib
import json
import math


def _artifact(recipe_id: str, points: list[dict], analysis: dict) -> dict:
    canonical = json.dumps(points, ensure_ascii=False, separators=(",", ":"))
    return {
        "recipeId": recipe_id,
        "dataSource": "simulator",
        "format": "json-points-v1",
        "pointCount": len(points),
        "sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "points": points,
        "analysis": analysis,
        "publishAllowed": False,
    }


def s21_trace() -> dict:
    center_hz, span_hz, count = 6_000_000_000, 40_000_000, 41
    resonance_hz = center_hz + 2_000_000
    points = []
    for index in range(count):
        frequency_hz = center_hz - span_hz / 2 + span_hz * index / (count - 1)
        detuning = (frequency_hz - resonance_hz) / 3_000_000
        magnitude_db = -12 - 16 / (1 + detuning * detuning) + math.sin(index * 1.7) * 0.025
        points.append({"frequencyHz": frequency_hz, "magnitudeDb": round(magnitude_db, 6)})
    minimum = min(range(count), key=lambda index: points[index]["magnitudeDb"])
    depth_db = (points[0]["magnitudeDb"] + points[-1]["magnitudeDb"]) / 2 - points[minimum]["magnitudeDb"]
    passed = 0 < minimum < count - 1 and depth_db >= 3
    analysis = {
        "quality": "pass" if passed else "inconclusive",
        "reason": None if passed else "NO_SIGNIFICANT_INTERIOR_DIP",
        "candidateHz": points[minimum]["frequencyHz"] if passed else None,
        "depthDb": round(depth_db, 3),
        "method": "discrete-minimum-demo-v1",
    }
    return _artifact("readout_resonator_scan", points, analysis)


def f01_trace() -> dict:
    center_hz, span_hz, count = 5_100_000_000, 100_000_000, 101
    points = []
    for index in range(count):
        frequency_hz = center_hz - span_hz / 2 + span_hz * index / (count - 1)
        detuning = (frequency_hz - center_hz) / 8_000_000
        response = 0.1 + 0.75 / (1 + detuning * detuning) + math.sin(index * 1.31) * 0.002
        points.append({"frequencyHz": frequency_hz, "response": round(response, 6)})
    peak = max(range(count), key=lambda index: points[index]["response"])
    height = points[peak]["response"] - (points[0]["response"] + points[-1]["response"]) / 2
    passed = 0 < peak < count - 1 and height >= 0.1
    analysis = {
        "quality": "pass" if passed else "inconclusive",
        "reason": None if passed else "WEAK_OR_BOUNDARY_PEAK",
        "candidateHz": points[peak]["frequencyHz"] if passed else None,
        "peakHeight": round(height, 5),
        "method": "discrete-maximum-demo-v1",
        "sourceNodeId": "ReadoutFreqDip",
        "sourceParameterRevision": None,
    }
    return _artifact("f01_spectroscopy", points, analysis)


def trace_for_node(node_id: str) -> dict | None:
    if node_id == "ReadoutFreqDip":
        return s21_trace()
    if node_id == "F01Spectroscopy":
        return f01_trace()
    return None

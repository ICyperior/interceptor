"""Integration tests for POST /signalid/match route."""

from __future__ import annotations

import json

import pytest


@pytest.fixture
def client():
    """Minimal Flask test client with only the signalid blueprint registered."""
    from flask import Flask

    from routes.signalid import signalid_bp

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(signalid_bp)

    with app.test_client() as c:
        yield c


class TestSignalidMatchRoute:
    def test_missing_frequency_returns_400(self, client):
        resp = client.post(
            "/signalid/match",
            data=json.dumps({}),
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_invalid_frequency_returns_400(self, client):
        resp = client.post(
            "/signalid/match",
            data=json.dumps({"frequency_mhz": -1}),
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_valid_request_returns_200(self, client):
        resp = client.post(
            "/signalid/match",
            data=json.dumps({"frequency_mhz": 98.5}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert isinstance(data["matches"], list)
        assert isinstance(data["match_count"], int)

    def test_fm_broadcast_in_results_at_98mhz(self, client):
        resp = client.post(
            "/signalid/match",
            data=json.dumps({"frequency_mhz": 98.5, "modulation": "WFM"}),
            content_type="application/json",
        )
        data = resp.get_json()
        names = [m["name"] for m in data["matches"]]
        assert "FM Broadcast Radio" in names

    def test_no_matches_returns_empty_list(self, client):
        resp = client.post(
            "/signalid/match",
            data=json.dumps({"frequency_mhz": 5000.0}),
            content_type="application/json",
        )
        data = resp.get_json()
        assert data["status"] == "ok"
        assert data["matches"] == []
        assert data["match_count"] == 0

    def test_limit_param_respected(self, client):
        resp = client.post(
            "/signalid/match",
            data=json.dumps({"frequency_mhz": 98.5, "limit": 2}),
            content_type="application/json",
        )
        data = resp.get_json()
        assert len(data["matches"]) <= 2

    def test_bandwidth_hz_accepted(self, client):
        resp = client.post(
            "/signalid/match",
            data=json.dumps({"frequency_mhz": 98.5, "bandwidth_hz": 200000}),
            content_type="application/json",
        )
        assert resp.status_code == 200

    def test_cached_true_on_second_identical_request(self, client):
        import routes.signalid as signalid_module

        signalid_module._match_cache.clear()
        payload = json.dumps({"frequency_mhz": 98.5})
        client.post("/signalid/match", data=payload, content_type="application/json")
        resp2 = client.post("/signalid/match", data=payload, content_type="application/json")
        data = resp2.get_json()
        assert data["cached"] is True

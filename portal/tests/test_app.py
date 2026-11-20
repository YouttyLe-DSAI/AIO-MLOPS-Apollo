"""Model Portal tests: auth against MLflow, CSRF guard, rendering, workflow dispatch (no network)."""

import base64

import httpx
import pytest
from fastapi.testclient import TestClient

import app as portal

AUTH = {"Authorization": "Basic " + base64.b64encode(b"hoang:secret").decode()}
SAME_ORIGIN = {**AUTH, "Origin": "http://testserver"}

VERSIONS = {
    "model_versions": [
        {"version": "1", "creation_timestamp": 1795140000000, "status": "READY",
         "tags": [{"key": "model_version_id", "value": "resnet18-placeholder-fp32-v1"},
                  {"key": "variant", "value": "placeholder"}, {"key": "target_precision", "value": "FP32"},
                  {"key": "offline_top1_accuracy", "value": "0.3853"},
                  {"key": "benchmark.max_images_per_s", "value": "12345.6"}]},
        {"version": "2", "creation_timestamp": 1795150000000, "status": "READY",
         "tags": [{"key": "model_version_id", "value": "resnet18-baseline-fp16-v1"},
                  {"key": "target_precision", "value": "FP16"}, {"key": "offline_top1_accuracy", "value": "0.9480"}]},
    ]
}


class FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code, self._payload, self.text = status_code, payload or {}, ""

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=None)


@pytest.fixture
def client(monkeypatch, tmp_path):
    portal._auth_cache.clear()
    portal._runs_cache.clear()
    calls = {"dispatch": []}

    def fake_get(url, **kw):
        if url.endswith("/registered-models/search"):
            return FakeResponse(200 if kw["auth"] == ("hoang", "secret") else 401)
        if url.endswith("/model-versions/search"):
            return FakeResponse(200, VERSIONS)
        if "/actions/workflows/" in url:
            return FakeResponse(200, {"workflow_runs": [{"status": "completed", "conclusion": "success",
                                                         "display_title": "release v1", "html_url": "https://x",
                                                         "created_at": "2026-11-20T03:40:00Z"}]})
        raise AssertionError(url)

    def fake_post(url, **kw):
        calls["dispatch"].append((url, kw["json"]))
        return FakeResponse(204)

    monkeypatch.setattr(portal.httpx, "get", fake_get)
    monkeypatch.setattr(portal.httpx, "post", fake_post)
    monkeypatch.setattr(portal, "serving_now", lambda: {"tag": "resnet18-placeholder-fp32-v1", "image": "x", "replicas": 1, "ready": 1})
    token = tmp_path / "token"
    token.write_text("ghp_test\n")
    monkeypatch.setattr(portal, "GITHUB_TOKEN_FILE", token)
    c = TestClient(portal.app)
    c.calls = calls
    return c


def test_requires_mlflow_login(client):
    assert client.get("/").status_code == 401
    bad = {"Authorization": "Basic " + base64.b64encode(b"hoang:wrong").decode()}
    assert client.get("/", headers=bad).status_code == 401
    assert client.get("/healthz").text == "ok"


def test_lists_versions_newest_first_and_marks_serving(client):
    r = client.get("/", headers=AUTH)
    assert r.status_code == 200
    body = r.text
    table = body[body.index("<tbody>"):]
    assert table.index("resnet18-baseline-fp16-v1") < table.index("resnet18-placeholder-fp32-v1")
    assert 'class="serving"' in body and "38.53%" in body and "12345.6" in body


def test_release_dispatches_workflow(client):
    r = client.post("/actions/release", data={"version": "2"}, headers=SAME_ORIGIN, follow_redirects=False)
    assert r.status_code == 303 and "msg=" in r.headers["location"]
    url, payload = client.calls["dispatch"][0]
    assert url.endswith("/actions/workflows/release.yml/dispatches")
    assert payload == {"ref": "main", "inputs": {"model_name": "resnet18-cifar10", "model_version": "2",
                                                  "requested_by": "portal:hoang"}}


def test_benchmark_passes_keep_serving(client):
    client.post("/actions/benchmark", data={"version": "1", "keep_serving": "true"}, headers=SAME_ORIGIN, follow_redirects=False)
    url, payload = client.calls["dispatch"][0]
    assert url.endswith("/benchmark.yml/dispatches") and payload["inputs"]["keep_serving"] == "true"


def test_rejects_cross_site_post_and_unknown_version(client):
    evil = {**AUTH, "Origin": "https://evil.example"}
    assert client.post("/actions/release", data={"version": "1"}, headers=evil).status_code == 403
    assert client.post("/actions/release", data={"version": "99"}, headers=SAME_ORIGIN).status_code == 404
    assert client.calls["dispatch"] == []


def test_read_only_without_github_token(client, monkeypatch, tmp_path):
    monkeypatch.setattr(portal, "GITHUB_TOKEN_FILE", tmp_path / "missing")
    assert "disabled" in client.get("/", headers=AUTH).text
    r = client.post("/actions/release", data={"version": "1"}, headers=SAME_ORIGIN, follow_redirects=False)
    assert r.status_code == 303 and "err=" in r.headers["location"]
    assert client.calls["dispatch"] == []

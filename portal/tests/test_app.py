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
                  {"key": "benchmark.run_id", "value": "run-v1"},
                  {"key": "benchmark.max_images_per_s", "value": "12345.6"},
                  {"key": "benchmark.smoke_top1", "value": "0.3853"}]},
        {"version": "2", "creation_timestamp": 1795150000000, "status": "READY",
         "tags": [{"key": "model_version_id", "value": "resnet18-baseline-fp16-v1"},
                  {"key": "target_precision", "value": "FP16"}, {"key": "offline_top1_accuracy", "value": "0.9480"}]},
    ]
}
DONE_RUN = {"id": 7, "status": "completed", "conclusion": "success", "display_title": "release resnet18-cifar10 v1 (portal:hoang)",
            "html_url": "https://x/7", "created_at": "2026-11-20T03:40:00Z", "run_started_at": "2026-11-20T03:40:05Z",
            "updated_at": "2026-11-20T03:52:00Z"}


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
    for cache in (portal._auth_cache, portal._runs_cache, portal._steps_cache, portal._metrics_cache):
        cache.clear()
    calls = {"dispatch": []}
    state = {"runs": [DONE_RUN], "steps": [], "metrics": {}}

    def fake_get(url, **kw):
        if url.endswith("/registered-models/search"):
            return FakeResponse(200 if kw["auth"] == ("hoang", "secret") else 401)
        if url.endswith("/model-versions/search"):
            return FakeResponse(200, VERSIONS)
        if url.endswith("/runs/get"):
            metrics = [{"key": k, "value": v} for k, v in state["metrics"].items()]
            return FakeResponse(200, {"run": {"data": {"metrics": metrics}}})
        if "/actions/workflows/" in url:
            return FakeResponse(200, {"workflow_runs": state["runs"]})
        if "/actions/runs/" in url and url.endswith("/jobs"):
            return FakeResponse(200, {"jobs": [{"steps": state["steps"]}]})
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
    c.calls, c.state = calls, state
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
    cards = body[body.index('id="versions"'):]
    assert cards.index("resnet18-baseline-fp16-v1") < cards.index("resnet18-placeholder-fp32-v1")
    assert 'class="mv serving"' in body and "38.53" in body and "12,346" in body
    assert "Triton khớp offline" in body  # smoke top-1 == offline top-1
    # nothing running: no auto-refresh, no highlighted pipeline stage
    assert "data-refresh" not in body and 'class="now"' not in body


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
    body = client.get("/", headers=AUTH).text
    assert "disabled" in body and "Chế độ chỉ xem" in body
    r = client.post("/actions/release", data={"version": "1"}, headers=SAME_ORIGIN, follow_redirects=False)
    assert r.status_code == 303 and "err=" in r.headers["location"]
    assert client.calls["dispatch"] == []


def test_live_progress_of_a_running_benchmark(client):
    client.state["runs"] = [{"id": 42, "status": "in_progress", "conclusion": None,
                             "display_title": "benchmark resnet18-cifar10 v1 (portal:hoang)", "html_url": "https://x/42",
                             "created_at": "2026-11-20T15:17:26Z", "run_started_at": "2026-11-20T15:17:30Z"}, DONE_RUN]
    client.state["steps"] = [
        {"name": "Set up job", "status": "completed"}, {"name": "kubeconfig", "status": "completed"},
        {"name": "Release the Model version (skipped when the image is already in ECR)", "status": "completed"},
        {"name": "Check out the GitOps repo", "status": "completed"},
        {"name": "Serve it (tag + replicas 1 in GitOps)", "status": "completed"},
        {"name": "Benchmark (Smoke test + Perf Analyzer matrix -> MLflow)", "status": "in_progress"},
        {"name": "Free the GPU (replicas 0)", "status": "pending"},
        {"name": "Post Run actions/checkout@v4", "status": "pending"},
    ]
    body = client.get("/", headers=AUTH).text
    assert f'data-refresh="{portal.REFRESH_S}"' in body
    assert "Benchmark v1" in body and "hoang" in body and "Đang benchmark" in body
    # 5 readable steps: prep, release, serve done; benchmark running; free the GPU waiting
    assert body.count('<li class="done"><span class="ck">') == 3
    assert '<li class="now"><span class="ck">' in body and "Đo hiệu năng" in body
    assert "Post Run" not in body and "Check out the GitOps repo" not in body
    assert portal.checklist(client.state["steps"])[-1] == ("Tắt Triton, trả GPU", "todo", 4)
    assert portal.current_stage([{"steps": client.state["steps"]}]) == 4


def test_queued_run_waits_for_the_previous_one(client):
    client.state["runs"] = [{"id": 43, "status": "queued", "conclusion": None,
                             "display_title": "release resnet18-cifar10 v2 (portal:hoang)", "html_url": "https://x/43",
                             "created_at": "2026-11-20T15:20:00Z"}]
    body = client.get("/", headers=AUTH).text
    assert "Đang xếp hàng" in body and "data-refresh" in body


def test_benchmark_charts_from_mlflow_metrics(client):
    client.state["metrics"] = ({f"c{c}_b8.images_per_s": 1000.0 * c for c in portal.CONCURRENCY}
                               | {f"c{c}_b1.p95_latency_ms": 1.0 + c / 10 for c in portal.CONCURRENCY})
    body = client.get("/", headers=AUTH).text
    assert body.count("<polyline") == 2
    assert "concurrency 32: 32,000 ảnh/s" in body and "concurrency 1: 1.10 ms" in body
    assert "So sánh các version" in body

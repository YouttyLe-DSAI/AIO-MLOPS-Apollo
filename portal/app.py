"""Model Portal: pick a Model version from the MLflow registry and trigger a release or a benchmark.

Architecture box "Model Portal (FastAPI): pick version, trigger a release". The portal holds no
state of its own:

- Login = an MLflow account (HTTP Basic, checked against MLflow); registry reads use the caller's
  own credentials, so MLflow permissions apply as they do in the MLflow UI.
- "Serving now" = the Triton Deployment in namespace `serving` (in-cluster ServiceAccount, read-only).
- Release / Benchmark = GitHub Actions workflow_dispatch (release.yml / benchmark.yml) with a
  fine-grained token (Actions: read and write on this repo only), mounted from Secret portal-github.
  Without that Secret the portal is read-only.

The page is made for demos: serving status, the pipeline, live progress of running workflows (the
page reloads itself while something runs), one card per Model version with its benchmark numbers,
and comparison charts drawn from the benchmark runs in MLflow (experiment "benchmark").
"""

from __future__ import annotations

import hashlib
import html
import math
import os
import re
import time
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit

import httpx
from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

MLFLOW_URL = os.environ.get("MLFLOW_URL", "http://mlflow.mlflow.svc.cluster.local")
MLFLOW_PUBLIC_URL = os.environ.get("MLFLOW_PUBLIC_URL", "https://mlflow.3-39-32-158.sslip.io")
GRAFANA_URL = os.environ.get("GRAFANA_URL", "https://grafana.3-39-32-158.sslip.io")
ARGOCD_URL = os.environ.get("ARGOCD_URL", "https://argocd.3-39-32-158.sslip.io")
REGISTERED_MODEL = os.environ.get("REGISTERED_MODEL", "resnet18-cifar10")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "YouttyLe-DSAI/AIO-MLOPS-Apollo")
GITHUB_TOKEN_FILE = Path(os.environ.get("GITHUB_TOKEN_FILE", "/var/run/secrets/portal-github/token"))
TRITON_NAMESPACE = os.environ.get("TRITON_NAMESPACE", "serving")
TRITON_DEPLOYMENT = os.environ.get("TRITON_DEPLOYMENT", "triton")
SA_DIR = Path("/var/run/secrets/kubernetes.io/serviceaccount")
WORKFLOWS = {"release": "release.yml", "benchmark": "benchmark.yml"}
WF_LABEL = {"release": "Release", "benchmark": "Benchmark"}
ACTIVE_STATUSES = {"queued", "in_progress", "waiting", "requested", "pending"}
CONCURRENCY = (1, 2, 4, 8, 16, 32)  # benchmark matrix (infra/benchmark/benchmark.yaml)
LOCAL_TZ = timezone(timedelta(hours=7), "ICT")  # giờ Việt Nam (no daylight saving)
REFRESH_S = 10  # page reload interval while a workflow runs

app = FastAPI(title="Apollo Model Portal", docs_url=None, redoc_url=None)
security = HTTPBasic(realm="Apollo Model Portal (MLflow account)")
_auth_cache: dict[str, float] = {}  # sha256(user:password) -> expiry
_runs_cache: dict[str, tuple[float, list]] = {}
_steps_cache: dict[int, tuple[float, list]] = {}
_metrics_cache: dict[str, dict[str, float]] = {}  # benchmark run_id -> metrics (a finished run never changes)


# ------------------------------------------------------------------ auth

def current_user(creds: HTTPBasicCredentials = Depends(security)) -> HTTPBasicCredentials:
    key = hashlib.sha256(f"{creds.username}:{creds.password}".encode()).hexdigest()
    if _auth_cache.get(key, 0) > time.time():
        return creds
    try:
        r = httpx.get(f"{MLFLOW_URL}/api/2.0/mlflow/registered-models/search", params={"max_results": 1},
                      auth=(creds.username, creds.password), timeout=10)
    except httpx.HTTPError as e:
        raise HTTPException(503, f"MLflow unreachable: {e}") from e
    if r.status_code != 200:
        raise HTTPException(401, "Sai tài khoản MLflow", headers={"WWW-Authenticate": 'Basic realm="Apollo Model Portal"'})
    _auth_cache[key] = time.time() + 300
    return creds


def same_origin(request: Request) -> None:
    """Basic auth is sent automatically by the browser, so POSTs must come from this page (CSRF)."""
    source = request.headers.get("origin") or request.headers.get("referer") or ""
    if urlsplit(source).netloc != request.headers.get("host"):
        raise HTTPException(403, "cross-site request refused")


# ------------------------------------------------------------------ data sources

def model_versions(creds: HTTPBasicCredentials) -> list[dict]:
    r = httpx.get(f"{MLFLOW_URL}/api/2.0/mlflow/model-versions/search",
                  params={"filter": f"name='{REGISTERED_MODEL}'", "max_results": 200},
                  auth=(creds.username, creds.password), timeout=15)
    r.raise_for_status()
    out = []
    for mv in r.json().get("model_versions", []):
        tags = {t["key"]: t["value"] for t in mv.get("tags", [])}
        out.append({"version": int(mv["version"]), "created": int(mv.get("creation_timestamp", 0)) // 1000,
                    "status": mv.get("status", ""), "tags": tags})
    return sorted(out, key=lambda m: m["version"], reverse=True)


def benchmark_metrics(run_id: str, creds: HTTPBasicCredentials) -> dict[str, float]:
    """Metrics of a benchmark run (c{c}_b{b}.images_per_s, .p95_latency_ms, ...); {} when unreadable.

    Cached across users: benchmark numbers are not secret and every MLflow user can read them.
    """
    if run_id in _metrics_cache:
        return _metrics_cache[run_id]
    try:
        r = httpx.get(f"{MLFLOW_URL}/api/2.0/mlflow/runs/get", params={"run_id": run_id},
                      auth=(creds.username, creds.password), timeout=10)
    except httpx.HTTPError:
        return {}
    if r.status_code != 200:
        return {}
    metrics = {m["key"]: float(m["value"]) for m in r.json().get("run", {}).get("data", {}).get("metrics", [])}
    _metrics_cache[run_id] = metrics
    return metrics


def serving_now() -> dict:
    """Image tag, desired and ready replicas of the Triton Deployment (None outside the cluster)."""
    token_file = SA_DIR / "token"
    if not token_file.exists():
        return {"error": "not running in the cluster"}
    try:
        r = httpx.get(f"https://kubernetes.default.svc/apis/apps/v1/namespaces/{TRITON_NAMESPACE}/deployments/{TRITON_DEPLOYMENT}",
                      headers={"Authorization": f"Bearer {token_file.read_text().strip()}"},
                      verify=str(SA_DIR / "ca.crt"), timeout=5)
    except httpx.HTTPError as e:
        return {"error": str(e)}
    if r.status_code == 404:
        return {"error": "Triton chưa được deploy (chưa có release nào)"}
    if r.status_code != 200:
        return {"error": f"Kubernetes API HTTP {r.status_code}"}
    d = r.json()
    image = d["spec"]["template"]["spec"]["containers"][0]["image"]
    return {"tag": image.rsplit(":", 1)[-1], "image": image, "replicas": d["spec"].get("replicas", 0),
            "ready": d.get("status", {}).get("readyReplicas", 0)}


def github_token() -> str | None:
    try:
        token = GITHUB_TOKEN_FILE.read_text().strip()
    except OSError:
        return None
    return token or None


def github_headers() -> dict:
    h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token := github_token():
        h["Authorization"] = f"Bearer {token}"
    return h


def recent_runs(workflow: str) -> list[dict]:
    cached = _runs_cache.get(workflow)
    if cached and cached[0] > time.time():
        return cached[1]
    try:
        r = httpx.get(f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/{WORKFLOWS[workflow]}/runs",
                      params={"per_page": 5}, headers=github_headers(), timeout=10)
        runs = r.json().get("workflow_runs", []) if r.status_code == 200 else []
    except httpx.HTTPError:
        runs = []
    _runs_cache[workflow] = (time.time() + 8, runs)
    return runs


def run_steps(run_id: int) -> list[dict]:
    """Steps (name, status) of a running workflow, for the live progress box."""
    cached = _steps_cache.get(run_id)
    if cached and cached[0] > time.time():
        return cached[1]
    steps: list[dict] = []
    try:
        r = httpx.get(f"https://api.github.com/repos/{GITHUB_REPO}/actions/runs/{run_id}/jobs",
                      headers=github_headers(), timeout=10)
        if r.status_code == 200:
            steps = [s for job in r.json().get("jobs", []) for s in job.get("steps", [])]
    except httpx.HTTPError:
        pass
    _steps_cache[run_id] = (time.time() + 8, steps)
    return steps


TITLE_RE = re.compile(r"^(?P<wf>release|benchmark) (?P<model>\S+) v(?P<ver>\d+)(?: \((?P<by>[^)]*)\))?$")


def describe(workflow: str, r: dict) -> dict:
    """A GitHub run in the portal's terms; run-name is '<workflow> <model> v<N> (<requested_by>)'."""
    title = r.get("display_title") or r.get("name") or workflow
    m = TITLE_RE.match(title)
    actor = (r.get("triggering_actor") or r.get("actor") or {}).get("login", "")
    return {"id": r.get("id"), "workflow": m["wf"] if m else workflow, "title": title,
            "version": int(m["ver"]) if m else None, "by": m["by"] if m and m["by"] else actor,
            "url": r.get("html_url", ""), "status": r.get("status", ""), "conclusion": r.get("conclusion") or "",
            "created": r.get("created_at"), "started": r.get("run_started_at") or r.get("created_at"),
            "updated": r.get("updated_at")}


def collect_runs() -> list[dict]:
    seen, runs = set(), []
    for wf in WORKFLOWS:
        for r in recent_runs(wf):
            d = describe(wf, r)
            key = d["id"] or (d["title"], d["created"])
            if key not in seen:
                seen.add(key)
                runs.append(d)
    runs.sort(key=lambda d: d["created"] or "", reverse=True)
    for d in runs:
        if d["status"] in ACTIVE_STATUSES and d["id"]:
            d["steps"] = run_steps(d["id"])
    return runs


def dispatch(workflow: str, inputs: dict) -> None:
    if not github_token():
        raise HTTPException(503, "Portal chưa có GitHub token (Secret portal/portal-github): chỉ xem được, không trigger được.")
    r = httpx.post(f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/{WORKFLOWS[workflow]}/dispatches",
                   json={"ref": "main", "inputs": inputs}, headers=github_headers(), timeout=15)
    if r.status_code != 204:
        raise HTTPException(502, f"GitHub workflow_dispatch {workflow}: HTTP {r.status_code} {r.text[:300]}")
    _runs_cache.pop(workflow, None)


# ------------------------------------------------------------------ formatting

def esc(v) -> str:
    return html.escape("" if v is None else str(v))


def num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def pct(v) -> str:
    x = num(v)
    return "–" if x is None else f"{x * 100:.2f}"


def thousands(v) -> str:
    x = num(v)
    return "–" if x is None else f"{x:,.0f}"


def fixed(v, digits: int = 2) -> str:
    x = num(v)
    return "–" if x is None else f"{x:.{digits}f}"


def parse_time(v) -> datetime | None:
    if not v:
        return None
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(v, UTC)
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None


def when(v) -> str:
    dt = parse_time(v)
    return dt.astimezone(LOCAL_TZ).strftime("%d/%m %H:%M") if dt else "–"


def ago(v) -> str:
    dt = parse_time(v)
    if not dt:
        return ""
    s = int((datetime.now(UTC) - dt).total_seconds())
    if s < 60:
        return "vừa xong"
    if s < 3600:
        return f"{s // 60} phút trước"
    if s < 86400:
        return f"{s // 3600} giờ trước"
    return f"{s // 86400} ngày trước"


def duration(start, end=None) -> str:
    a, b = parse_time(start), (parse_time(end) if end else datetime.now(UTC))
    if not a or not b:
        return ""
    s = max(0, int((b - a).total_seconds()))
    if s < 60:
        return f"{s} giây"
    if s < 3600:
        return f"{s // 60} phút {s % 60} giây"
    return f"{s // 3600} giờ {s % 3600 // 60} phút"


def short_k(v: float) -> str:
    if v < 1000:
        return f"{v:.0f}"
    return f"{v / 1000:.1f}".rstrip("0").rstrip(".") + "k"


def nice_ceil(v: float) -> float:
    if v <= 0:
        return 1.0
    e = 10 ** math.floor(math.log10(v))
    return next(m * e for m in (1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10) if v <= m * e)


# ------------------------------------------------------------------ page pieces

ICONS = {
    "box": '<path d="M21 8 12 3 3 8l9 5 9-5Z"/><path d="M3 8v8l9 5 9-5V8"/><path d="M12 13v8"/>',
    "chip": '<rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4"/>',
    "layers": '<path d="m12 3 9 5-9 5-9-5 9-5Z"/><path d="m3 13 9 5 9-5"/>',
    "gauge": '<path d="M4.9 19a9 9 0 1 1 14.2 0"/><path d="m12 14 4-5"/>',
    "pulse": '<path d="M3 12h4l3 8 4-16 3 8h4"/>',
    "play": '<path d="M7 4.5v15l12-7.5-12-7.5Z"/>',
    "check": '<path d="m5 12 5 5 9-10"/>',
    "x": '<path d="M6 6l12 12M18 6 6 18"/>',
    "ext": '<path d="M14 4h6v6M20 4l-9 9"/><path d="M19 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h5"/>',
    "moon": '<path d="M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5Z"/>',
    "user": '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
    "github": ('<path d="M9 19c-4 1.5-4-2-6-2.5M15 21v-3.5c0-1 .1-1.4-.5-2 2.8-.3 5.5-1.4 5.5-6a4.6 4.6 0 0 0-1.3-3.2 '
               '4.2 4.2 0 0 0-.1-3.2s-1.1-.3-3.5 1.3a12 12 0 0 0-6.2 0C6.5 2.8 5.4 3.1 5.4 3.1a4.2 4.2 0 0 0-.1 3.2A4.6 '
               '4.6 0 0 0 4 9.5c0 4.6 2.7 5.7 5.5 6-.6.6-.6 1.2-.5 2V21"/>'),
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.5"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
}
FILLED_ICONS = {"play"}


def icon(name: str) -> str:
    cls = "ic f" if name in FILLED_ICONS else "ic"
    return f'<svg class="{cls}" viewBox="0 0 24 24" aria-hidden="true">{ICONS[name]}</svg>'


def prec_chip(precision: str) -> str:
    if not precision:
        return ""
    cls = {"FP32": "p-fp32", "FP16": "p-fp16", "INT8": "p-int8"}.get(precision.upper(), "p-other")
    return f'<span class="chip {cls}">{esc(precision)}</span>'


def variant_chip(tags: dict) -> str:
    return f'<span class="chip">{esc(tags["variant"])}</span>' if tags.get("variant") else ""


def stat(label: str, value: str, unit: str, cls: str = "stat") -> str:
    small = f"<small>{esc(unit)}</small>" if unit and value != "–" else ""
    return f'<div class="{cls}"><div class="k">{esc(label)}</div><div class="v">{esc(value)}{small}</div></div>'


def run_title(d: dict) -> str:
    return f'{WF_LABEL.get(d["workflow"], d["workflow"])} v{d["version"]}' if d["version"] is not None else d["title"]


def who(d: dict) -> str:
    by = d.get("by") or ""
    if by.startswith("portal:"):
        return f'{icon("user")}{esc(by[len("portal:"):])} qua Portal'
    return f'{icon("github")}{esc(by)} qua GitHub' if by else ""


# Steps of release.yml / benchmark.yml as the audience sees them: GitHub step-name prefix, label,
# pipeline stage (STAGES). Other steps before the first of these fold into "Chuẩn bị"; the rest are hidden.
MAIN_STEPS = (
    ("Build TensorRT Engine", "Release: build TensorRT engine trên GPU L4", 2),
    ("Release the Model version", "Release: TensorRT engine → ECR (bỏ qua nếu đã có)", 2),
    ("Pin the Serving image tag", "Deploy: GitOps → Argo CD → Triton", 3),
    ("Serve it", "Bật Triton với version này (GitOps → Argo CD)", 3),
    ("Benchmark (", "Đo hiệu năng: smoke test + Perf Analyzer", 4),
    ("Free the GPU", "Tắt Triton, trả GPU", 4),
)
PREP_STEP = ("Chuẩn bị: runner, đăng nhập AWS (OIDC), kết nối EKS", 2)
HIDDEN_STEPS = ("Check out the GitOps repo", "Post ", "Complete job")


def checklist(steps: list[dict]) -> list[tuple[str, str, int]]:
    """Fold GitHub steps into a few readable ones: (label, done | now | todo, pipeline stage)."""
    prep: list[str] = []
    items: list[tuple[str, str, int]] = []
    for s in steps:
        name, status = s.get("name", ""), s.get("status", "")
        main = next((m for m in MAIN_STEPS if name.startswith(m[0])), None)
        if main:
            items.append((main[1], status, main[2]))
        elif not items and not name.startswith(HIDDEN_STEPS):
            prep.append(status)
    if prep:
        if all(x == "completed" for x in prep):
            st = "completed"
        elif any(x in ("in_progress", "completed") for x in prep):
            st = "in_progress"
        else:
            st = "queued"
        items.insert(0, (PREP_STEP[0], st, PREP_STEP[1]))
    out = [(label, "done" if st == "completed" else "now" if st == "in_progress" else "todo", stage)
           for label, st, stage in items]
    if not any(s == "now" for _, s, _ in out):  # between two steps: the next one is about to start
        nxt = next((i for i, (_, s, _) in enumerate(out) if s == "todo"), None)
        if nxt is not None:
            out[nxt] = (out[nxt][0], "now", out[nxt][2])
    return out


def current_stage(active: list[dict]) -> int:
    stages = [stage for d in active for _, s, stage in checklist(d.get("steps", [])) if s == "now"]
    return max(stages) if stages else (2 if active else 0)


STAGES = (
    ("box", "Đăng ký", "Model version (ONNX + meta.yaml) vào MLflow Registry"),
    ("chip", "Release", "Build TensorRT engine trên GPU L4, image lên ECR"),
    ("layers", "Deploy", "GitOps → Argo CD → NVIDIA Triton"),
    ("gauge", "Benchmark", "Smoke test + Perf Analyzer, kết quả vào MLflow"),
    ("pulse", "Theo dõi", "Grafana: throughput, latency, GPU"),
)


def pipeline(stage: int) -> str:
    out = []
    for i, (ic, name, desc) in enumerate(STAGES, 1):
        cls = "now" if i == stage else ("done" if stage and i < stage else "")
        attr = f' class="{cls}"' if cls else ""
        out.append(f'<li{attr}><div class="st-ic">{icon(ic)}</div><div><div class="st-n">{i}. {name}</div>'
                   f'<div class="st-d">{desc}</div></div></li>')
    return "".join(out)


def serving_card(serving: dict, versions: list[dict]) -> str:
    head = f'<div class="card-k">{icon("chip")}Triton đang phục vụ</div>'
    if "error" in serving:
        return f'<div class="card serve off">{head}<div class="serve-id">–</div><div class="serve-note">{esc(serving["error"])}</div></div>'
    tag, replicas, ready = serving.get("tag", ""), serving.get("replicas", 0), serving.get("ready", 0)
    mv = next((m for m in versions if m["tags"].get("model_version_id") == tag), None)
    t = mv["tags"] if mv else {}
    if ready:
        state, label, note = "on", "Đang phục vụ", f"{ready}/{replicas} pod sẵn sàng trên GPU NVIDIA L4 · endpoint trong cluster: triton.serving:8000"
    elif replicas:
        state, label, note = "starting", "Đang khởi động", "Đang bật node GPU L4 và tải image Triton (khoảng 5–10 phút)."
    else:
        state, label, note = "off", "Đang tắt", "replicas 0: đã trả GPU nên không tốn tiền. Benchmark (tick “Giữ Triton chạy”) sẽ bật lại."
    chips = f'<div class="chips"><span class="chip">v{mv["version"]}</span>{prec_chip(t.get("target_precision", ""))}{variant_chip(t)}</div>' if mv else ""
    stats = (stat("Top-1 offline", pct(t.get("offline_top1_accuracy")), "%")
             + stat("Throughput tối đa", thousands(t.get("benchmark.max_images_per_s")), "ảnh/s")
             + stat("Latency p95 · c1 b1", fixed(t.get("benchmark.p95_ms_c1_b1")), "ms")
             + stat("Pod sẵn sàng", f"{ready}/{replicas}", ""))
    return (f'<div class="card serve {state}">{head}<div class="serve-state"><span class="dot {state}"></span>{label}</div>'
            f'<div class="serve-id">{esc(tag)}</div>{chips}<div class="serve-note">{esc(note)}</div>'
            f'<div class="stats">{stats}</div></div>')


CHECK_MARK = {"done": icon("check"), "now": '<span class="spin sm"></span>', "todo": ""}


def job_block(d: dict) -> str:
    items = checklist(d.get("steps", []))
    if items:
        done = sum(1 for _, s, _ in items if s == "done")
        now = any(s == "now" for _, s, _ in items)
        width = int(100 * (done + (0.5 if now else 0)) / len(items))
        lis = "".join(f'<li class="{s}"><span class="ck">{CHECK_MARK[s]}</span>{esc(label)}</li>' for label, s, _ in items)
        body = f'<div class="bar"><span style="width:{width}%"></span></div><ol class="checklist">{lis}</ol>'
    else:
        body = f'<div class="queued">{icon("clock")}Đang xếp hàng: chờ tác vụ trước chạy xong (Release và Benchmark chạy lần lượt).</div>'
    return (f'<div class="job"><div class="job-top"><span class="spin"></span><b>{esc(run_title(d))}</b>'
            f'<span class="wf {esc(d["workflow"])}">{esc(d["workflow"])}</span></div>'
            f'<div class="job-sub">{who(d)} · bắt đầu {esc(when(d["started"]))} · đã chạy {esc(duration(d["started"]))}</div>'
            f'{body}<a class="job-link" href="{esc(d["url"])}">Xem log trên GitHub Actions {icon("ext")}</a></div>')


def status_text(d: dict) -> str:
    c = d["conclusion"]
    if c == "success":
        return '<span class="ok">thành công</span>'
    if c in ("failure", "timed_out"):
        return '<span class="bad">lỗi</span>'
    return esc({"cancelled": "đã huỷ"}.get(c, c or d["status"]))


def activity_card(active: list[dict], runs: list[dict], pending: bool, refresh: int) -> str:
    note = f'<span class="countdown" id="countdown">Tự cập nhật sau {refresh} giây</span>' if refresh else ""
    head = f'<div class="card-k">{icon("pulse")}{"Đang chạy" if active else "Hoạt động"}{note}</div>'
    if active:
        return f'<div class="card">{head}{"".join(job_block(d) for d in active)}</div>'
    if pending:
        body = '<div class="idle"><span class="spin"></span>Đang chờ GitHub Actions nhận yêu cầu…</div>'
    else:
        body = (f'<div class="idle">{icon("check")}Không có tác vụ nào đang chạy</div>'
                '<p class="hint">Chọn một version bên dưới: <b>Release</b> để deploy lên Triton, '
                '<b>Benchmark</b> để đo hiệu năng trên GPU L4.</p>')
    if runs:
        d = runs[0]
        body += (f'<div class="last">Lần chạy gần nhất: <a href="{esc(d["url"])}">{esc(run_title(d))}</a> · '
                 f'{status_text(d)} · {esc(ago(d["created"]))}</div>')
    return f'<div class="card">{head}{body}</div>'


def version_card(mv: dict, serving: dict, busy: dict | None, can_trigger: bool, queue: bool) -> str:
    t, v = mv["tags"], mv["version"]
    mvid = t.get("model_version_id") or f"{REGISTERED_MODEL} v{v}"
    pinned = bool(t.get("model_version_id")) and serving.get("tag") == t.get("model_version_id")
    chips = prec_chip(t.get("target_precision", "")) + variant_chip(t)
    if pinned and serving.get("ready"):
        chips += '<span class="chip live"><span class="dot on"></span>Đang phục vụ</span>'
    elif pinned:
        chips += '<span class="chip">Gắn với Triton (đang tắt)</span>'
    if busy:
        chips += f'<span class="chip busy"><span class="spin sm"></span>Đang {esc(WF_LABEL.get(busy["workflow"], busy["workflow"]).lower())}</span>'

    tiles = stat("Top-1 offline", pct(t.get("offline_top1_accuracy")), "%", "tile")
    if "benchmark.max_images_per_s" in t:
        tiles += (stat("Throughput tối đa", thousands(t.get("benchmark.max_images_per_s")), "ảnh/s", "tile")
                  + stat("Latency p95 · c1 b1", fixed(t.get("benchmark.p95_ms_c1_b1")), "ms", "tile")
                  + stat("VRAM đỉnh", thousands(t.get("benchmark.gpu_vram_peak_mib")), "MiB", "tile"))
    else:
        tiles += '<div class="nobench">Chưa benchmark. Bấm <b>Benchmark</b> để đo throughput và latency trên GPU L4.</div>'

    meta = [f"Đăng ký {esc(when(mv['created']))}"]
    if t.get("benchmark.at"):
        meta.append(f"Benchmark {esc(when(t['benchmark.at']))}")
    smoke, top1 = num(t.get("benchmark.smoke_top1")), num(t.get("offline_top1_accuracy"))
    if smoke is not None and top1 is not None:
        delta = abs(smoke - top1) * 100
        if delta <= 0.5:
            meta.append(f'<span class="ok">{icon("check")} Triton khớp offline (Δ {delta:.2f} pp)</span>')
        else:
            meta.append(f'<span class="bad">{icon("x")} Triton lệch offline {delta:.2f} pp</span>')
    meta.append(f'<a href="{esc(MLFLOW_PUBLIC_URL)}/#/models/{esc(REGISTERED_MODEL)}/versions/{v}">MLflow {icon("ext")}</a>')

    disabled = "" if can_trigger else ' disabled title="Chưa cấu hình GitHub token"'
    queued = "\n\nĐang có tác vụ khác chạy: yêu cầu này sẽ xếp hàng và chạy sau." if queue else ""
    release_q = (f"Release v{v} ({mvid})?\n\nBuild TensorRT engine trên GPU L4, đẩy image lên ECR rồi Argo CD deploy "
                 f"lên Triton (khoảng 10–30 phút).{queued}")
    bench_q = (f"Benchmark v{v} ({mvid})?\n\nBật Triton trên GPU L4, chạy smoke test và Perf Analyzer (12 cấu hình × 3 lần đo): "
               f"khoảng 30–45 phút, tốn khoảng $1 tiền GPU. Xong tự tắt GPU, trừ khi chọn “Giữ Triton chạy”.{queued}")
    return (f'<article class="mv{" serving" if pinned else ""}" data-version="{v}">'
            f'<div class="mv-top"><div class="mv-v">v{v}</div><div class="mv-id">{esc(mvid)}</div></div>'
            f'<div class="chips">{chips}</div><div class="tiles">{tiles}</div>'
            f'<div class="mv-meta">{"".join(f"<span>{m}</span>" for m in meta)}</div>'
            f'<div class="mv-act"><div class="btns">'
            f'<form method="post" action="actions/release" data-confirm="{esc(release_q)}"><input type="hidden" name="version" value="{v}">'
            f'<button class="btn primary"{disabled}>{icon("play")}<span>Release</span></button></form>'
            f'<form method="post" action="actions/benchmark" id="bench-{v}" data-confirm="{esc(bench_q)}"><input type="hidden" name="version" value="{v}">'
            f'<button class="btn"{disabled}>{icon("gauge")}<span>Benchmark</span></button></form></div>'
            f'<label class="keep"><input type="checkbox" name="keep_serving" value="true" form="bench-{v}">'
            f'Giữ Triton chạy sau benchmark (để demo gọi thử)</label>'
            f'</div></article>')


def compare(versions: list[dict]) -> str:
    data = [(mv, num(mv["tags"].get("offline_top1_accuracy")), num(mv["tags"].get("benchmark.max_images_per_s")),
             num(mv["tags"].get("benchmark.p95_ms_c1_b1")), num(mv["tags"].get("benchmark.gpu_vram_peak_mib")))
            for mv in versions]
    if not any(top1 is not None or tp for _, top1, tp, _, _ in data):
        return ""
    max_tp = max((tp for _, _, tp, _, _ in data if tp), default=0)
    base = next((tp for mv, _, tp, _, _ in data if tp and mv["tags"].get("variant") == "baseline"), None)
    rows = ['<div class="cmp-row head"><div>Version</div><div>Độ chính xác (Top-1 offline)</div>'
            '<div>Throughput tối đa (ảnh/s)</div><div class="cmp-n">p95 c1·b1</div><div class="cmp-n">VRAM</div></div>']
    for mv, top1, tp, p95, vram in data:
        t = mv["tags"]
        if top1 is not None:
            acc = f'<div class="bar acc"><span style="width:{top1 * 100:.1f}%"></span></div><div class="cmp-v">{top1 * 100:.2f}%</div>'
        else:
            acc = '<div class="bar acc"></div><div class="cmp-v muted">–</div>'
        if tp:
            speed = f'<span class="speed">×{tp / base:.2f}</span>' if base and t.get("variant") != "baseline" else ""
            thr = f'<div class="bar"><span style="width:{tp / max_tp * 100:.1f}%"></span></div><div class="cmp-v">{tp:,.0f}{speed}</div>'
        else:
            thr = '<div class="bar"></div><div class="cmp-v muted">chưa đo</div>'
        p95_txt = f"{p95:.2f} ms" if p95 is not None else "–"
        vram_txt = f"{vram:,.0f} MiB" if vram is not None else "–"
        rows.append(f'<div class="cmp-row"><div class="cmp-l"><b>v{mv["version"]}</b>{prec_chip(t.get("target_precision", ""))}'
                    f'<span class="muted">{esc(t.get("variant", ""))}</span></div><div class="cmp-b">{acc}</div>'
                    f'<div class="cmp-b">{thr}</div><div class="cmp-n">{p95_txt}</div><div class="cmp-n">{vram_txt}</div></div>')
    hint = " · ×N = nhanh gấp N lần baseline" if base else ""
    return (f'<section><div class="sec-head"><h2>So sánh các version</h2><p>Top-1 càng cao càng chính xác, '
            f'throughput càng dài càng nhanh{hint}</p></div><div class="card flush">{"".join(rows)}</div></section>')


PALETTE = ("#6366f1", "#10b981", "#f59e0b", "#ef4444", "#06b6d4", "#a855f7")


def line_chart(title: str, subtitle: str, series: list[tuple[str, str, list[tuple[int, float]]]], tick, value) -> str:
    """Inline SVG line chart over the concurrency axis (log2), one line per Model version."""
    values = [v for _, _, pts in series for _, v in pts]
    if not values:
        return ""
    w, h, left, right, top, bottom = 600, 270, 58, 18, 14, 44
    ymax = nice_ceil(max(values) * 1.08)
    lo, hi = math.log2(CONCURRENCY[0]), math.log2(CONCURRENCY[-1])

    def x(c: float) -> float:
        return left + (math.log2(c) - lo) / (hi - lo) * (w - left - right)

    def y(v: float) -> float:
        return top + (1 - v / ymax) * (h - top - bottom)

    parts = []
    for i in range(5):
        val = ymax * i / 4
        parts.append(f'<line class="gl" x1="{left}" x2="{w - right}" y1="{y(val):.1f}" y2="{y(val):.1f}"/>'
                     f'<text class="yl" x="{left - 8}" y="{y(val) + 4:.1f}">{esc(tick(val))}</text>')
    for c in CONCURRENCY:
        parts.append(f'<text class="xl" x="{x(c):.1f}" y="{h - bottom + 18}">{c}</text>')
    parts.append(f'<text class="al" x="{(left + w - right) / 2:.0f}" y="{h - 6}">Số request đồng thời (concurrency)</text>')
    for label, color, pts in series:
        if not pts:
            continue
        points = " ".join(f"{x(c):.1f},{y(v):.1f}" for c, v in pts)
        parts.append(f'<polyline class="ln" points="{points}" style="stroke:{color}"/>')
        for c, v in pts:
            parts.append(f'<circle class="pt" cx="{x(c):.1f}" cy="{y(v):.1f}" r="4.5" style="fill:{color}">'
                         f'<title>{esc(label)} · concurrency {c}: {esc(value(v))}</title></circle>')
    legend = "".join(f'<span><i style="background:{color}"></i>{esc(label)}</span>' for label, color, pts in series if pts)
    return (f'<figure class="chart"><figcaption><b>{esc(title)}</b><span>{esc(subtitle)}</span></figcaption>'
            f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="{esc(title)}">{"".join(parts)}</svg>'
            f'<div class="legend">{legend}</div></figure>')


def charts(versions: list[dict], metrics: dict[int, dict[str, float]]) -> str:
    throughput, latency = [], []
    benchmarked = sorted((mv for mv in versions if metrics.get(mv["version"])), key=lambda mv: mv["version"])
    for i, mv in enumerate(benchmarked):
        m, t = metrics[mv["version"]], mv["tags"]
        label = " · ".join(x for x in (f"v{mv['version']}", t.get("target_precision", ""), t.get("variant", "")) if x)
        color = PALETTE[i % len(PALETTE)]
        throughput.append((label, color, [(c, m[f"c{c}_b8.images_per_s"]) for c in CONCURRENCY if f"c{c}_b8.images_per_s" in m]))
        latency.append((label, color, [(c, m[f"c{c}_b1.p95_latency_ms"]) for c in CONCURRENCY if f"c{c}_b1.p95_latency_ms" in m]))
    if not any(pts for _, _, pts in throughput + latency):
        return ""
    a = line_chart("Throughput", "ảnh/giây · batch 8 · càng cao càng tốt", throughput, short_k, lambda v: f"{v:,.0f} ảnh/s")
    b = line_chart("Latency p95", "mili-giây · batch 1 · càng thấp càng tốt", latency, lambda v: f"{v:.3g}", lambda v: f"{v:.2f} ms")
    return (f'<section><div class="sec-head"><h2>Kết quả benchmark</h2><p>Perf Analyzer trên GPU NVIDIA L4 · mỗi điểm là '
            f'trung bình 3 lần đo · rê chuột lên điểm để xem số</p></div>'
            f'<div class="charts"><div class="card">{a}</div><div class="card">{b}</div></div></section>')


def timeline(runs: list[dict]) -> str:
    if not runs:
        return '<li class="empty">Chưa có lần chạy nào.</li>'
    items = []
    for d in runs[:8]:
        if d["status"] in ACTIVE_STATUSES:
            st, mark = "run", '<span class="spin sm"></span>'
            label = "Đang chạy" if d["status"] == "in_progress" else "Đang xếp hàng"
            dur = "đã chạy " + duration(d["started"])
        else:
            c = d["conclusion"]
            st = "ok" if c == "success" else ("muted" if c in ("cancelled", "skipped") else "bad")
            mark = icon("check") if st == "ok" else icon("x")
            label = {"success": "Thành công", "failure": "Lỗi", "cancelled": "Đã huỷ", "skipped": "Bỏ qua",
                     "timed_out": "Quá giờ"}.get(c, c or d["status"])
            dur = duration(d["started"], d["updated"])
        sub = " · ".join(x for x in (who(d), esc(when(d["created"])), esc(ago(d["created"])), esc(dur)) if x)
        items.append(f'<li class="{st}"><span class="tl-ic">{mark}</span><div class="tl-main">'
                     f'<a href="{esc(d["url"])}">{esc(run_title(d))}</a><span class="wf {esc(d["workflow"])}">{esc(d["workflow"])}</span>'
                     f'<div class="tl-sub">{sub}</div></div><span class="tl-st {st}">{esc(label)}</span></li>')
    return "".join(items)


DARK_VARS = ("--bg:#0a0f1d;--surface:#111827;--surface-2:#172135;--fg:#e6eaf2;--muted:#8d9ab3;--line:#253150;"
             "--accent:#8b93ff;--accent-soft:#1e2452;--ok:#34d399;--ok-soft:#0d2a22;--warn:#fbbf24;--warn-soft:#2b2311;"
             "--bad:#f87171;--bad-soft:#2e1618;--shadow:0 1px 2px rgba(0,0,0,.35),0 10px 30px rgba(0,0,0,.25);color-scheme:dark")
CSS = (
    ":root{--bg:#f3f5fa;--surface:#fff;--surface-2:#f7f8fc;--fg:#0f172a;--muted:#64748b;--line:#e3e8f0;"
    "--accent:#4f46e5;--accent-soft:#eef0ff;--ok:#059669;--ok-soft:#e8f8f1;--warn:#d97706;--warn-soft:#fff6e6;"
    "--bad:#dc2626;--bad-soft:#fdeeee;--shadow:0 1px 2px rgba(15,23,42,.05),0 6px 20px rgba(15,23,42,.06);color-scheme:light}"
    "@media (prefers-color-scheme:dark){:root:not([data-theme=light]){" + DARK_VARS + "}}"
    ":root[data-theme=dark]{" + DARK_VARS + "}"
    """
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 "Be Vietnam Pro",system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;-webkit-font-smoothing:antialiased}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
code{font:12.5px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;background:var(--surface-2);border:1px solid var(--line);border-radius:6px;padding:1px 6px}
.ic{width:18px;height:18px;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;stroke-linejoin:round;flex:none}
.ic.f{fill:currentColor;stroke:none}
.wrap{max-width:1240px;margin:0 auto;padding-left:20px;padding-right:20px}
.top{position:sticky;top:0;z-index:10;background:color-mix(in srgb,var(--surface) 88%,transparent);backdrop-filter:saturate(1.4) blur(12px);border-bottom:1px solid var(--line)}
.top-in{display:flex;align-items:center;gap:8px 20px;flex-wrap:wrap;min-height:64px;padding-top:10px;padding-bottom:10px}
.brand{display:flex;align-items:center;gap:12px;margin-right:auto}
.logo{width:40px;height:40px;border-radius:12px;display:grid;place-items:center;color:#fff;font-weight:800;font-size:19px;background:linear-gradient(135deg,#6366f1,#06b6d4);box-shadow:0 6px 16px rgba(79,70,229,.35)}
.name{font-weight:800;font-size:17px;letter-spacing:-.01em}.name span{color:var(--accent)}
.sub{font-size:12.5px;color:var(--muted)}
.links{display:flex;flex-wrap:wrap;gap:2px}
.links a{display:inline-flex;align-items:center;gap:6px;padding:7px 10px;border-radius:9px;color:var(--fg);font-size:13.5px;font-weight:500}
.links a:hover{background:var(--surface-2);text-decoration:none}.links .ic{width:16px;height:16px;color:var(--muted)}
.who{display:flex;align-items:center;gap:8px}
.pill{display:inline-flex;align-items:center;gap:6px;font-size:12.5px;font-weight:500;padding:5px 11px;border-radius:99px;border:1px solid var(--line);background:var(--surface)}
.pill .ic{width:14px;height:14px}
.icon-btn{border:1px solid var(--line);background:var(--surface);color:var(--fg);width:36px;height:36px;border-radius:10px;display:grid;place-items:center;cursor:pointer}
.icon-btn:hover{background:var(--surface-2)}
main{padding-top:24px;padding-bottom:48px}
section{margin-top:32px}
.sec-head{display:flex;align-items:baseline;gap:6px 14px;flex-wrap:wrap;margin-bottom:14px}
h2{font-size:19px;margin:0;letter-spacing:-.015em}
.sec-head p{margin:0;color:var(--muted);font-size:13.5px}
.card{background:var(--surface);border:1px solid var(--line);border-radius:18px;padding:20px 22px;box-shadow:var(--shadow);min-width:0}
.card.flush{padding:4px 22px}
.card-k{display:flex;align-items:center;gap:8px;font-size:12px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;color:var(--muted)}
.card-k .ic{width:16px;height:16px}
.hero{display:grid;grid-template-columns:minmax(0,1.3fr) minmax(0,1fr);gap:18px;margin-top:0}
.serve.on{background:linear-gradient(160deg,color-mix(in srgb,var(--ok) 9%,var(--surface)),var(--surface) 60%)}
.serve.starting{background:linear-gradient(160deg,color-mix(in srgb,var(--warn) 9%,var(--surface)),var(--surface) 60%)}
.serve-state{display:flex;align-items:center;gap:10px;margin-top:16px;font-weight:700;font-size:15px}
.serve.on .serve-state{color:var(--ok)}.serve.starting .serve-state{color:var(--warn)}.serve.off .serve-state{color:var(--muted)}
.serve-id{font:700 26px/1.25 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;letter-spacing:-.02em;margin:6px 0 10px;word-break:break-all}
.serve-note{color:var(--muted);font-size:13.5px;margin-top:10px}
.stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin-top:18px}
.stat,.tile{background:var(--surface-2);border:1px solid var(--line);border-radius:13px;padding:10px 12px;min-width:0}
.stat .k,.tile .k{font-size:11.5px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.stat .v,.tile .v{font-size:21px;font-weight:700;letter-spacing:-.02em;font-variant-numeric:tabular-nums;white-space:nowrap}
.stat .v small,.tile .v small{font-size:12px;font-weight:500;color:var(--muted);margin-left:3px}
.dot{width:10px;height:10px;border-radius:50%;display:inline-block;background:var(--muted);flex:none}
.dot.on{background:var(--ok);animation:pulse 2s infinite}
.dot.starting{background:var(--warn);animation:pulse-warn 1.4s infinite}
@keyframes pulse{0%{box-shadow:0 0 0 0 color-mix(in srgb,var(--ok) 60%,transparent)}70%,100%{box-shadow:0 0 0 9px transparent}}
@keyframes pulse-warn{0%{box-shadow:0 0 0 0 color-mix(in srgb,var(--warn) 60%,transparent)}70%,100%{box-shadow:0 0 0 9px transparent}}
.spin{width:16px;height:16px;border-radius:50%;border:2.5px solid color-mix(in srgb,var(--accent) 22%,transparent);border-top-color:var(--accent);display:inline-block;animation:spin .8s linear infinite;flex:none}
.spin.sm{width:12px;height:12px;border-width:2px}
@keyframes spin{to{transform:rotate(360deg)}}
.countdown{margin-left:auto;font-weight:500;letter-spacing:0;text-transform:none;font-size:12px}
.idle{display:flex;align-items:center;gap:10px;margin-top:16px;font-weight:700;font-size:16px}
.idle .ic{color:var(--ok);width:22px;height:22px}
.hint{color:var(--muted);font-size:13.5px;margin:8px 0 0}
.last{margin-top:14px;padding-top:12px;border-top:1px solid var(--line);font-size:13px;color:var(--muted)}
.job{border:1px solid var(--line);background:var(--surface-2);border-radius:14px;padding:14px 16px;margin-top:14px}
.job-top{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.job-top b{font-size:16px}
.job-sub{font-size:12.5px;color:var(--muted);margin:4px 0 10px}
.job-sub .ic,.tl-sub .ic{width:13px;height:13px;vertical-align:-2px;margin-right:3px}
.bar{height:8px;border-radius:99px;background:color-mix(in srgb,var(--accent) 13%,transparent);overflow:hidden}
.bar>span{display:block;height:100%;border-radius:99px;background:linear-gradient(90deg,#6366f1,#06b6d4)}
.checklist{list-style:none;margin:12px 0 0;padding:0;display:grid;gap:7px}
.checklist li{display:flex;align-items:center;gap:9px;font-size:13.5px;color:var(--muted)}
.checklist li.done{color:var(--fg)}.checklist li.now{color:var(--fg);font-weight:600}
.ck{width:20px;height:20px;border-radius:50%;display:grid;place-items:center;border:1.5px solid var(--line);flex:none;background:var(--surface)}
li.done .ck{background:var(--ok);border-color:var(--ok);color:#fff}li.done .ck .ic{width:12px;height:12px;stroke-width:3.2}
li.now .ck{border-color:color-mix(in srgb,var(--accent) 40%,transparent)}
.queued{display:flex;gap:8px;align-items:center;font-size:13.5px;color:var(--muted)}
.job-link{display:inline-flex;align-items:center;gap:5px;margin-top:12px;font-size:13px;font-weight:600}.job-link .ic{width:14px;height:14px}
.steps{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:12px}
.steps li{display:flex;gap:12px;align-items:flex-start;background:var(--surface);border:1px solid var(--line);border-radius:16px;padding:14px;box-shadow:var(--shadow);min-width:0}
.st-ic{width:38px;height:38px;border-radius:11px;display:grid;place-items:center;background:var(--accent-soft);color:var(--accent);flex:none}
.st-n{font-weight:700;font-size:14px}.st-d{font-size:12.5px;color:var(--muted);line-height:1.45;margin-top:2px}
.steps li.done .st-ic{background:var(--ok-soft);color:var(--ok)}
.steps li.now{border-color:var(--accent);box-shadow:0 0 0 3px color-mix(in srgb,var(--accent) 20%,transparent),var(--shadow)}
.steps li.now .st-ic{background:var(--accent);color:#fff;animation:glow 1.6s ease-in-out infinite}
@keyframes glow{50%{box-shadow:0 0 0 7px color-mix(in srgb,var(--accent) 18%,transparent)}}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,340px),1fr));gap:18px}
.mv{background:var(--surface);border:1px solid var(--line);border-radius:18px;padding:18px 20px;box-shadow:var(--shadow);display:flex;flex-direction:column;gap:14px;min-width:0}
.mv.serving{border-color:color-mix(in srgb,var(--ok) 50%,var(--line));box-shadow:0 0 0 3px color-mix(in srgb,var(--ok) 14%,transparent),var(--shadow)}
.mv-top{display:flex;align-items:center;gap:12px;min-width:0}
.mv-v{font-weight:800;font-size:20px;color:var(--accent);background:var(--accent-soft);border-radius:12px;padding:5px 12px;flex:none}
.mv-id{font:600 14px/1.35 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;word-break:break-all}
.chips{display:flex;flex-wrap:wrap;gap:6px}
.chip{--c:var(--muted);display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:600;padding:3px 10px;border-radius:99px;color:var(--c);background:color-mix(in srgb,var(--c) 11%,transparent);border:1px solid color-mix(in srgb,var(--c) 26%,transparent)}
.p-fp32{--c:#64748b}.p-fp16{--c:#3b82f6}.p-int8{--c:#d97706}.p-other{--c:#8b5cf6}
.chip.live{--c:var(--ok)}.chip.busy{--c:var(--accent)}
.tiles{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px}
.nobench{grid-column:1/-1;font-size:13px;color:var(--muted);border:1.5px dashed var(--line);border-radius:13px;padding:10px 12px}
.mv-meta{display:flex;flex-wrap:wrap;gap:4px 14px;font-size:12.5px;color:var(--muted)}
.mv-meta .ic{width:13px;height:13px;vertical-align:-2px}
.ok{color:var(--ok)}.bad{color:var(--bad)}.muted{color:var(--muted)}
.mv-act{display:flex;flex-direction:column;gap:10px;margin-top:auto;padding-top:14px;border-top:1px solid var(--line)}
.btns{display:flex;gap:10px}.btns form{flex:1;min-width:0}.btns .btn{width:100%;justify-content:center}
form{margin:0}
.btn{font:inherit;font-weight:600;font-size:14px;display:inline-flex;align-items:center;gap:7px;padding:9px 16px;border-radius:11px;border:1px solid var(--line);background:var(--surface);color:var(--fg);cursor:pointer;transition:transform .08s,box-shadow .15s}
.btn .ic{width:16px;height:16px}
.btn:hover:not(:disabled){transform:translateY(-1px);box-shadow:var(--shadow)}
.btn.primary{background:linear-gradient(135deg,#6366f1,#4f46e5);border-color:transparent;color:#fff;box-shadow:0 4px 14px rgba(79,70,229,.3)}
.btn:disabled{opacity:.45;cursor:not-allowed}.btn.sending{opacity:.7;pointer-events:none}
.keep{display:inline-flex;align-items:center;gap:7px;font-size:13px;color:var(--muted);cursor:pointer;user-select:none}
.keep input{width:16px;height:16px;margin:0;accent-color:var(--accent)}
.empty{color:var(--muted);padding:18px 0}
.cmp-row{display:grid;grid-template-columns:minmax(150px,1fr) minmax(0,1.3fr) minmax(0,1.6fr) 90px 90px;gap:18px;align-items:center;padding:13px 0;border-bottom:1px solid var(--line)}
.cmp-row:last-child{border-bottom:0}
.cmp-row.head{font-size:11.5px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);padding:12px 0 10px}
.cmp-l{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.cmp-b{display:flex;align-items:center;gap:10px;min-width:0}.cmp-b .bar{flex:1;height:10px}
.bar.acc>span{background:linear-gradient(90deg,#10b981,#34d399)}
.cmp-v{min-width:96px;text-align:right;font-weight:700;font-size:13.5px;font-variant-numeric:tabular-nums;white-space:nowrap}
.cmp-n{text-align:right;font-size:13.5px;font-variant-numeric:tabular-nums;white-space:nowrap}
.speed{margin-left:6px;font-size:11.5px;color:var(--ok)}
.charts{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}
.chart{margin:0}
.chart figcaption{display:flex;align-items:baseline;gap:4px 10px;flex-wrap:wrap;margin-bottom:8px}.chart figcaption b{font-size:15px}.chart figcaption span{color:var(--muted);font-size:12.5px}
.chart svg{display:block;width:100%;height:auto;overflow:visible}
.chart .gl{stroke:var(--line);stroke-width:1}
.chart .yl,.chart .xl,.chart .al{fill:var(--muted);font-size:11px}
.chart .yl{text-anchor:end}.chart .xl,.chart .al{text-anchor:middle}
.chart .ln{fill:none;stroke-width:2.5;stroke-linejoin:round;stroke-linecap:round}
.chart .pt{stroke:var(--surface);stroke-width:2;cursor:default}
.chart .pt:hover{r:7}
.legend{display:flex;flex-wrap:wrap;gap:6px 16px;font-size:12.5px;color:var(--muted);margin-top:8px}
.legend i{display:inline-block;width:11px;height:11px;border-radius:3px;margin-right:6px;vertical-align:-1px}
.timeline{list-style:none;margin:0;padding:0}
.timeline li{display:flex;align-items:center;gap:14px;padding:13px 0;border-bottom:1px solid var(--line)}
.timeline li:last-child{border-bottom:0}
.tl-ic{width:32px;height:32px;border-radius:50%;display:grid;place-items:center;flex:none;background:var(--surface-2);color:var(--muted)}
.tl-ic .ic{width:16px;height:16px;stroke-width:2.6}
li.ok .tl-ic{background:var(--ok-soft);color:var(--ok)}li.bad .tl-ic{background:var(--bad-soft);color:var(--bad)}li.run .tl-ic{background:var(--accent-soft)}
.tl-main{flex:1;min-width:0}.tl-main a{font-weight:600;color:var(--fg)}
.tl-sub{font-size:12.5px;color:var(--muted)}
.wf{--c:#6366f1;font-size:10.5px;font-weight:700;letter-spacing:.05em;text-transform:uppercase;padding:2px 7px;border-radius:6px;margin-left:8px;color:var(--c);background:color-mix(in srgb,var(--c) 12%,transparent);vertical-align:1px}
.wf.benchmark{--c:#a855f7}
.tl-st{font-size:12.5px;font-weight:700;white-space:nowrap}
.tl-st.ok{color:var(--ok)}.tl-st.bad{color:var(--bad)}.tl-st.run{color:var(--accent)}.tl-st.muted{color:var(--muted)}
.flash{display:flex;gap:12px;align-items:flex-start;padding:14px 18px;border-radius:14px;border:1px solid var(--line);background:var(--surface);margin-bottom:16px;box-shadow:var(--shadow)}
.flash .ic{width:20px;height:20px;margin-top:1px}
.flash.ok{background:var(--ok-soft);border-color:color-mix(in srgb,var(--ok) 35%,transparent)}.flash.ok .ic{color:var(--ok)}
.flash.bad{background:var(--bad-soft);border-color:color-mix(in srgb,var(--bad) 35%,transparent)}.flash.bad .ic{color:var(--bad)}
.flash.info{background:var(--warn-soft);border-color:color-mix(in srgb,var(--warn) 35%,transparent)}.flash.info .ic{color:var(--warn)}
footer{margin-top:44px;text-align:center;font-size:12.5px;color:var(--muted)}
@media (max-width:1000px){.hero,.charts{grid-template-columns:minmax(0,1fr)}.steps{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media (max-width:760px){.cmp-row{grid-template-columns:repeat(2,minmax(0,1fr));gap:10px 14px}.cmp-row.head{display:none}.cmp-l{grid-column:1/-1}.cmp-n{text-align:left}}
@media (max-width:600px){.wrap{padding-left:16px;padding-right:16px}.stats{grid-template-columns:repeat(2,minmax(0,1fr))}.steps{grid-template-columns:minmax(0,1fr)}.serve-id{font-size:20px}.links{order:3;width:100%}
.chart .yl,.chart .xl,.chart .al{font-size:19px}.chart .ln{stroke-width:4}}
"""
)

THEME_JS = 'try{var t=localStorage.getItem("portal-theme");if(t)document.documentElement.dataset.theme=t}catch(e){}'
PAGE_JS = """(function(){
var root=document.documentElement,btn=document.getElementById("theme");
if(btn)btn.addEventListener("click",function(){
  var dark=root.dataset.theme?root.dataset.theme==="dark":matchMedia("(prefers-color-scheme: dark)").matches;
  root.dataset.theme=dark?"light":"dark";
  try{localStorage.setItem("portal-theme",root.dataset.theme)}catch(e){}
});
document.querySelectorAll("form[data-confirm]").forEach(function(f){
  f.addEventListener("submit",function(e){
    if(!window.confirm(f.dataset.confirm)){e.preventDefault();return}
    var b=f.querySelector("button");
    if(b){b.classList.add("sending");var s=b.querySelector("span");if(s)s.textContent="Đang gửi…"}
  });
});
var every=parseInt(document.body.dataset.refresh||"0",10);
if(every>0){
  var left=every,el=document.getElementById("countdown");
  setInterval(function(){
    if(document.querySelector(".keep input:checked,.btn.sending")){left=every;if(el)el.textContent="Tạm dừng tự cập nhật";return}
    left-=1;if(el)el.textContent="Tự cập nhật sau "+left+" giây";
    if(left<=0)location.replace(location.pathname);
  },1000);
}
})();"""
FAVICON = "data:image/svg+xml," + quote(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
    '<stop offset="0" stop-color="#6366f1"/><stop offset="1" stop-color="#06b6d4"/></linearGradient></defs>'
    '<rect width="32" height="32" rx="9" fill="url(#g)"/><text x="16" y="22.5" font-family="Arial,sans-serif" '
    'font-size="18" font-weight="700" text-anchor="middle" fill="#fff">A</text></svg>')


def render(user: str, versions: list[dict], serving: dict, runs: list[dict], metrics: dict[int, dict[str, float]],
           can_trigger: bool, msg: str, err: str) -> str:
    active = [d for d in runs if d["status"] in ACTIVE_STATUSES]
    busy: dict[int, dict] = {}
    for d in active:
        if d["version"] is not None:
            busy.setdefault(d["version"], d)
    starting = "error" not in serving and serving.get("replicas", 0) != serving.get("ready", 0)
    refresh = REFRESH_S if (active or starting or msg) else 0

    flash = ""
    if msg:
        flash += f'<div class="flash ok">{icon("check")}<div><b>Đã gửi yêu cầu</b><div>{esc(msg)}</div></div></div>'
    if err:
        flash += f'<div class="flash bad">{icon("x")}<div><b>Có lỗi</b><div>{esc(err)}</div></div></div>'
    if not can_trigger:
        flash += (f'<div class="flash info">{icon("info")}<div><b>Chế độ chỉ xem</b><div>Portal chưa có GitHub token '
                  '(Secret portal/portal-github) nên chưa bấm được Release / Benchmark.</div></div></div>')
    cards = "".join(version_card(mv, serving, busy.get(mv["version"]), can_trigger, bool(active)) for mv in versions)
    if not cards:
        cards = '<div class="empty">Chưa có Model version nào. Đăng ký bằng <code>scripts/register_model_version.py</code>.</div>'
    refresh_attr = f' data-refresh="{refresh}"' if refresh else ""
    return f"""<!doctype html><html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Apollo Model Portal</title><link rel="icon" href="{FAVICON}">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Be+Vietnam+Pro:wght@400;500;600;700;800&display=swap">
<script>{THEME_JS}</script><style>{CSS}</style></head><body{refresh_attr}>
<header class="top"><div class="wrap top-in">
<div class="brand"><div class="logo">A</div><div><div class="name">Apollo <span>Model Portal</span></div><div class="sub">ResNet-18 · CIFAR-10 · NVIDIA Triton trên AWS EKS</div></div></div>
<nav class="links"><a href="{esc(MLFLOW_PUBLIC_URL)}">{icon("box")}MLflow</a><a href="{esc(GRAFANA_URL)}">{icon("pulse")}Grafana</a><a href="{esc(ARGOCD_URL)}">{icon("layers")}Argo CD</a><a href="https://github.com/{esc(GITHUB_REPO)}/actions">{icon("github")}GitHub Actions</a></nav>
<div class="who"><span class="pill"><span class="dot on"></span>dev · Seoul</span><span class="pill">{icon("user")}{esc(user)}</span><button id="theme" class="icon-btn" type="button" title="Đổi giao diện sáng / tối" aria-label="Đổi giao diện sáng / tối">{icon("moon")}</button></div>
</div></header>
<main class="wrap">{flash}
<section class="hero">{serving_card(serving, versions)}{activity_card(active, runs, bool(msg), refresh)}</section>
<section><div class="sec-head"><h2>Luồng triển khai</h2><p>Từ model đã train đến Triton trên GPU: mỗi bước chạy tự động bằng GitHub Actions</p></div><ol class="steps">{pipeline(current_stage(active))}</ol></section>
<section id="versions"><div class="sec-head"><h2>Model versions</h2><p>{len(versions)} version trong registry <code>{esc(REGISTERED_MODEL)}</code> · mới nhất trước</p></div><div class="grid">{cards}</div></section>
{compare(versions)}{charts(versions, metrics)}
<section><div class="sec-head"><h2>Lịch sử chạy</h2><p>Release và Benchmark gần đây · giờ Việt Nam (UTC+7)</p></div><div class="card flush"><ul class="timeline">{timeline(runs)}</ul></div></section>
<footer>Apollo · AIO MLOps 2026 · dữ liệu lấy trực tiếp từ MLflow Registry, GitHub Actions và Kubernetes</footer>
</main><script>{PAGE_JS}</script></body></html>"""


# ------------------------------------------------------------------ routes

@app.get("/healthz", response_class=PlainTextResponse)
def healthz() -> str:
    return "ok"


@app.get("/", response_class=HTMLResponse)
def index(msg: str = "", err: str = "", creds: HTTPBasicCredentials = Depends(current_user)) -> str:
    try:
        versions = model_versions(creds)
    except httpx.HTTPError as e:
        versions, err = [], err or f"Không đọc được MLflow registry: {e}"
    metrics = {mv["version"]: benchmark_metrics(mv["tags"]["benchmark.run_id"], creds)
               for mv in versions if mv["tags"].get("benchmark.run_id")}
    return render(creds.username, versions, serving_now(), collect_runs(), metrics, github_token() is not None, msg, err)


def _trigger(workflow: str, version: int, creds: HTTPBasicCredentials, extra: dict | None = None) -> RedirectResponse:
    if version not in {mv["version"] for mv in model_versions(creds)}:
        raise HTTPException(404, f"{REGISTERED_MODEL} v{version} không có trong registry")
    inputs = {"model_name": REGISTERED_MODEL, "model_version": str(version), "requested_by": f"portal:{creds.username}", **(extra or {})}
    try:
        dispatch(workflow, inputs)
    except HTTPException as e:
        return RedirectResponse("./?" + urlencode({"err": e.detail}), status_code=303)
    msg = f"{WF_LABEL[workflow]} v{version} đã được gửi tới GitHub Actions. Tiến độ hiện ở khung bên phải sau vài giây."
    return RedirectResponse("./?" + urlencode({"msg": msg}), status_code=303)


@app.post("/actions/release", dependencies=[Depends(same_origin)])
def release(version: int = Form(...), creds: HTTPBasicCredentials = Depends(current_user)) -> RedirectResponse:
    return _trigger("release", version, creds)


@app.post("/actions/benchmark", dependencies=[Depends(same_origin)])
def benchmark(version: int = Form(...), keep_serving: str = Form("false"),
              creds: HTTPBasicCredentials = Depends(current_user)) -> RedirectResponse:
    return _trigger("benchmark", version, creds, {"keep_serving": "true" if keep_serving == "true" else "false"})

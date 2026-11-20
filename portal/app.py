"""Model Portal: pick a Model version from the MLflow registry and trigger a release or a benchmark.

Architecture box "Model Portal (FastAPI): pick version, trigger a release". The portal holds no
state of its own:

- Login = an MLflow account (HTTP Basic, checked against MLflow); registry reads use the caller's
  own credentials, so MLflow permissions apply as they do in the MLflow UI.
- "Serving now" = the Triton Deployment in namespace `serving` (in-cluster ServiceAccount, read-only).
- Release / Benchmark = GitHub Actions workflow_dispatch (release.yml / benchmark.yml) with a
  fine-grained token (Actions: read and write on this repo only), mounted from Secret portal-github.
  Without that Secret the portal is read-only.
"""

from __future__ import annotations

import hashlib
import html
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode, urlsplit

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

app = FastAPI(title="Apollo Model Portal", docs_url=None, redoc_url=None)
security = HTTPBasic(realm="Apollo Model Portal (MLflow account)")
_auth_cache: dict[str, float] = {}  # sha256(user:password) -> expiry
_runs_cache: dict[str, tuple[float, list]] = {}


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
    _runs_cache[workflow] = (time.time() + 20, runs)
    return runs


def dispatch(workflow: str, inputs: dict) -> None:
    if not github_token():
        raise HTTPException(503, "Portal chưa có GitHub token (Secret portal/portal-github): chỉ xem được, không trigger được.")
    r = httpx.post(f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/{WORKFLOWS[workflow]}/dispatches",
                   json={"ref": "main", "inputs": inputs}, headers=github_headers(), timeout=15)
    if r.status_code != 204:
        raise HTTPException(502, f"GitHub workflow_dispatch {workflow}: HTTP {r.status_code} {r.text[:300]}")
    _runs_cache.pop(workflow, None)


# ------------------------------------------------------------------ pages

CSS = """
:root{--bg:#f7f7f5;--fg:#1d1d1b;--muted:#6b6b66;--card:#fff;--line:#e3e2dc;--accent:#2f5bd3;--ok:#1f7a3d;--warn:#a05a00;--bad:#b3261e}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--fg:#ecebe6;--muted:#9a9993;--card:#20201e;--line:#34332f;--accent:#7d9cf0;--ok:#5fc27e;--warn:#e0a24a;--bad:#f07167}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:1180px;margin:0 auto;padding:24px 16px 48px}h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;margin:28px 0 10px}
.muted{color:var(--muted)}a{color:var(--accent)}.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.row{display:flex;gap:12px;flex-wrap:wrap}.row>.card{flex:1 1 260px}.big{font-size:20px;font-weight:600}
.scroll{overflow-x:auto;border:1px solid var(--line);border-radius:10px;background:var(--card)}
table{border-collapse:collapse;width:100%;min-width:900px}th,td{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}
th{font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)}tr.serving td{background:color-mix(in srgb,var(--ok) 10%,transparent)}
td.num{text-align:right;font-variant-numeric:tabular-nums}button{font:inherit;padding:4px 10px;border-radius:6px;border:1px solid var(--line);background:var(--card);color:var(--fg);cursor:pointer}
button.primary{background:var(--accent);border-color:var(--accent);color:#fff}button:disabled{opacity:.45;cursor:not-allowed}form{display:inline}
.pill{display:inline-block;padding:1px 8px;border-radius:99px;font-size:12px;border:1px solid var(--line)}.ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}
.flash{margin:16px 0;padding:10px 14px;border-radius:8px;border:1px solid var(--ok);color:var(--ok)}.flash.err{border-color:var(--bad);color:var(--bad)}
label.chk{font-size:12px;color:var(--muted);margin-left:4px}
"""


def esc(v) -> str:
    return html.escape("" if v is None else str(v))


def ts(epoch: int | str | None) -> str:
    if not epoch:
        return "–"
    if isinstance(epoch, str):
        return esc(epoch.replace("T", " ")[:16])
    return datetime.fromtimestamp(epoch, UTC).strftime("%Y-%m-%d %H:%M")


def run_status(run: dict) -> str:
    if run.get("status") != "completed":
        return f'<span class="pill warn">{esc(run.get("status"))}</span>'
    cls = "ok" if run.get("conclusion") == "success" else "bad"
    return f'<span class="pill {cls}">{esc(run.get("conclusion"))}</span>'


def render(user: str, versions: list[dict], serving: dict, runs: dict[str, list], can_trigger: bool, msg: str, err: str) -> str:
    tag = serving.get("tag")
    if "error" in serving:
        serving_html = f'<div class="big">–</div><div class="muted">{esc(serving["error"])}</div>'
    else:
        state = ("ok", "đang chạy") if serving["ready"] else (("warn", "đang khởi động") if serving["replicas"] else ("muted", "đã tắt (replicas 0)"))
        serving_html = (f'<div class="big">{esc(tag)}</div><div><span class="{state[0]}">{state[1]}</span> · '
                        f'{esc(serving["ready"])}/{esc(serving["replicas"])} pod sẵn sàng</div>')
    disabled = "" if can_trigger else " disabled title=\"Chưa cấu hình GitHub token\""
    rows = []
    for mv in versions:
        t, v = mv["tags"], mv["version"]
        mvid = t.get("model_version_id", "?")
        top1 = t.get("offline_top1_accuracy")
        bench = t.get("benchmark.max_images_per_s")
        is_serving = mvid == tag
        badge = ' <span class="pill ok">serving</span>' if is_serving else ""
        rows.append(
            f'<tr class="{"serving" if is_serving else ""}">'
            f'<td><a href="{esc(MLFLOW_PUBLIC_URL)}/#/models/{esc(REGISTERED_MODEL)}/versions/{v}">v{v}</a></td>'
            f'<td>{esc(mvid)}{badge}</td>'
            f'<td>{esc(t.get("variant"))}</td><td>{esc(t.get("target_precision"))}</td>'
            f'<td class="num">{f"{float(top1) * 100:.2f}%" if top1 else "–"}</td>'
            f'<td class="num">{esc(bench) if bench else "–"}</td>'
            f'<td class="num">{esc(t.get("benchmark.p95_ms_c1_b1", "–"))}</td>'
            f'<td class="num">{esc(t.get("benchmark.gpu_vram_peak_mib", "–"))}</td>'
            f'<td>{ts(mv["created"])}</td>'
            f'<td><form method="post" action="actions/release"><input type="hidden" name="version" value="{v}">'
            f'<button class="primary"{disabled}>Release</button></form> '
            f'<form method="post" action="actions/benchmark"><input type="hidden" name="version" value="{v}">'
            f'<button{disabled}>Benchmark</button><label class="chk"><input type="checkbox" name="keep_serving" value="true"> giữ Triton</label></form></td></tr>'
        )
    if not rows:
        rows.append('<tr><td colspan="10" class="muted">Chưa có Model version nào. Đăng ký bằng scripts/register_model_version.py.</td></tr>')
    run_rows = []
    for wf, items in runs.items():
        for r in items:
            run_rows.append(f'<tr><td>{esc(wf)}</td><td><a href="{esc(r.get("html_url"))}">{esc(r.get("display_title") or r.get("name"))}</a></td>'
                            f'<td>{run_status(r)}</td><td>{ts(r.get("created_at"))}</td></tr>')
    flash = (f'<div class="flash">{esc(msg)}</div>' if msg else "") + (f'<div class="flash err">{esc(err)}</div>' if err else "")
    return f"""<!doctype html><html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Model Portal</title><style>{CSS}</style></head><body><main>
<h1>Apollo · Model Portal</h1>
<div class="muted">Đăng nhập: <b>{esc(user)}</b> · registry <code>{esc(REGISTERED_MODEL)}</code> ·
<a href="{esc(MLFLOW_PUBLIC_URL)}">MLflow</a> · <a href="{esc(GRAFANA_URL)}">Grafana</a> · <a href="{esc(ARGOCD_URL)}">Argo CD</a> ·
<a href="https://github.com/{esc(GITHUB_REPO)}/actions">GitHub Actions</a></div>
{flash}
<div class="row" style="margin-top:16px">
  <div class="card"><div class="muted">Triton đang serve</div>{serving_html}</div>
  <div class="card"><div class="muted">Release</div><div>Build Engine TensorRT trên L4 → image lên ECR → Argo CD deploy (~10–30 phút)</div></div>
  <div class="card"><div class="muted">Benchmark</div><div>Bật Triton với version đó → Perf Analyzer (ma trận concurrency × batch, 3 lần đo) → MLflow; xong tự tắt GPU trừ khi chọn "giữ Triton"</div></div>
</div>
<h2>Model versions</h2>
<div class="scroll"><table><thead><tr><th>Version</th><th>model_version_id</th><th>Variant</th><th>Precision</th><th>Top-1 offline</th>
<th>Max img/s</th><th>p95 ms (c1,b1)</th><th>VRAM MiB</th><th>Đăng ký</th><th></th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>
<h2>Lần chạy gần đây</h2>
<div class="scroll"><table><thead><tr><th>Workflow</th><th>Run</th><th>Kết quả</th><th>Bắt đầu (UTC)</th></tr></thead>
<tbody>{"".join(run_rows) or '<tr><td colspan="4" class="muted">Chưa có.</td></tr>'}</tbody></table></div>
</main></body></html>"""


@app.get("/healthz", response_class=PlainTextResponse)
def healthz() -> str:
    return "ok"


@app.get("/", response_class=HTMLResponse)
def index(msg: str = "", err: str = "", creds: HTTPBasicCredentials = Depends(current_user)) -> str:
    try:
        versions = model_versions(creds)
    except httpx.HTTPError as e:
        versions, err = [], err or f"Không đọc được MLflow registry: {e}"
    runs = {wf: recent_runs(wf) for wf in WORKFLOWS}
    return render(creds.username, versions, serving_now(), runs, github_token() is not None, msg, err)


def _trigger(workflow: str, version: int, creds: HTTPBasicCredentials, extra: dict | None = None) -> RedirectResponse:
    if version not in {mv["version"] for mv in model_versions(creds)}:
        raise HTTPException(404, f"{REGISTERED_MODEL} v{version} không có trong registry")
    inputs = {"model_name": REGISTERED_MODEL, "model_version": str(version), "requested_by": f"portal:{creds.username}", **(extra or {})}
    try:
        dispatch(workflow, inputs)
    except HTTPException as e:
        return RedirectResponse("./?" + urlencode({"err": e.detail}), status_code=303)
    return RedirectResponse("./?" + urlencode({"msg": f"Đã trigger {workflow} cho {REGISTERED_MODEL} v{version}. Theo dõi ở bảng bên dưới hoặc GitHub Actions."}), status_code=303)


@app.post("/actions/release", dependencies=[Depends(same_origin)])
def release(version: int = Form(...), creds: HTTPBasicCredentials = Depends(current_user)) -> RedirectResponse:
    return _trigger("release", version, creds)


@app.post("/actions/benchmark", dependencies=[Depends(same_origin)])
def benchmark(version: int = Form(...), keep_serving: str = Form("false"),
              creds: HTTPBasicCredentials = Depends(current_user)) -> RedirectResponse:
    return _trigger("benchmark", version, creds, {"keep_serving": "true" if keep_serving == "true" else "false"})

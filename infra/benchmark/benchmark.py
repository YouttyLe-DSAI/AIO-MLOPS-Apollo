"""In-cluster Benchmark Job: Smoke test + Benchmark matrix against the Triton Argo CD deployed.

Runs in nvcr.io/nvidia/tritonserver:26.09-py3-sdk (perf_analyzer 26.09 + tritonclient) on the loadgen
node, so the load generator never shares the L4 node's CPUs with Triton, nor a system node's memory
with MLflow / Argo CD. Follows the protocol of
aio-mlops-serving (`serving run`) and writes the same results layout, so `serving report` there can
aggregate EKS runs together with workstation runs:

    /work/results/<model_version_id>_<UTC %Y%m%dT%H%M%SZ>/
        benchmark_config.yaml   config used (infra/benchmark/benchmark.yaml)
        smoke_test.json         full CIFAR-10 test set through Triton vs offline_top1_accuracy
        payload.json            Perf Analyzer --input-data (first N test images, shared Preprocessing)
        raw/c{c}_b{b}_r{r}.csv|.stdout.txt         measured Benchmark runs, untouched
        raw/warmup/c{c}_b{b}_w{w}.csv|.stdout.txt  warm-ups, untouched
        benchmark.csv           one row per point x repeat (+ runtime metrics columns)
        summary.csv             mean / std over repeats per point
        metadata.json           run-level facts + every Benchmark run window in passes[]

The whole folder is logged to MLflow (experiment `benchmark`, artifacts results/<run_id>/) and the
registered Model version gets benchmark.* tags (read by the Model Portal).

Env: MODEL_NAME + MODEL_VERSION (MLflow registry), MLFLOW_TRACKING_URI/USERNAME/PASSWORD,
TRITON_HTTP, TRITON_METRICS, MIMIR_URL, NODE_NAME, TRITON_IMAGE, WAIT_READY_S, CLIENT_MEMORY_BUDGET_MIB (optional).
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import platform
import shutil
import statistics
import subprocess
import sys
import tarfile
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from string import Template

import numpy as np
import yaml

WORK = Path(os.environ.get("WORK_DIR", "/work"))
CONFIG_PATH = Path(os.environ.get("BENCHMARK_CONFIG", "/scripts/benchmark.yaml"))
CFG = yaml.safe_load(CONFIG_PATH.read_text())
MODEL = CFG["model_name"]
TRITON_HTTP = os.environ.get("TRITON_HTTP", "triton.serving.svc.cluster.local:8000")
TRITON_METRICS = os.environ.get("TRITON_METRICS", "http://triton.serving.svc.cluster.local:8002/metrics")
MIMIR = os.environ.get("MIMIR_URL", "http://mimir.monitoring.svc:9009/prometheus")
PERF_ANALYZER = os.environ.get("PERF_ANALYZER", "perf_analyzer")

# aio-mlops-serving src/serving/preprocessing.py (single definition of image -> tensor)
CIFAR10_MEAN = np.asarray((0.4914, 0.4822, 0.4465), dtype=np.float32).reshape(1, 3, 1, 1)
CIFAR10_STD = np.asarray((0.2470, 0.2435, 0.2616), dtype=np.float32).reshape(1, 3, 1, 1)
CIFAR10_URL = "https://www.cs.toronto.edu/~kriz/cifar-10-binary.tar.gz"
CIFAR10_MD5 = "c32a1d4ab5d03f1284b67883e8d87530"

# aio-mlops-serving pipeline.BENCHMARK_COLUMNS + runtime_metrics.PROMETHEUS_COLUMNS
BENCHMARK_COLUMNS = (
    "model_version_id", "variant", "target_precision",
    "concurrency", "batch_size", "requests_per_s", "images_per_s",
    "p50_latency_ms", "p95_latency_ms", "p99_latency_ms",
    "repeat", "started_at", "ended_at",
)
RUNTIME_COLUMNS = ("gpu_vram_peak_mib", "gpu_util_mean_pct", "triton_queue_ms", "triton_compute_infer_ms")
SUMMARY_FIELDS = ("images_per_s", "requests_per_s", "p50_latency_ms", "p95_latency_ms", "p99_latency_ms",
                  "gpu_util_mean_pct", "gpu_vram_peak_mib", "triton_queue_ms", "triton_compute_infer_ms")
METADATA_SCHEMA_VERSION = 1


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def log(msg: str) -> None:
    print(f"{datetime.now(UTC):%H:%M:%S} {msg}", flush=True)


def http_get(url: str, timeout: float = 10) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except (urllib.error.URLError, OSError):
        return 0, b""


# ------------------------------------------------------------------ Model version (MLflow registry)

def load_model_version(name: str, version: str) -> dict:
    from mlflow import MlflowClient

    mv = MlflowClient().get_model_version(name, version)
    t = mv.tags
    missing = [k for k in ("model_version_id", "variant", "target_precision", "input.name", "output.name",
                           "offline_top1_accuracy") if k not in t]
    if missing:
        raise SystemExit(f"{name} v{version}: tags {missing} missing (register with scripts/register_model_version.py)")
    return {"registered_model": name, "registered_model_version": str(version), "run_id": mv.run_id, "tags": dict(t),
            "model_version_id": t["model_version_id"], "variant": t["variant"],
            "target_precision": t["target_precision"], "input_name": t["input.name"],
            "output_name": t["output.name"], "offline_top1": float(t["offline_top1_accuracy"])}


# ------------------------------------------------------------------ Triton

def wait_for_triton(expected_id: str, timeout_s: int) -> dict:
    """Wait until Triton serves the model; check it is the expected Model version when the Engine
    config carries it (releases since 2026-11-20 write parameters.model_version_id)."""
    deadline, last = time.time() + timeout_s, None
    while time.time() < deadline:
        code, _ = http_get(f"http://{TRITON_HTTP}/v2/models/{MODEL}/ready", timeout=5)
        if code == 200:
            _, body = http_get(f"http://{TRITON_HTTP}/v2/models/{MODEL}/config")
            config = json.loads(body or b"{}")
            served = ((config.get("parameters") or {}).get("model_version_id") or {}).get("string_value")
            if served and served != expected_id:
                last = f"Triton serves {served}, waiting for {expected_id}"
            else:
                if not served:
                    log(f"WARNING: Engine config has no model_version_id parameter; assuming Triton serves {expected_id}")
                _, meta = http_get(f"http://{TRITON_HTTP}/v2")
                return {"server_metadata": json.loads(meta or b"{}"), "model_config": config, "served_model_version_id": served}
        else:
            last = f"model not ready (HTTP {code or 'unreachable'})"
        log(f"waiting for Triton: {last}")
        time.sleep(15)
    raise SystemExit(f"Triton not ready with {expected_id} after {timeout_s}s: {last}")


def triton_counters() -> dict[str, float]:
    """Cumulative Triton counters for MODEL (summed over versions)."""
    wanted = ("nv_inference_queue_duration_us", "nv_inference_compute_infer_duration_us", "nv_inference_request_success")
    out = dict.fromkeys(wanted, 0.0)
    code, body = http_get(TRITON_METRICS)
    if code != 200:
        return {}
    for line in body.decode().splitlines():
        name = line.split("{", 1)[0]
        if name in out and f'model="{MODEL}"' in line:
            out[name] += float(line.rsplit(" ", 1)[1])
    return out


def triton_deltas(before: dict, after: dict) -> dict[str, float | None]:
    if not before or not after:
        return {"triton_queue_ms": None, "triton_compute_infer_ms": None}
    n = after["nv_inference_request_success"] - before["nv_inference_request_success"]
    if n <= 0:
        return {"triton_queue_ms": None, "triton_compute_infer_ms": None}
    return {
        "triton_queue_ms": (after["nv_inference_queue_duration_us"] - before["nv_inference_queue_duration_us"]) / n / 1000,
        "triton_compute_infer_ms": (after["nv_inference_compute_infer_duration_us"]
                                    - before["nv_inference_compute_infer_duration_us"]) / n / 1000,
    }


# ------------------------------------------------------------------ CIFAR-10 + Preprocessing

def load_cifar10_test(cache: Path) -> tuple[np.ndarray, np.ndarray]:
    """10k test images (uint8 [N,32,32,3] RGB HWC) + labels, from the official binary release."""
    target = cache / "test_batch.bin"
    if not target.exists():
        cache.mkdir(parents=True, exist_ok=True)
        archive = cache / "cifar-10-binary.tar.gz"
        log("downloading CIFAR-10 (once; cached on the benchmark-cache volume)")
        part = archive.with_suffix(".part")
        with urllib.request.urlopen(CIFAR10_URL, timeout=120) as r, open(part, "wb") as f:
            shutil.copyfileobj(r, f)
        part.replace(archive)
        if hashlib.md5(archive.read_bytes()).hexdigest() != CIFAR10_MD5:
            raise SystemExit("CIFAR-10 download corrupt (md5 mismatch)")
        with tarfile.open(archive, "r:gz") as tar:
            src = tar.extractfile("cifar-10-batches-bin/test_batch.bin")
            target.with_suffix(".part").write_bytes(src.read())
            target.with_suffix(".part").replace(target)
        archive.unlink()  # keep only the 30 MB test batch
    raw = np.fromfile(target, dtype=np.uint8).reshape(-1, 1 + 3072)
    images = raw[:, 1:].reshape(-1, 3, 32, 32).transpose(0, 2, 3, 1)
    return np.ascontiguousarray(images), raw[:, 0].astype(np.int64)


def preprocess(images: np.ndarray) -> np.ndarray:
    x = images.transpose(0, 3, 1, 2).astype(np.float32) / np.float32(255.0)
    return np.ascontiguousarray((x - CIFAR10_MEAN) / CIFAR10_STD, dtype=np.float32)


# ------------------------------------------------------------------ Smoke test

def smoke_test(mv: dict, images: np.ndarray, labels: np.ndarray) -> dict:
    import tritonclient.http as httpclient

    cfg = CFG["smoke_test"]
    client = httpclient.InferenceServerClient(url=TRITON_HTTP, connection_timeout=60, network_timeout=60)
    bs, correct, t0 = int(cfg["batch_size"]), 0, time.time()
    for i in range(0, len(images), bs):
        x = preprocess(images[i:i + bs])
        inp = httpclient.InferInput(mv["input_name"], list(x.shape), "FP32")
        inp.set_data_from_numpy(x, binary_data=True)
        out = client.infer(MODEL, [inp], outputs=[httpclient.InferRequestedOutput(mv["output_name"], binary_data=True)])
        logits = out.as_numpy(mv["output_name"])
        if logits is None or logits.shape != (len(x), 10) or not np.isfinite(logits).all():
            raise SystemExit(f"Smoke test: bad output {None if logits is None else logits.shape}")
        correct += int((logits.argmax(1) == labels[i:i + bs]).sum())
    top1 = correct / len(images)
    diff_pp = abs(top1 - mv["offline_top1"]) * 100
    return {"passed": diff_pp <= float(cfg["tolerance_pp"]), "triton_top1_accuracy": top1,
            "offline_top1_accuracy": mv["offline_top1"], "diff_pp": diff_pp, "tolerance_pp": float(cfg["tolerance_pp"]),
            "images": len(images), "batch_size": bs, "duration_s": round(time.time() - t0, 2)}


# ------------------------------------------------------------------ Benchmark matrix

def points() -> list[tuple[int, int]]:
    m = CFG["benchmark"]["matrix"]
    return [(int(c), int(b)) for c in m["concurrency"] for b in m["batch_size"]]


def perf_command(c: int, b: int, csv_path: Path, payload: Path) -> list[str]:
    bc = CFG["benchmark"]
    url = TRITON_HTTP if bc["protocol"] == "http" else TRITON_HTTP.replace(":8000", ":8001")
    return [PERF_ANALYZER, "-m", MODEL, "-i", bc["protocol"], "-u", url, "-b", str(b),
            "--concurrency-range", str(c), "--measurement-interval", str(bc["measurement_interval_ms"]),
            "--stability-percentage", f"{float(bc['stability_percentage']):g}", "--max-trials", str(bc["max_trials"]),
            "--input-data", str(payload), "-f", str(csv_path), *[str(a) for a in bc.get("perf_analyzer_args") or []]]


def parse_perf_csv(text: str, batch_size: int) -> dict:
    r = list(csv.DictReader(io.StringIO(text)))[-1]
    ips = float(r["Inferences/Second"])  # images/s (requests x batch size)
    return {"concurrency": int(r["Concurrency"]), "batch_size": batch_size, "requests_per_s": ips / batch_size,
            "images_per_s": ips, "p50_latency_ms": float(r["p50 latency"]) / 1000,
            "p95_latency_ms": float(r["p95 latency"]) / 1000, "p99_latency_ms": float(r["p99 latency"]) / 1000}


CGROUP_MEMORY = Path("/sys/fs/cgroup/memory.current")  # cgroup v2 (EKS AL2023 nodes)
CGROUP_MEMORY_MAX = Path("/sys/fs/cgroup/memory.max")
MEMORY_HEADROOM = 1 << 30  # stop Perf Analyzer this far below the container limit


def memory_budget() -> int | None:
    """Bytes this container may use before a Perf Analyzer run is stopped: CLIENT_MEMORY_BUDGET_MIB, else
    the cgroup limit minus MEMORY_HEADROOM. None when there is no limit (e.g. a local run)."""
    if os.environ.get("CLIENT_MEMORY_BUDGET_MIB"):
        return int(os.environ["CLIENT_MEMORY_BUDGET_MIB"]) << 20
    try:
        limit = CGROUP_MEMORY_MAX.read_text().strip()
    except OSError:
        return None
    return int(limit) - MEMORY_HEADROOM if limit.isdigit() else None


class MemoryGuard:
    """Peak memory of this container while one Perf Analyzer run is in flight, and a guard against the limit.

    Perf Analyzer keeps one record per request in memory for the whole run, so the high-rate points
    (concurrency 32, batch 1: ~10k requests/s) need the most. If the container crosses `budget`, the run
    is killed here (`tripped`), so the point is recorded as failed instead of the kernel OOM-killing the
    whole container (cgroup v2 kills every process in it) and losing the points measured so far.
    """

    def __init__(self, budget: int | None = None, interval_s: float = 0.5):
        self.budget, self.interval_s = budget, interval_s
        self.peak, self.tripped, self.proc = 0, False, None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._sample, daemon=True)

    @staticmethod
    def _read() -> int:
        try:
            return int(CGROUP_MEMORY.read_text())
        except (OSError, ValueError):
            return 0

    def _sample(self) -> None:
        while not self._stop.is_set():
            now = self._read()
            self.peak = max(self.peak, now)
            if self.budget and now > self.budget and self.proc and self.proc.poll() is None:
                self.tripped = True
                self.proc.kill()
            self._stop.wait(self.interval_s)

    def run(self, cmd: list[str], out) -> int:
        self.proc = subprocess.Popen(cmd, stdout=out, stderr=subprocess.STDOUT)
        return self.proc.wait()

    def __enter__(self) -> MemoryGuard:
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        self._thread.join()
        self.peak = max(self.peak, self._read())

    @property
    def mib(self) -> int | None:
        return round(self.peak / 1048576) if self.peak else None


def run_matrix(run_dir: Path, payload: Path, metadata: dict) -> list[dict]:
    """Every point x (warm-ups + repeats). A Perf Analyzer failure is retried once; if it fails again
    the point is recorded in metadata["failed_points"] and skipped, so the other points still count."""
    bc, rows, budget = CFG["benchmark"], [], memory_budget()
    metadata.setdefault("failed_points", [])
    if budget:
        log(f"client memory budget {budget >> 20} MiB (a Perf Analyzer run above it is stopped)")
    for c, b in points():
        plan = [("warmup", w) for w in range(1, int(bc["warmup_runs"]) + 1)] + \
               [("measure", r) for r in range(1, int(bc["repeats"]) + 1)]
        point_rows: list[dict] = []
        for kind, idx in plan:
            out_dir = run_dir / "raw" / ("warmup" if kind == "warmup" else "")
            out_dir.mkdir(parents=True, exist_ok=True)
            stem = f"c{c}_b{b}_{'w' if kind == 'warmup' else 'r'}{idx}"
            csv_path, stdout_path = out_dir / f"{stem}.csv", out_dir / f"{stem}.stdout.txt"
            for attempt in (1, 2):
                csv_path.unlink(missing_ok=True)
                before, started = triton_counters(), utc_now()
                with open(stdout_path, "w") as out, MemoryGuard(budget) as mem:
                    rc = mem.run(perf_command(c, b, csv_path, payload), out)
                ended, after = utc_now(), triton_counters()
                if rc == 0 and csv_path.exists() and not mem.tripped:
                    break
                if mem.tripped:  # the same point would hit the budget again: no retry
                    log(f"{stem}: perf_analyzer stopped at {mem.mib} MiB client memory (budget {budget >> 20} MiB)")
                    break
                log(f"{stem}: perf_analyzer failed (exit {rc}, attempt {attempt}/2, client memory peak {mem.mib} MiB); "
                    f"see raw/{stdout_path.relative_to(run_dir / 'raw')}")
            if rc != 0 or mem.tripped or not csv_path.exists():
                metadata["failed_points"].append({"concurrency": c, "batch_size": b, "pass": stem, "exit_code": rc,
                                                  "reason": "memory_budget" if mem.tripped else "perf_analyzer_error",
                                                  "client_mem_peak_mib": mem.mib})
                point_rows = []  # an incomplete point is left out of benchmark.csv / summary.csv
                break
            entry = {"kind": kind, "concurrency": c, "batch_size": b, "index": idx,
                     "repeat": idx if kind == "measure" else None, "started_at": started, "ended_at": ended,
                     "raw_csv": str(csv_path.relative_to(run_dir)), "raw_stdout": str(stdout_path.relative_to(run_dir)),
                     "client_mem_peak_mib": mem.mib}
            deltas = triton_deltas(before, after)
            metadata["passes"].append({**entry, "runtime_metrics": deltas})
            parsed = parse_perf_csv(csv_path.read_text(), b)
            log(f"{stem}: {parsed['images_per_s']:9.1f} img/s  p95 {parsed['p95_latency_ms']:7.2f} ms  client mem {mem.mib} MiB")
            if kind == "measure":
                point_rows.append({**parsed, "repeat": idx, "started_at": started, "ended_at": ended, **deltas})
        rows += point_rows
    return rows


def mimir_query(promql: str, at: float) -> float | None:
    qs = urllib.parse.urlencode({"query": promql, "time": f"{at:.3f}"})
    code, body = http_get(f"{MIMIR}/api/v1/query?{qs}", timeout=15)
    if code != 200:
        return None
    result = json.loads(body)["data"]["result"]
    if not result:
        return None
    v = float(result[0]["value"][1])
    return v if np.isfinite(v) else None


def add_gpu_metrics(rows: list[dict], metadata: dict) -> None:
    rm = CFG["runtime_metrics"]
    if rows:
        wait = datetime.fromisoformat(rows[-1]["ended_at"]).timestamp() + float(rm["query_delay_s"]) - time.time()
        if wait > 0:
            log(f"waiting {wait:.0f}s for the last window's samples to reach Mimir")
            time.sleep(wait)
    for row in rows:
        t0 = datetime.fromisoformat(row["started_at"]).timestamp()
        t1 = datetime.fromisoformat(row["ended_at"]).timestamp()
        rng = f"{max(1, round((t1 - t0) * 1000))}ms"
        for col, q in rm["queries"].items():
            row[col] = mimir_query(Template(q).substitute(range=rng, model=MODEL), t1)
    by_window = {(r["concurrency"], r["batch_size"], r["repeat"]): r for r in rows}
    for p in metadata["passes"]:
        r = by_window.get((p["concurrency"], p["batch_size"], p["repeat"])) if p["kind"] == "measure" else None
        if r:
            p["runtime_metrics"].update({c: r.get(c) for c in rm["queries"]})


def summarize(rows: list[dict]) -> list[dict]:
    out = []
    for c, b in points():
        reps = [r for r in rows if r["concurrency"] == c and r["batch_size"] == b]
        if not reps:
            continue
        s = {"concurrency": c, "batch_size": b, "repeats": len(reps)}
        for f in SUMMARY_FIELDS:
            vals = [r[f] for r in reps if r.get(f) is not None]
            s[f"{f}_mean"] = statistics.fmean(vals) if vals else None
            s[f"{f}_std"] = statistics.stdev(vals) if len(vals) > 1 else (0.0 if vals else None)
        out.append(s)
    return out


def gpu_info() -> dict:
    code, body = http_get(f"{MIMIR}/api/v1/query?" + urllib.parse.urlencode({"query": "DCGM_FI_DEV_GPU_TEMP_CELSIUS"}))
    try:
        labels = json.loads(body)["data"]["result"][0]["metric"] if code == 200 else {}
    except (IndexError, KeyError, ValueError):
        labels = {}
    return {k: labels.get(k) for k in ("modelName", "hostname", "DCGM_FI_DRIVER_VERSION", "UUID") if labels.get(k)}


# ------------------------------------------------------------------ MLflow logging

def log_to_mlflow(mv: dict, run_dir: Path, metadata: dict, summary: list[dict], smoke: dict | None) -> str:
    import mlflow
    from mlflow import MlflowClient

    bc = CFG["benchmark"]
    mlflow.set_experiment("benchmark")
    with mlflow.start_run(run_name=run_dir.name) as run:
        mlflow.set_tags({
            "model_version_id": mv["model_version_id"], "variant": mv["variant"],
            "target_precision": mv["target_precision"], "registered_model": mv["registered_model"],
            "registered_model_version": mv["registered_model_version"], "status": metadata["status"],
            "triton_version": metadata["triton"].get("version") or "", "gpu": metadata["hardware"]["gpu"].get("modelName", ""),
            "runner": "eks", "contract": "aio-mlops-serving results layout v1",
        })
        mlflow.log_params({
            "concurrency": json.dumps(bc["matrix"]["concurrency"]), "batch_size": json.dumps(bc["matrix"]["batch_size"]),
            "measurement_interval_ms": bc["measurement_interval_ms"], "stability_percentage": bc["stability_percentage"],
            "max_trials": bc["max_trials"], "warmup_runs": bc["warmup_runs"], "repeats": bc["repeats"],
            "payload_images": bc["payload_images"], "protocol": bc["protocol"], "client_node": metadata["hardware"]["client"].get("node", ""),
        })
        metrics = {}
        if smoke:
            metrics["smoke_top1_accuracy"] = smoke["triton_top1_accuracy"]
        for s in summary:
            key = f"c{s['concurrency']}_b{s['batch_size']}"
            for f in ("images_per_s", "p50_latency_ms", "p95_latency_ms", "p99_latency_ms", "gpu_util_mean_pct", "gpu_vram_peak_mib"):
                if s.get(f"{f}_mean") is not None:
                    metrics[f"{key}.{f}"] = s[f"{f}_mean"]
        if summary:
            metrics["max_images_per_s"] = max(s["images_per_s_mean"] for s in summary)
            vram = [s["gpu_vram_peak_mib_mean"] for s in summary if s.get("gpu_vram_peak_mib_mean") is not None]
            if vram:
                metrics["gpu_vram_peak_mib"] = max(vram)
        mlflow.log_metrics(metrics)
        mlflow.log_artifacts(str(run_dir), artifact_path=f"results/{run_dir.name}")
        run_id = run.info.run_id

    # Registry tags: the Model Portal shows the latest benchmark of each Model version.
    client, name, ver = MlflowClient(), mv["registered_model"], mv["registered_model_version"]
    tags = {"benchmark.run_id": run_id, "benchmark.status": metadata["status"], "benchmark.at": metadata["ended_at"] or utc_now()}
    if "max_images_per_s" in metrics:
        tags["benchmark.max_images_per_s"] = f"{metrics['max_images_per_s']:.1f}"
    if "c1_b1.p95_latency_ms" in metrics:
        tags["benchmark.p95_ms_c1_b1"] = f"{metrics['c1_b1.p95_latency_ms']:.3f}"
    if "gpu_vram_peak_mib" in metrics:
        tags["benchmark.gpu_vram_peak_mib"] = f"{metrics['gpu_vram_peak_mib']:.0f}"
    if smoke:
        tags["benchmark.smoke_top1"] = f"{smoke['triton_top1_accuracy']:.4f}"
    for k, v in tags.items():
        client.set_model_version_tag(name, ver, k, v)
    return run_id


def markdown_summary(mv: dict, summary: list[dict], smoke: dict | None, status: str) -> str:
    lines = [f"### Benchmark `{mv['model_version_id']}` ({mv['target_precision']}) on EKS L4: {status}", ""]
    if smoke:
        lines += [f"Smoke test: Triton top-1 {smoke['triton_top1_accuracy']:.4f} vs offline {smoke['offline_top1_accuracy']:.4f} "
                  f"(|diff| {smoke['diff_pp']:.2f} pp, tolerance {smoke['tolerance_pp']} pp)", ""]
    if summary:
        lines += ["| concurrency | batch | images/s | p50 ms | p95 ms | p99 ms | GPU util % | VRAM MiB |",
                  "|---:|---:|---:|---:|---:|---:|---:|---:|"]

        def f(v, d=1):
            return "–" if v is None else f"{v:.{d}f}"
        for s in summary:
            lines.append(f"| {s['concurrency']} | {s['batch_size']} | {f(s['images_per_s_mean'])} ± {f(s['images_per_s_std'])} "
                         f"| {f(s['p50_latency_ms_mean'], 2)} | {f(s['p95_latency_ms_mean'], 2)} | {f(s['p99_latency_ms_mean'], 2)} "
                         f"| {f(s['gpu_util_mean_pct_mean'])} | {f(s['gpu_vram_peak_mib_mean'], 0)} |")
    return "\n".join(lines)


# ------------------------------------------------------------------ main

def main() -> int:
    mv = load_model_version(os.environ["MODEL_NAME"], os.environ["MODEL_VERSION"])
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = WORK / "results" / f"{mv['model_version_id']}_{stamp}"
    run_dir.mkdir(parents=True)
    shutil.copy2(CONFIG_PATH, run_dir / "benchmark_config.yaml")
    bc = CFG["benchmark"]
    metadata = {
        "schema_version": METADATA_SCHEMA_VERSION, "run_id": run_dir.name, "status": "running",
        "started_at": utc_now(), "ended_at": None, "benchmark_started_at": None, "benchmark_ended_at": None,
        "model_version": {"model_version_id": mv["model_version_id"], "variant": mv["variant"],
                          "target_precision": mv["target_precision"], "artifact_format": mv["tags"].get("artifact_format"),
                          "registered_model": mv["registered_model"], "registered_model_version": mv["registered_model_version"],
                          "mlflow_run_id": mv["run_id"], "meta": mv["tags"]},
        "triton": {"version": None, "runner": "eks", "image": os.environ.get("TRITON_IMAGE"), "url": TRITON_HTTP,
                   "model_name": MODEL, "backend": "tensorrt", "instance_kind": "KIND_GPU", "instance_count": 1,
                   "max_batch_size": 32, "dynamic_batching": True},
        "hardware": {"gpu": {}, "client": {"node": os.environ.get("NODE_NAME"), "cpus": os.cpu_count(),
                                           "platform": platform.platform()}},
        "benchmark": {"perf_analyzer": PERF_ANALYZER, "protocol": bc["protocol"], "url": TRITON_HTTP,
                      "matrix": bc["matrix"], "measurement_interval_ms": bc["measurement_interval_ms"],
                      "stability_percentage": bc["stability_percentage"], "max_trials": bc["max_trials"],
                      "perf_analyzer_args": bc.get("perf_analyzer_args") or [], "warmup_runs": bc["warmup_runs"],
                      "repeats": bc["repeats"], "payload_images": bc["payload_images"], "payload_file": "payload.json"},
        "config_file": "benchmark_config.yaml", "config": CFG, "passes": [],
    }
    smoke, rows, summary, rc = None, [], [], 1
    try:
        log(f"{mv['registered_model']} v{mv['registered_model_version']} = {mv['model_version_id']} "
            f"({mv['target_precision']}, offline top-1 {mv['offline_top1']:.4f})")
        info = wait_for_triton(mv["model_version_id"], int(os.environ.get("WAIT_READY_S", "1500")))
        metadata["triton"].update(version=info["server_metadata"].get("version"), server_metadata=info["server_metadata"],
                                  served_model_version_id=info["served_model_version_id"])
        metadata["hardware"]["gpu"] = gpu_info()
        images, labels = load_cifar10_test(Path(os.environ.get("DATASET_CACHE", "/cache/cifar10")))

        smoke = smoke_test(mv, images, labels)
        (run_dir / "smoke_test.json").write_text(json.dumps(smoke, indent=2) + "\n")
        log(f"Smoke test {'PASSED' if smoke['passed'] else 'FAILED'}: top-1 {smoke['triton_top1_accuracy']:.4f} "
            f"vs offline {smoke['offline_top1_accuracy']:.4f} (|diff| {smoke['diff_pp']:.2f} pp)")
        if not smoke["passed"]:
            metadata["status"] = "smoke_test_failed"
            return 1

        x = preprocess(images[: int(bc["payload_images"])])
        payload = run_dir / "payload.json"
        payload.write_text(json.dumps({"data": [{mv["input_name"]: {"content": s.ravel().tolist(), "shape": list(s.shape)}} for s in x]}))
        metadata["benchmark_started_at"] = utc_now()
        rows = run_matrix(run_dir, payload, metadata)
        metadata["benchmark_ended_at"] = utc_now()
        if not rows:
            raise RuntimeError("no Benchmark point succeeded")
        add_gpu_metrics(rows, metadata)

        identity = {k: mv[k] for k in ("model_version_id", "variant", "target_precision")}
        with open(run_dir / "benchmark.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=BENCHMARK_COLUMNS + RUNTIME_COLUMNS, extrasaction="ignore")
            w.writeheader()
            w.writerows({**identity, **r} for r in rows)
        summary = summarize(rows)
        with open(run_dir / "summary.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(summary[0]))
            w.writeheader()
            w.writerows(summary)
        if metadata["failed_points"]:
            failed = ", ".join(f"c{p['concurrency']}_b{p['batch_size']}" for p in metadata["failed_points"])
            log(f"PARTIAL: {len(metadata['failed_points'])} point(s) failed and are left out: {failed}")
            metadata["status"], rc = "partial", 1
        else:
            metadata["status"], rc = "completed", 0
    except BaseException as e:  # noqa: BLE001 - record any failure in metadata.json and MLflow
        if metadata["status"] == "running":
            metadata["status"] = "failed"
            metadata["error"] = f"{type(e).__name__}: {e}"
        traceback.print_exc()
    finally:
        metadata["ended_at"] = utc_now()
        (run_dir / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
        md = markdown_summary(mv, summary, smoke, metadata["status"])
        print("----- SUMMARY -----\n" + md + "\n----- END SUMMARY -----", flush=True)
        try:
            run_id = log_to_mlflow(mv, run_dir, metadata, summary, smoke)
            print(f"BENCHMARK_MLFLOW_RUN={run_id}", flush=True)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())

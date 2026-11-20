"""Release step 1/3 (CPU): fetch one Model version from the MLflow registry.

Runs in the release Job (python image, mlflow-skinny). Resolves MODEL_NAME / MODEL_VERSION in the
MLflow registry, downloads its artifacts (model.onnx + meta.yaml, the serving contract of
aio-mlops-serving, plus calibration.cache for INT8) into /work/mv, checks the contract, and writes:

    /work/meta.json    meta.yaml as JSON (read by build_engine.py, which has no PyYAML)
    /work/IMAGE        full Serving image reference, tag = model_version_id (ADR-0001)
    /work/SKIP         present when that image already exists in ECR (tags are immutable)
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path

import boto3
import mlflow
import yaml
from mlflow import MlflowClient

WORK = Path("/work")
TAG_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}")
PRECISIONS = ("FP32", "FP16", "INT8")
REQUIRED = ("model_version_id", "variant", "target_precision", "input", "output", "offline_top1_accuracy")


def fail(msg: str) -> None:
    print(f"ERROR: {msg}", flush=True)
    sys.exit(1)


def find_one(root: Path, name: str) -> Path | None:
    hits = sorted(root.rglob(name))
    return hits[0] if hits else None


def image_exists(repo_uri: str, tag: str) -> bool:
    registry, repo = repo_uri.split("/", 1)
    ecr = boto3.client("ecr", region_name=os.environ["AWS_REGION"])
    try:
        ecr.describe_images(repositoryName=repo, imageIds=[{"imageTag": tag}])
        return True
    except ecr.exceptions.ImageNotFoundException:
        return False


def main() -> None:
    name, version = os.environ["MODEL_NAME"], os.environ["MODEL_VERSION"]
    repo_uri = os.environ["ECR_REPOSITORY_URI"]
    client = MlflowClient()
    mv = client.get_model_version(name, version)
    print(f"MLflow {name} v{version}: source={mv.source} run_id={mv.run_id}", flush=True)

    dst = WORK / "download"
    shutil.rmtree(dst, ignore_errors=True)
    mlflow.artifacts.download_artifacts(artifact_uri=mv.source, dst_path=str(dst))

    onnx_file, meta_file = find_one(dst, "model.onnx"), find_one(dst, "meta.yaml")
    if onnx_file is None or meta_file is None:
        fail(f"{name} v{version} must contain model.onnx and meta.yaml (aio-mlops-serving contract)")
    meta = yaml.safe_load(meta_file.read_text())

    missing = [k for k in REQUIRED if meta.get(k) is None]
    if missing:
        fail(f"meta.yaml is missing {missing}")
    tag = str(meta["model_version_id"])
    if not TAG_RE.fullmatch(tag):
        fail(f"model_version_id {tag!r} is not a valid image tag")
    if meta["target_precision"] not in PRECISIONS:
        fail(f"target_precision must be one of {PRECISIONS}, got {meta['target_precision']!r}")
    calib = find_one(dst, "calibration.cache")
    if meta["target_precision"] == "INT8" and calib is None:
        fail("INT8 needs calibration.cache next to model.onnx (built from TRAIN images only)")

    mv_dir = WORK / "mv"
    shutil.rmtree(mv_dir, ignore_errors=True)
    mv_dir.mkdir(parents=True)
    shutil.copy2(onnx_file, mv_dir / "model.onnx")
    if calib is not None:
        shutil.copy2(calib, mv_dir / "calibration.cache")
    meta["mlflow"] = {"registered_model": name, "version": str(version), "run_id": mv.run_id}
    (WORK / "meta.json").write_text(json.dumps(meta, indent=2))

    image = f"{repo_uri}:{tag}"
    (WORK / "IMAGE").write_text(image)
    if image_exists(repo_uri, tag):
        (WORK / "SKIP").write_text("image already in ECR\n")
        print(f"{image} already exists in ECR: nothing to build", flush=True)
    else:
        print(f"will build {image} ({meta['target_precision']}, variant {meta['variant']})", flush=True)


if __name__ == "__main__":
    main()

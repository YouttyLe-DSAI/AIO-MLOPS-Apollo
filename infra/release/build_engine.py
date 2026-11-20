"""Release step 2/3 (GPU L4): ONNX -> TensorRT Engine -> Triton model repository.

Runs in the release Job inside nvcr.io/nvidia/tensorrt:26.09-py3 on the L4 node, so the Engine is
built with the same TensorRT release (26.09) and the same GPU type Triton 26.09 will serve it on.
Reads /work/meta.json + /work/mv/model.onnx (written by fetch_model.py) and writes the build
context of the Serving image:

    /work/repo/Dockerfile
    /work/repo/cifar10_classifier/config.pbtxt
    /work/repo/cifar10_classifier/1/model.plan

config.pbtxt uses the fixed comparison settings of aio-mlops-serving (serving-decisions #10):
max_batch_size 32, dynamic batching, one KIND_GPU instance, identical for every Model version.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

WORK = Path("/work")
TRTEXEC = "/usr/src/tensorrt/bin/trtexec"
MODEL_NAME = "cifar10_classifier"
MAX_BATCH = 32
OPT_BATCH = 8
TRITON_DTYPES = {"float32": "TYPE_FP32", "float16": "TYPE_FP16", "int8": "TYPE_INT8", "int32": "TYPE_INT32"}


def shape_arg(name: str, dims: list[int], batch: int) -> str:
    return f"{name}:" + "x".join(str(d) for d in [batch, *dims])


def trtexec_command(meta: dict, onnx: Path, plan: Path) -> list[str]:
    inp = meta["input"]
    dims = [int(d) for d in inp["shape"][1:]]  # without the batch dim
    cmd = [
        TRTEXEC,
        f"--onnx={onnx}",
        f"--saveEngine={plan}",
        f"--minShapes={shape_arg(inp['name'], dims, 1)}",
        f"--optShapes={shape_arg(inp['name'], dims, OPT_BATCH)}",
        f"--maxShapes={shape_arg(inp['name'], dims, MAX_BATCH)}",  # = max_batch_size in config.pbtxt
    ]
    precision = meta["target_precision"]
    if precision == "FP32":
        cmd.append("--noTF32")  # the baseline was trained and evaluated in strict FP32
    elif precision == "FP16":
        cmd.append("--fp16")
    elif precision == "INT8":
        cmd += ["--int8", "--fp16", f"--calib={onnx.parent / 'calibration.cache'}"]
    return cmd


def tensor_block(t: dict) -> str:
    dims = ", ".join(str(int(d)) for d in t["shape"][1:])
    return f'  {{\n    name: "{t["name"]}"\n    data_type: {TRITON_DTYPES[t["dtype"]]}\n    dims: [ {dims} ]\n  }}\n'


def config_pbtxt(meta: dict) -> str:
    return (
        f'name: "{MODEL_NAME}"\n'
        'backend: "tensorrt"\n'
        f"max_batch_size: {MAX_BATCH}\n"
        "input [\n" + tensor_block(meta["input"]) + "]\n"
        "output [\n" + tensor_block(meta["output"]) + "]\n"
        "instance_group [\n  {\n    count: 1\n    kind: KIND_GPU\n  }\n]\n"
        "dynamic_batching { }\n"
        # Exposed by GET /v2/models/<model>/config: the benchmark Job checks it measures the right version.
        f'parameters {{ key: "model_version_id" value: {{ string_value: "{meta["model_version_id"]}" }} }}\n'
        f'parameters {{ key: "target_precision" value: {{ string_value: "{meta["target_precision"]}" }} }}\n'
    )


def main() -> None:
    if (WORK / "SKIP").exists():
        print("image already released; skipping Engine build", flush=True)
        return
    meta = json.loads((WORK / "meta.json").read_text())
    model_dir = WORK / "repo" / MODEL_NAME
    shutil.rmtree(WORK / "repo", ignore_errors=True)
    (model_dir / "1").mkdir(parents=True)
    plan = model_dir / "1" / "model.plan"

    cmd = trtexec_command(meta, WORK / "mv" / "model.onnx", plan)
    print("+ " + " ".join(cmd), flush=True)
    if subprocess.run(cmd).returncode != 0 or not plan.is_file():
        print("ERROR: trtexec failed", flush=True)
        sys.exit(1)

    (model_dir / "config.pbtxt").write_text(config_pbtxt(meta))
    shutil.copy2(Path(__file__).with_name("serving.Dockerfile"), WORK / "repo" / "Dockerfile")
    (WORK / "repo" / "model_version.json").write_text(json.dumps(meta, indent=2))
    print(f"Engine {plan.stat().st_size / 1e6:.1f} MB; repository:\n{(model_dir / 'config.pbtxt').read_text()}", flush=True)


if __name__ == "__main__":
    main()

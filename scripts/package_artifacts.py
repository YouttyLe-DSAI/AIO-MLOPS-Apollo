"""Make a portable, checksummed handoff without publishing any artifacts."""
import argparse
import hashlib
import json
import tarfile
from pathlib import Path


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--run", default="artifacts/resnet18_cifar10_seed42")
    p.add_argument("--output", default="artifacts/baseline-handoff.tar.gz")
    a = p.parse_args()
    run = Path(a.run)
    # A partially finished run must not be labelled a completed handoff.
    for required in ("summary.json", "test_metrics.json", "onnx_metrics.json", "model_metadata.json", "best.pt", "last.pt"):
        if not (run / required).is_file():
            raise FileNotFoundError(run / required)
    files = []
    for name in ("src", "configs", "scripts", "tests", "docs", "results", "benchmarks", "triton_models", "docker", ".github", str(run)):
        files.extend(p for p in Path(name).rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.suffix not in (".pyc", ".gz"))
    files.extend(Path(n) for n in ("README.md", "requirements-training.txt", ".gitignore") if Path(n).exists())
    checksums = {str(f): digest(f) for f in sorted(set(files))}
    target = Path(a.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    manifest = target.with_name("handoff-sha256.json")
    manifest.write_text(json.dumps(checksums, indent=2) + "\n")
    with tarfile.open(target, "w:gz") as tar:
        for filename in checksums:
            tar.add(filename, arcname=filename)
        tar.add(manifest, arcname="handoff-sha256.json")
    target.with_suffix(target.suffix + ".sha256").write_text(digest(target) + "  " + target.name + "\n")
    print(target, target.stat().st_size, "bytes")

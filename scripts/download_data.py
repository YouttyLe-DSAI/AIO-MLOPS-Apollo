"""Optional mirror download, always checked against torchvision's official MD5."""
import argparse
import hashlib
import json
import urllib.request
from pathlib import Path

URLS = {
    "official": "https://www.cs.toronto.edu/~kriz/cifar-10-python.tar.gz",
    "paddle": "https://dataset.bj.bcebos.com/cifar/cifar-10-python.tar.gz",
}
EXPECTED_MD5 = "c58f30108f718f92721af3b95e74349a"


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--source", choices=URLS, default="official")
    p.add_argument("--data-root", default="data")
    a = p.parse_args()
    root = Path(a.data_root)
    root.mkdir(parents=True, exist_ok=True)
    temporary = root / "cifar-10-download.tmp"
    with urllib.request.urlopen(URLS[a.source], timeout=60) as response, temporary.open("wb") as f:
        while chunk := response.read(1024 * 1024):
            f.write(chunk)
    data = temporary.read_bytes()
    actual = hashlib.md5(data).hexdigest()
    if actual != EXPECTED_MD5:
        raise RuntimeError(f"Archive checksum mismatch: {actual}")
    temporary.replace(root / "cifar-10-python.tar.gz")
    (root / "download_provenance.json").write_text(json.dumps({"url": URLS[a.source], "md5": actual,
        "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}, indent=2) + "\n")
    print("Verified CIFAR-10 archive:", actual)

# Model Portal

https://portal.3-39-32-158.sslip.io. Đăng nhập bằng **tài khoản MLflow** của bạn.

Portal liệt kê các Model version của `resnet18-cifar10` trong MLflow, kèm top-1 offline, kết quả benchmark mới nhất và version Triton đang serve. Mỗi version có hai nút:

| Nút | Gọi workflow | Làm gì |
| --- | --- | --- |
| **Release** | `release.yml` | Build Engine TensorRT trên L4, đẩy image lên ECR, Argo CD deploy Triton (~10–30 phút) |
| **Benchmark** | `benchmark.yml` | Release nếu cần, bật Triton với version đó, chạy Smoke test và Perf Analyzer, ghi kết quả vào MLflow, rồi tắt GPU (trừ khi chọn "giữ Triton") |

Portal không lưu gì cả: dữ liệu đọc từ MLflow (bằng tài khoản của bạn), từ Deployment `serving/triton` (chỉ đọc) và từ GitHub Actions.

## Bật nút Release/Benchmark (làm một lần)

Portal cần một GitHub token để gọi `workflow_dispatch`. Chưa có token thì portal chỉ xem được.

1. GitHub → Settings → Developer settings → **Fine-grained tokens** → Generate new token
   - Repository access: **Only select repositories** → `AIO-MLOPS-Apollo`
   - Permissions: **Actions: Read and write** (không cần quyền nào khác)
   - Expiration: sau ngày gỡ hạ tầng (xem `infra/TEARDOWN.md`)
2. Lưu token vào cluster (token không bao giờ nằm trong Git):
   ```bash
   read -rs GH_PORTAL_TOKEN && kubectl -n portal create secret generic portal-github --from-literal=token="$GH_PORTAL_TOKEN"; unset GH_PORTAL_TOKEN
   kubectl -n portal rollout restart deploy/portal
   ```

## Phát triển

```bash
cd portal
uv run --python 3.12 --with-requirements requirements.txt --with pytest python -m pytest -q tests
```

Push lên `main` (thư mục `portal/`) thì `.github/workflows/portal.yml` sẽ chạy test, build image `model-portal:<sha>` lên ECR và ghi tag vào GitOps `envs/dev/portal/kustomization.yaml`. Argo CD tự rollout.

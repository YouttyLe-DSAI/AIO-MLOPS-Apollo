# Release pipeline: MLflow Model version → Triton trên EKS

```
scripts/register_model_version.py        GitHub Actions: release (workflow_dispatch)          Argo CD
  thư mục Model version ──► MLflow ──►   1. OIDC → AWS (không có access key)                 envs/dev/triton/values.yaml
  (model.onnx + meta.yaml)   registry    2. Job trên GPU L4 (namespace release):        ──►   tag mới → deploy Triton
                                            fetch-model → build-engine (TensorRT 26.09)        → smoke test (PostSync)
                                            → ecr-login → build-image (BuildKit) → ECR
                                         3. commit tag vào GitOps repo
```

| File | Vai trò |
| --- | --- |
| `fetch_model.py` | Lấy Model version từ MLflow registry, kiểm tra hợp đồng `meta.yaml`, bỏ qua nếu image đã có trên ECR |
| `build_engine.py` | `trtexec` (TensorRT 26.09) trên L4 → `model.plan` + `config.pbtxt` (cấu hình cố định của repo serving) |
| `serving.Dockerfile` | Serving image = Triton 26.09 + model repository (tag = `model_version_id`, ADR-0001) |
| `release-job.yaml` | Kubernetes Job gồm 4 bước, chạy dưới ServiceAccount `release-builder` (Pod Identity: push ECR) |
| `run_release.sh` | Tạo Job, chờ, in log, trả về image. Workflow `.github/workflows/release.yml` gọi script này |

## Release một Model version

1. Đăng ký Model version lên MLflow (Hoàng, Mạnh):
   ```bash
   export MLFLOW_TRACKING_URI=https://mlflow.3-39-32-158.sslip.io MLFLOW_TRACKING_USERNAME=<tên> MLFLOW_TRACKING_PASSWORD=<mật khẩu>
   uv run --with "mlflow-skinny==3.16.0" --with pyyaml python scripts/register_model_version.py path/to/resnet18-baseline-fp32-v1
   ```
   Script in ra số version, ví dụ `resnet18-cifar10 version 2`.
2. GitHub → **Actions → release → Run workflow**, nhập `model_name=resnet18-cifar10`, `model_version=2`.
3. Khoảng 20–30 phút sau (lần đầu lâu hơn vì phải bật GPU và kéo image Triton), Argo CD deploy Triton với Model version đó và tự chạy smoke test. Xem tại https://argocd.3-39-32-158.sslip.io (app `triton`).

## Lưu ý

- **Mỗi target precision là một Model version riêng** (FP32, FP16, INT8). INT8 cần thêm `calibration.cache`, build từ ảnh **train**, đặt cạnh `model.onnx`.
- Tag trên ECR không ghi đè được. Release lại cùng `model_version_id` thì Job bỏ qua bước build và chỉ deploy lại.
- Triton giữ một GPU L4 (~$0.99/giờ) khi `replicas: 1`. Không benchmark thì đặt `replicas: 0` trong `envs/dev/triton/values.yaml` của GitOps repo; Cluster Autoscaler sẽ tắt node sau khoảng 10 phút.
- Chạy tay không qua GitHub (cần quyền vào cluster): `infra/release/run_release.sh resnet18-cifar10 2`.

# Model Portal

https://portal.3-39-32-158.sslip.io. Đăng nhập bằng **tài khoản MLflow** của bạn.

Trang gồm (từ trên xuống):

- **Triton đang phục vụ**: version đang chạy, trạng thái (đang phục vụ / đang khởi động / đang tắt), Top-1, throughput, p95.
- **Đang chạy**: các run Release/Benchmark đang chạy, có checklist từng bước và thanh tiến độ. Khi có tác vụ, trang tự cập nhật mỗi 10 giây.
- **Luồng triển khai**: 5 bước Đăng ký → Release → Deploy → Benchmark → Theo dõi. Bước đang chạy được làm nổi bật.
- **Model versions**: mỗi version là một thẻ có Top-1 offline, throughput tối đa, p95, VRAM, kết quả smoke test và hai nút:

| Nút | Gọi workflow | Làm gì |
| --- | --- | --- |
| **Release** | `release.yml` | Build Engine TensorRT trên L4, đẩy image lên ECR, Argo CD deploy Triton (~10–30 phút) |
| **Benchmark** | `benchmark.yml` | Release nếu cần, bật Triton với version đó, chạy Smoke test và Perf Analyzer, ghi kết quả vào MLflow, rồi tắt GPU (trừ khi tick "Giữ Triton chạy") |

- **So sánh các version** (Top-1 và throughput dạng thanh, ×N so với version có `variant: baseline`) và **Kết quả benchmark** (biểu đồ throughput batch 8 và latency p95 batch 1 theo concurrency, đọc từ run MLflow của từng version).
- **Lịch sử chạy**: 8 run gần nhất, ai bấm, bao lâu, kết quả. Giờ hiển thị là giờ Việt Nam.

Mẹo khi demo: nút mặt trăng ở góc phải đổi giao diện sáng/tối (máy chiếu nên dùng sáng). Mọi nút đều hỏi xác nhận trước khi chạy. Muốn gọi thử Triton sau khi đo thì tick "Giữ Triton chạy", xong nhớ tắt GPU (Benchmark lại không tick, hoặc `replicas: 0` trong GitOps).

Portal không lưu gì cả: dữ liệu đọc từ MLflow (bằng tài khoản của bạn), từ Deployment `serving/triton` (chỉ đọc) và từ GitHub Actions.

## Bật nút Release/Benchmark (làm một lần)

Portal cần một GitHub token để gọi `workflow_dispatch`. Chưa có token thì portal chỉ xem được.

1. Tạo token bằng link đã điền sẵn tên, quyền **Actions: Read and write** và hạn 30 ngày:
   https://github.com/settings/personal-access-tokens/new?name=apollo-model-portal&target_name=YouttyLe-DSAI&expires_in=30&actions=write
   - Repository access: **Only select repositories** → `AIO-MLOPS-Apollo` (link không điền sẵn được mục này)
   - Không thêm quyền nào khác. Hạn dùng nên kéo tới sau ngày gỡ hạ tầng (xem `infra/TEARDOWN.md`)
2. Lưu token vào cluster (token không bao giờ nằm trong Git). Token được nhập ẩn và đi qua stdin, không lộ trong lịch sử lệnh:
   ```bash
   read -rsp 'Token: ' T; echo; [ -n "$T" ] && printf %s "$T" | kubectl -n portal create secret generic portal-github --from-file=token=/dev/stdin --dry-run=client -o yaml | kubectl apply -f -; unset T
   kubectl -n portal rollout restart deploy/portal
   ```
3. Đổi token (hết hạn hoặc lộ): GitHub → Fine-grained tokens → `apollo-model-portal` → **Regenerate token**, rồi chạy lại bước 2.

Hiện tại: token `apollo-model-portal` tạo ngày 20/11/2026, hết hạn 30/12/2026. Revoke khi gỡ hạ tầng.

## Phát triển

```bash
cd portal
uv run --python 3.12 --with-requirements requirements.txt --with pytest python -m pytest -q tests
```

Push lên `main` (thư mục `portal/`) thì `.github/workflows/portal.yml` sẽ chạy test, build image `model-portal:<sha>` lên ECR và ghi tag vào GitOps `envs/dev/portal/kustomization.yaml`. Argo CD tự rollout.

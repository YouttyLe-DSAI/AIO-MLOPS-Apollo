# Benchmark Job: Perf Analyzer trên Triton của cluster

Đo Model version mà Triton trên EKS L4 đang serve, theo đúng giao thức của repo serving (`serving run`):

1. **Smoke test**: chạy toàn bộ 10k ảnh test CIFAR-10 qua Triton. Top-1 phải lệch không quá 0.5 pp so với `offline_top1_accuracy` trong `meta.yaml`. Không đạt thì không benchmark.
2. **Benchmark matrix**: concurrency {1, 2, 4, 8, 16, 32} × batch {1, 8}. Mỗi điểm chạy 1 lần warm-up và 3 lần đo. Mỗi lần đo là một lệnh `perf_analyzer` (cửa sổ 5 s, ổn định 10 %), payload là 64 ảnh test qua cùng bước preprocessing.
3. **Runtime metrics** cho từng lần đo:
   - GPU util và VRAM peak: lấy từ Mimir (DCGM).
   - Thời gian queue và compute của Triton: lấy từ counter của Triton trước và sau lần đo.
4. **Kết quả**: theo layout `results/<model_version_id>_<UTC>/` của repo serving (gồm `benchmark.csv`, `summary.csv`, `metadata.json`, raw CSV/stdout).
   - Cả thư mục được ghi vào MLflow, experiment **`benchmark`**.
   - Model version trong registry được gắn tag `benchmark.*`, Model Portal đọc các tag này.

Load generator chạy trên node system, không chạy trên node L4. Nhờ vậy Perf Analyzer không tranh CPU với Triton. Request đi qua Service nội bộ cluster.

| File | Vai trò |
| --- | --- |
| `benchmark.yaml` | Ma trận và ngưỡng, sao từ `configs/serving.gpu.yaml` của repo serving. Đổi ở bên đó trước |
| `benchmark.py` | Chạy trong `tritonserver:26.09-py3-sdk` (perf_analyzer 26.09 = Triton 2.73.0) |
| `benchmark-job.yaml` | Job trong namespace `release`, chạy trên node system |
| `run_benchmark.sh` | Tạo Job, stream log, ghi bảng kết quả vào summary của GitHub Actions |

## Chạy

- **GitHub → Actions → benchmark → Run workflow** (hoặc nút *Benchmark* trên Model Portal). Workflow tự release nếu cần, bật Triton với version đó, benchmark, rồi tắt GPU.
- Chạy tay khi Triton đã serve đúng version: `infra/benchmark/run_benchmark.sh resnet18-cifar10 <version>`.

Một lần chạy đủ ma trận mất khoảng 20–25 phút, chưa tính thời gian bật GPU. Lần đầu phải tải CIFAR-10 (khoảng 160 MB, server Toronto chậm, có thể mất gần 30 phút). Sau đó file test được cache trên PVC `release/benchmark-cache`.

**Khi có điểm lỗi.** Mỗi lệnh `perf_analyzer` lỗi sẽ được chạy lại một lần. Vẫn lỗi thì điểm đó bị bỏ qua, các điểm còn lại vẫn được ghi. Run có trạng thái `partial`, danh sách điểm lỗi nằm trong `metadata.json:failed_points`, và workflow báo đỏ để mọi người biết.

**Bộ nhớ.** Perf Analyzer giữ một bản ghi cho mỗi request trong suốt lần đo. Điểm concurrency 32 × batch 1 (khoảng 10k request/s) tốn RAM nhất: ngày 20/11 nó vượt giới hạn 4 GiB và bị OOMKilled. Job hiện xin 3 GiB, giới hạn 6 GiB. Log in bộ nhớ đỉnh của client sau mỗi lần đo (`client mem … MiB`); giá trị này cũng nằm trong `metadata.json:passes[].client_mem_peak_mib`.

Tải kết quả về để tổng hợp:

```bash
mlflow artifacts download -r <run_id> -d results/
uv run serving report results/<run_id>   # trong repo serving
```

Kết quả từ EKS có `metadata.json:triton.runner = "eks"`. Các file khác giống layout của `serving run`.

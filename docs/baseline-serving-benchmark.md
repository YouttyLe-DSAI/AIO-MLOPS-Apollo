# Baseline FP32: Release và Benchmark đã kiểm chứng

Model version: **resnet18-cifar10 v2**, `resnet18-baseline-fp32-v1`. Đo trên **NVIDIA L4**, TensorRT FP32 (`--noTF32`), Triton 2.73.0, 1 GPU instance, dynamic batching, max batch 32. Load generator chạy trên node riêng.

| Tiêu chí | Kết quả |
|---|---|
| Smoke test | 94,85% trên đủ 10.000 ảnh, lệch offline 0,00 pp |
| Ma trận | 12/12 điểm: concurrency 1/2/4/8/16/32 × batch 1/8 |
| Lượt đo | Mỗi điểm 1 warm-up + 3 lần đo, tổng 36 lần đo chính thức |
| Metadata | `completed`, `failed_points: []` |
| CV throughput lớn nhất | 0,78774%, dưới 5% |
| Throughput cao nhất (mean 3 lần đo) | 7.907,57 ảnh/s tại concurrency 16, batch 8 |
| P95 tại concurrency 1, batch 1 (mean) | 1,61 ms |
| VRAM peak theo cửa sổ đo | 350 MiB |
| Cleanup | GitOps replicas 0; Portal xác nhận pod 0/0. Node shutdown không được xác nhận riêng; autoscaler có thời gian chờ rảnh 10 phút |

- [Workflow Release/Benchmark thành công](https://github.com/YouttyLe-DSAI/AIO-MLOPS-Apollo/actions/runs/45157351610)
- [MLflow benchmark run](https://mlflow.3-39-32-158.sslip.io/#/experiments/3/runs/96bc50c90d1945ed921c30046014fef0)
- [Model Portal](https://portal.3-39-32-158.sslip.io)
- [Báo cáo và biểu đồ](../results/apollo-benchmark/README.md)
- [Kiểm tra độc lập](../results/apollo-benchmark/verification.json)
- [SHA-256 từng raw file](../results/apollo-benchmark/raw-files-sha256.json)
- [Bằng chứng cleanup](../results/apollo-handoff/benchmark-cleanup.json)

Đường dẫn raw run và experiment ID thực tế được lưu trong `results/apollo-benchmark/verification.json`. Có đủ 102 files gốc đã tải từ MLflow, gồm config, metadata, Smoke test, payload và CSV/stdout của mọi warm-up/lần đo. Các file gốc được giữ nguyên; báo cáo được tính lại từ `benchmark.csv` bằng mean và sample standard deviation.

## Tạo lại báo cáo

Dùng môi trường đã có package `serving` từ repo `dauvannam1804/aio-mlops-serving` (commit dùng để tạo report: `6b89488f51623e0902761eaed4e49b9894437acb`) và matplotlib. Từ root Apollo:

```bash
python scripts/report_apollo_benchmark.py --root .
```

Producer EKS ghi `hardware.gpu.modelName`, reader workstation dùng `hardware.gpus[].name`. Script ánh xạ metadata chỉ trong bộ nhớ để report hiển thị đúng GPU; không ghi đè `metadata.json` gốc. CPU model không được ghi nhận và được hiển thị là `not recorded`, không suy đoán từ tên node.

# Cách đóng góp vào repo Apollo

Áp dụng Working Agreement của nhóm: một task có một owner và một reviewer, hoàn thành phải có **bằng chứng**, mọi thay đổi kỹ thuật đi qua **branch → PR → review**.

## 1. Quy trình

1. Nhận task trong Google Sheet `AIO-MLOPS2026`. Task phải có đầu ra, owner, reviewer và acceptance criteria.
2. Tạo branch từ `main` mới nhất:
   ```bash
   git switch main && git pull
   git switch -c feat/baseline-training
   ```
3. Commit nhỏ, đúng một mục đích. Push và mở PR vào `main` theo mẫu PR của repo.
4. Gán reviewer theo bảng ở mục 3. **Không tự merge PR của chính mình khi chưa có approval.**
5. Merge xong thì cập nhật trạng thái task trong Sheet và dán link PR làm bằng chứng.

## 2. Đặt tên branch và commit

| Tiền tố branch | Dùng cho | Ví dụ |
| --- | --- | --- |
| `feat/` | Tính năng, code mới | `feat/distillation` |
| `fix/` | Sửa lỗi | `fix/onnx-dynamic-batch` |
| `exp/` | Thí nghiệm chưa chắc giữ | `exp/pruning-30pct` |
| `docs/` | Tài liệu, report | `docs/benchmark-report` |
| `infra/` | Hạ tầng, CI/CD | `infra/cluster-autoscaler` |
| `chore/` | Việc lặt vặt (cấu trúc, dependency) | `chore/repo-skeleton` |

Commit theo [Conventional Commits](https://www.conventionalcommits.org/): `<loại>(<phạm vi>): <mô tả>`, ví dụ `feat(baseline): add stratified split with seed 42`.

## 3. Ai review phần nào

| Phần | Owner | Reviewer |
| --- | --- | --- |
| `src/baseline/`, cấu hình training | Hoàng | Tuấn (thêm Mạnh nếu đụng tới calibration hoặc interface ONNX) |
| `src/compression/`, build TensorRT | Mạnh | Hoàng hoặc Tuấn |
| `src/serving/`, `triton_models/`, `benchmarks/` | Nam | Tuấn |
| `infra/`, `.github/` | Tuấn | Nam |
| `docs/` (report, slide) | Người viết | Ít nhất một thành viên khác |

## 4. Những thứ KHÔNG được commit (repo public)

| Loại | Đưa vào đâu |
| --- | --- |
| Mật khẩu, token, access key, file `.pem` | Không ở đâu trong Git. Dùng biến môi trường hoặc Kubernetes Secret |
| Dataset CIFAR-10 | Script tải trong `scripts/` (kiểm tra checksum) |
| Checkpoint `*.pt` / `*.pth`, model `*.onnx`, Engine `*.plan`, `*.npy` / `*.npz` | **MLflow** (artifact của run, lưu trên S3) |
| Kết quả chạy thử (`results/`) | Không commit. Bộ số liệu chính thức đã chốt thì đưa vào `benchmarks/` |

`.gitignore` đã chặn sẵn các đuôi file trên. Trước khi commit, luôn chạy `git status` để kiểm tra lại.

## 5. Quy ước MLflow

- Tracking URI: `https://mlflow.3-39-32-158.sslip.io`. Mỗi người dùng tài khoản riêng.
- Experiment theo Variant: `baseline`, `distillation`, `pruning`, `ptq-int8`.
- Mỗi run log đủ: config (params), seed, metric theo epoch, accuracy trên test set, artifact `model.onnx`, và commit Git (`mlflow.set_tag("git_commit", ...)`).
- Model version giao cho phần serving theo hợp đồng `meta.yaml` (ADR-0001: **mỗi target precision là một Model version riêng**), xem repo serving `docs/model-version-contract.md`.

## 6. Bằng chứng khi đóng task (Definition of Done)

Một PR được coi là xong khi có:

- [ ] Code hoặc config chạy được theo hướng dẫn trong README của thư mục tương ứng
- [ ] Link MLflow run, hoặc file kết quả / log chứng minh đầu ra
- [ ] Test hoặc lệnh kiểm tra đã chạy (ghi lệnh và kết quả vào PR)
- [ ] Không có mật khẩu hay file lớn trong diff
- [ ] Tài liệu đã cập nhật nếu thay đổi interface (ONNX, `meta.yaml`, layout kết quả)

# Đối chiếu kế hoạch và hướng dẫn kiểm tra

Cập nhật 22/11/2026. Người viết: Tuấn (PM, Integration & Release).

Tài liệu này dành cho cả nhóm, nhằm trả lời ba câu hỏi trước khi bàn giao:

1. Phần Integration & Release đã làm những gì?
2. So với Project Handbook (*AIO-MLOps-2026*: tab 01 Charter, tab Technical Design, Benchmark Protocol, DoR/DoD, và *03-architecture-and-technical-decisions*), nhóm đã đủ chưa, còn thiếu gì, ai làm?
3. Mỗi thành viên tự kiểm tra từng phần như thế nào: kiểm tra ở đâu, và kết quả ra sao là đúng?

Luồng chi tiết của nền tảng nằm ở [`platform-workflow.md`](platform-workflow.md). File này không lặp lại phần đó.

**Ký hiệu:** ✅ đã xong, có bằng chứng · 🟡 xong một phần · ❌ chưa có · ↔ làm khác kế hoạch (cần ghi vào Decision Log)

## 0. Quyền truy cập cần có

| Dịch vụ | Địa chỉ | Tài khoản |
| --- | --- | --- |
| Repo Apollo (public) | https://github.com/YouttyLe-DSAI/AIO-MLOPS-Apollo | Hoàng, Mạnh có quyền ghi. Nam được mời lại ngày 23/11 (quyền ghi), nhận lời mời tại https://github.com/YouttyLe-DSAI/AIO-MLOPS-Apollo/invitations trong 7 ngày |
| Repo serving (private) | https://github.com/dauvannam1804/aio-mlops-serving | Nam (owner) |
| Repo GitOps (private) | `YouttyLe-DSAI/AIO-MLOPS-Apollo-gitops` | Hiện chỉ Tuấn có quyền |
| MLflow | https://mlflow.3-39-32-158.sslip.io | Mỗi người một tài khoản: `hoang`, `manh`, `nam` |
| Model Portal | https://portal.3-39-32-158.sslip.io | Đăng nhập bằng tài khoản MLflow |
| Grafana | https://grafana.3-39-32-158.sslip.io | Mỗi người một tài khoản `hoang`, `manh`, `nam`, quyền Editor (xem dashboard Apollo, dùng Explore để xem metric GPU và log) |
| Argo CD | https://argocd.3-39-32-158.sslip.io | Chỉ có tài khoản admin, nhờ Tuấn mở |

Repo Apollo là public, nên mật khẩu **không bao giờ** được ghi vào repo. Cần mật khẩu thì hỏi Tuấn qua tin nhắn riêng.

Mật khẩu ban đầu Tuấn gửi qua tin nhắn. Sau lần đăng nhập đầu, mỗi người nên tự đổi:

- **Grafana:** góc trên bên phải, bấm avatar → Profile → Change password.
- **MLflow** (Portal dùng chung mật khẩu này):

  ```bash
  curl -u <user>:<mật-khẩu-cũ> -X PATCH https://mlflow.3-39-32-158.sslip.io/api/2.0/mlflow/users/update-password \
    -H 'Content-Type: application/json' \
    -d '{"username":"<user>","password":"<mật-khẩu-mới>","current_password":"<mật-khẩu-cũ>"}'
  ```

## 1. Kết luận nhanh

Phần nền tảng **đã chạy trọn một vòng**. Một Model version đi từ MLflow qua TensorRT Engine, lên Triton trên GPU L4, được benchmark, rồi kết quả quay về MLflow và hiện trên Portal. Mọi bước đều bấm được trên Portal và truy vết được qua GitHub Actions, Git và MLflow. Ma trận benchmark mới nhất có đủ 12/12 điểm.

Nhưng tất cả mới chạy với **Model version placeholder** (Top-1 38,53 %, chỉ để thử pipeline). Các variant thật (baseline, FP16, INT8, KD, pruning) **chưa có trên MLflow registry**, nên chưa có bảng so sánh, report và slides. Đây là phần quyết định nghiệm thu, cần làm ngay (mục 4).

| Mục tiêu trong Charter §3 | Trạng thái | Bằng chứng / còn thiếu | Owner |
| --- | --- | --- | --- |
| 1. Baseline với split, preprocessing, evaluation thống nhất | 🟡 | ResNet-18 CIFAR-10, test top-1 94,85 %, split 45k/5k/10k, seed 42, calib 1.000 ảnh **train**. Mới nằm trong gói handoff, chưa có trên Git và MLflow registry | Hoàng |
| 2. TensorRT FP32, FP16, INT8 | 🟡 | Pipeline build Engine (trtexec 26.09 trên L4) đã chạy với FP32 placeholder. Chưa có Model version FP16/INT8 để build | Mạnh (variant), Tuấn (pipeline) |
| 3. Student model bằng knowledge distillation | ❌ | Chưa có model hay run nào | Mạnh |
| 4. Structured pruning, có bằng chứng kiến trúc nhỏ hơn | ❌ | Chưa có model hay run nào | Mạnh |
| 5. Serve qua Triton, benchmark công bằng | 🟡 | Pipeline xong, chạy đủ ma trận với placeholder. Đang chờ variant thật | Nam, Tuấn |
| 6. Tài liệu, code, artifact để người khác chạy lại | 🟡 | Tài liệu nền tảng đã có. Còn thiếu README tổng (PR #2), biên bản chạy lại, tag `v1.0` | Tuấn và cả nhóm |

## 2. Phần Integration & Release đã làm, và cách kiểm tra

Mỗi mục gồm ba phần: **Đã làm**, **Kiểm tra** (ở đâu, làm gì) và **Đúng khi** (kết quả mong đợi).

### 2.1 Hạ tầng AWS bằng Terraform

**Đã làm**

- EKS 1.36 ở `ap-northeast-2` (Seoul), với ba node group:
  - `system`: spot, 2–3 node, chạy MLflow, Argo CD, Grafana, Portal.
  - `gpu-l4`: g6.xlarge on-demand, 0–2 node, chạy Triton và build Engine.
  - `loadgen`: c6i.2xlarge on-demand, 0–1 node, chỉ chạy Perf Analyzer.
- Các tài nguyên khác:
  - ECR cho image.
  - S3 cho artifact MLflow.
  - IAM / Pod Identity.
  - GitHub OIDC, nên không có access key nào trong GitHub.
  - Budget 300 USD/tháng.
- Cluster Autoscaler bật node GPU và node loadgen khi có pod cần, rồi tắt sau 10 phút rảnh.
- Code nằm trong [`infra/terraform/`](../infra/terraform/README.md) (gồm `modules/` và `environments/dev/{foundation,platform}`).

**Kiểm tra**

1. Đọc `infra/terraform/README.md` để biết cấu trúc và lý do các quyết định.
2. Mỗi PR đều chạy job `terraform` (`fmt -check` và `validate`). Xem tab **Actions** của repo.
3. `terraform plan` cần quyền AWS, nên nhờ Tuấn chạy cho xem.

**Đúng khi**

- Job `terraform` xanh.
- `plan` báo `No changes`, nghĩa là hạ tầng thật khớp với code.
- Lúc không có việc, thẻ **Triton đang phục vụ** trên Portal ở trạng thái *Đang tắt* (replicas 0) và không có node GPU nào chạy.

### 2.2 GitOps và Argo CD

**Đã làm**

- Các ứng dụng (MLflow, Triton, Grafana stack, Portal) được deploy từ repo GitOps. Không ai `kubectl apply` bằng tay.
- Argo CD tự sync và tự sửa lệch (self-heal).
- Chart Triton có smoke test PostSync, gửi request batch 1, 8 và 32 sau mỗi lần deploy.

**Kiểm tra**

- Mở Argo CD (nhờ Tuấn).
- Xem lịch sử commit của repo GitOps. Mỗi lần release hay benchmark để lại một commit, ví dụ `benchmark: serve resnet18-placeholder-fp32-v1 (run 44856800277)` và `benchmark: replicas 0 after run 44856800277`.

**Đúng khi**

- Cả 11 application đều **Synced** và **Healthy**.

### 2.3 MLflow: tracking và Model registry

**Đã làm**

- MLflow chạy trên EKS, lưu dữ liệu trong Postgres và S3. Mỗi người có tài khoản riêng.
- Registry `resnet18-cifar10` và experiment `benchmark`.
- Đăng ký version bằng [`scripts/register_model_version.py`](../scripts/register_model_version.py). Phải chạy `serving validate` của repo serving trước.

**Kiểm tra**

1. Đăng nhập MLflow, vào **Models → resnet18-cifar10 → Version 1**. Xem các tag.
2. Vào **Experiments → benchmark** và mở run `485e2173a01f46f4bee43f5f7a1a1903`.

**Đúng khi**

- Version 1 có đủ các tag `model_version_id`, `variant`, `target_precision`, `offline_top1_accuracy` và nhóm `benchmark.*`.
- Run ở trạng thái `FINISHED`, tag `status = completed`.

### 2.4 Release pipeline: Model version lên Triton

**Đã làm**

`release.yml` chạy theo thứ tự:

1. Job trên node L4 lấy model từ MLflow và kiểm tra `meta.yaml`.
2. Build Engine bằng `trtexec`. TensorRT 26.09 cùng bản phát hành với Triton 26.09 (Triton 2.73.0).
3. Đóng image `resnet18-triton:<model_version_id>` và đẩy lên ECR. Tag đã có thì bỏ qua bước build.
4. Ghi tag vào repo GitOps.
5. Argo CD cập nhật Triton:
   - Triton đang bật (replicas ≥ 1): chạy image mới, rồi chạy smoke test PostSync.
   - Triton đang tắt (replicas 0, mặc định để không tốn GPU): image mới được dùng ở lần bật sau, ví dụ khi Benchmark.

Chi tiết xem [`infra/release/README.md`](../infra/release/README.md).

**Kiểm tra**

- Vào **Actions → release**. Run chạy trọn đầu tiên là [44669359621](https://github.com/YouttyLe-DSAI/AIO-MLOPS-Apollo/actions/runs/44669359621) (20/11). Mở step *Release the Model version* để xem log build.
- Thẻ **Triton đang phục vụ** trên Portal hiện tag mới (`model_version_id`). Trạng thái là *Đang phục vụ* nếu Triton đang bật, *Đang tắt* nếu replicas 0.

**Đúng khi**

- Run xanh.
- Image `resnet18-triton:<model_version_id>` đã có trên ECR, và tag đã được ghi vào repo GitOps.
- Khi Triton bật (bằng Benchmark hoặc khi đang chạy sẵn): ready với đúng `model_version_id` và smoke test PostSync pass.

### 2.5 Benchmark pipeline

**Đã làm**

`benchmark.yml` chạy theo thứ tự:

1. Release Model version nếu image chưa có.
2. Bật Triton.
3. Chạy Job trên node `loadgen`:
   - **Smoke test**: cho 10.000 ảnh test qua Triton. Top-1 phải lệch không quá 0,5 pp so với `offline_top1_accuracy`. Không đạt thì dừng, không benchmark.
   - **Ma trận đo**: concurrency {1, 2, 4, 8, 16, 32} × batch {1, 8}. Mỗi điểm có 1 warm-up và 3 lần đo bằng `perf_analyzer` (cửa sổ 5 s, ngưỡng ổn định 10 %).
   - **Runtime metrics**: GPU util và VRAM lấy từ DCGM, thời gian queue và compute lấy từ Triton.
4. Ghi kết quả vào MLflow theo layout `results/` của repo serving, và gắn tag `benchmark.*` lên Model version.
5. Tắt GPU.

Chi tiết xem [`infra/benchmark/README.md`](../infra/benchmark/README.md).

**Kiểm tra**

1. Vào **Actions → benchmark**, mở run [44856800277](https://github.com/YouttyLe-DSAI/AIO-MLOPS-Apollo/actions/runs/44856800277) (21/11).
2. Trong MLflow, mở run đó, vào **Artifacts → `results/<model_version_id>_<UTC>/`**. Phải có:
   - `benchmark.csv`: mỗi dòng là một điểm × một lần đo.
   - `summary.csv`: trung bình và độ lệch chuẩn của mỗi điểm.
   - `metadata.json`: điều kiện đo, toàn bộ các lần đo, `failed_points`.
   - `raw/`: CSV và stdout gốc của từng lần đo, kể cả warm-up.
3. Lấy về máy để tổng hợp:

   ```bash
   mlflow artifacts download -r <run_id> -d results/
   uv run serving report results/<thư mục>    # trong repo serving
   ```

**Đúng khi**

- `metadata.json` có `status: completed` và `failed_points: []`.
- Đủ 12 điểm, mỗi điểm 3 lần đo.
- Hệ số biến thiên (CV) của throughput giữa 3 lần đo dưới 5 %. Lần chạy 21/11 có CV cao nhất 0,9 %.

Số đo của placeholder **chỉ để chứng minh pipeline, không đưa vào report**: tối đa 31.753 ảnh/s (concurrency 32, batch 8); p95 1,15 ms ở concurrency 1, batch 1; VRAM đỉnh 328 MiB.

### 2.6 Model Portal

**Đã làm**

- Trang web đăng nhập bằng tài khoản MLflow. Trang hiển thị:
  - danh sách Model version và version đang serve;
  - nút **Release** và **Benchmark**, mỗi nút gọi workflow trên GitHub Actions;
  - tiến độ run trực tiếp, tự refresh mỗi 10 giây;
  - so sánh và biểu đồ throughput / p95 lấy từ MLflow.
- `portal.yml` tự build image và rollout qua GitOps khi có thay đổi trong `portal/`.

**Kiểm tra**

1. Mở Portal và đăng nhập.
2. Xem thẻ v1.
3. Nếu muốn, bấm **Benchmark** để xem checklist tiến độ. Mỗi lần chạy giữ GPU khoảng 25 phút (khoảng 0,5 USD), nên báo nhóm trước khi bấm. Các run chạy lần lượt, không song song.

**Đúng khi**

- Thẻ v1 hiện Top-1 38,53 %, smoke test khớp offline, tối đa 31.753 ảnh/s.
- Biểu đồ throughput và latency theo concurrency có dữ liệu.

### 2.7 Monitoring

**Đã làm**

- Alloy gửi metrics vào Mimir và log vào Loki, Grafana hiển thị cả hai.
- DCGM exporter cho GPU.
- Dashboard **Apollo · Triton serving**: throughput, latency, queue/compute, GPU util, VRAM, công suất, nhiệt độ.

**Kiểm tra**

- Mở dashboard trong Grafana (nhờ Tuấn) và chọn khung giờ của một lần benchmark.
- Vào **Explore → Loki**, truy vấn `{namespace="release"}` để xem log của Job benchmark.

**Đúng khi**

- Có số liệu GPU và Triton trong đúng khung giờ chạy.

### 2.8 CI, bảo vệ nhánh, bảo mật

**Đã làm**

- `ci.yml` chạy cho mỗi PR:
  - Terraform `fmt` / `validate`;
  - kiểm tra cú pháp script bash và Python;
  - pytest của Portal.
- Nhánh `main` được bảo vệ: phải qua PR và cả 3 check `terraform`, `scripts`, `portal` phải pass mới merge được.
- GitHub đăng nhập AWS bằng OIDC, nên không có access key nào.
- Secret chỉ nằm trong Kubernetes Secret và file tfvars bị git-ignore.

**Kiểm tra**

- Mở một PR nhỏ và xem đủ 3 check.
- Chạy `git grep -nE 'AKIA[0-9A-Z]{16}|BEGIN (RSA|OPENSSH) PRIVATE KEY-----'` trong repo Apollo.

**Đúng khi**

- PR không merge được khi check còn đỏ.
- `git grep` không ra kết quả nào.

### 2.9 Sự cố đã xử lý, chi phí, gỡ hạ tầng

**Sự cố 20–21/11 (đã xử lý)**

- Perf Analyzer giữ một bản ghi cho mỗi request. Ở điểm concurrency 32 × batch 1, nó tốn hơn 4 GiB RAM.
- Lần đầu, pod bị OOMKilled. Lần sau, khi đã nới giới hạn RAM, nó làm treo một node system; MLflow và Argo CD ngừng cho tới khi reboot node.
- Cách sửa (PR #7, #8):
  - tách node `loadgen` riêng cho Perf Analyzer;
  - script tự dừng lần đo khi gần chạm giới hạn RAM;
  - gắn label GPU để Cluster Autoscaler không bật thừa node L4.
- Chi tiết trong `infra/benchmark/README.md`, mục *Bộ nhớ*.

**Chi phí**

- GPU và node loadgen chỉ chạy khi có việc.
- Mỗi lần benchmark mất khoảng 25 phút, cộng 10 phút rảnh trước khi node tự tắt.

**Gỡ hạ tầng**

- Làm theo [`infra/TEARDOWN.md`](../infra/TEARDOWN.md).
- Chỉ gỡ sau nghiệm thu, khi PM ra lệnh. Trước khi gỡ phải backup MLflow.

## 3. Đối chiếu với Project Handbook

### 3.1 Deliverables bắt buộc (Charter §6)

| ID | Yêu cầu | Trạng thái | Hiện có / còn thiếu |
| --- | --- | --- | --- |
| DEL-01 | Repo đúng cấu trúc `src/ configs/ scripts/ triton_models/ benchmarks/ results/ docs/` | 🟡 | **Có:** repo serving của Nam đã đúng cấu trúc; Apollo có `docs/ infra/ portal/ scripts/`. **Thiếu:** khung DEL-01 cho Apollo nằm ở PR #2 (chưa merge); code train/KD/pruning chưa có trên Git. Cần chốt repo nào là repo nộp |
| DEL-02 | Môi trường và deployment: container, phiên bản, hướng dẫn | 🟡 | **Có:** trên cloud, Terraform + Helm + GitOps với phiên bản pin (Triton/TensorRT 26.09, EKS 1.36, add-on EKS); repo serving có `docker/serving.Dockerfile` và hướng dẫn cài trên máy sạch. **Thiếu:** bảng phiên bản chung (`docs/versions.md`), Dockerfile cho train/TensorRT ở máy local |
| DEL-03 | Code train/eval, KD, pruning, quantization; checkpoint hoặc link | ❌ | Chưa có trên Git hay registry. Baseline mới nằm trong gói handoff |
| DEL-04 | Benchmark package: script, raw CSV/JSON, metadata phần cứng, config, log | 🟡 | **Có:** đủ script, raw, metadata, config và log (`infra/benchmark`, repo serving, MLflow). **Thiếu:** mới chạy với placeholder |
| DEL-05 | Technical report: bảng, biểu đồ, kết luận có bằng chứng | ❌ | Chờ benchmark các variant thật |
| DEL-06 | Slides khoảng 30 trang, report khoảng 10–20 trang | ❌ | Mới có slides baseline (Hoàng) và slide MLOps overview (Nam) |

### 3.2 Tiêu chí thành công (Charter §9)

| Tiêu chí | Trạng thái | Ghi chú |
| --- | --- | --- |
| Có kết quả cho FP32/FP16, INT8, distillation, structured pruning | ❌ | Registry mới có placeholder |
| Đánh giá quality trước khi benchmark serving | ✅ | Smoke test 10k ảnh chặn benchmark nếu lệch quá 0,5 pp. `offline_top1_accuracy` là trường bắt buộc trong `meta.yaml` |
| Có warm-up, ít nhất 3 lần đo, ghi rõ điều kiện đo | ✅ | 1 warm-up + 3 lần đo. Điều kiện nằm trong `benchmark_config.yaml` và `metadata.json` |
| Calibration không lấy từ test, không leakage | 🟡 | Contract yêu cầu `calibration.cache` build từ ảnh train. Mạnh cần nộp bằng chứng khi có INT8 (nguồn ảnh, số ảnh, phương pháp) |
| Pruning có bằng chứng kiến trúc nhỏ hơn | ❌ | Cần bảng params/FLOPs trước và sau, đo bằng cùng một công cụ |
| Kết quả có đơn vị, raw data, truy vết được | ✅ | Đơn vị ảnh/s, request/s, ms, MiB. Có `raw/`. Truy vết qua MLflow run ID, `model_version_id` và run ID của GitHub Actions |
| Người khác chạy lại theo README trên máy sạch | 🟡 | Phần cloud chạy lại bằng một nút trên Portal. Repo serving có hướng dẫn máy sạch (CPU). Còn thiếu buổi chạy lại có biên bản (REPRO-04) |
| Bàn giao đủ report và slides | ❌ | |

### 3.3 Benchmark Protocol và DoD benchmark

| Yêu cầu trong Handbook | Thực tế | Trạng thái |
| --- | --- | --- |
| Warm-up, ít nhất 3 lần đo mỗi cấu hình | 1 warm-up + 3 lần đo. File kiến trúc đề xuất ≥ 5 lần đo, cần chốt một con số | ✅ |
| Concurrency 1–32 × batch {1, 4, 8} | × batch {1, 8}, theo config GPU của repo serving | ↔ |
| CV throughput giữa 3 lần đo < 5 % | Cao nhất 0,9 % (21/11) | ✅ |
| Mỗi lúc chỉ một model trên GPU; GPU sạch | Triton chỉ chạy một Model version trên node L4 riêng (có taint). Không có bước pre-check `nvidia-smi` riêng | ✅ |
| Đo VRAM khi model rảnh (idle) | Chỉ có VRAM đỉnh | 🟡 |
| Lấy mẫu GPU util mỗi 200–500 ms | DCGM qua Alloy, 15 s một lần | 🟡 |
| Raw CSV/JSON và metadata môi trường | `raw/` và `metadata.json` (phiên bản Triton, image, GPU, node) | ✅ |
| Ghi rõ infer/s hay img/s | `benchmark.csv` có cả `requests_per_s` và `images_per_s` | ✅ |
| Load generator không tranh tài nguyên với server | Node `loadgen` riêng | ✅ |
| Perf Analyzer đi qua Gateway (file kiến trúc) | Đi thẳng tới Service trong cluster | ↔ |

### 3.4 Checkpoint theo sprint

| Checkpoint | Trạng thái | Ghi chú |
| --- | --- | --- |
| CP1, Baseline | 🟡 | **Có:** baseline ResNet-18 CIFAR-10 (thay vì ResNet-50); Triton chạy được trên EKS (với placeholder). **Thiếu:** bảng phiên bản |
| CP2, Optimization | 🟡 | **Có:** pipeline build Engine. **Thiếu:** Engine FP16/INT8 thật kèm accuracy |
| CP3, Compression | ❌ | **Thiếu:** KD, pruning, bảng params/FLOPs |
| CP4, Serving benchmark | 🟡 | **Có:** pipeline đo đủ ma trận. **Thiếu:** đo trên các variant thật, biểu đồ trade-off |
| CP5, Teaching handoff | ❌ | **Thiếu:** biên bản chạy lại, README tổng, `v1.0`, report, slides |

Repo chưa có git tag `cp1`–`cp5` nào.

### 3.5 Điểm làm khác kế hoạch (cần ghi vào Decision Log)

| Handbook | Thực tế | Ghi chú |
| --- | --- | --- |
| CIFAR-100, input 224×224, ImageNet-pretrained | CIFAR-10, 32×32, train từ đầu | Theo baseline của Hoàng |
| Teacher ResNet-50, student ResNet-18 | Baseline ResNet-18 | Cần chốt teacher và student cho KD |
| RTX 4090 cho serving và benchmark | AWS EKS g6.xlarge (L4) ở Seoul; train trên máy riêng (baseline chạy trên H100) | Số đo trong report là số trên L4 |
| ADR-01: chưa chốt bản NGC | 26.09: Triton 2.73.0, TensorRT cùng bản phát hành | Ghi vào bảng phiên bản |
| GitLab CI | GitHub Actions + OIDC | |
| Upload API (FastAPI), S3 bất biến, metadata trong Postgres | MLflow registry + `register_model_version.py`; image ECR tag theo `model_version_id` | |
| Gateway API | Traefik + cert-manager | |
| Nơi lưu artifact: Drive/HF Hub (cần chốt) | MLflow (artifact trong S3) | |
| Charter §5 đề xuất để ngoài phạm vi: Kubernetes, giao diện web, autoscaling, GitOps | Đã làm (EKS, Portal, Cluster Autoscaler, Argo CD) như phần thực hành MLOps | Charter §5 ghi đây chỉ là đề xuất. DEL-02 vẫn cần đường chạy local |
| Rollback bằng `git revert` | Release lại version cũ trên Portal, tức là commit tag mới vào GitOps | Chưa diễn tập |

## 4. Việc còn lại trước bàn giao

| # | Việc | Owner | Xong khi |
| --- | --- | --- | --- |
| 1 | Đăng ký Model version thật: baseline FP32, FP16, INT8 (kèm `calibration.cache` từ ảnh train), KD, pruned | Hoàng, Mạnh | Mỗi variant là một version trong MLflow `resnet18-cifar10` và hiện trên Portal (mục 5) |
| 2 | Release rồi Benchmark từng version trên Portal (khoảng 25 phút mỗi version, chạy lần lượt, báo nhóm trước) | Người đăng ký; Nam kiểm kết quả | `metadata.json` có `status: completed`, `failed_points: []` |
| 3 | Bảng so sánh và biểu đồ cho report: accuracy vs throughput, latency vs concurrency, VRAM vs throughput | Nam | Mỗi con số truy về được một MLflow run |
| 4 | Đưa code train/KD/pruning/PTQ lên Git, kèm link checkpoint và sha256 | Hoàng, Mạnh | PR merge, CI xanh |
| 5 | Chốt repo nộp, merge PR #2, viết README tổng và bảng phiên bản | Tuấn | README dẫn được người mới chạy từ đầu |
| 6 | Repo serving: đổi tên metric DCGM sang tên mới (`DCGM_FI_DEV_GPU_UTIL` → `…_GPU_UTIL_RATIO`, `FB_USED` → `…_FB_USED_BYTES`); xử lý PR #11; Nam nhận lời mời vào Apollo (đã gửi lại 23/11) | Nam | Truy vấn GPU trong repo serving trả về dữ liệu |
| 7 | Ghi các điểm ↔ ở mục 3.5 vào Decision Log | Tuấn | Handbook khớp với thực tế |
| 8 | Một người không viết code chạy lại end-to-end theo README, có biên bản (REPRO-04) | Mạnh (tester) | Có biên bản, lỗi gặp phải đã sửa |
| 9 | Report và slides | Cả nhóm | DEL-05, DEL-06 |
| 10 | Tag `v1.0` | Tuấn | Tag trỏ tới commit đã qua biên bản chạy lại |
| 11 | Gỡ hạ tầng | Tuấn | Theo `infra/TEARDOWN.md`, chỉ sau nghiệm thu |

## 5. Tự kiểm tra một Model version từ đầu đến cuối

Dành cho Hoàng và Mạnh khi đưa variant thật lên. Lệnh đầy đủ ở [`platform-workflow.md` mục 1](platform-workflow.md#1-đăng-ký-model-version-hoàng-mạnh).

| Bước | Làm gì | Đúng khi |
| --- | --- | --- |
| 1 | Trong repo serving: `uv run serving validate --model-version <thư mục>` | Không có lỗi contract. Thư mục có `model.onnx` và `meta.yaml` (thêm `calibration.cache` nếu là INT8) |
| 2 | Trong repo Apollo: `python scripts/register_model_version.py <thư mục>`, với biến môi trường MLflow là tài khoản của bạn | In ra `registered resnet18-cifar10 version N` |
| 3 | Mở Portal | Có thẻ version N với đúng `variant`, `target_precision`, Top-1 offline |
| 4 | Bấm **Release** | Checklist chạy hết, run xanh. Thẻ **Triton đang phục vụ** hiện tag mới (`model_version_id`). Release không tự bật Triton, nên trạng thái có thể vẫn là *Đang tắt*. Lần đầu có thể mất 10–30 phút vì phải build Engine |
| 5 | Bấm **Benchmark** (Benchmark tự release nếu bạn bỏ qua bước 4) | Trong lúc chạy, trạng thái chuyển sang *Đang phục vụ*. Run xanh trên GitHub Actions. Thẻ version có số ảnh/s và dòng "Triton khớp offline" |
| 6 | Mở MLflow → experiment `benchmark` → run mới nhất | `status: completed`, `failed_points: []`, đủ 12 điểm, CV < 5 % |
| 7 | Mở mục **So sánh các version** trên Portal | Version mới nằm cạnh các version khác trong bảng và biểu đồ |

Bước 5 báo đỏ thì gửi link run GitHub Actions cho Tuấn và Nam. Log của Job còn giữ trên Loki và trong artifact của run.

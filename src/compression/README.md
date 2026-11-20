# src/compression/ (Mạnh)

- Teacher ResNet-50 → student ResNet-18 (knowledge distillation)
- Structured pruning + fine-tune (phải có bằng chứng kiến trúc thực sự nhỏ hơn)
- PTQ INT8 với `calibration.npy` (chỉ lấy từ tập train)
- Script build TensorRT Engine bằng **TensorRT 26.09** (khớp Triton 26.09), build trên **đúng GPU L4** sẽ chạy

Mỗi biến thể: log lên MLflow (experiment theo Variant), đánh giá cả accuracy tổng và accuracy từng lớp, giao Model version theo hợp đồng `meta.yaml`.

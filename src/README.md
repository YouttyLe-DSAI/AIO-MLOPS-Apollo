# src/

Code Python của dự án. Mỗi phần một package riêng, có owner riêng:

| Thư mục | Nội dung | Owner |
| --- | --- | --- |
| `baseline/` | Data pipeline CIFAR-10 (split cố định, seed 42), train/eval ResNet-18, export ONNX | Hoàng |
| `compression/` | Teacher ResNet-50, knowledge distillation, structured pruning, PTQ INT8, script build TensorRT Engine | Mạnh |
| `serving/` | Model repository Triton, Smoke test, Benchmark matrix, report | Nam (ghép từ repo `aio-mlops-serving`) |

Interface giữa các phần, **đã thống nhất, không đổi nếu chưa báo cả nhóm**:

- Input model: tensor float32 `[N, 3, 32, 32]` **đã chuẩn hoá** `(x/255 − mean) / std`, với mean `[0.4914, 0.4822, 0.4465]` và std `[0.2470, 0.2435, 0.2616]`.
- ONNX: input tên `input`, output tên `logits` `[N, 10]`, batch động, opset 17.
- Calibration INT8 chỉ lấy từ tập **train** (1.000 ảnh, 100 ảnh mỗi lớp), không bao giờ lấy từ test.

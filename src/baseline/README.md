# src/baseline/ (Hoàng)

Data pipeline + baseline ResNet-18 (CIFAR stem, train từ đầu, FP32): split 45k/5k/10k stratified (seed 42), train 100 epoch, chọn checkpoint theo validation, đánh giá trên test, export ONNX và kiểm tra parity với PyTorch.

Kết quả hiện tại: **94.85% top-1** trên 10k ảnh test (ONNX Runtime cho kết quả y hệt).

> Code đang nằm trong gói bàn giao `baseline-handoff-rtx4090.tar.gz`, sẽ đưa vào đây qua PR.

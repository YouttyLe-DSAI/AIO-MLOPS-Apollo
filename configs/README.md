# configs/

File YAML cấu hình, mỗi lần chạy đọc một file. Mọi khác biệt giữa môi trường (CPU/GPU, local/cloud) nằm ở config, không nằm trong code.

Đặt tên: `<phần>.<môi trường hoặc mục đích>.yaml`, ví dụ `baseline.yaml` (profile RTX 4090), `distillation.yaml`, `serving.gpu.yaml`.

Không đặt mật khẩu hay token trong config. Dùng biến môi trường.

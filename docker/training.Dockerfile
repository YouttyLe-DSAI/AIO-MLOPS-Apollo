FROM python:3.12.3-slim
WORKDIR /workspace
COPY requirements-training.txt .
RUN pip install --no-cache-dir -r requirements-training.txt
COPY src src
COPY configs configs
COPY scripts scripts
ENV CUBLAS_WORKSPACE_CONFIG=:4096:8 OMP_NUM_THREADS=4
ENTRYPOINT ["python", "-m", "src.baseline.train"]
CMD ["--config", "configs/baseline.yaml"]

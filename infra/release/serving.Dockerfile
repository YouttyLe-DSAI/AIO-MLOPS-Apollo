# Serving image: Triton 2.73.0 (NGC 26.09) with one Model version's repository baked in.
# Same layout as aio-mlops-serving/docker/serving.Dockerfile. Tag = Model version (ADR-0001).
# The Engine (model.plan) was built by build_engine.py with TensorRT 26.09 on an L4.
FROM nvcr.io/nvidia/tritonserver:26.09-py3

ENV MODEL_NAME=cifar10_classifier
COPY cifar10_classifier/ /models/cifar10_classifier/
COPY model_version.json /models/model_version.json

# 8000 HTTP, 8001 gRPC, 8002 metrics (Prometheus; scraped by Alloy)
EXPOSE 8000 8001 8002

CMD ["sh", "-c", "exec tritonserver --model-repository=/models --model-control-mode=explicit --load-model=${MODEL_NAME}"]

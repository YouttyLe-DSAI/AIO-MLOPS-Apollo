#!/usr/bin/env bash
# Benchmark one MLflow Model version against the Triton that currently serves it.
#
#   infra/benchmark/run_benchmark.sh <registered_model> <version>
#
# Triton must serve (or be about to serve) that Model version: the Job waits up to 25 min for the
# model to be ready. .github/workflows/benchmark.yml pins the tag and replicas in GitOps first.
# Needs kubectl access to the `release` namespace. Writes mlflow_run=<id> to $GITHUB_OUTPUT and the
# result table to $GITHUB_STEP_SUMMARY when set.
set -euo pipefail

MODEL_NAME=${1:?usage: $0 <registered_model> <version>}
MODEL_VERSION=${2:?usage: $0 <registered_model> <version>}
NAMESPACE=${RELEASE_NAMESPACE:-release}
TIMEOUT_S=${BENCHMARK_TIMEOUT_S:-5400}
TRITON_IMAGE=${TRITON_IMAGE:-}
HERE=$(cd "$(dirname "$0")" && pwd)

SLUG=$(printf '%s-v%s' "$MODEL_NAME" "$MODEL_VERSION" | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9-' '-' | cut -c1-38)
JOB_NAME="benchmark-${SLUG%-}-$(date +%s)"
export JOB_NAME NAMESPACE MODEL_NAME MODEL_VERSION TRITON_IMAGE

echo "::group::Benchmark ${MODEL_NAME} v${MODEL_VERSION} (Job ${JOB_NAME})"
kubectl -n "$NAMESPACE" create configmap benchmark-scripts \
  --from-file="$HERE/benchmark.py" --from-file="$HERE/benchmark.yaml" \
  --dry-run=client -o yaml | kubectl -n "$NAMESPACE" apply -f -
python3 - "$HERE/benchmark-job.yaml" <<'EOF' | kubectl -n "$NAMESPACE" apply -f -
import os, string, sys
keys = ("JOB_NAME", "NAMESPACE", "MODEL_NAME", "MODEL_VERSION", "TRITON_IMAGE")
print(string.Template(open(sys.argv[1]).read()).safe_substitute({k: os.environ[k] for k in keys}))
EOF
echo "::endgroup::"

# Stream the Job's own progress lines (one per Benchmark run) until it finishes.
deadline=$(( $(date +%s) + TIMEOUT_S ))
until kubectl -n "$NAMESPACE" logs "job/$JOB_NAME" --tail=1 >/dev/null 2>&1; do
  [ "$(date +%s)" -ge "$deadline" ] && { echo "::error::benchmark pod never started"; exit 1; }
  sleep 10
done
kubectl -n "$NAMESPACE" logs -f "job/$JOB_NAME" --pod-running-timeout=20m | tee /tmp/"$JOB_NAME".log || true

while :; do
  succeeded=$(kubectl -n "$NAMESPACE" get job "$JOB_NAME" -o jsonpath='{.status.succeeded}')
  failed=$(kubectl -n "$NAMESPACE" get job "$JOB_NAME" -o jsonpath='{.status.failed}')
  [ "${succeeded:-0}" -ge 1 ] || [ "${failed:-0}" -ge 1 ] && break
  [ "$(date +%s)" -ge "$deadline" ] && break
  sleep 10
done

LOG=$(kubectl -n "$NAMESPACE" logs "job/$JOB_NAME" 2>/dev/null || cat /tmp/"$JOB_NAME".log)
SUMMARY=$(printf '%s\n' "$LOG" | sed -n '/^----- SUMMARY -----$/,/^----- END SUMMARY -----$/p' | sed '1d;$d')
RUN=$(printf '%s\n' "$LOG" | sed -n 's/^BENCHMARK_MLFLOW_RUN=//p' | tail -1)
[ -n "${GITHUB_STEP_SUMMARY:-}" ] && [ -n "$SUMMARY" ] && printf '%s\n\nMLflow run: `%s` (experiment `benchmark`)\n' "$SUMMARY" "$RUN" >> "$GITHUB_STEP_SUMMARY"
[ -n "${GITHUB_OUTPUT:-}" ] && echo "mlflow_run=${RUN}" >> "$GITHUB_OUTPUT"

if [ "${succeeded:-0}" -ge 1 ]; then
  echo "Benchmark done. MLflow run ${RUN} (experiment benchmark)."
else
  kubectl -n "$NAMESPACE" describe job "$JOB_NAME" | tail -15
  echo "::error::benchmark Job ${JOB_NAME} failed or timed out"; exit 1
fi

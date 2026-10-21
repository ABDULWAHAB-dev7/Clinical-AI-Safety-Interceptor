# NLI inference service: the approved checkpoint behind an HTTP API.
#
# The checkpoint is mounted, not baked in. It is about 6 GB, and the lock file (models/nli/MODEL.lock) is what
# proves which weights are in use, so the image stays small and the weights stay auditable.
#
#   docker build -f nli_service/Dockerfile -t clinical-nli .
#   docker run --rm -p 8100:8100 \
#     -e NLI_MODEL_PATH=/models/nli/cross-encoder-nli-deberta-v3-base \
#     -e NLI_MODEL_LOCK=/models/nli/MODEL.lock \
#     -v /path/to/models:/models:ro \
#     clinical-nli
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    OMP_NUM_THREADS=4 \
    MKL_NUM_THREADS=4

WORKDIR /app

# libgomp1 is what torch needs for its OpenMP kernels.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-nli.txt ./
RUN pip install -r requirements-nli.txt

COPY src ./src
COPY nli_service ./nli_service

RUN useradd --create-home --uid 10002 nli && chown -R nli /app
USER nli

ENV PYTHONPATH=/app/src
EXPOSE 8100

# One worker: the model is loaded once per worker, and the checkpoint does not fit twice in memory.
CMD ["python", "-m", "uvicorn", "nli_service.app:app", "--host", "0.0.0.0", "--port", "8100", "--workers", "1"]

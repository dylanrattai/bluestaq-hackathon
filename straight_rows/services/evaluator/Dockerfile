# Straight Rows evaluator. Build context: services/evaluator
#   docker build -t straight-rows ./services/evaluator
# Multi-arch base (amd64 laptop, arm64 Jetson Orin Nano). No CUDA in v1 (NumPy path only).
# python:3.12-slim matches the repository's pinned .python-version (3.12); the code also runs on 3.11.
FROM python:3.12-slim
ENV FORCE_COLOR=1 PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY . /app
# the starter-kit validator; the run commands may also mount a newer copy at /submission
RUN mkdir -p /submission && cp /app/submission/validate_submission.py /submission/validate_submission.py
VOLUME ["/data", "/out"]
EXPOSE 8000
# default: paced live replay with the API on :8000. Batch:
#   docker run --rm -v "$PWD/data:/data:ro" -v "$PWD/out:/out" straight-rows python main.py batch --data /data --out /out
CMD ["python", "main.py", "live", "--data", "/data", "--out", "/out", "--speed", "60", "--port", "8000"]

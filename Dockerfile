# Backend image (API + live replay engine). Build from the repo root:
#   docker build -t cloudopt-api .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*

COPY requirements/ requirements/
RUN pip install -r requirements/backend.txt

COPY pyproject.toml ./
COPY cloudopt/ cloudopt/
RUN pip install --no-deps -e .

COPY backend/ backend/
COPY ml/artifacts/demo/ ml/artifacts/demo/
COPY reports/*.json reports/

WORKDIR /app/backend
EXPOSE 8000
# One worker only: the replay engine must not run twice against the same database.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --proxy-headers"]

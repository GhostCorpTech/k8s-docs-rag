FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=5

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.lock ./
RUN pip install -r requirements.lock
COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-deps .

RUN mkdir -p /app/data /app/eval/results /app/.tmp
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

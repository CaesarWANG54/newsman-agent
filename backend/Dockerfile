FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app/backend
COPY requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
RUN apt-get update \
    && apt-get install -y --no-install-recommends libatomic1 \
    && playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml ./
COPY newsman ./newsman
RUN pip install --no-cache-dir --no-deps -e . \
    && useradd --create-home newsman \
    && mkdir -p /app/digests /app/state \
    && chown -R newsman:newsman /app

USER newsman
CMD ["uvicorn", "newsman.main:app", "--host", "0.0.0.0", "--port", "8000"]

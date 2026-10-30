# Sandbox image: runs TaskFlow tests and replay probes for proposed patches.
# Containers from this image get no internet (internal docker network) and no secrets.
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir \
    "fastapi>=0.115" "uvicorn>=0.30" "sqlalchemy>=2.0" "psycopg[binary]>=3.2" \
    "pydantic-settings>=2.4" "prometheus-client>=0.20" "pyjwt>=2.8" "httpx>=0.27" \
    "pyyaml>=6.0" "pytest>=8" "bandit>=1.9"

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/work
WORKDIR /work
CMD ["sleep", "infinity"]

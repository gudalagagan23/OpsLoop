# One image for every Python service (OpsLoop and the simulated TaskFlow); compose picks the command.
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

COPY pyproject.toml alembic.ini ./
COPY opsloop ./opsloop
COPY taskflow ./taskflow
RUN pip install --no-cache-dir .

RUN useradd --create-home app
USER app

CMD ["uvicorn", "opsloop.api.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

COPY pyproject.toml ./
COPY opsloop ./opsloop
RUN pip install --no-cache-dir .

RUN useradd --create-home opsloop
USER opsloop

EXPOSE 8000
CMD ["uvicorn", "opsloop.api.main:app", "--host", "0.0.0.0", "--port", "8000"]

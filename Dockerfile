FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
COPY src/ src/

RUN pip install --no-cache-dir ".[all]"

COPY config.example.yaml config.yaml
COPY .env.example .env

CMD ["schedulebot", "run"]

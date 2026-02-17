FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src/ src/

RUN pip install --no-cache-dir hatchling && pip install --no-cache-dir ".[deploy]"

COPY config.yaml .

CMD ["schedulebot", "run", "-v"]

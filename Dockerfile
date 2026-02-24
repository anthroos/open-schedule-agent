FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl gosu && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src/ src/

RUN pip install --no-cache-dir hatchling && pip install --no-cache-dir ".[deploy]"

COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

RUN groupadd -r schedulebot && useradd -r -g schedulebot -d /app -s /sbin/nologin schedulebot \
    && mkdir -p /app/data && chown -R schedulebot:schedulebot /app
# USER is set in entrypoint after fixing volume permissions

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
  CMD curl -f http://localhost:${PORT:-8080}/api/health || exit 1

ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["schedulebot", "run", "-v"]

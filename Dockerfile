# News Harness V1 — Docker image
# Zero dependencies beyond Python 3.12 stdlib
FROM python:3.12-alpine

WORKDIR /app

# Copy application code
COPY news_harness/ ./news_harness/
COPY pyproject.toml .
COPY configs/ ./configs/
COPY fixtures/ ./fixtures/
COPY schemas/ ./schemas/
COPY web/ ./web/
COPY scripts/docker_entrypoint.sh ./docker_entrypoint.sh

RUN chmod +x docker_entrypoint.sh

# No pip install needed — pure stdlib

EXPOSE 8765

ENTRYPOINT ["./docker_entrypoint.sh"]

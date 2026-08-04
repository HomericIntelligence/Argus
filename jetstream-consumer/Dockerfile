# Pinned to stable 3.13-slim; see tests/test_dockerfile_constraints.py for guard
FROM python:3.13-slim AS base

WORKDIR /app

# Install pinned runtime deps at build time (was previously a runtime
# `pip install` baked into docker-compose `command:` — see #171). Baking
# them into the image makes startup fast, deterministic, and air-gap
# compatible.
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

FROM base AS final

# Create a dedicated non-root group and user with fixed UID/GID 1000,
# mirroring exporter/Dockerfile.
RUN groupadd -g 1000 consumer \
    && useradd -u 1000 -g consumer --no-create-home --shell /sbin/nologin consumer

COPY --chown=consumer:consumer consumer.py /app/consumer.py

USER consumer

EXPOSE 9101

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:9101/health', timeout=4)"

CMD ["python3", "/app/consumer.py"]

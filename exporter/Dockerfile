# Base-image minor must stay within the CI validation set (.github/workflows/ci.yml
# setup-python version) and the pixi dev/test env; enforced by
# tests/test_dockerfile_constraints.py
FROM python:3.13-slim AS base

# Install no extra packages — exporter uses stdlib only
WORKDIR /app

FROM base AS final

# Create a dedicated non-root group and user with fixed UID/GID 1000
RUN groupadd -g 1000 exporter \
    && useradd -u 1000 -g exporter --no-create-home --shell /sbin/nologin exporter

COPY --chown=exporter:exporter exporter.py /app/exporter.py

# Run as non-root
USER exporter

EXPOSE 9100

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:9100/health', timeout=4)"

CMD ["python3", "/app/exporter.py"]

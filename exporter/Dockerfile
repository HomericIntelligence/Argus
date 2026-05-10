# Pinned to stable 3.12-slim; see tests/test_dockerfile_constraints.py for guard
FROM python:3.14-slim AS base

# Install no extra packages — exporter uses stdlib only
WORKDIR /app

FROM base AS final

# Run as non-root
RUN adduser --disabled-password --no-create-home exporter
USER exporter

COPY --chown=exporter:exporter exporter.py /app/exporter.py

EXPOSE 9100

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:9100/health', timeout=4)"

CMD ["python3", "/app/exporter.py"]

# Pinned to stable 3.12-slim; see tests/test_dockerfile_constraints.py for guard
FROM python:3.12-slim
RUN groupadd -r exporter && useradd -r -g exporter -u 1000 -m -s /bin/sh exporter
COPY --chown=exporter:exporter exporter.py /exporter.py
USER exporter
CMD ["python", "/exporter.py"]

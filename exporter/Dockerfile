FROM python:3.14.0-slim@sha256:0aecac02dc3d4c5dbb024b753af084cafe41f5416e02193f1ce345d671ec966e AS builder
WORKDIR /build
COPY exporter.py .

FROM python:3.14.0-slim@sha256:0aecac02dc3d4c5dbb024b753af084cafe41f5416e02193f1ce345d671ec966e AS runtime
ENV EXPORTER_VERSION=0.1.0
COPY --from=builder /build/exporter.py /exporter.py
CMD ["python", "/exporter.py"]

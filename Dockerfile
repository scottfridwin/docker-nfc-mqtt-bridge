# Bookworm, not trixie: trixie's libpcsclite 2.x cannot talk to pcscd 1.9 hosts (e.g. Raspberry Pi OS bullseye)
# Build stage: compile pyscard (needs swig and the PC/SC headers) into wheels
FROM python:3.14-slim-bookworm@sha256:48b13b003dda20b16f9442b8475aa05fe21bf6579a8c881db92ffb4d8fd20f83 AS build
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libc6-dev libpcsclite-dev swig \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /tmp/
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r /tmp/requirements.txt

# Runtime stage: only the PC/SC client library and the wheels
FROM python:3.14-slim-bookworm@sha256:48b13b003dda20b16f9442b8475aa05fe21bf6579a8c881db92ffb4d8fd20f83 AS runtime
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpcsclite1 \
    && rm -rf /var/lib/apt/lists/*
# pip is not needed at runtime and vendors its own (scanner-flagged) libraries
RUN --mount=type=bind,from=build,source=/wheels,target=/wheels \
    pip install --no-cache-dir --no-index /wheels/*.whl \
    && python -m pip uninstall --yes pip
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY nfc_reader.py healthcheck.py start.sh ./
RUN useradd --system --uid 1000 --no-create-home nfc
USER 1000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD ["python", "/app/healthcheck.py"]
CMD ["./start.sh"]

# Test stage: CI builds this target on every platform before publishing
FROM runtime AS test
COPY tests/ tests/
RUN python -m unittest discover -s tests -v

FROM runtime

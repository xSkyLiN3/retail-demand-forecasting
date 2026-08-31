# syntax=docker/dockerfile:1.7@sha256:a57df69d0ea827fb7266491f2813635de6f17269be881f696fbfdf2d83dda33e

FROM python:3.12.13-slim@sha256:229a2c5bfa27522db7815ea81f9bed70af17ccb9de9fc7ad142b1877b5830d36 AS builder

ARG PROJECT_EXTRAS="api,postgres"

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    VIRTUAL_ENV=/opt/venv

WORKDIR /build

RUN python -m venv "$VIRTUAL_ENV"
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

COPY pyproject.toml README.md LICENSE ./
COPY requirements ./requirements
COPY src ./src

RUN python -m pip install --upgrade "pip==26.2.1" \
    && python -m pip install \
        --constraint requirements/constraints-py312.txt \
        ".[${PROJECT_EXTRAS}]" \
    && python -m pip check \
    && python -m pip uninstall --yes pip setuptools \
    && find "$VIRTUAL_ENV" -type d -name __pycache__ -prune -exec rm -rf {} +


FROM python:3.12.13-slim@sha256:229a2c5bfa27522db7815ea81f9bed70af17ccb9de9fc7ad142b1877b5830d36 AS runtime

ARG APP_VERSION="1.0.1"

LABEL org.opencontainers.image.title="Retail Demand Forecasting Demo" \
      org.opencontainers.image.description="Read-only historical ML evaluation replay" \
      org.opencontainers.image.source="https://github.com/xSkyLiN3/retail-demand-forecasting" \
      org.opencontainers.image.version="${APP_VERSION}"

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN python -m pip uninstall --yes pip setuptools \
    && rm -rf /root/.cache/pip \
    && useradd --no-create-home --uid 10001 --shell /usr/sbin/nologin appuser

COPY --from=builder /opt/venv /opt/venv
COPY --chown=10001:10001 demo ./demo

USER 10001:10001
EXPOSE 8000
STOPSIGNAL SIGTERM

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"

CMD ["retail-forecast-api"]

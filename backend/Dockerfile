FROM python:3.14.0-slim AS codex-binary
COPY scripts/install-codex.py /tmp/install-codex.py
RUN python /tmp/install-codex.py && codex --version

FROM python:3.14.0-slim
WORKDIR /app
COPY backend/requirements.lock.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/requirements.lock.txt backend/requirements-mcp.lock.txt ./
RUN pip install --no-cache-dir -r requirements-mcp.lock.txt
COPY --from=codex-binary /usr/local/bin/codex /usr/local/bin/codex
COPY deploy/codex.config.toml /home/app/.codex/config.toml
COPY backend/app ./app
RUN useradd --uid 10001 --create-home app && mkdir -p /data/files /home/app/.codex \
    && chown -R app:app /data /home/app && chmod 700 /home/app/.codex && chmod 600 /home/app/.codex/config.toml
ENV HOME=/home/app CODEX_HOME=/home/app/.codex
USER app
CMD ["sh", "-c", "python -m app.migrate && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
ARG HWP_REVISION=development
LABEL org.opencontainers.image.revision=${HWP_REVISION}

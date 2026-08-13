FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY src ./src
COPY alembic.ini ./
COPY alembic ./alembic
COPY scripts ./scripts

RUN pip install --no-cache-dir uv \
    && uv sync --frozen --no-dev \
    && rm -rf /root/.cache

EXPOSE 8000

CMD ["sh", "-c", "exec uvicorn agent_system.main:app --host 0.0.0.0 --port ${PORT:-8000}"]

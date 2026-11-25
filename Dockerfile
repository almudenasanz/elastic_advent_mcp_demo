FROM python:3.13-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    UV_CACHE_DIR=/tmp/uv-cache

RUN pip install --no-cache-dir uv==0.12.7
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable \
    && useradd --create-home --uid 10001 santa \
    && chown -R santa:santa /app

USER santa
EXPOSE 8000
ENTRYPOINT ["opentelemetry-instrument"]
CMD ["santa-mcp", "--host", "0.0.0.0"]

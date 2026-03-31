# All dependencies have pre-built wheels — no compiler needed
FROM python:3.13-slim

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Install dependencies (cached layer — only re-runs when pyproject.toml/uv.lock change)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy source and install project
COPY . .
RUN uv sync --frozen --no-dev

ENV PYTHONUNBUFFERED=1
EXPOSE 8080

CMD ["sh", "-c", "uv run --quiet server.py --serve --token-path /tmp/token.json"]

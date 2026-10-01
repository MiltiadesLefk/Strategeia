FROM python:3.14-slim

WORKDIR /app

# Claude Code CLI, for the optional `claude_code_cli` LLM provider
# (llm_providers/claude_code_cli_provider.py resolves it via
# shutil.which("claude")). Installed before the app layers so a
# requirements/code change doesn't re-download it; rebuild with --no-cache
# to pick up a newer CLI. Auth comes from CLAUDE_CODE_OAUTH_TOKEN at runtime
# (see docker-compose.yml) — nothing credential-shaped is baked in here.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && curl -fsSL https://claude.ai/install.sh | bash \
    && rm -rf /var/lib/apt/lists/*
ENV PATH="/root/.local/bin:${PATH}" \
    DISABLE_AUTOUPDATER=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY data ./data

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

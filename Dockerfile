# Mission-Aligned Careers image: the `radar` CLI, Dagster and the API (no dev tools).
# Build: docker build -t mission-aligned-careers .     Run the demo: docker run --rm mission-aligned-careers demo

FROM python:3.13-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# Dependencies first (cached layer), then the project itself.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-default-groups --group orchestration --group api --no-install-project
COPY src ./src
RUN uv sync --frozen --no-default-groups --group orchestration --group api --no-editable

FROM python:3.13-slim
RUN useradd --create-home --uid 1000 radar
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY packs ./packs
COPY demo ./demo
COPY config/criteria.example.yaml config/sources.example.yaml ./config/
RUN mkdir -p data demo-run dagster-home && chown -R radar:radar /app
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1
USER radar
ENTRYPOINT ["radar"]
CMD ["--help"]

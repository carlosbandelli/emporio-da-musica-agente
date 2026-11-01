# syntax=docker/dockerfile:1

# Imagem da aplicação Empório da Música.
# Estágios: builder (instala dependências com uv) -> runtime (slim, usuário não-root).
# Build local:   docker build --target runtime -t emporio:local .
# Os alvos do compose e da pipeline usam o estágio "runtime".

ARG PYTHON_IMAGE=python:3.12-slim-bookworm
# Fixar a versão do uv (e, em produção, o digest da imagem base) para builds reprodutíveis.
ARG UV_VERSION=0.8

# Estágio auxiliar: binários do uv. COPY --from não aceita variável, então o tag entra num FROM.
FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

# ---------------------------------------------------------------------------
# Estágio 1: builder — resolve e instala as dependências a partir do uv.lock
# ---------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS builder

COPY --from=uv /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Camada de dependências: só muda quando pyproject.toml ou uv.lock mudam.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

# Código-fonte e dados: mudanças aqui não invalidam a camada de dependências.
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

# ---------------------------------------------------------------------------
# Estágio 2: runtime — só o necessário para executar
# ---------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS runtime

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONFAULTHANDLER=1 \
    TENANTS_CONFIG=config/tenants.yaml \
    LOG_LEVEL=INFO

# Usuário sem privilégios com UID/GID fixos (o Kubernetes valida runAsNonRoot pelo número).
RUN groupadd --system --gid 10001 emporio \
 && useradd --system --uid 10001 --gid emporio --no-create-home --home-dir /app --shell /usr/sbin/nologin emporio

WORKDIR /app

# O venv é copiado já pronto (sem compiladores, sem uv). O código fica instalado no venv.
COPY --from=builder /app/.venv /app/.venv
# Configuração por tenant e dados (CSVs e manual de políticas) usados pelo seed e pelo agente.
COPY --from=builder /app/config /app/config
COPY --from=builder /app/data /app/data

USER 10001:10001

EXPOSE 8000

# Liveness via o próprio Python (a imagem slim não tem curl). Falha se /health não responder 2xx.
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"]

# Um worker por contêiner: o escalonamento é horizontal (réplicas), não por processos.
# --forwarded-allow-ips "*": só o Traefik alcança a porta 8000 (sem ports publicados), então o
# X-Forwarded-For/Proto do proxy é confiável dentro da rede interna.
CMD ["uvicorn", "emporio.adaptadores.api.app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]

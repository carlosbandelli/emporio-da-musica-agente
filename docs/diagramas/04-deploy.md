# 04 · Deploy, observabilidade e CI/CD

*EN: Deployment: Traefik, app replicas, Postgres and Redis, migrate job, Prometheus scraping /metrics, optional n8n, CI/CD pipeline and the Kubernetes example.*

Base: [`docs/deploy.md`](../deploy.md), `docker-compose.yml`, `.github/workflows/ci-cd.yml`, `deploy/k8s/`,
`deploy/observabilidade/prometheus/prometheus.yml`. Este diagrama mostra só o que existe no repositório.
**Grafana e Sentry não aparecem:** ficaram para o futuro (ver `docker-compose.yml`, comentário do topo).

## 4.1 Docker Compose (conjunto essencial e perfil `observabilidade`)

```mermaid
flowchart LR
    CLIENTE["Cliente ou integração<br/>http://localhost (única porta publicada)"]

    subgraph BORDA ["rede borda (com porta publicada e saída para os LLMs)"]
        TR["traefik v3.6<br/>:80 · rate limit por IP 20 req/s, rajada 60<br/>headers de segurança · compressão · basic auth no dashboard"]
        APP["app · 3 réplicas por padrão (APP_REPLICAS)<br/>uvicorn :8000 · stateless"]
    end

    subgraph DADOS ["rede dados (internal: sem saída para a internet)"]
        MIG["migrate (one-shot)<br/>python -m emporio.adaptadores.db.migrar<br/>advisory lock do Postgres"]
        PG[("postgres:16-alpine<br/>um schema por tenant")]
        RD[("redis:7-alpine<br/>AOF ligado: sessões e rate limit")]
        PROM["prometheus v3.5.0<br/>scrape a cada 15 s"]
    end

    subgraph OBS ["perfil observabilidade (opcional)"]
        OTEL["otel-collector<br/>traces para debug"]
        LOKI[("loki 3.5.0")]
        ALLOY["alloy v1.11.0<br/>coleta o stdout dos contêineres"]
    end

    N8N["n8n (opcional, fora do compose)<br/>webhook + AI Agent + HTTP Request"]
    LLMS["provedores de LLM<br/>Groq · Gemini · Claude"]

    CLIENTE -->|"HTTP :80, só /v1, /health e /ready"| TR
    TR -->|"PathPrefix /v1 e sondas"| APP
    TR -.->|"health ativo em /health"| APP
    MIG -->|"conclui antes da app (service_completed_successfully)"| APP
    MIG -->|"migrar e seed"| PG
    APP -->|"DATABASE_URL, schema do tenant"| PG
    APP -->|"REDIS_URL"| RD
    APP -->|"chamadas aos LLMs"| LLMS
    APP -.->|"MOTOR_ATENDIMENTO=n8n: webhook"| N8N
    N8N -->|"POST /v1/tools/{nome} direto na rede (N8N_URL_API)"| APP
    PROM -->|"scrape /metrics na porta 8000 (DNS do serviço app)"| APP
    PROM -->|"scrape traefik:8082 (métricas da borda)"| TR
    APP -.->|"OTLP, se OTEL_EXPORTER_OTLP_ENDPOINT estiver definido"| OTEL
    ALLOY -->|"logs JSON"| LOKI
```

- **Entrada única:** só o Traefik publica porta. `/metrics` **não** passa pela borda: o Prometheus raspa pela rede
  interna. O Traefik não roteia `/metrics` nem outros caminhos que não sejam `/v1`, `/health` e `/ready`.
- **Escala:** `APP_REPLICAS=5 docker compose up -d`. O Traefik descobre as réplicas pelo DNS do serviço e pelas
  labels. Não há sticky session.
- **Ordem de subida:** `postgres` fica saudável, `migrate` roda e termina com sucesso, e só então `app` sobe (ela
  também espera `postgres` e `redis` saudáveis).
- **Métricas da app (contrato, `docs/deploy.md` §6):** `emporio_http_requisicoes_total`, `emporio_llm_fallback_total`,
  `emporio_circuito_estado` e as demais métricas `emporio_*`, com o rótulo `tenant_id`. Sem `session_id` como rótulo.
- **Sem a `observabilidade`:** a app e o Traefik continuam funcionando. Só registram erros de conexão com o collector.

## 4.2 CI/CD (`.github/workflows/ci-cd.yml`) e Kubernetes de exemplo

```mermaid
flowchart LR
    GATILHO["push ou pull_request<br/>(branch main)"] --> LINT["lint<br/>ruff check · ruff format --check · lint-imports (C1 a C5)"]
    LINT --> TIPOS["tipos<br/>mypy src"]
    TIPOS --> TESTES["testes<br/>pytest, com service postgres:16"]
    TESTES --> BUILD["build e push para o GHCR<br/>só em push na main · tag = SHA · target runtime"]
    BUILD --> SCAN["scan<br/>trivy: vuln e secret, CRITICAL e HIGH<br/>só com DEPLOY_ENABLED=true"]
    SCAN --> DEPLOY["deploy<br/>environment producao (aprovação manual)<br/>só com DEPLOY_ENABLED=true"]
    DEPLOY --> JOB["Job emporio-migracao<br/>aplicado e espera a conclusão"]
    JOB --> ROLL["kubectl apply e rollout<br/>Deployment emporio-app"]

    subgraph K8S ["overlay deploy/k8s/overlays/producao (exemplo, sem cluster no repositório)"]
        ING["Ingress emporio<br/>ingressClassName traefik<br/>middlewares: ratelimit-ip, headers, compress"]
        SVC["Service emporio-app (ClusterIP :80, targetPort http)"]
        DEP["Deployment emporio-app<br/>réplicas iniciais 3"]
        HPA["HPA emporio-app<br/>3 a 10 réplicas · CPU 70% · memória 80%"]
        VPA["VPA emporio-app<br/>updateMode Off (só recomenda)"]
        PDB["PodDisruptionBudget emporio-app"]
        CM["ConfigMap emporio-config"]
        SEC["Secret emporio-segredos<br/>(exemplo, removido pelo overlay de produção)"]
        PGG[("Postgres gerenciado<br/>DATABASE_URL com TLS")]
        RDG[("Redis gerenciado<br/>REDIS_URL com TLS")]
    end

    ROLL --> DEP
    ING --> SVC --> DEP
    HPA -.-> DEP
    VPA -.-> DEP
    PDB -.-> DEP
    CM -.-> DEP
    SEC -.-> DEP
    DEP --> PGG
    DEP --> RDG
    JOB --> PGG
```

- **Gates:** lint, tipos e testes rodam em todo push e pull request. O build só roda em push na `main`. O scan e o
  deploy só rodam com `DEPLOY_ENABLED=true` (desligado por padrão). O deploy é um **exemplo**: não há cluster.
- **Migração antes do rollout:** o Job é imutável, então a pipeline o recria antes de aplicar os manifestos. Se a
  migração falhar, o deploy é abortado. Por isso as migrações precisam ser compatíveis com a versão anterior
  (expand e contract).
- **Traefik no Kubernetes:** é o Ingress Controller, instalado por Helm (instrução em `docs/deploy.md` §5). Não há
  chart no repositório.
- **Prometheus e Grafana:** não há manifesto de Prometheus no `deploy/k8s/`. O diagrama de Kubernetes não mostra nenhum.

## Componentes e onde existem

| Componente | Onde está |
|---|---|
| Traefik (borda, labels, rate limit, basic auth) | `docker-compose.yml` (serviço `traefik`), `deploy/traefik/usuarios.htpasswd` |
| `app` (3 réplicas por padrão, `APP_REPLICAS`) | `docker-compose.yml` (serviço `app`), `Dockerfile` (target `runtime`) |
| `migrate` (`python -m emporio.adaptadores.db.migrar`) | `docker-compose.yml` (serviço `migrate`), `src/emporio/adaptadores/db/migrar.py` |
| `postgres:16-alpine`, `redis:7-alpine` | `docker-compose.yml` |
| Prometheus, scrape de `app:8000` e `traefik:8082` | `docker-compose.yml` (serviço `prometheus`), `deploy/observabilidade/prometheus/prometheus.yml` |
| `/metrics` na app (`emporio_*`) | `src/emporio/adaptadores/api/app.py` (rota `/metrics`), `src/emporio/adaptadores/observabilidade/telemetria.py` |
| `/health`, `/ready` | `src/emporio/adaptadores/api/app.py` |
| n8n (`AgenteN8n`, `/v1/tools`) | `src/emporio/adaptadores/motor/n8n/agente.py`, `src/emporio/adaptadores/api/app.py` (`/v1/tools`), `deploy/n8n/workflow-atendimento.json`, `deploy/n8n/README.md` |
| Perfil `observabilidade`: `otel-collector`, `loki`, `alloy` | `docker-compose.yml`, `deploy/observabilidade/otel-collector/`, `loki/`, `alloy/` |
| Pipeline: lint, tipos, testes, build GHCR, scan trivy, deploy | `.github/workflows/ci-cd.yml` |
| Manifestos K8s (Ingress, Service, Deployment, HPA, VPA, PDB, ConfigMap, Secret de exemplo, Job) | `deploy/k8s/base/` e `deploy/k8s/overlays/producao/` |

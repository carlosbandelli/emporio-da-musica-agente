# Deploy

Como a aplicação é empacotada, executada, escalada e observada. As decisões de arquitetura estão em
[`arquitetura.md`](arquitetura.md) (§5 escala e resiliência, §6 borda, §7 observabilidade, §12 deploy).

## 1. Artefatos

| Caminho | O que é |
|---|---|
| `Dockerfile` | Build multi-stage: `builder` (uv, instala pelo `uv.lock`) e `runtime` (slim, usuário não-root UID 10001, HEALTHCHECK em `/health`). |
| `.dockerignore` | Exclui `.env`, `.mcp.json`, `.claude/`, `.git`, caches e o PDF do enunciado do contexto de build. |
| `docker-compose.yml` | Traefik, app em réplicas, migração (one-shot), Postgres 16, Redis 7 e, no perfil `observabilidade`, OTel Collector, Prometheus, Loki, Alloy e Grafana. |
| `deploy/traefik/usuarios.htpasswd` | Usuário de exemplo do basic auth (dashboard do Traefik e Grafana). |
| `deploy/observabilidade/` | Configs do Collector, Prometheus, Loki, Alloy e Grafana (datasources e dashboard `agente.json` provisionados). |
| `deploy/k8s/base/` | Namespace, ConfigMap, Secret de **exemplo**, Deployment, Service, Ingress (`networking.k8s.io/v1`, `ingressClassName: traefik`), middlewares Traefik, HPA, VPA (modo `Off`), PDB e Job de migração. |
| `deploy/k8s/overlays/producao/` | Remove o Secret de exemplo, fixa a imagem por SHA, ajusta recursos, TLS e faixa do HPA. |
| `.github/workflows/ci-cd.yml` | lint (ruff) → tipos (mypy) → testes (pytest com Postgres e Redis) → build/push no GHCR (só na `main`, tag por SHA) → scan (trivy) → deploy com environment `producao`. |

## 2. Rodar com Docker Compose

Pré-requisitos: Docker com o plugin `compose` (v2.24 ou superior).

```bash
cp .env.example .env          # preencha as chaves de LLM
docker compose up -d --build  # essencial: Traefik, app (3 réplicas), migração, Postgres, Redis
docker compose --profile observabilidade up -d --build   # inclui a observabilidade
docker compose logs -f app
docker compose down           # mantém os volumes; use "down -v" para apagar os dados
```

Entrada única: `http://localhost` (só o Traefik publica porta). Endereços:

| Endereço | Acesso |
|---|---|
| `http://localhost/v1/...` | API da loja (`/v1/chat`, `/v1/admin`, `/v1/tools`). |
| `http://localhost/traefik/dashboard/` | Dashboard do Traefik. Basic auth de exemplo: `admin` / `troque-esta-senha`. |
| `http://localhost/grafana/` | Grafana (perfil `observabilidade`). Basic auth do Traefik e login do Grafana (`GRAFANA_ADMIN_PASSWORD`, padrão `troque-esta-senha`). |

Trocar a senha do basic auth: `htpasswd -nB admin` gera o hash (ou um `bcrypt` equivalente), e o resultado vai para
`deploy/traefik/usuarios.htpasswd`. Não há sticky session, então cada requisição pode cair em qualquer réplica.

Como o tenant é resolvido pelo subdomínio, no local use o header `X-Tenant-ID` ou `curl --resolve` para testar outra loja.

Sem o perfil `observabilidade`, o Traefik e a app tentam exportar OTLP para `otel-collector` e registram erros de conexão.
Isso não derruba o serviço.

## 3. Escalar

**Horizontal (réplicas)**: a app é stateless. Sessão e rate limit ficam no Redis.

- Compose: `APP_REPLICAS=5 docker compose up -d` (ou `docker compose up -d --scale app=5`). O Traefik descobre as réplicas
  pelo DNS do serviço e pelas labels, sem reconfiguração.
- Kubernetes: o HPA (`hpa.yaml`) escala de 3 a 10 réplicas (12 em produção) por CPU (70%) e memória (80%). Requer metrics-server.
  Não defina `replicas` fixo em produção, pois o HPA é a fonte de verdade.

**Vertical (recursos por réplica)**: requests e limits de CPU e memória em `deployment.yaml` (compose: `deploy.resources`).

- O VPA (`vpa.yaml`) fica em `updateMode: "Off"`. Ele só recomenda valores (`kubectl -n emporio describe vpa emporio-app`).
- **Conflito VPA × HPA**: o HPA calcula utilização como uso ÷ request. Um VPA em `Auto` ou `Recreate` muda os requests e reinicia
  os pods, então os dois ficam brigando. A regra: o VPA não controla os recursos que o HPA usa como métrica. Os valores
  recomendados são aplicados manualmente, com revisão.

**Migração**: só uma réplica executa migrações por vez (advisory lock do Postgres). No compose, a app só sobe depois
de o serviço `migrate` terminar com sucesso. No Kubernetes, o Job roda antes do rollout (ver seção 5).

## 4. Papel do Traefik e divisão borda × app

| Ambiente | Papel do Traefik |
|---|---|
| Docker Compose | Load balancer e reverse proxy. Descobre as réplicas pelas labels e faz health check ativo em `/health`. |
| Kubernetes | Ingress Controller. Lê os `Ingress` padrão e os middlewares por annotation. |
| Ambos | API gateway: ponto único de entrada, TLS, middlewares e roteamento. |

Divisão (sem duplicar regras):

| Responsabilidade | Borda (Traefik) | App |
|---|---|---|
| TLS (Let's Encrypt, opcional) | sim | não |
| Rate limit por **IP** (20 req/s, rajada 60) | sim | não |
| Rate limit por sessão, usuário, tenant e provedor LLM | não | sim (Redis, token bucket) |
| Roteamento (`/v1` aberto; `/metrics`, `/health`, `/ready` fechados) | sim | não |
| Headers de segurança, compressão | sim | não |
| Validação de JWT, RBAC, resolução do tenant | não | sim |
| Retry | **não** (cada tentativa de chat custa uma chamada de LLM) | sim (com circuit breaker e fallback) |

Pendências de borda, não implementadas ainda: CORS (depende das origens do front) e IP allowlist opcional para `/v1/admin`.

## 5. Kubernetes

Pré-requisitos no cluster: Traefik como Ingress Controller, metrics-server e os CRDs do VPA. Exemplo de instalação do Traefik com Helm
(comando de referência, não há chart vendorizado no repositório):

```bash
helm repo add traefik https://traefik.github.io/charts
helm repo update
helm install traefik traefik/traefik --namespace traefik --create-namespace \
  --set providers.kubernetesIngress.enabled=true \
  --set providers.kubernetesCRD.enabled=true \
  --set ingressClass.enabled=true \
  --set ingressClass.name=traefik
```

Validar e aplicar:

```bash
kubectl kustomize deploy/k8s/overlays/producao          # renderiza sem cluster
kubectl apply -k deploy/k8s/overlays/producao           # após aprovação (a pipeline faz isso)
```

Postgres e Redis em produção são serviços gerenciados. As URLs entram pelo Secret (`DATABASE_URL`, `REDIS_URL`, com TLS). Não há
StatefulSet no repositório. O Secret real vem de External Secrets Operator ou Sealed Secrets; o de exemplo não vai para produção.

A pipeline aplica primeiro o Job de migração, espera a conclusão e só depois faz o rollout da Deployment. Por isso as migrações precisam
ser compatíveis com a versão anterior da app (expand/contract).

## 6. Contrato de métricas da aplicação

A app deve expor `/metrics` no formato Prometheus, na porta 8000. Essa rota **não** passa pelo Traefik nem pelo Ingress.
Os nomes abaixo são o contrato usado pelo dashboard `deploy/observabilidade/grafana/dashboards/agente.json`.

Regras: todo rótulo de tenant se chama `tenant_id` (arquitetura §7). Nunca use como rótulo `session_id`, ID de usuário ou
caminho bruto de URL (cardinalidade). Use o template da rota (ex.: `/v1/chat`).

| Métrica | Tipo | Rótulos | Descrição |
|---|---|---|---|
| `emporio_http_requisicoes_total` | counter | `tenant_id`, `rota`, `metodo`, `status` | Requisições HTTP da app. |
| `emporio_http_duracao_segundos` | histogram | `tenant_id`, `rota` | Latência total da requisição (p95 no dashboard). |
| `emporio_rate_limit_bloqueios_total` | counter | `tenant_id`, `camada` | 429 por camada da app: `sessao`, `usuario`, `tenant`, `llm`. |
| `emporio_roteamento_total` | counter | `tenant_id`, `nivel` | Mensagens por nível: `simples`, `media`, `complexa`. |
| `emporio_llm_requisicoes_total` | counter | `tenant_id`, `provedor`, `nivel`, `resultado` | Chamadas ao LLM. `resultado`: `ok`, `transitorio`, `permanente`, `circuito_aberto`. |
| `emporio_llm_duracao_segundos` | histogram | `provedor`, `nivel` | Latência de cada chamada ao LLM. |
| `emporio_llm_tokens_total` | counter | `tenant_id`, `provedor`, `tipo` | Tokens. `tipo`: `entrada`, `saida`, `cache_leitura`. |
| `emporio_llm_custo_usd_total` | counter | `tenant_id`, `provedor` | Custo estimado em USD, pela tabela de preços configurada. |
| `emporio_llm_fallback_total` | counter | `tenant_id`, `de`, `para` | Trocas de provedor pela cadeia de fallback. |
| `emporio_circuito_estado` | gauge | `provedor` | Circuit breaker: 0 = fechado, 1 = meio-aberto, 2 = aberto. |
| `emporio_prompt_cache_total` | counter | `tenant_id`, `provedor`, `resultado` | Cache do prompt (CAG). `resultado`: `hit`, `miss`. |
| `emporio_tool_duracao_segundos` | histogram | `tenant_id`, `tool` | Latência por tool. |
| `emporio_tool_chamadas_total` | counter | `tenant_id`, `tool`, `resultado` | Chamadas por tool e resultado. |

Métricas da borda (já expostas pelo Traefik e raspadas pelo Prometheus): `traefik_router_requests_total` (usada para o 429 da borda)
e `traefik_entrypoint_requests_total`.

Logs: JSON no stdout (coletados pelo Alloy, consultados no Loki). Telemetria sem conteúdo de mensagem nem dado pessoal
(LGPD, arquitetura §7).

## 7. Pendências e suposições

- **Traces**: o Collector manda traces para `debug`. Backend de traces (Sentry ou Tempo) fica em aberto.
- **TLS local**: roda em HTTP. Let's Encrypt está comentado no compose e no Traefik.
- **Dados no contexto**: `data/` entra na imagem (CSVs, incluindo `customers.csv` com dados pessoais). Em produção, o seed deve
  vir de uma fonte controlada, não da imagem.
- **Versões de imagens**: as tags de Traefik, Postgres, Redis, Prometheus, Loki, Alloy, Grafana e OTel Collector e as actions do
  GitHub precisam de revisão antes do uso real. Para builds reprodutíveis, fixe digests.
- **Docker socket**: Traefik e Alloy montam o socket do Docker em somente leitura. Em produção, use um socket-proxy.

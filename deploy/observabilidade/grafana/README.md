# Grafana (trabalho futuro)

Esta pasta guarda o dashboard (`dashboards/agente.json`) e o provisionamento do Grafana, mas o serviço **não** está no
`docker-compose.yml` desta entrega (decisão: sem Grafana e sem Sentry agora). Os arquivos ficam como ponto de partida.

Para que serviria: painéis sobre as métricas Prometheus (latência p95 por provedor, taxa de fallback/degradação,
circuito aberto, tokens e custo por tenant) e alertas. O Prometheus já sobe no compose e raspa `/metrics`.

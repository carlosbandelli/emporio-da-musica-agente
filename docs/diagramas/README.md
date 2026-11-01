# Diagramas

*EN: Diagrams of the Empório da Música agent. They are Mermaid blocks, rendered by GitHub.*

Os diagramas estão em Mermaid e o GitHub renderiza direto na página. Cada um tem uma legenda, uma lista de
componentes com o caminho do arquivo no código ou no deploy, e a origem da decisão em `docs/`.

- [01 · Arquitetura em runtime](01-arquitetura.md): monólito modular, portas, adaptadores e `composicao.py`, com as regras de dependência C1 a C5.
  *EN: runtime architecture, modular monolith, ports, adapters and the composition root, with import rules C1 to C5.*
- [02 · Sequência de uma mensagem](02-sequencia-mensagem.md): do cliente ao motor, roteador, LLM com fallback, tools, guarda de valores e resposta, com os caminhos de degradação.
  *EN: sequence of one message, from the client through the engine, router, LLM fallback, tools and value guard to the reply.*
- [03 · Modelo de dados (ERD)](03-erd.md): tabelas do schema por tenant, com colunas principais e relações.
  *EN: data model of the per-tenant schema, with main columns and relations.*
- [04 · Deploy, observabilidade e CI/CD](04-deploy.md): compose (Traefik, réplicas da app, Postgres, Redis, migração, Prometheus, n8n opcional), pipeline de CI e o overlay Kubernetes de exemplo.
  *EN: deployment with Compose, Prometheus, optional n8n, the CI/CD pipeline and the Kubernetes example.*

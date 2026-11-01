# config/

Configuração por tenant. O Dockerfile copia esta pasta para `/app/config`.

- `tenants.yaml`: um bloco por loja (`tenants.<id>`) com nome, schema, idiomas, persona, contatos, caminhos
  (`politicas_arquivo`, `dados_dir`), deadline por mensagem, limites de rate limit (token bucket por camada,
  arquitetura §5.1) e termos fora de escopo. Campos desconhecidos ou inválidos são rejeitados na inicialização, com o
  caminho do campo na mensagem (`emporio.limites.sessao.capacidade: ...`).
- `tenants/<id>/politicas.md`: manual de políticas em texto (criado na tarefa 1.7).

Modelo de validação: `emporio.portas.tenant.ConfigTenant`; carregador: `emporio.configuracao.carregar_tenants`.
Variáveis de ambiente (chaves de LLM, `MOTOR_ATENDIMENTO`, `DATA_REFERENCIA`, `TENANTS_CONFIG`) ficam em
`emporio.configuracao.Settings`, não aqui.

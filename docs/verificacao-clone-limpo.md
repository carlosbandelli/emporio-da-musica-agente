# Verificação de clone limpo (task 1.16)

Executada em 2026-11-01 a partir do GitHub, em `C:	mp\c16` (Windows 11, Git Bash, uv), no commit `7cdd143`.
Cada configuração recebeu **só uma** chave por variável de ambiente (nenhum arquivo de ambiente no clone) e
`DATA_REFERENCIA=2026-03-10T15:00:00-04:00`. Uma pergunta por configuração (mínimo de chamadas reais).

| Passo | Resultado |
|---|---|
| `git clone` + `uv sync` | ok |
| `uv run pytest` (sem chave) | 729 passed, 3 skipped |
| só `GOOGLE_API_KEY`: `diagnostico` + `chat` | ok; preço do Takamine GD20 correto (R$ 2.199,00, PIX R$ 2.089,05, 12x R$ 183,25) |
| só `GROQ_API_KEY`: `diagnostico` + `chat` | `diagnostico` ok; a pergunta com tool saiu **degradada** (limite de 8 mil tokens/min do plano grátis do Groq, premissa #67) |

Nenhum passo fora do README foi necessário. Conclusão: a chave recomendada para avaliar é `GOOGLE_API_KEY`;
só com Groq, perguntas que exigem consulta de dados podem não ser respondidas no plano grátis.

## Log: só Gemini

```text
$ uv run emporio diagnostico  (só GOOGLE_API_KEY; exit 0)
Diagnóstico do Empório da Música
- Provedores com chave: gemini
- Motor ativo: nativo
- Tenant: emporio (válido: sim)
- Dados: 65 produtos e 20 pedidos carregados
- Manual de políticas: encontrado
- Data de referência: 2026-03-10

Tudo certo: `emporio chat` está pronto para usar.

$ uv run emporio chat  (só GOOGLE_API_KEY; exit 0)
Empório da Música - atendimento com Sônia
Tenant: emporio | idioma: pt-BR | motor: nativo | provedores: gemini | data de referência: 2026-03-10
Digite 'sair' (ou Ctrl+C) para encerrar.

Você: Sônia: O Takamine GD20 Dreadnought Natural custa R$ 2.199,00. Se pagar com PIX, ele sai por R$ 2.089,05, aproveitando os 5% de desconto. Você também pode parcelar em até 12 vezes de R$ 183,25 sem juros. A promoção para este violão já terminou, mas ainda temos 5 unidades em estoque!

Posso te ajudar com mais alguma informação?

Você: Sônia: Até logo!
```

## Log: só Groq

```text
$ uv run emporio diagnostico  (só GROQ_API_KEY; exit 0)
Diagnóstico do Empório da Música
- Provedores com chave: groq
- Motor ativo: nativo
- Tenant: emporio (válido: sim)
- Dados: 65 produtos e 20 pedidos carregados
- Manual de políticas: encontrado
- Data de referência: 2026-03-10

Tudo certo: `emporio chat` está pronto para usar.

$ uv run emporio chat  (só GROQ_API_KEY; exit 0)
Empório da Música - atendimento com Sônia
Tenant: emporio | idioma: pt-BR | motor: nativo | provedores: groq | data de referência: 2026-03-10
Digite 'sair' (ou Ctrl+C) para encerrar.

Você: Sônia: Poxa, estou com uma instabilidade e não consigo responder agora. Tenta de novo daqui a pouquinho? Se preferir, fale com a nossa equipe pelo WhatsApp (67) 3321-4500.

Você: Sônia: Até logo!
motor nativo: sem provedor tenant=emporio erro=nenhum provedor respondeu (nível None): groq: ErroLLMTransitorio
```

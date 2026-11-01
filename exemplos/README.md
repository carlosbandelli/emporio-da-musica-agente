# Conversas de exemplo

Gravadas em 2026-11-01 com LLM real, pelo próprio CLI: `uv run emporio chat --gravar exemplos/<arquivo>.md`
(com `DATA_REFERENCIA` para simular o "hoje" dos dados, que vão até 2026-03). Abaixo de cada resposta, a linha em
itálico mostra `nível · provedor · tools chamadas`.

| Arquivo | Cenário | O que mostra |
|---|---|---|
| [01-violoes-ate-1000.md](01-violoes-ate-1000.md) | "violões até R$1000" e parcelamento | busca no catálogo (`buscar_produtos`), simulação de pagamento (`simular_pagamento`) com a regra de parcela mínima |
| [02-arrependimento-pedido.md](02-arrependimento-pedido.md) | "me arrependi da compra, posso devolver?" | pedido real verificado por e-mail (`consultar_pedido`) e janela de 7 dias do manual §4.1 aplicada à data |
| [03-english-takamine-strings.md](03-english-takamine-strings.md) | em inglês: preço do Takamine GD20, cordas, endereço | preço, PIX e 12x da tool em formato en-US; acessório fora de escopo; endereço do manual |

Conferência dos valores contra os CSV (`data/`), também coberta pelos testes `tests/e2e`:
- 01: os 5 violões citados são ativos, da categoria Violões e custam o que aparece (R$ 429,90 a R$ 699,90);
  Tagima Memphis AC-39 R$ 429,90 → máximo 5x de R$ 85,98 (4–6x exige parcela ≥ R$ 80; 6x daria R$ 71,65).
- 02: pedido 7, `delivered`, entrega estimada em 2026-02-17 → arrependimento até 2026-02-24; hoje = 2026-02-20 → 4 dias.
- 03: Takamine GD20 (id 95) R$ 2.199,00; PIX R$ 2.089,05 (−5%, sem promoção ativa); 12x de R$ 183,25.

Limitações vistas na gravação:
- A conta Anthropic estava sem crédito: o nível complexo caiu no Gemini (fallback esperado; o circuito do Claude abriu
  na primeira chamada). Com uma chave Anthropic com crédito, esse nível usa `claude-opus-5-5`.
- No 01, a 2ª pergunta pedia PIX e 10x; a resposta trouxe só o parcelamento (o valor informado está correto).

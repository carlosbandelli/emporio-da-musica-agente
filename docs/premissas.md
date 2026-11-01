# Premissas e decisões sobre ambiguidades

O enunciado pede: "se algo estiver ambíguo, assuma uma interpretação razoável e documente a suposição".
Critério geral: **a regra mais específica vence** e, **na dúvida, o agente não inventa** — encaminha para a equipe
(manual §10: "em caso de dúvidas, consultar a gerência").

| # | Ambiguidade | Decisão |
|---|---|---|
| 1 | Telefone (67) 3341-4444 (capa/§1.2) × WhatsApp (67) 3321-4500 (§7) | Informar os dois: **telefone 3341-4444**, **WhatsApp 3321-4500** — §7 é a seção específica do canal WhatsApp. |
| 2 | E-mail com acento (§1.2) × sem acento (capa) | Usar **contato@emporiodamusica.com.br** (sem acento); a versão acentuada é tratada como erro de digitação. |
| 3 | Qual é a data de "hoje" (pedidos vão até 03/2026) | Relógio real por padrão; variável `DATA_REFERENCIA` no `.env` permite simular uma data em testes e exemplos. |
| 4 | Tabela §3 diz "parcela mínima R$100" × §3.1 com faixas | Vale a **§3.1**: até 3x parcela mínima **R$50** (leitura de "sem mínimo, exceto abaixo de R$50"); 4–6x mínimo R$80; 7–12x mínimo R$100. |
| 5 | Defeito "30 dias após a compra" × garantia legal "90 dias após o recebimento" | Não conflitam: até 30 dias da compra a **loja troca**; até 90 dias do recebimento vale a **garantia legal**, com a loja intermediando junto ao fabricante. |
| 6 | Não há data real de recebimento nos dados | Para pedidos `delivered`, usar `estimated_delivery` como data de recebimento, deixando isso explícito na resposta. |
| 7 | Arrependimento vale só para compra online; pedido não tem canal | Todos os pedidos do sistema são tratados como **online** (todos têm entrega/rastreio). |
| 8 | "Venda final" (§4.4) não aparece nos dados | O agente cita a exceção, mas nunca afirma que um item é venda final; em caso de dúvida, encaminha para a equipe. |
| 9 | Manual cita sopros e cordas orquestrais, mas não há produtos | Responder que **no momento não há itens dessas categorias no catálogo** (não dizer que a loja não vende). |
| 10 | "Sugerir lojas parceiras" sem lista de parceiras | Não inventar nomes; apenas redirecionar com educação. |
| 11 | Frete fora de Campo Grande depende de CEP/peso/dimensões, ausentes nos dados | Em Campo Grande a resposta é exata (≥ R$500 grátis, senão R$35, 1–3 dias úteis). Fora dela, o agente informa modalidades e prazos (PAC 5–12, SEDEX 2–5, Jadlog 3–8 dias úteis), **não informa valor** e registra um pedido de cotação (`registrar_solicitacao`, tipo `cotacao_frete`) para a equipe responder pelo WhatsApp. Instrumentos grandes: sempre cotação especial (§5.2). |
| 12 | "Região metropolitana" sem lista de cidades; caso de exatamente R$500 | Considerar a cidade de **Campo Grande**; pedido **≥ R$500** tem frete grátis (interpretação favorável ao cliente). |
| 13 | Quais feriados contam | Feriados nacionais (biblioteca `holidays`). Horário estendido em datas especiais não tem dados: o agente avisa que "pode ser estendido". |
| 14 | Mensagem fora do expediente | O assistente virtual **responde normalmente** e informa quando a equipe humana volta a atender. |
| 15 | Verificação de identidade para consultar pedidos (LGPD, §9) | Exigir **número do pedido + e-mail ou telefone** do cliente; sem correspondência, nenhum dado do pedido é exibido. |
| 16 | Onde "registrar" reclamação (§7.3) | `registrar_solicitacao` grava em `reclamacoes.jsonl` (tipos: reclamação, cotação de frete, outros) e informa retorno em 24h úteis. |

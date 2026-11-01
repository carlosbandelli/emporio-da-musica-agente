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
| 16 | Onde "registrar" reclamação (§7.3) | `registrar_solicitacao` grava a solicitação no repositório `Solicitacoes` (Postgres no schema do tenant; arquivo `.jsonl` no modo local) — tipos: reclamação, cotação de frete, outros — e informa retorno em 24h úteis. |
| 17 | Arredondamento das parcelas (ex.: R$ 1.000,00 em 3x) | Valores em centavos, `ROUND_HALF_UP`; a sobra da divisão vai para a **1ª parcela** (R$ 333,34 + R$ 333,33 + R$ 333,33). |
| 18 | Pagamento combinado (acima de R$ 2.000) em que uma das partes falha | **Estornar as partes já aprovadas** e o cliente refaz o pagamento do zero. Mais simples e sem estado parcial pendente; o manual não trata o caso. |
| 19 | O avaliador roda o projeto sem nenhuma chave de LLM configurada | `emporio diagnostico` explica o que falta e `emporio chat` encerra com mensagem clara (código de saída 2), sem stack trace. `uv run pytest` continua funcionando sem chave (arquitetura §9.1, caminho 3). |
| 20 | O manual de políticas só existe em PDF; o CAG precisa de texto | O PDF é convertido uma vez para `config/tenants/emporio/politicas.md`, revisado à mão contra o original e versionado. O CAG lê o `.md`; o PDF em `data/` fica como origem e não é lido em tempo de execução. |
| 21 | Onde ficam sessão, rate limit e estado do circuito sem Redis (modo local, Fase 1) | Em memória, por processo (adapters `memoria`): o histórico some ao sair do `chat`. Documentado como limitação do modo local; com Redis (caminho 2), sessão e rate limit passam a ser compartilhados entre réplicas e o circuito continua por pod (arquitetura §5.3). |
| 22 | `orders.total_brl` está em reais ou centavos? (`11499`, `799.9`) | **Reais, com casas decimais opcionais** (`11499` = R$ 11.499,00). Evidência: em 18 dos 20 pedidos o total é igual a soma(preço de `products.csv` x quantidade de `order_items.csv`) em reais (ex.: pedido 1 = R$ 11.499,00; pedido 4 = R$ 799,90). Nos pedidos 3 e 20 o total é menor que a soma (R$ 3.450 vs 3.498; R$ 1.400 vs 1.488): tratado como desconto/ajuste no pedido, sem inferir a causa. Convertido para centavos inteiros com `ROUND_HALF_UP`. |
| 23 | `specs` com JSON inválido (nenhum caso nos dados atuais) | O produto é **mantido com `specs` vazias** e um `warning` com linha e id; `specs` que não seja objeto JSON recebe o mesmo tratamento. Preço, nome e estoque continuam utilizáveis. |
| 24 | Linha malformada em campo estrutural (id, preço, categoria, estoque negativo, `status`/forma de pagamento desconhecidos, promoção com desconto fora de 1-100) | A linha é **descartada** com `warning` (arquivo, nº da linha; nunca dados pessoais). Id duplicado em produtos: vale a primeira ocorrência. Campos acessórios inválidos (datas, `total_brl`) viram `None` e a linha é mantida. |
| 25 | Pedidos `delivered` também têm rastreio e previsão; só `pending`/`confirmed`/`cancelled` não | `tracking_code`/`estimated_delivery` vazios viram `None`; o adapter não presume que só pedidos `shipped` tenham rastreio. `notes` é preservado como texto. `credit_Nx` vira crédito em N parcelas; `debit` vira débito. |
| 26 | Todas as promoções (ativas e inativas) e categorias 6-8 | `listar_promocoes` devolve **todas** as linhas do produto com a flag `ativa`; filtrar por `is_active` é regra de domínio. Categorias sem produtos são mantidas. Produtos `discontinued`/`coming_soon` só aparecem com `incluir_inativos=True`; busca por texto ignora acento e caixa e exige todos os termos. |
| 27 | Relógio com `DATA_REFERENCIA` | A referência é o **instante inicial**: o relógio avança com o tempo real a partir dela (deslocamento fixo). Referência sem fuso é lida como hora de Campo Grande (`America/Campo_Grande`); `agora()` devolve sempre UTC com fuso e o domínio converte. |

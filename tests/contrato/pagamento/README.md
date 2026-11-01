# Suíte de contrato da porta `GatewayPagamento`

Garante que **todo** gateway de pagamento cumpre o contrato de `docs/pagamento.md` (§2 tipos, §3 máquina de estados,
§4 isolamento por tenant, §5 idempotência/retry/webhook). Cada teste de `test_gateway_pagamento.py` roda uma vez por
gateway registrado em `fabricas.GATEWAYS`; hoje só `fake` (`FakeGatewayPagamento`).

```
uv run pytest tests/contrato/pagamento -v
```

Sem rede, sem `.env`, sem dinheiro real. Comportamentos próprios do simulado (cenários por centavos, expiração pelo
relógio, latência, rotação de segredo, janela anti-replay) ficam em `tests/adaptadores/pagamento/test_fake.py`.

## O que o contrato cobre

| Grupo | Verificação |
|---|---|
| Emissão | PIX e checkout (crédito/débito) nascem `PENDENTE`, com valor em centavos, expiração com fuso e link `https://`; valor ≤ 0 e parcelas fora de 1..12 (débito = 1) → `ErroPermanente` sem cobrar. |
| Idempotência | Mesma chave → mesmo resultado e uma só cobrança (também em 5 chamadas concorrentes); mesma chave com outro conteúdo → `ErroPermanente`; nova tentativa = nova chave; `buscar_por_idempotencia`. |
| Resiliência | Transitória + retry (§5.2: 3 tentativas, base 1 s, teto 8 s) com a mesma chave → 1 cobrança; desiste após 3; permanente não repete; timeout ambíguo → reconcilia pela chave, sem cobrança/estorno duplo. |
| Tenants | Pagamento de um tenant não existe no outro; mesma chave em tenants diferentes = cobranças distintas; webhook assinado por outro tenant é rejeitado. |
| Estados (§3) | Caminhos pendente→aprovado, autorizado→captura/void, recusado, expirado, disputa ganha/perdida; toda sequência observada é transição permitida; terminal não volta; cancelar só pendente/autorizado. |
| Estorno | Parcial(is) e depois o restante, total direto, repetição idempotente, valor inválido, pagamento não aprovado ou já estornado. |
| Webhook | Assinatura válida → `EventoPagamento` normalizado **sem alterar estado**; evento fora de ordem não regride; corpo adulterado ou sem assinatura → `AssinaturaInvalida`. |
| Meta | `_GatewaySemIdempotencia` (fora do registro) prova que a verificação de idempotência detecta cobrança dupla. |

## Como um gateway real (ex.: Mercado Pago) entra

1. Implemente o adapter em `src/emporio/adaptadores/pagamento/` seguindo a porta `emporio.portas.pagamento`.
2. Em `fabricas.py`, escreva uma fábrica `() -> KitGateway` que crie o adapter apontando para o **sandbox** e um
   `ControleGateway` (ver `kit.py`):
   - `levar_a(tenant, id, status)`: usa as alavancas do sandbox (cartões/valores de teste, API de simulação);
   - `webhook(tenant, id, status)`: monta um webhook assinado com o segredo de teste do tenant, sem mudar estado;
   - `cobrancas_emitidas(tenant)`: conta as cobranças criadas no sandbox (detecta cobrança dupla);
   - `suporta_falhas = False` se o sandbox não permite injetar falhas (os testes de resiliência são pulados).
3. Registre em `GATEWAYS` (`"mercadopago": fabrica_mercadopago`), de preferência só quando as credenciais de sandbox
   existirem (ex.: marcador ou `pytest.skip` na fábrica), para o CI continuar sem rede.
4. Rode a suíte e corrija o adapter, não os testes.

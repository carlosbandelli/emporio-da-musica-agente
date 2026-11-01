# Pagamento — desenho da porta `GatewayPagamento`

Documento de **desenho** (não há implementação de pagamento real). O desafio pede um agente de atendimento, não um
checkout: no protótipo o agente **calcula e explica** formas de pagamento (`simular_pagamento`) e, no máximo, gera uma
cobrança **simulada**. Este documento deixa a borda pronta para um gateway real virar troca de adapter, coerente com
`docs/arquitetura.md` (§3, §4, §5.2, §8).

## 1. Onde a porta se encaixa

```
Tool / endpoint admin / webhook
        │
        ▼
Caso de uso (aplicação: módulo Comercial/Pedidos)
   ├─ Domínio: Dinheiro, Parcelamento, PlanoPagamento  → calcula valores e valida regras
   ├─ Repositório Pagamentos (Postgres, schema do tenant) → estado local, idempotência, auditoria
   └─ Porta GatewayPagamento ──► FakeGatewayPagamento (padrão) │ MercadoPagoGateway (evolução) │ ...
                                     └─ credenciais via porta Segredos (por tenant)
```

| Domínio (código determinístico, sem I/O) | Adapter do gateway (borda) |
|---|---|
| Preço final: PIX −5% sobre preço de **tabela**; **sem** desconto sobre preço promocional (não acumula) | Recebe o valor **já calculado** em centavos; nunca aplica desconto |
| Débito e boleto à vista; boleto compensa em até 3 dias úteis | Emite a cobrança e informa o status |
| Crédito até 12x sem juros com parcela mínima por faixa (≤3x: R$50; 4–6x: R$80; 7–12x: R$100) → calcula `parcelas_max` | Limita as parcelas oferecidas no checkout a `parcelas_max`; configura "sem juros" (taxa absorvida pela loja — verificar na documentação de cada gateway) |
| Combinação de formas só acima de R$2.000 → `PlanoPagamento` com N partes | Uma cobrança por parte; não conhece a regra |
| Máquina de estados do pagamento e efeito no pedido | Traduz status/eventos do provedor para o enum interno |
| Arredondamento: `Dinheiro` em centavos (`int`), `ROUND_HALF_UP`; resto da divisão em parcelas vai para a 1ª parcela | Converte centavos para o formato do provedor (ex.: decimal) |

Como as faixas exigem mínimos crescentes, as parcelas válidas formam sempre um prefixo `1..k`; por isso basta passar
`parcelas_max = k` ao gateway. O valor e as parcelas **nunca** vêm de argumentos do LLM: o caso de uso recalcula a
partir do pedido no banco.

## 2. Contrato da porta

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Literal, Mapping, NewType, Protocol

TenantId = NewType("TenantId", str)
Centavos = NewType("Centavos", int)          # sempre > 0; nunca float
ChaveIdempotencia = NewType("ChaveIdempotencia", str)
Moeda = Literal["BRL"]


class FormaPagamento(StrEnum):
    PIX = "pix"
    CREDITO = "credito"
    DEBITO = "debito"
    BOLETO = "boleto"                        # evolução: criar_boleto() com o mesmo padrão


class StatusPagamento(StrEnum):
    CRIADO = "criado"                        # registro local, antes de chamar o gateway
    PENDENTE = "pendente"                    # cobrança emitida, aguardando o cliente
    AUTORIZADO = "autorizado"                # cartão autorizado, não capturado
    APROVADO = "aprovado"
    RECUSADO = "recusado"
    EXPIRADO = "expirado"
    CANCELADO = "cancelado"
    ESTORNADO_PARCIAL = "estornado_parcial"
    ESTORNADO = "estornado"
    CONTESTADO = "contestado"                # chargeback


@dataclass(frozen=True, slots=True)
class ReferenciaPagador:
    cliente_id: str                          # id interno; dados pessoais mínimos (LGPD)
    email: str | None = None                 # só se o gateway exigir — verificar na documentação


@dataclass(frozen=True, slots=True)
class NovaCobrancaPix:
    tenant_id: TenantId
    pedido_id: str
    valor: Centavos                          # já com o desconto PIX aplicado pelo domínio
    descricao: str                           # sem PII
    expira_em: timedelta                     # ex.: 30 min (config por tenant)
    chave_idempotencia: ChaveIdempotencia
    pagador: ReferenciaPagador
    moeda: Moeda = "BRL"


@dataclass(frozen=True, slots=True)
class CobrancaPix:
    pagamento_id_externo: str
    status: StatusPagamento
    valor: Centavos
    copia_e_cola: str                        # payload EMV do PIX
    qr_code_png_base64: str | None           # alguns gateways só devolvem o payload — verificar
    expira_em: datetime                      # timezone-aware


@dataclass(frozen=True, slots=True)
class NovoCheckoutCartao:
    tenant_id: TenantId
    pedido_id: str
    valor: Centavos
    tipo: Literal[FormaPagamento.CREDITO, FormaPagamento.DEBITO]
    parcelas_max: int                        # calculado por Parcelamento (1..12); débito = 1
    url_retorno: str
    expira_em: timedelta
    chave_idempotencia: ChaveIdempotencia
    pagador: ReferenciaPagador
    moeda: Moeda = "BRL"


@dataclass(frozen=True, slots=True)
class CheckoutCartao:
    pagamento_id_externo: str
    status: StatusPagamento
    url_checkout: str                        # página HOSPEDADA pelo gateway (PCI)
    expira_em: datetime


@dataclass(frozen=True, slots=True)
class SituacaoPagamento:
    pagamento_id_externo: str
    status: StatusPagamento
    valor: Centavos
    valor_estornado: Centavos
    parcelas: int | None
    atualizado_em: datetime


@dataclass(frozen=True, slots=True)
class SolicitacaoEstorno:
    tenant_id: TenantId
    pagamento_id_externo: str
    valor: Centavos | None                   # None = estorno total
    motivo: str
    solicitado_por: str                      # usuario_id do JWT (auditoria)
    chave_idempotencia: ChaveIdempotencia


@dataclass(frozen=True, slots=True)
class ResultadoEstorno:
    estorno_id_externo: str
    status: StatusPagamento                  # ESTORNADO_PARCIAL | ESTORNADO
    valor_estornado: Centavos


@dataclass(frozen=True, slots=True)
class EventoPagamento:
    """Evento do provedor já verificado e normalizado (sem PII)."""
    evento_id: str                           # chave de deduplicação
    pagamento_id_externo: str
    tipo: str                                # ex.: "pagamento.atualizado"
    status_informado: StatusPagamento | None # pode faltar: o caso de uso reconsulta
    ocorrido_em: datetime


# Erros: a política de retry depende do tipo, não do texto da mensagem.
class ErroGateway(Exception): ...
class ErroTransitorio(ErroGateway): ...      # timeout, conexão, 429, 5xx → retry permitido
class ErroPermanente(ErroGateway): ...       # 400, 401/403, credencial inválida → sem retry
class AssinaturaInvalida(ErroGateway): ...
class PagamentoNaoEncontrado(ErroGateway): ...


class GatewayPagamento(Protocol):
    nome: str                                # "fake", "mercadopago", ... (métricas/circuit breaker)

    async def criar_cobranca_pix(self, cmd: NovaCobrancaPix) -> CobrancaPix: ...

    async def criar_checkout_cartao(self, cmd: NovoCheckoutCartao) -> CheckoutCartao: ...

    async def consultar(self, tenant_id: TenantId, pagamento_id_externo: str) -> SituacaoPagamento: ...

    async def buscar_por_idempotencia(
        self, tenant_id: TenantId, chave: ChaveIdempotencia
    ) -> SituacaoPagamento | None: ...          # reconciliação após timeout ambíguo

    async def cancelar(
        self, tenant_id: TenantId, pagamento_id_externo: str, chave: ChaveIdempotencia
    ) -> SituacaoPagamento: ...                 # só PENDENTE/AUTORIZADO (void)

    async def estornar(self, cmd: SolicitacaoEstorno) -> ResultadoEstorno: ...

    async def processar_webhook(
        self, tenant_id: TenantId, cabecalhos: Mapping[str, str], corpo: bytes
    ) -> EventoPagamento: ...                   # verifica assinatura e normaliza; não altera estado
```

Decisões do contrato:
- **Centavos `int` e BRL fixo**: sem `float` em dinheiro; `Moeda` é `Literal` para abrir multimoeda sem quebrar a API.
- **`tenant_id` em toda chamada**: o adapter resolve as credenciais do tenant a cada operação (seção 4).
- **Chave de idempotência gerada pelo caso de uso**, gravada no registro local `CRIADO` **antes** de chamar o
  gateway (`UNIQUE` no schema do tenant). Toda nova tentativa reusa a mesma chave. Formato:
  `{tenant}:{pedido}:{forma}:{tentativa}` — uma nova tentativa do cliente (ex.: após recusa) incrementa `tentativa`.
- **`processar_webhook` só verifica e normaliza**; deduplicação e transição de estado ficam no caso de uso, iguais
  para qualquer adapter.

## 3. Máquina de estados

```
CRIADO     → PENDENTE | CANCELADO (falha permanente ao emitir)
PENDENTE   → APROVADO | AUTORIZADO | RECUSADO | EXPIRADO | CANCELADO
AUTORIZADO → APROVADO (captura) | CANCELADO (void)
APROVADO   → ESTORNADO_PARCIAL | ESTORNADO | CONTESTADO
ESTORNADO_PARCIAL → ESTORNADO_PARCIAL (novo estorno parcial) | ESTORNADO
CONTESTADO → APROVADO (disputa ganha) | ESTORNADO (disputa perdida)
```

- Transições são validadas no domínio; evento fora de ordem ou que regride estado (ex.: `pendente` depois de
  `aprovado`) é **ignorado e registrado**, não aplicado.
- Terminais: `RECUSADO`, `EXPIRADO`, `CANCELADO`, `ESTORNADO`.

Relação com o pedido (`orders.status`). O pedido só muda por caso de uso da aplicação, nunca pelo adapter:

| Pedido | Condição de pagamento |
|---|---|
| `pending` | nenhuma parte `APROVADO` ainda (`CRIADO`/`PENDENTE`/`AUTORIZADO`, ou `RECUSADO` com nova tentativa possível); boleto fica aqui até compensar (até 3 dias úteis) |
| `confirmed` | **soma das partes `APROVADO` = total do pedido** (com combinação de formas, todas as partes) |
| `shipped` / `delivered` | logística; pré-condição: pagamento `APROVADO` |
| `cancelled` | todas as tentativas `EXPIRADO`/`CANCELADO`/`RECUSADO` após o prazo, ou cancelamento do pedido → `cancelar` (se pendente) ou `estornar` (se aprovado) |

- **Combinação de formas** (> R$2.000): se uma parte falhar/expirar, as já aprovadas são estornadas (compensação),
  ou o cliente refaz só a parte pendente dentro do prazo — decisão de produto a confirmar com a loja.
- **Lacuna dos dados**: estorno após `delivered` (devolução, manual §4) não tem status próprio em `orders.csv`; o
  pedido continua `delivered` e o estorno fica no histórico do pagamento. Evolução: status `returned`/`refunded`.
- `payment_method` dos dados (`pix`, `debit`, `boleto`, `credit_Nx`) mapeia para `FormaPagamento` + `parcelas`.

## 4. Multi-tenancy

- Tabelas `pagamentos`, `eventos_pagamento` e `auditoria_pagamentos` em **cada schema de tenant** (mesmo isolamento
  dos pedidos; não existe consulta de pagamento sem tenant).
- `config/tenants.yaml` guarda **só referências**, nunca valores:

  ```yaml
  emporio:
    pagamento:
      habilitado: false            # protótipo: tool gerar_link_pagamento desligada
      gateway: fake                # fake | mercadopago | ...
      pix_expiracao_min: 30
      segredos:                    # nomes resolvidos pela porta Segredos
        access_token: PAGAMENTO_EMPORIO_ACCESS_TOKEN
        webhook_secret: PAGAMENTO_EMPORIO_WEBHOOK_SECRET
  ```

- O adapter pede `Segredos.obter(tenant_id, "pagamento.access_token")` — variável de ambiente hoje, Secret do K8s
  (um por tenant) em produção, cofre (Vault/Secrets Manager) como evolução. Cliente HTTP por tenant em cache por pod
  (single-flight), sem estado de negócio.
- Cada loja usa **a própria conta** no gateway (o dinheiro cai na conta da loja; a plataforma não intermedeia
  recursos — evita virar subcredenciadora). Gateways diferentes por tenant são permitidos.
- Rotação: aceitar dois `webhook_secret` simultâneos durante a troca.

## 5. Resiliência (alinhada à §5.2)

`deadline → bulkhead → timeout → retry (backoff exponencial + full jitter) → circuit breaker → fallback`

| Operação | Retry? | Observação |
|---|---|---|
| `consultar`, `buscar_por_idempotencia` | sim (leitura) | 2–3 tentativas, só erros transitórios |
| `criar_cobranca_pix`, `criar_checkout_cartao`, `cancelar`, `estornar` | **só com idempotency key honrada pelo provedor** (verificar na documentação de cada um) | sem garantia de idempotência no provedor: após timeout, **não** reenvia; chama `buscar_por_idempotencia`/reconciliação |
| `processar_webhook` | não se aplica | o provedor reenvia; nós respondemos rápido |

- **Timeout ambíguo** (não se sabe se a cobrança foi criada): o pagamento fica `CRIADO`; o agente responde
  "estou gerando seu link, já te aviso" e a reconciliação resolve. Nunca gera uma segunda cobrança com outra chave.
- **Circuit breaker** por `(gateway, tenant)`: `401/403` (credencial de uma loja) abre só o circuito daquela loja;
  limiar de `5xx`/timeout abre por gateway. Estado em memória por pod (porta `EstadoCircuito`).
- **Bulkhead** separado dos LLMs: gateway lento não prende os workers do chat.
- **Fallback**: **não** há troca automática para outro gateway (risco de cobrança dupla). Fallback = mensagem fixa
  traduzida ("não consegui gerar o pagamento agora") + `registrar_solicitacao` para a equipe enviar o link.
- **Webhook** — `POST /v1/webhooks/pagamento/{tenant_id}` (sem JWT; autenticado pela assinatura):
  1. Lê o corpo **bruto**; verifica a assinatura com o `webhook_secret` do tenant (HMAC, `hmac.compare_digest`,
     tolerância de timestamp contra replay — formato exato por gateway, verificar na documentação). Inválida → `401`.
  2. **Deduplica**: `INSERT ... ON CONFLICT DO NOTHING` em `eventos_pagamento(evento_id UNIQUE)` no Postgres (durável;
     não no Redis). Duplicado → `200` sem reprocessar.
  3. Responde `2xx` rápido; o processamento não depende do LLM.
  4. **Não confia no payload**: reconsulta `consultar()` e aplica a transição pela máquina de estados (resolve eventos
     fora de ordem).
- **Reconciliação** (job periódico, advisory lock para rodar em uma réplica): pagamentos `CRIADO`/`PENDENTE` antigos
  são consultados no gateway; expirados são fechados; relatório diário compara aprovados × liquidação do gateway.
- Métricas OTel: taxa de aprovação, latência por operação, estado do circuito, webhooks inválidos/duplicados,
  divergências da reconciliação — todas com `tenant_id`, sem PII.

## 6. Segurança e compliance

- **PCI DSS**: dados de cartão **nunca** passam pelo agente, pela API, pelos logs ou pelo LLM. Cartão só em
  **checkout hospedado** pelo gateway (ou campos tokenizados do próprio gateway no front, em evolução). Com página
  totalmente hospedada, o escopo tende ao SAQ A — confirmar com o gateway/adquirente.
- **Guardrail no chat**: antes de enviar ao LLM e de salvar na sessão, mensagens são varridas por números com cara
  de cartão (regex + Luhn) e CVV; se encontrar, o trecho é mascarado e o agente responde (mensagem fixa) que **não
  deve enviar dados de cartão pelo chat**.
- **Credenciais**: o LLM nunca recebe token, segredo ou ID de conta. A tool devolve apenas link, copia-e-cola,
  valor e status. Segredos não aparecem em logs, traces nem mensagens de erro.
- **LGPD**: minimização — ao gateway vai só o necessário (id do cliente; nome/CPF/e-mail apenas se o gateway exigir,
  coletados preferencialmente na página hospedada — verificar na documentação). Do webhook guardamos id, status,
  valor e datas, não dados do pagador. Telemetria segue a §7 (sem PII).
- **RBAC** (permissões novas, mesmo mecanismo `Depends(exige(...))`):

| Permissão | cliente | atendente | gerente | admin_plataforma |
|---|---|---|---|---|
| `pagamentos:simular` (calcular formas/parcelas) | ✅ | ✅ | ✅ | — |
| `pagamentos:gerar_link` (pedido próprio, identidade verificada) | ✅ via agente | ✅ | ✅ | — |
| `pagamentos:ler` | só pedido próprio | ✅ | ✅ | métricas agregadas |
| `pagamentos:cancelar` (cobrança pendente) | — | ✅ | ✅ | — |
| `pagamentos:estornar` (total/parcial) | — | — | ✅ | — |
| `pagamentos:configurar` (gateway/credenciais do tenant) | — | — | ✅ (escolha do gateway) | ✅ (provisionar segredos) |

- **Estorno nunca é tool do LLM** (nem para atendente/gerente via chat): é endpoint admin com motivo obrigatório e
  trilha de auditoria append-only (quem, quando, valor, motivo, chave de idempotência). A dupla checagem de tenant
  (§8) vale para todos os endpoints de pagamento.

## 7. Uso pelo agente

Hoje: `simular_pagamento` (só domínio, sem gateway) responde "quanto fica no PIX / em quantas vezes".

Evolução: tool `gerar_link_pagamento(pedido_id, forma, parcelas_desejadas)`:

1. Pré-condições em código: identidade verificada (premissa 15), pedido do tenant em `pending`, tenant com
   `pagamento.habilitado: true`.
2. **Proposta**: o caso de uso calcula valor/desconto/parcelas pelo domínio e grava uma proposta na sessão (Redis,
   TTL curto) com hash dos valores; o agente mostra o resumo ("PIX: R$ 1.234,05, já com 5% de desconto; vence em
   30 min. Posso gerar?").
3. **Confirmação explícita**: a cobrança só é criada na mensagem seguinte do cliente, se houver proposta válida e
   inalterada. Em canais com botões (WhatsApp/web), a confirmação é estruturada (quick reply), sem depender da
   interpretação do LLM.
4. Retorna link/copia-e-cola; a confirmação do pagamento chega **pelo webhook**, não pela conversa ("já paguei" não
   muda status — o agente consulta).

No protótipo só simulamos porque: o desafio avalia atendimento, não checkout; não há conta nem credenciais reais;
cobrar a partir de dados fictícios seria arriscado. O `FakeGatewayPagamento` mantém o contrato, os estados e a
resiliência testáveis sem dinheiro real.

## 8. Adapters

**`FakeGatewayPagamento` (padrão)**
- Sem credenciais nem rede; estado no schema do tenant (ou memória em testes).
- Copia-e-cola claramente inválido (`SIMULADO-...`) e link `https://pagamento.simulado.local/...`; respostas marcadas
  como simulação para o agente avisar o cliente.
- Cenários determinísticos: valores com centavos específicos → recusa/expiração; comando de CLI
  `simular-evento <pagamento> aprovado` dispara um webhook **assinado com HMAC** pelo mesmo endpoint real.
- Injeção de falhas configurável (latência, `429`, `5xx`, timeout ambíguo) para testar retry, circuit breaker e
  reconciliação.
- **Testes de contrato** compartilhados: a mesma suíte roda contra o Fake e, na evolução, contra o sandbox do
  gateway real.

**Comparação de gateways brasileiros com PIX** (características gerais; taxas, limites e detalhes de API —
verificar na documentação de cada um antes de implementar):

| Gateway | Pontos fortes | Pontos de atenção |
|---|---|---|
| **Mercado Pago** | Muito usado no varejo BR; PIX, cartão com parcelamento, boleto; checkout hospedado (Checkout Pro); SDK Python oficial; sandbox; header de idempotência e webhooks assinados | Configuração de parcelamento sem juros e formato exato da assinatura do webhook — verificar na documentação |
| **Pagar.me** (Stone) | API orientada a pedidos (PIX, boleto, cartão); foco em e-commerce/marketplace; split de pagamentos | SDK Python e mecanismo de assinatura de webhook — verificar na documentação |
| **Asaas** | Simples para PIX/boleto e cobrança recorrente; bom para pequenos negócios; sandbox | Autenticação de webhook por token e recursos de checkout hospedado para cartão — verificar na documentação |
| **Stripe** | Melhor experiência de desenvolvedor: idempotency keys nativas, webhooks assinados, Checkout hospedado, SDK Python maduro | Disponibilidade de PIX e parcelamento para contas brasileiras — verificar na documentação |

**Recomendação: Mercado Pago como primeiro adapter real.** Cobre as quatro formas do manual (PIX, débito, crédito
parcelado, boleto) com **checkout hospedado** (mantém o escopo PCI mínimo), é familiar para o público de Campo Grande,
tem SDK Python oficial e sandbox para os testes de contrato, e oferece idempotência e webhooks assinados — os dois
requisitos da §5.2. Stripe é a alternativa se a cobertura de PIX/parcelamento no Brasil for confirmada; Asaas faz
sentido se o foco virar PIX/boleto e recorrência.

"""Apoio dos cenários ponta a ponta: montagem real (motor + tools + CSV) com LLM roteirizado e leitura dos CSVs.

Os CSVs são lidos aqui de forma independente da aplicação (módulo `csv`), para conferir os valores das tools
contra a fonte e não contra o próprio código testado.
"""

import csv
import json
from collections.abc import Callable
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Final

from emporio.adaptadores.llm import LLMFalso
from emporio.composicao import Aplicacao, montar
from emporio.configuracao import Settings
from emporio.portas import (
    MensagemCliente,
    PapelMensagem,
    RequisicaoLLM,
    RespostaAtendimento,
    RespostaLLM,
    TenantContext,
)
from tests.modulos.fakes import RelogioFixo

RAIZ: Final = Path(__file__).resolve().parents[2]
CTX: Final = TenantContext("emporio")
Linha = dict[str, str]
Resultado = dict[str, Any]
"""Resultado de uma tool como o LLM o recebe: `{"nome", "ok", "dados"}` (JSON já decodificado)."""


def settings_e2e(data_referencia: datetime | None = None, **valores: Any) -> Settings:
    """`Settings` sem arquivo de ambiente e com o `tenants.yaml` do repositório."""
    return Settings(
        _env_file=None, tenants_config=RAIZ / "config" / "tenants.yaml", data_referencia=data_referencia, **valores
    )


def montar_com_roteiro(roteiro: list[Any], agora: datetime, dir_estado: Path) -> tuple[Aplicacao, LLMFalso]:
    """Motor nativo + tools + CSVs reais; só o LLM é o roteiro. `agora` fixa o "hoje" (e a hora) da loja."""
    falso = LLMFalso(roteiro)
    app = montar(
        settings_e2e(agora),
        provedores={"falso": falso},
        raiz=RAIZ,
        dir_estado=dir_estado,
        relogio=RelogioFixo(agora),
    )
    return app, falso


async def conversar(app: Aplicacao, texto: str, sessao_id: str = "e2e") -> RespostaAtendimento:
    """Uma mensagem do cliente ao motor montado."""
    return await app.motor.responder(CTX, sessao_id, MensagemCliente(texto))


def resultados_das_tools(falso: LLMFalso) -> list[Resultado]:
    """Todos os resultados de tool que o LLM viu no turno (na ordem), lidos da última requisição."""
    return [json.loads(m.content) for m in falso.ultima_requisicao.mensagens if m.role is PapelMensagem.TOOL]


def resultado_da_tool(falso: LLMFalso, nome: str) -> Resultado:
    """O único resultado da tool `nome` no turno."""
    achados = [r for r in resultados_das_tools(falso) if r["nome"] == nome]
    assert len(achados) == 1, f"esperava 1 resultado de {nome}, vieram {len(achados)}"
    assert achados[0]["ok"] is True, achados[0]
    return achados[0]["dados"]  # type: ignore[no-any-return]


def argumentos_chamados(falso: LLMFalso) -> list[tuple[str, dict[str, Any]]]:
    """Tools pedidas pelo LLM (nome, argumentos), vistas no histórico da última requisição."""
    return [(c.nome, dict(c.argumentos)) for m in falso.ultima_requisicao.mensagens for c in m.tool_calls]


def redigir(montar_texto: Callable[[list[Resultado]], str]) -> Callable[[RequisicaoLLM], RespostaLLM]:
    """Item de roteiro: resposta final escrita a partir dos resultados de tool recebidos na requisição."""

    def _responder(req: RequisicaoLLM) -> RespostaLLM:
        resultados = [json.loads(m.content) for m in req.mensagens if m.role is PapelMensagem.TOOL]
        return RespostaLLM(montar_texto(resultados), "", "")

    return _responder


# --- CSVs (fonte independente) ---------------------------------------------------------------------------------------
def _ler(tabela: str) -> list[Linha]:
    arquivo = RAIZ / "data" / f"desafio_tecnico_ai_eng - {tabela}.csv"
    with arquivo.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def produtos_csv() -> dict[int, Linha]:
    return {int(p["product_id"]): p for p in _ler("products")}


def produto_por_nome(trecho: str) -> Linha:
    achados = [p for p in produtos_csv().values() if trecho in p["name"]]
    assert len(achados) == 1, trecho
    return achados[0]


def pedidos_csv() -> dict[int, Linha]:
    return {int(p["order_id"]): p for p in _ler("orders")}


def cliente_do_pedido(numero: int) -> Linha:
    cliente_id = pedidos_csv()[numero]["customer_id"]
    return next(c for c in _ler("customers") if c["customer_id"] == cliente_id)


def desconto_ativo(produto_id: int) -> int:
    """Maior desconto ativo do produto (0 se nenhum), como inteiro em %."""
    ativos = [
        int(p["discount_percent"])
        for p in _ler("promotions")
        if int(p["product_id"]) == produto_id and p["is_active"] == "1"
    ]
    return max(ativos, default=0)


def centavos(reais: str | Decimal) -> int:
    return int((Decimal(reais) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def preco_vigente_centavos(produto: Linha) -> int:
    """Preço de tabela com a promoção ativa (se houver), arredondado ao centavo."""
    base = Decimal(produto["price_brl"]) * 100
    desconto = desconto_ativo(int(produto["product_id"]))
    return int((base * (100 - desconto) / 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))

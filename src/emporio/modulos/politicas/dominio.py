"""Regras de domínio das políticas: HorarioLoja, Frete e JanelaDevolucao.

Domínio puro: sem I/O e sem leitura de relógio. Toda função recebe o instante (``agora``, com fuso) ou as
datas como parâmetro. Valores monetários são inteiros em centavos (``*_centavos``); nunca ``float``.

Fonte das regras: manual de políticas v2.1 e ``docs/premissas.md`` (#5, #6, #7, #11, #12, #13, #14).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from functools import cache
from zoneinfo import ZoneInfo

from holidays import HolidayBase
from holidays.countries.brazil import Brazil

__all__ = [
    "FUSO_LOJA",
    "ErroCidadeInvalida",
    "ErroDatetimeSemFuso",
    "ErroPoliticas",
    "HorarioLoja",
    "JanelaDevolucao",
    "ModalidadeFrete",
    "MotivoCotacao",
    "MotivoFechamento",
    "OpcaoFrete",
    "ResultadoFrete",
    "SituacaoJanela",
    "StatusLoja",
    "StatusPedido",
    "TipoJanela",
    "avaliar_janelas",
    "calcular_frete",
    "janela_de",
    "normalizar_cidade",
]

# ---------------------------------------------------------------------------
# Erros
# ---------------------------------------------------------------------------


class ErroPoliticas(Exception):  # noqa: N818 - nome do domínio em pt-BR
    """Erro base das regras de política."""


class ErroDatetimeSemFuso(ErroPoliticas):
    """Foi informado um ``datetime`` sem fuso horário (ingênuo)."""

    def __init__(self) -> None:
        super().__init__("O instante informado não tem fuso horário; use um datetime com fuso (timezone-aware).")


class ErroCidadeInvalida(ErroPoliticas):
    """A cidade de destino está vazia ou não pôde ser interpretada."""

    def __init__(self, cidade: str) -> None:
        super().__init__(f"Cidade de destino inválida: {cidade!r}.")


# ---------------------------------------------------------------------------
# Horário da loja
# ---------------------------------------------------------------------------

FUSO_LOJA = ZoneInfo("America/Campo_Grande")
"""Fuso da loja (UTC-4, sem horário de verão)."""

# weekday(): segunda = 0 ... domingo = 6. Domingo não consta: sempre fechado.
_EXPEDIENTE: dict[int, tuple[time, time]] = {
    0: (time(9), time(18)),
    1: (time(9), time(18)),
    2: (time(9), time(18)),
    3: (time(9), time(18)),
    4: (time(9), time(18)),
    5: (time(9), time(13)),
}
_DOMINGO = 6


class MotivoFechamento(StrEnum):
    """Por que a loja está fechada."""

    FIM_DE_SEMANA = "fim_de_semana"
    FERIADO = "feriado"
    FORA_DO_HORARIO = "fora_do_horario"


@dataclass(frozen=True, slots=True)
class StatusLoja:
    """Situação da loja em um instante.

    Attributes:
        aberta: se a loja está aberta no instante consultado.
        proxima_abertura: o próprio instante (no fuso da loja) se aberta; senão a próxima abertura.
        fechamento_hoje: horário de fechamento do dia (mesmo que já tenha passado); ``None`` em domingo/feriado.
        motivo: ``None`` se aberta; senão o motivo do fechamento (feriado tem precedência sobre domingo).
        nome_feriado: nome do feriado nacional, quando o dia é feriado.
    """

    aberta: bool
    proxima_abertura: datetime
    fechamento_hoje: datetime | None
    motivo: MotivoFechamento | None
    nome_feriado: str | None = None


@cache
def _feriados(ano: int) -> HolidayBase:
    return Brazil(years=ano, language="pt_BR")


def _nome_feriado(dia: date) -> str | None:
    return _feriados(dia.year).get(dia)


def _local(agora: datetime) -> datetime:
    if agora.tzinfo is None or agora.utcoffset() is None:
        raise ErroDatetimeSemFuso
    return agora.astimezone(FUSO_LOJA)


def _expediente_do_dia(dia: date) -> tuple[datetime, datetime] | None:
    """Abertura e fechamento do dia, ou ``None`` se domingo/feriado."""
    if _nome_feriado(dia) is not None:
        return None
    horas = _EXPEDIENTE.get(dia.weekday())
    if horas is None:
        return None
    abre, fecha = horas
    return datetime.combine(dia, abre, tzinfo=FUSO_LOJA), datetime.combine(dia, fecha, tzinfo=FUSO_LOJA)


@dataclass(frozen=True, slots=True)
class HorarioLoja:
    """Horário de funcionamento: seg-sex 09-18, sáb 09-13; domingo e feriados nacionais fechado.

    O fechamento é exclusivo (às 18:00 de sexta a loja já está fechada). Feriados nacionais vêm da
    biblioteca ``holidays`` (premissa #13); horário estendido em datas especiais não é modelado.
    """

    def esta_aberta(self, agora: datetime) -> bool:
        """Informa se a loja está aberta em ``agora`` (datetime com fuso)."""
        local = _local(agora)
        expediente = _expediente_do_dia(local.date())
        return expediente is not None and expediente[0] <= local < expediente[1]

    def proxima_abertura(self, agora: datetime) -> datetime:
        """Próximo instante de abertura, no fuso da loja.

        Se a loja já está aberta, devolve o próprio ``agora`` convertido para o fuso da loja.
        Domingos e feriados nacionais são pulados.
        """
        local = _local(agora)
        dia = local.date()
        while True:
            expediente = _expediente_do_dia(dia)
            if expediente is not None and local < expediente[1]:
                return max(local, expediente[0])
            dia += timedelta(days=1)

    def status(self, agora: datetime) -> StatusLoja:
        """Situação completa da loja em ``agora``."""
        local = _local(agora)
        dia = local.date()
        expediente = _expediente_do_dia(dia)
        feriado = _nome_feriado(dia)
        aberta = expediente is not None and expediente[0] <= local < expediente[1]
        motivo: MotivoFechamento | None
        if aberta:
            motivo = None
        elif feriado is not None:
            motivo = MotivoFechamento.FERIADO
        elif dia.weekday() == _DOMINGO:
            motivo = MotivoFechamento.FIM_DE_SEMANA
        else:
            motivo = MotivoFechamento.FORA_DO_HORARIO
        return StatusLoja(
            aberta=aberta,
            proxima_abertura=self.proxima_abertura(local),
            fechamento_hoje=expediente[1] if expediente is not None else None,
            motivo=motivo,
            nome_feriado=feriado,
        )


# ---------------------------------------------------------------------------
# Frete
# ---------------------------------------------------------------------------

_FRETE_GRATIS_MINIMO_CENTAVOS = 50_000  # premissa #12: >= R$ 500,00 é grátis
_FRETE_CAMPO_GRANDE_CENTAVOS = 3_500
_CAMPO_GRANDE = "campo grande"
_SUFIXO_UF_MS = re.compile(r"^(?P<cidade>.*?)(?:\s*[/,-]\s*|\s+)(?:ms|mato grosso do sul)$")


class ModalidadeFrete(StrEnum):
    """Modalidades de entrega."""

    ENTREGA_LOCAL = "entrega_local"
    PAC = "pac"
    SEDEX = "sedex"
    JADLOG = "jadlog"


class MotivoCotacao(StrEnum):
    """Por que o frete exige cotação em vez de valor."""

    FORA_DE_CAMPO_GRANDE = "fora_de_campo_grande"
    INSTRUMENTO_GRANDE = "instrumento_grande"


@dataclass(frozen=True, slots=True)
class OpcaoFrete:
    """Modalidade de entrega com prazo em dias úteis."""

    modalidade: ModalidadeFrete
    prazo_min_dias_uteis: int
    prazo_max_dias_uteis: int


_OPCAO_LOCAL = OpcaoFrete(ModalidadeFrete.ENTREGA_LOCAL, 1, 3)
_OPCOES_NACIONAIS = (
    OpcaoFrete(ModalidadeFrete.PAC, 5, 12),
    OpcaoFrete(ModalidadeFrete.SEDEX, 2, 5),
    OpcaoFrete(ModalidadeFrete.JADLOG, 3, 8),
)


@dataclass(frozen=True, slots=True)
class ResultadoFrete:
    """Resultado do cálculo de frete.

    ``valor_centavos`` é ``None`` quando o valor depende de cotação; ``0`` quando grátis.
    """

    cidade_normalizada: str
    em_campo_grande: bool
    valor_centavos: int | None
    gratis: bool
    requer_cotacao: bool
    motivo_cotacao: MotivoCotacao | None
    opcoes: tuple[OpcaoFrete, ...]
    mensagem: str

    @property
    def prazo_min_dias_uteis(self) -> int:
        """Menor prazo entre as modalidades, em dias úteis."""
        return min(o.prazo_min_dias_uteis for o in self.opcoes)

    @property
    def prazo_max_dias_uteis(self) -> int:
        """Maior prazo entre as modalidades, em dias úteis."""
        return max(o.prazo_max_dias_uteis for o in self.opcoes)


def normalizar_cidade(cidade: str) -> str:
    """Normaliza o nome da cidade: sem acentos, minúsculo, espaços colapsados e sem sufixo ``/MS``.

    Aceita ``Campo Grande/MS``, ``CAMPO GRANDE - MS``, ``campo  grande``, ``Campo Grande, MS`` etc.
    Outras UFs não são removidas (``Campo Grande/RJ`` não é a cidade da loja).

    Raises:
        ErroCidadeInvalida: se sobrar nome vazio.
    """
    sem_acento = "".join(c for c in unicodedata.normalize("NFKD", cidade) if not unicodedata.combining(c))
    texto = " ".join(sem_acento.casefold().split())
    achado = _SUFIXO_UF_MS.match(texto)
    if achado:
        texto = achado.group("cidade")
    texto = texto.strip(" /,-")
    if not texto:
        raise ErroCidadeInvalida(cidade)
    return texto


def _reais(centavos: int) -> str:
    reais, resto = divmod(centavos, 100)
    return f"R$ {reais:,}".replace(",", ".") + f",{resto:02d}"


def _prazo_texto(opcao: OpcaoFrete) -> str:
    return f"{opcao.prazo_min_dias_uteis} a {opcao.prazo_max_dias_uteis} dias úteis"


def calcular_frete(cidade: str, total_pedido_centavos: int, *, instrumento_grande: bool = False) -> ResultadoFrete:
    """Calcula o frete conforme o manual (§5) e as premissas #11 e #12.

    Campo Grande: grátis a partir de R$ 500,00 (inclusive), senão R$ 35,00; 1 a 3 dias úteis.
    Outras cidades: modalidades e prazos (PAC, SEDEX, Jadlog) sem valor; exige cotação.
    ``instrumento_grande`` (§5.2) sempre exige cotação especial, inclusive em Campo Grande.

    Args:
        cidade: cidade de destino, em texto livre.
        total_pedido_centavos: total dos itens, em centavos.
        instrumento_grande: se o pedido inclui instrumento de grande porte.

    Raises:
        ErroCidadeInvalida: cidade vazia.
        ErroPoliticas: total negativo.
    """
    if total_pedido_centavos < 0:
        raise ErroPoliticas("O total do pedido não pode ser negativo.")
    normalizada = normalizar_cidade(cidade)
    local = normalizada == _CAMPO_GRANDE
    opcoes = (_OPCAO_LOCAL,) if local else _OPCOES_NACIONAIS

    if instrumento_grande:
        return ResultadoFrete(
            cidade_normalizada=normalizada,
            em_campo_grande=local,
            valor_centavos=None,
            gratis=False,
            requer_cotacao=True,
            motivo_cotacao=MotivoCotacao.INSTRUMENTO_GRANDE,
            opcoes=opcoes,
            mensagem="Instrumento de grande porte: o frete é sob cotação especial, feita pela nossa equipe.",
        )
    if not local:
        detalhes = ", ".join(f"{o.modalidade.name.capitalize()} {_prazo_texto(o)}" for o in opcoes)
        return ResultadoFrete(
            cidade_normalizada=normalizada,
            em_campo_grande=False,
            valor_centavos=None,
            gratis=False,
            requer_cotacao=True,
            motivo_cotacao=MotivoCotacao.FORA_DE_CAMPO_GRANDE,
            opcoes=opcoes,
            mensagem=f"Para essa cidade o valor depende de cotação. Modalidades e prazos: {detalhes}.",
        )
    gratis = total_pedido_centavos >= _FRETE_GRATIS_MINIMO_CENTAVOS
    valor = 0 if gratis else _FRETE_CAMPO_GRANDE_CENTAVOS
    prazo = _prazo_texto(_OPCAO_LOCAL)
    texto_valor = (
        f"Frete grátis (pedido a partir de {_reais(_FRETE_GRATIS_MINIMO_CENTAVOS)})"
        if gratis
        else f"Frete de {_reais(valor)}"
    )
    return ResultadoFrete(
        cidade_normalizada=normalizada,
        em_campo_grande=True,
        valor_centavos=valor,
        gratis=gratis,
        requer_cotacao=False,
        motivo_cotacao=None,
        opcoes=opcoes,
        mensagem=f"{texto_valor}, entrega em {prazo}.",
    )


# ---------------------------------------------------------------------------
# Janelas de devolução, troca e garantia
# ---------------------------------------------------------------------------


class StatusPedido(StrEnum):
    """Status de pedido relevantes para as janelas (valores iguais aos do CSV)."""

    PENDING = "pending"
    CONFIRMED = "confirmed"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"


class TipoJanela(StrEnum):
    """Tipos de janela, na ordem em que são devolvidos."""

    ARREPENDIMENTO = "arrependimento"
    TROCA_PREFERENCIA = "troca_preferencia"
    DEFEITO = "defeito"
    GARANTIA_LEGAL = "garantia_legal"


class SituacaoJanela(StrEnum):
    """Situação de uma janela em relação a ``hoje``."""

    DENTRO = "dentro"
    FORA = "fora"
    NAO_INICIADA = "nao_iniciada"
    NAO_APLICAVEL = "nao_aplicavel"


@dataclass(frozen=True, slots=True)
class JanelaDevolucao:
    """Resultado da avaliação de uma janela.

    ``prazo_final`` é o último dia dentro do prazo (inclusive); ``dias_restantes`` só existe quando
    ``situacao`` é ``DENTRO`` (0 no último dia).
    """

    tipo: TipoJanela
    situacao: SituacaoJanela
    prazo_final: date | None
    dias_restantes: int | None
    descricao: str


@dataclass(frozen=True, slots=True)
class _RegraJanela:
    dias: int
    desde_compra: bool
    descricao: str


_REGRAS_JANELA: dict[TipoJanela, _RegraJanela] = {
    TipoJanela.ARREPENDIMENTO: _RegraJanela(7, False, "Arrependimento: 7 dias após o recebimento (compra online)"),
    TipoJanela.TROCA_PREFERENCIA: _RegraJanela(7, False, "Troca por preferência: 7 dias após o recebimento"),
    TipoJanela.DEFEITO: _RegraJanela(30, True, "Defeito: 30 dias após a compra (a loja realiza a troca)"),
    TipoJanela.GARANTIA_LEGAL: _RegraJanela(
        90, False, "Garantia legal: 90 dias após o recebimento (a loja intermedia com o fabricante)"
    ),
}


def _converter_status(status: StatusPedido | str) -> StatusPedido:
    try:
        return StatusPedido(status)
    except ValueError:
        raise ErroPoliticas(f"Status de pedido desconhecido: {status!r}.") from None


def avaliar_janelas(
    *,
    data_compra: date,
    status_pedido: StatusPedido | str,
    data_recebimento: date | None,
    hoje: date,
) -> tuple[JanelaDevolucao, ...]:
    """Avalia as quatro janelas (arrependimento, troca por preferência, defeito, garantia legal).

    O dia da referência é o dia 0: com prazo de 7 dias, o 7º dia ainda está dentro e o 8º está fora.
    Todos os pedidos são tratados como online (premissa #7). Para pedidos ``delivered`` o chamador passa
    ``estimated_delivery`` como ``data_recebimento`` (premissa #6).

    Regras de situação:
        * ``cancelled``: todas ``NAO_APLICAVEL``.
        * Janelas contadas do recebimento: ``NAO_INICIADA`` se o pedido não foi entregue (``data_recebimento``
          é ignorada) ou se o recebimento ainda está no futuro em relação a ``hoje``.
        * Defeito conta sempre da compra (premissa #5), mesmo que o pedido ainda não tenha sido entregue.

    Raises:
        ErroPoliticas: status desconhecido, pedido entregue sem ``data_recebimento``, recebimento anterior
            à compra ou ``hoje`` anterior à compra.
    """
    status = _converter_status(status_pedido)
    if hoje < data_compra:
        raise ErroPoliticas("A data de hoje é anterior à data da compra.")

    recebimento: date | None = None
    if status is StatusPedido.DELIVERED:
        if data_recebimento is None:
            raise ErroPoliticas("Pedido entregue exige a data de recebimento.")
        if data_recebimento < data_compra:
            raise ErroPoliticas("A data de recebimento é anterior à data da compra.")
        recebimento = data_recebimento

    return tuple(
        _avaliar(tipo, regra, status=status, data_compra=data_compra, recebimento=recebimento, hoje=hoje)
        for tipo, regra in _REGRAS_JANELA.items()
    )


def _avaliar(
    tipo: TipoJanela,
    regra: _RegraJanela,
    *,
    status: StatusPedido,
    data_compra: date,
    recebimento: date | None,
    hoje: date,
) -> JanelaDevolucao:
    def montar(situacao: SituacaoJanela, prazo: date | None = None, restantes: int | None = None) -> JanelaDevolucao:
        return JanelaDevolucao(tipo, situacao, prazo, restantes, regra.descricao)

    if status is StatusPedido.CANCELLED:
        return montar(SituacaoJanela.NAO_APLICAVEL)

    referencia = data_compra if regra.desde_compra else recebimento
    if referencia is None:
        return montar(SituacaoJanela.NAO_INICIADA)

    prazo_final = referencia + timedelta(days=regra.dias)
    if referencia > hoje:
        return montar(SituacaoJanela.NAO_INICIADA, prazo_final)
    if hoje <= prazo_final:
        return montar(SituacaoJanela.DENTRO, prazo_final, (prazo_final - hoje).days)
    return montar(SituacaoJanela.FORA, prazo_final)


def janela_de(janelas: tuple[JanelaDevolucao, ...], tipo: TipoJanela) -> JanelaDevolucao:
    """Seleciona a janela de um ``tipo`` no resultado de :func:`avaliar_janelas`."""
    for janela in janelas:
        if janela.tipo is tipo:
            return janela
    raise ErroPoliticas(f"Janela não encontrada: {tipo}.")  # pragma: no cover

"""Casos de uso de políticas: horário da loja, frete e janelas de devolução/troca/garantia.

Devolve modelos Pydantic (DTOs) já prontos para o LLM: outros módulos usam estes tipos e nunca o `dominio` daqui.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

from emporio.modulos.comum import Montante, montante
from emporio.modulos.comum.dinheiro import Dinheiro
from emporio.modulos.politicas.dominio import (
    FUSO_LOJA,
    ErroPoliticas,
    HorarioLoja,
    ResultadoFrete,
    calcular_frete,
)
from emporio.modulos.politicas.dominio import avaliar_janelas as _avaliar_janelas
from emporio.portas import Idioma, Relogio, ResolvedorConfig, TenantContext

_HORARIOS = {
    Idioma.PT_BR: (
        "Segunda a sexta: 09:00 às 18:00",
        "Sábado: 09:00 às 13:00",
        "Domingo e feriados nacionais: fechado",
    ),
    Idioma.EN_US: (
        "Monday to Friday: 9:00 AM to 6:00 PM",
        "Saturday: 9:00 AM to 1:00 PM",
        "Sunday and national holidays: closed",
    ),
}
_DIAS = {
    Idioma.PT_BR: (
        "segunda-feira",
        "terça-feira",
        "quarta-feira",
        "quinta-feira",
        "sexta-feira",
        "sábado",
        "domingo",
    ),
    Idioma.EN_US: ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"),
}
_OBSERVACAO_HORARIO = (
    "Em datas especiais o horário pode ser estendido; esse dado não está disponível aqui (confirme com a equipe)."
)


class _Dto(BaseModel):
    model_config = ConfigDict(frozen=True)


class ContatosLoja(_Dto):
    telefone: str
    whatsapp: str
    email: str


class StatusLojaResultado(_Dto):
    """Situação da loja agora (hora de Campo Grande), horário semanal e contatos."""

    aberta: bool
    agora: str
    proxima_abertura: str
    proxima_abertura_dia: str
    fechamento_hoje: str | None = None
    motivo: str | None = None
    feriado: str | None = None
    horarios: tuple[str, ...]
    contatos: ContatosLoja
    observacao: str


class OpcaoFreteResultado(_Dto):
    modalidade: str
    prazo_min_dias_uteis: int
    prazo_max_dias_uteis: int


class FreteResultado(_Dto):
    """Frete calculado pela regra da loja. `valor` é `None` quando depende de cotação."""

    cidade: str | None = None
    em_campo_grande: bool = False
    valor: Montante | None = None
    gratis: bool = False
    requer_cotacao: bool = False
    motivo_cotacao: str | None = None
    opcoes: tuple[OpcaoFreteResultado, ...] = ()
    mensagem: str | None = None
    erro: str | None = None


class JanelaResultado(_Dto):
    tipo: str
    situacao: str
    prazo_final: date | None = None
    dias_restantes: int | None = None
    descricao: str


class AvaliacaoDevolucao(_Dto):
    """Janelas de arrependimento, troca, defeito e garantia legal em relação a `hoje`."""

    hoje: date
    janelas: tuple[JanelaResultado, ...] = ()
    erro: str | None = None


def _hora(instante_local: datetime) -> str:
    return instante_local.strftime("%H:%M")


class ServicoPoliticas:
    """Casos de uso de políticas da loja."""

    def __init__(self, relogio: Relogio, resolver_config: ResolvedorConfig) -> None:
        self._relogio = relogio
        self._config = resolver_config
        self._horario = HorarioLoja()

    def status_loja(self, ctx: TenantContext) -> StatusLojaResultado:
        """Loja aberta agora? Se não, motivo e próxima abertura; inclui horário semanal e contatos do tenant."""
        agora = self._relogio.agora().astimezone(FUSO_LOJA)
        status = self._horario.status(agora)
        contatos = self._config(ctx.tenant_id).contatos
        proxima = status.proxima_abertura
        return StatusLojaResultado(
            aberta=status.aberta,
            agora=agora.isoformat(timespec="minutes"),
            proxima_abertura=proxima.isoformat(timespec="minutes"),
            proxima_abertura_dia=_DIAS[ctx.idioma][proxima.weekday()],
            fechamento_hoje=None if status.fechamento_hoje is None else _hora(status.fechamento_hoje),
            motivo=None if status.motivo is None else status.motivo.value,
            feriado=status.nome_feriado,
            horarios=_HORARIOS[ctx.idioma],
            contatos=ContatosLoja(telefone=contatos.telefone, whatsapp=contatos.whatsapp, email=contatos.email),
            observacao=_OBSERVACAO_HORARIO,
        )

    def cotar_frete(
        self, ctx: TenantContext, cidade: str, total_centavos: int, *, instrumento_grande: bool = False
    ) -> FreteResultado:
        """Frete da entrega para `cidade` dado o total dos itens (preço vigente, em centavos)."""
        try:
            r: ResultadoFrete = calcular_frete(cidade, total_centavos, instrumento_grande=instrumento_grande)
        except ErroPoliticas as erro:
            return FreteResultado(erro=str(erro))
        return FreteResultado(
            cidade=r.cidade_normalizada,
            em_campo_grande=r.em_campo_grande,
            valor=None if r.valor_centavos is None else montante(Dinheiro(r.valor_centavos), ctx.idioma.value),
            gratis=r.gratis,
            requer_cotacao=r.requer_cotacao,
            motivo_cotacao=None if r.motivo_cotacao is None else r.motivo_cotacao.value,
            opcoes=tuple(
                OpcaoFreteResultado(
                    modalidade=o.modalidade.value,
                    prazo_min_dias_uteis=o.prazo_min_dias_uteis,
                    prazo_max_dias_uteis=o.prazo_max_dias_uteis,
                )
                for o in r.opcoes
            ),
            mensagem=r.mensagem,
        )

    def avaliar_devolucao(self, *, data_compra: date, status: str, data_recebimento: date | None) -> AvaliacaoDevolucao:
        """Janelas de devolução/troca/defeito/garantia de um pedido, com "hoje" vindo do `Relogio`."""
        hoje = self._relogio.agora().astimezone(FUSO_LOJA).date()
        try:
            janelas = _avaliar_janelas(
                data_compra=data_compra, status_pedido=status, data_recebimento=data_recebimento, hoje=hoje
            )
        except ErroPoliticas as erro:
            return AvaliacaoDevolucao(hoje=hoje, erro=str(erro))
        return AvaliacaoDevolucao(
            hoje=hoje,
            janelas=tuple(
                JanelaResultado(
                    tipo=j.tipo.value,
                    situacao=j.situacao.value,
                    prazo_final=j.prazo_final,
                    dias_restantes=j.dias_restantes,
                    descricao=j.descricao,
                )
                for j in janelas
            ),
        )

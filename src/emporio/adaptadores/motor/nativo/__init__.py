"""Motor nativo: `AgenteNativo` (roteador + prompt CAG + loop de tools) e o catálogo de mensagens fixas."""

from emporio.adaptadores.motor.nativo.agente import (
    MAX_ITERACOES_PADRAO,
    MAX_MENSAGENS_HISTORICO,
    TTL_SESSAO_S,
    AgenteNativo,
    FachadaTools,
)
from emporio.adaptadores.motor.nativo.mensagens import ChaveMensagem, mensagem_fixa

__all__ = [
    "MAX_ITERACOES_PADRAO",
    "MAX_MENSAGENS_HISTORICO",
    "TTL_SESSAO_S",
    "AgenteNativo",
    "ChaveMensagem",
    "FachadaTools",
    "mensagem_fixa",
]

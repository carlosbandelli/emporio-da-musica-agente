"""Mensagens fixas de erro da API (pt-BR e en-US). Não dependem de LLM, tenant nem dados: só constantes."""

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from emporio.portas import Idioma


class CodigoErro(StrEnum):
    """Códigos estáveis dos erros da API (campo `codigo` do corpo)."""

    TENANT_AUSENTE = "tenant_ausente"
    TENANT_DESCONHECIDO = "tenant_desconhecido"
    INDISPONIVEL = "indisponivel"
    REQUISICAO_INVALIDA = "requisicao_invalida"


_TEXTOS: Final[Mapping[tuple[CodigoErro, Idioma], str]] = MappingProxyType(
    {
        (CodigoErro.TENANT_AUSENTE, Idioma.PT_BR): (
            "Não consegui identificar a loja. Envie o cabeçalho X-Tenant-ID ou acesse pelo endereço da loja."
        ),
        (CodigoErro.TENANT_AUSENTE, Idioma.EN_US): (
            "I could not identify the store. Send the X-Tenant-ID header or use the store's own address."
        ),
        (CodigoErro.TENANT_DESCONHECIDO, Idioma.PT_BR): "Loja não encontrada.",
        (CodigoErro.TENANT_DESCONHECIDO, Idioma.EN_US): "Store not found.",
        (CodigoErro.INDISPONIVEL, Idioma.PT_BR): (
            "O atendimento está fora do ar no momento. Tente novamente em alguns instantes."
        ),
        (CodigoErro.INDISPONIVEL, Idioma.EN_US): "Customer service is offline at the moment. Please try again shortly.",
        (CodigoErro.REQUISICAO_INVALIDA, Idioma.PT_BR): (
            "Requisição inválida. Envie session_id e mensagem em texto (idioma, se informado, é pt-BR ou en-US)."
        ),
        (CodigoErro.REQUISICAO_INVALIDA, Idioma.EN_US): (
            "Invalid request. Send session_id and a text mensagem (idioma, if given, is pt-BR or en-US)."
        ),
    }
)


def mensagem_erro(codigo: CodigoErro, idioma: Idioma) -> str:
    """Texto fixo do erro no idioma pedido."""
    return _TEXTOS[(codigo, idioma)]

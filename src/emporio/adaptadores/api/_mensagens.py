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
    DADOS_INVALIDOS = "dados_invalidos"
    NAO_AUTENTICADO = "nao_autenticado"
    TOKEN_INVALIDO = "token_invalido"
    SEM_PERMISSAO = "sem_permissao"
    TENANT_DIVERGENTE = "tenant_divergente"
    AUTENTICACAO_INDISPONIVEL = "autenticacao_indisponivel"
    SOLICITACAO_NAO_ENCONTRADA = "solicitacao_nao_encontrada"
    TRANSICAO_INVALIDA = "transicao_invalida"


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
        (CodigoErro.DADOS_INVALIDOS, Idioma.PT_BR): "Requisição inválida. Confira os campos e os valores enviados.",
        (CodigoErro.DADOS_INVALIDOS, Idioma.EN_US): "Invalid request. Check the fields and values you sent.",
        (CodigoErro.NAO_AUTENTICADO, Idioma.PT_BR): (
            "Autenticação necessária. Envie o cabeçalho Authorization: Bearer <token>."
        ),
        (CodigoErro.NAO_AUTENTICADO, Idioma.EN_US): "Authentication required. Send Authorization: Bearer <token>.",
        (CodigoErro.TOKEN_INVALIDO, Idioma.PT_BR): "Token inválido ou expirado. Gere um novo token.",
        (CodigoErro.TOKEN_INVALIDO, Idioma.EN_US): "Invalid or expired token. Issue a new token.",
        (CodigoErro.SEM_PERMISSAO, Idioma.PT_BR): "Seu papel não tem permissão para esta operação.",
        (CodigoErro.SEM_PERMISSAO, Idioma.EN_US): "Your role is not allowed to perform this operation.",
        (CodigoErro.TENANT_DIVERGENTE, Idioma.PT_BR): "O token não pertence a esta loja.",
        (CodigoErro.TENANT_DIVERGENTE, Idioma.EN_US): "The token does not belong to this store.",
        (CodigoErro.AUTENTICACAO_INDISPONIVEL, Idioma.PT_BR): (
            "A área autenticada está indisponível no momento (autenticação não configurada)."
        ),
        (CodigoErro.AUTENTICACAO_INDISPONIVEL, Idioma.EN_US): (
            "The authenticated area is unavailable right now (authentication not configured)."
        ),
        (CodigoErro.SOLICITACAO_NAO_ENCONTRADA, Idioma.PT_BR): "Solicitação não encontrada nesta loja.",
        (CodigoErro.SOLICITACAO_NAO_ENCONTRADA, Idioma.EN_US): "Request not found in this store.",
        (CodigoErro.TRANSICAO_INVALIDA, Idioma.PT_BR): "Essa mudança de status não é permitida.",
        (CodigoErro.TRANSICAO_INVALIDA, Idioma.EN_US): "This status change is not allowed.",
    }
)


def mensagem_erro(codigo: CodigoErro, idioma: Idioma) -> str:
    """Texto fixo do erro no idioma pedido."""
    return _TEXTOS[(codigo, idioma)]

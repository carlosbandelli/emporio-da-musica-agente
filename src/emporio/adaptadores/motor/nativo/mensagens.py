"""Catálogo de mensagens fixas do motor nativo (arquitetura §10), em pt-BR e en-US, no tom da Sônia.

Não dependem de LLM: funcionam com todos os provedores fora. Os contatos vêm da configuração do tenant, nunca
escritos aqui. Só constantes imutáveis no módulo.
"""

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from emporio.portas.contexto import Idioma
from emporio.portas.tenant import Contatos


class ChaveMensagem(StrEnum):
    """Situações que têm resposta fixa."""

    VAZIA = "vazia"
    LONGA = "longa"
    LIMITE = "limite"
    INDISPONIVEL = "indisponivel"
    DEADLINE = "deadline"
    ERRO = "erro"
    LIMITE_ITERACOES = "limite_iteracoes"
    VALOR_NAO_VERIFICADO = "valor_nao_verificado"


_PT: Final = Idioma.PT_BR
_EN: Final = Idioma.EN_US

_TEXTOS: Final[Mapping[tuple[ChaveMensagem, Idioma], str]] = MappingProxyType(
    {
        (ChaveMensagem.VAZIA, _PT): (
            "Oi! Acho que sua mensagem chegou em branco. Me conta: como posso te ajudar? Posso falar de instrumentos, "
            "pedidos ou das políticas da loja."
        ),
        (ChaveMensagem.VAZIA, _EN): (
            "Hi! Looks like your message came through empty. How can I help? I can talk about instruments, orders "
            "or store policies."
        ),
        (ChaveMensagem.LONGA, _PT): (
            "Opa, sua mensagem ficou longa demais pra mim (o limite é de {limite} caracteres). Pode resumir ou mandar "
            "em partes?"
        ),
        (ChaveMensagem.LONGA, _EN): (
            "Oops, your message is a bit too long for me (the limit is {limite} characters). Could you shorten it or "
            "send it in parts?"
        ),
        (ChaveMensagem.LIMITE, _PT): (
            "Calma, que eu ainda estou dando conta das suas mensagens! Espera uns segundinhos e manda de novo, "
            "por favor."
        ),
        (ChaveMensagem.LIMITE, _EN): (
            "Hang on, I'm still catching up with your messages! Please wait a few seconds and send it again."
        ),
        (ChaveMensagem.INDISPONIVEL, _PT): (
            "Poxa, estou com uma instabilidade e não consigo responder agora. Tenta de novo daqui a pouquinho?"
        ),
        (ChaveMensagem.INDISPONIVEL, _EN): (
            "Sorry, I'm having some trouble and can't answer right now. Could you try again in a little while?"
        ),
        (ChaveMensagem.DEADLINE, _PT): (
            "Desculpa, essa consulta demorou mais do que deveria. Pode mandar a pergunta de novo?"
        ),
        (ChaveMensagem.DEADLINE, _EN): (
            "Sorry, that took longer than it should have. Could you send your question again?"
        ),
        (ChaveMensagem.ERRO, _PT): "Desculpa, tive um problema aqui para responder. Pode tentar de novo?",
        (ChaveMensagem.ERRO, _EN): "Sorry, something went wrong on my side. Could you try again?",
        (ChaveMensagem.LIMITE_ITERACOES, _PT): (
            "Essa ficou mais enrolada do que eu consigo resolver por aqui agora. Pode me perguntar por partes?"
        ),
        (ChaveMensagem.LIMITE_ITERACOES, _EN): (
            "That one got more tangled than I can sort out here right now. Could you ask me one part at a time?"
        ),
        (ChaveMensagem.VALOR_NAO_VERIFICADO, _PT): (
            "Não consegui confirmar os valores no sistema agora e prefiro não te passar um número errado. Pode "
            "perguntar de novo em instantes?"
        ),
        (ChaveMensagem.VALOR_NAO_VERIFICADO, _EN): (
            "I couldn't confirm the amounts in our system right now, and I'd rather not give you a wrong number. "
            "Could you ask again in a moment?"
        ),
    }
)

_COM_CONTATO: Final = frozenset(
    {
        ChaveMensagem.INDISPONIVEL,
        ChaveMensagem.DEADLINE,
        ChaveMensagem.ERRO,
        ChaveMensagem.LIMITE_ITERACOES,
        ChaveMensagem.VALOR_NAO_VERIFICADO,
    }
)
"""Falhas do lado da loja: oferecem o WhatsApp da equipe humana."""

_SUFIXO_CONTATO: Final[Mapping[Idioma, str]] = MappingProxyType(
    {
        _PT: " Se preferir, fale com a nossa equipe pelo WhatsApp {whatsapp}.",
        _EN: " If you prefer, you can reach our team on WhatsApp at {whatsapp}.",
    }
)


def mensagem_fixa(
    chave: ChaveMensagem,
    idioma: Idioma,
    *,
    contatos: Contatos | None = None,
    limite_caracteres: int | None = None,
) -> str:
    """Texto fixo para a situação, no idioma pedido (pt-BR se o idioma não tiver tradução).

    `contatos` acrescenta o WhatsApp do tenant nas falhas do lado da loja; sem contatos (ex.: tenant desconhecido),
    a mensagem sai sem ele. `limite_caracteres` preenche a mensagem `LONGA`.
    """
    texto = _TEXTOS.get((chave, idioma)) or _TEXTOS[(chave, _PT)]
    if chave is ChaveMensagem.LONGA:
        texto = texto.format(limite=limite_caracteres if limite_caracteres is not None else "?")
    if contatos is not None and chave in _COM_CONTATO:
        sufixo = _SUFIXO_CONTATO.get(idioma) or _SUFIXO_CONTATO[_PT]
        texto += sufixo.format(whatsapp=contatos.whatsapp)
    return texto

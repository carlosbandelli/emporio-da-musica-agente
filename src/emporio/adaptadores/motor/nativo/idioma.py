"""Idioma da sessão (arquitetura §10): heurística leve pt-BR × en-US e pedido explícito de troca.

Sem modelo de linguagem nem dependência externa: listas de palavras frequentes e distintivas de cada idioma
(palavras que existem nos dois, como "do", "no", "a", "me", "ok", ficam de fora). Funções puras.
"""

import re
import unicodedata
from collections.abc import Collection, Mapping
from types import MappingProxyType
from typing import Final

from emporio.portas.contexto import Idioma


def _palavras(lista: str) -> frozenset[str]:
    return frozenset(lista.split())


_PALAVRAS_PT: Final = _palavras(
    """
    oi ola bom boa dia tarde noite tudo bem obrigado obrigada valeu brigado quanto quanta quantos custa custam preco
    precos valor voce voces vc vcs tem teria tenho quero queria gostaria posso pode podem poderia qual quais onde
    quando como porque que isso esse essa este esta estao aberta aberto fechada loja pedido pedidos compra comprar
    comprei devolver devolucao troca trocar garantia frete entrega parcelar parcelado parcelas vezes desconto
    violao violoes guitarra guitarras baixo baixos teclado teclados bateria baterias instrumento instrumentos
    endereco horario ate para pra com sem por favor nao sim meu minha minhas meus uma um uns umas de da das dos
    na nas nos ao aos mais muito obg sobre tambem ainda agora hoje amanha
    """
)

_PALAVRAS_EN: Final = _palavras(
    """
    hi hello hey good morning afternoon evening thanks thank you your price prices cost costs how much many does
    have has want would like could should can which what where when why who is are was this that these those open
    closed store shop order orders buy bought purchase return refund exchange warranty shipping delivery
    installments discount guitar guitars acoustic bass keyboard keyboards drums instrument instruments address
    hours the and for with without please not yes my mine an of to in any some more very about also still now
    today tomorrow there
    """
)

_RE_PALAVRA: Final = re.compile(r"[a-z]+")

_IDIOMA_DO_NOME: Final[Mapping[str, Idioma]] = MappingProxyType(
    {"english": Idioma.EN_US, "ingles": Idioma.EN_US, "portugues": Idioma.PT_BR, "portuguese": Idioma.PT_BR}
)
_NOMES: Final = "english|ingles|portugues|portuguese"

_RE_SO_IDIOMA: Final = re.compile(
    rf"^\W*(?:(?:in|em)\W+)?({_NOMES})(?:\W+(?:please|pls|por favor|pf))?\W*$",
)
"""A mensagem é só o nome do idioma ("English", "em português, por favor")."""

_RE_PEDIDO_TROCA: Final = re.compile(
    r"\b(?:speak|talk|write|reply|answer|respond|continue|switch|change|swap|prefer|use|"
    r"falar|(?<!\bse )fala|falamos|conversar|escrever|escreve|responder|responda|responde|atender|atende|mudar|muda|"
    r"trocar|troca|prefiro|pode ser|podemos|usar)\b"
    rf"(?:\W+\w+){{0,3}}?\W+({_NOMES})\b"
)
"""Verbo de pedido seguido (em até 3 palavras) do nome do idioma: "can we speak English?", "pode ser em português?".

"como se fala X em inglês?" e "how do you say X in Portuguese?" não são pedido de troca.
"""


def normalizar(texto: str) -> str:
    """Minúsculas e sem acentos."""
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return sem_acento.lower()


def pedido_de_troca(texto: str) -> Idioma | None:
    """Idioma pedido explicitamente pelo cliente, ou `None`."""
    normal = normalizar(texto)
    achado = _RE_SO_IDIOMA.match(normal) or _RE_PEDIDO_TROCA.search(normal)
    return _IDIOMA_DO_NOME[achado.group(1)] if achado else None


def detectar_idioma(texto: str) -> Idioma | None:
    """pt-BR ou en-US pela contagem de palavras distintivas; empate (inclusive nenhuma) = `None` (ambíguo)."""
    palavras = _RE_PALAVRA.findall(normalizar(texto))
    pt = sum(1 for p in palavras if p in _PALAVRAS_PT)
    en = sum(1 for p in palavras if p in _PALAVRAS_EN)
    if pt > en:
        return Idioma.PT_BR
    if en > pt:
        return Idioma.EN_US
    return None


def resolver_idioma(texto: str, idioma_sessao: Idioma | None, suportados: Collection[Idioma]) -> Idioma | None:
    """Idioma a gravar na sessão depois desta mensagem (`None` = ainda indefinido).

    Ordem: pedido explícito de troca (se suportado) > idioma já definido na sessão > detecção na mensagem (enquanto
    a sessão não tem idioma, cada mensagem tenta de novo). Idioma não suportado pelo tenant é ignorado.
    """
    pedido = pedido_de_troca(texto)
    if pedido is not None and pedido in suportados:
        return pedido
    if idioma_sessao is not None:
        return idioma_sessao
    detectado = detectar_idioma(texto)
    return detectado if detectado is not None and detectado in suportados else None

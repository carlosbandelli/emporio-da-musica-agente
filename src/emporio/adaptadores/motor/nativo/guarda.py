"""Guarda de valores monetários (invariante I6, reforço de "o LLM redige, o código calcula").

Antes de devolver a resposta final, todo valor em dinheiro do texto (`R$ 1.299,00`, `R$1,299.00`, `$183.25`,
`R$ 1299`) precisa ter vindo de uma destas fontes:

- resultado de tool da sessão (o texto formatado e os campos `"centavos"` do JSON);
- mensagem do próprio cliente (para ecoar o orçamento: "violões até 1000" → "até R$ 1.000,00");
- manual de políticas do tenant (R$ 500, R$ 35, R$ 80, R$ 100, R$ 2.000, ...).

Respostas anteriores da assistente e notas internas não contam. Valores sem símbolo de moeda não são verificados
(limitação documentada). Funções puras; só constantes no módulo.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Final

from emporio.portas.llm import MensagemLLM, PapelMensagem

NOTA_INTERNA_PREFIXO: Final = "[nota interna do sistema, não é mensagem do cliente]"
"""Prefixo das mensagens `USER` que o motor acrescenta (rodada corretiva); o corte de histórico as reconhece."""

_DIGITOS_MILHAR: Final = 3
_CASAS_DECIMAIS: Final = (1, 2)
_NUMERO: Final = r"\d[\d.,]*\d|\d"
_RE_MOEDA: Final = re.compile(rf"(?:R\$|\$)\s*({_NUMERO})")
_RE_NUMERO: Final = re.compile(_NUMERO)
_RE_CENTAVOS_JSON: Final = re.compile(r'"centavos"\s*:\s*(\d+)')


@dataclass(frozen=True, slots=True)
class ValorMonetario:
    """Valor encontrado num texto: como apareceu e em centavos."""

    texto: str
    centavos: int


def centavos_de(numero: str) -> int | None:
    """Converte `1.299,00`, `1,299.00`, `1299`, `2.000` ou `80,5` em centavos; formato inválido → `None`.

    Com `.` e `,` juntos, o último é o separador decimal. Com um só tipo: uma ocorrência seguida de 1 ou 2 dígitos é
    decimal; grupos de 3 dígitos são milhar.
    """
    s = numero
    if "," in s and "." in s:
        decimal = "," if s.rfind(",") > s.rfind(".") else "."
        milhar = "." if decimal == "," else ","
        inteiro, _, fracao = s.rpartition(decimal)
        if decimal in inteiro or not all(len(g) == _DIGITOS_MILHAR for g in inteiro.split(milhar)[1:]):
            return None
        s = inteiro.replace(milhar, "") + "." + fracao
    elif "," in s or "." in s:
        separador = "," if "," in s else "."
        partes = s.split(separador)
        if len(partes) == 2 and len(partes[1]) in _CASAS_DECIMAIS:  # noqa: PLR2004 - parte inteira + decimal
            s = f"{partes[0]}.{partes[1]}"
        elif all(len(p) == _DIGITOS_MILHAR for p in partes[1:]):
            s = "".join(partes)
        else:
            return None
    try:
        reais = Decimal(s)
    except InvalidOperation:
        return None
    return int((reais * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def valores_monetarios(texto: str) -> tuple[ValorMonetario, ...]:
    """Valores com símbolo de moeda (`R$` ou `$`) no texto, na ordem."""
    saida: list[ValorMonetario] = []
    for achado in _RE_MOEDA.finditer(texto):
        centavos = centavos_de(achado.group(1))
        if centavos is not None:
            saida.append(ValorMonetario(achado.group(0), centavos))
    return tuple(saida)


def eh_nota_interna(mensagem: MensagemLLM) -> bool:
    """Mensagem `USER` acrescentada pelo motor (não escrita pelo cliente)."""
    return mensagem.role is PapelMensagem.USER and mensagem.content.startswith(NOTA_INTERNA_PREFIXO)


def valores_permitidos(mensagens: Iterable[MensagemLLM], manual: str) -> frozenset[int]:
    """Centavos que a resposta pode citar: tools da sessão, falas do cliente (inclusive números sem `R$`) e manual."""
    permitidos = {v.centavos for v in valores_monetarios(manual)}
    for m in mensagens:
        if m.role is PapelMensagem.TOOL:
            permitidos.update(v.centavos for v in valores_monetarios(m.content))
            permitidos.update(int(c) for c in _RE_CENTAVOS_JSON.findall(m.content))
        elif m.role is PapelMensagem.USER and not eh_nota_interna(m):
            numeros = (centavos_de(n) for n in _RE_NUMERO.findall(m.content))
            permitidos.update(n for n in numeros if n is not None)
    return frozenset(permitidos)


def valores_nao_verificados(texto: str, permitidos: frozenset[int]) -> tuple[str, ...]:
    """Valores do texto que não vieram de nenhuma fonte permitida (sem repetição, na ordem)."""
    vistos: dict[str, None] = {}
    for valor in valores_monetarios(texto):
        if valor.centavos not in permitidos:
            vistos.setdefault(valor.texto.strip(), None)
    return tuple(vistos)


def nota_correcao(valores: Iterable[str]) -> str:
    """Nota interna da rodada corretiva (em pt-BR: é instrução para o modelo, não texto para o cliente)."""
    lista = ", ".join(valores)
    return (
        f"{NOTA_INTERNA_PREFIXO} Na sua última resposta, estes valores não vieram de nenhuma ferramenta desta "
        f"conversa nem do manual: {lista}. Consulte a ferramenta adequada ou reescreva a resposta sem esses valores. "
        "Responda ao cliente normalmente, sem mencionar esta nota."
    )

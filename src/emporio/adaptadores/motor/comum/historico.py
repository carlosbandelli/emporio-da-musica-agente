"""Limite do histórico da sessão: corta turnos inteiros, os mais antigos primeiro.

Um turno começa numa mensagem `USER` do cliente (não numa nota interna) que vem no início do histórico ou logo
após uma resposta final da assistente (`ASSISTANT` sem `tool_calls`). Cortar só nesses pontos garante que nenhum
`TOOL` fica sem o `tool_use` correspondente.

Ao cortar, o `bruto` (raciocínio do Claude, `thought_signature` do Gemini) sai das mensagens mantidas: esses blocos
são vinculados ao prefixo exato da conversa, que deixou de existir. Sem eles o histórico continua válido em todos os
provedores (premissa #47); o custo é um cache miss na rodada seguinte ao corte.
"""

import dataclasses
from collections.abc import Sequence

from emporio.adaptadores.motor.comum.guarda import eh_nota_interna
from emporio.portas.llm import MensagemLLM, PapelMensagem


def inicio_de_turno(historico: Sequence[MensagemLLM], indice: int) -> bool:
    """`True` se `historico[indice]` abre um turno do cliente."""
    m = historico[indice]
    if m.role is not PapelMensagem.USER or eh_nota_interna(m):
        return False
    if indice == 0:
        return True
    anterior = historico[indice - 1]
    return anterior.role is PapelMensagem.ASSISTANT and not anterior.tool_calls


def cortar_historico(historico: Sequence[MensagemLLM], maximo: int) -> tuple[MensagemLLM, ...]:
    """Mantém no máximo `maximo` mensagens cortando turnos inteiros do começo.

    Se nem o último turno cabe, mantém o último turno inteiro (nunca corta no meio). Sem corte, devolve intacto.
    """
    if len(historico) <= maximo:
        return tuple(historico)
    inicios = [i for i in range(len(historico)) if inicio_de_turno(historico, i)]
    corte = next((i for i in inicios if len(historico) - i <= maximo), inicios[-1] if inicios else 0)
    if corte == 0:
        return tuple(historico)
    return tuple(dataclasses.replace(m, bruto=None) if m.bruto is not None else m for m in historico[corte:])

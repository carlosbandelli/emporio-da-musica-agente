"""Fábrica dos provedores de LLM, já embrulhados na resiliência. Chamada pelo composition root (task 1.10)."""

import asyncio
import time

from emporio.adaptadores.llm.claude import ProvedorClaude, cliente_claude
from emporio.adaptadores.llm.gemini import ProvedorGemini, cliente_gemini
from emporio.adaptadores.llm.groq import ProvedorGroq, cliente_groq
from emporio.adaptadores.llm.resiliencia import PoliticaResiliencia, ProvedorResiliente, RelogioMonotonic, Sono
from emporio.configuracao.settings import Settings
from emporio.portas.estado import CircuitoStore
from emporio.portas.llm import LLMProvider

POLITICAS_PADRAO: dict[str, PoliticaResiliencia] = {
    "claude": PoliticaResiliencia(max_concorrentes=5),
    "gemini": PoliticaResiliencia(max_concorrentes=10),
    "groq": PoliticaResiliencia(max_concorrentes=10),
}
"""Bulkhead por provedor (arquitetura §5.2); demais valores são os padrões de `PoliticaResiliencia`."""


def criar_provedores(
    settings: Settings,
    circuito: CircuitoStore,
    *,
    sleep: Sono = asyncio.sleep,
    relogio_monotonic: RelogioMonotonic = time.monotonic,
) -> dict[str, LLMProvider]:
    """Um `ProvedorResiliente` por provedor **com chave** (`settings.provedores_com_chave()`); sem chave, ausente.

    Cada chamada cria clientes e semáforos novos: chame uma vez por processo (o bulkhead é por instância).
    """
    provedores: dict[str, LLMProvider] = {}
    for nome in settings.provedores_com_chave():
        interno = _interno(nome, settings)
        provedores[nome] = ProvedorResiliente(
            interno, circuito, POLITICAS_PADRAO[nome], sleep=sleep, relogio_monotonic=relogio_monotonic
        )
    return provedores


def _interno(nome: str, s: Settings) -> LLMProvider:
    if nome == "claude" and s.anthropic_api_key is not None:
        return ProvedorClaude(
            cliente_claude(s.anthropic_api_key.get_secret_value()),
            modelo=s.modelo_claude,
            esforco=s.esforco_claude,
            fallback_servidor=s.claude_fallback_servidor,
        )
    if nome == "gemini" and s.google_api_key is not None:
        return ProvedorGemini(cliente_gemini(s.google_api_key.get_secret_value()), modelo=s.modelo_gemini)
    if nome == "groq" and s.groq_api_key is not None:
        return ProvedorGroq(
            cliente_groq(s.groq_api_key.get_secret_value()), modelo=s.modelo_groq, esforco=s.esforco_groq
        )
    raise ValueError(f"provedor de LLM desconhecido ou sem chave: {nome!r}")

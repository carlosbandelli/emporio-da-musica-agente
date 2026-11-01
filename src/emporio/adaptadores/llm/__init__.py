"""Adaptadores de LLM (groq, gemini, claude e falso para testes) e a camada de resiliência."""

from emporio.adaptadores.llm.claude import ProvedorClaude, cliente_claude
from emporio.adaptadores.llm.fabrica import POLITICAS_PADRAO, criar_provedores
from emporio.adaptadores.llm.falso import (
    ItemRoteiro,
    LLMFalso,
    RoteiroEsgotado,
    chamar_tool,
    chamar_tools,
    responder_texto,
)
from emporio.adaptadores.llm.gemini import ProvedorGemini, cliente_gemini
from emporio.adaptadores.llm.groq import ProvedorGroq, cliente_groq
from emporio.adaptadores.llm.resiliencia import PoliticaResiliencia, ProvedorResiliente

__all__ = [
    "POLITICAS_PADRAO",
    "ItemRoteiro",
    "LLMFalso",
    "PoliticaResiliencia",
    "ProvedorClaude",
    "ProvedorGemini",
    "ProvedorGroq",
    "ProvedorResiliente",
    "RoteiroEsgotado",
    "chamar_tool",
    "chamar_tools",
    "cliente_claude",
    "cliente_gemini",
    "cliente_groq",
    "criar_provedores",
    "responder_texto",
]

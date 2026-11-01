"""Portas (contratos Protocol) e tipos compartilhados, como TenantContext."""

from emporio.portas.contexto import Idioma, Papel, TenantContext
from emporio.portas.estado import (
    CircuitoStore,
    EstadoCircuito,
    EstadoSessao,
    RateLimiter,
    Relogio,
    ResultadoLimite,
    SessaoStore,
)
from emporio.portas.llm import (
    ChamadaTool,
    DefinicaoTool,
    ErroLLM,
    ErroLLMPermanente,
    ErroLLMTransitorio,
    LLMProvider,
    MensagemLLM,
    PapelMensagem,
    RequisicaoLLM,
    RespostaLLM,
    UsoTokens,
)
from emporio.portas.motor import (
    Diagnostico,
    DiagnosticoInfo,
    MensagemCliente,
    MotorDeAtendimento,
    RespostaAtendimento,
)
from emporio.portas.repositorios import Catalogo, Clientes, FiltroProdutos, Pedidos, Solicitacoes
from emporio.portas.tenant import ConfigTenant

__all__ = [
    "Catalogo",
    "ChamadaTool",
    "CircuitoStore",
    "Clientes",
    "ConfigTenant",
    "DefinicaoTool",
    "Diagnostico",
    "DiagnosticoInfo",
    "ErroLLM",
    "ErroLLMPermanente",
    "ErroLLMTransitorio",
    "EstadoCircuito",
    "EstadoSessao",
    "FiltroProdutos",
    "Idioma",
    "LLMProvider",
    "MensagemCliente",
    "MensagemLLM",
    "MotorDeAtendimento",
    "Papel",
    "PapelMensagem",
    "Pedidos",
    "RateLimiter",
    "Relogio",
    "RequisicaoLLM",
    "RespostaAtendimento",
    "RespostaLLM",
    "ResultadoLimite",
    "SessaoStore",
    "Solicitacoes",
    "TenantContext",
    "UsoTokens",
]

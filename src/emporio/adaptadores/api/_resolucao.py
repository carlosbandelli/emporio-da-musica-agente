"""Resolução de tenant (header ou subdomínio) e de idioma (corpo ou `Accept-Language`). Funções puras."""

import ipaddress
import re
from typing import Final

from emporio.portas import Idioma

MIN_ROTULOS_SUBDOMINIO: Final = 3
"""`emporio.exemplo.com` tem 3 rótulos: o primeiro é o tenant. `exemplo.com` (2) não tem subdomínio."""
SUBDOMINIO_LOCALHOST: Final = 2
_PORTA: Final = re.compile(r":\d+$")


def tenant_do_host(host: str | None) -> str | None:
    """Tenant pelo subdomínio do `Host`; `None` se o host não tem subdomínio.

    Regras: ignora a porta; o primeiro rótulo é o tenant quando há pelo menos 3 rótulos (`emporio.api.exemplo.com` e
    `emporio.exemplo.com` → `emporio`). Extensão para desenvolvimento: `emporio.localhost` (2 rótulos, terminando em
    `localhost`) → `emporio`. `localhost`, nomes de um rótulo (ex.: `testserver`, nomes de serviço do Docker) e
    endereços IP (v4 ou v6) nunca têm subdomínio.
    """
    nome = (host or "").strip().lower()
    if not nome or nome.startswith("["):  # vazio, ou IPv6 literal entre colchetes
        return None
    nome = _PORTA.sub("", nome).rstrip(".")
    if _eh_ip(nome):
        return None
    rotulos = [r for r in nome.split(".") if r]
    if len(rotulos) >= MIN_ROTULOS_SUBDOMINIO or (len(rotulos) == SUBDOMINIO_LOCALHOST and rotulos[-1] == "localhost"):
        return rotulos[0]
    return None


def _eh_ip(nome: str) -> bool:
    try:
        ipaddress.ip_address(nome)
    except ValueError:
        return False
    return True


def idioma_do_accept_language(cabecalho: str | None) -> Idioma | None:
    """Primeiro idioma suportado do `Accept-Language`, respeitando `q`; `pt*` → pt-BR, `en*` → en-US."""
    if not cabecalho:
        return None
    candidatos: list[tuple[float, int, Idioma]] = []
    for posicao, item in enumerate(cabecalho.split(",")):
        tag, *parametros = (parte.strip() for parte in item.split(";"))
        qualidade = 1.0
        for parametro in parametros:
            if parametro.startswith("q="):
                try:
                    qualidade = float(parametro[2:])
                except ValueError:
                    qualidade = 0.0
        idioma = _idioma_da_tag(tag)
        if idioma is not None and qualidade > 0:
            candidatos.append((-qualidade, posicao, idioma))
    return min(candidatos)[2] if candidatos else None


def _idioma_da_tag(tag: str) -> Idioma | None:
    primario = tag.lower().split("-")[0]
    if primario == "pt":
        return Idioma.PT_BR
    if primario == "en":
        return Idioma.EN_US
    return None

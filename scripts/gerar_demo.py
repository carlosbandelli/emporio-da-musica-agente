"""Gera o GIF e o print do README a partir das conversas reais gravadas em `exemplos/`.

As respostas são as que o LLM real devolveu na gravação (`emporio chat --gravar`); este script só desenha o terminal,
com a mesma aparência da CLI, para mostrar o agente funcionando sem gastar chamadas novas de API.

Uso (não é dependência do projeto):
    uv run --with pillow python scripts/gerar_demo.py
Saída: docs/imagens/demo-chat.gif e docs/imagens/print-chat.png
"""

from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

RAIZ = Path(__file__).resolve().parents[1]
SAIDA = RAIZ / "docs" / "imagens"
FONTE = Path("C:/Windows/Fonts/consola.ttf")
FONTE_NEGRITO = Path("C:/Windows/Fonts/consolab.ttf")

LARGURA, ALTURA = 1100, 680
MARGEM, TAM_FONTE, ENTRELINHA = 22, 17, 24
COLUNAS = 96
FUNDO, TEXTO, APAGADO = (24, 26, 33), (220, 223, 228), (120, 126, 138)
VERDE, AZUL, AMARELO, BARRA = (126, 211, 133), (120, 180, 255), (240, 200, 110), (44, 47, 58)


@dataclass(frozen=True)
class Linha:
    texto: str
    cor: tuple[int, int, int] = TEXTO
    negrito: bool = False


@dataclass(frozen=True)
class Turno:
    cliente: str
    sonia: str
    meta: str


def ler_conversa(arquivo: Path) -> tuple[dict[str, str], list[Turno]]:
    """Lê cabeçalho e turnos do Markdown gravado pela CLI (`--gravar`)."""
    texto = arquivo.read_text(encoding="utf-8")
    cabecalho = dict(re.findall(r"- \*\*(.+?):\*\* (.+)", texto))
    turnos = []
    # A linha de metadados ocupa a linha toda (o nome da tool tem "_", então não dá para parar no primeiro "_").
    padrao = r"\*\*Cliente:\*\* (.+?)\n\n\*\*Sônia:\*\* (.+?)\n\n_([^\n]+)_\n"
    for cliente, sonia, meta in re.findall(padrao, texto, re.S):
        sem_emoji = re.sub(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]", "", sonia).replace("**", "")
        turnos.append(Turno(cliente.strip(), sem_emoji.replace("\u2011", "-").strip(), meta.strip()))
    return cabecalho, turnos


def quebrar(texto: str, prefixo: str = "") -> list[str]:
    linhas: list[str] = []
    for paragrafo in texto.split("\n"):
        if not paragrafo.strip():
            linhas.append("")
            continue
        linhas.extend(textwrap.wrap(paragrafo, COLUNAS - len(prefixo)) or [""])
    return linhas


def desenhar(linhas: list[Linha], titulo: str) -> Image.Image:
    img = Image.new("RGB", (LARGURA, ALTURA), FUNDO)
    d = ImageDraw.Draw(img)
    fonte = ImageFont.truetype(str(FONTE), TAM_FONTE)
    negrito = ImageFont.truetype(str(FONTE_NEGRITO), TAM_FONTE)
    d.rectangle([0, 0, LARGURA, 34], fill=BARRA)
    for i, cor in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
        d.ellipse([16 + i * 22, 11, 28 + i * 22, 23], fill=cor)
    d.text((LARGURA // 2, 17), titulo, font=fonte, fill=APAGADO, anchor="mm")
    visiveis = (ALTURA - 34 - MARGEM) // ENTRELINHA
    y = 34 + MARGEM // 2
    for linha in linhas[-visiveis:]:
        d.text((MARGEM, y), linha.texto, font=negrito if linha.negrito else fonte, fill=linha.cor)
        y += ENTRELINHA
    return img


def cabecalho_cli(cab: dict[str, str], comando: str) -> list[Linha]:
    idioma = cab.get("Idioma", "pt-BR")
    data = cab.get("Data de referência (simulada)", "")[:10]
    provedores = cab.get("Provedores com chave", "")
    return [
        Linha(f"$ {comando}", VERDE, negrito=True),
        Linha("Empório da Música - atendimento com Sônia", AZUL, negrito=True),
        Linha(
            f"Tenant: emporio | idioma: {idioma} | motor: nativo | provedores: {provedores} | referência: {data}",
            APAGADO,
        ),
        Linha("Digite 'sair' (ou Ctrl+C) para encerrar.", APAGADO),
        Linha(""),
    ]


def quadros_conversa(arquivo: Path, comando: str, titulo: str) -> tuple[list[Image.Image], list[int]]:
    cab, turnos = ler_conversa(arquivo)
    base = cabecalho_cli(cab, comando)
    quadros: list[Image.Image] = [desenhar(base, titulo)]
    duracoes = [1400]
    for t in turnos:
        for i in range(0, len(t.cliente) + 1, 3):  # digitação do cliente
            quadros.append(desenhar([*base, Linha("Você: " + t.cliente[:i], AMARELO, negrito=True)], titulo))
            duracoes.append(45)
        base = [*base, Linha("Você: " + t.cliente, AMARELO, negrito=True)]
        quadros.append(desenhar([*base, Linha("Sônia está digitando...", APAGADO)], titulo))
        duracoes.append(900)
        resposta = quebrar(t.sonia)
        for n in range(1, len(resposta) + 1):  # resposta aparece linha a linha
            parcial = [Linha(("Sônia: " if k == 0 else "       ") + r, TEXTO) for k, r in enumerate(resposta[:n])]
            quadros.append(desenhar([*base, *parcial], titulo))
            duracoes.append(160)
        base = [
            *base,
            *[Linha(("Sônia: " if k == 0 else "       ") + r) for k, r in enumerate(resposta)],
            Linha(f"       ({t.meta})", APAGADO),
            Linha(""),
        ]
        quadros.append(desenhar(base, titulo))
        duracoes.append(2600)
    return quadros, duracoes


def main() -> None:
    SAIDA.mkdir(parents=True, exist_ok=True)
    titulo = "emporio chat  ·  respostas reais gravadas em exemplos/"
    q1, d1 = quadros_conversa(
        RAIZ / "exemplos" / "01-violoes-ate-1000.md",
        "uv run emporio chat --gravar exemplos/01-violoes-ate-1000.md",
        titulo,
    )
    q3, d3 = quadros_conversa(
        RAIZ / "exemplos" / "03-english-takamine-strings.md",
        "uv run emporio chat --idioma en-US --gravar exemplos/03-english-takamine-strings.md",
        titulo,
    )
    quadros, duracoes = q1 + q3, d1 + d3
    duracoes[-1] = 4000
    quadros[0].save(
        SAIDA / "demo-chat.gif", save_all=True, append_images=quadros[1:], duration=duracoes, loop=0, optimize=True
    )
    q2, _ = quadros_conversa(
        RAIZ / "exemplos" / "02-arrependimento-pedido.md",
        "uv run emporio chat --gravar exemplos/02-arrependimento-pedido.md",
        titulo,
    )
    q2[-1].save(SAIDA / "print-chat.png", optimize=True)
    print(f"{len(quadros)} quadros -> {SAIDA / 'demo-chat.gif'}; print -> {SAIDA / 'print-chat.png'}")


if __name__ == "__main__":
    main()

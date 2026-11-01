"""Gravação da conversa em Markdown (incremental: cada turno é anexado e fechado, nada se perde se o processo cair)."""

from datetime import datetime
from pathlib import Path

from emporio.portas import RespostaAtendimento


class GravadorMarkdown:
    """Escreve o cabeçalho ao criar e anexa um turno por chamada. Só recebe nomes, nunca chaves."""

    def __init__(self, caminho: Path) -> None:
        self._caminho = caminho

    def cabecalho(
        self,
        *,
        persona: str,
        tenant: str,
        idioma: str,
        provedores: tuple[str, ...],
        motor: str,
        data_referencia: datetime | None,
        agora: datetime,
    ) -> None:
        """Cria (ou sobrescreve) o arquivo com o cabeçalho da conversa."""
        referencia = data_referencia.isoformat() if data_referencia else "não definida (data real)"
        linhas = [
            f"# Conversa com {persona}",
            "",
            f"- **Gravada em:** {agora.isoformat(timespec='seconds')}",
            f"- **Data de referência (simulada):** {referencia}",
            f"- **Tenant:** {tenant}",
            f"- **Idioma:** {idioma}",
            f"- **Provedores com chave:** {', '.join(provedores) if provedores else 'nenhum'}",
            f"- **Motor:** {motor}",
            "",
            "---",
            "",
            "",
        ]
        self._caminho.parent.mkdir(parents=True, exist_ok=True)
        self._caminho.write_text("\n".join(linhas), encoding="utf-8", newline="\n")

    def turno(self, persona: str, pergunta: str, resposta: RespostaAtendimento) -> None:
        """Anexa um turno: pergunta, resposta e a linha de metadados em itálico."""
        tools = ", ".join(resposta.tools_chamadas) if resposta.tools_chamadas else "sem tools"
        partes = [resposta.nivel or "sem LLM", resposta.provedor or "-", tools]
        if resposta.degradada:
            partes.append("degradada")
        meta = " · ".join(partes)
        bloco = f"**Cliente:** {pergunta}\n\n**{persona}:** {resposta.texto}\n\n_{meta}_\n\n"
        with self._caminho.open("a", encoding="utf-8", newline="\n") as f:
            f.write(bloco)

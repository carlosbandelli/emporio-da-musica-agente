"""`SolicitacoesJsonl`: solicitações para a equipe em `<base>/<tenant_id>/solicitacoes.jsonl` (modo local)."""

import asyncio
import json
from datetime import datetime
from pathlib import Path

from emporio.modulos.atendimento.dominio import Solicitacao, TipoSolicitacao
from emporio.portas.contexto import TenantContext


def _para_linha(s: Solicitacao) -> str:
    return json.dumps(
        {
            "id": s.id,
            "tipo": s.tipo.value,
            "descricao": s.descricao,
            "sessao_id": s.sessao_id,
            "chave_idempotencia": s.chave_idempotencia,
            "criada_em": s.criada_em.isoformat(),
            "contato": s.contato,
        },
        ensure_ascii=False,
    )


def _de_linha(linha: str) -> Solicitacao:
    d = json.loads(linha)
    return Solicitacao(
        id=d["id"],
        tipo=TipoSolicitacao(d["tipo"]),
        descricao=d["descricao"],
        sessao_id=d["sessao_id"],
        chave_idempotencia=d["chave_idempotencia"],
        criada_em=datetime.fromisoformat(d["criada_em"]),
        contato=d.get("contato"),
    )


class SolicitacoesJsonl:
    """Implementa `Solicitacoes`. Escritas serializadas por `asyncio.Lock` (um processo)."""

    def __init__(self, base_dir: Path = Path("var")) -> None:
        self._base = base_dir
        self._lock = asyncio.Lock()

    def _arquivo(self, tenant_id: str) -> Path:
        if not tenant_id or Path(tenant_id).name != tenant_id or tenant_id in {".", ".."}:
            raise ValueError("tenant_id inválido para caminho de arquivo")
        return self._base / tenant_id / "solicitacoes.jsonl"

    @staticmethod
    def _ler(arquivo: Path) -> tuple[Solicitacao, ...]:
        if not arquivo.exists():
            return ()
        with arquivo.open(encoding="utf-8") as f:
            return tuple(_de_linha(linha) for linha in f if linha.strip())

    def _gravar(self, arquivo: Path, solicitacao: Solicitacao) -> Solicitacao:
        for existente in self._ler(arquivo):
            if existente.chave_idempotencia == solicitacao.chave_idempotencia:
                return existente
        arquivo.parent.mkdir(parents=True, exist_ok=True)
        with arquivo.open("a", encoding="utf-8", newline="\n") as f:
            f.write(_para_linha(solicitacao) + "\n")
        return solicitacao

    async def registrar(self, ctx: TenantContext, solicitacao: Solicitacao) -> Solicitacao:
        """Grava a solicitação; mesma `chave_idempotencia` devolve a já gravada, sem nova linha."""
        arquivo = self._arquivo(ctx.tenant_id)
        async with self._lock:
            return await asyncio.to_thread(self._gravar, arquivo, solicitacao)

    async def listar(self, ctx: TenantContext) -> tuple[Solicitacao, ...]:
        """Todas as solicitações do tenant."""
        arquivo = self._arquivo(ctx.tenant_id)
        async with self._lock:
            return await asyncio.to_thread(self._ler, arquivo)

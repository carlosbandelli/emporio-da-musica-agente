"""`SolicitacoesJsonl`: solicitações para a equipe em `<base>/<tenant_id>/solicitacoes.jsonl` (modo local)."""

import asyncio
import json
import os
from datetime import datetime
from pathlib import Path

from emporio.modulos.atendimento.dominio import Solicitacao, StatusSolicitacao, TipoSolicitacao
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
            "status": s.status.value,
            "atualizada_em": s.atualizada_em.isoformat() if s.atualizada_em else None,
            "atualizada_por": s.atualizada_por,
        },
        ensure_ascii=False,
    )


def _de_linha(linha: str) -> Solicitacao:
    d = json.loads(linha)
    atualizada_em = d.get("atualizada_em")
    return Solicitacao(
        id=d["id"],
        tipo=TipoSolicitacao(d["tipo"]),
        descricao=d["descricao"],
        sessao_id=d["sessao_id"],
        chave_idempotencia=d["chave_idempotencia"],
        criada_em=datetime.fromisoformat(d["criada_em"]),
        contato=d.get("contato"),
        # Linhas gravadas antes do status existir (task 2.5) valem como `aberta`.
        status=StatusSolicitacao(d.get("status") or StatusSolicitacao.ABERTA),
        atualizada_em=datetime.fromisoformat(atualizada_em) if atualizada_em else None,
        atualizada_por=d.get("atualizada_por"),
    )


class SolicitacoesJsonl:
    """Implementa `Solicitacoes`. Escritas serializadas por `asyncio.Lock` (um processo).

    `atualizar_status` regrava o arquivo inteiro (arquivo temporário + `os.replace`, atômico): suficiente para o modo
    local; o volume real fica no Postgres.
    """

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

    def _mudar_status(
        self, arquivo: Path, solicitacao_id: str, status: StatusSolicitacao, por: str, em: datetime
    ) -> Solicitacao | None:
        todas = list(self._ler(arquivo))
        for i, atual in enumerate(todas):
            if atual.id == solicitacao_id:
                if atual.status == status:
                    return atual  # idempotente: não regrava nem muda a auditoria
                todas[i] = nova = atual.com_status(status, por=por, em=em)  # levanta se a transição é inválida
                temporario = arquivo.with_suffix(".jsonl.tmp")
                with temporario.open("w", encoding="utf-8", newline="\n") as f:
                    f.writelines(_para_linha(s) + "\n" for s in todas)
                os.replace(temporario, arquivo)
                return nova
        return None

    async def registrar(self, ctx: TenantContext, solicitacao: Solicitacao) -> Solicitacao:
        """Grava a solicitação; mesma `chave_idempotencia` devolve a já gravada, sem nova linha."""
        arquivo = self._arquivo(ctx.tenant_id)
        async with self._lock:
            return await asyncio.to_thread(self._gravar, arquivo, solicitacao)

    async def listar(
        self,
        ctx: TenantContext,
        *,
        status: StatusSolicitacao | None = None,
        tipo: TipoSolicitacao | None = None,
    ) -> tuple[Solicitacao, ...]:
        """Solicitações do tenant na ordem de gravação, com filtros opcionais."""
        arquivo = self._arquivo(ctx.tenant_id)
        async with self._lock:
            todas = await asyncio.to_thread(self._ler, arquivo)
        return tuple(s for s in todas if (status is None or s.status == status) and (tipo is None or s.tipo == tipo))

    async def atualizar_status(
        self, ctx: TenantContext, solicitacao_id: str, status: StatusSolicitacao, *, por: str, em: datetime
    ) -> Solicitacao | None:
        """Muda o status (regra de transição do domínio); `None` se o id não existe no tenant."""
        arquivo = self._arquivo(ctx.tenant_id)
        async with self._lock:
            return await asyncio.to_thread(self._mudar_status, arquivo, solicitacao_id, status, por, em)

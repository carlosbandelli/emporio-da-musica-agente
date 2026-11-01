"""`CatalogoComCache`: cache-aside do catálogo que nunca serve estoque do cache.

Só são cacheados dados sem estoque: categorias e promoções (TTL curto). `Produto` carrega `estoque`, então
`listar_produtos` e `obter_produto` sempre vão ao repositório (manual §7.1/§7.3: nunca confirmar disponibilidade
sem checar o estoque atual). Redis fora ou entrada corrompida: lê direto do repositório.
"""

import json
import logging
from dataclasses import asdict

from redis.asyncio import Redis

from emporio.adaptadores.redis._guarda import GuardaRedis, RedisIndisponivelError
from emporio.modulos.catalogo.dominio import Categoria, Produto, Promocao
from emporio.portas.contexto import TenantContext
from emporio.portas.repositorios import Catalogo, FiltroProdutos

logger = logging.getLogger(__name__)

TTL_PADRAO_S = 60


def prefixo_cache(tenant_id: str) -> str:
    """Prefixo de todas as chaves de cache do tenant."""
    return f"emp:{tenant_id}:cache:"


class CatalogoComCache:
    """Implementa `Catalogo` envolvendo outro `Catalogo`."""

    def __init__(
        self,
        repositorio: Catalogo,
        cliente: Redis,
        guarda: GuardaRedis | None = None,
        ttl_s: int = TTL_PADRAO_S,
    ) -> None:
        self._repo = repositorio
        self._r = cliente
        self._guarda = guarda or GuardaRedis()
        self._ttl_s = ttl_s

    async def _ler(self, chave: str) -> object | None:
        try:
            raw = await self._guarda.executar(lambda: self._r.get(chave), "cache.get")
        except RedisIndisponivelError:
            return None
        if raw is None:
            return None
        try:
            resultado: object = json.loads(raw)
            return resultado
        except ValueError:
            logger.error("Entrada de cache corrompida; lendo do repositório")
            return None

    async def _gravar(self, chave: str, valor: object) -> None:
        payload = json.dumps(valor, ensure_ascii=False, separators=(",", ":"))
        try:
            await self._guarda.executar(lambda: self._r.set(chave, payload, ex=self._ttl_s), "cache.set")
        except RedisIndisponivelError:
            return

    async def listar_categorias(self, ctx: TenantContext) -> tuple[Categoria, ...]:
        """Categorias (sem estoque): cache-aside."""
        chave = f"{prefixo_cache(ctx.tenant_id)}categorias"
        dados = await self._ler(chave)
        if isinstance(dados, list):
            try:
                return tuple(Categoria(**d) for d in dados)
            except TypeError:
                logger.error("Cache de categorias com formato inesperado; lendo do repositório")
        categorias = await self._repo.listar_categorias(ctx)
        await self._gravar(chave, [asdict(c) for c in categorias])
        return categorias

    async def listar_produtos(self, ctx: TenantContext, filtro: FiltroProdutos) -> tuple[Produto, ...]:
        """Sempre do repositório: o resultado inclui estoque."""
        return await self._repo.listar_produtos(ctx, filtro)

    async def obter_produto(self, ctx: TenantContext, produto_id: int) -> Produto | None:
        """Sempre do repositório: o resultado inclui estoque."""
        return await self._repo.obter_produto(ctx, produto_id)

    async def listar_promocoes(self, ctx: TenantContext, produto_id: int) -> tuple[Promocao, ...]:
        """Promoções do produto (sem estoque): cache-aside."""
        chave = f"{prefixo_cache(ctx.tenant_id)}promocoes:{produto_id}"
        dados = await self._ler(chave)
        if isinstance(dados, list):
            try:
                return tuple(Promocao(**d) for d in dados)
            except TypeError:
                logger.error("Cache de promoções com formato inesperado; lendo do repositório")
        promocoes = await self._repo.listar_promocoes(ctx, produto_id)
        await self._gravar(chave, [asdict(p) for p in promocoes])
        return promocoes

    async def invalidar(self, ctx: TenantContext) -> None:
        """Remove todo o cache do tenant (usar após seed/admin alterar dados)."""
        padrao = f"{prefixo_cache(ctx.tenant_id)}*"

        async def _apagar() -> None:
            async for chave in self._r.scan_iter(match=padrao, count=200):
                await self._r.delete(chave)

        try:
            await self._guarda.executar(_apagar, "cache.invalidar")
        except RedisIndisponivelError:
            return

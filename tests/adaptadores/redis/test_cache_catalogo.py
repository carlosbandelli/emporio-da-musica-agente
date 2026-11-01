import dataclasses
from collections.abc import Mapping
from datetime import datetime

import pytest
from redis.asyncio import Redis

from emporio.adaptadores.redis import CatalogoComCache, criar_estado_redis
from emporio.adaptadores.redis._guarda import GuardaRedis
from emporio.configuracao.settings import Settings
from emporio.modulos.catalogo.dominio import Categoria, Produto, Promocao, StatusProduto
from emporio.portas import TenantContext
from emporio.portas.repositorios import FiltroProdutos
from emporio.portas.tenant import ConfigTenant

A = TenantContext(tenant_id="emporio")
B = TenantContext(tenant_id="outro")


class CatalogoFalso:
    """Repositório com contadores de chamadas e estoque mutável."""

    def __init__(self) -> None:
        self.estoque = 5
        self.chamadas: dict[str, int] = {}
        self.promocoes = (Promocao(81, 10, "Promo de teste", True),)

    def _conta(self, nome: str) -> None:
        self.chamadas[nome] = self.chamadas.get(nome, 0) + 1

    def _produto(self) -> Produto:
        return Produto(
            81,
            "Takamine GD20",
            1,
            "Violão",
            129900,
            self.estoque,
            StatusProduto.ATIVO,
            {"top": "spruce"},
            datetime(2026, 1, 1),
        )

    async def listar_categorias(self, ctx: TenantContext) -> tuple[Categoria, ...]:
        self._conta("categorias")
        return (Categoria(1, "Violões", "x"), Categoria(6, "Sopros"))

    async def listar_produtos(self, ctx: TenantContext, filtro: FiltroProdutos) -> tuple[Produto, ...]:
        self._conta("produtos")
        return (self._produto(),)

    async def obter_produto(self, ctx: TenantContext, produto_id: int) -> Produto | None:
        self._conta("produto")
        return self._produto()

    async def listar_promocoes(self, ctx: TenantContext, produto_id: int) -> tuple[Promocao, ...]:
        self._conta("promocoes")
        return self.promocoes


async def test_categorias_e_promocoes_vem_do_cache_na_segunda_chamada(fake: Redis) -> None:
    repo = CatalogoFalso()
    cat = CatalogoComCache(repo, fake)
    c1 = await cat.listar_categorias(A)
    c2 = await cat.listar_categorias(A)
    assert c1 == c2 == (Categoria(1, "Violões", "x"), Categoria(6, "Sopros"))
    p1 = await cat.listar_promocoes(A, 81)
    p2 = await cat.listar_promocoes(A, 81)
    assert p1 == p2 == repo.promocoes
    assert repo.chamadas == {"categorias": 1, "promocoes": 1}


async def test_estoque_nunca_vem_do_cache(fake: Redis) -> None:
    repo = CatalogoFalso()
    cat = CatalogoComCache(repo, fake)
    assert (await cat.obter_produto(A, 81)) is not None
    assert (await cat.listar_produtos(A, FiltroProdutos()))[0].estoque == 5
    repo.estoque = 0  # vendeu a última unidade
    p = await cat.obter_produto(A, 81)
    assert p is not None
    assert p.estoque == 0
    assert (await cat.listar_produtos(A, FiltroProdutos()))[0].estoque == 0
    assert repo.chamadas["produto"] == 2
    assert repo.chamadas["produtos"] == 2
    chaves = {k.decode() async for k in fake.scan_iter()}
    assert not any(":produto" in k and "promocoes" not in k for k in chaves)


async def test_promocao_alterada_so_aparece_apos_invalidar_ou_ttl(fake: Redis) -> None:
    repo = CatalogoFalso()
    cat = CatalogoComCache(repo, fake, ttl_s=60)
    await cat.listar_promocoes(A, 81)
    repo.promocoes = (dataclasses.replace(repo.promocoes[0], ativa=False),)
    assert (await cat.listar_promocoes(A, 81))[0].ativa is True  # ainda no TTL (dado sem estoque)
    await cat.invalidar(A)
    assert (await cat.listar_promocoes(A, 81))[0].ativa is False


async def test_chaves_com_prefixo_de_tenant_e_invalidacao_isolada(fake: Redis) -> None:
    cat = CatalogoComCache(CatalogoFalso(), fake)
    await cat.listar_categorias(A)
    await cat.listar_categorias(B)
    await cat.listar_promocoes(A, 81)
    chaves = {k.decode() async for k in fake.scan_iter()}
    assert chaves == {"emp:emporio:cache:categorias", "emp:outro:cache:categorias", "emp:emporio:cache:promocoes:81"}
    assert 0 < await fake.ttl("emp:emporio:cache:categorias") <= 60
    await cat.invalidar(A)
    assert {k.decode() async for k in fake.scan_iter()} == {"emp:outro:cache:categorias"}


async def test_redis_fora_le_do_repositorio_sem_lancar() -> None:
    cliente = Redis(host="127.0.0.1", port=1, socket_timeout=0.3, socket_connect_timeout=0.3)
    cat = CatalogoComCache(CatalogoFalso(), cliente, GuardaRedis())
    assert len(await cat.listar_categorias(A)) == 2
    assert len(await cat.listar_promocoes(A, 81)) == 1
    await cat.invalidar(A)
    await cliente.aclose()


async def test_entrada_corrompida_recai_no_repositorio(fake: Redis) -> None:
    await fake.set("emp:emporio:cache:categorias", b"{lixo")
    await fake.set("emp:emporio:cache:promocoes:81", b'[{"campo_errado": 1}]')
    cat = CatalogoComCache(CatalogoFalso(), fake)
    assert len(await cat.listar_categorias(A)) == 2
    assert len(await cat.listar_promocoes(A, 81)) == 1


async def test_fabrica(fake: Redis, tenants: Mapping[str, ConfigTenant]) -> None:
    settings = Settings(_env_file=None, redis_url="redis://x:6379/0")  # type: ignore[call-arg,arg-type]
    estado = criar_estado_redis(settings, tenants, cliente=fake)
    assert isinstance(estado.cache_catalogo(CatalogoFalso()), CatalogoComCache)


def test_fabrica_sem_url_levanta(tenants: Mapping[str, ConfigTenant]) -> None:
    with pytest.raises(ValueError, match="REDIS_URL"):
        criar_estado_redis(Settings(_env_file=None, redis_url=None), tenants)  # type: ignore[call-arg]

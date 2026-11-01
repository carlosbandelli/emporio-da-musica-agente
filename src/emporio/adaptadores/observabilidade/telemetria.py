"""`TelemetriaReal`: implementa a porta `Telemetria` com Prometheus (registro próprio) e OpenTelemetry."""

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from typing import Final

from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Gauge, Histogram, generate_latest

from emporio.adaptadores.observabilidade.logs import tenant_atual
from emporio.portas.estado import EstadoCircuito
from emporio.portas.llm import UsoTokens
from emporio.portas.telemetria import AtributoSpan

PRECOS_USD_POR_MILHAO: Final[dict[str, tuple[float, float, float]]] = {
    # (entrada, saída, leitura de cache) em USD por milhão de tokens; provedor ausente = custo 0.
    # Claude: preço de tabela do claude-opus-5-5 (Anthropic, out/2026). Gemini e Groq: aproximados, conferir.
    "claude": (4.0, 20.0, 0.20),
    "gemini": (0.30, 2.50, 0.03),
    "groq": (0.15, 0.60, 0.0),
}
_BUCKETS_HTTP: Final = (0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 20, 30)
_BUCKETS_LLM: Final = (0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 25, 40)
_BUCKETS_TOOL: Final = (0.001, 0.005, 0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5)
_VALOR_CIRCUITO: Final = {EstadoCircuito.FECHADO: 0, EstadoCircuito.MEIO_ABERTO: 1, EstadoCircuito.ABERTO: 2}


class TelemetriaReal:
    """Um `CollectorRegistry` e um `TracerProvider` por instância (nada global, testes não vazam estado).

    Sem `processador_spans`, os spans existem (dão `trace_id` aos logs) mas nada é exportado.
    """

    def __init__(self, processador_spans: BatchSpanProcessor | None = None, servico: str = "emporio") -> None:
        self.registro = CollectorRegistry()
        r = self.registro
        self._http = Counter(
            "emporio_http_requisicoes_total",
            "Requisições HTTP da app.",
            ["tenant_id", "rota", "metodo", "status"],
            registry=r,
        )
        self._http_dur = Histogram(
            "emporio_http_duracao_segundos",
            "Latência total da requisição.",
            ["tenant_id", "rota"],
            buckets=_BUCKETS_HTTP,
            registry=r,
        )
        self._limite = Counter(
            "emporio_rate_limit_bloqueios_total", "429 por camada da app.", ["tenant_id", "camada"], registry=r
        )
        self._rota = Counter("emporio_roteamento_total", "Mensagens por nível.", ["tenant_id", "nivel"], registry=r)
        self._llm = Counter(
            "emporio_llm_requisicoes_total",
            "Chamadas ao LLM.",
            ["tenant_id", "provedor", "nivel", "resultado"],
            registry=r,
        )
        self._llm_dur = Histogram(
            "emporio_llm_duracao_segundos",
            "Latência de cada chamada ao LLM.",
            ["provedor", "nivel"],
            buckets=_BUCKETS_LLM,
            registry=r,
        )
        self._tokens = Counter("emporio_llm_tokens_total", "Tokens.", ["tenant_id", "provedor", "tipo"], registry=r)
        self._custo = Counter(
            "emporio_llm_custo_usd_total", "Custo estimado em USD.", ["tenant_id", "provedor"], registry=r
        )
        self._fallback = Counter(
            "emporio_llm_fallback_total", "Trocas de provedor pelo fallback.", ["tenant_id", "de", "para"], registry=r
        )
        self._circuito = Gauge(
            "emporio_circuito_estado",
            "Circuit breaker: 0 fechado, 1 meio-aberto, 2 aberto.",
            ["provedor"],
            registry=r,
        )
        self._cache = Counter(
            "emporio_prompt_cache_total", "Cache do prompt (CAG).", ["tenant_id", "provedor", "resultado"], registry=r
        )
        self._tool_dur = Histogram(
            "emporio_tool_duracao_segundos",
            "Latência por tool.",
            ["tenant_id", "tool"],
            buckets=_BUCKETS_TOOL,
            registry=r,
        )
        self._tool = Counter(
            "emporio_tool_chamadas_total",
            "Chamadas por tool e resultado.",
            ["tenant_id", "tool", "resultado"],
            registry=r,
        )

        self.provedor_traces = TracerProvider(resource=Resource.create({"service.name": servico}))
        if processador_spans is not None:
            self.provedor_traces.add_span_processor(processador_spans)
        self._tracer = self.provedor_traces.get_tracer("emporio")

    # --- métricas ---------------------------------------------------------------------------------------------------
    def http_requisicao(self, tenant_id: str, rota: str, metodo: str, status: int, duracao_s: float) -> None:
        self._http.labels(tenant_id, rota, metodo, str(status)).inc()
        self._http_dur.labels(tenant_id, rota).observe(duracao_s)

    def rate_limit_negado(self, tenant_id: str, camada: str) -> None:
        self._limite.labels(tenant_id, camada).inc()

    def roteamento(self, tenant_id: str, nivel: str) -> None:
        self._rota.labels(tenant_id, nivel).inc()

    def llm_chamada(
        self,
        tenant_id: str,
        provedor: str,
        nivel: str,
        resultado: str,
        *,
        duracao_s: float,
        uso: UsoTokens | None = None,
    ) -> None:
        self._llm.labels(tenant_id, provedor, nivel, resultado).inc()
        if resultado != "circuito_aberto":  # recusa local, sem chamada de rede
            self._llm_dur.labels(provedor, nivel).observe(duracao_s)
        if uso is None:
            return
        for tipo, qtd in (("entrada", uso.entrada), ("saida", uso.saida), ("cache_leitura", uso.cache_leitura)):
            self._tokens.labels(tenant_id, provedor, tipo).inc(max(qtd, 0))
        p_in, p_out, p_cache = PRECOS_USD_POR_MILHAO.get(provedor, (0.0, 0.0, 0.0))
        custo = (uso.entrada * p_in + uso.saida * p_out + uso.cache_leitura * p_cache) / 1_000_000
        self._custo.labels(tenant_id, provedor).inc(max(custo, 0.0))

    def llm_fallback(self, tenant_id: str, de: str, para: str) -> None:
        self._fallback.labels(tenant_id, de, para).inc()

    def circuito_estado(self, provedor: str, estado: EstadoCircuito) -> None:
        self._circuito.labels(provedor).set(_VALOR_CIRCUITO[estado])

    def prompt_cache(self, tenant_id: str, provedor: str, hit: bool) -> None:
        self._cache.labels(tenant_id, provedor, "hit" if hit else "miss").inc()

    def tool_executada(self, tenant_id: str, tool: str, ok: bool, duracao_s: float) -> None:
        self._tool.labels(tenant_id, tool, "ok" if ok else "erro").inc()
        self._tool_dur.labels(tenant_id, tool).observe(duracao_s)

    def exportar_metricas(self) -> tuple[bytes, str]:
        return generate_latest(self.registro), CONTENT_TYPE_LATEST

    # --- traces -----------------------------------------------------------------------------------------------------
    def span(self, nome: str, **atributos: AtributoSpan) -> AbstractContextManager[None]:
        return self._span(nome, atributos)

    @contextmanager
    def _span(self, nome: str, atributos: dict[str, AtributoSpan]) -> Iterator[None]:
        token = None
        tenant = atributos.get("tenant_id")
        if isinstance(tenant, str):
            token = tenant_atual.set(tenant)
        try:
            with self._tracer.start_as_current_span(nome, attributes=atributos):
                yield
        finally:
            if token is not None:
                tenant_atual.reset(token)

    def encerrar(self) -> None:
        """Descarrega e fecha o exportador de traces (shutdown)."""
        self.provedor_traces.shutdown()

"""Fábrica da observabilidade: logs JSON + métricas + traces a partir de `Settings`."""

from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from emporio.adaptadores.observabilidade.logs import configurar_logs_json
from emporio.adaptadores.observabilidade.telemetria import TelemetriaReal
from emporio.configuracao.settings import Settings


def criar_telemetria(settings: Settings, *, configurar_logs: bool = True) -> TelemetriaReal:
    """Cria a telemetria real. OTLP HTTP só com `otel_exporter_otlp_endpoint`; sem ele nada é exportado."""
    if configurar_logs:
        configurar_logs_json(settings.log_level)
    processador: BatchSpanProcessor | None = None
    if settings.otel_exporter_otlp_endpoint:
        endpoint = settings.otel_exporter_otlp_endpoint.rstrip("/")
        processador = BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces"))
    return TelemetriaReal(processador)

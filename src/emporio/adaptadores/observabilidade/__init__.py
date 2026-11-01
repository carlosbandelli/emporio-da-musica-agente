"""Observabilidade: métricas Prometheus, traces OpenTelemetry e logs JSON com scrubbing de PII (arquitetura §7)."""

from emporio.adaptadores.observabilidade.fabrica import criar_telemetria
from emporio.adaptadores.observabilidade.logs import FiltroPII, FormatadorJson, configurar_logs_json
from emporio.adaptadores.observabilidade.pii import mascarar
from emporio.adaptadores.observabilidade.telemetria import TelemetriaReal

__all__ = [
    "FiltroPII",
    "FormatadorJson",
    "TelemetriaReal",
    "configurar_logs_json",
    "criar_telemetria",
    "mascarar",
]

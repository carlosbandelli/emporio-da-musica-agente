"""Logs JSON e scrubbing de PII; sem endpoint OTel nada é exportado (e não há Sentry nesta fase)."""

import io
import json
import logging
from collections.abc import Iterator

import pytest

from emporio.adaptadores.observabilidade import TelemetriaReal, configurar_logs_json, criar_telemetria, mascarar
from emporio.configuracao import Settings


@pytest.fixture
def fluxo() -> Iterator[io.StringIO]:
    saida = io.StringIO()
    raiz = logging.getLogger()
    antes, nivel = list(raiz.handlers), raiz.level
    configurar_logs_json("INFO", saida)
    yield saida
    raiz.handlers[:] = antes
    raiz.setLevel(nivel)


@pytest.mark.parametrize(
    "bruto",
    ["fulano@x.com", "(67) 99999-1234", "+55 67 99999-1234", "67999991234", "(67) 3341-4444"],
)
def test_mascarar_formatos(bruto: str) -> None:
    saida = mascarar(f"cliente {bruto} pediu")
    assert bruto not in saida and saida.startswith("cliente [") and saida.endswith("] pediu")


def test_log_json_mascara_mensagem_argumentos_e_inclui_tenant_e_trace(fluxo: io.StringIO) -> None:
    t = TelemetriaReal()
    with t.span("chat", tenant_id="emporio"):
        logging.getLogger("teste").warning("contato %s e %s", "fulano@x.com", "+55 67 99999-1234")
    logging.getLogger("teste").info("sem contexto")
    linhas = [json.loads(x) for x in fluxo.getvalue().splitlines()]
    assert len(linhas) == 2
    com, sem = linhas
    assert set(com) == {"timestamp", "nivel", "logger", "mensagem", "tenant", "trace_id"}
    assert com["mensagem"] == "contato [email] e [telefone]" and com["tenant"] == "emporio"
    assert len(com["trace_id"]) == 32 and com["nivel"] == "WARNING" and com["logger"] == "teste"
    assert "tenant" not in sem and "trace_id" not in sem


def test_excecao_no_log_tambem_e_mascarada(fluxo: io.StringIO) -> None:
    try:
        raise ValueError("falhou para fulano@x.com")
    except ValueError:
        logging.getLogger("teste").exception("erro")
    assert "fulano@x.com" not in fluxo.getvalue() and "[email]" in json.loads(fluxo.getvalue())["excecao"]


def test_sem_endpoint_otel_nada_e_exportado() -> None:
    cfg = Settings(_env_file=None, otel_exporter_otlp_endpoint=None)  # type: ignore[call-arg]
    t = criar_telemetria(cfg, configurar_logs=False)
    assert t.provedor_traces._active_span_processor._span_processors == ()


def test_com_endpoint_otel_registra_exportador_sem_rede() -> None:
    cfg = Settings(_env_file=None, otel_exporter_otlp_endpoint="http://localhost:4318")  # type: ignore[call-arg]
    t = criar_telemetria(cfg, configurar_logs=False)
    assert len(t.provedor_traces._active_span_processor._span_processors) == 1
    t.encerrar()

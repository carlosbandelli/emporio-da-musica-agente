"""API (task 2.1): tenant, chat, idioma, rate limit, health/ready. `httpx.AsyncClient` + ASGITransport + `LLMFalso`."""

from pathlib import Path

import httpx
from asgi_lifespan import LifespanManager

from emporio.adaptadores.api import app as modulo
from emporio.adaptadores.api.app import criar_app
from emporio.adaptadores.llm import LLMFalso, responder_texto
from emporio.composicao import ErroConfiguracao
from tests.adaptadores.api.apoio import BASE, app_com, cabecalhos, tenants_com_limite_sessao

CORPO = {"session_id": "s1", "mensagem": "oi"}
CAMPOS_RESPOSTA = {"texto", "idioma", "nivel", "provedor", "tools_chamadas", "degradada"}


# --- tenant ----------------------------------------------------------------------------------------------------------
async def test_sem_tenant_devolve_400_traduzido(cliente: httpx.AsyncClient) -> None:
    r = await cliente.post("/v1/chat", json=CORPO)
    assert r.status_code == 400
    assert r.json()["codigo"] == "tenant_ausente"
    assert "X-Tenant-ID" in r.json()["mensagem"] and "Não consegui" in r.json()["mensagem"]

    en = await cliente.post("/v1/chat", json=CORPO, headers={"Accept-Language": "en-US"})
    assert en.status_code == 400 and "I could not identify" in en.json()["mensagem"]


async def test_tenant_desconhecido_devolve_404_sem_ecoar_a_entrada(cliente: httpx.AsyncClient) -> None:
    r = await cliente.post("/v1/chat", json=CORPO, headers=cabecalhos("<script>x</script>"))
    assert r.status_code == 404
    assert r.json() == {"codigo": "tenant_desconhecido", "mensagem": "Loja não encontrada."}

    en = await cliente.post("/v1/chat", json=CORPO, headers=cabecalhos("nao-existe", **{"Accept-Language": "en"}))
    assert en.status_code == 404 and en.json()["mensagem"] == "Store not found."


async def test_tenant_pelo_subdominio_do_host(cliente: httpx.AsyncClient, llm: LLMFalso) -> None:
    llm.roteirizar(responder_texto("Oi! Sou a Sônia."))
    r = await cliente.post("/v1/chat", json=CORPO, headers={"Host": "emporio.exemplo.com"})
    assert r.status_code == 200 and r.json()["texto"] == "Oi! Sou a Sônia."

    desconhecido = await cliente.post("/v1/chat", json=CORPO, headers={"Host": "outra.exemplo.com"})
    assert desconhecido.status_code == 404


async def test_header_vence_o_subdominio(cliente: httpx.AsyncClient) -> None:
    r = await cliente.post("/v1/chat", json=CORPO, headers=cabecalhos("nao-existe", Host="emporio.exemplo.com"))
    assert r.status_code == 404


async def test_host_sem_subdominio_exige_header(cliente: httpx.AsyncClient) -> None:
    r = await cliente.post("/v1/chat", json=CORPO, headers={"Host": "127.0.0.1:8000"})
    assert r.status_code == 400


async def test_erro_de_tenant_vem_antes_da_validacao_do_corpo(cliente: httpx.AsyncClient) -> None:
    r = await cliente.post("/v1/chat", json={"lixo": 1})
    assert r.status_code == 400


# --- chat ------------------------------------------------------------------------------------------------------------
async def test_chat_devolve_200_com_os_campos_da_resposta(cliente: httpx.AsyncClient, llm: LLMFalso) -> None:
    llm.roteirizar(responder_texto("Oi! Sou a Sônia, como posso ajudar?"))
    r = await cliente.post("/v1/chat", json={"session_id": "abc", "mensagem": "oi, tudo bem?"}, headers=cabecalhos())
    assert r.status_code == 200
    corpo = r.json()
    assert corpo.keys() >= CAMPOS_RESPOSTA
    assert corpo["texto"] == "Oi! Sou a Sônia, como posso ajudar?"
    assert corpo["idioma"] == "pt-BR" and corpo["degradada"] is False and corpo["tools_chamadas"] == []
    assert corpo["codigo"] is None and corpo["persona"] == "Sônia"


async def test_sessao_mantem_historico_entre_chamadas(cliente: httpx.AsyncClient, llm: LLMFalso) -> None:
    llm.roteirizar(responder_texto("Primeira."), responder_texto("Segunda."))
    await cliente.post("/v1/chat", json={"session_id": "x", "mensagem": "um"}, headers=cabecalhos())
    await cliente.post("/v1/chat", json={"session_id": "x", "mensagem": "dois"}, headers=cabecalhos())
    assert [m.content for m in llm.ultima_requisicao.mensagens] == ["um", "Primeira.", "dois"]


async def test_accept_language_en_us_responde_em_ingles(cliente: httpx.AsyncClient, llm: LLMFalso) -> None:
    llm.roteirizar(responder_texto("Hi!"))
    r = await cliente.post(
        "/v1/chat", json={"session_id": "e", "mensagem": ""}, headers=cabecalhos(**{"Accept-Language": "en-US"})
    )
    assert r.status_code == 200
    assert r.json()["idioma"] == "en-US"
    assert "empty" in r.json()["texto"]  # mensagem fixa de entrada vazia, traduzida


async def test_idioma_do_corpo_vence_o_accept_language(cliente: httpx.AsyncClient) -> None:
    r = await cliente.post(
        "/v1/chat",
        json={"session_id": "e", "mensagem": "", "idioma": "pt-BR"},
        headers=cabecalhos(**{"Accept-Language": "en-US"}),
    )
    assert r.json()["idioma"] == "pt-BR" and "branco" in r.json()["texto"]


async def test_sem_idioma_usa_o_padrao_do_tenant(cliente: httpx.AsyncClient) -> None:
    r = await cliente.post("/v1/chat", json={"session_id": "e", "mensagem": ""}, headers=cabecalhos())
    assert r.json()["idioma"] == "pt-BR"


async def test_mensagem_vazia_e_200_nao_degradada_com_codigo(cliente: httpx.AsyncClient, llm: LLMFalso) -> None:
    r = await cliente.post("/v1/chat", json={"session_id": "e", "mensagem": "   "}, headers=cabecalhos())
    assert r.status_code == 200
    assert r.json()["degradada"] is False and r.json()["codigo"] == "entrada_invalida"
    assert llm.requisicoes == []


async def test_falha_do_llm_e_200_degradada(cliente: httpx.AsyncClient) -> None:
    r = await cliente.post("/v1/chat", json=CORPO, headers=cabecalhos())  # LLM sem roteiro: falha
    assert r.status_code == 200
    assert r.json()["degradada"] is True and r.json()["codigo"] in {"erro", "indisponivel"}


async def test_corpo_invalido_devolve_422_traduzido(cliente: httpx.AsyncClient) -> None:
    for corpo in (
        {"mensagem": "oi"},
        {"session_id": "", "mensagem": "oi"},
        {"session_id": "s", "mensagem": "oi", "idioma": "fr"},
    ):
        r = await cliente.post("/v1/chat", json=corpo, headers=cabecalhos())
        assert r.status_code == 422, corpo
        assert r.json()["codigo"] == "requisicao_invalida"

    en = await cliente.post("/v1/chat", json={"mensagem": "oi"}, headers=cabecalhos(**{"Accept-Language": "en"}))
    assert en.status_code == 422 and "Invalid request" in en.json()["mensagem"]


# --- rate limit ------------------------------------------------------------------------------------------------------
async def test_rate_limit_devolve_429_com_retry_after(tmp_path: Path, llm: LLMFalso) -> None:
    llm.roteirizar(responder_texto("Primeira."))
    app = app_com(llm, tmp_path, tenants_com_limite_sessao(tmp_path, capacidade=1))
    async with LifespanManager(app), httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as http:
        primeira = await http.post("/v1/chat", json=CORPO, headers=cabecalhos())
        segunda = await http.post("/v1/chat", json=CORPO, headers=cabecalhos())
        outra_sessao = await http.post("/v1/chat", json={**CORPO, "session_id": "outra"}, headers=cabecalhos())
    assert primeira.status_code == 200
    assert segunda.status_code == 429
    assert int(segunda.headers["Retry-After"]) >= 1
    assert segunda.json()["codigo"] == "limite" and segunda.json()["degradada"] is True
    assert segunda.json()["texto"]  # a mensagem fixa vai no corpo para o canal exibir
    assert outra_sessao.status_code != 429  # o limite da sessão não afeta outra sessão


async def test_429_em_ingles(tmp_path: Path, llm: LLMFalso) -> None:
    llm.roteirizar(responder_texto("ok"))
    app = app_com(llm, tmp_path, tenants_com_limite_sessao(tmp_path, capacidade=1))
    async with LifespanManager(app), httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as http:
        cab = cabecalhos(**{"Accept-Language": "en-US"})
        corpo = {"session_id": "s1", "mensagem": "Hello, do you sell guitars?"}
        await http.post("/v1/chat", json=corpo, headers=cab)
        r = await http.post("/v1/chat", json=corpo, headers=cab)
    assert r.status_code == 429 and r.json()["idioma"] == "en-US" and "Hang on" in r.json()["texto"]


# --- health e ready --------------------------------------------------------------------------------------------------
async def test_health_200_sem_dependencias() -> None:
    def explode() -> None:
        raise AssertionError("o health não pode montar nada")

    app = criar_app(explode)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as http:  # sem lifespan
        r = await http.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


async def test_ready_200_quando_montada(cliente: httpx.AsyncClient) -> None:
    r = await cliente.get("/ready")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "motor": "nativo", "provedores": ["falso"]}


async def test_sem_provedor_health_200_ready_503_chat_503(cliente_sem_provedor: httpx.AsyncClient) -> None:
    assert (await cliente_sem_provedor.get("/health")).status_code == 200

    ready = await cliente_sem_provedor.get("/ready")
    assert ready.status_code == 503
    assert ready.json()["status"] == "indisponivel" and ready.json()["motivo"] == "sem_chave_llm"
    assert "GOOGLE_API_KEY" in ready.json()["detalhe"]  # só nomes de variáveis, nunca valores

    chat = await cliente_sem_provedor.post("/v1/chat", json=CORPO, headers=cabecalhos())
    assert chat.status_code == 503
    assert chat.json()["degradada"] is True and chat.json()["codigo"] == "indisponivel"
    assert "fora do ar" in chat.json()["texto"] and "GOOGLE_API_KEY" not in chat.text

    en = await cliente_sem_provedor.post("/v1/chat", json=CORPO, headers=cabecalhos(**{"Accept-Language": "en"}))
    assert en.status_code == 503 and "offline" in en.json()["texto"]


async def test_configuracao_invalida_nao_vaza_detalhes_no_ready() -> None:
    def falha() -> None:
        raise ErroConfiguracao("arquivo C:\\segredo\\tenants.yaml quebrado")

    app = criar_app(falha)  # type: ignore[arg-type]
    async with LifespanManager(app), httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as http:
        r = await http.get("/ready")
    assert r.status_code == 503 and r.json() == {"status": "indisponivel", "motivo": "configuracao_invalida"}


def test_importar_o_modulo_nao_monta_nada() -> None:
    assert modulo.app.state.aplicacao is None
    assert modulo.app.title.startswith("Empório")


async def test_openapi_documenta_o_chat(cliente: httpx.AsyncClient) -> None:
    esquema = (await cliente.get("/openapi.json")).json()
    respostas = esquema["paths"]["/v1/chat"]["post"]["responses"]
    assert {"200", "400", "404", "422", "429", "503"} <= respostas.keys()

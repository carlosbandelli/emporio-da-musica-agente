"""Motor n8n: `AgenteN8n` chama o webhook de um workflow n8n, que chama de volta `/v1/tools/*` da API."""

from emporio.adaptadores.motor.n8n.agente import NOME_CIRCUITO, TENTATIVAS_PADRAO, AgenteN8n
from emporio.adaptadores.motor.n8n.contrato import VERSAO_CONTRATO, PedidoWebhook, RespostaWebhook

__all__ = ["NOME_CIRCUITO", "TENTATIVAS_PADRAO", "VERSAO_CONTRATO", "AgenteN8n", "PedidoWebhook", "RespostaWebhook"]

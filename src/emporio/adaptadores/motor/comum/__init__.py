"""Peças dos motores de atendimento que não dependem de quem orquestra (nativo ou n8n).

Extraídas do motor nativo na task 2.7, sem mudar comportamento, para o `AgenteN8n` cumprir os mesmos invariantes
sem importar o `AgenteNativo`: guarda de valores (I6), mensagens fixas (I4/I7), idioma da sessão (I7), corte do
histórico (I2) e o system prompt CAG (persona + regras + manual). Funções puras; só constantes no módulo.
"""

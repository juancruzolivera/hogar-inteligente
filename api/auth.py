import os


def secret_valido(request) -> bool:
    esperado = os.getenv("N8N_WEBHOOK_SECRET")
    if not esperado:
        # Sin secreto configurado no se expone el endpoint (evita pulsos/comandos gratis).
        return False
    recibido = request.headers.get("X-Webhook-Secret")
    return recibido == esperado

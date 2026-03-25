"""
NMT — Fase 2
billing/abacatepay.py

Cliente HTTP para a AbacatePay REST API.
Cria cobranças Pix, consulta status e valida assinaturas de webhook.

CRÍTICO: verify_webhook_signature() DEVE ser chamada antes de processar
qualquer evento. Um webhook forjado pode liberar plano pago gratuitamente.
"""

import hashlib
import hmac
import logging
import os

import httpx

logger = logging.getLogger(__name__)

ABACATE_BASE_URL = os.getenv("ABACATEPAY_BASE_URL", "https://api.abacatepay.com/v1")
ABACATE_API_KEY  = os.getenv("ABACATEPAY_API_KEY", "")
FRONTEND_URL     = os.getenv("FRONTEND_URL", "https://nmt.up.railway.app")

# Timeout generoso — Pix pode ter latência variável
_TIMEOUT = httpx.Timeout(20.0, connect=5.0)


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {ABACATE_API_KEY}",
        "Content-Type": "application/json",
    }


async def create_billing(
    user_id: str,
    plan_id: str,
    plan_name: str,
    amount_brl: float,
    user_email: str,
    user_name: str,
) -> dict:
    """
    Cria cobrança Pix one-time na AbacatePay.

    Retorna o JSON da API. Campos relevantes:
      data.id              → billing_id para polling
      data.pixQrCode       → string do QR Code
      data.pixQrCodeImage  → base64 PNG do QR Code
      data.expiresAt       → ISO timestamp de expiração (15 min padrão)
    """
    payload = {
        "frequency": "ONE_TIME",
        "methods": ["PIX"],
        "products": [
            {
                "externalId": plan_id,
                "name": f"NMT {plan_name} — 1 mês",
                "description": f"Acesso ao plano {plan_name} por 30 dias",
                "quantity": 1,
                "price": int(round(amount_brl * 100)),  # centavos
            }
        ],
        "customer": {
            "name": user_name or user_email,
            "email": user_email,
            "cellphone": "",
            "taxId": "",
        },
        "metadata": {
            "user_id": user_id,
            "plan_id": plan_id,
        },
        "returnUrl":     f"{FRONTEND_URL}/dashboard?payment=success",
        "completionUrl": f"{FRONTEND_URL}/dashboard?payment=success",
    }

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.post(
            f"{ABACATE_BASE_URL}/billing/create",
            headers=_headers(),
            json=payload,
        )

    if response.status_code != 200:
        logger.error(
            "AbacatePay create_billing falhou: status=%s body=%s",
            response.status_code,
            response.text[:400],
        )
        response.raise_for_status()

    return response.json()


async def get_billing_status(billing_id: str) -> dict:
    """
    Consulta o status de uma cobrança pelo ID.

    Status possíveis: PENDING | PAID | EXPIRED | CANCELLED
    """
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.get(
            f"{ABACATE_BASE_URL}/billing/{billing_id}",
            headers=_headers(),
        )

    if response.status_code != 200:
        logger.error(
            "AbacatePay get_billing_status falhou: id=%s status=%s",
            billing_id,
            response.status_code,
        )
        response.raise_for_status()

    return response.json()


async def verify_webhook_signature(payload: bytes, signature: str) -> bool:
    """
    Valida HMAC-SHA256 do webhook.

    A AbacatePay assina o body com o ABACATEPAY_WEBHOOK_SECRET e envia
    o hex digest no header X-Abacate-Signature.

    Retorna True em ambiente de desenvolvimento se o secret não estiver
    configurado (evita bloquear testes locais). Em produção, o secret
    DEVE estar presente — loga aviso se ausente.
    """
    secret_str = os.getenv("ABACATEPAY_WEBHOOK_SECRET", "")

    if not secret_str:
        env = os.getenv("ENVIRONMENT", "development")
        if env == "production":
            logger.error(
                "ABACATEPAY_WEBHOOK_SECRET não configurado em produção! "
                "Todos os webhooks estão sendo REJEITADOS."
            )
            return False
        # Desenvolvimento: aceita sem validar, mas avisa
        logger.warning("ABACATEPAY_WEBHOOK_SECRET ausente — modo dev, assinatura ignorada.")
        return True

    expected = hmac.new(secret_str.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)

"""
NMT — Fase 2
auth/email.py

Emails transacionais via Resend.com.
Falhas de email nunca bloqueiam o fluxo principal — só logam o erro.
"""

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# Resend é importado condicionalmente — não quebra se não instalado
try:
    import resend
    resend.api_key = os.getenv("RESEND_API_KEY", "")
    RESEND_AVAILABLE = bool(resend.api_key)
except ImportError:
    RESEND_AVAILABLE = False

FROM_ADDRESS = os.getenv("EMAIL_FROM", "nmt@nmt.ai")
FRONTEND_URL = os.getenv("FRONTEND_URL", "https://nmt.up.railway.app")


# ──────────────────────────────────────────────
# TEMPLATES
# ──────────────────────────────────────────────

def _base_template(title: str, body: str) -> str:
    """Template HTML mínimo para emails transacionais."""
    return f"""
<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title}</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
           background: #0A0A0F; color: #FFFFFF; margin: 0; padding: 40px 20px; }}
    .container {{ max-width: 520px; margin: 0 auto; }}
    .logo {{ font-size: 24px; font-weight: 700; letter-spacing: -1px;
             color: #00D4FF; margin-bottom: 32px; }}
    .card {{ background: #12121A; border: 1px solid #1E1E2E;
             border-radius: 12px; padding: 32px; }}
    h1 {{ font-size: 22px; margin: 0 0 16px; color: #FFFFFF; }}
    p {{ color: #8888AA; line-height: 1.6; margin: 0 0 16px; }}
    .btn {{ display: inline-block; background: #00D4FF; color: #0A0A0F;
            font-weight: 600; padding: 12px 28px; border-radius: 8px;
            text-decoration: none; margin: 8px 0; }}
    .footer {{ margin-top: 24px; font-size: 12px; color: #555577; }}
  </style>
</head>
<body>
  <div class="container">
    <div class="logo">NMT</div>
    <div class="card">
      {body}
    </div>
    <div class="footer">
      NMT — Neural Machine Translator<br>
      A barreira do idioma não existe mais.
    </div>
  </div>
</body>
</html>
"""


# ──────────────────────────────────────────────
# FUNÇÕES DE ENVIO
# ──────────────────────────────────────────────

async def send_welcome_email(to_email: str, name: Optional[str], verification_token: str) -> bool:
    """Email de boas-vindas com link de verificação."""
    display_name = name or "Explorador"
    verify_url = f"{FRONTEND_URL}/verify-email?token={verification_token}"

    body = f"""
        <h1>Bem-vindo ao NMT, {display_name}!</h1>
        <p>Sua conta foi criada. Para ativar o acesso completo, confirme seu email clicando no botão abaixo.</p>
        <a href="{verify_url}" class="btn">Confirmar Email</a>
        <p>O link expira em 24 horas.</p>
        <p>Se você não criou esta conta, ignore este email.</p>
    """

    return await _send(
        to=to_email,
        subject="Confirme seu email — NMT",
        html=_base_template("Bem-vindo ao NMT", body),
    )


async def send_payment_confirmation_email(
    to_email: str,
    name: Optional[str],
    plan_name: str,
    amount_brl: float,
) -> bool:
    """Recibo de pagamento após assinatura confirmada."""
    display_name = name or "usuário"
    dashboard_url = f"{FRONTEND_URL}/dashboard"

    body = f"""
        <h1>Pagamento confirmado ✓</h1>
        <p>Olá, {display_name}. Seu plano <strong>{plan_name}</strong> está ativo.</p>
        <p style="font-size: 28px; font-weight: 700; color: #00D4FF; margin: 24px 0;">
          R$ {amount_brl:.2f}
        </p>
        <p>Acesse o dashboard para ver seu uso e gerenciar sua assinatura.</p>
        <a href="{dashboard_url}" class="btn">Acessar Dashboard</a>
        <p>Obrigado por assinar o NMT.</p>
    """

    return await _send(
        to=to_email,
        subject=f"Pagamento confirmado — Plano {plan_name} ativo",
        html=_base_template("Pagamento confirmado", body),
    )


async def send_subscription_cancelled_email(to_email: str, name: Optional[str], expires_at: str) -> bool:
    """Confirmação de cancelamento de assinatura."""
    display_name = name or "usuário"

    body = f"""
        <h1>Assinatura cancelada</h1>
        <p>Olá, {display_name}. Sua assinatura foi cancelada conforme solicitado.</p>
        <p>Você continua com acesso até <strong>{expires_at}</strong>.</p>
        <p>Após essa data, sua conta retorna automaticamente para o plano Free.</p>
        <p>Se mudou de ideia, você pode reativar a qualquer momento pelo dashboard.</p>
        <a href="{FRONTEND_URL}/dashboard" class="btn">Ver Dashboard</a>
    """

    return await _send(
        to=to_email,
        subject="Assinatura cancelada — NMT",
        html=_base_template("Assinatura cancelada", body),
    )


# ──────────────────────────────────────────────
# SENDER INTERNO
# ──────────────────────────────────────────────

async def _send(to: str, subject: str, html: str) -> bool:
    """
    Envia email via Resend. Nunca lança exceção — só loga erros.
    Retorna True se enviado, False se falhou.
    """
    if not RESEND_AVAILABLE:
        logger.warning(f"[EMAIL] Resend não configurado. Email para {to} não enviado: {subject}")
        return False

    try:
        params = {
            "from": FROM_ADDRESS,
            "to": [to],
            "subject": subject,
            "html": html,
        }
        resend.Emails.send(params)
        logger.info(f"[EMAIL] Enviado para {to}: {subject}")
        return True
    except Exception as e:
        logger.error(f"[EMAIL] Falha ao enviar para {to}: {e}")
        return False

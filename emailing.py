"""
Envoi d'emails transactionnels via Resend (reinitialisation de mot de passe,
verification d'adresse email). Necessite la variable d'environnement
RESEND_API_KEY (Railway). Si elle est absente, les envois sont simplement
ignores (pour ne jamais faire planter l'API en dev local).
"""

import os
import requests

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
FROM_EMAIL = os.environ.get("EMAIL_FROM", "H€CTOR <noreply@hector-app.fr>")
FRONTEND_URL = os.environ.get("FRONTEND_URL", "https://hector-app.fr")


def send_email(to: str, subject: str, html: str) -> bool:
    if not RESEND_API_KEY:
        return False
    try:
        resp = requests.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            json={"from": FROM_EMAIL, "to": [to], "subject": subject, "html": html},
            timeout=15,
        )
        return resp.status_code < 300
    except Exception:
        return False


def send_reset_password_email(to: str, token: str) -> bool:
    link = f"{FRONTEND_URL}/?reset_token={token}"
    html = f"""
    <div style="font-family: sans-serif; max-width: 480px; margin: 0 auto;">
      <h2 style="color:#0A2540;">Reinitialisation de votre mot de passe</h2>
      <p>Vous avez demande la reinitialisation de votre mot de passe H€CTOR.</p>
      <p>
        <a href="{link}" style="background:#378ADD; color:white; padding:12px 20px;
           border-radius:8px; text-decoration:none; display:inline-block;">
          Choisir un nouveau mot de passe
        </a>
      </p>
      <p style="color:#6B7A8D; font-size:13px;">
        Ce lien expire dans 1 heure. Si vous n'etes pas a l'origine de cette demande,
        vous pouvez ignorer cet email en toute securite.
      </p>
    </div>
    """
    return send_email(to, "Reinitialisation de votre mot de passe H€CTOR", html)


def send_verification_email(to: str, token: str) -> bool:
    link = f"{FRONTEND_URL}/?verify_token={token}"
    html = f"""
    <div style="font-family: sans-serif; max-width: 480px; margin: 0 auto;">
      <h2 style="color:#0A2540;">Confirmez votre adresse email</h2>
      <p>Bienvenue sur H€CTOR ! Confirmez votre adresse email pour activer votre compte.</p>
      <p>
        <a href="{link}" style="background:#378ADD; color:white; padding:12px 20px;
           border-radius:8px; text-decoration:none; display:inline-block;">
          Confirmer mon email
        </a>
      </p>
      <p style="color:#6B7A8D; font-size:13px;">Ce lien expire dans 24 heures.</p>
    </div>
    """
    return send_email(to, "Confirmez votre email H€CTOR", html)

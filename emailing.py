"""
Envoi d'emails transactionnels via Resend (reinitialisation de mot de passe,
verification d'adresse email). Necessite la variable d'environnement
RESEND_API_KEY (Railway). Si elle est absente, les envois sont simplement
ignores (pour ne jamais faire planter l'API en dev local).
"""

import os
import requests

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
FROM_EMAIL = os.environ.get("EMAIL_FROM", "TOTOR <noreply@hector-app.fr>")
FRONTEND_URL = os.environ.get("FRONTEND_URL", "https://hector-app.fr")

# Alertes internes envoyées au fondateur (nouvel inscrit, nouvel abonné payant).
FOUNDER_ALERT_EMAIL = os.environ.get("FOUNDER_ALERT_EMAIL", "gardereaucamille@gmail.com")
# Nombre de places au tarif Pionnier (cf. PRICING.md : 100 premiers payants).
PIONNIER_PLACES = int(os.environ.get("PIONNIER_PLACES", "100"))


def _adresse_expedition() -> str:
    """Extrait l'adresse technique de FROM_EMAIL (ex. noreply@hector-app.fr)."""
    if "<" in FROM_EMAIL and ">" in FROM_EMAIL:
        return FROM_EMAIL.split("<", 1)[1].split(">", 1)[0].strip()
    return FROM_EMAIL.strip()


def _nettoyer_nom_affichage(nom: str) -> str:
    """Nom d'affichage sûr pour l'en-tête From (pas de chevrons/guillemets/retours)."""
    return "".join(c for c in nom if c not in '<>"\r\n').strip()


def send_email(to: str, subject: str, html: str, from_name: str = None, reply_to: str = None) -> bool:
    """
    from_name : nom d'affichage de l'expéditeur (ex. le nom de l'utilisateur pour les
                emails envoyés à SES clients). L'adresse technique reste celle de
                FROM_EMAIL (DMARC en place). Sans from_name : expéditeur TOTOR habituel.
    reply_to  : adresse de réponse (ex. l'email de l'utilisateur, pour que son client
                puisse lui répondre directement).
    """
    if not RESEND_API_KEY:
        return False
    exp = FROM_EMAIL
    if from_name:
        nom = _nettoyer_nom_affichage(from_name)
        if nom:
            exp = f"{nom} <{_adresse_expedition()}>"
    payload = {"from": exp, "to": [to], "subject": subject, "html": html}
    if reply_to:
        payload["reply_to"] = reply_to
    try:
        resp = requests.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
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
      <p>Vous avez demande la reinitialisation de votre mot de passe TOTOR.</p>
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
    return send_email(to, "Reinitialisation de votre mot de passe TOTOR", html)


def send_verification_email(to: str, token: str) -> bool:
    link = f"{FRONTEND_URL}/?verify_token={token}"
    html = f"""
    <div style="font-family: sans-serif; max-width: 480px; margin: 0 auto;">
      <h2 style="color:#0A2540;">Confirmez votre adresse email</h2>
      <p>Bienvenue sur TOTOR ! Confirmez votre adresse email pour activer votre compte.</p>
      <p>
        <a href="{link}" style="background:#378ADD; color:white; padding:12px 20px;
           border-radius:8px; text-decoration:none; display:inline-block;">
          Confirmer mon email
        </a>
      </p>
      <p style="color:#6B7A8D; font-size:13px;">Ce lien expire dans 24 heures.</p>
    </div>
    """
    return send_email(to, "Confirmez votre email TOTOR", html)


def send_invoice_email(to: str, subject: str, html: str, from_name: str = None, reply_to: str = None) -> bool:
    return send_email(to, subject, html, from_name=from_name, reply_to=reply_to)


# ════════════════════════════════════════════════════════════════════════
#  Alertes fondateur (croissance) — best-effort, ne bloquent jamais un flux
# ════════════════════════════════════════════════════════════════════════
def _founder_html(titre: str, gros_chiffre: str, sous_titre: str, detail: str) -> str:
    return f"""
    <div style="font-family: sans-serif; max-width: 480px; margin: 0 auto; text-align:center;
                background:#07192E; color:#F8FAFC; padding:32px 24px; border-radius:16px;">
      <p style="color:#5DCAA5; font-weight:bold; letter-spacing:1px; margin:0 0 8px;">{titre}</p>
      <div style="font-size:56px; font-weight:800; color:#F8FAFC; line-height:1; margin:8px 0;">{gros_chiffre}</div>
      <p style="color:#5DCAA5; font-size:16px; margin:4px 0 20px;">{sous_titre}</p>
      <p style="color:#9BB0C4; font-size:13px; margin:0;">{detail}</p>
    </div>
    """


def send_founder_signup_alert(count: int, user_email: str) -> bool:
    """Alerte : une nouvelle personne vient de créer un compte (gratuit)."""
    if not FOUNDER_ALERT_EMAIL:
        return False
    html = _founder_html(
        "NOUVEL INSCRIT",
        f"n&deg;{count}",
        "TOTOR grandit.",
        f"Inscrit : {user_email}",
    )
    return send_email(FOUNDER_ALERT_EMAIL, f"Nouvel inscrit TOTOR (n{count})", html)


def send_founder_subscriber_alert(count: int, user_email: str) -> bool:
    """Alerte : une nouvelle personne vient de s'abonner (payant, via Stripe)."""
    if not FOUNDER_ALERT_EMAIL:
        return False
    places = max(0, PIONNIER_PLACES - count)
    sous = f"Plus que {places} places Pionnier." if places > 0 else "Toutes les places Pionnier sont prises."
    html = _founder_html(
        "NOUVEL ABONNE PAYANT",
        f"n&deg;{count}",
        sous,
        f"Abonne : {user_email}",
    )
    return send_email(FOUNDER_ALERT_EMAIL, f"Nouvel abonne payant TOTOR (n{count})", html)

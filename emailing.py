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
    <div style="font-family: sans-serif; max-width: 480px; margin: 0 auto; color:#0A2540;">
      <h2 style="color:#0A2540;">🐾 On te remet en selle</h2>
      <p>Salut, c'est Totor. Tu as demandé à changer ton mot de passe. Choisis-en un nouveau ci-dessous, et on repart.</p>
      <p style="margin:24px 0;">
        <a href="{link}" style="background:#5DCAA5; color:#04342C; padding:12px 20px;
           border-radius:8px; text-decoration:none; display:inline-block; font-weight:bold;">
          Choisir un nouveau mot de passe
        </a>
      </p>
      <p style="color:#6B7A8D; font-size:13px;">
        Ce lien est valable 1 heure. Si ce n'est pas toi qui l'as demandé, ignore ce message
        en toute tranquillité : ton compte reste protégé.
      </p>
      <p style="color:#6B7A8D; font-size:13px;">Je veille,<br/>Totor 🐾</p>
    </div>
    """
    return send_email(to, "🐾 Réinitialise ton mot de passe TOTOR", html)


def send_verification_email(to: str, token: str) -> bool:
    link = f"{FRONTEND_URL}/?verify_token={token}"
    html = f"""
    <div style="font-family: sans-serif; max-width: 480px; margin: 0 auto; color:#0A2540;">
      <h2 style="color:#0A2540;">🐾 Bienvenue, c'est Totor</h2>
      <p>Content de t'accueillir. Confirme ton adresse email, et je m'occupe du reste :
      compter tes chiffres, provisionner ce qu'il faut, et t'expliquer chaque euro.</p>
      <p style="margin:24px 0;">
        <a href="{link}" style="background:#5DCAA5; color:#04342C; padding:12px 20px;
           border-radius:8px; text-decoration:none; display:inline-block; font-weight:bold;">
          Confirmer mon email
        </a>
      </p>
      <p style="color:#6B7A8D; font-size:13px;">
        Ce lien est valable 24 heures. Si ce n'est pas toi qui viens de t'inscrire,
        ignore simplement ce message.
      </p>
      <p style="color:#6B7A8D; font-size:13px;">À tout de suite,<br/>Totor 🐾</p>
    </div>
    """
    return send_email(to, "🐾 Confirme ton email TOTOR", html)


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


def send_founder_promo_alert(code: str, count: int, max_uses, user_email: str) -> bool:
    """Alerte : quelqu'un vient d'utiliser un code cadeau (kind 'tester').
    Affiche le compteur d'utilisations (ex. 1/15) pour suivre la campagne."""
    if not FOUNDER_ALERT_EMAIL:
        return False
    quota = f"{count}/{max_uses}" if max_uses else str(count)
    html = _founder_html(
        "CODE CADEAU UTILISE",
        quota,
        f"Le code {code} vient d'etre utilise.",
        f"Par : {user_email}",
    )
    return send_email(FOUNDER_ALERT_EMAIL, f"Code {code} utilise ({quota})", html)


def send_founder_trial_ending_alert(essais: list) -> bool:
    """Alerte : un ou plusieurs essais gratuits arrivent bientôt à échéance.
    `essais` = liste de dicts {email, source, fin (datetime|None), annulera (bool)}.
    But : que Camille puisse relancer la personne avant la bascule payante."""
    if not FOUNDER_ALERT_EMAIL or not essais:
        return False
    lignes = ""
    for e in essais:
        fin = e.get("fin")
        fin_txt = fin.strftime("%d/%m/%Y") if fin else "bientôt"
        etat = ("a coupé le renouvellement (à relancer en priorité)"
                if e.get("annulera") else "se transformera en abonnement payant")
        lignes += (
            f"<li style='margin-bottom:10px;'><strong>{e.get('email','?')}</strong>"
            f"<br><span style='color:#9BB0C4;font-size:12px;'>"
            f"essai {e.get('source','')} - fin le {fin_txt} - {etat}</span></li>"
        )
    n = len(essais)
    titre = "1 essai se termine bientôt" if n == 1 else f"{n} essais se terminent bientôt"
    html = f"""
    <div style="font-family: sans-serif; max-width: 520px; margin: 0 auto;
                background:#07192E; color:#F8FAFC; padding:28px 24px; border-radius:16px;">
      <p style="color:#5DCAA5; font-weight:bold; letter-spacing:1px; margin:0 0 12px;">ESSAI(S) BIENTOT FINI(S)</p>
      <p style="color:#F8FAFC; font-size:15px; margin:0 0 16px;">{titre}. C'est le bon moment pour un petit mot.</p>
      <ul style="text-align:left; padding-left:18px; font-size:14px; line-height:1.5; color:#F8FAFC;">{lignes}</ul>
      <p style="color:#9BB0C4; font-size:12px; margin:14px 0 0;">TOTOR veille sur tes essais.</p>
    </div>
    """
    return send_email(FOUNDER_ALERT_EMAIL, f"TOTOR - {titre}", html)

# ─────────────────────────────────────────────────────────────────────────────
#  Double vérification (MFA/TOTP) — comptes email + mot de passe uniquement.
#  Les connexions Google et Apple héritent déjà du MFA de leur fournisseur.
#
#  TOTP implémenté en bibliothèque standard (RFC 6238, HMAC-SHA1, 30 s,
#  6 chiffres) : pas de dépendance de calcul, rien à auditer de plus.
#  Codes de secours : 8 codes à usage unique, stockés HACHÉS (bcrypt), affichés
#  UNE SEULE fois à l'activation.
# ─────────────────────────────────────────────────────────────────────────────
import base64
import hashlib
import hmac
import secrets
import struct
import time

import bcrypt

TOTP_PERIODE = 30          # secondes par fenêtre (standard universel)
TOTP_CHIFFRES = 6          # longueur du code
TOTP_TOLERANCE = 1         # fenêtres acceptées avant/après (dérive d'horloge)
NB_CODES_SECOURS = 8


def generer_secret() -> str:
    """Secret TOTP en Base32 (160 bits, la taille recommandée par la RFC)."""
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii")


def _code_totp(secret_b32: str, compteur: int) -> str:
    cle = base64.b32decode(secret_b32, casefold=True)
    message = struct.pack(">Q", compteur)
    empreinte = hmac.new(cle, message, hashlib.sha1).digest()
    decalage = empreinte[-1] & 0x0F
    tronque = struct.unpack(">I", empreinte[decalage:decalage + 4])[0] & 0x7FFFFFFF
    return str(tronque % (10 ** TOTP_CHIFFRES)).zfill(TOTP_CHIFFRES)


def verifier_code(secret_b32: str, code: str, maintenant: float | None = None) -> bool:
    """Vrai si le code correspond à la fenêtre courante (± tolérance).

    Comparaison en temps constant : on ne s'arrête pas au premier écart, pour ne
    rien laisser filtrer par le chronométrage.
    """
    code = (code or "").strip().replace(" ", "")
    if not code.isdigit() or len(code) != TOTP_CHIFFRES:
        return False
    t = int((maintenant if maintenant is not None else time.time()) // TOTP_PERIODE)
    ok = False
    for delta in range(-TOTP_TOLERANCE, TOTP_TOLERANCE + 1):
        attendu = _code_totp(secret_b32, t + delta)
        if hmac.compare_digest(attendu, code):
            ok = True
    return ok


def uri_provisionnement(secret_b32: str, email: str) -> str:
    """Lien otpauth:// que lisent toutes les applications d'authentification."""
    from urllib.parse import quote
    etiquette = quote(f"TOTOR:{email}")
    return (
        f"otpauth://totp/{etiquette}?secret={secret_b32}"
        f"&issuer=TOTOR&algorithm=SHA1&digits={TOTP_CHIFFRES}&period={TOTP_PERIODE}"
    )


def qr_svg_data_uri(contenu: str) -> str:
    """QR code du lien otpauth, en SVG encodé pour un <img src=...> direct."""
    import io
    import segno
    tampon = io.BytesIO()
    segno.make(contenu, error="m").save(tampon, kind="svg", scale=4, dark="#0A2540")
    b64 = base64.b64encode(tampon.getvalue()).decode("ascii")
    return f"data:image/svg+xml;base64,{b64}"


def generer_codes_secours() -> list[str]:
    """8 codes lisibles (XXXX-XXXX), sans lettres ambiguës."""
    alphabet = "ABCDEFGHJKMNPQRSTVWXYZ23456789"
    codes = []
    for _ in range(NB_CODES_SECOURS):
        brut = "".join(secrets.choice(alphabet) for _ in range(8))
        codes.append(f"{brut[:4]}-{brut[4:]}")
    return codes


def hacher_codes_secours(codes: list[str]) -> list[str]:
    return [bcrypt.hashpw(c.encode(), bcrypt.gensalt()).decode() for c in codes]


def consommer_code_secours(code: str, haches: list[str]) -> list[str] | None:
    """Si `code` correspond à un code de secours non utilisé, retourne la liste
    SANS ce code (à réenregistrer). Sinon None. Chaque code ne sert qu'une fois."""
    code = (code or "").strip().upper().replace(" ", "")
    if len(code) == 8 and "-" not in code:
        code = f"{code[:4]}-{code[4:]}"
    for i, h in enumerate(haches):
        try:
            if bcrypt.checkpw(code.encode(), h.encode()):
                return haches[:i] + haches[i + 1:]
        except ValueError:
            continue
    return None

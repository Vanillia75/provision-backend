"""
Verification des jetons « Se connecter avec Apple » (Sign in with Apple).

L'app iPhone fait signer un « identity token » par Apple et nous l'envoie.
Ce module verifie que ce jeton est authentique (signature RS256 contre les
cles publiques publiees par Apple) et en extrait de quoi identifier la
personne.

Deux particularites d'Apple, importantes pour la suite :

  - `sub` est l'identifiant STABLE de la personne pour notre app. C'est la
    seule cle sur laquelle on peut compter dans le temps : l'email, lui,
    peut etre masque.
  - `is_private_email` signale une adresse de relais anonyme
    (xxx@privaterelay.appleid.com). Nos emails n'y arrivent QUE si le domaine
    expediteur est declare dans Apple Developer (Sign in with Apple >
    Email Sources). Sinon Apple les jette silencieusement.
"""

import os
import jwt

APPLE_ISSUER = "https://appleid.apple.com"
APPLE_KEYS_URL = "https://appleid.apple.com/auth/keys"

# Destinataires acceptes du jeton : l'identifiant de l'app iPhone. Reglable par
# variable Railway (plusieurs valeurs separees par des virgules) pour couvrir
# un futur identifiant Android/web sans toucher au code.
APPLE_AUDIENCES = [
    a.strip()
    for a in os.environ.get("APPLE_AUDIENCES", "fr.montotor.ios").split(",")
    if a.strip()
]

# Les cles publiques d'Apple tournent regulierement. PyJWKClient les telecharge
# et les garde en cache (ici 1 h) : pas d'appel reseau a chaque connexion.
_jwk_client = jwt.PyJWKClient(APPLE_KEYS_URL, lifespan=3600)


class AppleTokenInvalide(Exception):
    """Le jeton n'est pas exploitable : signature, expiration, destinataire..."""


def verifier_identity_token(identity_token: str) -> dict:
    """Verifie le jeton Apple et rend {apple_id, email, email_verified, email_prive}.

    Leve AppleTokenInvalide si le jeton ne vient pas d'Apple, a expire, ou ne
    nous est pas destine. `email` peut etre None : Apple ne le transmet pas
    systematiquement aux connexions suivant la premiere.
    """
    try:
        signing_key = _jwk_client.get_signing_key_from_jwt(identity_token)
        payload = jwt.decode(
            identity_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=APPLE_AUDIENCES,
            issuer=APPLE_ISSUER,
        )
    except Exception as e:  # PyJWTError, reseau, JWK introuvable...
        raise AppleTokenInvalide(str(e))

    apple_id = payload.get("sub")
    if not apple_id:
        raise AppleTokenInvalide("jeton sans identifiant utilisateur (sub)")

    email = (payload.get("email") or "").strip().lower() or None

    return {
        "apple_id": apple_id,
        "email": email,
        # Apple envoie tantot un booleen, tantot la chaine "true".
        "email_verified": _bool_apple(payload.get("email_verified")),
        "email_prive": _bool_apple(payload.get("is_private_email")),
    }


def _bool_apple(valeur) -> bool:
    if isinstance(valeur, bool):
        return valeur
    return str(valeur).lower() == "true"

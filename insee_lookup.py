"""
Interrogation de l'API Sirene (INSEE) pour recuperer les informations
d'une entreprise a partir de son numero SIRET.
"""

import os
import re
import requests

INSEE_API_KEY = os.environ.get("INSEE_API_KEY", "")
INSEE_API_BASE = "https://api.insee.fr/api-sirene/3.11"


class SiretLookupError(Exception):
    """Erreur levee lorsque la recherche SIRET echoue (format invalide, introuvable, etc.)."""


def clean_siret(siret: str) -> str:
    """Retire les espaces / tirets eventuels d'un SIRET saisi par l'utilisateur."""
    return re.sub(r"[^0-9]", "", siret or "")


def lookup_siret(siret: str) -> dict:
    """
    Interroge l'API Sirene pour un SIRET donne et retourne les informations
    utiles au pre-remplissage du profil utilisateur.

    Leve SiretLookupError si le SIRET est invalide, introuvable, ou si l'API
    n'est pas configuree / indisponible.
    """
    siret = clean_siret(siret)

    if len(siret) != 14:
        raise SiretLookupError("Le SIRET doit comporter exactement 14 chiffres")

    if not INSEE_API_KEY:
        raise SiretLookupError("Recherche SIRET non configuree (cle API INSEE manquante)")

    url = f"{INSEE_API_BASE}/siret/{siret}"
    headers = {"X-INSEE-Api-Key-Integration": INSEE_API_KEY}

    try:
        resp = requests.get(url, headers=headers, timeout=10)
    except requests.RequestException as e:
        raise SiretLookupError(f"Impossible de contacter l'API INSEE : {e}")

    if resp.status_code == 404:
        raise SiretLookupError("Aucun etablissement trouve pour ce SIRET")

    if resp.status_code == 401 or resp.status_code == 403:
        raise SiretLookupError("Cle API INSEE invalide ou non autorisee")

    if resp.status_code != 200:
        raise SiretLookupError(f"Erreur API INSEE (code {resp.status_code})")

    data = resp.json()
    etablissement = data.get("etablissement", {})

    unite_legale = etablissement.get("uniteLegale", {})
    adresse = etablissement.get("adresseEtablissement", {})

    # Raison sociale : personne morale -> denomination ; personne physique -> nom + prenom
    raison_sociale = unite_legale.get("denominationUniteLegale")
    if not raison_sociale:
        prenom = unite_legale.get("prenom1UniteLegale", "") or ""
        nom = unite_legale.get("nomUniteLegale", "") or ""
        raison_sociale = f"{prenom} {nom}".strip() or None

    date_creation = etablissement.get("dateCreationEtablissement")

    code_ape = unite_legale.get("activitePrincipaleUniteLegale")
    libelle_ape = unite_legale.get("nomenclatureActivitePrincipaleUniteLegale")

    adresse_parts = [
        adresse.get("numeroVoieEtablissement"),
        adresse.get("typeVoieEtablissement"),
        adresse.get("libelleVoieEtablissement"),
    ]
    adresse_ligne = " ".join(p for p in adresse_parts if p) or None

    return {
        "siret": siret,
        "siren": etablissement.get("siren"),
        "raison_sociale": raison_sociale,
        "date_creation_etablissement": date_creation,
        "code_ape": code_ape,
        "nomenclature_ape": libelle_ape,
        "adresse": adresse_ligne,
        "code_postal": adresse.get("codePostalEtablissement"),
        "commune": adresse.get("libelleCommuneEtablissement"),
        "etablissement_actif": etablissement.get("etatAdministratifEtablissement") == "A",
    }

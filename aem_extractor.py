"""
Extraction des données d'une AEM (Attestation Employeur Mensuelle) du spectacle,
via Claude Vision. Beaucoup plus fiable que le regex pour ces documents structurés
et variables d'un employeur à l'autre.

Retourne un dict :
    {
      "employeur":      str | None,   # raison sociale
      "siret":          str | None,
      "date":           "YYYY-MM-DD" | None,  # date de fin de période / dernier jour travaillé
      "type_activite":  "cachet_isole" | "cachet_groupe" | "heures",
      "nombre":         float,         # nb de cachets OU nb d'heures selon type
      "salaire_brut":   float | None,
      "filename":       str,
    }
"""

import os
import json
import base64
import mimetypes
from datetime import datetime

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()  # .strip() : cf api.py (Railway ajoute un \n en fin de valeur)
MODEL = "claude-sonnet-4-6"

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

# Instruction donnée à Claude. On lui demande UNIQUEMENT du JSON, rien d'autre,
# pour pouvoir le parser directement.
PROMPT = """Tu lis un document qui contient UNE OU PLUSIEURS AEM (Attestation Employeur Mensuelle) d'un intermittent du spectacle français. Un même document (souvent un PDF de plusieurs pages) regroupe fréquemment PLUSIEURS attestations, une par contrat.
Le document peut aussi être une attestation GUSO (Guichet Unique du Spectacle Occasionnel — l'équivalent de l'AEM pour les employeurs occasionnels, fréquent chez les musiciens) : traite-la exactement comme une AEM et extrais les mêmes champs.
Le document peut aussi être une AER CS, dite aussi AE CS (« Attestation Employeur Rematérialisée Cinéma Spectacle ») : c'est la remplaçante progressive de l'AEM (réforme 2025-2027), produite par France Travail à partir de la DSN de l'employeur. Elle regroupe parfois PLUSIEURS contrats d'un même employeur sur le mois : traite-la exactement comme une AEM ("type_document": "aem") et extrais chaque contrat/période distinct comme une entrée séparée.

Repère CHAQUE attestation distincte (chaque numéro d'attestation différent, chaque période de travail différente = une AEM séparée) et extrais-les TOUTES.

Réponds STRICTEMENT en JSON, sans aucun texte autour, sans balises Markdown. Le format est une LISTE d'objets, même s'il n'y a qu'une seule attestation :

[
  {
    "employeur": "raison sociale de l'employeur (la structure qui emploie, pas le salarié)",
    "siret": "numéro SIRET de l'employeur si présent, sinon null",
    "date": "date de DÉBUT du contrat (date d'embauche) au format YYYY-MM-DD",
    "date_fin": "date de FIN du contrat au format YYYY-MM-DD si elle est indiquée, sinon null",
    "type_activite": "cachet_isole, cachet_groupe ou heures",
    "nombre": nombre de cachets OU nombre d'heures (un nombre),
    "salaire_brut": salaire brut total de la période en euros (un nombre, sans symbole), sinon null,
    "metier": "artiste" ou "technicien" selon l'emploi occupé indiqué sur l'attestation, sinon null,
    "type_document": "aem", "guso", "cddu_usage" ou "inconnu" (voir règles)
  }
]

Règles importantes :
- Une attestation = un bloc avec son propre numéro d'attestation et sa propre période. S'il y a 2 numéros d'attestation différents, renvoie 2 objets. S'il y en a 3, renvoie 3 objets.
- "date" : c'est TOUJOURS la date de début / d'embauche (le premier jour travaillé). Elle est presque toujours présente.
- "date_fin" : c'est le dernier jour travaillé / date de fin de contrat. Si le contrat est sur un seul jour, date_fin peut être égale à date. Si elle n'est pas indiquée, mets null. Ne confonds JAMAIS début et fin : si tu n'as qu'une date, mets-la dans "date" et mets "date_fin" à null.
- "type_activite" : si l'AEM mentionne des CACHETS, utilise "cachet_isole" (cas le plus courant) ou "cachet_groupe" si explicitement groupés. Si elle est en HEURES réelles (technicien, annexe 8), utilise "heures".
- "nombre" : si ce sont des cachets, mets le NOMBRE DE CACHETS. Si ce sont des heures, mets le NOMBRE D'HEURES. Ne convertis pas toi-même.
- "metier" : regarde l'EMPLOI OCCUPÉ écrit sur l'attestation. Métiers techniques (monteur, régisseur,
  ingénieur du son, cadreur, électricien, machiniste, habilleuse, maquilleuse, décorateur…) → "technicien".
  Métiers d'interprétation ou de création artistique (comédien, musicien, danseur, chanteur,
  circassien, metteur en scène, chorégraphe…) → "artiste". Si l'emploi n'est pas indiqué ou ambigu → null.
  ATTENTION : mannequin, figurant, publicité → null (statut à part, ne devine pas). Ne déduis JAMAIS
  le métier de la case « niveau de qualification » (ex. « profession intermédiaire (techniciens…) » est
  une catégorie administrative, PAS l'emploi occupé).
- "type_document" : identifie le FORMULAIRE. « Attestation employeur pour les activités relevant des
  annexes 8 et 10 » (AEM/FCTU spectacle) → "aem". Attestation GUSO → "guso". « Attestation employeur
  ayant conclu des contrats à durée déterminée d'usage » (formulaire Unédic AE-DSN / DAJ 1260,
  art. D.1242-1 — hors spectacle : pub, mannequinat…) → "cddu_usage". Tout AUTRE document (fiche de
  paie, contrat de travail, courrier, notification de droits…) → renvoie UNIQUEMENT
  [{"type_document": "inconnu"}] et aucun autre champ.
- Si une information est absente ou illisible, mets null (sauf "nombre" : mets 0 si introuvable).
- Ne devine jamais un SIRET ou un montant : si tu n'es pas sûr, mets null.
- Réponds en JSON pur (une liste []), rien d'autre."""


PROMPT_ARE = """Tu lis une attestation/notification France Travail (ARE — Allocation de Retour à l'Emploi) d'un intermittent du spectacle français.

Trouve DEUX informations :
1. La DATE ANNIVERSAIRE : la date de prochain réexamen / renouvellement / réadmission des droits. Elle est souvent libellée "date anniversaire", "date de fin de droits", "fin de droits", "prochaine date de réexamen". Format YYYY-MM-DD.
2. Le MONTANT JOURNALIER brut de l'allocation : l'allocation journalière (AJ), parfois "montant journalier brut" ou "allocation journalière brute", en euros.

Réponds STRICTEMENT en JSON, sans aucun texte autour, sans Markdown :
{"date_anniversaire": "YYYY-MM-DD" ou null, "montant_journalier": nombre ou null}

Règles :
- Si une info est absente ou illisible, mets null. Ne devine jamais.
- "montant_journalier" : un nombre (ex : 52.34), sans symbole € ni texte.
- Réponds en JSON pur, rien d'autre."""


def extract_are_data(file_path: str) -> dict:
    """Lit une attestation ARE via Claude Vision → {date_anniversaire, montant_journalier, filename}."""
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("Lecture d'attestation indisponible : clé API non configurée.")

    import requests

    source_blocks = _build_source_blocks(file_path)

    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": MODEL,
            "max_tokens": 600,
            "messages": [{"role": "user", "content": source_blocks + [{"type": "text", "text": PROMPT_ARE}]}],
        },
        timeout=60,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Lecture impossible (code {resp.status_code}).")

    body = resp.json()
    parts = [b.get("text", "") for b in body.get("content", []) if b.get("type") == "text"]
    raw = _clean_json("".join(parts))
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise RuntimeError("Je n'ai pas réussi à lire cette attestation. Essaie une photo plus nette, ou saisis à la main.")

    if isinstance(data, list):
        data = data[0] if data else {}
    if not isinstance(data, dict):
        raise RuntimeError("Lecture impossible : format inattendu. Saisis à la main.")

    # Normalisation : date stricte YYYY-MM-DD, montant en float.
    da = data.get("date_anniversaire")
    date_anniv = None
    if isinstance(da, str) and da.strip():
        try:
            datetime.strptime(da.strip()[:10], "%Y-%m-%d")
            date_anniv = da.strip()[:10]
        except ValueError:
            date_anniv = None

    mj = data.get("montant_journalier")
    montant = None
    if isinstance(mj, (int, float)):
        montant = float(mj)
    elif isinstance(mj, str):
        s = mj.replace("€", "").replace(",", ".").strip()
        try:
            montant = float(s)
        except ValueError:
            montant = None

    return {"date_anniversaire": date_anniv, "montant_journalier": montant, "filename": os.path.basename(file_path)}


def _render_pdf_form_pages(raw: bytes, indices=None) -> list:
    """
    Rend des pages d'un PDF en images PNG, AVEC les champs de formulaire dessinés.

    Pourquoi : beaucoup d'AEM/FCTU (ex. TF1, éditées par France Travail) sont des
    formulaires PDF (AcroForm). Les valeurs saisies (employeur, SIRET, cachets…)
    vivent dans des champs de saisie, PAS dans le contenu imprimé de la page.
    Quand on envoie le PDF brut à l'IA, elle l'aplatit et ne voit qu'un gabarit
    VIDE → « attestation non reconnue ». En rendant nous-mêmes les pages avec les
    champs, l'IA voit l'attestation remplie, exactement comme un humain.

    `indices` : numéros de pages à rendre (défaut : les 15 premières, garde-fou
    historique pour les appels sans découpage type ARE).

    Retourne une liste de blocs image (base64 PNG). Liste vide si échec.
    """
    import io
    import pypdfium2 as pdfium

    blocks = []
    doc = pdfium.PdfDocument(raw)
    try:
        doc.init_forms()  # nécessaire pour que le rendu dessine les champs remplis
        if indices is None:
            indices = range(min(len(doc), 15))
        for i in indices:
            if i >= len(doc):
                break
            pil = doc[i].render(scale=2.0).to_pil()
            buf = io.BytesIO()
            pil.save(buf, format="PNG")
            data = base64.standard_b64encode(buf.getvalue()).decode("utf-8")
            blocks.append({"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": data}})
    finally:
        doc.close()
    return blocks


def _build_source_blocks(file_path: str) -> list:
    """
    Prépare les blocs de contenu (image / document) à envoyer à l'IA pour ce fichier.

    - PDF contenant un formulaire rempli → pages rendues en images (voir
      _render_pdf_form_pages). C'est le cas des FCTU/AEM France Travail.
    - PDF « normal » (contenu imprimé) → bloc document PDF natif (multi-pages géré
      par l'API, comportement éprouvé pour les autres employeurs).
    - Image → bloc image.
    """
    ext = os.path.splitext(file_path)[1].lower()
    with open(file_path, "rb") as f:
        raw = f.read()

    if ext == ".pdf":
        # Détecte un formulaire PDF (AcroForm/XFA). Si oui, on rend les pages avec
        # les champs. En cas de souci, on retombe proprement sur le PDF natif.
        try:
            import pypdfium2 as pdfium
            doc = pdfium.PdfDocument(raw)
            has_form = doc.get_formtype() != 0
            doc.close()
            if has_form:
                blocks = _render_pdf_form_pages(raw)
                if blocks:
                    return blocks
        except Exception:
            pass  # repli sur le document natif ci-dessous
        b64 = base64.standard_b64encode(raw).decode("utf-8")
        return [{"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": b64}}]

    b64 = base64.standard_b64encode(raw).decode("utf-8")
    media_type = mimetypes.guess_type(file_path)[0] or "image/jpeg"
    return [{"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}}]


def _clean_json(text: str) -> str:
    """Retire d'éventuelles balises Markdown ```json ... ``` autour du JSON."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1] if "```" in t[3:] else t[3:]
        if t.startswith("json"):
            t = t[4:]
    return t.strip().strip("`").strip()


def _normalise(data: dict, filename: str) -> dict:
    """Nettoie et borne les valeurs renvoyées par le modèle."""
    type_act = data.get("type_activite") or "cachet_isole"
    if type_act not in ("cachet_isole", "cachet_groupe", "heures"):
        type_act = "cachet_isole"

    # nombre
    try:
        nombre = float(data.get("nombre") or 0)
        if nombre < 0:
            nombre = 0.0
    except (TypeError, ValueError):
        nombre = 0.0

    # salaire
    brut = data.get("salaire_brut")
    try:
        brut = float(brut) if brut is not None else None
        if brut is not None and brut < 0:
            brut = None
    except (TypeError, ValueError):
        brut = None

    # date (début / embauche) — sert de référence pour le calcul
    date_str = data.get("date")
    date_iso = None
    if date_str:
        try:
            date_iso = datetime.strptime(str(date_str)[:10], "%Y-%m-%d").date().isoformat()
        except ValueError:
            date_iso = None

    # date_fin (dernier jour) — purement pour l'affichage "du X au Y"
    date_fin_str = data.get("date_fin")
    date_fin_iso = None
    if date_fin_str:
        try:
            date_fin_iso = datetime.strptime(str(date_fin_str)[:10], "%Y-%m-%d").date().isoformat()
        except ValueError:
            date_fin_iso = None
    # Si la fin est avant le début (lecture inversée), on l'ignore plutôt que d'afficher une absurdité.
    if date_iso and date_fin_iso and date_fin_iso < date_iso:
        date_fin_iso = None
    # Si la fin égale le début (contrat d'un jour), pas besoin de l'afficher comme une période.
    if date_fin_iso and date_iso and date_fin_iso == date_iso:
        date_fin_iso = None

    # métier : uniquement les deux valeurs connues (sinon None — jamais de devinette).
    metier = data.get("metier")
    if metier not in ("artiste", "technicien"):
        metier = None

    # type de document : valeurs connues uniquement (défaut "aem" — rétro-compatible).
    type_doc = data.get("type_document")
    if type_doc not in ("aem", "guso", "cddu_usage"):
        type_doc = "aem"

    return {
        "type_document": type_doc,
        "employeur": (data.get("employeur") or None),
        "siret": (data.get("siret") or None),
        "date": date_iso,
        "date_fin": date_fin_iso,
        "type_activite": type_act,
        "nombre": nombre,
        "salaire_brut": brut,
        "metier": metier,
        "filename": filename,
    }


# Découpage des gros PDF : pages par appel au modèle, avec 1 page de chevauchement
# (une AEM à cheval sur deux lots est ainsi vue en entier dans au moins un lot).
_LOT_PAGES = 6
_LOT_CHEVAUCHEMENT = 1
_MAX_PAGES_DOCUMENT = 40


# Pauses (secondes) avant les tentatives 2 et 3 de chaque appel au modèle.
# Vécu du 23/07 : sur 3 fichiers envoyés coup sur coup, un appel peut prendre un
# refus passager (surcharge API, réseau) — un simple réessai suffit presque toujours.
_RETRY_PAUSES = (2.0, 5.0)


def _appeler_modele_aem(source_blocks: list) -> list:
    """Un appel au modèle sur un lot de pages/images → liste d'objets AEM bruts.
    Réessaie automatiquement (3 tentatives au total) avant d'abandonner.
    Lève RuntimeError si la lecture échoue encore après les réessais."""
    import time
    import requests  # déjà présent dans les dépendances backend

    derniere_erreur = None
    for tentative in range(len(_RETRY_PAUSES) + 1):
        if tentative > 0:
            time.sleep(_RETRY_PAUSES[tentative - 1])
        try:
            resp = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    # max_tokens 4000 : une liasse d'une dizaine d'AEM tient sans être
                    # tronquée (l'ancien plafond de 1500 coupait le JSON en plein vol).
                    "model": MODEL,
                    "max_tokens": 4000,
                    "messages": [
                        {
                            "role": "user",
                            "content": source_blocks + [{"type": "text", "text": PROMPT}],
                        }
                    ],
                },
                timeout=90,
            )
        except requests.RequestException:
            derniere_erreur = RuntimeError("Lecture impossible (connexion). Réessaie dans un instant.")
            continue

        if resp.status_code != 200:
            derniere_erreur = RuntimeError(f"Lecture impossible (code {resp.status_code}).")
            continue

        body = resp.json()
        parts = [b.get("text", "") for b in body.get("content", []) if b.get("type") == "text"]
        raw = _clean_json("".join(parts))
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            derniere_erreur = RuntimeError("Je n'ai pas réussi à lire cette AEM. Essaie une photo plus nette, ou saisis à la main.")
            continue
        if isinstance(data, dict):
            data = [data]
        if not isinstance(data, list):
            derniere_erreur = RuntimeError("Lecture impossible : format inattendu. Saisis à la main.")
            continue
        return data

    raise derniere_erreur


def _lots_de_pages(nb_pages: int) -> list:
    """Découpe [0..nb_pages) en lots de _LOT_PAGES avec chevauchement d'une page.
    Ex. 20 pages → [0..5], [5..10], [10..15], [15..19]."""
    lots = []
    debut = 0
    while debut < nb_pages:
        fin = min(debut + _LOT_PAGES, nb_pages)
        lots.append(list(range(debut, fin)))
        if fin >= nb_pages:
            break
        debut = fin - _LOT_CHEVAUCHEMENT
    return lots


def _cle_dedup(item: dict):
    """Clé d'identité d'une AEM extraite, pour écarter les doublons créés par le
    chevauchement des lots (même employeur, mêmes dates, même volume, même brut)."""
    return (
        (item.get("employeur") or "").strip().lower(),
        item.get("date"),
        item.get("date_fin"),
        item.get("type_activite"),
        item.get("nombre"),
        item.get("salaire_brut"),
    )


def extract_aem_data(file_path: str) -> dict:
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("Lecture d'AEM indisponible : clé API non configurée.")

    ext = os.path.splitext(file_path)[1].lower()
    data = None

    if ext == ".pdf":
        with open(file_path, "rb") as f:
            raw_pdf = f.read()
        try:
            import pypdfium2 as pdfium
            doc = pdfium.PdfDocument(raw_pdf)
            nb_pages = len(doc)
            has_form = doc.get_formtype() != 0
            doc.close()
        except Exception:
            nb_pages, has_form = 0, False

        if nb_pages > _MAX_PAGES_DOCUMENT:
            raise RuntimeError(
                f"Ce document fait {nb_pages} pages, c'est trop pour une seule lecture "
                f"(maximum {_MAX_PAGES_DOCUMENT}). Envoie-le en plusieurs fois."
            )

        # Formulaire PDF (champs remplis) OU liasse de plus de _LOT_PAGES pages :
        # on rend les pages en images et on lit lot par lot. Avant, tout partait en
        # un seul appel : les pages au-delà de 15 étaient JETÉES en silence et une
        # réponse trop longue était tronquée → les dernières AEM d'une liasse
        # disparaissaient (bug remonté par une utilisatrice le 23/07/2026).
        if nb_pages > 0 and (has_form or nb_pages > _LOT_PAGES):
            data = []
            for lot in _lots_de_pages(nb_pages):
                blocks = _render_pdf_form_pages(raw_pdf, lot)
                if not blocks:
                    raise RuntimeError(
                        f"Je n'ai pas réussi à lire les pages {lot[0] + 1} à {lot[-1] + 1}. "
                        "Réessaie, ou envoie ce document en plusieurs fois."
                    )
                # Le réessai automatique vit DANS _appeler_modele_aem (3 tentatives).
                data.extend(_appeler_modele_aem(blocks))

    if data is None:
        # Image, ou PDF court sans formulaire : un seul appel, comme avant.
        data = _appeler_modele_aem(_build_source_blocks(file_path))

    fname = os.path.basename(file_path)

    # Garde-fou : document reconnu comme N'ÉTANT PAS une attestation employeur (fiche de paie,
    # contrat, courrier…) → on le dit honnêtement au lieu d'extraire des données fausses en silence.
    items = [item for item in data if isinstance(item, dict)]
    if items and all(item.get("type_document") == "inconnu" for item in items):
        raise RuntimeError(
            "Je ne sais pas encore lire ce type de document — ça ne ressemble pas à une attestation "
            "employeur que je connais. Tu peux saisir ses heures à la main dans « Mes activités »."
        )

    resultats = [_normalise(item, fname) for item in items]
    # On écarte les entrées totalement vides (ni date, ni nombre exploitable).
    resultats = [r for r in resultats if r.get("date") or (r.get("nombre") or 0) > 0]
    # Dédoublonnage : le chevauchement des lots peut faire lire deux fois la même
    # AEM. Deux attestations distinctes gardent des dates différentes → conservées.
    vus, uniques = set(), []
    for r in resultats:
        cle = _cle_dedup(r)
        if cle in vus:
            continue
        vus.add(cle)
        uniques.append(r)
    resultats = uniques
    if not resultats:
        raise RuntimeError("Je n'ai rien trouvé d'exploitable sur ce document. Essaie une photo plus nette, ou saisis à la main.")
    return resultats

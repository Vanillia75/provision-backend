# ─────────────────────────────────────────────────────────────────────────────
#  MARCHÉS PUBLICS : deux services pour les auto-entrepreneurs.
#
#  1. LES OPPORTUNITÉS (source BOAMP) : les avis de marché en cours, quand une
#     mairie, un musée ou un office de tourisme cherche un prestataire.
#     API ouverte, sans clé, Licence Ouverte 2.0 (réutilisation commerciale
#     autorisée, attribution obligatoire). Vérifié le 06/08/2026.
#
#  2. LA CARTE AU TRÉSOR (source DECP) : les marchés DÉJÀ ATTRIBUÉS, avec leur
#     montant. Ce ne sont pas des opportunités ouvertes, c'est une aide au
#     démarchage : « ces collectivités près de chez toi ont commandé de la
#     photo cette année, pour ces montants ». Elle existe parce que les petits
#     marchés de services (sous 60 000 € HT depuis le 1er avril 2026, décret
#     n° 2025-1386 du 29/12/2025 ; c'était 40 000 € avant) sont dispensés de
#     publicité : ils ne s'annoncent nulle part, on ne peut que démarcher.
#
#  ⚠️ HONNÊTETÉ SUR LE VOLUME (mesuré le 06/08/2026, à ne pas oublier au moment
#     d'écrire les textes de l'app) : environ 4 avis créatifs par jour pour la
#     France entière, et le gisement recule d'environ 35 % par an à cause du
#     relèvement du seuil de publicité. Un utilisateur filtré sur son
#     département verra quelques opportunités par mois. C'est une VEILLE, pas
#     un flux d'emploi : ne jamais promettre autre chose.
# ─────────────────────────────────────────────────────────────────────────────
import re
import time
import unicodedata
from datetime import date, timedelta
from typing import Optional

import requests

BOAMP_URL = "https://boamp-datadila.opendatasoft.com/api/explore/v2.1/catalog/datasets/boamp/records"
DECP_URL = "https://data.economie.gouv.fr/api/explore/v2.1/catalog/datasets/decp-2022-marches-valides/records"
ANNUAIRE_URL = "https://recherche-entreprises.api.gouv.fr/search"
HTTP_TIMEOUT = 20

# Mention légale imposée par la Licence Ouverte 2.0 : citer la source.
ATTRIBUTION = "Source : BOAMP / DILA et données essentielles de la commande publique, sous Licence Ouverte 2.0."

# ─── Les univers de métier ────────────────────────────────────────────────────
#  Chaque univers porte ses mots-clés (pour chercher dans l'objet des avis) et
#  ses codes CPV (pour les marchés attribués, où le CPV est renseigné).
#  Le CPV est mal saisi par les acheteurs (on a trouvé une table de radiologie
#  classée en « photographie ») : il sert de filtre large, jamais de preuve.
UNIVERS = {
    "photo_video": {
        "libelle": "Photo et vidéo",
        "mots": ["photograph", "reportage photo", "captation", "audiovisuel",
                 "vidéo", "video", "film", "tournage", "prise de vue"],
        "cpv": ["79961", "92111", "92112"],
    },
    "graphisme": {
        "libelle": "Graphisme et identité visuelle",
        # ⚠️ Lister les variantes en toutes lettres : le raccourci « graphis »
        # ne reconnaissait pas « création graphiQUE » (attrapé par les tests).
        "mots": ["graphique", "graphisme", "graphiste", "identité visuelle",
                 "charte graphique", "logotype", "maquette", "illustration",
                 "signalétique", "signaletique", "mise en page", "direction artistique"],
        "cpv": ["79822", "79821", "22462"],
    },
    "communication": {
        "libelle": "Communication",
        # « édition » a été retiré : il attrapait « Fête de la lentille, Edition
        # 2026 », où le mot désigne le millésime et pas le métier.
        "mots": ["communication", "relations presse", "réseaux sociaux",
                 "community management", "stratégie de marque", "rédaction de contenus"],
        "cpv": ["79341", "79416", "79340"],
    },
    "formation": {
        "libelle": "Formation",
        "mots": ["formation", "formateur", "animation d'atelier", "pédagog"],
        "cpv": ["80500", "80510", "80530"],
    },
    "web": {
        "libelle": "Web et développement",
        "mots": ["site internet", "site web", "développement web", "application mobile",
                 "refonte de site", "webdesign"],
        "cpv": ["72413", "72212", "72400"],
    },
    "evenementiel": {
        "libelle": "Événementiel et spectacle",
        # « animation » seul a été retiré : il attrapait l'animation périscolaire
        # et la garde d'enfants, qui ne sont pas des prestations créatives.
        "mots": ["scénographie", "scenographie", "spectacle vivant", "sonorisation",
                 "régie technique", "regie technique", "captation de spectacle",
                 "programmation artistique", "animation culturelle"],
        "cpv": ["79952", "92312", "92320"],
    },
}

# Seuls les avis qui OUVRENT une consultation intéressent l'utilisateur.
# « Résultat de marché », « Rectificatif », « Avis d'annulation » n'en sont pas.
NATURES_OPPORTUNITE = ("Avis de marché",)

# ⚠️ LE PIÈGE DES MARCHÉS DE FOURNITURE (constaté au premier essai réel).
#  Les mots-clés seuls ramènent surtout de l'achat de matériel : « fourniture et
#  pose de signalétique » n'est pas du graphisme, c'est de la pose de panneaux ;
#  « fourniture de livres pédagogiques » n'est pas de la formation. Sur le
#  premier échantillon, 3 avis sur 4 étaient de faux positifs de ce type.
#  Un indépendant vend son TRAVAIL, pas des biens : ces marchés lui sont fermés,
#  et lui en montrer un seul abîme la confiance dans toute la carte.
MOTS_FOURNITURE = [
    "fourniture", "fournitures", "acquisition", "achat", "livraison",
    "matériel", "materiel", "équipement", "equipement", "mobilier",
    "installation", "pose ", "maintenance", "entretien", "nettoyage",
    "location", "travaux", "réhabilitation", "rehabilitation", "construction",
    "véhicule", "vehicule", "consommable", "logiciel", "licence",
]

# Deuxième famille de faux positifs, trouvée au second essai réel : des services
# bien réels, mais qui n'ont rien de créatif. « Animation » attrape la garde
# d'enfants périscolaire, « événementiel » attrape le gardiennage et le transport
# de festival. Ce sont des métiers respectables, simplement pas ceux de nos
# utilisateurs, et les afficher ferait douter de toute la carte.
MOTS_HORS_SUJET = [
    "sécurité", "securite", "surveillance", "gardiennage", "sûreté", "surete",
    "transport", "navette", "restauration", "repas", "cantine",
    "périscolaire", "periscolaire", "accueil de loisirs", "centre de loisirs",
    "petite enfance", "crèche", "creche", "ménage", "menage",
    "assurance", "bancaire", "intérim", "interim", "recrutement",
]


def univers_disponibles() -> list:
    return [{"cle": k, "libelle": v["libelle"]} for k, v in UNIVERS.items()]


def _norm(s: str) -> str:
    """Minuscules sans accent : les avis mélangent MAJUSCULES et accents."""
    s = unicodedata.normalize("NFD", (s or "").lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def _est_fourniture(objet: str) -> bool:
    """Vrai si l'avis vend des BIENS ou un service sans rapport avec nos métiers."""
    texte = _norm(objet)
    return any(_norm(m) in texte for m in MOTS_FOURNITURE + MOTS_HORS_SUJET)


def _correspond(objet: str, cles_univers: list) -> Optional[str]:
    """L'univers de l'avis, ou None s'il ne concerne pas un indépendant.

    Deux filtres : le marché doit parler d'un de nos métiers, ET ne pas être
    un marché de fourniture (cf. MOTS_FOURNITURE, le piège du premier essai).
    """
    if _est_fourniture(objet):
        return None
    texte = _norm(objet)
    for cle in cles_univers:
        for mot in UNIVERS[cle]["mots"]:
            if _norm(mot) in texte:
                return cle
    return None


def _departement_du_code_postal(code_postal: str) -> Optional[str]:
    """75018 → 75, 20000 → 2A/2B non géré (on renvoie 20), 97400 → 974."""
    cp = re.sub(r"\D", "", code_postal or "")
    if len(cp) < 5:
        return None
    return cp[:3] if cp.startswith("97") or cp.startswith("98") else cp[:2]


def _appel(url: str, params: dict) -> dict:
    """Un appel, avec une seule reprise en cas de limite de débit."""
    for essai in range(2):
        r = requests.get(url, params=params, timeout=HTTP_TIMEOUT)
        if r.status_code == 429:
            time.sleep(2)
            continue
        if r.status_code == 200:
            return r.json()
        return {}
    return {}


# ─── 1. Les opportunités ouvertes (BOAMP) ─────────────────────────────────────
def opportunites(departement: Optional[str] = None,
                 univers_choisis: Optional[list] = None,
                 jours: int = 45,
                 limite: int = 10,
                 depuis_le: int = 0) -> dict:
    """Avis de marché récents correspondant aux métiers demandés.

    departement : « 75 », « 974 »... None = France entière.
    univers_choisis : clés d'UNIVERS. None = tous.
    """
    cles = [c for c in (univers_choisis or list(UNIVERS)) if c in UNIVERS]
    if not cles:
        cles = list(UNIVERS)

    depuis = (date.today() - timedelta(days=jours)).isoformat()
    conditions = [
        f'dateparution >= "{depuis}"',
        'nature_libelle = "Avis de marché"',
        # Prestations de SERVICES uniquement : un indépendant vend son travail,
        # pas des biens (FOURNITURES) ni du chantier (TRAVAUX).
        'type_marche = "SERVICES"',
    ]
    if departement:
        conditions.append(f'code_departement = "{departement}"')

    # ⚠️ La recherche des mots-clés se fait CÔTÉ SERVEUR, sinon on ne voit rien.
    #  Le BOAMP publie environ 10 000 avis par mois, tous métiers confondus :
    #  demander « les 100 plus récents » puis filtrer chez nous ne ramenait que
    #  2 ou 3 avis créatifs (constaté au premier essai). On demande donc
    #  directement les avis qui contiennent nos mots.
    mots = {m for cle in cles for m in UNIVERS[cle]["mots"]}
    recherche = " or ".join(f'search(objet, "{m}")' for m in sorted(mots))
    conditions.append(f"({recherche})")

    data = _appel(BOAMP_URL, {
        "where": " and ".join(conditions),
        "order_by": "dateparution desc",
        "limit": 100,
        "select": ("idweb,objet,nomacheteur,dateparution,datelimitereponse,"
                   "url_avis,code_departement,famille_libelle,type_marche"),
    })

    # On garde TOUT ce qui correspond, puis on découpe en pages : le total sert
    # à savoir s'il faut proposer « voir la suite ».
    retenus = []
    for a in data.get("results", []):
        objet = (a.get("objet") or "").strip()
        cle = _correspond(objet, cles)
        if not cle:
            continue
        deps = a.get("code_departement") or []
        retenus.append({
            "id": a.get("idweb"),
            "objet": objet[:220],
            "acheteur": (a.get("nomacheteur") or "").strip(),
            "departement": deps[0] if isinstance(deps, list) and deps else None,
            "publie_le": a.get("dateparution"),
            "limite_le": a.get("datelimitereponse"),
            "univers": cle,
            "univers_libelle": UNIVERS[cle]["libelle"],
            "petit_marche": (a.get("famille_libelle") or "").startswith("MAPA"),
            "lien": a.get("url_avis"),
        })

    page = retenus[depuis_le:depuis_le + limite]
    return {
        "disponible": True,
        "opportunites": page,
        "total": len(retenus),
        "reste": max(0, len(retenus) - (depuis_le + len(page))),
        "attribution": ATTRIBUTION,
    }


# ─── 2. La carte au trésor (DECP) ─────────────────────────────────────────────
def _nom_acheteur(siret: str) -> Optional[str]:
    """Traduit un SIRET en nom lisible via l'annuaire public des entreprises.

    Le jeu de données des marchés ne porte QUE le SIRET de l'acheteur, or
    « 59820132500100 » ne parle à personne. On tente le SIRET complet puis le
    SIREN seul (beaucoup de collectivités changent d'établissement).
    """
    for q in (siret, (siret or "")[:9]):
        if not q or len(q) < 9:
            continue
        data = _appel(ANNUAIRE_URL, {"q": q, "per_page": 1})
        for e in data.get("results", []):
            nom = e.get("nom_raison_sociale") or e.get("nom_complet")
            if nom:
                commune = (e.get("siege") or {}).get("libelle_commune")
                return f"{nom} ({commune})" if commune else nom
    return None


def acheteurs_proches(departement: str,
                      univers_choisis: Optional[list] = None,
                      mois: int = 18,
                      limite: int = 12) -> dict:
    """Qui a acheté ce métier près de chez toi, et pour quel montant.

    Ce ne sont PAS des opportunités ouvertes : ces marchés sont déjà attribués.
    C'est une liste d'acheteurs à démarcher, utile précisément parce que leurs
    petits marchés suivants ne seront jamais publiés.
    """
    cles = [c for c in (univers_choisis or list(UNIVERS)) if c in UNIVERS]
    if not cles:
        cles = list(UNIVERS)
    if not departement:
        return {"disponible": False, "raison": "departement_absent"}

    cpv = [c for cle in cles for c in UNIVERS[cle]["cpv"]]
    ou_cpv = " or ".join(f'codecpv like "{c}*"' for c in cpv)
    depuis = (date.today() - timedelta(days=30 * mois)).isoformat()

    # ⚠️ « lieuexecution_code » n'a PAS un format unique : les acheteurs y mettent
    #  tantôt le département seul (« 33 »), tantôt le code INSEE ou postal de la
    #  commune (« 33000 »), parfois un code pays (« IE »). Mesuré le 07/08/2026
    #  sur 424 marchés : 49 % en code commune, 49 % en département nu. Filtrer sur
    #  l'égalité seule perdait donc la moitié du gisement. On prend les deux.
    lieu = (f'(lieuexecution_code = "{departement}" '
            f'or lieuexecution_code like "{departement}*")')
    data = _appel(DECP_URL, {
        "where": (f'({ou_cpv}) and datenotification >= "{depuis}" '
                  f'and {lieu}'),
        "order_by": "datenotification desc",
        "limit": 60,
        "select": "objet,montant,acheteur_id,datenotification,codecpv,procedure,dureemois",
    })

    vus = set()
    sortie = []
    for m in data.get("results", []):
        objet = (m.get("objet") or "").strip()
        # Le CPV est mal saisi par les acheteurs : on reconfirme sur l'objet.
        cle = _correspond(objet, cles)
        if not cle:
            continue
        siret = str(m.get("acheteur_id") or "")
        if siret in vus:          # un acheteur n'apparaît qu'une fois
            continue
        vus.add(siret)
        montant = m.get("montant")
        sortie.append({
            "objet": objet[:180],
            "acheteur": _nom_acheteur(siret) or f"Acheteur public {siret[:9]}",
            "montant": round(float(montant), 2) if montant else None,
            "notifie_le": m.get("datenotification"),
            "duree_mois": m.get("dureemois"),
            "univers": cle,
            "univers_libelle": UNIVERS[cle]["libelle"],
        })
        if len(sortie) >= limite:
            break

    return {"disponible": True, "acheteurs": sortie, "attribution": ATTRIBUTION}

# ════════════════════════════════════════════════════════════════════════
#  Marchés publics : le TRI est tout l'enjeu.
#
#  Le volume est faible (environ 4 avis créatifs par jour en France), donc
#  chaque faux positif compte double : un utilisateur qui ouvre la carte et
#  tombe sur « fourniture de mobilier scolaire » ne revient pas.
#
#  Ces tests figent les deux familles de faux positifs trouvées lors des
#  essais réels du 06/08/2026 :
#    1. les marchés de FOURNITURE (on vend des biens, pas du travail),
#    2. les services HORS SUJET (sécurité, transport, périscolaire).
#  Aucun appel réseau ici : on teste la logique de tri, pas l'API.
# ════════════════════════════════════════════════════════════════════════
import pytest

from marches_publics import (
    UNIVERS,
    _correspond,
    _departement_du_code_postal,
    _est_fourniture,
    _norm,
    univers_disponibles,
)


# ── Les vraies opportunités doivent passer ──────────────────────────────

@pytest.mark.parametrize("objet, univers_attendu", [
    ("Réalisation de reportages photographiques pour la ville", "photo_video"),
    ("PRESTATIONS DE CAPTATION VIDÉO ET POST PRODUCTION", "photo_video"),
    ("Conseil, conception et création graphique des supports", "graphisme"),
    ("Refonte de l'identité visuelle du domaine national", "graphisme"),
    ("Rédaction de contenus pour le futur site internet", "communication"),
    ("Réalisation d'actions de formation sur le thème du management", "formation"),
    ("Programmation artistique et production exécutive du festival", "evenementiel"),
])
def test_les_vraies_prestations_sont_reconnues(objet, univers_attendu):
    assert _correspond(objet, list(UNIVERS)) == univers_attendu


# ── Les marchés de fourniture doivent être écartés ──────────────────────

@pytest.mark.parametrize("objet", [
    "Fourniture, pose et maintenance de la signalétique interne",
    "Fourniture de livres scolaires et pédagogiques",
    "Acquisition de matériel photographique pour le service communication",
    "Achat de véhicules pour la régie municipale",
    "Location de matériel de sonorisation pour la salle des fêtes",
    "Travaux de réhabilitation du théâtre municipal",
    "Maintenance des logiciels de gestion documentaire",
])
def test_les_marches_de_fourniture_sont_ecartes(objet):
    """Un indépendant vend son travail : ces marchés lui sont fermés."""
    assert _est_fourniture(objet) is True
    assert _correspond(objet, list(UNIVERS)) is None


# ── Les services hors sujet doivent être écartés ────────────────────────

@pytest.mark.parametrize("objet", [
    "Prestations de sécurité des manifestations événementielles",
    "Exploitation de services de transports saisonniers et événementiels",
    "Prestations d'accueil et d'animation périscolaires dans les écoles",
    "Animation des activités des enfants en accueil de loisirs",
    "Restauration collective et fourniture de repas",
    "Prestations de gardiennage et de surveillance des sites",
])
def test_les_services_hors_sujet_sont_ecartes(objet):
    """Métiers respectables, mais pas ceux de nos utilisateurs."""
    assert _correspond(objet, list(UNIVERS)) is None


# ── Les pièges de vocabulaire déjà rencontrés ───────────────────────────

def test_le_mot_edition_ne_designe_pas_toujours_le_metier():
    """« Fête de la lentille, Edition 2026 » : édition = millésime."""
    assert _correspond("Organisation de la fête de la lentille, Edition 2026",
                       list(UNIVERS)) is None


def test_le_filtrage_ignore_les_accents_et_la_casse():
    """Les avis sont écrits en MAJUSCULES, avec ou sans accents."""
    assert _correspond("PRESTATIONS DE PHOTOGRAPHIE", list(UNIVERS)) == "photo_video"
    assert _correspond("prestations de photographie", list(UNIVERS)) == "photo_video"
    assert _correspond("REALISATION DE REPORTAGES PHOTOGRAPHIQUES", list(UNIVERS)) == "photo_video"


def test_on_ne_cherche_que_dans_les_univers_demandes():
    objet = "Réalisation de reportages photographiques"
    assert _correspond(objet, ["photo_video"]) == "photo_video"
    assert _correspond(objet, ["formation", "web"]) is None


def test_normalisation():
    assert _norm("Événementiel") == "evenementiel"
    assert _norm("PHOTOGRAPHIE") == "photographie"
    assert _norm(None) == ""


# ── Le département déduit du code postal ────────────────────────────────

@pytest.mark.parametrize("code_postal, departement", [
    ("75018", "75"),
    ("33000", "33"),
    ("97400", "974"),   # La Réunion, sur trois chiffres
    ("98800", "988"),   # Nouvelle-Calédonie
    ("01000", "01"),
    ("75018 Paris", "75"),
])
def test_departement_du_code_postal(code_postal, departement):
    assert _departement_du_code_postal(code_postal) == departement


@pytest.mark.parametrize("entree", ["", None, "Paris", "750"])
def test_code_postal_illisible_ne_casse_rien(entree):
    assert _departement_du_code_postal(entree) is None


# ── Le catalogue exposé à l'application ─────────────────────────────────

def test_les_univers_sont_exposes_avec_un_libelle_lisible():
    dispo = univers_disponibles()
    assert len(dispo) == len(UNIVERS)
    for u in dispo:
        assert u["cle"] in UNIVERS
        assert u["libelle"] and len(u["libelle"]) > 3


def test_chaque_univers_a_des_mots_et_des_codes():
    """Sans mots-clés, l'univers ne trouverait rien ; sans code, pas de trésor."""
    for cle, u in UNIVERS.items():
        assert u["mots"], f"{cle} n'a aucun mot-clé"
        assert u["cpv"], f"{cle} n'a aucun code d'achat public"
        for mot in u["mots"]:
            assert mot == mot.lower(), f"{cle} : « {mot} » doit être en minuscules"


# ── Le lieu d'exécution : le piège des deux formats ─────────────────────
#
#  Découvert le 06/08/2026 en rapatriant 424 marchés réels : les acheteurs
#  renseignent le lieu tantôt en département nu (« 33 »), tantôt en code de
#  commune (« 33000 »), moitié-moitié. Filtrer sur la seule égalité perdait
#  donc la moitié du gisement : la Gironde passait de 100 marchés à 27, le
#  Rhône de 218 à 38. Ce test empêche quiconque de « simplifier » la requête.

def test_le_filtre_de_lieu_accepte_les_deux_formats(monkeypatch):
    from marches_publics import acheteurs_proches

    vu = {}

    def faux_appel(url, params):
        vu["where"] = params.get("where", "")
        return {"results": []}

    monkeypatch.setattr("marches_publics._appel", faux_appel)
    acheteurs_proches("33", ["photo_video"])

    where = vu["where"]
    assert 'lieuexecution_code = "33"' in where, "le département nu doit rester accepté"
    assert 'lieuexecution_code like "33*"' in where, "les codes de commune doivent l'être aussi"
    assert " or " in where, "les deux formats doivent être en OU, pas en ET"


def test_sans_departement_la_carte_au_tresor_se_tait():
    """Mieux vaut ne rien afficher que d'afficher la France entière en vrac."""
    from marches_publics import acheteurs_proches

    assert acheteurs_proches("", ["photo_video"])["disponible"] is False
    assert acheteurs_proches(None)["disponible"] is False

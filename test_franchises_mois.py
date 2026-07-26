# ════════════════════════════════════════════════════════════════════════
#  Tests des franchises (différés d'indemnisation) dans l'estimation du mois.
#  Règles officielles (annexe X art. 29 §1 et 31 §2) : seuls les jours
#  INDEMNISABLES servent à leur computation ; ordre = congés payés puis
#  salaires ; CP à 2 j/mois (3 si le total dépasse 24 j) ; salaires étalés
#  sur 8 mois. Elles ne sont JAMAIS devinées : l'utilisateur les recopie.
# ════════════════════════════════════════════════════════════════════════
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models import User, Profile
from allocation_engine import calculer_aj, estimer_mois_civil
import api

RES_AJ = calculer_aj("annexe10", sr=5200.0, nht=480.0)   # zone validée, AJ ≤ 60 €


# ── Le moteur ────────────────────────────────────────────────────────────

def test_sans_franchise_rien_ne_change():
    r = estimer_mois_civil("annexe10", RES_AJ, [], 2026, 7)
    assert r["jours_indemnisables"] == 31
    assert r["franchise_cp_imputee"] == 0 and r["franchise_salaires_imputee"] == 0
    assert r["franchises_declarees"] is False


def test_franchise_cp_petit_total_2_jours_par_mois():
    r = estimer_mois_civil("annexe10", RES_AJ, [], 2026, 7, franchise_cp_jours=9)
    assert r["franchise_cp_imputee"] == 2                 # total 9 ≤ 24 → rythme 2
    assert r["jours_indemnisables"] == 29
    assert r["franchise_cp_restante_apres"] == 7.0        # ce que dira le prochain relevé
    assert r["franchises_declarees"] is True


def test_franchise_cp_gros_total_3_jours_par_mois():
    r = estimer_mois_civil("annexe10", RES_AJ, [], 2026, 7, franchise_cp_jours=30)
    assert r["franchise_cp_imputee"] == 3                 # total > 24 → rythme 3
    assert r["jours_indemnisables"] == 28


def test_franchise_salaires_etalee_sur_huit_mois():
    # 16 jours de différé salaires → 16/8 = 2 jours ce mois-ci.
    r = estimer_mois_civil("annexe10", RES_AJ, [], 2026, 7, franchise_salaires_jours=16)
    assert r["franchise_salaires_imputee"] == 2
    assert r["jours_indemnisables"] == 29
    assert r["franchise_salaires_restante_apres"] == 14.0


def test_les_deux_franchises_se_cumulent_dans_le_bon_ordre():
    r = estimer_mois_civil("annexe10", RES_AJ, [], 2026, 7,
                           franchise_cp_jours=9, franchise_salaires_jours=16)
    assert r["franchise_cp_imputee"] == 2                 # CP d'abord
    assert r["franchise_salaires_imputee"] == 2           # salaires ensuite
    assert r["jours_indemnisables"] == 27                 # 31 − 2 − 2


def test_la_franchise_ampute_bien_le_montant():
    sans = estimer_mois_civil("annexe10", RES_AJ, [], 2026, 7)
    avec = estimer_mois_civil("annexe10", RES_AJ, [], 2026, 7, franchise_cp_jours=9)
    assert avec["net_estime"] == round(29 * RES_AJ["aj_nette"], 2)
    assert avec["net_estime"] < sans["net_estime"]


def test_franchise_apres_le_decalage_du_travail():
    # 3 cachets (36 h) → 4 jours de décalage, PUIS la franchise sur ce qui reste.
    acts = [{"date": date(2026, 7, 10), "type_activite": "cachet_isole", "nombre": 3, "salaire_brut": 300.0}]
    r = estimer_mois_civil("annexe10", RES_AJ, acts, 2026, 7, franchise_cp_jours=9)
    assert r["jours_non_indemnisables"] == 4
    assert r["franchise_cp_imputee"] == 2
    assert r["jours_indemnisables"] == 25                 # 31 − 4 − 2


def test_franchise_ne_descend_jamais_sous_zero():
    # Reste 1 jour de franchise : on n'en impute qu'un, pas deux.
    r = estimer_mois_civil("annexe10", RES_AJ, [], 2026, 7, franchise_cp_jours=1)
    assert r["franchise_cp_imputee"] == 1
    assert r["franchise_cp_restante_apres"] == 0.0


def test_mois_sans_jour_indemnisable_ne_consomme_pas_de_franchise():
    # Seuil de non-indemnisation atteint : la franchise ne se consomme pas
    # (seuls les jours indemnisables la font courir).
    acts = [{"date": date(2026, 7, 1), "type_activite": "cachet_isole", "nombre": 23, "salaire_brut": 2300.0}]
    r = estimer_mois_civil("annexe10", RES_AJ, acts, 2026, 7, franchise_cp_jours=9)
    assert r["seuil_atteint"] is True
    assert r["franchise_cp_imputee"] == 0
    assert r["franchise_cp_restante_apres"] == 9.0


# ── L'enregistrement (route) ─────────────────────────────────────────────

@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _user(db):
    u = User(email="franchise@exemple-hector.fr")
    db.add(u)
    db.commit()
    db.refresh(u)
    db.add(Profile(user_id=u.id, statut="intermittent"))
    db.commit()
    return u


def test_enregistrement_et_date_de_saisie(db):
    u = _user(db)
    out = api.enregistrer_franchises(api.FranchisesRequest(franchise_cp_jours=9, franchise_salaires_jours=16), u, db)
    assert out["franchise_cp_jours"] == 9 and out["franchise_salaires_jours"] == 16
    assert out["franchise_maj_le"] == date.today().isoformat()


def test_zero_ou_negatif_efface_la_franchise(db):
    u = _user(db)
    api.enregistrer_franchises(api.FranchisesRequest(franchise_cp_jours=9), u, db)
    out = api.enregistrer_franchises(api.FranchisesRequest(franchise_cp_jours=0, franchise_salaires_jours=-3), u, db)
    assert out["franchise_cp_jours"] is None
    assert out["franchise_salaires_jours"] is None
    assert out["franchise_maj_le"] is None          # plus rien à dater


def test_valeur_delirante_bornee(db):
    u = _user(db)
    out = api.enregistrer_franchises(api.FranchisesRequest(franchise_cp_jours=99999), u, db)
    assert out["franchise_cp_jours"] == 365.0

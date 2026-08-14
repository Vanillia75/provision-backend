# -*- coding: utf-8 -*-
# ════════════════════════════════════════════════════════════════════════
#  LE RELEVÉ DE SITUATION : lecture, verdict, conservation.
#
#  Décision de Camille du 14/08/2026 : un relevé scanné doit permettre de
#  VÉRIFIER le versement France Travail (« payé juste » / « il manque X € »).
#
#  Deux étages testés ici, sans aucun appel réseau :
#    · la NORMALISATION de ce que le lecteur renvoie (releve_extractor), avec
#      les pièges du vrai relevé de Delphine : périodes répétées par les
#      « Situation au » successives, règlements cités deux fois, virgules ;
#    · le VERDICT de l'API (verifier), monté sur une base SQLite : conforme,
#      écart expliqué, compte gratuit verrouillé, mise à jour d'un même mois.
# ════════════════════════════════════════════════════════════════════════
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import api
from auth import hash_password
from database import Base, get_db
from models import IntermittentActivity, Profile, Subscription, User
from releve_extractor import _normaliser_releve

# ── La normalisation ─────────────────────────────────────────────────────

BRUT_DELPHINE = {
    "type_document": "releve_situation",
    "periodes": [
        # Situation au 01/07 : mai, première version.
        {"debut": "01/05/2026", "fin": "31/05/2026", "nature": "regularisation",
         "aj_dues": 7, "net_du": 427.00, "jours_travail": 24},
        # Situation au 07/07 : mai, version mise à jour (celle qui doit gagner).
        {"debut": "01/05/2026", "fin": "31/05/2026", "nature": "regularisation",
         "aj_dues": 15, "net_du": 915.00, "aj_deja_versees": 7, "net_deja_verse": 427.00,
         "jours_travail": 24, "jours_franchise_cp": 2},
        {"debut": "01/06/2026", "fin": "30/06/2026", "nature": "paiement_provisoire_regularise",
         "aj_dues": 22, "net_du": 1361.36, "aj_deja_versees": 20, "net_deja_verse": 1237.60,
         "jours_travail": 6, "jours_franchise_cp": 4},
    ],
    "reglements": [
        {"date": "03/07/2026", "montant": 1237.60},
        {"date": "09/07/2026", "montant": 611.76},
        {"date": "03/07/2026", "montant": 1237.60},   # répété par la 2e « Situation au »
    ],
}


def test_la_derniere_version_d_une_periode_gagne():
    r = _normaliser_releve(BRUT_DELPHINE, "releve.pdf")
    mai = [p for p in r["periodes"] if p["debut"] == "2026-05-01"]
    assert len(mai) == 1, "mai apparaissait deux fois, seule la version la plus récente doit rester"
    assert mai[0]["aj_dues"] == 15 and mai[0]["net_du"] == 915.00


def test_les_reglements_repetes_ne_comptent_qu_une_fois():
    r = _normaliser_releve(BRUT_DELPHINE, "releve.pdf")
    assert [(x["date"], x["montant"]) for x in r["reglements"]] == [
        ("2026-07-03", 1237.60), ("2026-07-09", 611.76)]


def test_le_taux_net_par_jour_se_deduit_du_document():
    """915 € pour 15 jours = 61 €/jour : LE chiffre qui a permis de percer le plancher CSG."""
    r = _normaliser_releve(BRUT_DELPHINE, "releve.pdf")
    mai = next(p for p in r["periodes"] if p["debut"] == "2026-05-01")
    assert mai["taux_net_jour"] == 61.00


def test_les_dates_francaises_et_les_virgules_passent():
    r = _normaliser_releve({
        "periodes": [{"debut": "01/06/2026", "fin": "30/06/2026", "aj_dues": "22",
                      "net_du": "1361,36"}],
        "reglements": [{"date": "2026-07-03", "montant": "1237,60"}],
    }, "x.pdf")
    assert r["periodes"][0]["net_du"] == 1361.36
    assert r["reglements"][0]["montant"] == 1237.60


def test_les_valeurs_folles_sont_ecartees_sans_planter():
    r = _normaliser_releve({
        "periodes": [{"debut": "01/06/2026", "fin": "30/06/2026",
                      "aj_dues": 99, "net_du": 999999, "jours_travail": -3},
                     {"debut": None, "fin": None},          # entrée vide : ignorée
                     "pas un dict"],
        "reglements": [{"date": "n'importe quoi", "montant": 100},
                       {"date": "2026-07-03", "montant": None}],
    }, "x.pdf")
    p = r["periodes"][0]
    assert p["aj_dues"] is None          # 99 jours dans un mois : impossible
    assert p["net_du"] is None           # montant délirant
    assert p["jours_travail"] is None    # négatif
    assert r["reglements"] == []         # date illisible ou montant absent


# ── Le verdict, par l'API ────────────────────────────────────────────────

@pytest.fixture()
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    def _db():
        s = TestSession()
        try:
            yield s
        finally:
            s.close()

    api.app.dependency_overrides[get_db] = _db
    yield TestClient(api.app), TestSession
    api.app.dependency_overrides.clear()


def _compte(TestSession, premium=True, montant_journalier=61.00):
    s = TestSession()
    u = User(email="releve@exemple-totor.fr", password_hash=hash_password("Secret-solide-1"),
             email_verified=True)
    s.add(u); s.commit()
    s.add(Profile(user_id=u.id, statut="intermittent", onboarding_complete=True,
                  montant_journalier=montant_journalier, annexe_allocation="annexe10"))
    if premium:
        s.add(Subscription(user_id=u.id, plan="premium", status="comp", source="comp"))
    s.commit()
    uid = u.id
    s.close()
    return uid


def _jeton(client):
    r = client.post("/auth/login", json={"email": "releve@exemple-totor.fr",
                                         "password": "Secret-solide-1"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_versement_conforme_donne_ok(client):
    """Mois sans travail : 31 jours × 61 € = 1 891 €. On déclare pile ça → ✓."""
    c, TS = client
    _compte(TS)
    h = _jeton(c)
    r = c.post("/intermittent/releve/verifier", headers=h, json={
        "annee": 2026, "mois": 7, "aj_nombre": 31, "net_verse": 1891.00})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["verdict"] == "ok"
    assert d["attendu_net"] == 1891.00


def test_jours_manquants_donnent_un_ecart_explique(client):
    """France Travail paie 22 jours, TOTOR (sans activité saisie) en attend 31 :
    l'écart doit être chiffré ET expliqué par les jours, avec le croisement AEM."""
    c, TS = client
    _compte(TS)
    h = _jeton(c)
    r = c.post("/intermittent/releve/verifier", headers=h, json={
        "annee": 2026, "mois": 7, "aj_nombre": 22, "net_verse": 1342.00,
        "jours_travail": 9})
    d = r.json()
    assert d["verdict"] == "ecart"
    assert d["ecart"] == pytest.approx(1342.00 - 1891.00, abs=0.01)
    assert "jour" in d["explication"]
    assert "manque peut-être un contrat" in d["explication"]


def test_le_travail_saisi_est_pris_en_compte(client):
    """6 cachets en juillet (comme Lucile) : 9 jours décalés → 22 jours payés.
    C'est LE cas du backtest gagné au centime le 04/08."""
    c, TS = client
    uid = _compte(TS, montant_journalier=45.29)
    s = TS()
    s.add(IntermittentActivity(user_id=uid, date=date(2026, 7, 15),
                               type_activite="cachet_isole", nombre=6, salaire_brut=1200))
    s.commit(); s.close()
    h = _jeton(c)
    r = c.post("/intermittent/releve/verifier", headers=h, json={
        "annee": 2026, "mois": 7, "aj_nombre": 22, "net_verse": 996.38})
    d = r.json()
    assert d["verdict"] == "ok", d["explication"]
    assert d["attendu_jours"] == 22.0


def test_compte_gratuit_conserve_mais_verrouille(client):
    c, TS = client
    _compte(TS, premium=False)
    h = _jeton(c)
    r = c.post("/intermittent/releve/verifier", headers=h, json={
        "annee": 2026, "mois": 7, "net_verse": 1000.00})
    d = r.json()
    assert d["verdict"] == "verrou"
    assert "TOTOR Veille" in d["explication"]
    # Les chiffres sont bien conservés malgré le verrou.
    liste = c.get("/intermittent/releves", headers=h).json()["releves"]
    assert len(liste) == 1 and liste[0]["net_verse"] == 1000.00


def test_le_meme_mois_se_met_a_jour_sans_doublon(client):
    c, TS = client
    _compte(TS)
    h = _jeton(c)
    c.post("/intermittent/releve/verifier", headers=h,
           json={"annee": 2026, "mois": 7, "aj_nombre": 31, "net_verse": 1891.00})
    c.post("/intermittent/releve/verifier", headers=h,
           json={"annee": 2026, "mois": 7, "aj_nombre": 30, "net_verse": 1830.00})
    liste = c.get("/intermittent/releves", headers=h).json()["releves"]
    assert len(liste) == 1
    assert liste[0]["aj_nombre"] == 30


def test_suppression(client):
    c, TS = client
    _compte(TS)
    h = _jeton(c)
    d = c.post("/intermittent/releve/verifier", headers=h,
               json={"annee": 2026, "mois": 6, "net_verse": 500.00}).json()
    assert c.delete(f"/intermittent/releves/{d['id']}", headers=h).json()["ok"] is True
    assert c.get("/intermittent/releves", headers=h).json()["releves"] == []


def test_entrees_absurdes_refusees_proprement(client):
    c, TS = client
    _compte(TS)
    h = _jeton(c)
    for corps in [{"annee": 2026, "mois": 13, "net_verse": 100},
                  {"annee": "abc", "mois": 7, "net_verse": 100},
                  {"annee": 2026, "mois": 7},                      # net manquant
                  {"annee": 2026, "mois": 7, "net_verse": -5},
                  {"annee": 2026, "mois": 7, "net_verse": "abc"}]:
        r = c.post("/intermittent/releve/verifier", headers=h, json=corps)
        assert r.status_code == 400, corps

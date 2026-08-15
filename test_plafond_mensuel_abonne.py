# -*- coding: utf-8 -*-
# ════════════════════════════════════════════════════════════════════════
#  LE PLAFOND MENSUEL DE L'ABONNÉ (15/08/2026).
#
#  Un abonné n'avait qu'un garde-fou JOURNALIER (40 scans). À ce rythme, il
#  pouvait théoriquement faire 1 200 scans dans le mois, soit environ 13 € de
#  lecture pour un abonnement Pionnier à 3,19 € nets mensuels. C'était le seul
#  scénario où un utilisateur coûte plus cher qu'il ne rapporte.
#
#  ⚠️ La tentation était de baisser le plafond JOURNALIER. C'aurait été une
#  faute : Stéphanie a scanné 21 attestations d'affilée le soir de son
#  inscription pour rattraper son historique, et s'est abonnée une heure et
#  demie plus tard. Un plafond journalier bas l'aurait bloquée pendant le seul
#  moment qui compte vraiment, la découverte.
#
#  On plafonne donc le MOIS, très haut, et on laisse la journée tranquille.
# ════════════════════════════════════════════════════════════════════════
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import api
from api import AI_AEM_MONTHLY_LIMIT
from auth import hash_password
from database import Base, get_db
from models import AIUsage, Profile, Subscription, User


@pytest.fixture()
def contexte():
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
    yield TestSession
    api.app.dependency_overrides.clear()


def _abonne(TestSession, scans_ce_mois=0.0):
    from datetime import date
    s = TestSession()
    u = User(email="abonne@exemple-totor.fr", password_hash=hash_password("Secret-solide-1"),
             email_verified=True)
    s.add(u); s.commit()
    s.add(Profile(user_id=u.id, statut="intermittent", onboarding_complete=True))
    s.add(Subscription(user_id=u.id, plan="premium", status="comp", source="comp"))
    # On ETALE sur des jours differents : sinon on bute sur le garde-fou
    # JOURNALIER, qui n'est pas ce que ces tests verifient.
    from datetime import timedelta
    reste, jour = float(scans_ce_mois), date.today()
    while reste > 0:
        part = min(reste, 10.0)
        s.add(AIUsage(user_id=u.id, jour=jour, type_appel="aem_scan", count=part))
        reste -= part
        jour -= timedelta(days=1)
    s.commit()
    uid = u.id
    s.close()
    return uid


def _consommer(TestSession, uid):
    """Appelle la porte de quota comme le fait une vraie route de scan."""
    from fastapi import HTTPException
    s = TestSession()
    try:
        u = s.query(User).filter(User.id == uid).first()
        api._consommer_quota(s, u, "aem_scan", api.AI_AEM_DAILY_LIMIT)
        return None
    except HTTPException as e:
        return e
    finally:
        s.close()


def test_un_abonne_ordinaire_scanne_sans_etre_gene(contexte):
    uid = _abonne(contexte, scans_ce_mois=38)   # le plus gros usage réel mesuré
    assert _consommer(contexte, uid) is None


def test_l_arrivee_dans_l_app_n_est_pas_bloquee(contexte):
    """Le geste de Stéphanie : 21 scans d'affilée le premier soir."""
    uid = _abonne(contexte, scans_ce_mois=21)
    assert _consommer(contexte, uid) is None


def test_au_dela_du_plafond_mensuel_on_s_arrete(contexte):
    uid = _abonne(contexte, scans_ce_mois=AI_AEM_MONTHLY_LIMIT)
    e = _consommer(contexte, uid)
    assert e is not None and e.status_code == 429


def test_le_message_propose_une_porte_de_sortie(contexte):
    """On ne claque pas la porte : quelqu'un avec un vrai besoin doit pouvoir
    en parler à un humain."""
    uid = _abonne(contexte, scans_ce_mois=AI_AEM_MONTHLY_LIMIT + 10)
    e = _consommer(contexte, uid)
    assert "bonjour@montotor.fr" in str(e.detail)
    assert "pause" in str(e.detail).lower()


def test_le_plafond_est_tres_au_dessus_de_l_usage_reel():
    """Garde-fou de conception : si quelqu'un baisse un jour cette valeur trop
    près de l'usage réel, ce test le dira."""
    assert AI_AEM_MONTHLY_LIMIT >= 100, (
        "le plus gros utilisateur réel fait 38 scans en 42 jours, et une reprise "
        "d'historique en fait ~21 d'un coup : descendre sous 100 gênerait de "
        "vraies personnes pour économiser quelques centimes"
    )

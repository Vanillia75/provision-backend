# ════════════════════════════════════════════════════════════════════════
#  Tests de l'assistant vocal TOTOR (Phase 1). Réseau mocké (guides + Claude).
#  Loi X : chercher_guide ne répond QUE depuis les guides, sinon il escalade.
# ════════════════════════════════════════════════════════════════════════
import pytest

import voice_agent as va


class FakeResp:
    def __init__(self, payload): self._p = payload
    def json(self): return self._p
    def raise_for_status(self): pass


FAUX_GUIDES = {
    "507-heures-intermittent": "Les 507 heures d'intermittent : le seuil de 507 heures "
                               "sur la periode de reference ouvre tes droits a l'allocation.",
    "acre-auto-entrepreneur": "L'ACRE est une reduction de cotisations la premiere annee.",
}


def test_chercher_guide_repond_depuis_les_guides(monkeypatch):
    monkeypatch.setattr(va, "ANTHROPIC_API_KEY", "x")
    monkeypatch.setattr(va, "_charger_guides", lambda: FAUX_GUIDES)
    monkeypatch.setattr(va.requests, "post",
                        lambda url, **k: FakeResp({"content": [{"text": "Les 507 heures, c'est le seuil pour ouvrir tes droits."}]}))
    out = va.chercher_guide("c'est quoi les 507 heures ?")
    assert "507" in out


def test_chercher_guide_escalade_si_hors_guides(monkeypatch):
    monkeypatch.setattr(va, "ANTHROPIC_API_KEY", "x")
    monkeypatch.setattr(va, "_charger_guides", lambda: FAUX_GUIDES)
    monkeypatch.setattr(va.requests, "post",
                        lambda url, **k: FakeResp({"content": [{"text": "ESCALADE"}]}))
    out = va.chercher_guide("est-ce que je peux deduire mon loyer ?")
    assert "rappeler" in out.lower()


def test_chercher_guide_sans_cle_ia_escalade(monkeypatch):
    monkeypatch.setattr(va, "ANTHROPIC_API_KEY", "")
    monkeypatch.setattr(va, "_charger_guides", lambda: FAUX_GUIDES)
    out = va.chercher_guide("507 heures")
    assert "rappeler" in out.lower()


def test_escalader_humain_envoie_email_et_confirme(monkeypatch):
    envoye = {}
    monkeypatch.setattr(va, "send_email", lambda *a, **k: envoye.update({"ok": True}) or True)
    out = va.escalader_humain(prenom="Alex", telephone="0600000000", question="cas complique")
    assert envoye.get("ok") is True
    assert "rappeler" in out.lower()


def test_escalader_humain_sans_telephone_demande_le_numero(monkeypatch):
    monkeypatch.setattr(va, "send_email", lambda *a, **k: True)
    out = va.escalader_humain(prenom="Alex", telephone=None)
    assert "numero" in out.lower() or "numéro" in out.lower()


def test_programmer_rappel_confirme_le_creneau(monkeypatch):
    monkeypatch.setattr(va, "send_email", lambda *a, **k: True)
    out = va.programmer_rappel("Alex", "0600000000", "demain 14h")
    assert "14h" in out


def test_retrieval_choisit_le_bon_guide():
    top = va._guides_pertinents("comment marche l'acre en auto-entrepreneur", FAUX_GUIDES)
    assert top and top[0][1] == "acre-auto-entrepreneur"

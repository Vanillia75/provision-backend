"""Tests de la projection AJ au prochain renouvellement (allocation_engine.projeter_renouvellement).

Demande testeuse du 23/07/2026. Garde-fous Loi X : pas de chiffre si bruts trop
incomplets, MÊME discipline d'affichage que la carte allocation (branche_affichable :
annexe 8 et > 60 EUR/jour -> aucun chiffre), courbe au cachet moyen REEL, arrêtée à 60 EUR.
"""

from datetime import date, timedelta

from allocation_engine import projeter_renouvellement, calculer_aj, branche_affichable

FIN = date(2026, 7, 24)


def _act(jours_avant, type_activite="cachet_isole", nombre=1, brut=100.0, metier="artiste"):
    return {
        "date": FIN - timedelta(days=jours_avant),
        "type_activite": type_activite,
        "nombre": nombre,
        "salaire_brut": brut,
        "metier": metier,
    }


def test_refuse_si_bruts_trop_incomplets():
    # 60 % des heures sans brut -> on REFUSE de projeter (Loi X : pas de chiffre bancal).
    acts = [
        _act(10, nombre=2, brut=None),
        _act(20, nombre=3, brut=300.0),
    ]
    r = projeter_renouvellement(acts, FIN)
    assert r["ok"] is False
    assert r["raison"] == "bruts_incomplets"
    assert r["completude"] == 60


def test_fenetre_365_jours():
    # Une activite plus vieille que la fenetre ne compte pas.
    acts = [
        _act(400, nombre=10, brut=5000.0),   # hors fenetre
        _act(30, nombre=4, brut=480.0),
    ]
    r = projeter_renouvellement(acts, FIN)
    assert r["ok"] is True
    assert r["nht"] == 48.0            # 4 cachets seulement
    assert r["sr"] == 480.0


def test_annexe8_affichable_depuis_l_ouverture_du_27_07():
    # Le cas du guide (800 h / 18 000 EUR annexe 8). L'annexe 8 etait muette faute
    # de cas reel ; elle a ete validee le 27/07/2026 contre le simulateur OFFICIEL
    # de France Travail (14 cas). La projection doit donc sortir des chiffres.
    acts = [_act(100, type_activite="heures", nombre=800, brut=18000.0, metier="technicien")]
    r = projeter_renouvellement(acts, FIN)
    assert r["ok"] is True
    assert r["annexe"] == "annexe8"
    assert r["affichable"] is True
    assert r["raison_non_affichable"] is None
    assert r["aj_brute"] == calculer_aj("annexe8", sr=18000.0, nht=800.0)["aj_brute"]
    assert len(r["points"]) > 0


def test_artiste_affichable_et_coherent_moteur():
    # Artiste sous 60 EUR/jour : la branche validee. La projection doit tomber
    # exactement sur calculer_aj (meme moteur, meme resultat).
    acts = [_act(i * 7, nombre=4, brut=520.0) for i in range(1, 11)]  # 40 cachets a 130 EUR
    r = projeter_renouvellement(acts, FIN)
    assert r["ok"] is True and r["affichable"] is True
    assert r["annexe"] == "annexe10"
    assert r["brut_moyen_cachet"] == 130.0
    attendu = calculer_aj("annexe10", sr=40 * 130.0, nht=480.0)
    assert branche_affichable("annexe10", attendu)[0] is True  # le cas de test reste dans la branche
    assert r["aj_brute"] == attendu["aj_brute"]
    assert r["aj_nette"] == attendu["aj_nette"]


def test_courbe_croissante_et_plafonnee_a_60():
    acts = [_act(i * 7, nombre=4, brut=520.0) for i in range(1, 11)]
    r = projeter_renouvellement(acts, FIN)
    ajs = [p["aj_brute"] for p in r["points"]]
    assert len(ajs) >= 1
    assert ajs[0] == r["aj_brute"]
    # Continue et croissante : chaque cachet compte (le mythe des paliers est mort).
    assert all(b >= a for a, b in zip(ajs, ajs[1:]))
    # Chaque point affiche reste dans la branche validee (<= 60, pas de CSG).
    for p in r["points"]:
        assert p["aj_brute"] <= 60.0
    # Et si la courbe s'est arretee avant 9 points, c'est qu'elle a touche la limite.
    if len(ajs) < 9:
        assert r["courbe_plafonnee_60"] is True


def test_annexe_indeterminee_prudente():
    # Que des heures sans metier -> annexe indeterminee : on retient la plus BASSE
    # des deux annexes. Si c'est l'annexe 8, la Loi X la rend non affichable.
    acts = [_act(15, type_activite="heures", nombre=600, brut=12000.0, metier=None)]
    r = projeter_renouvellement(acts, FIN)
    assert r["ok"] is True
    assert r["annexe_indeterminee"] is True
    a8 = calculer_aj("annexe8", sr=12000.0, nht=600.0)["aj_brute"]
    a10 = calculer_aj("annexe10", sr=12000.0, nht=600.0)["aj_brute"]
    annexe_basse = "annexe8" if a8 <= a10 else "annexe10"
    assert r["annexe"] == annexe_basse
    if r["affichable"]:
        assert r["aj_brute"] == min(a8, a10)
    else:
        assert "aj_brute" not in r


def test_simulation_n_cachets_a_x_euros():
    # « Et si j'ajoute 5 cachets a 200 EUR ? » : la simulation doit tomber
    # exactement sur calculer_aj avec le lot ajoute, et rester dans la Loi X.
    acts = [_act(i * 7, nombre=4, brut=520.0) for i in range(1, 11)]  # 480 h / 5 200 EUR
    r = projeter_renouvellement(acts, FIN, cachets_sup=5, brut_cachet=200.0)
    assert "simulation" in r
    s = r["simulation"]
    assert s["cachets"] == 5 and s["brut_cachet"] == 200.0
    attendu = calculer_aj("annexe10", sr=5200.0 + 5 * 200.0, nht=480.0 + 60.0)
    assert s["affichable"] is True
    assert s["aj_brute"] == attendu["aj_brute"]


def test_simulation_enorme_reste_juste_et_plafonnee():
    # Une simulation enorme (200 cachets a 5 000 EUR) sort maintenant un chiffre,
    # mais ce chiffre doit etre le PLAFOND officiel : 155,77 EUR, pas une
    # extrapolation. C'est la correction du 27/07 qui rend ce cas honnete.
    acts = [_act(i * 7, nombre=4, brut=520.0) for i in range(1, 11)]
    r = projeter_renouvellement(acts, FIN, cachets_sup=200, brut_cachet=5000.0)
    s = r["simulation"]
    assert s["affichable"] is True
    assert s["aj_brute"] == 155.77
    assert s["plafond_applique"] is True


def test_formation_exclue_du_montant():
    # La formation compte pour les 507h mais PAS pour l'AJ : elle ne doit pas
    # entrer dans la projection (ni en heures ni en brut).
    acts = [
        _act(10, type_activite="heures", nombre=500, brut=10000.0, metier="technicien"),
        {"date": FIN - timedelta(days=20), "type_activite": "formation", "nombre": 100, "salaire_brut": None, "metier": None},
    ]
    r = projeter_renouvellement(acts, FIN)
    assert r["ok"] is True
    assert r["nht"] == 500.0

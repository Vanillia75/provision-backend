# -*- coding: utf-8 -*-
"""Tests des fonctions pures du module Enable Banking (choix du solde et du compte).

Constat réel (test du 09/07/2026 sur comptes BRED) : un compte peut renvoyer
plusieurs soldes (types et dates différents). On doit choisir le comptable
(CLBD) le plus récent."""
from bank_regles import choisir_solde, choisir_compte


def _solde(montant, type_, date_):
    return {"balance_amount": {"amount": str(montant), "currency": "EUR"},
            "balance_type": type_, "reference_date": date_}


def test_solde_prefere_clbd_le_plus_recent():
    balances = [
        _solde("100.00", "XPCD", "2026-07-08"),   # prévisionnel, récent
        _solde("22078.96", "CLBD", "2026-07-08"),  # comptable, récent → gagnant
        _solde("-1456.68", "CLBD", "2026-07-07"),  # comptable, plus vieux
    ]
    assert choisir_solde(balances) == 22078.96


def test_solde_replis_sur_date_si_pas_de_clbd():
    balances = [
        _solde("50.00", "XPCD", "2026-07-01"),
        _solde("75.00", "XPCD", "2026-07-08"),
    ]
    assert choisir_solde(balances) == 75.00


def test_solde_liste_vide():
    assert choisir_solde([]) is None


def test_solde_montant_illisible():
    assert choisir_solde([{"balance_amount": {"amount": None}, "balance_type": "CLBD"}]) is None


def test_compte_prefere_compte_courant():
    comptes = [
        {"uid": "a", "cash_account_type": "SVGS"},   # épargne
        {"uid": "b", "cash_account_type": "CACC"},   # courant → gagnant
    ]
    assert choisir_compte(comptes)["uid"] == "b"


def test_compte_premier_par_defaut():
    comptes = [{"uid": "x"}, {"uid": "y"}]
    assert choisir_compte(comptes)["uid"] == "x"


def test_compte_liste_vide():
    assert choisir_compte([]) is None

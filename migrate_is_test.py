# -*- coding: utf-8 -*-
"""Migration one-time : colonne `is_test` + backfill des comptes de test existants.

À lancer UNE FOIS sur la prod (Railway) AVANT de pousser le nouveau code
(règle du projet : ALTER TABLE manuel avant le push). Idempotent
(ADD COLUMN IF NOT EXISTS ; le backfill peut se relancer sans dommage).

Le backfill reproduit EXACTEMENT l'ancien filtre par motif d'email, pour que les
chiffres ne bougent pas (on doit rester à 22 inscrits, 0 payant, 100 places).

Usage (avec l'URL publique de la base) :
    DATABASE_URL="<url publique>" python migrate_is_test.py
"""
from sqlalchemy import text

from database import engine

# Mêmes motifs que l'ancien _compter_stats (défaut ADMIN_STATS_EXCLUDE_PATTERNS).
PATTERNS = ["gard", "vanillia", "leetoh", "pomez", "@example.com", "exemple-hector"]

with engine.begin() as cx:
    cx.execute(text(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_test BOOLEAN NOT NULL DEFAULT false"
    ))
    where = " OR ".join(f"lower(email) LIKE :p{i}" for i in range(len(PATTERNS)))
    params = {f"p{i}": f"%{p}%" for i, p in enumerate(PATTERNS)}
    res = cx.execute(text(f"UPDATE users SET is_test = true WHERE {where}"), params)
    print(f"[migration] comptes marqués is_test = true : {res.rowcount}")

with engine.connect() as cx:
    total = cx.execute(text("SELECT count(*) FROM users")).scalar()
    tests = cx.execute(text("SELECT count(*) FROM users WHERE is_test")).scalar()
    print(f"[migration] total comptes={total}  is_test={tests}  inscrits réels={total - tests}")

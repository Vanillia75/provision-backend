# -*- coding: utf-8 -*-
"""Sauvegarde R2 fraîche et DISTINCTE, à lancer juste avant une migration de schéma.

Contrairement à la sauvegarde quotidienne (dédupliquée par date, donc sautée si
celle du jour existe déjà), celle-ci crée une archive au nom horodaté explicite
`backups/db/pre-migration-is_test-<UTC>.zip`, pour avoir un point de restauration
tout frais juste avant l'ALTER. Réutilise la même logique chiffrée que sauvegarde.py.

Usage (URL publique + clés R2/Fernet dans l'env) :
    python backup_avant_migration.py
"""
from datetime import datetime

# Machine locale de Camille : l'antivirus fait du MITM SSL. On fait confiance au
# magasin de certificats du système (inoffensif ailleurs, ex. Railway).
try:
    import truststore
    truststore.inject_into_ssl()
except Exception:
    pass

import models  # noqa: F401 — enregistre toutes les tables sur Base.metadata (sinon export vide)
import r2_storage
import sauvegarde

if not r2_storage.R2_ENABLED:
    raise SystemExit("R2 non configuré (R2_ACCESS_KEY/SECRET/ENDPOINT/BUCKET manquants).")
if not sauvegarde.SAUVEGARDE_CLE:
    raise SystemExit("SAUVEGARDE_CLE absente : on refuse d'écrire des données en clair.")

donnees, manifeste = sauvegarde.creer_archive()
donnees = sauvegarde._chiffrer(donnees)
cle = f"backups/db/pre-migration-is_test-{datetime.utcnow().strftime('%Y-%m-%dT%H%M%SZ')}.zip"
r2_storage._get_client().put_object(
    Bucket=r2_storage.R2_BUCKET, Key=cle, Body=donnees,
    ContentType="application/octet-stream",
)
total = sum(manifeste["tables"].values())
print(f"[backup-pre-migration] OK -> {cle}")
print(f"[backup-pre-migration] {len(donnees) // 1024} Ko chiffrés, {total} lignes, {len(manifeste['tables'])} tables")

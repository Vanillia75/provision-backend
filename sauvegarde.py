# -*- coding: utf-8 -*-
"""Sauvegarde quotidienne de la base PostgreSQL vers Cloudflare R2.

« Personne ne peut perdre son compte ou ses données, jamais. » (Camille, 09/07/2026)

Ceinture ET bretelles : indépendante des éventuelles sauvegardes de Railway.
- Chaque jour (à la première passe du scheduler après minuit UTC), TOUTES les tables
  sont exportées en CSV exact (COPY PostgreSQL, types sérialisés par Postgres lui-même),
  zippées avec un manifeste (date + nombre de lignes par table), et envoyées sur R2
  sous `backups/db/AAAA-MM-DD.zip`.
- Rétention : 30 jours (les archives plus vieilles sont purgées à chaque passe).
- Les archives contiennent des données sensibles → bucket R2 privé (jamais d'URL
  publique), chiffrement au repos R2, accès seulement par les clés du serveur.
- Restauration : voir SAUVEGARDES.md + restaurer_sauvegarde.py (à lancer en local).
"""

import csv
import io
import json
import os
import zipfile
from datetime import date, datetime, timedelta

from database import engine, Base
import r2_storage

PREFIX = "backups/db/"
RETENTION_JOURS = 30


def creer_archive() -> tuple:
    """Exporte toutes les tables en CSV (COPY) dans un zip en mémoire.
    Renvoie (bytes_du_zip, manifeste_dict)."""
    manifeste = {"date": datetime.utcnow().isoformat() + "Z", "tables": {}}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        raw = engine.raw_connection()
        try:
            cur = raw.cursor()
            for table in Base.metadata.sorted_tables:
                nom = table.name
                sortie = io.BytesIO()
                # COPY : l'export de référence de Postgres (types exacts, échappement exact).
                with cur.copy(f'COPY "{nom}" TO STDOUT (FORMAT csv, HEADER true)') as copie:
                    for morceau in copie:
                        sortie.write(bytes(morceau))
                contenu = sortie.getvalue()
                nb_lignes = max(0, contenu.count(b"\n") - 1)
                manifeste["tables"][nom] = nb_lignes
                zf.writestr(f"{nom}.csv", contenu)
            zf.writestr("MANIFESTE.json", json.dumps(manifeste, ensure_ascii=False, indent=1))
        finally:
            raw.close()
    return buf.getvalue(), manifeste


def _cle_du_jour() -> str:
    return f"{PREFIX}{date.today().isoformat()}.zip"


def _existe(cle: str) -> bool:
    try:
        r2_storage._get_client().head_object(Bucket=r2_storage.R2_BUCKET, Key=cle)
        return True
    except Exception:
        return False


def _purger_anciennes():
    """Supprime les archives plus vieilles que RETENTION_JOURS."""
    client = r2_storage._get_client()
    limite = date.today() - timedelta(days=RETENTION_JOURS)
    try:
        page = client.list_objects_v2(Bucket=r2_storage.R2_BUCKET, Prefix=PREFIX)
        for obj in page.get("Contents", []):
            nom = obj["Key"][len(PREFIX):][:10]  # AAAA-MM-DD
            try:
                if date.fromisoformat(nom) < limite:
                    client.delete_object(Bucket=r2_storage.R2_BUCKET, Key=obj["Key"])
                    print(f"[sauvegarde] purge de {obj['Key']} (> {RETENTION_JOURS} j)", flush=True)
            except ValueError:
                continue  # nom inattendu : on ne touche pas
    except Exception as e:
        print(f"[sauvegarde] purge impossible (non bloquant) : {e}", flush=True)


def executer_sauvegarde_quotidienne():
    """Une sauvegarde par jour, dédupliquée par le nom de l'archive. Jamais bloquant."""
    if not r2_storage.R2_ENABLED:
        print("[sauvegarde] R2 non configuré — AUCUNE sauvegarde ne part (à corriger !)", flush=True)
        return
    cle = _cle_du_jour()
    try:
        if _existe(cle):
            return  # déjà sauvegardé aujourd'hui
        donnees, manifeste = creer_archive()
        r2_storage._get_client().put_object(
            Bucket=r2_storage.R2_BUCKET, Key=cle, Body=donnees,
            ContentType="application/zip",
        )
        total = sum(manifeste["tables"].values())
        print(f"[sauvegarde] {cle} envoyée ({len(donnees) // 1024} Ko, {total} lignes, "
              f"{len(manifeste['tables'])} tables)", flush=True)
        _purger_anciennes()
    except Exception as e:
        # Jamais bloquant pour l'app, mais TRÈS visible dans les logs (et Sentry via print ? non).
        print(f"[sauvegarde] ÉCHEC de la sauvegarde du jour : {e}", flush=True)

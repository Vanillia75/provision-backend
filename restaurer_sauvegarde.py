# -*- coding: utf-8 -*-
"""Restauration d'une sauvegarde R2 vers une base PostgreSQL. À LANCER EN LOCAL.

⚠️ OPÉRATION DESTRUCTRICE sur la base CIBLE : chaque table restaurée est VIDÉE
puis rechargée depuis l'archive. À n'utiliser que pour une vraie restauration
(base perdue/corrompue) ou un exercice sur une base de TEST.

Usage :
  1. Poser les variables d'environnement : R2_ACCESS_KEY, R2_SECRET_KEY,
     R2_ENDPOINT, R2_BUCKET (celles de Railway) et DATABASE_URL (la base CIBLE,
     l'URL PUBLIQUE si Railway).
  2. Garde-fou obligatoire : RESTAURATION_CONFIRMEE=oui
  3. python restaurer_sauvegarde.py 2026-07-09        (la date de l'archive)
     python restaurer_sauvegarde.py 2026-07-09 --verifier-seulement
        → télécharge et contrôle l'archive SANS RIEN ÉCRIRE (exercice mensuel conseillé).
"""

import io
import json
import os
import sys
import zipfile

import boto3
from botocore.config import Config


def main():
    if len(sys.argv) < 2:
        raise SystemExit("Usage : python restaurer_sauvegarde.py AAAA-MM-JJ [--verifier-seulement]")
    jour = sys.argv[1]
    verifier_seulement = "--verifier-seulement" in sys.argv

    client = boto3.client(
        "s3",
        endpoint_url=os.environ["R2_ENDPOINT"],
        aws_access_key_id=os.environ["R2_ACCESS_KEY"],
        aws_secret_access_key=os.environ["R2_SECRET_KEY"],
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )
    cle = f"backups/db/{jour}.zip"
    print(f"Téléchargement de {cle}…")
    corps = client.get_object(Bucket=os.environ["R2_BUCKET"], Key=cle)["Body"].read()
    zf = zipfile.ZipFile(io.BytesIO(corps))
    manifeste = json.loads(zf.read("MANIFESTE.json"))
    print(f"Archive du {manifeste['date']} — {len(manifeste['tables'])} tables :")
    for nom, lignes in manifeste["tables"].items():
        print(f"  {nom} : {lignes} lignes")

    # Contrôle d'intégrité : chaque CSV listé est présent et lisible.
    for nom in manifeste["tables"]:
        contenu = zf.read(f"{nom}.csv")
        reel = max(0, contenu.count(b"\n") - 1)
        etat = "OK" if reel == manifeste["tables"][nom] else f"⚠️ {reel} lignes lues"
        print(f"  contrôle {nom}.csv : {etat}")

    if verifier_seulement:
        print("\n✅ Vérification terminée. RIEN n'a été écrit.")
        return

    if os.environ.get("RESTAURATION_CONFIRMEE") != "oui":
        raise SystemExit("\n⛔ Restauration refusée : pose RESTAURATION_CONFIRMEE=oui pour confirmer "
                         "(chaque table de la base cible sera VIDÉE puis rechargée).")

    import psycopg
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        with conn.cursor() as cur:
            # Ordre du manifeste = ordre de création (parents d'abord).
            noms = list(manifeste["tables"].keys())
            # On vide dans l'ordre inverse (enfants d'abord), on recharge dans l'ordre.
            cur.execute("SET session_replication_role = replica")  # désactive les FK le temps du chargement
            for nom in reversed(noms):
                cur.execute(f'TRUNCATE TABLE "{nom}" CASCADE')
            for nom in noms:
                donnees = zf.read(f"{nom}.csv")
                with cur.copy(f'COPY "{nom}" FROM STDIN (FORMAT csv, HEADER true)') as copie:
                    copie.write(donnees)
                print(f"  restauré {nom}")
            cur.execute("SET session_replication_role = DEFAULT")
        conn.commit()
    print("\n✅ Restauration terminée.")


if __name__ == "__main__":
    main()

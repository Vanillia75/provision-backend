"""
Stockage des documents AEM sur Cloudflare R2 (compatible S3).

Données sensibles (une AEM contient le n° de sécu du salarié) :
- fichiers jamais publics ; accès uniquement via URL signée temporaire (1h)
- chiffrement au repos assuré par R2 (par défaut)
- suppression RGPD : delete_file() appelé à la suppression d'une activité ou d'un compte

Variables d'environnement attendues (configurées sur Railway) :
  R2_ACCESS_KEY, R2_SECRET_KEY, R2_ENDPOINT, R2_BUCKET
"""

import os
import uuid
import logging

logger = logging.getLogger("r2_storage")

R2_ACCESS_KEY = os.environ.get("R2_ACCESS_KEY")
R2_SECRET_KEY = os.environ.get("R2_SECRET_KEY")
R2_ENDPOINT = os.environ.get("R2_ENDPOINT")
R2_BUCKET = os.environ.get("R2_BUCKET")

# R2 est activé seulement si les 4 variables sont présentes.
# Sinon, le code reste fonctionnel sans stockage (l'app ne plante pas).
R2_ENABLED = all([R2_ACCESS_KEY, R2_SECRET_KEY, R2_ENDPOINT, R2_BUCKET])

_client = None


def _get_client():
    """Client S3 (boto3) configuré pour R2. Créé une seule fois (lazy)."""
    global _client
    if _client is not None:
        return _client
    if not R2_ENABLED:
        raise RuntimeError("R2 n'est pas configuré (variables d'environnement manquantes).")
    import boto3
    from botocore.config import Config
    _client = boto3.client(
        "s3",
        endpoint_url=R2_ENDPOINT,
        aws_access_key_id=R2_ACCESS_KEY,
        aws_secret_access_key=R2_SECRET_KEY,
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )
    return _client


def upload_aem(file_path: str, user_id: str, original_filename: str) -> str:
    """
    Envoie un fichier AEM vers R2. Renvoie la clé de stockage (à conserver en base).
    La clé est préfixée par l'user_id pour cloisonner les fichiers par utilisateur.
    """
    ext = os.path.splitext(original_filename or "")[1].lower() or ".bin"
    key = f"aem/{user_id}/{uuid.uuid4().hex}{ext}"
    content_type = {
        ".pdf": "application/pdf",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }.get(ext, "application/octet-stream")

    client = _get_client()
    with open(file_path, "rb") as f:
        client.put_object(
            Bucket=R2_BUCKET,
            Key=key,
            Body=f,
            ContentType=content_type,
        )
    logger.info("AEM uploadée sur R2 : %s", key)
    return key


def upload_devis_signe(pdf_bytes: bytes, user_id: str, quote_id: str) -> str:
    """
    PDF scellé d'un devis accepté en ligne (pièce du fichier de preuve de la
    signature électronique). Clé stable par devis : une seule copie, celle du
    moment exact de l'acceptation (son SHA-256 est stocké en base).
    """
    key = f"devis-signes/{user_id}/{quote_id}.pdf"
    client = _get_client()
    client.put_object(Bucket=R2_BUCKET, Key=key, Body=pdf_bytes, ContentType="application/pdf")
    logger.info("Devis signé scellé sur R2 : %s", key)
    return key


def get_signed_url(key: str, expires_seconds: int = 3600) -> str:
    """
    Génère une URL signée temporaire (1h par défaut) pour consulter un fichier.
    L'URL n'est valable que le temps imparti, le fichier n'est jamais public.
    """
    client = _get_client()
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": R2_BUCKET, "Key": key},
        ExpiresIn=expires_seconds,
    )


def delete_file(key: str) -> bool:
    """
    Supprime définitivement un fichier de R2 (RGPD).
    Ne lève pas d'erreur si le fichier n'existe plus (idempotent).
    """
    if not key:
        return False
    try:
        client = _get_client()
        client.delete_object(Bucket=R2_BUCKET, Key=key)
        logger.info("AEM supprimée de R2 : %s", key)
        return True
    except Exception as e:
        logger.warning("Échec suppression R2 pour %s : %s", key, e)
        return False


def delete_all_for_user(user_id: str) -> int:
    """
    Supprime tous les fichiers d'un utilisateur (RGPD — suppression de compte).
    Renvoie le nombre de fichiers supprimés.
    """
    if not R2_ENABLED:
        return 0
    try:
        client = _get_client()
        paginator = client.get_paginator("list_objects_v2")
        count = 0
        # TOUS les espaces de l'utilisateur : AEM originales (+ signalements,
        # même préfixe) et devis signés. Ajouter ici tout nouveau préfixe.
        for prefix in (f"aem/{user_id}/", f"devis-signes/{user_id}/"):
            for page in paginator.paginate(Bucket=R2_BUCKET, Prefix=prefix):
                objs = page.get("Contents", [])
                if not objs:
                    continue
                client.delete_objects(
                    Bucket=R2_BUCKET,
                    Delete={"Objects": [{"Key": o["Key"]} for o in objs]},
                )
                count += len(objs)
        logger.info("Suppression RGPD : %d fichiers retirés pour user %s", count, user_id)
        return count
    except Exception as e:
        logger.warning("Échec suppression RGPD R2 pour user %s : %s", user_id, e)
        return 0

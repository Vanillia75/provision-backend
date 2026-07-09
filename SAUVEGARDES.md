# Sauvegardes de la base — « personne ne perd ses données, jamais »

> Décision de Camille, 09/07/2026, à l'audit total. Deux ceintures indépendantes.

## Ceinture 1 — Railway (à vérifier dans le tableau de bord)
Service **Postgres** → onglet **Backups**. Si des sauvegardes quotidiennes y sont
listées, Railway couvre la restauration « bouton » de son côté.

## Ceinture 2 — la nôtre (indépendante, `sauvegarde.py`)
- **Quoi** : toutes les tables, export CSV exact (`COPY` PostgreSQL), zip + manifeste
  (date, nombre de lignes par table).
- **Quand** : une fois par jour, à la première passe du scheduler (toutes les 6 h),
  dédupliquée par le nom d'archive.
- **Où** : Cloudflare R2 (le bucket du coffre), clés `backups/db/AAAA-MM-JJ.zip`.
  Bucket privé, chiffré au repos, jamais d'URL publique.
- **Rétention** : 30 jours, purge automatique.
- **Surveillance** : ligne `[sauvegarde]` dans les logs Railway à chaque envoi ;
  en cas d'échec, ligne `[sauvegarde] ÉCHEC` très visible.

## Restaurer (ou s'entraîner)
Script `restaurer_sauvegarde.py`, à lancer EN LOCAL avec les variables R2 + DATABASE_URL
(publique) :

```
# Exercice sans risque (conseillé une fois par mois) : contrôle l'archive, n'écrit rien
python restaurer_sauvegarde.py 2026-07-09 --verifier-seulement

# Vraie restauration (VIDE puis recharge chaque table de la base cible)
RESTAURATION_CONFIRMEE=oui python restaurer_sauvegarde.py 2026-07-09
```

## Chiffrement (exigence audit 09/07/2026)
Chaque archive est chiffrée (Fernet/AES) AVANT d'être envoyée : suffixe `.zip.chiffre`.
Sans la variable `SAUVEGARDE_CLE`, AUCUNE archive ne part (refus d'écrire des données
personnelles en clair). La clé vit sur Railway ET hors Railway (gestionnaire de mots de
passe de Camille + `Desktop\Hector\enablebanking-keys\SAUVEGARDE_CLE.txt`) : sans elle,
les archives sont définitivement illisibles — ne JAMAIS la perdre ni la committer.

## Exercice de restauration COMPLET — réussi le 09/07/2026
Archive chiffrée du jour → base Postgres VIERGE (service Railway temporaire, supprimé après) :
déchiffrement ✓, schéma recréé (`--creer-tables`) ✓, 14 tables rechargées ✓, compte témoin
fonctionnel (hash bcrypt + profil joint) ✓, zéro donnée orpheline ✓, 90 users / 87 profils /
13 abonnements ✓. Deux pièges corrigés grâce à l'exercice : l'import des modèles pour le
schéma, et l'ordre des colonnes (COPY avec liste explicite depuis l'en-tête CSV).

Procédure de l'exercice (PowerShell, PC de Camille) :
1. `railway add -d postgres` → attendre le service temporaire, récupérer sa `DATABASE_PUBLIC_URL`.
2. Env : R2_*, SAUVEGARDE_CLE (variables du service provision-backend), DATABASE_URL=cible,
   `R2_VERIFY_SSL=non` (antivirus local), `RESTAURATION_CONFIRMEE=oui`.
3. `python restaurer_sauvegarde.py AAAA-MM-JJ --creer-tables` puis vérifier un compte témoin.
4. `railway service delete --service <temporaire> --yes` puis `railway service link provision-backend`.

## RGPD (documenté dans la politique de confidentialité le 09/07/2026)
Un compte supprimé disparaît immédiatement de la base active ; ses données peuvent subsister
jusqu'à 30 jours dans les sauvegardes chiffrées avant d'en disparaître définitivement
(rétention + purge automatique). Formulation publiée dans la page Confidentialité.

## Règles gravées
1. Une sauvegarde qui n'a jamais été vérifiée n'existe pas : exercice
   `--verifier-seulement` à chaque fin de mois.
2. Toute nouvelle table est couverte automatiquement (l'export suit `Base.metadata`).
3. Les archives contiennent des données sensibles (NIR dans les activités ? non — les
   documents restent dans le coffre R2 ; mais emails, montants, IBAN partiels) :
   même niveau de protection que le coffre, jamais de copie locale qui traîne.

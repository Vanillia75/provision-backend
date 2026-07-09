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

## Règles gravées
1. Une sauvegarde qui n'a jamais été vérifiée n'existe pas : exercice
   `--verifier-seulement` à chaque fin de mois.
2. Toute nouvelle table est couverte automatiquement (l'export suit `Base.metadata`).
3. Les archives contiennent des données sensibles (NIR dans les activités ? non — les
   documents restent dans le coffre R2 ; mais emails, montants, IBAN partiels) :
   même niveau de protection que le coffre, jamais de copie locale qui traîne.

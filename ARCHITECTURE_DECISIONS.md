# Décisions d'architecture — V2

> Ce document n'est **pas** l'architecture. Ce sont les décisions **déjà validées**, sorties de la cartographie de l'app. Elles permettent de ne pas repartir de zéro. L'architecture détaillée (`ARCHITECTURE.md`) viendra après, à tête reposée.

Ordre de lecture du chantier V2 :
`CONSTITUTION.md` → `LOIS.md` → `TEST-HECTOR.md` → **`ARCHITECTURE_DECISIONS.md`** → `ARCHITECTURE.md`

## 🎯 La vision qui commande toute la V2

> **On ne construit plus une application pour un statut. On construit un compagnon pour une personne.**

Conséquence directe de l'Article 1 de la Constitution. Aujourd'hui, l'app force un choix : « je suis auto-entrepreneur » **ou** « je suis intermittent » — deux applications séparées, deux cerveaux. Demain, le modèle mental devient :

- Pas « je choisis mon statut » → mais « **je gère ma vie professionnelle** ».
- Les statuts (auto-entrepreneur, intermittent, et demain SCI, SASU, freelance international…) ne sont plus des **modes** entre lesquels on bascule, mais des **capacités** qu'Hector active au bon moment selon la personne.
- Une même personne peut cumuler les statuts. Hector adapte naturellement ce qu'il montre, sans jamais donner l'impression d'ouvrir deux applications.

C'est la plus grosse évolution produit depuis le début. Tout le reste en découle.

## À fusionner (les « deux cerveaux » → un seul)
- **Cockpit** AE (`dashboard`) + Intermittent (`cockpit`)
- **Hector / assistant** AE (`assistant`) + Intermittent (`hector`)
- **Conseils / pédagogie** AE (`conseils`) + Intermittent (`conseils` / « Comprendre »)
- **Réglages / Profil** AE (`profil`) + Intermittent (`reglages`)

*Preuve que c'est faisable : Carnet, Abonnement et Jeu sont **déjà** unifiés.*

## À supprimer (écrans morts — inaccessibles, code mort)
- `score`
- `simvie`
- `actualites`
- `societe`

## À refondre (Loi II — un écran = une idée)
- **Flux AEM** → une seule histoire (aujourd'hui éclaté sur `coffre` + `attestation` + `calcul` : scanner → lister → analyser revenus → compter heures = 4 vues d'un même flux).
- **Encaisser / Frais** (`frais`) → scinder : saisir un revenu ≠ saisir une dépense (2 idées dans 1 écran).
- **Mes documents** (`attestation`) → 3 onglets (Revenus / Mes AEM / Actualisations) à repenser dans le flux AEM unifié.
- **Profil** (`profil`) → réglages fourre-tout (5+ sujets) à ranger en sous-sections.
- **Mode Salaire** (`salaire`) + **Mode Achat** (`achat`) → fusionner : même question (« puis-je sortir de l'argent ? »).

## À conserver (déjà sains / déjà unifiés)
- Carnet (`carnet`) — déjà unifié
- Abonnement (`abonnement`) — déjà unifié
- Course avec Hector (le jeu) — déjà unifié
- Mes revenus, Mes factures, Mes devis, Ma déclaration, Mes échéances, Simulateur, Mes tarifs, Contacts, Modèles

## L'écran-étalon
Le **Cockpit (Salon)** est l'écran de référence : une fois qu'il sonne juste, il donne le ton à tous les autres. C'est par lui que commencera la reconstruction.

*Prochaine étape (à froid) : rédiger `ARCHITECTURE.md` — le détail écran par écran de la V2, en partant de ces décisions et de la vision « un compagnon pour une personne ».*

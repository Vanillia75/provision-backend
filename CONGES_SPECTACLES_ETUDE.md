# Étude — Congés Spectacles (Audiens) — LECTURE SEULE, non commitée

> Étude préparatoire à une décision (rappel daté niveau 1 / estimation niveau 2).
> Sourcing consigné le 2026-07-03. Aucun code. À commiter seulement si validé.

## 1. Règle sourcée

| Paramètre | Valeur | Source | verifie |
|---|---|---|---|
| **Période de référence (exercice)** | 1er avril (N) → 31 mars (N+1) | Audiens ; multiples guides | **True** |
| **Indemnité (ICP) versée au salarié** | **10 % des salaires BRUTS cumulés** sur l'exercice | Audiens ; mescachets ; culturepay | **True** |
| **Assiette** | salaires bruts AVANT abattement/déductions (frais pro non déduits), cumulés avril→mars | Audiens FP-Conges-Spectacles | **True** (nuance §zones grises) |
| **Cotisation employeur** | 15,5 % du brut (dont ~10 % ICP + ~5,5 % cotisations) — À LA CHARGE EMPLOYEUR | Audiens ; compta-online | True (info, pas nécessaire au calcul salarié) |
| **Éligibilité** | CDD(U) < 12 mois dans le spectacle. Exclus : CDI, contrats > 12 mois, hors champ spectacle | fichou-avocat ; culturepay | **True** |
| **Ouverture de la demande** | à partir de la **mi-avril** (exercice clos au 31/03) | Audiens ; cibtp | **True** |
| **Paiement** | par virement, **à partir du 1er mai** de l'année suivant l'exercice | Audiens | **True** |
| **Deadline de demande** | au plus tard le **31 mars de l'année suivante** ; prescription **3 ans** | Audiens ; mescachets | **True** |
| **Canal** | espace perso Congés Spectacles (en ligne) ou courrier (Audiens TSA 90406, 92177 Vanves) | Audiens | True |

Sources : https://www.audiens.org/solutions/vos-conges-spectacles.html · https://www.audiens.org/files/live/sites/siteAudiens/files/03_documents/entreprise/Fiches%20techniques/FP-Conges-Spectacles-V2018.pdf · https://www.mescachets.com/conges-spectacles-audiens-guide-pour-intermittents · https://blog.culturepay.fr/intermittent-du-spectacle/conges-spectacles/ · https://www.fichou-avocat.fr/post/conges-spectacle-intermittent

### Zones grises (à flaguer)
1. **10 % = BRUT.** L'ICP de 10 % est un montant BRUT ; le NET reçu est inférieur (cotisations sociales sur l'ICP). « Environ 10 % de tes bruts » = le brut, pas ce qui tombe sur le compte. → afficher « brut » explicitement.
2. **Assiette exacte.** « 10 % des bruts déclarés » est le titre ; l'assiette précise (quelles lignes de brut, plafonds éventuels par contrat) mérite confirmation sur un bordereau réel. Le taux global 10 % est fiable ; le centime ne l'est pas sans doc réel.
3. **Éligibilité par contrat.** Tous les CDDU spectacle < 12 mois comptent ; un contrat long ou hors champ non. En pratique, quasi toute l'activité intermittente qualifie, mais on ne peut pas garantir 100 % sans le détail contrat par contrat.

## 2. Ce qu'on a déjà en base
- **`IntermittentActivity.salaire_brut`** (Float, nullable) existe — on stocke bien le **brut par activité**.
- **Alimenté depuis les AEM scannées** (`aem_extractor` extrait `salaire_brut`). ✓ Ex. réels : ETOILE DE REVE 287,92 €, etc.
- **MAIS** : le **formulaire de saisie manuelle rapide** (cachets/heures) **ne demande pas** le salaire brut → une activité ajoutée à la main a `salaire_brut = null`.
- **Période** : toutes les activités sont datées → on peut filtrer sur l'exercice 1er avril → 31 mars.
- **Ce qui manque pour l'assiette d'une année** :
  1. **Complétude des bruts** : seules les activités scannées ont un brut ; les saisies manuelles non.
  2. **Un champ `salaire brut` dans le formulaire rapide** (aujourd'hui seul l'écran AEM le capture).
  3. Rien qui distingue « éligible Congés Spectacles » (mais quasi tout l'intermittent l'est → peu bloquant).

## 3. Plan niveau 1 — rappel daté (candidat pré-lancement)
- **Mécanisme** : une carte de présence datée, exactement comme la **bannière d'actualisation** (`actuOuverte`) déjà en place.
- **Déclencheur** : fenêtre **mi-avril → ~30 juin** (ouverture des demandes + début des paiements le 1er mai). Un 2e rappel doux avant la deadline (mars) possible en V2.
- **Texte (voix d'Hector)** :
  > 🐾 Tes **Congés Spectacles** de l'année écoulée (avril→mars) sont **demandables depuis la mi-avril** chez Audiens. C'est environ **10 % de tes bruts** qui dorment si tu ne fais rien — beaucoup d'intermittents oublient. [Faire ma demande sur Audiens →]
- **Loi X** : **aucun montant calculé** → pas de badge estimation nécessaire (c'est un rappel, pas un chiffre). Zone sûre.
- **Effort** : **faible** (~1-2 h). Frontend pur, calqué sur la bannière actu. Lien externe vers l'espace Audiens.

## 4. Plan niveau 2 — estimation du montant (post-lancement)
- **Périmètre V1** : ICP brute ≈ **10 % × Σ(salaire_brut des activités sur l'exercice avril→mars)**. Affiché « **environ X € brut** » + badge estimation.
- **Tests AVANT le code** : somme des bruts sur la bonne fenêtre (avril→mars) ; exclusion hors fenêtre ; activités sans brut → **signalées comme incomplètes** (pas ignorées en silence) ; 10 % exact ; brut ≠ net rappelé ; cas 0.
- **Loi X** : c'est un **montant € → badge estimation obligatoire** + dépliable (« sur la base de X activités, Y € de bruts saisis ») + « Audiens reste seul juge ». **Backtest réel = l'attestation/bordereau annuel Audiens** (le document qui indique l'ICP réellement versée). Tant qu'un bordereau réel n'a pas validé, ça reste estimation — et surtout **incomplet si des bruts manquent** (comme le contrôle de conformité : « je ne vois que ce que tu as saisi »).
- **Effort** : **moyen** (~½ journée) : calcul + carte + fenêtre avril→mars + gestion de l'incomplétude. Pré-requis utile : ajouter le champ brut au formulaire rapide.

## Reco
**Niveau 1 seul pour septembre.** Niveau 2 en post-lancement.
- L1 : cheap, sûr (pas de montant → hors piège Loi X), très on-brand (présence/rappel = notre force), et il rend un **vrai service** (10 % qui dorment). Différenciant, aucun concurrent grand public gratuit ne le fait.
- L2 : c'est un montant (Loi X), il exige un **bordereau Audiens réel** pour le backtest ET des données de bruts complètes (aujourd'hui partielles). À faire quand on aura une vraie attestation à confronter — donc après le lancement.

## Backtest réel (registre)
ICP brute = 10 % des bruts de l'exercice — vérifié au centime sur 3 millésimes Audiens réels (cas réel n°1) :

| Exercice | Assiette bruts | ICP brute (10 %) |
|---|---|---|
| 2023-2024 | 7 381 € | 738,10 € |
| 2024-2025 | 10 055 € | 1 005,50 € |
| 3ᵉ millésime | 6 859 € | 685,90 € |

→ la règle « 10 % » est solide sur le **brut**. Le **net** reste une estimation (≈ 76,95 %) ; le **net-net (après PAS)** n'est **jamais** affiché (PAS personnel et variable).

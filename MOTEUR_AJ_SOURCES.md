# Moteur AJ — Les formules officielles sourcées

> **Étape 1 du chantier « allocation journalière en € nets »** — réalisée le 2026-07-03.
> Règle d'or du chantier : **pas un euro affiché tant que le backtest sur cas réels
> n'est pas à ±5 %**. Ce document est la source unique des formules ; le moteur
> devra pointer ici, et chaque valeur ira dans `regles_intermittent.py` avec sa trace.

## Sources

1. **Guide officiel France Travail « Intermittents du spectacle »** (PDF, 28 p.) —
   https://www.francetravail.fr/files/live/sites/PE/files/fichiers-en-telechargement/fichiers-en-telechargement---dem/GUIDE-INTERMITTENT.pdf
   → formules A/B/C (p. 11), brut→net (p. 12), franchises (p. 13-15), mois type (p. 16-17).
2. **Unédic — Paramètres utiles, avril 2025** (PDF) — AJ minimale, planchers, partie C.
3. Recoupements : ARTCENA, tauxintermittent.net (cohérents avec 1 et 2).

## 1. L'allocation journalière brute : AJ = A + B + C

| | Annexe 8 (techniciens) | Annexe 10 (artistes) |
|---|---|---|
| **A** (salaires) | AJmin × [0,42 × SR (≤ 14 400 €) + 0,05 × SR (> 14 400 €)] / 5000 | AJmin × [0,36 × SR (≤ 13 700 €) + 0,05 × SR (> 13 700 €)] / 5000 |
| **B** (heures) | AJmin × [0,26 × NHT (≤ 720 h) + 0,08 × NHT (> 720 h)] / 507 | AJmin × [0,26 × NHT (≤ 690 h) + 0,08 × NHT (> 690 h)] / 507 |
| **C** (fixe) | AJmin × 0,40 = **12,78 €** | AJmin × 0,70 = **22,37 €** |
| **Plancher** | 38 € | 44 € |

- **AJ minimale = 31,96 €** (depuis le 01/07/2023 — évolue avec le SMIC).
- **Plafond de l'AJ = 174,80 €** (depuis le 01/01/2024).
- **SR** = salaires bruts soumis à cotisations (annexes 8/10), AVANT abattement frais pro.
  ⚠️ Les salaires d'heures d'enseignement ne comptent PAS dans le SR.
- **NHT** = heures travaillées annexes 8/10 en France (+ PTP, + assimilées ALD).
  ⚠️ Les heures assimilées **formation suivie et enseignement** comptent pour les 507 h
  mais **PAS pour le montant** (exclues de NHT) — guide p. 8.
- Réadmission avec période allongée : les diviseurs deviennent NH × SMIC horaire (A) et NH (B).

**Cas de vérification officiel (guide, exemple 6)** — technicien A8, 800 h, 18 000 € :
A = 31,96 × [(0,42 × 14 400) + (0,05 × 3 600)]/5000 = 39,80 € ; B = 31,96 × [(0,26 × 720) + (0,08 × 80)]/507 = 12,20 € ; C = 12,78 € → **AJ = 64,78 €** ✓ (premier test unitaire du futur moteur)

## 2. Le mois type (ce que l'utilisateur touche vraiment)

1. **Jours de travail du mois** = heures du mois / 8 (A8) ou / 10 (A10). Cachet = 12 h.
   Activités non quantifiées (piges, auto-entreprise…) : heures = rému brute / SMIC horaire.
2. **Seuil de non-indemnisation** : 26 jours de travail (A8) / 27 jours (A10) → 0 € le mois.
3. **Jours non indemnisables** = jours de travail × **1,4** (A8) ou × **1,3** (A10).
4. **Jours indemnisables** = jours calendaires du mois − jours non indemnisables
   (− délai d'attente, − franchises mensuelles le cas échéant, dans cet ordre).
5. **ARE du mois** = AJ brute × jours indemnisables.
6. **Plafond de cumul** : ARE + rémunérations brutes ≤ **118 % du PMSS** ; si dépassé,
   ARE recalculée = plafond − rémunérations (jours recalculés à l'entier supérieur).

**Cas de vérification officiel (guide, exemple 12)** — A8, AJ 140 €, 80 h et 4 000 € bruts
en avril 2024 (30 j) : 10 j travaillés < 26 ✓ ; non indemnisables = 10 × 1,4 = 14 ;
indemnisables = 16 ; ARE = 2 240 € ; cumul 6 240 € > plafond 4 559,52 € → ARE recalculée.
(second test unitaire du futur moteur)

## 3. Du brut au net

- AJ brute ≤ 31,96 € → **net = brut** (aucune retenue).
- 31,96 € < AJ ≤ 60 € → retenue **retraite complémentaire = 0,93 % × SJM**
  (SJM = SR / (NHTM/8) en A8 ; SR / (NHTM/10) en A10).
- AJ > 60 € → en plus : **CSG 6,2 %** (ou 3,8 % selon revenu fiscal, ou 0 % si non imposable)
  + **CRDS 0,5 %** (assiette CSG/CRDS = 98,25 % de l'allocation — à confirmer, cf. §5).
- Alsace-Moselle : +1,50 %. Puis prélèvement à la source selon le taux fiscal.

## 4. Franchises et délais (décalent le premier paiement)

- **Délai d'attente** : 7 jours à chaque ouverture/réadmission (max 7 j par 12 mois).
- **Franchise congés payés** = (jours travaillés dans la PRA × 2,5) / 24, arrondie à l'entier,
  **plafonnée à 30 jours** ; consommée par forfait mensuel de 2 j (si total ≤ 24 j) ou 3 j.
- **Franchise salaires** : répartie sur les 8 premiers mois (total/8, arrondi sup.), non plafonnée.
  ⚠️ **Formule exacte du total à confirmer** (le PDF la présente en schéma illisible en texte :
  fait intervenir salaires de la PRA, SMIC mensuel/journalier et 3 × SJM). → source
  complémentaire nécessaire avant de coder ce morceau.
- **Différé d'indemnisation** : indemnités de rupture supra-légales / SJM (ex. 8 du guide).

## 5. Reste à confirmer avant/pendant le code

1. Formule exacte de la **franchise salaires** (schéma p. 14 illisible en extraction texte).
2. **Assiette CSG/CRDS** (98,25 % ?) et arrondis exacts appliqués par France Travail.
3. Valeur **PMSS courante** pour le plafond 118 % (paramètre annuel).
4. Revalorisations : AJ min (31,96 €) et plafond AJ (174,80 €) à vérifier au 01/07/2026.

## 6. Le backtest (la porte à franchir avant tout affichage)

Disponible aujourd'hui :
- ✅ 2 cas officiels du guide (exemples 6 et 12) → tests unitaires.
- ✅ Historique réel d'heures/salaires (attestations FCTU/AEM réelles scannées le 2026-07-03).
- ✅ **BACKTEST RÉEL n°1 : RÉUSSI À 0,00 € D'ÉCART** (2026-07-03). Notification France
  Travail réelle (annexe 10, reprise ARE du 29/06/2026) : SR 8 537,10 €, NHT 636 h,
  AJ nette officielle **51,18 €**. Calcul avec les formules du §1 + §3 : A=19,64 +
  B=10,42 + C=22,37 = 52,43 € brute ; retenue 0,93 % × SJM 134,23 = 1,25 € →
  **51,18 € nette. Écart : 0,000 %** (objectif ±5 %).
  Enseignement : arrondir CHAQUE partie (A, B, C, retenue) au centime — c'est le
  schéma d'arrondi de France Travail. Ce cas devient un test unitaire obligatoire.
- 👍 Souhaitable : 1-2 notifications supplémentaires (autres profils : annexe 8,
  SR > plafond, AJ > 60 € avec CSG) pour couvrir les autres branches du calcul.

## 7. Plan des étapes suivantes

1. Porter ces valeurs dans `regles_intermittent.py` (+ jumeau .js), chacune tracée, `verifie`
   selon le §5.
2. **Écrire `test_allocation.py` AVANT le moteur** (exemples 6 et 12 en premiers cas).
3. Moteur `allocation_engine.py` séparé du moteur heures — mêmes principes (pur, testable).
4. Backtest sur cas réels (±5 % ou rien).
5. Seulement ensuite : l'affichage (« environ X € », badge estimation, renvoi France Travail).

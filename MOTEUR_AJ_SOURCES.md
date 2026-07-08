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
- **Franchise salaires** : répartie sur les 8 premiers mois (total/8, arrondi sup.), non plafonnée ;
  reliquat reporté au-delà des 8 mois.
  ⚠️ **Formule exacte du total à confirmer** (le PDF la présente en schéma illisible en texte :
  fait intervenir salaires de la PRA, SMIC mensuel/journalier et 3 × SJM, « diminuée de 27 jours »).
  → source complémentaire nécessaire avant de coder CE morceau (le total reste une ENTRÉE du moteur).
- **Différé d'indemnisation** : indemnités de rupture supra-légales / SJM (ex. 8 du guide).

**✅ MÉCANISME CODÉ + TESTÉ (2026-07-09)** — texte officiel annexe X (Unédic, décret 2016-961,
art. 29 §1, 30, 31 §1-2) relu mot à mot ce jour :
- **Ordre d'imputation** (art. 31 §1) : « différé d'indemnisation, délai d'attente, franchise
  congés payés, franchise [salaires] ».
- **Rythme CP** (art. 31 §2) : « 2 jours par mois, lorsque le nombre de jours de congés acquis
  est inférieur à 24 jours ; ou de 3 jours par mois, lorsque [...] supérieur à 24 jours, jusqu'à
  épuisement ».
- **Computation** (art. 31 §2) : « Seuls les jours indemnisables [...] servent à la computation
  des franchises » → un mois saturé de travail ne consomme RIEN, tout se reporte.
- Codé dans `calculer_mois` (`allocation_engine.py`) : paramètres `franchise_cp_restante/_totale`
  + `franchise_salaires_restante/_totale` (les TOTAUX sont des entrées, lus sur la notification
  d'admission — pas calculés). 7 tests mécanisme dans `test_allocation.py`.
- ⛔ **Backtest au centime : PAS ENCORE POSSIBLE (cas réel n°2)**. Les 9 jours « non indemnisé »
  récurrents du relevé du 14/04/2026 **contredisent** l'hypothèse « franchise CP » : rythme
  plafonné à 3 j/mois ET février n'en montre aucun (22 AJ + 6 travail = 28 j pile). Les 9 jours
  de mars restent **inexpliqués** (ni décalage des 40 h = 7 j, ni franchises). **Pièce
  manquante = la notification d'admission/réadmission du dossier (mai 2025)** qui affiche les
  totaux de franchises, + le prochain relevé (verdict du gel de mars). Tant que ces 9 jours ne
  sont pas reproduits, la branche franchise reste NON AFFICHABLE (Loi X).

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
  Enseignement — schéma d'arrondi de France Travail (décodé sur exemple 6 + cas réel) :
  les parties A, B, C sont **TRONQUÉES** au centime (39,8094 → 39,80, pas 39,81) ;
  la retenue retraite est **ARRONDIE** au centime (1,2483 → 1,25). Ce cas est un
  test unitaire obligatoire (`test_allocation.py`).
- ✅ **BACKTEST MENSUEL — cas réel n°1 (2026-07-04)** : décomptes réels octobre + novembre 2025 (annexe 10, AJ brute 51,93 € avant la revalorisation du 25/11 → 52,43 €).
  - **Octobre 2025 (mois plein) — VALIDÉ** : 0 h → 0 jour non indemnisable → **31 j × 51,93 = 1 609,83 € brut** ; − retraite 35,34 € (pas de CSG, exonérée) = **1 574,49 € net social** ; − PAS 5,6 % (88,04 €) = **1 486,45 € versé** ✓ (écart moteur 0,13 € = base du PAS, impôt personnel non estimé). Valide la **branche « mois plein »** + la **chaîne brut→net**.
  - **Décalage mensuel — formule confirmée par les textes** : annexe 10 = **(heures du mois ÷ 10) × 1,3** ; les revenus non-annexe (piges, micro-entreprise…) sont convertis en heures (÷ SMIC horaire) et **comptés eux aussi**. **Une seule formule** — la piste « salaire ÷ SJR » est **écartée** (fausse piste, consignée ici pour éviter la rechute). Sources : ARTCENA, Unédic (cumul allocation/salaire). Re-validée sur octobre (0 h → 31 j) + exemple 12 du guide (80 h A8 → 14 non indemnisables). ⚠️ arrondi à figer : le texte tronque (7,8 → 7), le moteur arrondit.
  - **Novembre 2025 — NON reproductible, entrées incomplètes** : 13 cachets / 1 625 € (156 h) → la formule donne **10 j indemnisables (~508 €)**, mais le réel = **95,90 € net versé le 03/12 = 2 j indemnisables** (28 non indemnisables ≈ 216 h comptées). Il **manque ~60 h** ; cause probable = **activité micro épisodique** (précédent : CA 6 578 € déclaré URSSAF en **nov 2024** ; l'actualisation FT de **nov 2025**, seule preuve possible, est **indisponible**). **On arrête de creuser.**
  - **Verdict Loi X** : « mois plein » **affichable** ; **mois travaillé = branche NON validée → aucun affichage** tant qu'un mois travaillé n'est pas reproduit au jour près.
  - **PAS variable** (ici 5,6 %) → confirme **« net-net jamais estimé »** (net social affiché, jamais l'après-impôt).
  - **Prochain juge** : décompte de **juillet 2026** (cas réel n°1, réindemnisée depuis le 27/06/2026 ; notification du 03/07 : AJ 51,18 €, franchises congés payés mentionnées), dispo **début août 2026**. **Chantier mensuel EN PAUSE** d'ici là.
- ✅ **BACKTEST DÉCALAGE — cas réel n°2, annexe 8 (2026-07-09)** : relevé de situation FT réel
  du 14/04/2026 (période 30/03 → 09/04/2026). Profil : SJR 168,37 €, AJ brute 63,69 €,
  droit ouvert le 17/05/2025, date limite 11/05/2026.
  - **Février 2026 — VALIDÉ AU JOUR PRÈS** : 34,5 h déclarées → (34,5 ÷ 8) × 1,4 = 6,04
    → **6 jours non indemnisables**, exactement ce que FT retient (22 AJ sur 28 j). La
    formule décalage **annexe 8 = (h ÷ 8) × 1,4, tronquée**, tient sur un cas réel.
  - **Mars 2026 — la régularisation du 03/04 VALIDE la conversion cachet isolé = 12 h** :
    déclaré 40 h + 5 cachets. FT retient d'abord **10 jours** = les cachets seuls
    (5 × 12 h = 60 h → 60 ÷ 8 × 1,4 = 10,5 → 10) : les AEM des cachets sont arrivées
    avant celles des 40 h. Puis régularisation du 09/04 : mars entièrement gelé
    (« somme en attente de décision administrative », trop-perçus 610 € + 732 €) =
    **gel conservatoire le temps de l'instruction, PAS une formule** ; le total réel
    (40 + 60 = 100 h → 17 j) devrait rendre ~5 AJ sur un relevé ultérieur. À suivre.
  - **Prélèvements réels observés (AJ 63,69 €)** : retraite comp. = 0,93 % × SJR
    = 1,57 €/j ✓ (notre formule) ; mais **CSG réelle = 1,12 €/j** alors que la formule
    plate (6,2 % × 98,25 % × AJ) donnerait 3,88 €/j → **écrêtement/seuil CSG NON modélisé**
    (net réel 61,00 €/j). ⚠️ Toute estimation nette pour AJ > ~60 € est fausse tant que
    cette règle n'est pas sourcée et codée. (Cohérent avec la Loi X : > 60 € jamais affiché.)
  - **Mystère persistant** : 9 jours « non indemnisé » chaque mois, même sans travail déclaré
    (mars avant régul : 22 AJ + 9 = 31 j). Hypothèse forte : **franchise congés payés**
    (déjà identifiée comme chaînon manquant). Preuve = notification de franchise du dossier,
    non disponible.
- 👍 Souhaitable : 1-2 notifications supplémentaires (autres profils : annexe 8,
  SR > plafond, AJ > 60 € avec CSG) pour couvrir les autres branches du calcul.

## 7. Plan des étapes suivantes

1. Porter ces valeurs dans `regles_intermittent.py` (+ jumeau .js), chacune tracée, `verifie`
   selon le §5.
2. **Écrire `test_allocation.py` AVANT le moteur** (exemples 6 et 12 en premiers cas).
3. Moteur `allocation_engine.py` séparé du moteur heures — mêmes principes (pur, testable).
4. Backtest sur cas réels (±5 % ou rien).
5. Seulement ensuite : l'affichage (« environ X € », badge estimation, renvoi France Travail).

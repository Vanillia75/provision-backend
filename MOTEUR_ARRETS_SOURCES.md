# Moteur 507h — Arrêts maladie / maternité / AT-MP : les règles sourcées

> **Étape ZÉRO du chantier « arrêts »** — sourcing uniquement, réalisé le 2026-07-03.
> Aucun code, aucun test écrit. Ce document est le livrable à valider ensemble AVANT
> tout développement. Règle du chantier : là où les textes sont ambigus, c'est dit
> explicitement → la branche héritera d'un drapeau `estimation` (Loi X).

## Sources

1. **Guide officiel France Travail « Intermittents du spectacle »** (PDF, 28 p.) —
   https://www.francetravail.fr/files/live/sites/PE/files/fichiers-en-telechargement/fichiers-en-telechargement---dem/GUIDE-INTERMITTENT.pdf
   → p.5 (neutralisation/allongement), p.8 (suspension pendant contrat), p.9 (assimilations hors contrat), p.11-12 (SR aménagé, exemple 7).
2. **Unédic — Dossier de synthèse « L'indemnisation des intermittents »** — https://www.unedic.org/ (recoupement).
3. **matermittentes.com — « Congé maternité côté France Travail »** — https://www.matermittentes.com/maternite-cote-france-travail (source terrain de référence sur la maternité).
4. **SNAM-CGT — congé maladie/maternité/paternité/AT intermittent** — https://www.snam-cgt.org/droits-sociaux/securite-sociale/conge-maladie-maternite-paternite-accident-du-travail-intermittent/

---

## 1. LE POINT-CLÉ : deux mécanismes DIFFÉRENTS, à ne pas confondre

L'erreur classique est de croire que « tout arrêt = des heures ». Faux. Il y a **deux effets distincts**, et un même arrêt ne déclenche pas forcément les deux :

### A. Assimilation EN HEURES (ajoute des heures vers les 507h)
Certaines périodes comptent comme du travail, à **5 heures par jour** (tous les jours calendaires, week-ends inclus — source matermittentes).

### B. Neutralisation + ALLONGEMENT de la période de référence (n'ajoute AUCUNE heure)
La période d'arrêt est « neutralisée » : elle n'ajoute pas d'heures, mais **allonge la fenêtre de 365 jours d'autant** (10 jours d'arrêt = 10 jours de plus pour chercher les 507h en remontant plus loin). Sert à ne pas être pénalisé par un « trou » d'arrêt.

**C'est LA subtilité du chantier.** Un arrêt maladie ordinaire *hors contrat* relève de **B seulement** (il n'ajoute pas d'heures), alors qu'une maternité relève de **A** (5h/jour). Les confondre ferait mentir le compteur dans l'autre sens (sur-comptage).

---

## 2. Détail par type d'arrêt (source : guide FT p.5, p.8, p.9)

| Situation | Effet | Taux | Conditions | `verifie` |
|---|---|---|---|---|
| **Suspension du contrat** (arrêt PENDANT un contrat : maladie, AT, accident de trajet) | A (heures) | 5h/jour | l'arrêt survient pendant un contrat en cours | **True** (p.8) |
| **Congé maternité** (hors contrat) | A (heures) | 5h/jour | indemnisé **Sécu OU Audiens** + retravailler après | **True** (p.9) |
| **Congé d'adoption** (hors contrat) | A (heures) | 5h/jour | indemnisé Sécu + retravailler après | **True** (p.9) |
| **Accident du travail** qui se prolonge à l'issue du contrat | A (heures) | 5h/jour | indemnisé Sécu | **True** (p.9) |
| **Arrêt maladie au titre d'une ALD** (affection longue durée), hors contrat | A (heures) | 5h/jour | indemnisé Sécu **+ au moins une ouverture de droit annexe 8/10 antérieure** | **True** (p.9) |
| **Arrêt maladie ORDINAIRE** (non-ALD), hors contrat, indemnisé Sécu | **B seulement** (neutralise + allonge, PAS d'heures) | — | y compris **congé paternité** (voir §4) | **True** (p.5) |

**Condition transversale (p.9)** : *« Vous devez justifier d'une période de travail APRÈS ces événements pour leur prise en compte »* (sauf PTP). Un arrêt qui n'est suivi d'aucun contrat ne compte pas — il ne peut pas servir de point de départ de la période de référence.

**Pas de plafond** sur les heures assimilées d'arrêt (contrairement à la formation plafonnée à 338h). Un congé maternité de 112 jours = **560h** à lui seul (> 507h). Confirmé matermittentes.

---

## 3. Effet sur le MONTANT de l'allocation (≠ le comptage des heures)

Quand une maternité/adoption/ALD *hors contrat* a été retenue dans les 507h, le salaire de référence est **aménagé** en SAR (guide p.11-12, exemple 7) :
`SAR = [SR / (jours de la période − jours d'arrêt)] × jours de la période`.
Exemple 7 : technicien, 8 000 € sur 12 mois, 120 j de maternité → SAR = [8 000/(365−120)]×365 = **11 918,36 €**. → ça relève du **moteur AJ** ([[hector-roadmap-proposee]] / `MOTEUR_AJ_SOURCES.md`), pas du moteur 507h.

---

## 4. Maternité en détail + le point litigieux (cas testeuse #2)

- **Assimilation** : 5h/jour, tous jours calendaires, indemnisé **Sécu ou Audiens** (Audiens = caisse intermittents, souvent plus favorable). 112 jours → 560h.
- **Recalcul de la date anniversaire** (source matermittentes) : France Travail **repart du dernier contrat AVANT le congé**. *« Le lendemain de cette dernière date travaillée avant mon congé devient ma nouvelle date anniversaire. »* → la date anniversaire se **décale** sur la dernière date travaillée pré-congé.
- **Le litige classique post-maternité** (celui de la testeuse #2, probablement) : désaccord sur **les dates retenues** — quelle « dernière date travaillée avant le congé » France Travail prend, et donc quelle période de référence. S'y ajoute le piège du **congé non indemnisé** : *seuls les jours indemnisés* comptent ; un jour de maternité non couvert crée un trou qui n'assimile pas et ne neutralise pas → dates faussées. Les matermittentes rapportent 2-3 ans pour récupérer des droits mal recalculés.
- ⚠️ **PATERNITÉ — divergence de sources réelle** : le guide FT officiel (p.5) range le **congé paternité** avec la maladie ordinaire → **neutralisation/allongement seulement, PAS 5h/jour**. Mais plusieurs sources communautaires l'assimilent à 5h/jour comme la maternité. **Contradiction non tranchée** → `verifie: False`, drapeau `estimation` obligatoire, ou exclusion V1.

---

## 5. Zones grises à flaguer (Loi X → `estimation` ou exclusion)

1. **Pendant contrat vs hors contrat** : la règle applicable en dépend, mais le modèle de données d'Hector (activités = date + type + heures) **n'encode pas les bornes de contrat par jour**. Hector ne peut pas *déduire* si un arrêt était pendant ou hors contrat → il devra le **demander explicitement** à l'utilisateur.
2. **Paternité** : contradiction guide officiel vs terrain (§4).
3. **« Fractionnement »** : ce terme **n'apparaît pas** dans les textes officiels consultés. Le mécanisme réel s'appelle **neutralisation + allongement** (§1-B). Si tu vises autre chose par « fractionnement » (ex. fractionner un congé en plusieurs morceaux), à préciser — je ne l'ai pas trouvé comme règle nommée.
4. **Conditions non vérifiables par Hector** : « retravailler après l'arrêt », « au moins une ouverture de droit antérieure » (ALD), « indemnisé Sécu/Audiens » → Hector ne connaît pas ces faits ; il devra les **rappeler comme conditions** sans pouvoir les enforcer.
5. **Arrêt non indemnisé** : ne compte ni en heures ni en neutralisation. Piège fréquent.

---

## 6. Réponses directes aux 4 questions du cadrage

1. **Assimilation** : maternité/adoption (hors contrat, 5h/j), AT prolongé (5h/j), ALD hors contrat (5h/j, + condition ouverture antérieure), suspension pendant contrat (5h/j). Distinction pendant/hors contrat = **cruciale et confirmée**.
2. **Allongement** : maladie ordinaire (+ paternité) hors contrat, indemnisée Sécu → neutralisée, allonge la fenêtre de 365j d'autant. Le « fractionnement » n'est pas un terme officiel (§5.3).
3. **Maternité** : 5h/j (Sécu ou Audiens), décale la date anniversaire sur la dernière date travaillée pré-congé, SAR aménagé pour le montant. Litige = dates retenues + jours non indemnisés (§4).
4. **Hors périmètre V1** : voir §7.

---

## 7. PROPOSITION de périmètre V1 — À VALIDER ENSEMBLE

**Principe** : ne faire que ce qui est calculable de façon fiable, tout le reste = message honnête « pas encore géré, vois France Travail ». Comme la formation, on ajoute une saisie explicite ; contrairement à la formation (front-only au départ), ici l'utilisateur **déclare le type d'arrêt** pour lever l'ambiguïté pendant/hors contrat.

### DANS le périmètre V1
- Nouveau type de saisie « arrêt », l'utilisateur choisit **le type** (maternité, adoption, AT/MP, ALD, arrêt pendant un contrat) + **nb de jours** + coche « indemnisé ».
- Le moteur **assimile 5h/jour** (tous jours calendaires) vers les 507h, **UNIQUEMENT** pour les types du mécanisme A.
- **Tout apport d'arrêt est marqué `estimation`** (branche non validée sur cas réel tant qu'on n'a pas le dossier d'Héloïse) + rappel des conditions non vérifiables (retravailler après, indemnisation).
- Aucun plafond (cohérent avec la règle).

### HORS périmètre V1 (exclus, avec message honnête)
- **Neutralisation/allongement** de la période de référence (maladie ordinaire hors contrat) : change le calcul de la fenêtre `borne_basse` — trop structurant, reporté. Hector dira « ça ne t'ajoute pas d'heures mais décale ta période — pas encore géré ».
- **SR aménagé (SAR)** pour le montant : relève du moteur AJ, pas des 507h.
- **Paternité** : exclue V1 (contradiction de sources) ou affichée `estimation` avec avertissement — **à trancher ensemble**.
- **Recalcul automatique de la date anniversaire** post-maternité : Hector continue de lire la date sur l'ARE (jamais recalculée), il ne la déduit pas.
- Conditions ALD (ouverture antérieure) : non enforced, seulement rappelées.

### Cas de test PRÉVUS (à écrire APRÈS validation, AVANT le code)
1. Maternité 112 j indemnisée → 560h assimilées, droits ouverts, drapeau `estimation`.
2. Week-ends inclus : arrêt de 7 j → 35h (pas 5×5).
3. AT/MP 30 j → 150h.
4. Suspension pendant contrat 10 j → 50h.
5. ALD 60 j → 300h + rappel « nécessite une ouverture de droit antérieure ».
6. Arrêt maladie ordinaire hors contrat → **0h ajoutée** + message « neutralisation non gérée, vois FT » (pas de sur-comptage).
7. Paternité → selon décision (exclu → 0h + message ; ou `estimation`).
8. Arrêt **non indemnisé** → 0h.
9. Mélange arrêt + cachets + heures : total cohérent, drapeau estimation présent dès qu'un arrêt contribue.

---

## 8. Backtest (la porte Loi X avant tout affichage définitif)
- ❌ **Manque : un dossier maternité réel complet** (notification de réadmission France Travail + dates retenues + heures assimilées). C'est l'équivalent du courrier ARE pour le moteur AJ. → demandé à **Héloïse** (a vécu un congé mat en intermittence).
- 👍 Souhaitable : le cas (anonymisé) de la **testeuse #2** si son litige est documenté — deuxième juge.
- Tant qu'aucun dossier réel ne valide la branche, l'apport d'arrêt reste `estimation` à l'écran.

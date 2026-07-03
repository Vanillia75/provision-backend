# Moteur — Période de référence : fractionnement / allongement / conformité

> **Étape ZÉRO** du chantier « contrôle de conformité » — sourcing uniquement, 2026-07-03.
> Aucun code, aucun test. Livrable à valider ensemble AVANT tout dev.
> Ce chantier est le **socle** de la feature-tueuse volée au concurrent (« vérifier la
> décision de France Travail, en humain »). Sa brique manquante = la déformation de la
> période de référence (leur mot : « fractionnement »).

## Sources
1. **Guide officiel France Travail « Intermittents du spectacle »** — p.5 (période de
   référence, neutralisation, réadmission 42h/30j), p.9 (assimilations), p.11-12 (SAR).
   https://www.francetravail.fr/files/live/sites/PE/files/fichiers-en-telechargement/fichiers-en-telechargement---dem/GUIDE-INTERMITTENT.pdf
2. **matermittentes.com** — recalcul de date post-maternité.
3. Recoupement Unédic / ARTCENA. Le concurrent (intermittent-application.fr) confirme
   la terminologie : « fractionnement de la période de référence en cas de maladie ».

## 1. La période de référence et ses TROIS déformations

**Base** : 365 jours (12 mois) précédant la **fin de contrat retenue** (FCT) pour l'ouverture.
*Ex. FCT du 10/02/2023 → référence du 11/02/2022 au 10/02/2023.*

### A. Neutralisation + allongement (le « fractionnement »)
Les périodes de **maladie ordinaire entre deux contrats** (y compris **congé paternité**),
indemnisées par la Sécu, sont **neutralisées** : elles n'ajoutent pas d'heures MAIS
**allongent la période de 365 jours d'autant** (10 j de maladie = 10 j de plus pour
chercher les 507h, en remontant plus loin). Source : guide p.5. `verifie: True`.
→ C'est le **mécanisme B** que j'avais laissé HORS de la V1 arrêts. Ce chantier le code.

### B. Allongement de réadmission (42h / 30 jours)
En **réadmission** (à la date anniversaire ou après), si les 507h ne sont pas réunies sur
365 j, on peut chercher une **affiliation majorée de 42h par tranche de 30 jours au-delà
du 365e jour**. *Ex. guide : 549h sur 395 j.* Limité à la dernière FCT ayant ouvert le
droit précédent (les heures déjà utilisées ne sont pas réutilisables). Source : guide p.5. `verifie: True`.

### C. Recalcul de la date anniversaire post-maternité
France Travail **repart du dernier contrat avant le congé maternité** : *« le lendemain de
cette dernière date travaillée avant le congé devient la nouvelle date anniversaire »*
(matermittentes). Le SR est aménagé en SAR pour le montant (guide p.11-12, exemple 7). `verifie: True`.

## 2. Ce que ça débloque : le CONTRÔLE DE CONFORMITÉ (la feature volée)
Avec la période de référence correctement déformée, Hector peut, à partir des données de
l'utilisateur (AEM + arrêts) et des chiffres de sa notification France Travail :
- **reconstituer** les heures retenues sur la bonne fenêtre (avec assimilations + allongements),
- **comparer** à ce que FT a retenu (NHT, dates),
- **expliquer l'écart avec les sources** : « voilà comment tes 636h se décomposent, voilà la
  règle appliquée, voilà où ça diffère — vérifie ce point avec France Travail ».
C'est exactement le litige de dates d'Héloïse. En version **humaine** (eux le font en jargon).

## 3. Zones grises (Loi X → `estimation` ou exclusion)
1. **« Entre deux contrats » vs « pendant contrat »** : le modèle de données d'Hector
   (activités datées) n'encode pas les bornes de contrat → il devra **demander** si un arrêt
   était hors contrat, comme pour l'assimilation.
2. **Quelle FCT est « retenue »** : c'est un choix de France Travail (souvent la plus favorable).
   Hector ne peut que **proposer une reconstitution**, pas trancher à la place de FT.
3. **Réadmission 42h/30j** : nécessite de connaître les bornes du droit précédent → données
   qu'Hector n'a pas toujours. Probablement HORS V1.
4. **SAR / montant** : relève du moteur AJ (déjà partiellement fait via la carte allocation
   qui compare déjà l'AJ recalculée au montant officiel).

## 4. PROPOSITION de périmètre V1 — À VALIDER ENSEMBLE

### DANS V1
- **Moteur** : période de référence **effective** = 365 j **+ jours d'arrêts neutralisés**
  (maladie ordinaire hors contrat + paternité, déclarés indemnisés). Concrètement : la
  `borne_basse` recule du nombre de jours neutralisés → les contrats plus anciens rentrent
  dans la fenêtre. Complète le mécanisme A (assimilation) déjà livré.
- **Contrôle de conformité V1 (lecture)** : un écran « Hector vérifie ta décision » qui, à
  partir des AEM/arrêts saisis + la NHT officielle (déjà stockée pour l'AJ), affiche la
  **reconstitution d'Hector vs le chiffre de France Travail**, avec l'écart expliqué et
  sourcé, et un **drapeau `estimation`** + « France Travail reste seul juge ».

### HORS V1 (exclus, message honnête)
- Réadmission 42h/30j (§3.3), choix automatique de la FCT retenue (§3.2), recalcul auto de la
  date anniversaire (Hector continue de la lire sur l'ARE), enseignement/plafonds d'âge,
  travail hors annexes (RG/piges) mélangé.

### Cas de test PRÉVUS (à écrire APRÈS validation, AVANT le code)
1. Maladie ordinaire hors contrat 20 j → fenêtre allongée de 20 j, 0h ajoutée, un contrat
   situé à J-370 (hors 365 mais dans 385) est désormais compté.
2. Paternité 25 j indemnisée → même effet (allongement, pas d'heures).
3. Sans arrêt neutralisé → fenêtre = 365 j inchangée.
4. **BACKTEST Héloïse** : reconstitution des heures sur sa fenêtre réelle → doit tendre vers
   les **636h** officielles (aujourd'hui Hector = 572h faute de ses AEM manquantes ; avec le
   fractionnement + ses AEM complètes, on vise 636h au chiffre près).
5. Contrôle de conformité : Hector 636h vs FT 636h → « cohérent ✓ » ; écart → explication sourcée.

## 5. Backtest / validation
- Le dossier d'Héloïse (maternité 112j + AEM) est déjà le cas de référence. Il manque **ses
  AEM de travail de la période** pour boucler les 636h — même besoin que le moteur arrêts.
- Tant qu'aucune reconstitution réelle n'égale la NHT officielle, la conformité reste `estimation`.

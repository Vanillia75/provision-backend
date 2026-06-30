# Flux AEM V2 — décisions actées

> Refonte issue d'une relecture en prod du parcours AEM intermittent, à la lumière des Lois
> (surtout la Loi II — un écran = une idée) et de la grammaire d'expérience commune.

---

## Le constat

L'analyse des AEM (attestations employeur mensuelles) est aujourd'hui **éclatée sur 3
entrées de menu distinctes**, alors que c'est **une seule histoire** :

1. **« Scanner une AEM »** (`coffre`) — l'entrée : je photographie l'attestation.
2. **« Mes documents »** (`attestation`) — 3 onglets : liste des AEM scannées + analyse des
   revenus + suivi des actualisations.
3. **« Calcul des heures »** (`calcul`) — le comptage des heures vers les 507h, à partir
   des mêmes AEM.

→ L'utilisateur doit naviguer entre **3 entrées de menu** pour suivre **un seul fil** :
je scanne → je vois ce que ça a donné → je vois où ça me situe sur les 507h.
C'est trois portes pour un seul couloir. (Violation de la Loi II au niveau de la navigation :
l'idée « mes AEM » est éclatée au lieu d'être un parcours.)

## Ce qui est déjà bon (à conserver tel quel)

L'écran de scan lui-même est **du très bon Hector** — à garder :
- « Photographie ton AEM, je lis tout 🐾 »
- « Employeur, cachets, heures, salaire brut — je remplis tout pour toi. Tu n'as qu'à vérifier. »
- « Une AEM, c'est l'attestation que ton employeur t'envoie après chaque contrat… je la range
  et je l'ajoute à ton compteur. » (explique le jargon, rassure — Loi III)

Le problème n'est pas la **qualité** des écrans, c'est leur **éclatement**. La refonte est
une **réunification**, pas une réécriture.

## La décision : un seul parcours « Mes AEM »

Un parcours linéaire, raconté par Hector, où l'utilisateur **ne change jamais d'écran** pour
suivre son histoire :

```
Je scanne mon AEM
        ↓
Hector lit et pré-remplit (je vérifie)
        ↓
Hector me montre IMMÉDIATEMENT ce que ça donne :
   → + X heures vers mes 507 h   (ce qui était dans « Calcul des heures »)
   → + Y € de revenus            (ce qui était dans « Mes documents > Revenus »)
        ↓
Mon AEM est rangée dans la liste   (ce qui était « Mes documents > Mes AEM »)
```

Après un scan, je vois en un seul endroit : **où ça me mène** (heures + revenus), pas trois
écrans à recoller mentalement. Hector relie le geste (scanner) à sa conséquence (mes droits
avancent) — c'est exactement son rôle : porter la charge mentale à ma place.

## Conséquences sur le menu

- Fusionner `coffre` + `attestation` + `calcul` en **une seule entrée** (« Mes AEM » ou
  équivalent).
- Les actualisations (3e onglet de l'ancien « Mes documents ») : à rattacher au flux
  Actualisation existant (`actu`), pas au parcours AEM — ce n'est pas la même histoire.

---

*Même esprit que le Salon : simplifier sans cacher, Hector relie le geste à sa conséquence,
une seule histoire par parcours.*

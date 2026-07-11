# Carte freemium officielle v1 : Gratuit vs TOTOR Veille

> Figée le 10/07/2026. C'est LA référence unique du partage gratuit/payant.
> Elle réconcilie le tableau produit, la doctrine (`DOCTRINE.md`), le pricing
> figé (`PRICING.md`) et l'état réel du code. En cas de doute, ce document gagne.
>
> Principe (doctrine) : le gratuit informe, calcule et explique, il crée
> l'habitude. TOTOR Veille vérifie, anticipe, agit et surveille. On ne fait
> jamais payer la donnée ni l'habitude, on fait payer l'intelligence et la
> responsabilité retirée.

## Prix (PRICING.md, inchangé)

- Mensuel : 9,99 €/mois. Annuel : 79 €/an. Pionnier : 44,99 €/an à vie,
  100 premiers payants réels.

## Quotas du gratuit (décisions TRANCHÉES, remplacent toute autre valeur)

| Quota | Valeur cible | Prod actuelle | Note |
|---|---|---|---|
| Chat « Parle à TOTOR » | **6 conversations les 30 premiers jours, puis 3/mois** | 3 chats/mois | Une conversation = un FIL (sujet, ~24h), JAMAIS un compteur de messages. Les questions de précision de Totor ne consomment rien. |
| Scan AEM | **5/mois** | 2/mois | Aligné sur la carte produit : le waouh est gratuit, ce quota ne mord presque jamais (1 AEM par employeur). À passer à 5 à la config Stripe. |
| Scan documents (factures, reçus) | **5/mois** | 3/mois | Même logique. |
| Factures / devis (AE) | **5/mois chacun** | illimité | Restriction à activer à la config Stripe, jamais rétroactive sur les documents déjà créés. |
| Mode Achat | **5 simulations/mois** | illimité | Idem. |
| Relances | **1 relance manuelle par facture** | relances auto livrées | Les relances AUTOMATIQUES passent TOTOR Veille. |

## Intermittent

| Fonction | Gratuit | TOTOR Veille | État du côté Veille |
|---|---|---|---|
| Cockpit | Vue d'ensemble | Cockpit intelligent, alertes prioritaires | À construire |
| Calcul des 507h | Illimité | inclus | Existe |
| Simulation « et si j'accepte ce contrat » | Simulation simple | Conseils personnalisés, multi-scénarios | À construire |
| Actualisation France Travail | Préparation | Vérification complète avant envoi | À construire |
| Scan AEM | 5/mois | Illimité | Existe (quota à brancher) |
| Parle à TOTOR | 6 conv. 1er mois puis 3/mois | Illimité | Existe (comptage par FIL à vérifier/brancher) |
| Mes activités | Illimité | inclus | Existe |
| Comprendre (guides) | Tous | inclus | Existe |
| Mes documents | Coffre-fort | Classement intelligent, recherche | Coffre existe ; intelligence à construire |
| Offres spectacle | Consultation | Recommandations personnalisées | Consultation existe ; reco à construire |
| Disponible aujourd'hui | inclus | inclus | Existe. Signature, toujours gratuite |
| Allocation journalière | Estimation | Analyse détaillée, impact futur | Estimation existe ; analyse à construire |
| Mon mois prochain | Projection simple | Projection intelligente, alertes | Simple existe ; intelligente à construire |
| Jours par employeur | inclus | Alertes avant dépassement | Compteur existe ; alertes à construire |
| TOTOR vérifie ta décision | Aperçu | Vérification complète | ⚠️ Existe mais GRATUIT et complet en prod. Bascule en aperçu/complet À FAIRE avant le lancement public (jamais après : retrait vécu comme une trahison) |

## Auto-entrepreneur

| Fonction | Gratuit | TOTOR Veille | État du côté Veille |
|---|---|---|---|
| Cockpit | inclus | Priorisation intelligente | À construire |
| Encaissements / Dépenses | inclus | inclus | Existe |
| Ma Paie | 1 montant conseillé (recommandé) | Prudent + recommandé + maximum + surveillance | ⚠️ Les 3 montants existent et sont gratuits en prod. Bascule à faire avant lancement |
| Mode Achat | 5 simulations/mois | Illimité, recommandations | Existe (quota à brancher) |
| Factures / Devis | 5/mois | Illimité | Existe (quota à brancher) |
| Mes tarifs | inclus | Recommandations | Coach existe ; reco avancées à construire |
| Contacts | inclus | inclus | Existe |
| Modèles | Quelques modèles | Bibliothèque complète | Existe (partage à définir) |
| Déclaration URSSAF | Préparation | Vérification et optimisation | Préparation existe ; vérification à construire |
| Mes échéances | inclus | Alertes intelligentes | Rappels existent ; intelligence à construire |
| Simulateur fiscal | inclus | Optimisation personnalisée | Simulateur existe ; optimisation à construire |
| Relances | 1 relance manuelle | Automatiques illimitées | ⚠️ Relances AUTO livrées et gratuites en prod. Bascule à faire avant lancement |
| Radar acompte | non | inclus | ⚠️ Livré et gratuit en prod. Bascule à faire avant lancement |
| Mon mois prochain | Projection simple | Projection intelligente | Simple existe ; intelligente à construire |

## Commun

| Fonction | Gratuit | TOTOR Veille | État du côté Veille |
|---|---|---|---|
| Connexion bancaire | Connexion + lecture du solde | Analyse, alertes, recommandations | Connexion existe (bêta) ; intelligence à construire |
| Scan de documents | 5/mois | Illimité | Existe (quota à ajuster) |
| Aide contextuelle | inclus, hors quota | inclus | Existe. Toujours gratuite |
| Réglages | inclus | inclus | Existe |
| Emails de rappel | Rappels calendaires (actu le 28, URSSAF J-10) | Emails intelligents (« attention, il manque une AEM ») | Calendaires existent et RESTENT gratuits (accompagnement) ; intelligents à construire |
| Jeu | inclus | inclus | Existe |

## ⚠️ Règle de lancement (Loi VIII appliquée au premium)

**On ne vend jamais une case « À construire ».** À la config Stripe (mi-août),
la page d'abonnement ne liste que ce qui existe. Le reste arrive en mise à jour
et enrichit l'abonnement (bonne surprise), il ne le précède jamais (promesse
trahie).

**Le minimum vendable au lancement** (déjà réel ou petit chantier) :
1. Vérification complète « TOTOR vérifie ta décision » (existe, bascule aperçu/complet à coder)
2. Chat illimité (existe, quota à brancher)
3. Relances automatiques illimitées (existent, bascule à coder)
4. Radar acompte (existe, bascule à coder)
5. Ma Paie 3 montants + Mode Achat illimité + factures/devis/scans illimités (existent, quotas à brancher)

C'est déjà un TOTOR Veille honnête et vendable. Les « intelligences » (cockpit,
projections, emails intelligents, vérification actualisation) viennent ensuite
et augmentent la valeur sans toucher au prix.

## Les 3 bascules sensibles (retraits du gratuit actuel)

À faire AVANT le lancement public, avec un mot honnête aux testeurs
(les comptes bêta gardent l'accès, tarif Pionnier garanti) :
1. « TOTOR vérifie ta décision » complet → aperçu gratuit / complet Veille
2. Relances automatiques → 1 relance manuelle gratuite / auto Veille
3. Radar acompte → Veille ; Ma Paie 3 montants → 1 montant gratuit

## Présentation client (jamais la liste de fonctions)

Les 7 promesses (Bible, partie 7.8) : je te montre / je veille. La page
d'abonnement ouvre sur : « Le TOTOR gratuit te permet de reprendre le contrôle
de ta situation. TOTOR Veille fait en sorte que tu n'aies plus à t'en
préoccuper. » Les 4 moteurs de conversion : vérification, chat illimité,
je m'en occupe, alertes proactives.

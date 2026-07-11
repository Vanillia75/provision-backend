# Application de la doctrine : le vrai TOTOR, écran par écran

> Compagnon de `DOCTRINE.md`. Ici on passe de la doctrine (le pourquoi) à la
> spec (le quoi, concrètement, sur les fonctionnalités qui existent aujourd'hui).
> Objectif : savoir en un coup d'oeil ce qui est gratuit, ce qui est TOTOR Veille,
> et ce qui reste à construire. Figé le 10/07/2026.

Rappel du tri : **Gratuit = Observer + Comprendre** (je te montre, tu reprends le
contrôle). **TOTOR Veille = Agir + Veiller** (je m'en occupe, je te retire une
responsabilité).

---

## 1. Classement des écrans existants

### Intermittent

| Écran / feature | Niveau | Gratuit / Veille | Note |
|---|---|---|---|
| Compteur 507h, « disponible aujourd'hui » | Observer + Comprendre | **Gratuit** | Le coeur du lien. Jamais payant. |
| Scan AEM | Observer | **Gratuit** (quota 2/mois) | Le quota mord peu (1 AEM par employeur). |
| Ton allocation journalière (moteur AJ) | Comprendre | **Gratuit** | Explique, rassure. |
| Simulateur « si j'accepte ce contrat » | Comprendre | **Gratuit** | Montre l'impact sur les 507h. |
| Trouver des cachets / heures (offres FT) | Observer + Comprendre | **Gratuit** | Acquisition. |
| Mes jours par employeur | Comprendre | **Gratuit** | |
| Contrôle de conformité (« Hector vérifie ta décision ») | **Agir** (vérifie) | **TOTOR Veille** | Pépite premium intermittent. |
| Projection « mon mois prochain » (simple) | Comprendre | **Gratuit** | Ce qui fait revenir. |
| Projection avancée + conseils | Agir | **TOTOR Veille** | L'intelligence, pas le chiffre. |
| Chat avec TOTOR | Comprendre + Agir | **Gratuit** jusqu'au quota, **Veille** au delà | Le compagnon. |

### Auto-entrepreneur

| Écran / feature | Niveau | Gratuit / Veille | Note |
|---|---|---|---|
| Encaissements / dépenses | Observer | **Gratuit** | Ses données. |
| La Paie (salaire lissé) | Comprendre | **Gratuit** | Habitude, le rituel du 1er. |
| « Pourquoi ce salaire » + optimisation + lettre annuelle | Agir | **TOTOR Veille** | On fait payer l'explication et l'optimisation, pas le salaire. |
| Mode Achat | Comprendre | **Gratuit** | |
| Créer une facture / un devis | Agir (générer) | **Gratuit** (le 1er geste) | Voir catalogue : le 1er « je m'en occupe » est offert. |
| Relances auto (facture impayée) | **Agir + Veiller** | **TOTOR Veille** | Pépite premium AE : se rembourse seul. |
| Radar acompte (client mauvais payeur) | Comprendre | **Gratuit** | Montre le risque. |
| Déclaration URSSAF + simulateur (base) | Comprendre | **Gratuit** | |
| Échéances : rappel simple | Agir | **Gratuit** (accompagnement) | Voir tension ci-dessous. |
| Surveillance active (anomalie, optimisation) | Veiller | **TOTOR Veille** | |

### Commun

| Écran / feature | Niveau | Gratuit / Veille | Note |
|---|---|---|---|
| Connexion bancaire (Enable Banking) | Observer | **Gratuit** | On ne fait jamais payer la donnée. |
| Coffre R2 (documents, NIR, RGPD) | Observer | **Gratuit** | |
| Carnet d'Hector | Comprendre | **Gratuit** | |
| Aide vivante | (support) | **Gratuit, hors quota** | |

---

## 2. Catalogue « Je m'en occupe / je veille »

Le bouton s'affiche au moment de la douleur. Première personne : **« Je m'en
occupe. »** Chaque promesse doit être branchée sur ce que TOTOR fait vraiment.

| Déclencheur | Gratuit montre (Observe / Comprend) | TOTOR Veille agit | TOTOR peut vraiment ? |
|---|---|---|---|
| Facture en retard | « Ta facture #12 a 14 j de retard » | Je m'en occupe : relance envoyée | Oui (existe) |
| Facture, suivi | | Je veille jusqu'à ce qu'elle soit payée | À construire (suivi d'état) |
| Offre de cachet | « Si tu l'acceptes, tu passes à 519h » | Je te préviens au bon moment, sans que tu ouvres l'app | À construire (proactif) |
| AEM manquante | « Il te manque sûrement l'AEM de X du 12/06 » | Je relance ton employeur pour toi (modèle) | Partiel : « avec toi », jamais « c'est réglé » |
| Écart de conformité | (teaser) « quelque chose cloche » | J'ai repéré un écart heures vs FT, je te montre où | Oui (conformité), proactif à construire |
| Échéance URSSAF | « Ta déclaration de juin est à faire avant le 31 » + montant prêt | Je te préviens et je garde le chiffre prêt | Oui (rappels existent) |
| Ton salaire (AE) | « Tu peux te verser ~1 840 € » | Je surveille ta tréso et t'alerte si ça te met en danger le mois prochain | À construire |

**Mécanique de conversion** : le **premier** « Je m'en occupe » est **gratuit**
(le déclic magique, une fois). La **veille en continu** est TOTOR Veille.
« Je l'ai relancée pour toi. Tu veux que je veille jusqu'à ce qu'elle soit payée ?
-> TOTOR Veille. »

---

## 3. Ce qui reste à CONSTRUIRE pour tenir la promesse « Veiller »

« Veiller » est une promesse ET une dette technique. On vend la profondeur de la
veille : si elle est mince, on casse la promesse.

**Déjà en place :**
- Relances auto de factures (armé, envoi réel vérifié).
- Emails de rappel (actualisation le 28, URSSAF J-10) : armés en prod.

**À construire (le vrai chantier premium) :**
1. **Moteur de surveillance de fond** générique : des tâches planifiées qui
   re-vérifient l'état d'un utilisateur (heures, tréso, factures, échéances) même
   app fermée.
2. **Notifications proactives au bon moment** : « ce contrat te ferait franchir
   tes 507h », « attention plafond », déclenchées par la donnée, pas par un timer
   aveugle. (Respecter la Loi VIII : ne parler que quand la donnée le prouve.)
3. **Suivi d'état « jusqu'à résolution »** : marquer une facture / une AEM comme
   « sous surveillance » et la suivre jusqu'à paiement / arrivée, avec relance
   automatique tant que non résolu.
4. **Détection d'anomalie proactive** : faire tourner la conformité en tâche de
   fond et alerter l'utilisateur au lieu d'attendre qu'il ouvre l'écran.

---

## 4. Deux tensions à trancher (décision de Camille)

1. **Les rappels proactifs sont aujourd'hui gratuits pour tout le monde**
   (actualisation 28, URSSAF J-10). Or « prévenir spontanément » est du
   « Veiller » = premium. À trancher : le rappel simple reste-t-il gratuit
   (accompagnement) et seule la **surveillance active** (anomalie, impact d'un
   contrat, optimisation) passe TOTOR Veille ? Recommandation : oui, garder le
   rappel simple gratuit (c'est du lien), vendre l'intelligence par dessus.

2. **Facturation** : créer une facture reste gratuit (le 1er geste, le déclic).
   L'**envoi illimité + les relances + la veille de paiement** = TOTOR Veille.
   À confirmer.

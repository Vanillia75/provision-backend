# Salon V2 — décisions actées

## ✅ LIVRÉ EN PROD (1er juillet 2026)

Le Salon V2 (cockpit AE) est fusionné et en production. Fait en 4 PR petites, réversibles, chacune vérifiée en prod connecté :

- **PR1** (`e115bbc`) — `renderHeroHector()` : fonction lazy + gardée dans `estimateData &&`. Corrige le crash `Cannot read properties of null (periode_courante)`.
- **PR2** (`6e02ee2`) — Fusion « un seul Hector, un seul salut ». Fin du télescopage des deux présences. Ceinture anti-crash `estimateData?.` intégrée.
- **PR3** (`18e4ae6`) — Accueil sans doublon de champ solde (le repli Réserve renvoie vers la carte d'en-tête).
- **PR4** (`cda9124`) — Train de vie branché sur l'invitation d'accueil (gate « une demande à la fois » : solde d'abord, train de vie ensuite).

Résultat : l'accueil est auto-suffisant (solde → train de vie → Hector veille), un seul Hector, sur une base qui ne peut plus crasher (double protection : fonction gardée + optional chaining).

**Leçon gravée** : une PR n'est jamais « validée » sur le seul build — toujours vérifier en prod connecté. Byte-identique ≠ comportement-identique quand un garde change de contexte.

**Reste à faire (V2)** : PR éventuelle sur le mobile (fraîcheur solde), puis le Salon intermittent (même grammaire), puis les autres écrans.

> Le **Salon** est l'écran-étalon de la V2. Une fois qu'il sonne juste, il donne le ton à tous les autres.

## Décision fondatrice : une seule présence, deux maisons

Unifier, pas uniformiser. L'AE demande « qu'est-ce que je peux dépenser ? », l'intermittent « mes droits sont-ils en sécurité ? » — deux angoisses. Donc deux Salons, mais une seule grammaire d'expérience (Hector accueille → chiffre qui compte → ce que je garde → conseil → prochaine action → projection → conversation). Pas de héros qui change chaque jour (le cockpit crée une habitude).

## Le héros (AE)

Héros = `disponibleAujourdhui` (1170), label « Aujourd'hui, tu peux dépenser ». Phrase : « J'ai déjà protégé ton URSSAF (875 € mis de côté). Il reste juste ta réserve de sécurité à préserver. »

Dépliable « Comment j'arrive à ce montant ? » :

```
  Disponible après charges et fiscalité : 4 170 €
− Réserve de sécurité :                   3 000 €
  Tu peux dépenser aujourd'hui :          1 170 €
```

⚠️ Le 4170 est DÉJÀ net d'URSSAF/impôts/CFE/frais — ne JAMAIS re-soustraire l'URSSAF (double comptage). L'URSSAF est expliquée à côté. Le vrai gagne toujours.

## Checklist → phrase d'Hector

4/4 → « 🐾 J'ai tout ce qu'il me faut pour veiller sur toi. » Cas incomplet : invitation douce, jamais de coches scolaires ni de rouge.

## 🎯 Découverte majeure (test prod) : UN SEUL Hector

En remontant la carte image Hector en premier regard, elle se télescope avec le briefing : deux Hector, deux humeurs contradictoires (« EN ATTENTE, j'ai besoin de... » inquiet, puis « Bonsoir, tu peux dépenser 1170 » serein). Ça viole l'Art. 4 (« rassure avant d'informer ») : l'écran demandait avant d'accueillir.

Le prototype a tranché :
- ✅ **Validé** : Hector doit être le premier regard.
- ❌ **Invalidé** : deux incarnations d'Hector sur un écran ne marchent pas.

Décision de direction : **un seul H€CTOR, une seule conversation, un seul fil narratif du haut jusqu'en bas.** On supprime le concept de « carte image Hector » comme composant indépendant. Une présence raconte tout l'écran, ce n'est pas un bloc. Un seul bloc d'accueil :

```
🐶 Bonsoir Camille
   Tu peux dépenser aujourd'hui
   1 170 €
   J'ai déjà mis de côté ton URSSAF et ta réserve.
   Tu peux utiliser cette somme sereinement.

── et SEULEMENT en dessous, si une info manque (jamais avant l'accueil) ──

🐾 Pour aller encore plus loin
   Quand tu veux, dis-moi combien tu dépenses par mois pour vivre
   — je pourrai te dire combien de temps tu peux tenir sans revenu.
   [ Renseigner mon train de vie ]
```

Hector rassure d'abord, demande ensuite. Jamais l'inverse.

## Quick fix déjà en prod (commit beaaba0)

Badge « EN ATTENTE » orange → « 🐾 Pour aller encore plus loin » bleu doux, casse normale + texte invitant. Calme la contradiction mais ne la résout pas — la vraie réponse reste la FUSION image↔briefing (prochain chantier, à froid).

## À garder tel quel

Le ton (« tête de chien »), l'accueil « Bonsoir », la projection « Hector regarde ton mois prochain », « Hector veille, tu peux souffler ».

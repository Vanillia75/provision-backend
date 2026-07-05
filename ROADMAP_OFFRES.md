# ROADMAP_OFFRES.md — Module « Offres spectacle »

> Statut : décision produit actée. Le module existe, il est en prod, les retours testeurs sont bons.
> Ce document ne débat plus du « si ». Il cadre le « comment le rendre 10/10 sans casser l'esprit H€CTOR ».

---

## Principe directeur

H€CTOR ne devient jamais un job board. Il reste un compagnon qui veille :
le cockpit dit « il te manque des heures », le module montre des pistes pour en trouver.
La boucle complète — comprendre → trouver → saisir → projeter — est la signature de H€CTOR.
Vécue par l'utilisateur : « il me manque 63 h → je comprends pourquoi → je trouve une piste → je l'enregistre → je vois immédiatement l'impact. »
Le « comprendre » est ce qui différencie H€CTOR de n'importe quel autre produit.
Tout ce qui renforce cette boucle est prioritaire. Tout ce qui n'y contribue pas directement est écarté.

## Garde-fous permanents (non négociables, à chaque version)

- Jamais promettre un emploi, un revenu, ou que des heures compteront pour le renouvellement.
- La mention « Vérifie toujours que le contrat est bien éligible à ton annexe » reste visible.
- Jamais de données inventées (chiffres, fraîcheur, volumes) : tout affichage vient de données réelles France Travail ou de l'état réel du compte.
- Attribution « Source : France Travail » sur chaque offre. Jamais de dénigrement de France Travail.
- Le mot « IA » n'existe pas dans le produit.
- Le module n'importe jamais le moteur 507h. Lecture seule des données d'affichage si besoin (V4).
- Vigilance continue sur la qualité des offres selon les villes (pollution ROME côté FT documentée : filtre de contenu serveur actif). Tout nouveau signalement de pollution prime sur la roadmap.
- Rien d'anxiogène : Hector se tait plutôt que d'annoncer du vide. Présence, pas pression.

---

## V2.1 — Confort (petit lot, rapide)

| Feature | Ce que ça fait | Priorité |
|---|---|---|
| Tri par fraîcheur + âge affiché | Offres récentes d'abord, « publiée il y a X jours » (donnée FT réelle) | Must |
| Zéro-résultat accompagné | Ville vide → Hector propose : élargir le rayon, métiers voisins. Jamais d'écran mort | Must |
| Badge « Nouveau » | Marque ce qui est apparu depuis la dernière visite (timestamp local) | Should |

**Critère de passage à V2.2 :** V2.1 en prod + aucun signalement de pollution d'offres en cours.

## V2.2 — La boucle (le cœur)

| Feature | Ce que ça fait | Priorité |
|---|---|---|
| « Ajouter comme contrat prévu » | Depuis une offre décrochée : pré-remplit la saisie d'activité (employeur, dates si disponibles) → badge « À venir » → projection mise à jour. Trouver → saisir → projeter en trois gestes | Must |
| Multi-zones mémorisées | 2-3 zones de recherche enregistrées (domicile, tournée, festival), bascule en un tap | Should |
| Widget cockpit | Encart discret au cockpit : « X offres près de chez toi → » (X = donnée réelle du cache) | Should |

**Note sur « Ajouter comme contrat prévu » :** ce n'est pas une simple fonctionnalité — c'est le moment où H€CTOR cesse d'être un observateur pour devenir un compagnon. Tu trouves une offre, tu décroches le contrat, tu appuies sur un bouton : H€CTOR sait déjà où tu vas. Cette continuité est très difficile à copier ; elle mérite un soin d'exécution à la hauteur (pré-remplissage impeccable, zéro friction). Le pré-remplissage n'affirme rien pour autant — l'utilisateur reste seul maître de ce qu'il enregistre. Hector pré-remplit, il ne déclare pas.

**Critère de passage à V3 :** la boucle utilisée en réel par au moins un testeur (retour qualitatif).

## V3 — La veille

| Feature | Ce que ça fait | Priorité |
|---|---|---|
| Favoris surveillés | Sauvegarder une offre ; Hector signale si elle expire ou disparaît | Must (de ce palier) |
| Digest hebdo opt-in | Email hebdomadaire « X nouvelles offres près de chez toi ». Règle d'or : zéro offre = Hector se tait. Opt-in, désabonnement en un clic | Should |
| Statut personnel Vu / Postulé | Mémoire discrète de « où j'en suis » par offre. Pas de relance, pas de culpabilisation | Should |

**Critère de lancement du digest :** volume d'offres suffisant et stable sur les villes des testeurs (sinon le digest est morte-née et anxiogène).

## V4 — Horizon

| Feature | Ce que ça fait | Priorité |
|---|---|---|
| Recherche informée par la date anniversaire | Met en avant les offres dont les dates tombent avant la date anniversaire de l'utilisateur. Lecture seule de l'affichage du compteur, aucune promesse de comptabilisation, mention annexe maintenue | Could |

**Prérequis :** fiabilité du parsing des dates dans les offres FT (souvent floues) à valider avant tout développement.

---

## Écarté volontairement (et pourquoi)

- **Notifications push** : anxiogènes par nature dans ce contexte.
- **Score de matching (« 92 % compatible »)** : promesse déguisée.
- **Génération de candidatures / lettres** : job board + hors philosophie.
- **Candidature dans l'app** : la candidature se fait sur le site source (API en lecture ; France Travail reste le lieu officiel).
- **Partage social** : gadget.

## Règle de cadence

Chaque palier attend les retours du précédent. Un signalement de pollution d'offres ou un bug sur un parcours critique suspend la roadmap le temps du fix. Une feature par PR, cadrage → diff → GO écrit → push.

## Règle d'or

Avant toute nouvelle fonctionnalité, poser une seule question :

**Est-ce qu'elle aide réellement un intermittent à vivre plus sereinement son intermittence ?**

Si la réponse n'est pas clairement oui, elle n'a probablement pas sa place dans H€CTOR.

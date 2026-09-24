# PRICING.md — TOTOR

> Grille figée le 10 juillet 2026, **BAISSÉE le 24 septembre 2026** (relance : l'interface
> se simplifie, le côté auto-entrepreneur se retire, et les prix descendent).
> Référence unique pour la configuration Stripe et pour les deux stores.
> Objectif : ne plus re-débattre, et donner à Claude Code une source claire.

## ⬇️ La baisse du 24/09/2026 en un coup d'œil

| | Avant | Maintenant |
|---|---|---|
| Mensuel | 9,99 € | **4,99 €** |
| Annuel | 79 € | **34,99 €** |
| Pionnier (à vie) | 44,99 € | **24,99 €** |
| Tarif solidaire | 4,99 €/mois sur demande | **supprimé** : le prix public l'a rejoint |

**Tout le monde descend**, y compris les abonnés déjà là : personne ne paie plus cher
qu'un nouveau. Les codes communauté (AAM) sont à −50 % à vie, donc **2,50 €/mois**.

⚠️ **Un prix Stripe ne se modifie jamais** : une baisse crée de NOUVEAUX identifiants de
prix. D'où `STRIPE_PRICE_PIONNIER_ANCIENS` côté serveur, qui garde les anciens prix
Pionnier dans le compteur des 100 places (sinon elles repartiraient à zéro).

⚠️ **Les apps iPhone et Android embarquent une copie figée du site.** Depuis le 24/09,
les montants affichés viennent du serveur (`/billing/offres`), pour qu'une prochaine
baisse ne demande plus de republier sur les stores. Les versions installées AVANT
continuent d'afficher les anciens montants jusqu'à leur mise à jour ; c'est bien le
nouveau prix qui est facturé.

---

## Principe directeur

TOTOR n'est pas un compteur ni un logiciel de compta. C'est un **compagnon**. On ne vend pas une fonctionnalité, on vend la tranquillité : comprendre → trouver → saisir → projeter. Le prix se justifie par la valeur, pas par la comparaison avec les concurrents.

**Règle de présentation :** la valeur est montrée AVANT le prix, partout. Sur la page d'abonnement, on liste d'abord ce que TOTOR fait pour l'utilisateur, le prix arrive quand le cerveau a déjà dit « ça vaut le coup ». Jamais le chiffre en premier.

---

## Les trois formules

| Formule | Prix | Pour qui |
|---|---|---|
| **Pionnier (early adopter)** | **24,99 € / an — À VIE** | Les **100 premiers** abonnés payants uniquement |
| **Mensuel** | **4,99 € / mois** | Ceux qui veulent tester sans s'engager |
| **Annuel** | **34,99 € / an** (≈ 5 mois offerts vs le mensuel) | Ceux qui sont convaincus |

*Logique de l'annuel : 4,99 × 12 = 59,88 €. À 34,99 €, on offre ~42 % de réduction, soit près de 5 mois gratuits, et 2,92 € par mois. Bénéfice double : rétention (l'engagement annuel réduit le risque de résiliation mensuelle) ET trésorerie (34,99 € encaissés d'un coup).*

*Ce que ça laisse vraiment : sur les stores, Apple et Google retirent d'abord la TVA de 20 %, puis leur commission de 15 %. Un 4,99 € TTC laisse donc **3,53 € net** (formule : prix TTC ÷ 1,20 × 0,85), au-dessus du coût d'IA moyen d'un abonné. Sur le site, via Stripe, il n'y a pas de commission de magasin.*

---

## L'offre Pionnier — règles strictes

- **24,99 €/an, verrouillé à VIE** pour les 100 premiers payants. Tant qu'ils restent abonnés, ils gardent ce tarif, même quand le prix public montera. Et si le prix public BAISSE, on les fait descendre avec : un Pionnier doit toujours avoir le meilleur prix, sinon la promesse ne vaut rien (décision du 24/09/2026).
- **Limité aux 100 premiers réels.** Le compteur « places restantes » doit refléter le **nombre réel d'abonnés payants**, jamais un faux compteur marketing (Loi X appliquée au pricing : pas de fausse rareté).
- La rareté est l'argument : « plus que X places au tarif Pionnier » crée l'action. Mais X doit être vrai.
- Les testeuses/testeurs de la bêta (Delphine, Amélie, Héloïse…) reçoivent ce tarif ou mieux — reconnaissance de leur contribution (voir note testeurs).

## Le free (freemium)

- ✅ **Les quotas free sont DÉJÀ aux vraies valeurs** (vérifié le 10/07/2026, code + Railway) : **2 scans AEM/mois, 3 chats/mois, 3 scans docs/mois**. Aucune variable de test à 9999 n'existe en prod (les défauts du code s'appliquent). Rien à corriger avant lancement.
- Le coût IA réel constaté en bêta est ~0,66 $/mois pour toute l'activité → un seul abonné finance des dizaines de free. Le freemium tient très large.
- L'Aide vivante (support produit) reste hors quota.

---

## Garde-fous

- **Loi X pricing** : aucun faux compteur, aucune fausse promesse de rareté, aucun « prix barré » fictif.
- **Marge** : coût d'infra dérisoire (Railway + Vercel + ~0,66 $/mois d'IA en bêta) → même le tarif Pionnier à 24,99 €/an reste margé.
- **Réversibilité** : les quotas free et les prix publics (hors Pionnier verrouillé) peuvent être ajustés à tout moment selon les chiffres réels post-lancement.
- **Le tarif Pionnier, lui, est un engagement** : une fois donné à un abonné, il est à vie. C'est le seul prix non réversible — d'où la limite stricte à 100.

## À décider / confirmer au moment de la config Stripe (mi-août)

- ✅ Prix annuel : **34,99 €** (79 € du 10/07/2026 au 24/09/2026).
- ✅ Valeurs des quotas free : **2 / 3 / 3** (déjà en place).
- Mécanisme du compteur « places Pionnier restantes » (doit lire le nombre réel de payants).
- Faut-il une période d'essai gratuite en plus du free, ou le free tient lieu d'essai ?
- Créer les 3 prix Stripe (les prix LIVE actuels ne correspondent plus à cette grille).

## Note testeurs

Les testeuses de la bêta ont contribué de façon décisive (Delphine notamment : documents réels qui ont validé le moteur, features designées). Reconnaissance à prévoir au lancement : tarif Pionnier garanti, ou abonnement offert pour les contributeurs majeurs. À trancher au lancement.

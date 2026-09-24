"""Liste les codes promo et le coupon Stripe sur lequel chacun pointe.

LECTURE SEULE. N'affiche ni secret, ni donnée personnelle : seulement les codes,
leur type, leur coupon, et leurs compteurs d'utilisation.

Pourquoi cet outil (24/09/2026) : lors de la baisse des prix, il a fallu vérifier
qu'aucun code ne pointait encore sur un vieux coupon en EUROS (« 34,01 € de
moins »), qui sur un annuel à 34,99 € aurait fait tomber le prix à 98 centimes.
Un coupon en POURCENTAGE suit les baisses tout seul, un coupon en euros non.

Se lance depuis le conteneur Railway, où vit la base :
    railway ssh --service provision-backend python outils_codes_promo.py
"""
import os

import stripe
from sqlalchemy import text

# On passe par la connexion de l'application : elle sait déjà quel pilote utiliser
# (psycopg v3, et non psycopg2 qui n'est pas installé) et quelle base viser.
from database import SessionLocal

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "").strip()

LIGNES = text("""
    select code, kind, stripe_coupon_id, active, times_used, max_uses
    from promo_codes
    order by active desc, code
""")

db = SessionLocal()
try:
    codes = list(db.execute(LIGNES))
finally:
    db.close()

print(f"{len(codes)} code(s) promo.\n")
coupons = {}
for code, kind, coupon_id, actif, utilise, maxi in codes:
    detail = ""
    if coupon_id:
        if coupon_id not in coupons:
            try:
                c = stripe.Coupon.retrieve(coupon_id)
                coupons[coupon_id] = (f"{c.percent_off} %" if c.percent_off
                                      else f"{(c.amount_off or 0) / 100:.2f} EUR") + f", {c.duration}"
            except Exception:
                coupons[coupon_id] = "⚠️ COUPON INTROUVABLE CHEZ STRIPE"
        detail = f" -> {coupon_id} ({coupons[coupon_id]})"
    etat = "actif " if actif else "coupé "
    print(f"  {code:<14} {etat} {kind or '?':<11} {utilise or 0}/{maxi or '∞'}{detail}")

en_euros = [c for c, v in coupons.items() if "EUR" in v]
if en_euros:
    print("\n⚠️ Coupons en EUROS (ils ne suivent pas une baisse de prix, à revoir "
          "à chaque changement de tarif) : " + ", ".join(en_euros))

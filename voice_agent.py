# -*- coding: utf-8 -*-
"""
voice_agent.py — Outils de l'assistant vocal TOTOR (Vapi), Phase 1.

Trois fonctions, appelées par Vapi via l'endpoint /vapi/tools :

  - chercher_guide(question)   : réponse GROUNDED sur les VRAIS guides TOTOR.
        Loi X : la voix ne restitue QUE ce qui est écrit dans les guides. Si
        l'info n'y est pas, elle renvoie un signal d'escalade (jamais d'invention).
  - escalader_humain(...)      : demande de rappel -> email à l'équipe.
  - programmer_rappel(...)     : rappel à un créneau -> email à l'équipe.

Aucune donnée personnelle du compte n'est manipulée ici (l'assistant vocal ne
touche pas aux montants perso : il renvoie vers l'app pour ça).
"""
import os
import re
import time
import html as _html

import requests

from emailing import send_email

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
SUPPORT_EMAIL = os.environ.get("SUPPORT_EMAIL", "vanilliabusiness@gmail.com")
MODELE = "claude-sonnet-4-6"

GUIDE_BASE = "https://www.montotor.fr/guides/"
# Slugs des 18 guides publics (cf. sitemap). Servent de base de connaissances.
GUIDES = [
    "507-heures-intermittent", "actualisation-france-travail-intermittent",
    "attestation-employeur-mensuelle-aem-intermittent", "date-anniversaire-intermittent",
    "cachet-heures-intermittent", "allocation-journaliere-intermittent-are",
    "conges-spectacles-intermittent", "inscription-france-travail-intermittent",
    "auto-entrepreneur", "acre-auto-entrepreneur", "bic-bnc-choisir-activite-auto-entrepreneur",
    "cfe-auto-entrepreneur", "declaration-revenus-auto-entrepreneur-impot",
    "creer-son-auto-entreprise-etapes", "declarer-chiffre-affaires-urssaf-auto-entrepreneur",
    "versement-liberatoire-auto-entrepreneur", "seuil-tva-micro-entreprise",
]

_CACHE = {"t": 0.0, "textes": {}}      # slug -> texte nettoyé
_CACHE_TTL = 24 * 3600                  # 1 jour : les guides bougent rarement


def _html_en_texte(h: str) -> str:
    """Extrait le texte lisible d'une page guide (retire script/style + balises)."""
    h = re.sub(r"(?is)<(script|style|head)[^>]*>.*?</\1>", " ", h)
    h = re.sub(r"(?s)<[^>]+>", " ", h)
    h = _html.unescape(h)
    return re.sub(r"\s+", " ", h).strip()


def _charger_guides() -> dict:
    """Charge (et met en cache) le texte des guides depuis montotor.fr."""
    now = time.time()
    if _CACHE["textes"] and now - _CACHE["t"] < _CACHE_TTL:
        return _CACHE["textes"]
    textes = {}
    for slug in GUIDES:
        try:
            r = requests.get(GUIDE_BASE + slug + ".html", timeout=6)
            if r.status_code == 200:
                textes[slug] = _html_en_texte(r.text)
        except Exception:
            continue
    if textes:                          # on ne remplace le cache que si on a récupéré qqch
        _CACHE.update({"t": now, "textes": textes})
    return _CACHE["textes"]


def _normaliser(s: str) -> str:
    s = (s or "").lower()
    for a, b in (("é", "e"), ("è", "e"), ("ê", "e"), ("à", "a"), ("â", "a"),
                 ("î", "i"), ("ï", "i"), ("ô", "o"), ("û", "u"), ("ù", "u"), ("ç", "c")):
        s = s.replace(a, b)
    return re.sub(r"[^a-z0-9 ]", " ", s)


_STOP = set("le la les un une des de du et a au aux en pour sur dans que qui quoi est "
            "je tu il elle on nous vous ils mes ma mon ton ta tes se ne pas plus me te "
            "comment combien quand ou est-ce c'est quel quelle avec".split())


def _guides_pertinents(question: str, textes: dict, k: int = 3) -> list:
    """Retourne les k guides les plus pertinents pour la question (score par mots)."""
    mots = [m for m in _normaliser(question).split() if len(m) > 2 and m not in _STOP]
    scores = []
    for slug, txt in textes.items():
        base = _normaliser(slug.replace("-", " ")) + " " + _normaliser(txt[:4000])
        score = sum(base.count(m) for m in mots)
        # bonus fort si le mot est dans le slug (le titre du guide)
        score += 5 * sum(1 for m in mots if m in _normaliser(slug.replace("-", " ")))
        if score:
            scores.append((score, slug, txt))
    scores.sort(reverse=True)
    return scores[:k]


_SYS = (
    "Tu es l'assistante vocale de TOTOR. Tu réponds À VOIX HAUTE (phrases courtes, "
    "ton chaleureux, tutoiement, zéro jargon). RÈGLE ABSOLUE : tu ne dis QUE ce qui "
    "est écrit dans les extraits de guides fournis. Tu n'inventes RIEN, tu ne donnes "
    "AUCUN chiffre qui n'y figure pas, aucun conseil fiscal/juridique personnalisé. "
    "Pour un montant qui dépend de la situation de la personne, renvoie vers l'app "
    "TOTOR (« regarde dans ton appli, elle te le calcule »). Si la réponse n'est PAS "
    "dans les extraits, réponds EXACTEMENT le mot : ESCALADE"
)


def chercher_guide(question: str) -> str:
    """Réponse vérifiée à partir des guides TOTOR, ou signal d'escalade."""
    question = (question or "").strip()
    if not question:
        return "Je n'ai pas bien saisi ta question, tu peux la reformuler ?"
    textes = _charger_guides()
    if not textes or not ANTHROPIC_API_KEY:
        return ("Je préfère te faire rappeler par quelqu'un pour être sûre, "
                "je ne veux pas te donner une info fausse.")
    pertinents = _guides_pertinents(question, textes)
    if not pertinents:
        return ("Je préfère te faire rappeler par quelqu'un pour être sûre, "
                "je ne veux pas te donner une info fausse.")
    extraits = "\n\n".join(f"[GUIDE {slug}]\n{txt[:2500]}" for _s, slug, txt in pertinents)
    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": MODELE, "max_tokens": 300, "system": _SYS,
                  "messages": [{"role": "user",
                                "content": f"EXTRAITS DES GUIDES :\n{extraits}\n\nQUESTION : {question}"}]},
            timeout=25,
        )
        resp.raise_for_status()
        reply = "".join(b.get("text", "") for b in resp.json().get("content", [])).strip()
    except Exception:
        return ("Je préfère te faire rappeler par quelqu'un pour être sûre, "
                "je ne veux pas te donner une info fausse.")
    if not reply or "ESCALADE" in reply.upper():
        return ("Ça, je ne l'ai pas dans mes guides. Le mieux : je te fais rappeler "
                "par quelqu'un pour ne pas te donner une info fausse. Tu veux ?")
    return re.sub(r"\*{1,3}", "", reply).strip()


def _email_demande(titre: str, prenom, telephone, extra: dict) -> None:
    lignes = "".join(
        f"<li><strong>{_html.escape(k)}</strong> : {_html.escape(str(v))}</li>"
        for k, v in extra.items() if v
    )
    corps = f"""
    <div style="font-family:sans-serif;max-width:520px;margin:0 auto;color:#0A2540;">
      <h2>📞 {_html.escape(titre)} (assistant vocal)</h2>
      <ul style="font-size:14px;line-height:1.7;">
        <li><strong>Prénom</strong> : {_html.escape(str(prenom or "(non donné)"))}</li>
        <li><strong>Téléphone</strong> : {_html.escape(str(telephone or "(non donné)"))}</li>
        {lignes}
      </ul>
      <p style="color:#6B7A8D;font-size:12px;">Demande créée par l'assistante vocale TOTOR.</p>
    </div>"""
    send_email(SUPPORT_EMAIL, f"[Vocal] {titre}", corps, from_name="TOTOR Vocal")


def escalader_humain(prenom=None, telephone=None, question=None) -> str:
    """Crée une demande de rappel humain (email à l'équipe)."""
    if not telephone:
        return "Il me faut juste ton numéro pour qu'on te rappelle. Tu me le donnes ?"
    _email_demande("Demande de rappel", prenom, telephone, {"Sa question": question})
    return ("C'est noté, je transmets à un humain qui va te rappeler. "
            "En attendant, tu fais ton métier, on veille. À bientôt !")


def programmer_rappel(prenom=None, telephone=None, creneau=None) -> str:
    """Planifie un rappel à un créneau choisi (email à l'équipe)."""
    if not telephone:
        return "Il me faut ton numéro pour programmer le rappel. Tu me le donnes ?"
    _email_demande("Rappel programmé", prenom, telephone, {"Créneau souhaité": creneau})
    quand = f" {creneau}" if creneau else ""
    return f"C'est programmé, on te rappelle{quand}. À très vite !"

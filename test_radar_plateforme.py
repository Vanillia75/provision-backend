# ════════════════════════════════════════════════════════════════════════
#  Radar de l'Aide vivante : d'où vient la personne qui pose une question.
#
#  Camille, 28/07/2026 : « elle a téléchargé l'application sur App Store ou
#  Android ? ». Le radar n'envoyait que l'écran et la question, par choix de
#  confidentialité. La plateforme, elle, n'est PAS une donnée de compte : on
#  la lit dans l'en-tête User-Agent, donc sans rien changer aux applis déjà
#  installées.
#
#  Limite assumée et testée plus bas : sur iPhone, la WebView de l'appli et
#  Safari envoient pratiquement le même en-tête. On distingue l'iPhone de
#  l'Android à coup sûr ; « appli ou navigateur » n'est fiable que sur
#  Android, grâce au marqueur « wv ».
# ════════════════════════════════════════════════════════════════════════
import pytest

from api import _plateforme_lisible


@pytest.mark.parametrize("user_agent,attendu", [
    # Android : le marqueur « wv » signe la WebView, donc l'application.
    ("Mozilla/5.0 (Linux; Android 14; SM-S911B; wv) AppleWebKit/537.36 Chrome/120 Mobile",
     "Android (application)"),
    ("Mozilla/5.0 (Linux; Android 14; SM-S911B) AppleWebKit/537.36 Chrome/120 Mobile Safari",
     "Android (navigateur)"),
    # iPhone et iPad : reconnus, sans prétendre distinguer l'appli de Safari.
    ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15",
     "iPhone"),
    ("Mozilla/5.0 (iPad; CPU OS 17_5 like Mac OS X) AppleWebKit/605.1.15",
     "iPad"),
    # Ordinateurs.
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36", "Mac"),
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120",
     "Ordinateur Windows"),
])
def test_plateforme_reconnue(user_agent, attendu):
    assert _plateforme_lisible(user_agent) == attendu


def test_en_tete_absent_ne_plante_pas():
    """Un en-tête vide ou absent doit donner « inconnu », jamais une erreur :
    le radar est best-effort, il ne doit JAMAIS faire échouer une réponse
    d'aide à quelqu'un qui attend."""
    assert _plateforme_lisible("") == "inconnu"
    assert _plateforme_lisible(None) == "inconnu"


def test_iphone_prime_sur_mac_os():
    """Piège : l'en-tête d'un iPhone contient « like Mac OS X ». Sans le bon
    ordre de tests, tous les iPhone seraient comptés comme des Mac."""
    assert _plateforme_lisible(
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15"
    ) == "iPhone"

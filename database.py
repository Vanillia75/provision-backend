import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

DATABASE_URL = os.environ.get("DATABASE_URL")
ALLOW_SQLITE_FALLBACK = os.environ.get("ALLOW_SQLITE_FALLBACK", "false").lower() == "true"

if not DATABASE_URL:
    if ALLOW_SQLITE_FALLBACK:
        # Uniquement pour le developpement local explicite - jamais en production
        DATABASE_URL = "sqlite:///./local.db"
        print("ATTENTION : DATABASE_URL absente, utilisation de SQLite local (dev uniquement).")
    else:
        # En production (Railway), on prefere planter bruyamment plutot que de basculer
        # silencieusement vers une base SQLite vide et ephemere, ce qui ferait croire
        # a tort que les comptes/donnees des utilisateurs ont disparu.
        raise RuntimeError(
            "DATABASE_URL n'est pas definie. L'application refuse de demarrer pour eviter "
            "de basculer silencieusement vers une base de secours vide (ce qui supprimerait "
            "l'acces aux donnees existantes). Verifiez la configuration Postgres sur Railway, "
            "ou definissez ALLOW_SQLITE_FALLBACK=true uniquement pour du developpement local."
        )

# Railway fournit parfois une URL en postgres:// ; SQLAlchemy attend postgresql://
# On force aussi l'utilisation du driver psycopg (v3) plutot que psycopg2,
# pour eviter les problemes de dependance systeme libpq sur certains builders.
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://") and not DATABASE_URL.startswith("postgresql+psycopg://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

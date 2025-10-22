# config.py
# Connexion MySQL SANS .env

import mysql.connector
from contextlib import contextmanager

# >>> Modifie ici tes identifiants si besoin <<<
DB_CONFIG = {
    "host": "127.0.0.1",
    "port": 3306,
    "user": "ubereats",            # ex: l'utilisateur que tu as créé
    "password": "M13012005i", # son mot de passe
    "database": "ubereats",        # ta base
    "autocommit": False,
}

def connect():
    """Retourne une connexion mysql.connector."""
    return mysql.connector.connect(**DB_CONFIG)

@contextmanager
def get_cursor(dictionary: bool = False):
    """
    Contexte pratique :
      with get_cursor(dictionary=True) as (con, cur):
          cur.execute("SELECT ...")
          rows = cur.fetchall()
    Commit auto si OK, rollback sinon.
    """
    con = connect()
    cur = con.cursor(dictionary=dictionary)
    try:
        yield con, cur
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        cur.close()
        con.close()

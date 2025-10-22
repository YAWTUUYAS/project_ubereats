#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
redis_load_from_dir.py (version adaptée aux fichiers JSONL du POC UberEats)
--------------------------------------------------------------------------
Charge :
  - users.jsonl
  - restaurants_menus.jsonl
  - orders.jsonl
dans Redis, et reconstruit les index nécessaires.
"""

import os, json
from collections import defaultdict
from typing import Dict, Any, List
from redis import Redis

# ========= CONFIG =========
REDIS = Redis(host="127.0.0.1", port=6379, decode_responses=True)
INDIR = "./REDIS_POC/out"          # répertoire où se trouvent les fichiers JSONL
FLUSH_FIRST = True       # True = purge avant rechargement

# ---------- helpers ----------
def load_jsonl(path):
    """Lit un fichier JSONL ligne par ligne"""
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)

def flush_prefixes(r: Redis):
    """Supprime toutes les clés du POC"""
    patterns = [
        "order:*",
        "zone:*:annonces",
        "interest:by_order:*", "interest:by_courier:*",
        "courier:*:assigned",
        "user:*", "user:index:*",
        "restaurant:*", "menu:*"
    ]
    for pat in patterns:
        cursor = 0
        while True:
            cursor, keys = r.scan(cursor=cursor, match=pat, count=500)
            if keys:
                r.delete(*keys)
            if cursor == 0:
                break

def k_zone_annonces(zone): return f"zone:{zone}:annonces"
def k_interest_by_order(oid): return f"interest:by_order:{oid}"
def k_interest_by_courier(lid): return f"interest:by_courier:{lid}"
def k_courier_assigned(lid): return f"courier:{lid}:assigned"

def rebuild_indexes_for_order(r: Redis, o: Dict[str, Any]):
    """Reconstruit les index Redis à partir d'une commande"""
    oid = o["id"]
    ts = (o.get("timestamps") or {})
    # annonces par zone
    if o.get("statut") == "ANONCEE" and o.get("zone"):
        score = ts.get("publiee") or ts.get("creation") or 0
        r.zadd(k_zone_annonces(o["zone"]), {oid: score})
    # intérêts
    for liv in (o.get("interets") or {}).keys():
        r.sadd(k_interest_by_order(oid), liv)
        r.sadd(k_interest_by_courier(liv), oid)
    # courses assignées
    if o.get("livreur_assigne") and ts.get("assignee"):
        r.zadd(k_courier_assigned(o["livreur_assigne"]), {oid: ts["assignee"]})

# ---------- main ----------
def main():
    if FLUSH_FIRST:
        print("🧹 Purge des clés existantes…")
        flush_prefixes(REDIS)

    users_path = os.path.join(INDIR, "users.jsonl")
    rest_path  = os.path.join(INDIR, "restaurants_menus.jsonl")
    orders_path = os.path.join(INDIR, "orders.jsonl")

    # ----- USERS -----
    if os.path.exists(users_path):
        print(f"📦 Chargement {users_path} …")
        pipe = REDIS.pipeline()
        role_indexes = defaultdict(dict)

        for row in load_jsonl(users_path):
            if "user" in row:
                # Exemple: {"key":"user:CLIENT:cli_001", "user":{...}}
                pipe.set(row["key"], json.dumps(row["user"], ensure_ascii=False))
            elif "mapping" in row:
                # Exemple: {"key":"user:index:CLIENT", "mapping":{username:id}}
                role = row["key"].split(":")[-1]
                role_indexes[role].update(row["mapping"])

        pipe.execute()

        # Créer les index hmap
        for role, mapping in role_indexes.items():
            if mapping:
                for username, uid in mapping.items():
                    REDIS.hset(f"user:index:{role}", username, uid)

        print("✅ Utilisateurs + index chargés.")
    else:
        print("⚠️ Fichier users.jsonl manquant.")

    # ----- RESTAURANTS + MENUS -----
    if os.path.exists(rest_path):
        print(f"📦 Chargement {rest_path} …")
        pipe = REDIS.pipeline()
        for row in load_jsonl(rest_path):
            rid = row["restaurant"]["id"]
            pipe.set(f"restaurant:{rid}", json.dumps(row["restaurant"], ensure_ascii=False))
            pipe.set(f"menu:{rid}", json.dumps(row.get("menu") or [], ensure_ascii=False))
        pipe.execute()
        print("✅ Restaurants + menus chargés.")
    else:
        print("⚠️ Fichier restaurants_menus.jsonl manquant.")

    # ----- ORDERS -----
    if os.path.exists(orders_path):
        print(f"📦 Chargement {orders_path} …")
        orders = []
        pipe = REDIS.pipeline()
        for row in load_jsonl(orders_path):
            pipe.set(row["key"], json.dumps(row["order"], ensure_ascii=False))
            orders.append(row["order"])
        pipe.execute()
        # Index Redis secondaires
        for o in orders:
            rebuild_indexes_for_order(REDIS, o)
        print("✅ Commandes chargées + index reconstruits.")
    else:
        print("⚠️ Fichier orders.jsonl manquant.")

    print("🎯 Chargement terminé avec succès.")
    print("🔍 Nombre total de clés:", REDIS.dbsize())

if __name__ == "__main__":
    main()

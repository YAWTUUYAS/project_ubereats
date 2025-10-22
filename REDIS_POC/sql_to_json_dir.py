#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sql_to_json_dir.py (version config en dur)
-----------------------------------------
Exporte MySQL -> répertoire contenant :
  - orders.jsonl
  - restaurants_menus.jsonl
  - users.jsonl

Dépendances: mysql-connector-python
"""

import os, json, sys
from decimal import Decimal
from typing import Dict, Any, List, Optional

import mysql.connector
from mysql.connector import Error

# ========= CONFIG MYSQL EN DUR =========
MYSQL = dict(
    host="127.0.0.1",
    port=3306,
    user="ubereats",        # <-- mets ton user MySQL ici
    password="M13012005i",  # <-- mets ton mot de passe MySQL ici
    database="ubereats"
)
OUTDIR = "./out"            # répertoire de sortie

# ---------- helpers ----------
def to_float(x):
    if isinstance(x, Decimal): return float(x)
    return float(x) if x is not None else None

def jwrite(fp, obj):
    fp.write(json.dumps(obj, ensure_ascii=False))
    fp.write("\n")

# ---------- ORDERS (agrégat) ----------
def build_order_doc(cnx, cmd: Dict[str, Any]) -> Dict[str, Any]:
    cur = cnx.cursor(dictionary=True)

    cli = res = ass = None
    if cmd.get("id_client"):
        cur.execute("SELECT id_client, nom FROM client WHERE id_client=%s", (cmd["id_client"],))
        cli = cur.fetchone()
    if cmd.get("id_restaurant"):
        cur.execute("SELECT id_restaurant, nom, zone FROM restaurant WHERE id_restaurant=%s", (cmd["id_restaurant"],))
        res = cur.fetchone()
    if cmd.get("id_livreur_assigne"):
        cur.execute("SELECT id_livreur, nom FROM livreur WHERE id_livreur=%s", (cmd["id_livreur_assigne"],))
        ass = cur.fetchone()

    # lignes (si tables présentes)
    lignes = []
    try:
        cur.execute("""
            SELECT l.id_plat, l.quantite, l.prix_unitaire, p.nom
            FROM commande_ligne l
            LEFT JOIN plat p ON p.id_plat = l.id_plat
            WHERE l.id_commande=%s
        """, (cmd["id_commande"],))
        for row in cur.fetchall():
            qty = int(row["quantite"])
            pu  = to_float(row["prix_unitaire"]) or 0.0
            lignes.append({
                "id_plat": row["id_plat"],
                "nom": row.get("nom"),
                "qty": qty,
                "pu": pu,
                "total": round(qty * pu, 2)
            })
    except mysql.connector.Error:
        pass

    montant_total_client = to_float(cmd.get("montant_total_client"))
    if (montant_total_client is None) and lignes:
        montant_total_client = round(sum(l["total"] for l in lignes), 2)

    # intérêts
    interets = {}
    try:
        cur.execute("""
            SELECT id_livreur, temps_estime, commentaire, ts
            FROM interet
            WHERE id_commande=%s
        """, (cmd["id_commande"],))
        for i in cur.fetchall():
            interets[i["id_livreur"]] = {
                "eta": i["temps_estime"],
                "comment": i["commentaire"],
                "ts": int(i["ts"])
            }
    except mysql.connector.Error:
        pass

    # events (optionnel)
    events = []
    try:
        cur.execute("""
            SELECT type, acteur_role, acteur_id, details, ts
            FROM commande_evenement
            WHERE id_commande=%s
            ORDER BY ts ASC, id ASC
        """, (cmd["id_commande"],))
        for e in cur.fetchall():
            events.append({
                "type": e["type"],
                "acteur_role": e["acteur_role"],
                "acteur_id": e.get("acteur_id"),
                "details": e.get("details"),
                "ts": int(e["ts"]) if e.get("ts") else None
            })
    except mysql.connector.Error:
        pass

    cur.close()

    oid = cmd["id_commande"]
    return {
        "key": f"order:{oid}",
        "order": {
            "id": oid,
            "version": 1,
            "statut": cmd["statut"],
            "timestamps": {
                "creation": int(cmd["date_creation"]) if cmd.get("date_creation") else None,
                "publiee": int(cmd["date_publiee"]) if cmd.get("date_publiee") else None,
                "assignee": int(cmd["date_assignee"]) if cmd.get("date_assignee") else None,
                "cloture": int(cmd["date_cloture"]) if cmd.get("date_cloture") else None
            },
            "zone": cmd.get("zone"),
            "livraison": {
                "adresse": cmd.get("livraison_adresse"),
                "lat": to_float(cmd.get("livraison_lat")),
                "lon": to_float(cmd.get("livraison_lon"))
            },
            "client": {"id": cli["id_client"], "nom": cli["nom"]} if cli else {},
            "restaurant": {"id": res["id_restaurant"], "nom": res["nom"], "zone": res["zone"]} if res else {},
            "livreur_assigne": cmd.get("id_livreur_assigne"),
            "livreur_assigne_nom": ass["nom"] if ass else None,
            "livree_par": cmd.get("livree_par_livreur"),

            "remuneration": to_float(cmd.get("remuneration")) or 0.0,
            "montant_total_client": montant_total_client,
            "lignes": lignes,

            "annule_par": cmd.get("annule_par"),
            "motif_annulation": cmd.get("motif_annulation"),
            "interets": interets,
            "events": events
        }
    }

def export_orders(cnx, path):
    cur = cnx.cursor(dictionary=True)
    cur.execute("SELECT * FROM commande ORDER BY date_creation ASC, id_commande")
    rows = cur.fetchall()
    cur.close()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for cmd in rows:
            jwrite(f, build_order_doc(cnx, cmd))

# ---------- RESTAURANTS + MENUS ----------
def fetch_menu_for_restaurant(cur, rest_id: str) -> List[Dict[str, Any]]:
    # cas 1 : plat(id_plat, id_restaurant, nom, prix)
    try:
        cur.execute("""
            SELECT id_plat, nom, prix
            FROM plat
            WHERE id_restaurant=%s
        """, (rest_id,))
        rows = cur.fetchall()
        if rows:
            return [{"id_plat": r["id_plat"], "nom": r["nom"], "pu": to_float(r.get("prix")) or 0.0} for r in rows]
    except mysql.connector.Error:
        pass
    # cas 2 : menu_plat + plat
    try:
        cur.execute("""
            SELECT mp.id_plat, p.nom, mp.prix
            FROM menu_plat mp
            JOIN plat p ON p.id_plat = mp.id_plat
            WHERE mp.id_restaurant=%s
        """, (rest_id,))
        rows = cur.fetchall()
        if rows:
            return [{"id_plat": r["id_plat"], "nom": r["nom"], "pu": to_float(r.get("prix")) or 0.0} for r in rows]
    except mysql.connector.Error:
        pass
    return []

def export_restaurants_menus(cnx, path):
    cur = cnx.cursor(dictionary=True)
    cur.execute("SELECT id_restaurant, nom, adresse, telephone, zone FROM restaurant ORDER BY id_restaurant")
    restos = cur.fetchall()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in restos:
            menu = fetch_menu_for_restaurant(cur, r["id_restaurant"])
            jwrite(f, {
                "key": f"restaurant:{r['id_restaurant']}",
                "restaurant": {
                    "id": r["id_restaurant"],
                    "nom": r["nom"],
                    "adresse": r["adresse"],
                    "telephone": r["telephone"],
                    "zone": r["zone"]
                },
                "menu": menu
            })
    cur.close()

# ---------- USERS ----------
def export_users(cnx, path):
    cur = cnx.cursor(dictionary=True)
    cur.execute("SELECT id_client, username, password FROM client")
    clients = cur.fetchall()
    cur.execute("SELECT id_restaurant, username, password FROM restaurant")
    restos = cur.fetchall()
    cur.execute("SELECT id_livreur, username, password FROM livreur")
    livreurs = cur.fetchall()
    # managers optionnels
    managers = []
    try:
        cur.execute("SELECT id_manager, username, password FROM manager")
        managers = cur.fetchall()
    except mysql.connector.Error:
        pass
    cur.close()

    idx_client, idx_resto, idx_livreur, idx_manager = {}, {}, {}, {}

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for c in clients:
            jwrite(f, {"key": f"user:CLIENT:{c['id_client']}",
                       "user": {"id": c["id_client"], "role": "CLIENT", "username": c["username"], "password": c["password"]}})
            if c["username"]: idx_client[c["username"]] = c["id_client"]
        for r in restos:
            jwrite(f, {"key": f"user:RESTAURANT:{r['id_restaurant']}",
                       "user": {"id": r["id_restaurant"], "role": "RESTAURANT", "username": r["username"], "password": r["password"]}})
            if r["username"]: idx_resto[r["username"]] = r["id_restaurant"]
        for l in livreurs:
            jwrite(f, {"key": f"user:LIVREUR:{l['id_livreur']}",
                       "user": {"id": l["id_livreur"], "role": "LIVREUR", "username": l["username"], "password": l["password"]}})
            if l["username"]: idx_livreur[l["username"]] = l["id_livreur"]
        for m in managers:
            jwrite(f, {"key": f"user:MANAGER:{m['id_manager']}",
                       "user": {"id": m["id_manager"], "role": "MANAGER", "username": m["username"], "password": m["password"]}})
            if m.get("username"): idx_manager[m["username"]] = m["id_manager"]

        if idx_client:  jwrite(f, {"key": "user:index:CLIENT",     "mapping": idx_client})
        if idx_resto:   jwrite(f, {"key": "user:index:RESTAURANT", "mapping": idx_resto})
        if idx_livreur: jwrite(f, {"key": "user:index:LIVREUR",    "mapping": idx_livreur})
        if idx_manager: jwrite(f, {"key": "user:index:MANAGER",    "mapping": idx_manager})

def main():
    try:
        cnx = mysql.connector.connect(**MYSQL, charset="utf8mb4", collation="utf8mb4_unicode_ci")
    except Error as e:
        print("Erreur connexion MySQL:", e, file=sys.stderr); sys.exit(1)

    try:
        os.makedirs(OUTDIR, exist_ok=True)
        export_orders(cnx, os.path.join(OUTDIR, "orders.jsonl"))
        export_restaurants_menus(cnx, os.path.join(OUTDIR, "restaurants_menus.jsonl"))
        export_users(cnx, os.path.join(OUTDIR, "users.jsonl"))
        print(f"✅ Écrit: {os.path.join(OUTDIR, 'orders.jsonl')}")
        print(f"✅ Écrit: {os.path.join(OUTDIR, 'restaurants_menus.jsonl')}")
        print(f"✅ Écrit: {os.path.join(OUTDIR, 'users.jsonl')}")
    finally:
        try: cnx.close()
        except: pass

if __name__ == "__main__":
    main()

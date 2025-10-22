# mysql_to_redis.py
import json, argparse, time
from decimal import Decimal
import mysql.connector
from redis import Redis

# ---------- CONFIG ----------
MYSQL_CFG = dict(
    host="127.0.0.1",
    port=3306,
    user="ubereats",
    password="M13012005i",        # <--- adapte
    database="ubereats"
)
REDIS_CFG = dict(host="127.0.0.1", port=6379, decode_responses=True)

# ---------- Helpers clés ----------
def k_order(order_id): return f"order:{order_id}"
def k_events(order_id): return f"order:{order_id}:events"
def k_zone_annonces(zone): return f"zone:{zone}:annonces"
def k_interest_by_order(order_id): return f"interest:by_order:{order_id}"
def k_interest_by_courier(livreur): return f"interest:by_courier:{livreur}"
def k_courier_assigned(livreur): return f"courier:{livreur}:assigned"

def to_float(x):
    if isinstance(x, Decimal): return float(x)
    return float(x) if x is not None else None

# ---------- Exporter une commande -> agrégat JSON ----------
def build_order_aggregate(cnx, cmd_row):
    cur = cnx.cursor(dictionary=True)

    # snapshots client & restaurant
    cli = None
    if cmd_row.get("id_client"):
        cur.execute("SELECT id_client, nom FROM client WHERE id_client=%s", (cmd_row["id_client"],))
        cli = cur.fetchone()

    res = None
    if cmd_row.get("id_restaurant"):
        cur.execute("SELECT id_restaurant, nom, zone FROM restaurant WHERE id_restaurant=%s", (cmd_row["id_restaurant"],))
        res = cur.fetchone()

    # lignes
    lignes = []
    try:
        cur.execute("""
            SELECT l.id_plat, l.quantite, l.prix_unitaire, p.nom
            FROM commande_ligne l
            LEFT JOIN plat p ON p.id_plat = l.id_plat
            WHERE l.id_commande=%s
        """, (cmd_row["id_commande"],))
        for row in cur.fetchall():
            qty = int(row["quantite"])
            pu = to_float(row["prix_unitaire"]) or 0.0
            lignes.append({
                "id_plat": row["id_plat"],
                "nom": row.get("nom"),
                "qty": qty,
                "pu": pu,
                "total": round(qty * pu, 2)
            })
    except mysql.connector.Error:
        # pas de table commande_ligne/plat
        pass

    # total client si absent
    montant_total_client = to_float(cmd_row.get("montant_total_client"))
    if montant_total_client is None:
        montant_total_client = round(sum(l["total"] for l in lignes), 2)

    # interets -> map
    interets = {}
    try:
        cur.execute("""
            SELECT id_livreur, temps_estime, commentaire, ts
            FROM interet
            WHERE id_commande=%s
        """, (cmd_row["id_commande"],))
        for i in cur.fetchall():
            interets[i["id_livreur"]] = {
                "eta": i["temps_estime"],
                "comment": i["commentaire"],
                "ts": int(i["ts"])
            }
    except mysql.connector.Error:
        pass

    # construire l'agrégat
    agg = {
        "id": cmd_row["id_commande"],
        "statut": cmd_row["statut"],
        "timestamps": {
            "creation": int(cmd_row["date_creation"]) if cmd_row.get("date_creation") else None,
            "publiee": int(cmd_row["date_publiee"]) if cmd_row.get("date_publiee") else None,
            "assignee": int(cmd_row["date_assignee"]) if cmd_row.get("date_assignee") else None,
            "cloture": int(cmd_row["date_cloture"]) if cmd_row.get("date_cloture") else None
        },
        "zone": cmd_row.get("zone"),
        "livraison": {
            "adresse": cmd_row.get("livraison_adresse"),
            "lat": to_float(cmd_row.get("livraison_lat")),
            "lon": to_float(cmd_row.get("livraison_lon"))
        },
        "client": {"id": cli["id_client"], "nom": cli["nom"]} if cli else {},
        "restaurant": {"id": res["id_restaurant"], "nom": res["nom"], "zone": res["zone"]} if res else {},
        "livreur_assigne": cmd_row.get("id_livreur_assigne"),
        "livree_par": cmd_row.get("livree_par_livreur"),
        "remuneration": to_float(cmd_row.get("remuneration")) or 0.0,
        "montant_total_client": montant_total_client,
        "lignes": lignes,
        "interets": interets
    }
    cur.close()
    return agg

def stream_events_from_sql(r: Redis, cnx, order_id):
    # Rejouer commande_evenement dans Stream
    cur = cnx.cursor(dictionary=True)
    cur.execute("""
        SELECT type, acteur_role, acteur_id, details, ts
        FROM commande_evenement
        WHERE id_commande=%s ORDER BY ts ASC, id ASC
    """, (order_id,))
    for e in cur.fetchall():
        ts = int(e["ts"]) if e.get("ts") else int(time.time())
        # ID explicite basé sur timestamp => ordre temporel
        r.xadd(k_events(order_id),
               {"type": e["type"], "acteur_role": e["acteur_role"], "acteur_id": e.get("acteur_id") or "",
                "details": e.get("details") or "", "ts": str(ts)},
               id=f"{ts}-0")
    cur.close()

def rebuild_indexes_for_order(r: Redis, agg: dict):
    oid = agg["id"]
    # Annonces par zone
    if agg["statut"] == "ANONCEE" and agg.get("zone"):
        score = agg["timestamps"]["publiee"] or agg["timestamps"]["creation"] or int(time.time())
        r.zadd(k_zone_annonces(agg["zone"]), {oid: score})
    # Intérêts
    for liv in (agg.get("interets") or {}).keys():
        r.sadd(k_interest_by_order(oid), liv)
        r.sadd(k_interest_by_courier(liv), oid)
    # Courses assignées (utile pour la vue livreur)
    if agg.get("livreur_assigne") and agg["timestamps"].get("assignee"):
        r.zadd(k_courier_assigned(agg["livreur_assigne"]), {oid: agg["timestamps"]["assignee"]})

def flush_poc_keys(r: Redis):
    # Supprimer uniquement nos préfixes POC
    patterns = ["order:*", "zone:*:annonces", "interest:by_order:*", "interest:by_courier:*", "courier:*:assigned"]
    for pat in patterns:
        cursor = 0
        while True:
            cursor, keys = r.scan(cursor=cursor, match=pat, count=500)
            if keys:
                r.delete(*keys)
            if cursor == 0:
                break

def main():
    ap = argparse.ArgumentParser(description="Export MySQL -> JSONL et chargement Redis (POC dénormalisé).")
    ap.add_argument("--dump-json", default="orders.jsonl", help="Chemin du fichier JSON Lines en sortie")
    ap.add_argument("--no-redis", action="store_true", help="N'écrit pas dans Redis (export JSON uniquement)")
    ap.add_argument("--flush", action="store_true", help="Purge les clés POC dans Redis avant chargement")
    args = ap.parse_args()

    # Connexions
    cnx = mysql.connector.connect(**MYSQL_CFG)
    r = Redis(**REDIS_CFG)

    if not args.no_redis and args.flush:
        print("Purge des clés POC...")
        flush_poc_keys(r)

    cur = cnx.cursor(dictionary=True)
    cur.execute("SELECT * FROM commande ORDER BY date_creation ASC")
    orders = cur.fetchall()
    cur.close()

    out = []
    print(f"Trouvé {len(orders)} commandes. Construction des agrégats...")
    for cmd in orders:
        agg = build_order_aggregate(cnx, cmd)
        out.append(agg)

    # Écrire JSONL
    if args.dump_json:
        with open(args.dump_json, "w", encoding="utf-8") as f:
            for agg in out:
                f.write(json.dumps(agg, ensure_ascii=False) + "\n")
        print(f"✅ Export JSONL: {args.dump_json}")

    # Charger Redis
    if not args.no_redis:
        pipe = r.pipeline()
        for agg in out:
            pipe.set(k_order(agg["id"]), json.dumps(agg, ensure_ascii=False))
        pipe.execute()
        print("✅ Agrégats chargés dans Redis")

        # Index + Events
        for agg in out:
            rebuild_indexes_for_order(r, agg)
            stream_events_from_sql(r, cnx, agg["id"])
        print("✅ Index reconstruits + Events rejoués")

    cnx.close()

if __name__ == "__main__":
    main()

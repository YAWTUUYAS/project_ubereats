#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
POC UberEats — Version SQL
Front partagé : ../frontend/
"""

import os, time, json
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
import mysql.connector
from functools import wraps
from werkzeug.exceptions import abort

# -----------------------------
# Auth decorator
# -----------------------------
def login_required(role=None):
    def deco(fn):
        @wraps(fn)
        def inner(*args, **kwargs):
            u = session.get("user")
            if not u:
                flash("Merci de vous connecter.")
                return redirect(url_for("login"))
            if role and u.get("role") != role:
                abort(403)
            return fn(*args, **kwargs)
        return inner
    return deco

# -----------------------------
# Config
# -----------------------------
BASE_DIR = os.path.dirname(__file__)
FRONTEND_DIR = os.path.join(BASE_DIR, "../frontend")

app = Flask(
    __name__,
    template_folder=os.path.join(FRONTEND_DIR, "templates"),
    static_folder=os.path.join(FRONTEND_DIR, "static")
)
app.secret_key = "change-me-please"

# -----------------------------
# MySQL connection helper
# -----------------------------
def get_cursor():
    conn = mysql.connector.connect(
        host="localhost", user="ubereats", password="M13012005i", database="ubereats"
    )
    return conn, conn.cursor(dictionary=True)

def now():
    return int(time.time())

# -----------------------------
# Auth routes
# -----------------------------
@app.route("/")
def home():
    return render_template("home.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        role = request.form["role"].upper().strip()
        username = request.form["username"].strip()
        password = request.form["password"].strip()

        table_map = {
            "CLIENT": {"table": "client", "id_col": "id_client", "extra_cols": ["nom"]},
            "RESTAURANT": {"table": "restaurant", "id_col": "id_restaurant", "extra_cols": ["nom", "zone"]},
            "LIVREUR": {"table": "livreur", "id_col": "id_livreur", "extra_cols": ["nom", "zone", "vehicule"]},
        }

        if role not in table_map:
            flash("Rôle invalide.")
            return render_template("login.html")

        meta = table_map[role]
        cols = [meta["id_col"], "username", "password"] + meta["extra_cols"]
        col_list = ", ".join(cols)

        conn, cur = get_cursor()
        cur.execute(f"SELECT {col_list} FROM {meta['table']} WHERE username=%s AND password=%s", (username, password))
        u = cur.fetchone()
        conn.close()

        if not u:
            flash("Identifiants invalides.")
            return render_template("login.html")

        session["user"] = {"id": u[meta["id_col"]], "role": role, "username": username}
        for k in meta["extra_cols"]:
            session["user"][k] = u.get(k)

        flash("Connecté")
        if role == "CLIENT":
            return redirect(url_for("client_restaurants"))
        elif role == "RESTAURANT":
            return redirect(url_for("restaurant_dashboard"))
        else:
            return redirect(url_for("livreur_dashboard"))

    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    flash("Déconnecté")
    return redirect(url_for("home"))

# -----------------------------
# CLIENT
# -----------------------------
@app.route("/client/restaurants")
@login_required("CLIENT")
def client_restaurants():
    q = (request.args.get("q") or "").strip()
    conn, cur = get_cursor()
    try:
        if q:
            cur.execute("SELECT * FROM restaurant WHERE nom LIKE %s OR zone LIKE %s", (f"%{q}%", f"%{q}%"))
        else:
            cur.execute("SELECT * FROM restaurant")
        rows = cur.fetchall()
    finally:
        conn.close()
    return render_template("client/restaurants.html", restaurants=rows)

@app.route("/client/restaurant/<string:restaurant_id>")
@login_required("CLIENT")
def client_restaurant_menu(restaurant_id):
    conn, cur = get_cursor()
    try:
        cur.execute("SELECT * FROM restaurant WHERE id_restaurant=%s", (restaurant_id,))
        rest = cur.fetchone()
        if not rest:
            flash("Restaurant introuvable.")
            return redirect(url_for("client_restaurants"))
        cur.execute("SELECT * FROM plat WHERE id_restaurant=%s AND (disponible=1 OR disponible IS NULL)", (restaurant_id,))
        menu = cur.fetchall()
    finally:
        conn.close()
    return render_template("client/restaurant_menu.html", restaurant=rest, menu=menu)

@app.route("/client/add_line", methods=["POST"])
@login_required("CLIENT")
def client_add_line():
    panier = session.get("panier", [])
    panier.append({
        "id_plat": request.form["id_plat"],
        "nom": request.form["nom"],
        "pu": float(request.form["pu"]),
        "qty": int(request.form["qty"]),
        "id_restaurant": request.form.get("id_restaurant"),
        "restaurant_name": request.form.get("restaurant_name"),
    })
    session["panier"] = panier
    flash("Plat ajouté au panier")
    return redirect(request.referrer or url_for("client_restaurants"))

@app.route("/client/cart", methods=["GET", "POST"])
@login_required("CLIENT")
def client_cart():
    panier = session.get("panier", [])

    if request.method == "POST":
        # 1) Gérer d'abord les actions du panier (update / clear)
        action = (request.form.get("action") or "").strip().lower()

        if action == "clear":
            session["panier"] = []
            flash("Panier vidé avec succès.")
            return redirect(url_for("client_cart"))

        if action == "update":
            cart_data_raw = request.form.get("cart_data")
            try:
                updates = json.loads(cart_data_raw) if cart_data_raw else []
            except Exception:
                updates = []

            # indexer l’ancien panier par nom pour conserver id_plat / id_restaurant
            by_name = {str(it.get("nom")): it for it in panier}

            new_panier = []
            for u in updates:
                nom = str(u.get("nom"))
                qty = int(u.get("qty", 0))
                pu  = float(u.get("pu", 0))
                if qty <= 0:
                    continue

                if nom in by_name:
                    it = by_name[nom].copy()
                    it["qty"] = qty
                    it["pu"]  = pu  # on met à jour le prix affiché côté front
                    new_panier.append(it)
                else:
                    # cas rare : l'élément n'existait pas (on garde ce qui arrive)
                    new_panier.append({
                        "id_plat": u.get("id_plat"),  # peut être None
                        "nom": nom,
                        "pu": pu,
                        "qty": qty,
                        # ces champs ne sont pas envoyés par update(), on ne les connaît pas
                        "id_restaurant": (panier[0].get("id_restaurant") if panier else None),
                        "restaurant_name": (panier[0].get("restaurant_name") if panier else None),
                    })

            session["panier"] = new_panier
            flash("Panier mis à jour.")
            return redirect(url_for("client_cart"))

        # 2) Sinon, c'est une validation de commande
        adresse = (request.form.get("adresse") or "").strip()
        zone = (request.form.get("zone") or "").strip()
        id_restaurant = (request.form.get("id_restaurant") or "").strip()

        # Petits fallback au cas où
        if not id_restaurant and panier:
            id_restaurant = str(panier[0].get("id_restaurant") or "")
        if not adresse:
            adresse = (request.form.get("final-address") or "").strip()
        if not zone:
            zone = (request.form.get("final-zone") or "").strip()

        if not adresse or not zone or not id_restaurant:
            flash("Adresse, zone et restaurant sont requis.")
            return redirect(request.referrer or url_for("client_cart"))

        if not panier:
            flash("Votre panier est vide.")
            return redirect(url_for("client_cart"))

        total = sum(float(i["qty"]) * float(i["pu"]) for i in panier)
        new_id = f"cmd_{int(time.time()) % 100000000:08d}"

        conn, cur = get_cursor()
        try:
            cur.execute(
                """
                INSERT INTO commande(
                  id_commande, id_client, id_restaurant, zone,
                  livraison_adresse, remuneration, statut, date_creation, montant_total_client
                ) VALUES (%s,%s,%s,%s,%s,%s,'CREEE',UNIX_TIMESTAMP(),%s)
                """,
                (
                    new_id,
                    session["user"]["id"],
                    id_restaurant,
                    zone,
                    adresse,
                    0.00,
                    total,
                ),
            )

            for it in panier:
                cur.execute(
                    """
                    INSERT INTO commande_ligne (id_commande, id_plat, quantite, prix_unitaire)
                    VALUES (%s,%s,%s,%s)
                    """,
                    (new_id, it.get("id_plat"), int(it["qty"]), float(it["pu"]))
                )

            conn.commit()
        finally:
            conn.close()

        session["panier"] = []
        flash(f"Commande {new_id} créée avec succès.")
        return redirect(url_for("client_orders"))

    # GET -> affichage
    return render_template("client/cart.html", panier=panier)


@app.route("/client/remove_line", methods=["POST"])
@login_required("CLIENT")
def client_remove_line():
    """Supprime un plat du panier à partir du nom ou de l'id"""
    item_name = request.form.get("item_name")
    id_plat = request.form.get("id_plat")
    
    panier = session.get("panier", [])
    if not panier:
        flash("Panier vide.")
        return redirect(url_for("client_cart"))
    
    # Supprime l’élément correspondant (par nom ou id)
    panier = [
        it for it in panier
        if str(it.get("id_plat")) != str(id_plat) and it.get("nom") != item_name
    ]
    session["panier"] = panier
    
    flash("Plat retiré du panier.")
    return redirect(url_for("client_cart"))


@app.route("/client/orders")
@login_required("CLIENT")
def client_orders():
    conn, cur = get_cursor()
    cur.execute("SELECT * FROM commande WHERE id_client=%s ORDER BY date_creation DESC", (session["user"]["id"],))
    rows = cur.fetchall()
    conn.close()
    return render_template("client/orders.html", commandes=rows)

@app.route("/client/cancel/<string:order_id>", methods=["POST"])
@login_required("CLIENT")
def client_cancel(order_id):
    motif = request.form.get("motif") or "Annulation par le client"
    conn, cur = get_cursor()
    try:
        cur.execute("SELECT statut FROM commande WHERE id_commande=%s AND id_client=%s", (order_id, session["user"]["id"]))
        cmd = cur.fetchone()
        if not cmd:
            flash("Commande introuvable ou non autorisée.")
        elif cmd["statut"] in ("CREEE", "ANONCEE"):
            cur.execute("""
                UPDATE commande SET statut='ANNULEE', annule_par='CLIENT',
                    motif_annulation=%s, date_cloture=UNIX_TIMESTAMP()
                WHERE id_commande=%s
            """, (motif, order_id))
            conn.commit()
            flash("Commande annulée.")
        else:
            flash("Impossible d’annuler cette commande.")
    finally:
        conn.close()
    return redirect(url_for("client_orders"))

# -----------------------------
# RESTAURANT
# -----------------------------
@app.route("/restaurant")
@login_required("RESTAURANT")
def restaurant_dashboard():
    statut = request.args.get("statut", "").strip()
    conn, cur = get_cursor()
    try:
        if statut:
            cur.execute("SELECT * FROM commande WHERE id_restaurant=%s AND statut=%s ORDER BY date_creation DESC",
                        (session["user"]["id"], statut))
        else:
            cur.execute("SELECT * FROM commande WHERE id_restaurant=%s ORDER BY date_creation DESC",
                        (session["user"]["id"],))
        rows = cur.fetchall()
    finally:
        conn.close()
    return render_template("restaurant/dashboard.html", orders=rows)

@app.route("/restaurant/order/<string:order_id>")
@login_required("RESTAURANT")
def restaurant_order_details(order_id):
    conn, cur = get_cursor()
    try:
        cur.execute("SELECT * FROM commande WHERE id_commande=%s", (order_id,))
        order = cur.fetchone()
        if not order:
            flash("Commande introuvable.")
            return redirect(url_for("restaurant_dashboard"))
        cur.execute("""
            SELECT p.nom, cl.quantite, cl.prix_unitaire
            FROM commande_ligne cl
            JOIN plat p ON p.id_plat = cl.id_plat
            WHERE cl.id_commande=%s
        """, (order_id,))
        lignes = cur.fetchall()
        cur.execute("SELECT * FROM interet WHERE id_commande=%s ORDER BY ts DESC", (order_id,))
        interets = cur.fetchall()
    finally:
        conn.close()
    return render_template("restaurant/order_details.html", order=order, lignes=lignes, interets=interets)

@app.route("/restaurant/order/<string:order_id>/publish", methods=["POST"])
@login_required("RESTAURANT")
def restaurant_publish(order_id):
    remuneration = request.form.get("remuneration")
    if not remuneration:
        flash("Rémunération requise.")
        return redirect(url_for("restaurant_order_details", order_id=order_id))
    conn, cur = get_cursor()
    try:
        cur.execute("UPDATE commande SET remuneration=%s, statut='ANONCEE', date_publiee=UNIX_TIMESTAMP() WHERE id_commande=%s",
                    (remuneration, order_id))
        conn.commit()
    finally:
        conn.close()
    flash("Commande publiée.")
    return redirect(url_for("restaurant_order_details", order_id=order_id))

@app.route("/restaurant/order/<string:order_id>/assign", methods=["POST"])
@login_required("RESTAURANT")
def restaurant_assign(order_id):
    livreur_id = request.form.get("livreur_id")
    if not livreur_id:
        flash("Aucun livreur sélectionné.")
        return redirect(url_for("restaurant_order_details", order_id=order_id))
    conn, cur = get_cursor()
    try:
        cur.execute("UPDATE commande SET id_livreur_assigne=%s, statut='ASSIGNEE', date_assignee=UNIX_TIMESTAMP() WHERE id_commande=%s",
                    (livreur_id, order_id))
        conn.commit()
    finally:
        conn.close()
    flash("Livreur assigné.")
    return redirect(url_for("restaurant_order_details", order_id=order_id))

@app.route("/restaurant/order/<string:order_id>/cancel", methods=["POST"])
@login_required("RESTAURANT")
def restaurant_cancel(order_id):
    motif = request.form.get("motif") or "Annulation par le restaurant"
    conn, cur = get_cursor()
    try:
        cur.execute("""
            UPDATE commande SET statut='ANNULEE', annule_par='RESTAURANT',
                motif_annulation=%s, date_cloture=UNIX_TIMESTAMP()
            WHERE id_commande=%s
        """, (motif, order_id))
        conn.commit()
    finally:
        conn.close()
    flash("Commande annulée.")
    return redirect(url_for("restaurant_dashboard"))

# -----------------------------
# LIVREUR
# -----------------------------
@app.route("/livreur")
@login_required("LIVREUR")
def livreur_dashboard():
    return render_template("livreur/dashboard.html")

@app.route("/livreur/annonces")
@login_required("LIVREUR")
def livreur_annonces():
    conn, cur = get_cursor()
    cur.execute("""
        SELECT c.*, r.nom AS restaurant_nom
        FROM commande c
        JOIN restaurant r ON r.id_restaurant=c.id_restaurant
        WHERE c.zone=%s AND c.statut='ANONCEE'
        ORDER BY c.date_creation DESC
    """, (session["user"]["zone"],))
    rows = cur.fetchall()
    conn.close()
    return render_template("livreur/annonces.html", orders=rows, zone=session["user"]["zone"])

@app.route("/livreur/accepter/<string:order_id>", methods=["POST"])
@login_required("LIVREUR")
def livreur_accepter(order_id):
    conn, cur = get_cursor()
    try:
        cur.execute("SELECT * FROM interet WHERE id_commande=%s AND id_livreur=%s", (order_id, session["user"]["id"]))
        if cur.fetchone():
            flash("Vous avez déjà accepté cette course.")
        else:
            cur.execute("""
                INSERT INTO interet (id_commande, id_livreur, ts, temps_estime, commentaire)
                VALUES (%s, %s, UNIX_TIMESTAMP(), %s, %s)
            """, (order_id, session["user"]["id"], request.form.get("temps_estime"), request.form.get("commentaire")))
            conn.commit()
            flash("Course acceptée.")
    finally:
        conn.close()
    return redirect(url_for("livreur_annonces"))

@app.route("/livreur/demarrer/<string:order_id>", methods=["POST"])
@login_required("LIVREUR")
def livreur_demarrer(order_id):
    conn, cur = get_cursor()
    try:
        cur.execute("UPDATE commande SET statut='EN_LIVRAISON', date_assignee=UNIX_TIMESTAMP() WHERE id_commande=%s AND id_livreur_assigne=%s",
                    (order_id, session["user"]["id"]))
        conn.commit()
        flash("Livraison démarrée.")
    finally:
        conn.close()
    return redirect(url_for("livreur_mes_courses"))

@app.route("/livreur/terminer/<string:order_id>", methods=["POST"])
@login_required("LIVREUR")
def livreur_terminer(order_id):
    conn, cur = get_cursor()
    try:
        cur.execute("""
            UPDATE commande SET statut='LIVREE', date_cloture=UNIX_TIMESTAMP(), livree_par_livreur=%s
            WHERE id_commande=%s AND id_livreur_assigne=%s
        """, (session["user"]["id"], order_id, session["user"]["id"]))
        conn.commit()
        flash("Commande livrée.")
    finally:
        conn.close()
    return redirect(url_for("livreur_mes_courses"))

@app.route("/livreur/mes_courses")
@login_required("LIVREUR")
def livreur_mes_courses():
    conn, cur = get_cursor()
    cur.execute("""
        SELECT c.*, r.nom AS restaurant_nom
        FROM commande c
        JOIN restaurant r ON r.id_restaurant=c.id_restaurant
        WHERE c.id_livreur_assigne=%s
        ORDER BY c.date_creation DESC
    """, (session["user"]["id"],))
    rows = cur.fetchall()
    conn.close()
    return render_template("livreur/mes_courses.html", orders=rows)

@app.route("/livreur/historique")
@login_required("LIVREUR")
def livreur_historique():
    conn, cur = get_cursor()
    cur.execute("""
        SELECT c.*, r.nom AS restaurant_nom
        FROM commande c
        JOIN restaurant r ON r.id_restaurant=c.id_restaurant
        WHERE c.livree_par_livreur=%s
        ORDER BY c.date_cloture DESC
    """, (session["user"]["id"],))
    rows = cur.fetchall()
    conn.close()
    return render_template("livreur/historique.html", orders=rows)

@app.post("/livreur/interet/<string:order_id>")
@login_required("LIVREUR")
def livreur_interet(order_id):
    action = (request.form.get("action") or "ajouter").strip()
    if action == "ajouter":
        # Reuse existing accepter logic
        return livreur_accepter(order_id)
    # optional: support "retirer" by deleting from `interet` table
    flash("Opération non supportée.", "warning")
    return redirect(url_for("livreur_annonces"))


# -----------------------------
# API JSON
# -----------------------------
@app.route("/orders/<string:oid>/json")
@login_required()
def order_json(oid):
    conn, cur = get_cursor()
    cur.execute("SELECT * FROM commande WHERE id_commande=%s", (oid,))
    o = cur.fetchone()
    conn.close()
    if not o:
        return jsonify({"error": "Commande introuvable"}), 404
    return jsonify(o)

@app.route("/orders/<string:oid>")
@login_required()
def order_json_alias(oid):
    return order_json(oid)  # same payload as /json


# -----------------------------
# Run
# -----------------------------
if __name__ == "__main__":
    app.run(debug=True, port=5000)

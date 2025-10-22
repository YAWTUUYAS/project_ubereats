#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
POC UberEats — Version SQL
Front partagé : ../frontend/
"""

import os, time
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
import mysql.connector


from functools import wraps
from werkzeug.exceptions import abort

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
FRONTEND_DIR = os.path.join(BASE_DIR, "../frontend")  # dossier en minuscules

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

# -----------------------------
# Helpers
# -----------------------------
def now():
    return int(time.time())

# -----------------------------
# Auth
# -----------------------------
@app.route("/")
def home():
    return render_template("home.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        role = request.form["role"].upper().strip()          # "CLIENT" | "RESTAURANT" | "LIVREUR"
        username = request.form["username"].strip()
        password = request.form["password"].strip()

        table_map = {
            "CLIENT": {
                "table": "client",
                "id_col": "id_client",
                "extra_cols": ["nom"],
            },
            "RESTAURANT": {
                "table": "restaurant",
                "id_col": "id_restaurant",
                "extra_cols": ["nom","zone"],
            },
            "LIVREUR": {
                "table": "livreur",
                "id_col": "id_livreur",
                "extra_cols": ["nom","zone","vehicule"],
            },
        }

        if role not in table_map:
            flash("Rôle invalide.")
            return render_template("login.html")

        meta = table_map[role]
        cols = [meta["id_col"], "username", "password"] + meta["extra_cols"]
        col_list = ", ".join(cols)

        conn, cur = get_cursor()
        cur.execute(
            f"SELECT {col_list} FROM {meta['table']} WHERE username=%s AND password=%s",
            (username, password),
        )
        u = cur.fetchone()
        conn.close()

        if not u:
            flash("Identifiants invalides")
            return render_template("login.html")

        # Stocke l’utilisateur en session avec le bon id_col
        session["user"] = {
            "id": u[meta["id_col"]],
            "role": role,
            "username": username
        }
        # Optionnel : stocker quelques champs utiles (nom, zone, etc.)
        for k in meta["extra_cols"]:
            session["user"][k] = u.get(k)

        flash("Connecté")

        if role == "CLIENT":
            return redirect(url_for("client_restaurants"))
        elif role == "RESTAURANT":
            return redirect(url_for("restaurant_dashboard"))
        elif role == "LIVREUR":
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
# routes client
@app.route("/client/restaurants")
def client_restaurants():
    q = (request.args.get("q") or "").strip()
    conn, cur = get_cursor()
    try:
        if q:
            cur.execute(
                "SELECT * FROM restaurant WHERE nom LIKE %s OR zone LIKE %s",
                (f"%{q}%", f"%{q}%")
            )
        else:
            cur.execute("SELECT * FROM restaurant")
        rows = cur.fetchall()
    finally:
        conn.close()
    return render_template("client/restaurants.html", restaurants=rows)

# Accepte /client/restaurant/<restaurant_id>
@app.route("/client/restaurant/<string:restaurant_id>")
def client_restaurant_menu(restaurant_id):
    conn, cur = get_cursor()
    try:
        cur.execute("SELECT * FROM restaurant WHERE id_restaurant=%s", (restaurant_id,))
        rest = cur.fetchone()
        if not rest:
            flash("Restaurant introuvable.")
            return redirect(url_for("client_restaurants"))

        cur.execute("""
            SELECT * FROM plat
            WHERE id_restaurant=%s AND (disponible=1 OR disponible IS NULL)
        """, (restaurant_id,))
        menu = cur.fetchall()
    finally:
        conn.close()

    return render_template("client/restaurant_menu.html", restaurant=rest, menu=menu)

@app.route("/client/cart", methods=["GET", "POST"])
def client_cart():
    panier = session.get("panier", [])
    if request.method == "POST":
        adresse = (request.form.get("adresse") or "").strip()
        zone = (request.form.get("zone") or "").strip()
        id_restaurant = (request.form.get("id_restaurant") or "").strip()

        if not adresse or not zone or not id_restaurant:
            flash("Adresse, zone et restaurant sont requis.")
            return redirect(request.referrer or url_for("client_restaurants"))

        total = sum(float(i["qty"]) * float(i["pu"]) for i in panier)

        # cmd_ + 8 chiffres (<= 12 chars) → compatible VARCHAR(16)
        new_id = f"cmd_{int(time.time())%100000000:08d}"

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
                    session["user"]["id"],   # id_client
                    id_restaurant,
                    zone,
                    adresse,
                    0.00,                    # NOT NULL
                    total
                ),
            )

            for it in panier:
                cur.execute(
                    """
                    INSERT INTO commande_ligne (id_commande, id_plat, quantite, prix_unitaire)
                    VALUES (%s,%s,%s,%s)
                    """,
                    (new_id, it["id_plat"], int(it["qty"]), float(it["pu"]))
                )

            conn.commit()
        finally:
            conn.close()

        session["panier"] = []
        flash(f"Commande {new_id} créée")
        return redirect(url_for("client_orders"))

    return render_template("client/cart.html", panier=panier)

@app.route("/client/add_line", methods=["POST"])
def client_add_line():
    panier = session.get("panier", [])
    panier.append(
        {
            "id_plat": request.form["id_plat"],
            "nom": request.form["nom"],
            "pu": float(request.form["pu"]),
            "qty": int(request.form["qty"]),
        }
    )
    session["panier"] = panier
    flash("Plat ajouté au panier")
    return redirect(request.referrer or url_for("client_restaurants"))

@app.route("/client/orders")
@login_required("CLIENT")
def client_orders():
    conn, cur = get_cursor()
    cur.execute(
        "SELECT * FROM commande WHERE id_client=%s ORDER BY date_creation DESC",
        (session["user"]["id"],)
    )
    rows = cur.fetchall()
    conn.close()
    return render_template("client/orders.html", commandes=rows)


@app.route("/client/cancel/<string:order_id>", methods=["POST"])
@login_required("CLIENT")
def client_cancel(order_id):
    """Permet au client d'annuler une commande tant qu'elle n'est pas assignée"""
    motif = request.form.get("motif") or "Annulation par le client"

    conn, cur = get_cursor()
    try:
        # Vérifie le statut actuel
        cur.execute("""
            SELECT statut FROM commande
            WHERE id_commande=%s AND id_client=%s
        """, (order_id, session["user"]["id"]))
        cmd = cur.fetchone()

        if not cmd:
            flash("Commande introuvable ou non autorisée.")
            return redirect(url_for("client_orders"))

        statut = cmd["statut"]

        # Seuls ces statuts sont annulables
        if statut in ("CREEE", "ANONCEE"):
            cur.execute("""
                UPDATE commande
                   SET statut='ANNULEE',
                       annule_par='CLIENT',
                       motif_annulation=%s,
                       date_cloture=UNIX_TIMESTAMP()
                 WHERE id_commande=%s
            """, (motif, order_id))
            conn.commit()
            flash("Commande annulée avec succès.")
        else:
            flash(f"Impossible d’annuler une commande déjà {statut.lower()}.")
    finally:
        conn.close()

    return redirect(url_for("client_orders"))


# -----------------------------
# RESTAURANT
# -----------------------------
@app.route("/restaurant")
@login_required("RESTAURANT")
def restaurant_dashboard():
    """Tableau de bord du restaurant : commandes filtrées par statut"""
    statut = request.args.get("statut", "").strip()

    conn, cur = get_cursor()
    try:
        if statut:
            cur.execute("""
                SELECT * FROM commande
                WHERE id_restaurant=%s AND statut=%s
                ORDER BY date_creation DESC
            """, (session["user"]["id"], statut))
        else:
            cur.execute("""
                SELECT * FROM commande
                WHERE id_restaurant=%s
                ORDER BY date_creation DESC
            """, (session["user"]["id"],))
        rows = cur.fetchall()
    finally:
        conn.close()

    return render_template("restaurant/dashboard.html", orders=rows)


@app.route("/restaurant/order/<string:order_id>")
@login_required("RESTAURANT")
def restaurant_order_details(order_id):
    """Affiche le détail d'une commande pour un restaurant"""
    conn, cur = get_cursor()
    try:
        # 1️⃣ Charger la commande
        cur.execute("SELECT * FROM commande WHERE id_commande=%s", (order_id,))
        order = cur.fetchone()
        if not order:
            flash("Commande introuvable.")
            return redirect(url_for("restaurant_dashboard"))

        # 2️⃣ Charger les lignes de commande
        cur.execute("""
            SELECT p.nom, cl.quantite, cl.prix_unitaire
            FROM commande_ligne cl
            JOIN plat p ON p.id_plat = cl.id_plat
            WHERE cl.id_commande=%s
        """, (order_id,))
        lignes = cur.fetchall()

        # 3️⃣ Charger les livreurs intéressés (table interet)
        cur.execute("""
            SELECT i.id_livreur, i.temps_estime
            FROM interet i
            WHERE i.id_commande=%s
            ORDER BY i.ts DESC
        """, (order_id,))
        interets = cur.fetchall()
    finally:
        conn.close()

    return render_template(
        "restaurant/order_details.html",
        order=order,
        lignes=lignes,
        interets=interets
    )


# -----------------------------
# 🔧 Actions Restaurant
# -----------------------------

@app.route("/restaurant/order/<string:order_id>/publish", methods=["POST"])
@login_required("RESTAURANT")
def restaurant_publish(order_id):
    """Publier une commande pour la rendre visible aux livreurs"""
    remuneration = request.form.get("remuneration")
    if not remuneration:
        flash("Veuillez indiquer une rémunération.")
        return redirect(url_for("restaurant_order_details", order_id=order_id))

    conn, cur = get_cursor()
    try:
        cur.execute("""
            UPDATE commande
               SET remuneration=%s,
                   statut='ANONCEE',
                   date_publiee=UNIX_TIMESTAMP()
             WHERE id_commande=%s
        """, (remuneration, order_id))
        conn.commit()
    finally:
        conn.close()

    flash("Commande publiée avec succès.")
    return redirect(url_for("restaurant_order_details", order_id=order_id))


@app.route("/restaurant/order/<string:order_id>/assign", methods=["POST"])
@login_required("RESTAURANT")
def restaurant_assign(order_id):
    """Assigner un livreur à une commande annoncée"""
    livreur_id = request.form.get("livreur_id")
    if not livreur_id:
        flash("Aucun livreur sélectionné.")
        return redirect(url_for("restaurant_order_details", order_id=order_id))

    conn, cur = get_cursor()
    try:
        cur.execute("""
            UPDATE commande
               SET id_livreur_assigne=%s,
                   statut='ASSIGNEE',
                   date_assignee=UNIX_TIMESTAMP()
             WHERE id_commande=%s
        """, (livreur_id, order_id))
        conn.commit()
    finally:
        conn.close()

    flash(f"Livreur {livreur_id} assigné avec succès.")
    return redirect(url_for("restaurant_order_details", order_id=order_id))


@app.route("/restaurant/order/<string:order_id>/cancel", methods=["POST"])
@login_required("RESTAURANT")
def restaurant_cancel(order_id):
    """Annuler une commande"""
    motif = request.form.get("motif") or "Annulation par le restaurant"

    conn, cur = get_cursor()
    try:
        cur.execute("""
            UPDATE commande
               SET statut='ANNULEE',
                   annule_par='RESTAURANT',
                   motif_annulation=%s,
                   date_cloture=UNIX_TIMESTAMP()
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
    """Page d’accueil du livreur (menu principal)"""
    return render_template("livreur/dashboard.html")


@app.route("/livreur/interet/<string:order_id>", methods=["POST"])
@login_required("LIVREUR")
def livreur_interet(order_id):
    """Ajoute ou retire l'intérêt d'un livreur pour une commande ANONCEE"""
    action = request.form.get("action")  # 'ajouter' ou 'retirer'
    livreur_id = session["user"]["id"]

    conn, cur = get_cursor()
    try:
        # Vérifie le statut de la commande
        cur.execute("SELECT statut FROM commande WHERE id_commande=%s", (order_id,))
        cmd = cur.fetchone()
        if not cmd:
            flash("Commande introuvable.")
            return redirect(url_for("livreur_annonces"))

        # Si le livreur est déjà assigné, il ne peut plus retirer son intérêt
        if cmd["statut"] in ("ASSIGNEE", "EN_LIVRAISON", "LIVREE"):
            flash("Vous êtes déjà assigné à cette commande.")
            return redirect(url_for("livreur_mes_courses"))

        if action == "ajouter":
            # Vérifie si un intérêt existe déjà
            cur.execute(
                "SELECT 1 FROM interet WHERE id_commande=%s AND id_livreur=%s",
                (order_id, livreur_id),
            )
            if cur.fetchone():
                flash("Vous avez déjà manifesté votre intérêt.")
            else:
                cur.execute("""
                    INSERT INTO interet (id_commande, id_livreur, ts)
                    VALUES (%s, %s, UNIX_TIMESTAMP())
                """, (order_id, livreur_id))
                conn.commit()
                flash("Intérêt enregistré.")
        elif action == "retirer":
            cur.execute("""
                DELETE FROM interet
                WHERE id_commande=%s AND id_livreur=%s
            """, (order_id, livreur_id))
            conn.commit()
            flash("Intérêt retiré.")
    finally:
        conn.close()

    return redirect(url_for("livreur_annonces"))

@app.route("/livreur/demarrer/<string:order_id>", methods=["POST"])
@login_required("LIVREUR")
def livreur_demarrer(order_id):
    """Le livreur commence la livraison"""
    conn, cur = get_cursor()
    try:
        cur.execute("""
            UPDATE commande
               SET statut='EN_LIVRAISON',
                   date_assignee=UNIX_TIMESTAMP()
             WHERE id_commande=%s AND id_livreur_assigne=%s
        """, (order_id, session["user"]["id"]))
        conn.commit()
        flash("Livraison démarrée.")
    finally:
        conn.close()
    return redirect(url_for("livreur_mes_courses"))

@app.route("/livreur/terminer/<string:order_id>", methods=["POST"])
@login_required("LIVREUR")
def livreur_terminer(order_id):
    """Le livreur marque la commande comme livrée"""
    conn, cur = get_cursor()
    try:
        cur.execute("""
            UPDATE commande
               SET statut='LIVREE',
                   date_cloture=UNIX_TIMESTAMP(),
                   livree_par_livreur=%s
             WHERE id_commande=%s AND id_livreur_assigne=%s
        """, (session["user"]["id"], order_id, session["user"]["id"]))
        conn.commit()
        flash("Commande livrée avec succès !")
    finally:
        conn.close()
    return redirect(url_for("livreur_mes_courses"))


@app.route("/livreur/annonces")
@login_required("LIVREUR")
def livreur_annonces():
    """Affiche les commandes disponibles dans la même zone"""
    conn, cur = get_cursor()
    cur.execute("""
        SELECT c.*, r.nom AS restaurant_nom
        FROM commande c
        JOIN restaurant r ON r.id_restaurant = c.id_restaurant
        WHERE c.zone=%s AND c.statut = 'ANONCEE'
        ORDER BY c.date_creation DESC
    """, (session["user"]["zone"],))
    rows = cur.fetchall()
    conn.close()
    return render_template("livreur/annonces.html", orders=rows, zone=session["user"]["zone"])

@app.route("/livreur/mes_courses")
@login_required("LIVREUR")
def livreur_mes_courses():
    """Affiche les commandes assignées à ce livreur"""
    conn, cur = get_cursor()
    cur.execute(
        """
        SELECT c.*, r.nom AS restaurant_nom
        FROM commande c
        JOIN restaurant r ON r.id_restaurant = c.id_restaurant
        WHERE c.id_livreur_assigne=%s
        ORDER BY c.date_creation DESC
        """,
        (session["user"]["id"],)
    )
    rows = cur.fetchall()
    conn.close()
    return render_template("livreur/mes_courses.html", orders=rows)

@app.route("/livreur/historique")
@login_required("LIVREUR")
def livreur_historique():
    """Commandes livrées ou terminées"""
    conn, cur = get_cursor()
    cur.execute("""
        SELECT c.*, r.nom AS restaurant_nom
        FROM commande c
        JOIN restaurant r ON r.id_restaurant = c.id_restaurant
        WHERE c.livree_par_livreur=%s
        ORDER BY c.date_cloture DESC
    """, (session["user"]["id"],))
    rows = cur.fetchall()
    conn.close()
    return render_template("livreur/historique.html", orders=rows)

@app.route("/livreur/accepter/<string:order_id>", methods=["POST"])
@login_required("LIVREUR")
def livreur_accepter(order_id):
    """Quand un livreur accepte une annonce (il manifeste son intérêt)"""
    conn, cur = get_cursor()
    try:
        # Vérifie si le livreur a déjà postulé
        cur.execute("""
            SELECT * FROM interet
            WHERE id_commande=%s AND id_livreur=%s
        """, (order_id, session["user"]["id"]))
        existing = cur.fetchone()

        if existing:
            flash("Vous avez déjà accepté cette course.")
        else:
            cur.execute("""
                INSERT INTO interet (id_commande, id_livreur, ts, temps_estime, commentaire)
                VALUES (%s, %s, UNIX_TIMESTAMP(), %s, %s)
            """, (
                order_id,
                session["user"]["id"],
                request.form.get("temps_estime") or None,
                request.form.get("commentaire") or None,
            ))
            conn.commit()
            flash("Vous avez accepté la course.")
    finally:
        conn.close()

    return redirect(url_for("livreur_annonces"))


# -----------------------------
# API JSON (ex: pour suivi)
# -----------------------------
@app.route("/orders/<string:oid>/json")
@login_required("LIVREUR")
def order_json(oid):
    conn, cur = get_cursor()
    cur.execute("SELECT * FROM commande WHERE id_commande=%s", (oid,))
    o = cur.fetchone()
    conn.close()
    return jsonify(o)

# -----------------------------
# Run
# -----------------------------
if __name__ == "__main__":
    app.run(debug=True, port=5000)

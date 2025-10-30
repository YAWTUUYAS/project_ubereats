#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
POC UberEats — Version MongoDB (Cloud, nested docs compatible)
Matches Redis POC in endpoints and logic.
"""

import os, json, time, uuid
from functools import wraps
from types import SimpleNamespace
from flask import Flask, render_template, request, redirect, url_for, flash, make_response, Response
from pymongo import MongoClient
from dotenv import load_dotenv

# -----------------------------
# Setup MongoDB + Flask
# -----------------------------
load_dotenv()

MONGO_URI = os.getenv("MONGO_URI")
DB_NAME = os.getenv("DB_NAME", "ubereats_poc")
APP_SECRET = os.getenv("APP_SECRET", "change-me-please")

client = MongoClient(MONGO_URI)
db = client[DB_NAME]

BASE_DIR = os.path.dirname(__file__)
FRONTEND_DIR = os.path.join(BASE_DIR, "../frontend")

app = Flask(
    __name__,
    template_folder=os.path.join(FRONTEND_DIR, "templates"),
    static_folder=os.path.join(FRONTEND_DIR, "static"),
)
app.secret_key = APP_SECRET


# -----------------------------
# Helpers
# -----------------------------
def now(): return int(time.time())
def new_order_id(): return "cmd_" + uuid.uuid4().hex[:8]


# -----------------------------
# Session injection
# -----------------------------
@app.context_processor
def inject_session_user():
    auth = request.cookies.get("auth")
    user = None
    if auth:
        try:
            user = SimpleNamespace(**json.loads(auth))
        except Exception:
            pass
    return {"session": SimpleNamespace(user=user)}

@app.before_request
def inject_user_from_cookie():
    auth = request.cookies.get("auth")
    request.user = None
    if not auth:
        return
    try:
        request.user = json.loads(auth)
    except Exception:
        pass


def require_login(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if not getattr(request, "user", None):
            return redirect(url_for("login"))
        return f(*a, **kw)
    return wrapper


def role_required(*roles):
    def decorator(f):
        @wraps(f)
        def wrapper(*a, **kw):
            if not getattr(request, "user", None):
                return redirect(url_for("login"))
            if request.user["role"] not in roles:
                flash("Accès refusé.")
                return redirect(url_for("home"))
            return f(*a, **kw)
        return wrapper
    return decorator


# -----------------------------
# Auth
# -----------------------------
@app.route("/")
def home():
    return render_template("home.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("login.html")

    role = request.form["role"].strip().upper()
    username = request.form["username"].strip()
    password = request.form["password"].strip()

    doc = db.users.find_one({"user.username": username, "user.role": role, "user.password": password})
    if not doc or "user" not in doc:
        flash("Identifiants invalides.")
        return render_template("login.html")

    u = doc["user"]
    auth_data = {
        "id": u.get("id"),
        "role": u.get("role"),
        "username": u.get("username"),
        "nom": u.get("nom"),
        "zone": u.get("zone"),
    }

    target = (
        "client_restaurants" if role == "CLIENT" else
        "restaurant_dashboard" if role == "RESTAURANT" else
        "livreur_dashboard"
    )

    resp = make_response(redirect(url_for(target)))
    resp.set_cookie("auth", json.dumps(auth_data), httponly=False, samesite="Lax")
    flash(f"Connecté en tant que {role.lower()} : {u['username']}")
    return resp


@app.route("/logout")
def logout():
    resp = redirect(url_for("home"))
    resp.delete_cookie("auth")
    flash("Déconnecté.")
    return resp


# -----------------------------
# CLIENT
# -----------------------------
@app.route("/client/restaurants")
@require_login
@role_required("CLIENT")
def client_restaurants():
    q = (request.args.get("q") or "").lower()
    restaurants = []
    for m in db.menus.find({}, {"_id": 0}):
        r = m.get("restaurant", {})
        if not q or q in r.get("nom", "").lower() or q in r.get("zone", "").lower():
            restaurants.append(r)
    return render_template("client/restaurants.html", restaurants=restaurants)


@app.route("/client/restaurant/<string:restaurant_id>")
@require_login
@role_required("CLIENT")
def client_restaurant_menu(restaurant_id):
    m = db.menus.find_one({"restaurant.id": restaurant_id}, {"_id": 0})
    if not m:
        flash("Restaurant introuvable.")
        return redirect(url_for("client_restaurants"))

    restaurant = m.get("restaurant", {})
    menu = m.get("menu", [])
    panier = []
    return render_template("client/restaurant_menu.html", restaurant=restaurant, menu=menu, panier=panier)


@app.route("/client/cart", methods=["GET", "POST"])
@require_login
@role_required("CLIENT")
def client_cart():
    if request.method == "POST":
        # Simplified: immediate order creation
        adresse = request.form.get("adresse", "")
        zone = request.form.get("zone", "")
        restaurant_id = request.form.get("restaurant_id")
        restaurant_name = request.form.get("restaurant_name", "")
        total = float(request.form.get("total", "0"))

        oid = new_order_id()
        order = {
            "id": oid,
            "version": 1,
            "statut": "CREEE",
            "timestamps": {"creation": now()},
            "zone": zone,
            "livraison": {"adresse": adresse},
            "client": {"id": request.user["id"], "nom": request.user.get("nom")},
            "restaurant": {"id": restaurant_id, "nom": restaurant_name},
            "remuneration": 0,
            "montant_total_client": total,
            "lignes": [],
            "interets": [],
        }
        db.orders.insert_one({"key": f"order:{oid}", "order": order})
        flash(f"Commande {oid} créée.")
        return redirect(url_for("client_orders"))

    return render_template("client/cart.html", panier=[])


@app.route("/client/orders")
@require_login
@role_required("CLIENT")
def client_orders():
    id_client = request.user["id"]
    docs = db.orders.find({"order.client.id": id_client}, {"_id": 0})
    commandes = []
    for d in docs:
        o = d.get("order", {})
        o["id_commande"] = o.get("id")
        o["livraison_adresse"] = (o.get("livraison") or {}).get("adresse")
        commandes.append(o)
    commandes.sort(key=lambda x: (x.get("timestamps") or {}).get("creation", 0), reverse=True)
    return render_template("client/orders.html", commandes=commandes)


@app.post("/client/cancel/<string:order_id>")
@require_login
@role_required("CLIENT")
def client_cancel(order_id):
    db.orders.update_one({"order.id": order_id}, {"$set": {"order.statut": "ANNULEE"}})
    flash("Commande annulée.")
    return redirect(url_for("client_orders"))


# -----------------------------
# RESTAURANT
# -----------------------------
@app.route("/restaurant")
@require_login
@role_required("RESTAURANT")
def restaurant_dashboard():
    rid = request.user["id"]
    docs = db.orders.find({"order.restaurant.id": rid}, {"_id": 0})
    commandes = []
    for d in docs:
        o = d.get("order", {})
        o["id_commande"] = o.get("id")
        o["livraison_adresse"] = (o.get("livraison") or {}).get("adresse")
        commandes.append(o)
    commandes.sort(key=lambda x: (x.get("timestamps") or {}).get("creation", 0), reverse=True)
    return render_template("restaurant/dashboard.html", orders=commandes)


@app.route("/restaurant/order/<string:order_id>")
@require_login
@role_required("RESTAURANT")
def restaurant_order_details(order_id):
    d = db.orders.find_one({"order.id": order_id}, {"_id": 0})
    if not d:
        flash("Commande introuvable.")
        return redirect(url_for("restaurant_dashboard"))
    o = d.get("order", {})
    o["id_commande"] = o.get("id")
    o["livraison_adresse"] = (o.get("livraison") or {}).get("adresse")
    return render_template("restaurant/order_details.html", order=o, lignes=o.get("lignes", []), interets=o.get("interets", []))


@app.post("/restaurant/order/<string:order_id>/publish")
@require_login
@role_required("RESTAURANT")
def restaurant_publish(order_id):
    remuneration = float(request.form.get("remuneration") or 0)
    db.orders.update_one(
        {"order.id": order_id},
        {"$set": {"order.statut": "ANONCEE", "order.remuneration": remuneration}}
    )
    flash(f"Commande {order_id} publiée ({remuneration} €).")
    return redirect(url_for("restaurant_order_details", order_id=order_id))


@app.post("/restaurant/order/<string:order_id>/assign")
@require_login
@role_required("RESTAURANT")
def restaurant_assign(order_id):
    livreur_id = request.form.get("livreur_id")
    db.orders.update_one(
        {"order.id": order_id},
        {"$set": {"order.statut": "ASSIGNEE", "order.id_livreur_assigne": livreur_id}}
    )
    flash(f"Livreur {livreur_id} assigné à la commande {order_id}.")
    return redirect(url_for("restaurant_order_details", order_id=order_id))


@app.post("/restaurant/order/<string:order_id>/cancel")
@require_login
@role_required("RESTAURANT")
def restaurant_cancel(order_id):
    motif = request.form.get("motif", "Annulée par le restaurant")
    db.orders.update_one(
        {"order.id": order_id},
        {"$set": {"order.statut": "ANNULEE", "order.motif_annulation": motif}}
    )
    flash(f"Commande {order_id} annulée.")
    return redirect(url_for("restaurant_dashboard"))


# -----------------------------
# LIVREUR
# -----------------------------
@app.route("/livreur")
@require_login
@role_required("LIVREUR")
def livreur_dashboard():
    return render_template("livreur/dashboard.html")


@app.route("/livreur/annonces")
@require_login
@role_required("LIVREUR")
def livreur_annonces():
    zone = request.user.get("zone")
    docs = db.orders.find({"order.statut": "ANONCEE", "order.zone": zone}, {"_id": 0})
    annonces = []
    for d in docs:
        o = d.get("order", {})
        o["id_commande"] = o.get("id")
        o["livraison_adresse"] = (o.get("livraison") or {}).get("adresse")
        annonces.append(o)
    return render_template("livreur/annonces.html", orders=annonces, zone=zone)


@app.route("/livreur/mes_courses")
@require_login
@role_required("LIVREUR")
def livreur_mes_courses():
    lid = request.user["id"]
    docs = db.orders.find({"order.id_livreur_assigne": lid}, {"_id": 0})
    courses = []
    for d in docs:
        o = d.get("order", {})
        o["id_commande"] = o.get("id")
        o["livraison_adresse"] = (o.get("livraison") or {}).get("adresse")
        courses.append(o)
    return render_template("livreur/mes_courses.html", orders=courses)


@app.post("/livreur/demarrer/<string:order_id>")
@require_login
@role_required("LIVREUR")
def livreur_demarrer(order_id):
    db.orders.update_one({"order.id": order_id}, {"$set": {"order.statut": "EN_LIVRAISON"}})
    flash("Livraison démarrée.")
    return redirect(url_for("livreur_mes_courses"))


@app.post("/livreur/terminer/<string:order_id>")
@require_login
@role_required("LIVREUR")
def livreur_terminer(order_id):
    db.orders.update_one(
        {"order.id": order_id},
        {"$set": {"order.statut": "LIVREE", "order.timestamps.cloture": now()}}
    )
    flash("Commande livrée avec succès.")
    return redirect(url_for("livreur_mes_courses"))


@app.route("/livreur/historique")
@require_login
@role_required("LIVREUR")
def livreur_historique():
    lid = request.user["id"]
    docs = db.orders.find({"order.statut": "LIVREE", "order.id_livreur_assigne": lid}, {"_id": 0})
    histo = []
    for d in docs:
        o = d.get("order", {})
        o["id_commande"] = o.get("id")
        o["livraison_adresse"] = (o.get("livraison") or {}).get("adresse")
        histo.append(o)
    return render_template("livreur/historique.html", orders=histo)


# -----------------------------
# Run
# -----------------------------
if __name__ == "__main__":
    app.run(debug=True, port=5002)

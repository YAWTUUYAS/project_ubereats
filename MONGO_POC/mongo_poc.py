#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
POC UberEats — Version MongoDB (FULL, feature-parity with Redis POC)
- Denormalized nested docs (no normalization layer needed)
- Same endpoints and user flows as Redis POC
- Reuses shared frontend: ../frontend/templates + ../frontend/static
- SSE events via MongoDB Change Streams (Atlas or local replica set)

Collections (expected):
  - users            : { key: "user:ROLE:uid", user: {...} }
  - menus            : { restaurant: {...}, menu: [ {...}, ... ] }
  - orders           : { key: "order:<id>", order: {...} }
  - carts (optional) : { user_id: "<client_id>", items: [ {...} ] }

.env variables required:
  - MONGODB_URI
  - DB_NAME (default: ubereats_poc)
  - APP_SECRET (default: "change-me-please")
"""

import os, json, time, uuid, threading
from functools import wraps
from types import SimpleNamespace
from flask import Flask, render_template, request, redirect, url_for, flash, make_response, Response
from pymongo import MongoClient, ASCENDING
from pymongo.errors import PyMongoError
from dotenv import load_dotenv

# -----------------------------
# Setup MongoDB + Flask
# -----------------------------
load_dotenv()
MONGO_URI = os.getenv("MONGODB_URI") or os.getenv("MONGO_URI")
if not MONGO_URI:
    raise RuntimeError("Missing MONGODB_URI in .env")
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
# Helpers (time/ids/normalize)
# -----------------------------
def now() -> int:
    return int(time.time())

def new_order_id() -> str:
    return "cmd_" + uuid.uuid4().hex[:8]

def norm_order_for_view(o: dict) -> dict:
    """Normalize an order document (nested under 'order') into template-friendly dict."""
    out = dict(o)  # shallow copy
    if "id_commande" not in out and "id" in out:
        out["id_commande"] = out["id"]
    if "livraison_adresse" not in out and "livraison" in out:
        out["livraison_adresse"] = (out.get("livraison") or {}).get("adresse")
    # expose client fields
    if out.get("client"):
        out["id_client"] = out["client"].get("id")
        out["nom_client"] = out["client"].get("nom")
    # normalize lines for restaurant details view
    lignes = out.get("lignes") or []
    for l in lignes:
        if "quantite" not in l and "qty" in l:
            l["quantite"] = l["qty"]
        if "prix_unitaire" not in l and "pu" in l:
            l["prix_unitaire"] = l["pu"]
    return out

# -----------------------------
# Session / auth helpers
# -----------------------------
@app.context_processor
def inject_session_user():
    """Simule session.user pour les templates (comme Redis/SQL POC)."""
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
        request.user = None

def require_login(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if not getattr(request, "user", None):
            return redirect(url_for("login"))
        return f(*a, **kw)
    return wrapper

def role_required(*roles):
    def deco(f):
        @wraps(f)
        def wrapper(*a, **kw):
            if not getattr(request, "user", None):
                return redirect(url_for("login"))
            if request.user["role"] not in roles:
                flash("Accès refusé.")
                return redirect(url_for("home"))
            return f(*a, **kw)
        return wrapper
    return deco

# -----------------------------
# Indexes (recommended)
# -----------------------------
try:
    db.users.create_index([("user.role", ASCENDING), ("user.username", ASCENDING)])
    db.orders.create_index([("order.id", ASCENDING)], unique=True)
    db.orders.create_index([("order.client.id", ASCENDING)])
    db.orders.create_index([("order.restaurant.id", ASCENDING)])
    db.orders.create_index([("order.zone", ASCENDING), ("order.statut", ASCENDING)])
    db.menus.create_index([("restaurant.id", ASCENDING)], unique=True)
    db.carts.create_index([("user_id", ASCENDING)], unique=True)
except Exception:
    # Best effort; not fatal for dev
    pass

# -----------------------------
# Home / Auth routes
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

    # Users are stored like: { key, user: { id, role, username, password, ... } }
    doc = db.users.find_one({
        "user.role": role,
        "user.username": username,
        "user.password": password
    })
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
    # Same lax cookie as Redis POC
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
# Shared JSON endpoints (order)
# -----------------------------
@app.get("/orders/<string:oid>/json")
def order_json(oid):
    d = db.orders.find_one({"order.id": oid}, {"_id": 0})
    if not d:
        return {"error": "Commande introuvable"}, 404
    o = d.get("order") or {}
    out = norm_order_for_view(o)
    return out

@app.get("/orders/<string:oid>")
def order_json_alias(oid):
    return order_json(oid)

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
        r = m.get("restaurant") or {}
        if not q or q in (r.get("nom") or "").lower() or q in (r.get("zone") or "").lower():
            # Keep same fields used by frontend
            restaurants.append({
                "id_restaurant": r.get("id"),  # compatibility with Redis front list
                "nom": r.get("nom"),
                "zone": r.get("zone"),
            })
    return render_template("client/restaurants.html", restaurants=restaurants)

@app.route("/client/restaurant/<string:restaurant_id>")
@require_login
@role_required("CLIENT")
def client_restaurant_menu(restaurant_id):
    m = db.menus.find_one({"restaurant.id": restaurant_id}, {"_id": 0})
    if not m:
        flash("Restaurant introuvable.")
        return redirect(url_for("client_restaurants"))

    restaurant = m.get("restaurant", {"id_restaurant": restaurant_id, "nom": f"Restaurant {restaurant_id}"})
    menu = m.get("menu") or []

    # Normalize dish keys like Redis POC
    for p in menu:
        if isinstance(p, dict):
            if "pu" in p and "prix" not in p:
                p["prix"] = p["pu"]
            if "nom" not in p:
                p["nom"] = p.get("label") or "Plat"

    # Load current cart (denormalized) by user id
    panier_doc = db.carts.find_one({"user_id": request.user["id"]}, {"_id": 0})
    panier = (panier_doc or {}).get("items") or []

    return render_template("client/restaurant_menu.html", restaurant=restaurant, menu=menu, panier=panier)

@app.route("/client/add_line", methods=["POST"])
@require_login
@role_required("CLIENT")
def client_add_line():
    user_id = request.user["id"]
    restaurant_id = request.form.get("id_restaurant")
    restaurant_name = request.form.get("restaurant_name", "Restaurant")

    # Fetch or create cart
    panier_doc = db.carts.find_one({"user_id": user_id})
    panier = (panier_doc or {}).get("items") or []

    # Enforce single-restaurant cart
    if panier and restaurant_id:
        for it in panier:
            if it.get("id_restaurant") != restaurant_id:
                flash("Vous ne pouvez commander que d'un seul restaurant à la fois. Videz votre panier pour changer de restaurant.")
                return redirect(request.referrer or url_for("client_restaurants"))

    item = {
        "id_plat": request.form["id_plat"],
        "nom": request.form["nom"],
        "pu": float(request.form["pu"]),
        "qty": int(request.form["qty"]),
        "id_restaurant": restaurant_id,
        "restaurant_name": restaurant_name,
    }
    panier.append(item)
    db.carts.update_one(
        {"user_id": user_id},
        {"$set": {"items": panier}},
        upsert=True
    )
    flash("Plat ajouté au panier.")
    return redirect(request.referrer or url_for("client_restaurants"))

@app.route("/client/remove_line", methods=["POST"])
@require_login
@role_required("CLIENT")
def client_remove_line():
    user_id = request.user["id"]
    item_name = request.form.get("item_name")

    panier_doc = db.carts.find_one({"user_id": user_id})
    panier = (panier_doc or {}).get("items") or []
    if item_name:
        panier = [it for it in panier if (it.get("nom") != item_name)]
        db.carts.update_one({"user_id": user_id}, {"$set": {"items": panier}}, upsert=True)
        flash(f"Article '{item_name}' supprimé du panier.")
    return redirect(url_for("client_cart"))

@app.route("/client/cart", methods=["GET", "POST"])
@require_login
@role_required("CLIENT")
def client_cart():
    user_id = request.user["id"]
    panier_doc = db.carts.find_one({"user_id": user_id})
    panier = (panier_doc or {}).get("items") or []

    if request.method == "POST":
        action = (request.form.get("action") or "").strip().lower()

        if action == "clear":
            db.carts.update_one({"user_id": user_id}, {"$set": {"items": []}}, upsert=True)
            flash("Panier vidé avec succès", "success")
            return redirect(url_for("client_cart"))

        elif action == "update":
            cart_data = request.form.get("cart_data")
            try:
                updates = json.loads(cart_data) if cart_data else []
            except Exception:
                updates = []

            by_name = {str(it.get("nom")): it for it in panier}
            new_panier = []
            for u in updates:
                nom = str(u.get("nom"))
                qty = int(u.get("qty", 0))
                pu  = float(u.get("pu", 0))
                if qty <= 0:
                    continue
                if nom in by_name:
                    it = dict(by_name[nom])
                    it["qty"] = qty
                    it["pu"]  = pu
                    new_panier.append(it)
                else:
                    new_panier.append({
                        "id_plat": u.get("id_plat"),
                        "nom": nom,
                        "pu": pu,
                        "qty": qty,
                        "id_restaurant": (panier[0].get("id_restaurant") if panier else None),
                        "restaurant_name": (panier[0].get("restaurant_name") if panier else None),
                    })
            db.carts.update_one({"user_id": user_id}, {"$set": {"items": new_panier}}, upsert=True)
            flash("Panier mis à jour avec succès", "success")
            return redirect(url_for("client_cart"))

        # Else: Create order (same logic / validations)
        adresse = (request.form.get("adresse") or "").strip()
        zone = (request.form.get("zone") or "").strip()
        if not adresse or not zone:
            flash("Adresse et zone requis.")
            return redirect(url_for("client_cart"))
        if not panier:
            flash("Votre panier est vide.")
            return redirect(url_for("client_cart"))

        # Restaurant from cart
        restaurant_id = None
        restaurant_name = None
        for it in panier:
            if "id_restaurant" in it:
                restaurant_id = it["id_restaurant"]
                restaurant_name = it.get("restaurant_name", "Restaurant")
                break
        if not restaurant_id and panier:
            # As fallback: try find in menus
            m = db.menus.find_one({"menu.nom": panier[0].get("nom")}, {"restaurant.id": 1, "restaurant.nom": 1})
            if m and m.get("restaurant"):
                restaurant_id = m["restaurant"]["id"]
                restaurant_name = m["restaurant"]["nom"]

        if not restaurant_id:
            flash("Impossible de déterminer le restaurant. Veuillez réessayer.")
            return redirect(url_for("client_cart"))

        oid = new_order_id()
        total = round(sum(p["pu"] * p["qty"] for p in panier), 2)
        order = {
            "id": oid,
            "version": 1,
            "statut": "CREEE",
            "timestamps": {"creation": now()},
            "zone": zone,
            "livraison": {"adresse": adresse},
            "client": {"id": request.user["id"], "nom": request.user.get("nom") or request.user.get("username")},
            "restaurant": {"id": restaurant_id, "nom": restaurant_name},
            "livreur_assigne": None,
            "livreur_assigne_nom": None,
            "livree_par": None,
            "remuneration": 0.0,
            "montant_total_client": total,
            "lignes": panier,
            "annule_par": None,
            "motif_annulation": None,
            "interets": [],
            "events": [{
                "type": "CREATION",
                "acteur_role": "CLIENT",
                "acteur_id": request.user["id"],
                "details": f"Commande créée par {request.user.get('nom') or request.user['username']}",
                "ts": now()
            }]
        }
        db.orders.insert_one({"key": f"order:{oid}", "order": order})
        # clear cart
        db.carts.update_one({"user_id": user_id}, {"$set": {"items": []}}, upsert=True)
        flash(f"Commande {oid} créée avec succès.")
        return redirect(url_for("client_orders"))

    # GET
    return render_template("client/cart.html", panier=panier)

@app.route("/client/orders")
@require_login
@role_required("CLIENT")
def client_orders():
    my_id = request.user["id"]
    docs = db.orders.find({"order.client.id": my_id}, {"_id": 0})
    commandes = []
    for d in docs:
        o = norm_order_for_view(d.get("order") or {})
        commandes.append(o)
    commandes.sort(key=lambda x: (x.get("timestamps") or {}).get("creation", 0), reverse=True)
    return render_template("client/orders.html", commandes=commandes)

@app.post("/client/cancel/<string:order_id>")
@require_login
@role_required("CLIENT")
def client_cancel(order_id):
    """Client may cancel if status allows; persists motif + who cancelled."""
    d = db.orders.find_one({"order.id": order_id}, {"_id": 0})
    if not d:
        flash("Commande introuvable.")
        return redirect(url_for("client_orders"))
    o = d.get("order") or {}
    # ensure ownership
    if (o.get("client") or {}).get("id") != request.user["id"]:
        flash("Vous ne pouvez pas annuler cette commande.")
        return redirect(url_for("client_orders"))
    if o.get("statut") in ("CREEE", "ANONCEE"):
        motif = request.form.get("motif", "Annulée par le client")
        db.orders.update_one(
            {"order.id": order_id},
            {"$set": {
                "order.statut": "ANNULEE",
                "order.motif_annulation": motif,
                "order.annule_par": "CLIENT",
                "order.timestamps.cloture": now()
            }}
        )
        flash("Commande annulée avec succès.")
    else:
        flash("Impossible d'annuler cette commande (déjà en cours ou livrée).")
    return redirect(url_for("client_orders"))

# -----------------------------
# RESTAURANT
# -----------------------------
@app.route("/restaurant")
@require_login
@role_required("RESTAURANT")
def restaurant_dashboard():
    """Dashboard with optional status filter; mirrors Redis normalization."""
    wanted = (request.args.get("statut") or "").strip()
    rid = request.user["id"]

    docs = db.orders.find({"order.restaurant.id": rid}, {"_id": 0})
    commandes = []
    for d in docs:
        o = d.get("order") or {}
        row = {
            "id_commande": o.get("id") or o.get("id_commande"),
            "zone": o.get("zone"),
            "livraison_adresse": (o.get("livraison") or {}).get("adresse"),
            "montant_total_client": o.get("montant_total_client"),
            "statut": o.get("statut"),
            "id_livreur_assigne": o.get("id_livreur_assigne") or (o.get("livreur") or {}).get("id"),
            "date_creation": (o.get("timestamps") or {}).get("creation") or o.get("date_creation"),
            "id_client": (o.get("client") or {}).get("id"),
        }
        if wanted and row["statut"] != wanted:
            continue
        commandes.append(row)

    commandes.sort(key=lambda x: x.get("date_creation") or 0, reverse=True)
    return render_template("restaurant/dashboard.html", orders=commandes)

@app.route("/restaurant/order/<string:order_id>")
@require_login
@role_required("RESTAURANT")
def restaurant_order_details(order_id):
    d = db.orders.find_one({"order.id": order_id}, {"_id": 0})
    if not d:
        flash("Commande introuvable.")
        return redirect(url_for("restaurant_dashboard"))
    o = norm_order_for_view(d.get("order") or {})
    interets = o.get("interets") or []
    return render_template("restaurant/order_details.html", order=o, lignes=o.get("lignes", []), interets=interets)

@app.post("/restaurant/order/<string:order_id>/publish")
@require_login
@role_required("RESTAURANT")
def restaurant_publish(order_id):
    """Mark order ANONCEE + remuneration; visible to couriers by zone."""
    d = db.orders.find_one({"order.id": order_id}, {"_id": 0})
    if not d:
        flash("Commande introuvable.")
        return redirect(url_for("restaurant_dashboard"))
    remuneration = float(request.form.get("remuneration") or 0)
    db.orders.update_one(
        {"order.id": order_id},
        {"$set": {"order.statut": "ANONCEE", "order.remuneration": remuneration}}
    )
    flash(f"Commande {order_id} publiée avec rémunération {remuneration:.2f} €.")
    return redirect(url_for("restaurant_order_details", order_id=order_id))

@app.post("/restaurant/order/<string:order_id>/cancel")
@require_login
@role_required("RESTAURANT")
def restaurant_cancel(order_id):
    d = db.orders.find_one({"order.id": order_id}, {"_id": 0})
    if not d:
        flash("Commande introuvable.")
        return redirect(url_for("restaurant_dashboard"))
    motif = request.form.get("motif", "Annulation par le restaurant")
    db.orders.update_one(
        {"order.id": order_id},
        {"$set": {
            "order.statut": "ANNULEE",
            "order.motif_annulation": motif,
            "order.annule_par": "RESTAURANT",
            "order.timestamps.cloture": now()
        }}
    )
    flash(f"Commande {order_id} annulée.")
    return redirect(url_for("restaurant_dashboard"))

@app.post("/restaurant/order/<string:order_id>/assign")
@require_login
@role_required("RESTAURANT")
def restaurant_assign(order_id):
    livreur_id = request.form.get("livreur_id")
    if not livreur_id:
        flash("Aucun livreur sélectionné.")
        return redirect(url_for("restaurant_order_details", order_id=order_id))

    d = db.orders.find_one({"order.id": order_id}, {"_id": 0})
    if not d:
        flash("Commande introuvable.")
        return redirect(url_for("restaurant_dashboard"))

    db.orders.update_one(
        {"order.id": order_id},
        {"$set": {"order.id_livreur_assigne": livreur_id, "order.statut": "ASSIGNEE"}}
    )
    flash(f"Livreur {livreur_id} assigné à la commande {order_id}.")
    return redirect(url_for("restaurant_order_details", order_id=order_id))

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
        o = d.get("order") or {}
        annonces.append({
            "id_commande": o.get("id") or o.get("id_commande"),
            "livraison_adresse": (o.get("livraison") or {}).get("adresse"),
            "remuneration": o.get("remuneration", 0.0),
            "statut": o.get("statut"),
            "zone": o.get("zone"),
        })
    annonces.sort(key=lambda x: x.get("id_commande") or "", reverse=True)
    return render_template("livreur/annonces.html", orders=annonces, zone=zone)

@app.post("/livreur/interet/<string:order_id>")
@require_login
@role_required("LIVREUR")
def livreur_interet(order_id):
    action = request.form.get("action")  # 'ajouter' or 'retirer'
    livreur_id = request.user["id"]

    d = db.orders.find_one({"order.id": order_id}, {"_id": 0})
    if not d:
        flash("Commande introuvable.")
        return redirect(url_for("livreur_annonces"))
    o = d.get("order") or {}
    interets = o.get("interets") or []

    if action == "ajouter":
        if any(i.get("id_livreur") == livreur_id for i in interets):
            flash("Vous avez déjà manifesté votre intérêt.")
        else:
            interets.append({
                "id_livreur": livreur_id,
                "ts": now(),
                "temps_estime": request.form.get("temps_estime") or "",
                "commentaire": request.form.get("commentaire") or "",
            })
            db.orders.update_one({"order.id": order_id}, {"$set": {"order.interets": interets}})
            flash("Intérêt ajouté.")
    elif action == "retirer":
        interets = [i for i in interets if i.get("id_livreur") != livreur_id]
        db.orders.update_one({"order.id": order_id}, {"$set": {"order.interets": interets}})
        flash("Intérêt retiré.")

    return redirect(url_for("livreur_annonces"))

@app.route("/livreur/mes_courses")
@require_login
@role_required("LIVREUR")
def livreur_mes_courses():
    lid = request.user["id"]
    docs = db.orders.find({"order.id_livreur_assigne": lid}, {"_id": 0})
    courses = []
    for d in docs:
        o = d.get("order") or {}
        courses.append({
            "id_commande": o.get("id") or o.get("id_commande"),
            "livraison_adresse": (o.get("livraison") or {}).get("adresse"),
            "statut": o.get("statut"),
        })
    courses.sort(key=lambda x: x.get("id_commande") or "", reverse=True)
    return render_template("livreur/mes_courses.html", orders=courses)

@app.post("/livreur/demarrer/<string:order_id>")
@require_login
@role_required("LIVREUR")
def livreur_demarrer(order_id):
    d = db.orders.find_one({"order.id": order_id}, {"_id": 0})
    if not d:
        flash("Commande introuvable.")
        return redirect(url_for("livreur_mes_courses"))
    db.orders.update_one(
        {"order.id": order_id},
        {"$set": {"order.statut": "EN_LIVRAISON", "order.timestamps.demarrage": now()}}
    )
    flash("Livraison démarrée.")
    return redirect(url_for("livreur_mes_courses"))

@app.post("/livreur/terminer/<string:order_id>")
@require_login
@role_required("LIVREUR")
def livreur_terminer(order_id):
    d = db.orders.find_one({"order.id": order_id}, {"_id": 0})
    if not d:
        flash("Commande introuvable.")
        return redirect(url_for("livreur_mes_courses"))
    db.orders.update_one(
        {"order.id": order_id},
        {"$set": {
            "order.statut": "LIVREE",
            "order.timestamps.cloture": now(),
            "order.livree_par_livreur": request.user["id"],
            "order.date_cloture": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now()))
        }}
    )
    flash("Commande livrée avec succès.")
    return redirect(url_for("livreur_mes_courses"))

@app.route("/livreur/historique")
@require_login
@role_required("LIVREUR")
def livreur_historique():
    lid = request.user["id"]
    docs = db.orders.find({"order.statut": "LIVREE", "order.livree_par_livreur": lid}, {"_id": 0})
    histo = []
    for d in docs:
        o = d.get("order") or {}
        histo.append({
            "id_commande": o.get("id") or o.get("id_commande"),
            "livraison_adresse": (o.get("livraison") or {}).get("adresse"),
            "statut": o.get("statut"),
            "date_cloture": o.get("date_cloture"),
        })
    histo.sort(key=lambda x: x.get("date_cloture") or "", reverse=True)
    return render_template("livreur/historique.html", orders=histo)

# -----------------------------
# SSE EVENTS via Mongo Change Streams
# -----------------------------
def map_change_to_event(change: dict) -> dict:
    """
    Map Mongo change stream doc to the event schema used by your frontend.
    We keep structured events similar to your Redis Pub/Sub schema.

    Returns:
      {
        "event": "<created|published|assigned|cancelled|updated|delivered>",
        "channel": "orders.<...>",
        "payload": {...},
        "ts": <epoch>
      }
    """
    ts = now()
    full = (change.get("fullDocument") or {})
    order = full.get("order") or {}
    op = change.get("operationType")

    # Defaults
    ev = "updated"
    channel = "orders.updated"
    payload = {"id": order.get("id")}

    if op == "insert":
        ev = "created"
        channel = "orders.created"
        payload.update({
            "zone": order.get("zone"),
            "id_client": (order.get("client") or {}).get("id"),
            "id_restaurant": (order.get("restaurant") or {}).get("id"),
        })
    elif op == "update":
        upd = change.get("updateDescription", {})
        setf = (upd.get("updatedFields") or {}).keys()
        # Heuristics on the updated fields to pick the semantic event
        if "order.statut" in setf:
            new_status = (upd.get("updatedFields") or {}).get("order.statut")
            if new_status == "ANONCEE":
                ev = "published"; channel = "orders.published"
                payload.update({"zone": order.get("zone"), "remuneration": order.get("remuneration", 0)})
            elif new_status == "ASSIGNEE":
                ev = "assigned"; channel = "orders.assigned"
                payload.update({"livreur": order.get("id_livreur_assigne")})
            elif new_status == "ANNULEE":
                ev = "cancelled"; channel = "orders.cancelled"
                payload.update({"motif": order.get("motif_annulation")})
            elif new_status == "LIVREE":
                ev = "delivered"; channel = "orders.updated"
                payload.update({"livreur": order.get("livree_par_livreur")})
            else:
                ev = "updated"; channel = "orders.updated"
        elif "order.id_livreur_assigne" in setf:
            ev = "assigned"; channel = "orders.assigned"
            payload.update({"livreur": order.get("id_livreur_assigne")})
        else:
            ev = "updated"; channel = "orders.updated"
    else:
        ev = "updated"; channel = "orders.updated"

    return {
        "event": ev,
        "channel": channel,
        "payload": payload,
        "ts": ts
    }

@app.route("/events")
def events():
    """
    Server-Sent Events stream backed by MongoDB change streams.
    Requires Mongo Atlas or a local replica set (change streams need that).
    """
    def stream():
        try:
            pipeline = [
                {"$match": {"operationType": {"$in": ["insert", "update"]}}}
            ]
            with db.orders.watch(pipeline, full_document='updateLookup') as change_stream:
                for change in change_stream:
                    try:
                        event = map_change_to_event(change)
                        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    except Exception as ex:
                        yield f"data: {json.dumps({'event':'error','payload':str(ex),'ts':now()})}\n\n"
        except PyMongoError as e:
            # If change streams not available, degrade gracefully
            yield f"data: {json.dumps({'event':'error','payload':'Change streams unavailable','ts':now()})}\n\n"

    return Response(stream(), mimetype="text/event-stream")

# -----------------------------
# Run
# -----------------------------
if __name__ == "__main__":
    # Optional log so you see connection OK in console
    try:
        client.admin.command('ping')
        print(f"MongoDB connected to DB: {DB_NAME}")
    except Exception as e:
        print("MongoDB connection error:", e)
    app.run(debug=True, port=5002)

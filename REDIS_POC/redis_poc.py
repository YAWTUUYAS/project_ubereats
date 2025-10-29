#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
POC UberEats — Version Redis (FULL, with structured Pub/Sub)
Front partagé : ../frontend/
"""

import os, json, time, uuid
from types import SimpleNamespace
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, flash, make_response, Response
from redis import Redis

# -----------------------------
# Config Redis & Flask
# -----------------------------
REDIS = Redis(host="127.0.0.1", port=6379, decode_responses=True)
APP_SECRET = "change-me-please"

BASE_DIR = os.path.dirname(__file__)
FRONTEND_DIR = os.path.join(BASE_DIR, "../frontend")

app = Flask(
    __name__,
    template_folder=os.path.join(FRONTEND_DIR, "templates"),
    static_folder=os.path.join(FRONTEND_DIR, "static"),
)
app.secret_key = APP_SECRET


# -----------------------------
# Pub/Sub channels (constants)
# -----------------------------
CHANNEL_ORDER_CREATED   = "orders.created"
CHANNEL_ORDER_PUBLISHED = "orders.published"
CHANNEL_ORDER_ASSIGNED  = "orders.assigned"
CHANNEL_ORDER_CANCELLED = "orders.cancelled"
CHANNEL_ORDER_UPDATED   = "orders.updated"   # generic fallback if needed


def rpub(channel, event_type, payload):
    """
    Publish structured events across Redis Pub/Sub.

    event schema:
      {
        "event": <event_type>,        # e.g. "created", "published"
        "channel": <channel>,         # e.g. "orders.created"
        "payload": { ... },           # event-specific data
        "ts": <epoch seconds>         # server timestamp
      }
    """
    REDIS.publish(channel, json.dumps({
        "event": event_type,
        "channel": channel,
        "payload": payload,
        "ts": int(time.time())
    }, ensure_ascii=False))


def rsub(pattern="orders.*"):
    """
    Subscribe to a Pub/Sub pattern and yield structured events.
    Intended to be used by SSE stream.
    """
    ps = REDIS.pubsub()
    ps.psubscribe(pattern)
    for msg in ps.listen():
        if msg["type"] not in ("message", "pmessage"):
            continue
        data = msg["data"]
        if isinstance(data, bytes):
            data = data.decode("utf-8")
        try:
            yield json.loads(data)
        except Exception:
            # if legacy messages were published as plain JSON payloads
            yield {"event": "raw", "channel": msg.get("channel") or msg.get("pattern"), "payload": data, "ts": int(time.time())}


# -----------------------------
# Jinja2 context
# -----------------------------
@app.context_processor
def inject_session_user():
    """Simule session.user dans les templates (comme dans le POC SQL)."""
    auth = request.cookies.get("auth")
    user = None
    if auth:
        try:
            u = json.loads(auth)
            user = SimpleNamespace(**u)
        except Exception:
            pass
    session_obj = SimpleNamespace(user=user)
    return {"session": session_obj}


# -----------------------------
# Redis Helpers
# -----------------------------
def now(): return int(time.time())
def new_order_id(): return "cmd_" + uuid.uuid4().hex[:8]

def k_user(role, uid): return f"user:{role}:{uid}"
def k_user_index(role): return f"user:index:{role}"
def k_menu(rid): return f"menu:{rid}"
def k_order(oid): return f"order:{oid}"

def load_json(k):
    s = REDIS.get(k)
    return json.loads(s) if s else None

def save_json(k, obj):
    REDIS.set(k, json.dumps(obj, ensure_ascii=False))


# -----------------------------
# Auth middleware
# -----------------------------
@app.before_request
def inject_user_from_cookie():
    auth = request.cookies.get("auth")
    if not auth:
        request.user = None
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
# Auth routes
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

    uid = REDIS.hget(k_user_index(role), username)
    if not uid:
        flash("Utilisateur inconnu.")
        return render_template("login.html")

    u = load_json(k_user(role, uid))
    if not u or u.get("password") != password:
        flash("Identifiants invalides.")
        return render_template("login.html")

    auth_data = {
        "id": u["id"],
        "role": role,
        "username": u["username"],
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
# Shared JSON order endpoints
# -----------------------------
@app.get("/orders/<string:oid>/json")
def order_json(oid):
    o = load_json(k_order(oid))
    if not o:
        return {"error": "Commande introuvable"}, 404

    # Provide a minimal normalized view for the frontend
    out = o.copy()
    if "id_commande" not in out and "id" in out:
        out["id_commande"] = out["id"]
    if "livraison_adresse" not in out and "livraison" in out:
        out["livraison_adresse"] = (out.get("livraison") or {}).get("adresse")
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
    for k in REDIS.scan_iter("menu:*"):
        rid = k.split(":")[-1]
        data = load_json(k)
        if not data:
            continue
        if isinstance(data, dict) and "restaurant" in data:
            r = data["restaurant"]
        else:
            r = {"id_restaurant": rid, "nom": rid, "zone": "inconnue"}
        if not q or q in r.get("nom", "").lower() or q in r.get("zone", "").lower():
            restaurants.append(r)
    return render_template("client/restaurants.html", restaurants=restaurants)

@app.route("/client/restaurant/<string:restaurant_id>")
@require_login
@role_required("CLIENT")
def client_restaurant_menu(restaurant_id):
    data = load_json(k_menu(restaurant_id))
    if not data:
        flash("Restaurant introuvable.")
        return redirect(url_for("client_restaurants"))

    if isinstance(data, dict) and "menu" in data:
        restaurant = data.get("restaurant", {"id_restaurant": restaurant_id, "nom": f"Restaurant {restaurant_id}"})
        menu = data["menu"]
    else:
        restaurant = {"id_restaurant": restaurant_id, "nom": f"Restaurant {restaurant_id}"}
        menu = data if isinstance(data, list) else []

    # S'assurer que le restaurant a un nom
    if "nom" not in restaurant:
        restaurant["nom"] = f"Restaurant {restaurant_id}"

    # Normalize dishes
    for p in menu:
        if isinstance(p, dict):
            if "pu" in p and "prix" not in p:
                p["prix"] = p["pu"]
            if "nom" not in p:
                p["nom"] = p.get("label") or "Plat"

    panier = load_json(f"panier:{request.user['id']}") or []
    return render_template("client/restaurant_menu.html", restaurant=restaurant, menu=menu, panier=panier)

@app.route("/client/add_line", methods=["POST"])
@require_login
@role_required("CLIENT")
def client_add_line():
    panier_key = f"panier:{request.user['id']}"
    panier = load_json(panier_key) or []

    # Enforce single-restaurant cart
    restaurant_id = request.form.get("id_restaurant")
    if panier and restaurant_id:
        for item in panier:
            if item.get("id_restaurant") != restaurant_id:
                flash("Vous ne pouvez commander que d'un seul restaurant à la fois. Videz votre panier pour changer de restaurant.")
                return redirect(request.referrer or url_for("client_restaurants"))

    panier.append({
        "id_plat": request.form["id_plat"],
        "nom": request.form["nom"],
        "pu": float(request.form["pu"]),
        "qty": int(request.form["qty"]),
        "id_restaurant": restaurant_id,
        "restaurant_name": request.form.get("restaurant_name", "Restaurant")
    })
    save_json(panier_key, panier)
    flash("Plat ajouté au panier.")
    return redirect(request.referrer or url_for("client_restaurants"))

@app.route("/client/remove_line", methods=["POST"])
@require_login
@role_required("CLIENT")
def client_remove_line():
    """Supprimer un article du panier"""
    panier_key = f"panier:{request.user['id']}"
    panier = load_json(panier_key) or []

    item_name = request.form.get("item_name")
    if item_name:
        panier = [item for item in panier if item.get("nom") != item_name]
        save_json(panier_key, panier)
        flash(f"Article '{item_name}' supprimé du panier.")
    return redirect(url_for("client_cart"))

@app.route("/client/cart", methods=["GET", "POST"])
@require_login
@role_required("CLIENT")
def client_cart():
    panier_key = f"panier:{request.user['id']}"
    panier = load_json(panier_key) or []

    if request.method == "POST":
        # Possible actions: clear | update | (else -> create order)
        action = (request.form.get("action") or "").strip().lower()

        if action == "clear":
            save_json(panier_key, [])
            flash("Panier vidé avec succès", "success")
            return redirect(url_for("client_cart"))

        elif action == "update":
            cart_data = request.form.get("cart_data")
            try:
                updates = json.loads(cart_data) if cart_data else []
            except Exception:
                updates = []
            # Preserve metadata by item name
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
            save_json(panier_key, new_panier)
            flash("Panier mis à jour avec succès", "success")
            return redirect(url_for("client_cart"))

        # Create order
        adresse = request.form.get("adresse", "").strip()
        zone = request.form.get("zone", "").strip()
        if not adresse or not zone:
            flash("Adresse et zone requis.")
            return redirect(url_for("client_cart"))
        if not panier:
            flash("Votre panier est vide.")
            return redirect(url_for("client_cart"))

        # Find restaurant from cart items
        restaurant_id = None
        restaurant_name = None
        for item in panier:
            if "id_restaurant" in item:
                restaurant_id = item["id_restaurant"]
                restaurant_name = item.get("restaurant_name", "Restaurant")
                break

        if not restaurant_id and panier:
            # Deduce from menus in Redis if needed
            for k in REDIS.scan_iter("menu:*"):
                menu_data = load_json(k)
                if menu_data and isinstance(menu_data, dict) and "menu" in menu_data:
                    menu_items = menu_data["menu"]
                    for menu_item in menu_items:
                        for panier_item in panier:
                            if (menu_item.get("id_plat") == panier_item.get("id_plat") or 
                                menu_item.get("nom") == panier_item.get("nom")):
                                restaurant_id = menu_data["restaurant"]["id"]
                                restaurant_name = menu_data["restaurant"]["nom"]
                                break
                        if restaurant_id:
                            break
                if restaurant_id:
                    break

        if not restaurant_id:
            flash("Impossible de déterminer le restaurant. Veuillez réessayer.")
            return redirect(url_for("client_cart"))

        oid = new_order_id()
        total = round(sum(p["pu"] * p["qty"] for p in panier), 2)
        o = {
            "id": oid,
            "version": 1,
            "statut": "CREEE",
            "timestamps": {"creation": now()},
            "zone": zone,
            "livraison": {"adresse": adresse},
            "client": {"id": request.user["id"], "nom": request.user["nom"] or request.user["username"]},
            "restaurant": {"id": restaurant_id, "nom": restaurant_name},
            "livreur_assigne": None,
            "livreur_assigne_nom": None,
            "livree_par": None,
            "remuneration": 0.0,
            "montant_total_client": total,
            "lignes": panier,
            "annule_par": None,
            "motif_annulation": None,
            "interets": {},
            "events": [{
                "type": "CREATION",
                "acteur_role": "CLIENT",
                "acteur_id": request.user["id"],
                "details": f"Commande créée par {request.user['nom'] or request.user['username']}",
                "ts": now()
            }]
        }
        save_json(k_order(oid), o)
        save_json(panier_key, [])
        # Pub/Sub
        rpub(CHANNEL_ORDER_CREATED, "created", {"id": oid, "zone": zone, "id_client": request.user["id"], "id_restaurant": restaurant_id})
        flash(f"Commande {oid} créée avec succès.")
        return redirect(url_for("client_orders"))

    # GET
    return render_template("client/cart.html", panier=panier)

@app.route("/client/orders")
@require_login
@role_required("CLIENT")
def client_orders():
    commandes = []
    for k in REDIS.scan_iter("order:*"):
        o = load_json(k)
        if o and o.get("client", {}).get("id") == request.user["id"]:
            if "id_commande" not in o and "id" in o:
                o["id_commande"] = o["id"]
            if "id_restaurant" not in o and "restaurant" in o:
                o["id_restaurant"] = o["restaurant"].get("id")
            commandes.append(o)
    commandes.sort(key=lambda x: x.get("timestamps", {}).get("creation", 0), reverse=True)
    return render_template("client/orders.html", commandes=commandes)

@app.post("/client/cancel/<string:order_id>")
@require_login
@role_required("CLIENT")
def client_cancel(order_id):
    """Permet au client d'annuler une commande s'il est autorisé."""
    o = load_json(k_order(order_id))
    if not o:
        flash("Commande introuvable.")
        return redirect(url_for("client_orders"))

    if o.get("client", {}).get("id") != request.user["id"]:
        flash("Vous ne pouvez pas annuler cette commande.")
        return redirect(url_for("client_orders"))

    if o.get("statut") in ("CREEE", "ANONCEE"):
        o["statut"] = "ANNULEE"
        o["motif_annulation"] = request.form.get("motif", "Annulée par le client")
        o["timestamps"]["cloture"] = now()
        o["annule_par"] = "CLIENT"
        save_json(k_order(order_id), o)
        rpub(CHANNEL_ORDER_CANCELLED, "cancelled", {"id": order_id, "client": request.user["id"], "motif": o["motif_annulation"]})
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
    """Tableau de bord du restaurant avec filtre par statut (compatible front SQL)."""
    wanted = (request.args.get("statut") or "").strip()  # ex: 'ANNULEE', 'CREEE', ...
    commandes = []

    for k in REDIS.scan_iter("order:*"):
        o = load_json(k)
        if not o:
            continue

        # Filtre par restaurant
        rest_id = (o.get("restaurant") or {}).get("id")
        if rest_id != request.user["id"]:
            continue

        # Normalisation -> format attendu par les templates SQL
        row = {
            "id_commande": o.get("id") or o.get("id_commande"),
            "zone": o.get("zone"),
            "livraison_adresse": (o.get("livraison") or {}).get("adresse"),
            "montant_total_client": o.get("montant_total_client"),
            "statut": o.get("statut"),
            "id_livreur_assigne": (o.get("livreur") or {}).get("id") or o.get("id_livreur_assigne"),
            "date_creation": (o.get("timestamps") or {}).get("creation") or o.get("date_creation"),
            # Optionally expose client for list if your template wants it:
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
    o = load_json(k_order(order_id))
    if not o:
        flash("Commande introuvable.")
        return redirect(url_for("restaurant_dashboard"))

    # ✅ Normalize keys for template compatibility
    if "id_commande" not in o and "id" in o:
        o["id_commande"] = o["id"]
    if "livraison_adresse" not in o and "livraison" in o:
        o["livraison_adresse"] = (o.get("livraison") or {}).get("adresse")
    if o.get("client"):
        o["id_client"] = o["client"].get("id")
        o["nom_client"] = o["client"].get("nom")
    else:
        o["id_client"] = None
        o["nom_client"] = None

    lignes = o.get("lignes", [])
    interets = load_json(f"interets:{order_id}") or []

    for l in lignes:
        if "quantite" not in l and "qty" in l:
            l["quantite"] = l["qty"]
        if "prix_unitaire" not in l and "pu" in l:
            l["prix_unitaire"] = l["pu"]

    return render_template("restaurant/order_details.html", order=o, lignes=lignes, interets=interets)

@app.post("/restaurant/order/<string:order_id>/publish")
@require_login
@role_required("RESTAURANT")
def restaurant_publish(order_id):
    """Publier une commande pour les livreurs"""
    o = load_json(k_order(order_id))
    if not o:
        flash("Commande introuvable.")
        return redirect(url_for("restaurant_dashboard"))

    o["statut"] = "ANONCEE"
    o["remuneration"] = float(request.form.get("remuneration") or 0)
    save_json(k_order(order_id), o)
    rpub(CHANNEL_ORDER_PUBLISHED, "published", {"id": order_id, "zone": o.get("zone"), "remuneration": o["remuneration"]})
    flash(f"Commande {order_id} publiée avec rémunération {o['remuneration']} €.")
    return redirect(url_for("restaurant_order_details", order_id=order_id))

@app.post("/restaurant/order/<string:order_id>/cancel")
@require_login
@role_required("RESTAURANT")
def restaurant_cancel(order_id):
    """Annule une commande"""
    o = load_json(k_order(order_id))
    if not o:
        flash("Commande introuvable.")
        return redirect(url_for("restaurant_dashboard"))

    o["statut"] = "ANNULEE"
    o["motif_annulation"] = request.form.get("motif", "Annulation par le restaurant")
    o["timestamps"] = o.get("timestamps") or {}
    o["timestamps"]["cloture"] = now()
    o["annule_par"] = "RESTAURANT"
    save_json(k_order(order_id), o)
    rpub(CHANNEL_ORDER_CANCELLED, "cancelled", {"id": order_id, "motif": o["motif_annulation"], "restaurant": request.user["id"]})
    flash(f"Commande {order_id} annulée.")
    return redirect(url_for("restaurant_dashboard"))

@app.post("/restaurant/order/<string:order_id>/assign")
@require_login
@role_required("RESTAURANT")
def restaurant_assign(order_id):
    """Assigne un livreur à la commande"""
    o = load_json(k_order(order_id))
    if not o:
        flash("Commande introuvable.")
        return redirect(url_for("restaurant_dashboard"))

    livreur_id = request.form.get("livreur_id")
    if not livreur_id:
        flash("Aucun livreur sélectionné.")
        return redirect(url_for("restaurant_order_details", order_id=order_id))

    o["id_livreur_assigne"] = livreur_id
    o["statut"] = "ASSIGNEE"
    save_json(k_order(order_id), o)
    rpub(CHANNEL_ORDER_ASSIGNED, "assigned", {"id": order_id, "livreur": livreur_id})
    flash(f"Livreur {livreur_id} assigné à la commande {order_id}.")
    return redirect(url_for("restaurant_order_details", order_id=order_id))


# -----------------------------
# LIVREUR
# -----------------------------
@app.route("/livreur")
@require_login
@role_required("LIVREUR")
def livreur_dashboard():
    """Page d’accueil du livreur"""
    return render_template("livreur/dashboard.html")

@app.route("/livreur/annonces")
@require_login
@role_required("LIVREUR")
def livreur_annonces():
    """Affiche les commandes ANONCÉES dans la zone du livreur"""
    zone = request.user.get("zone")
    annonces = []

    for k in REDIS.scan_iter("order:*"):
        o = load_json(k)
        if not o:
            continue
        if o.get("statut") == "ANONCEE" and o.get("zone") == zone:
            # Normalisation pour compatibilité front
            annonce = {
                "id_commande": o.get("id") or o.get("id_commande"),
                "livraison_adresse": (o.get("livraison") or {}).get("adresse"),
                "remuneration": o.get("remuneration", 0.0),
                "statut": o.get("statut"),
                "zone": o.get("zone"),
            }
            annonces.append(annonce)

    annonces.sort(key=lambda x: x.get("id_commande", ""), reverse=True)
    return render_template("livreur/annonces.html", orders=annonces, zone=zone)

@app.post("/livreur/interet/<string:order_id>")
@require_login
@role_required("LIVREUR")
def livreur_interet(order_id):
    """Ajoute ou retire l'intérêt d'un livreur pour une commande ANONCÉE"""
    action = request.form.get("action")  # 'ajouter' ou 'retirer'
    livreur_id = request.user["id"]
    interets_key = f"interets:{order_id}"
    interets = load_json(interets_key) or []

    if action == "ajouter":
        if any(i["id_livreur"] == livreur_id for i in interets):
            flash("Vous avez déjà manifesté votre intérêt.")
        else:
            interets.append({
                "id_livreur": livreur_id,
                "ts": now(),
                "temps_estime": request.form.get("temps_estime") or "",
                "commentaire": request.form.get("commentaire") or "",
            })
            save_json(interets_key, interets)
            rpub(CHANNEL_ORDER_UPDATED, "interest_added", {"id": order_id, "livreur": livreur_id})
            flash("Intérêt ajouté.")

    elif action == "retirer":
        interets = [i for i in interets if i["id_livreur"] != livreur_id]
        save_json(interets_key, interets)
        rpub(CHANNEL_ORDER_UPDATED, "interest_removed", {"id": order_id, "livreur": livreur_id})
        flash("Intérêt retiré.")
    return redirect(url_for("livreur_annonces"))

@app.route("/livreur/mes_courses")
@require_login
@role_required("LIVREUR")
def livreur_mes_courses():
    """Commandes assignées à ce livreur"""
    livreur_id = request.user["id"]
    courses = []

    for k in REDIS.scan_iter("order:*"):
        o = load_json(k)
        if not o:
            continue
        if o.get("id_livreur_assigne") == livreur_id:
            course = {
                "id_commande": o.get("id") or o.get("id_commande"),
                "livraison_adresse": (o.get("livraison") or {}).get("adresse"),
                "statut": o.get("statut"),
            }
            courses.append(course)

    courses.sort(key=lambda x: x.get("id_commande", ""), reverse=True)
    return render_template("livreur/mes_courses.html", orders=courses)

@app.post("/livreur/demarrer/<string:order_id>")
@require_login
@role_required("LIVREUR")
def livreur_demarrer(order_id):
    """Le livreur commence la livraison"""
    o = load_json(k_order(order_id))
    if not o:
        flash("Commande introuvable.")
        return redirect(url_for("livreur_mes_courses"))

    o["statut"] = "EN_LIVRAISON"
    o.setdefault("timestamps", {})["demarrage"] = now()
    save_json(k_order(order_id), o)
    rpub(CHANNEL_ORDER_UPDATED, "delivery_started", {"id": order_id, "livreur": request.user["id"]})
    flash("Livraison démarrée.")
    return redirect(url_for("livreur_mes_courses"))

@app.post("/livreur/terminer/<string:order_id>")
@require_login
@role_required("LIVREUR")
def livreur_terminer(order_id):
    """Le livreur marque la commande comme livrée"""
    o = load_json(k_order(order_id))
    if not o:
        flash("Commande introuvable.")
        return redirect(url_for("livreur_mes_courses"))

    o["statut"] = "LIVREE"
    o.setdefault("timestamps", {})["cloture"] = now()
    o["livree_par_livreur"] = request.user["id"]
    o["date_cloture"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now()))
    save_json(k_order(order_id), o)
    rpub(CHANNEL_ORDER_UPDATED, "delivered", {"id": order_id, "livreur": request.user["id"]})
    flash("Commande livrée avec succès.")
    return redirect(url_for("livreur_mes_courses"))

@app.route("/livreur/historique")
@require_login
@role_required("LIVREUR")
def livreur_historique():
    """Commandes livrées par ce livreur"""
    livreur_id = request.user["id"]
    histo = []

    for k in REDIS.scan_iter("order:*"):
        o = load_json(k)
        if not o:
            continue
        if o.get("statut") == "LIVREE" and o.get("livree_par_livreur") == livreur_id:
            histo.append({
                "id_commande": o.get("id") or o.get("id_commande"),
                "livraison_adresse": (o.get("livraison") or {}).get("adresse"),
                "statut": o.get("statut"),
                "date_cloture": o.get("date_cloture"),
            })

    histo.sort(key=lambda x: x.get("date_cloture") or "", reverse=True)
    return render_template("livreur/historique.html", orders=histo)


# -----------------------------
# SSE EVENTS — Unified subscriber
# -----------------------------
@app.route("/events")
def events():
    def stream():
        for event in rsub("orders.*"):
            try:
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'event': 'error', 'payload': str(e), 'ts': int(time.time())})}\n\n"
    return Response(stream(), mimetype="text/event-stream")


# -----------------------------
# Run
# -----------------------------
if __name__ == "__main__":
    app.run(debug=True, port=5001)

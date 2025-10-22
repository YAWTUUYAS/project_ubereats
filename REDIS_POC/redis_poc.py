#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
POC UberEats — Version Redis
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
# Injection du contexte utilisateur dans Jinja2
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

def rpub(channel, payload):
    REDIS.publish(channel, json.dumps(payload, ensure_ascii=False))

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
        restaurant = data.get("restaurant", {"id_restaurant": restaurant_id})
        menu = data["menu"]
    else:
        restaurant = {"id_restaurant": restaurant_id}
        menu = data if isinstance(data, list) else []

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
    panier.append({
        "id_plat": request.form["id_plat"],
        "nom": request.form["nom"],
        "pu": float(request.form["pu"]),
        "qty": int(request.form["qty"]),
    })
    save_json(panier_key, panier)
    flash("Plat ajouté au panier.")
    return redirect(request.referrer or url_for("client_restaurants"))

@app.route("/client/cart", methods=["GET", "POST"])
@require_login
@role_required("CLIENT")
def client_cart():
    panier_key = f"panier:{request.user['id']}"
    panier = load_json(panier_key) or []
    if request.method == "POST":
        adresse = request.form.get("adresse", "").strip()
        zone = request.form.get("zone", "").strip()
        rid = request.form.get("id_restaurant", "").strip()
        if not adresse or not zone or not rid:
            flash("Adresse, zone et restaurant requis.")
            return redirect(url_for("client_cart"))

        oid = new_order_id()
        total = round(sum(p["pu"] * p["qty"] for p in panier), 2)
        o = {
            "id": oid,
            "statut": "CREEE",
            "timestamps": {"creation": now()},
            "zone": zone,
            "livraison": {"adresse": adresse},
            "client": {"id": request.user["id"], "nom": request.user["username"]},
            "restaurant": {"id": rid},
            "montant_total_client": total,
            "lignes": panier,
        }
        save_json(k_order(oid), o)
        save_json(panier_key, [])
        rpub("orders.created", {"id": oid, "zone": zone})
        flash(f"Commande {oid} créée.")
        return redirect(url_for("client_orders"))
    return render_template("client/cart.html", panier=panier)

@app.route("/client/orders")
@require_login
@role_required("CLIENT")
def client_orders():
    commandes = []
    for k in REDIS.scan_iter("order:*"):
        o = load_json(k)
        if o and o.get("client", {}).get("id") == request.user["id"]:
            # Adapter les clés pour le front SQL
            if "id_commande" not in o and "id" in o:
                o["id_commande"] = o["id"]
            if "id_restaurant" not in o and "restaurant" in o:
                o["id_restaurant"] = o["restaurant"].get("id")
            commandes.append(o)

    commandes.sort(key=lambda x: x.get("timestamps", {}).get("creation", 0), reverse=True)
    return render_template("client/orders.html", commandes=commandes)


# -----------------------------
# ACTIONS CLIENT (annuler commande)
# -----------------------------
@app.post("/client/cancel/<string:order_id>")
@require_login
@role_required("CLIENT")
def client_cancel(order_id):
    """Permet au client d'annuler une commande s'il est autorisé."""
    o = load_json(k_order(order_id))
    if not o:
        flash("Commande introuvable.")
        return redirect(url_for("client_orders"))

    # Vérifie que la commande appartient bien au client connecté
    if o.get("client", {}).get("id") != request.user["id"]:
        flash("Vous ne pouvez pas annuler cette commande.")
        return redirect(url_for("client_orders"))

    if o.get("statut") in ("CREEE", "ANONCEE"):
        o["statut"] = "ANNULEE"
        o["motif_annulation"] = request.form.get("motif", "Annulée par le client")
        o["timestamps"]["cloture"] = now()
        save_json(k_order(order_id), o)
        rpub("orders.cancelled", {"id": order_id, "client": request.user["id"]})
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

        # Filtre par restaurant (schéma Redis: o["restaurant"]["id"])
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
            # On fournit aussi date_creation pour le tri et pour d’éventuels affichages
            "date_creation": (o.get("timestamps") or {}).get("creation") or o.get("date_creation"),
        }

        # Appliquer le filtre sur le statut s'il est demandé
        if wanted and row["statut"] != wanted:
            continue

        commandes.append(row)

    # Tri décroissant par date_creation (fallback 0 si manquant)
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

    # ✅ Adapter les noms pour le front
    if "id_commande" not in o and "id" in o:
        o["id_commande"] = o["id"]
    if "livraison_adresse" not in o and "livraison" in o:
        o["livraison_adresse"] = o["livraison"].get("adresse")

    lignes = o.get("lignes", [])
    interets = load_json(f"interets:{order_id}") or []

    for l in lignes:
        if "quantite" not in l and "qty" in l:
            l["quantite"] = l["qty"]
        if "prix_unitaire" not in l and "pu" in l:
            l["prix_unitaire"] = l["pu"]

    return render_template("restaurant/order_details.html", order=o, lignes=lignes, interets=interets)


# -----------------------------
# ACTIONS DU RESTAURANT (publish / cancel / assign)
# -----------------------------
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
    rpub("orders.published", {"id": order_id, "zone": o.get("zone"), "remuneration": o["remuneration"]})
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
    save_json(k_order(order_id), o)
    rpub("orders.cancelled", {"id": order_id})
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
    rpub("orders.assigned", {"id": order_id, "livreur": livreur_id})
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
            interets.append({"id_livreur": livreur_id, "ts": now()})
            save_json(interets_key, interets)
            flash("Intérêt ajouté.")
    elif action == "retirer":
        interets = [i for i in interets if i["id_livreur"] != livreur_id]
        save_json(interets_key, interets)
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
# SSE EVENTS
# -----------------------------
@app.route("/events")
def events():
    def stream():
        ps = REDIS.pubsub()
        ps.psubscribe("orders.*")
        for msg in ps.listen():
            if msg["type"] not in ("message", "pmessage"):
                continue
            data = msg["data"]
            if isinstance(data, bytes):
                data = data.decode("utf-8")
            yield f"data: {data}\n\n"
    return Response(stream(), mimetype="text/event-stream")

# -----------------------------
# Run
# -----------------------------
if __name__ == "__main__":
    app.run(debug=True, port=5001)

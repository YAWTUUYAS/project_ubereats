#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
redis_poc.py — POC UberEats full Redis (agrégats + Pub/Sub + UI proche de app.py SQL)

Données attendues côté Redis (min) :
- Users (POC en clair) :
    SET user:CLIENT:{id} -> {"id","role","username","password"}
    HSET user:index:CLIENT username -> {id}
    (idem RESTAURANT / LIVREUR)
- Menus :
    SET menu:{rest_id} -> [{"id_plat":"...","nom":"...","pu":8.5}, ...]
- Commandes (agrégats dénormalisés) :
    SET order:{id} -> {...doc...}

Index Redis :
- ZSET zone:{zone}:annonces
- SET interest:by_order:{id}
- SET interest:by_courier:{livreur}
- ZSET courier:{livreur}:assigned

Pub/Sub (SSE sur /events) :
- orders.created
- orders.published.{zone}
- interests.created / interests.removed
- orders.assigned
- orders.started
- orders.delivered
- orders.canceled
"""

import json, time, uuid
from functools import wraps
from flask import Flask, request, redirect, url_for, render_template_string, flash, Response
from redis import Redis

# -----------------------------
# Config
# -----------------------------
REDIS = Redis(host="127.0.0.1", port=6379, decode_responses=True)
APP_SECRET = "change-me-please"

# -----------------------------
# Helpers
# -----------------------------
def now() -> int: return int(time.time())
def k_user(role, uid): return f"user:{role}:{uid}"
def k_user_index(role): return f"user:index:{role}"
def k_menu(rest_id): return f"menu:{rest_id}"
def k_order(oid): return f"order:{oid}"
def k_zone_annonces(zone): return f"zone:{zone}:annonces"
def k_interest_by_order(oid): return f"interest:by_order:{oid}"
def k_interest_by_courier(lid): return f"interest:by_courier:{lid}"
def k_courier_assigned(lid): return f"courier:{lid}:assigned"

def load_json(key):
    s = REDIS.get(key)
    return json.loads(s) if s else None

def save_json(key, obj):
    REDIS.set(key, json.dumps(obj, ensure_ascii=False))

# !!! RENOMMÉ pour éviter le conflit avec la route publish() !!!
def rpub(channel, payload):
    REDIS.publish(channel, json.dumps(payload, ensure_ascii=False))

def require_login(f):
    @wraps(f)
    def w(*a, **kw):
        if not request.cookies.get("auth"):
            return redirect(url_for("login"))
        try:
            auth = json.loads(request.cookies["auth"])
            request.user = auth # {"role","id","username"}
        except Exception:
            return redirect(url_for("login"))
        return f(*a, **kw)
    return w

def role_required(*roles):
    def deco(f):
        @wraps(f)
        def w(*a, **kw):
            if not getattr(request, "user", None):
                return redirect(url_for("login"))
            if request.user["role"] not in roles:
                flash("Accès refusé pour votre rôle.")
                return redirect(url_for("home"))
            return f(*a, **kw)
        return w
    return deco

def new_order_id():
    return "cmd_" + uuid.uuid4().hex[:8]

# -----------------------------
# Flask app + templates
# -----------------------------
app = Flask(__name__)
app.secret_key = APP_SECRET

BASE = """
<!doctype html><html lang="fr"><head>
<meta charset="utf-8"><title>Ubereats POC (Redis)</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css">
<style>body{padding:24px} .muted{opacity:.7}</style></head><body>
<nav class="mb-3 d-flex gap-2">
  <a class="btn btn-outline-secondary btn-sm" href="{{ url_for('home') }}">Accueil</a>
  {% if current_user %}
    {% if current_user.role == 'CLIENT' %}
      <a class="btn btn-outline-primary btn-sm" href="{{ url_for('client_dashboard') }}">Espace Client</a>
    {% elif current_user.role == 'RESTAURANT' %}
      <a class="btn btn-outline-primary btn-sm" href="{{ url_for('restaurant_dashboard') }}">Espace Restaurant</a>
    {% elif current_user.role == 'LIVREUR' %}
      <a class="btn btn-outline-success btn-sm" href="{{ url_for('livreur_dashboard') }}">Espace Livreur</a>
    {% endif %}
    <a class="btn btn-outline-danger btn-sm" href="{{ url_for('logout') }}">Se déconnecter ({{ current_user.username }})</a>
  {% else %}
    <a class="btn btn-outline-primary btn-sm" href="{{ url_for('login') }}">Se connecter</a>
  {% endif %}
  <a class="btn btn-outline-dark btn-sm" href="{{ url_for('events_page') }}">Événements (SSE)</a>
</nav>
<div class="container">
  {% with messages = get_flashed_messages() %}
    {% if messages %}<div class="alert alert-info">{{ messages[0] }}</div>{% endif %}
  {% endwith %}
  {% block content %}{% endblock %}
</div>
<script>
if (!!window.EventSource) {
  const es = new EventSource("/events");
  es.onmessage = (e) => { try { console.log("SSE:", JSON.parse(e.data)); } catch(_){} };
}
</script>
</body></html>
"""
HOME = """
{% extends "base.html" %}{% block content %}
<h3>Accueil</h3>
<p class="text-muted">Bienvenue sur le POC Redis (agrégats + Pub/Sub). Connectez-vous pour accéder à votre espace.</p>
<ul>
  <li>Client : <code>carlos / secret</code> (ou <code>marie / secret</code>)</li>
  <li>Restaurant : <code>roma / secret</code> (ou <code>zen / secret</code>)</li>
  <li>Livreur : <code>lina / secret</code>, <code>yassine / secret</code>, <code>marco / secret</code></li>
</ul>
{% endblock %}
"""
LOGIN = """
{% extends "base.html" %}{% block content %}
<h3>Se connecter</h3>
<form method="post" class="row g-3" style="max-width:420px">
  <div class="col-12">
    <label class="form-label">Rôle</label>
    <select class="form-select" name="role" required>
      <option value="CLIENT">CLIENT</option>
      <option value="RESTAURANT">RESTAURANT</option>
      <option value="LIVREUR">LIVREUR</option>
    </select>
  </div>
  <div class="col-12">
    <label class="form-label">Nom d'utilisateur</label>
    <input class="form-control" name="username" required>
  </div>
  <div class="col-12">
    <label class="form-label">Mot de passe</label>
    <input type="password" class="form-control" name="password" required>
  </div>
  <div class="col-12">
    <button class="btn btn-primary">Connexion</button>
  </div>
</form>
{% endblock %}
"""
CLIENT_DASH = """
{% extends "base.html" %}{% block content %}
<h3>Espace Client</h3>
<div class="row g-3">
  <div class="col-md-6">
    <h5>1) Choisir un restaurant</h5>
    <form method="get" class="row g-2" action="{{ url_for('client_dashboard') }}">
      <div class="col-8"><input class="form-control" name="rest" placeholder="ex: rest_001" value="{{ rest or '' }}"></div>
      <div class="col-4"><button class="btn btn-secondary btn-sm">Charger menu</button></div>
    </form>
    {% if menu is not none %}
      <div class="mt-3">
        <h6>Menu du restaurant {{ rest }}</h6>
        {% if menu %}
          <ul class="list-group">
            {% for p in menu %}
              <li class="list-group-item d-flex justify-content-between align-items-center">
                {{ p['nom'] }} — {{ '%.2f'|format(p['pu']) }} €
                <form method="post" action="{{ url_for('client_add_line') }}" class="d-flex gap-2">
                  <input type="hidden" name="rest" value="{{ rest }}">
                  <input type="hidden" name="id_plat" value="{{ p['id_plat'] }}">
                  <input type="hidden" name="nom" value="{{ p['nom'] }}">
                  <input type="hidden" name="pu" value="{{ p['pu'] }}">
                  <input class="form-control form-control-sm" name="qty" type="number" step="1" min="1" value="1" style="width:90px">
                  <button class="btn btn-sm btn-outline-primary">Ajouter</button>
                </form>
              </li>
            {% endfor %}
          </ul>
        {% else %}
          <p class="muted">Ce restaurant n'a pas (encore) de menu dans Redis (clé <code>menu:{{ rest }}</code>).</p>
        {% endif %}
      </div>
    {% endif %}
  </div>
  <div class="col-md-6">
    <h5>2) Composer la commande</h5>
    {% if panier %}
      <ul class="list-group mb-2">
        {% for l in panier %}
          <li class="list-group-item d-flex justify-content-between">
            {{ l['nom'] }} × {{ l['qty'] }} — {{ '%.2f'|format(l['qty'] * l['pu']) }} €
          </li>
        {% endfor %}
      </ul>
      <form method="post" action="{{ url_for('client_create_order') }}" class="row g-2">
        <div class="col-12">
          <label class="form-label">Adresse de livraison</label>
          <input class="form-control" name="adresse" required>
        </div>
        <div class="col-6">
          <label class="form-label">Zone</label>
          <input class="form-control" name="zone" placeholder="ex: paris-1" required>
        </div>
        <div class="col-6">
          <label class="form-label">Restaurant</label>
          <input class="form-control" name="id_restaurant" value="{{ rest }}" readonly required>
        </div>
        <div class="col-12">
          <button class="btn btn-primary">Créer la commande</button>
        </div>
      </form>
      <form method="post" action="{{ url_for('client_clear_panier') }}" class="mt-2">
        <button class="btn btn-sm btn-outline-danger">Vider le panier</button>
      </form>
    {% else %}
      <p class="muted">Ajoutez des plats depuis le menu pour composer votre commande.</p>
    {% endif %}
    <hr>
    <h5>Mes commandes</h5>
    {% if commandes %}
      <ul class="list-group">
        {% for c in commandes %}
          <li class="list-group-item d-flex justify-content-between align-items-center">
            {{ c['id'] }} — {{ c['statut'] }} — {{ c['zone'] }}
            <a class="btn btn-sm btn-outline-primary" href="{{ url_for('order_details', oid=c['id']) }}">Ouvrir</a>
          </li>
        {% endfor %}
      </ul>
    {% else %}
      <p class="muted">Aucune commande pour l’instant.</p>
    {% endif %}
  </div>
</div>
{% endblock %}
"""
RESTAURANT_DASH = """
{% extends "base.html" %}{% block content %}
<h3>Espace Restaurant</h3>
<form class="row g-2 mb-3" method="get" action="{{ url_for('restaurant_dashboard') }}">
  <div class="col-auto">
    <select name="statut" class="form-select">
      {% for s in ['CREEE','ANONCEE','ASSIGNEE','EN_LIVRAISON'] %}
        <option value="{{s}}" {% if request.args.get('statut','CREEE')==s %}selected{% endif %}>{{ s }}</option>
      {% endfor %}
    </select>
  </div>
  <div class="col-auto"><button class="btn btn-secondary btn-sm">Filtrer</button></div>
</form>
{% if commandes %}
<table class="table table-sm">
  <thead><tr><th>Id</th><th>Zone</th><th>Adresse</th><th>Total (€)</th><th>Statut</th><th>Assigné</th><th></th></tr></thead>
  <tbody>
  {% for c in commandes %}
    <tr>
      <td>{{ c['id'] }}</td>
      <td>{{ c['zone'] }}</td>
      <td>{{ c['livraison']['adresse'] }}</td>
      <td>{{ '%.2f'|format(c.get('montant_total_client') or 0.0) }}</td>
      <td>{{ c['statut'] }}</td>
      <td>{{ c.get('livreur_assigne') or '-' }}</td>
      <td><a class="btn btn-outline-primary btn-sm" href="{{ url_for('order_details', oid=c['id']) }}">Détails</a></td>
    </tr>
  {% endfor %}
  </tbody>
</table>
{% else %}
<p class="muted">Aucune commande à afficher.</p>
{% endif %}
{% endblock %}
"""
LIVREUR_DASH = """
{% extends "base.html" %}{% block content %}
<h3>Espace Livreur</h3>
<form class="row g-2 mb-3" method="get" action="{{ url_for('livreur_annonces') }}">
  <div class="col-auto"><input class="form-control" name="zone" placeholder="ex: paris-1"></div>
  <div class="col-auto"><button class="btn btn-secondary btn-sm">Voir annonces</button></div>
</form>
<p>
  <a class="btn btn-outline-primary btn-sm" href="{{ url_for('livreur_mes_interets') }}">Mes intérêts</a>
  <a class="btn btn-outline-primary btn-sm" href="{{ url_for('livreur_mes_courses') }}">Mes courses</a>
</p>
{% endblock %}
"""
DETAILS = """
{% extends "base.html" %}{% block content %}
<h4>Commande {{ o['id'] }}</h4>
<p>
  <b>Restaurant:</b> {{ o['restaurant']['id'] }} |
  <b>Client:</b> {{ o['client']['id'] }} |
  <b>Zone:</b> {{ o['zone'] }} |
  <b>Adresse:</b> {{ o['livraison']['adresse'] }} |
  <b>Statut:</b> {{ o['statut'] }} |
  <b>Assigné:</b> {{ o['livreur_assigne'] or '-' }}
</p>

<div class="row">
  <div class="col-md-7">
    <h5>Intérêts</h5>
    {% if interets %}
      <table class="table table-sm">
        <thead><tr><th>Livreur</th><th>ETA</th><th>Commentaire</th><th>TS</th><th></th></tr></thead>
        <tbody>
          {% for lid, i in interets.items() %}
            <tr>
              <td>{{ lid }}</td>
              <td>{{ i['eta'] or '-' }} min</td>
              <td>{{ i['comment'] or '' }}</td>
              <td>{{ i['ts'] }}</td>
              <td>
                {% if current_user.role=='RESTAURANT' and o['restaurant']['id']==current_user.id and o['statut']=='ANONCEE' %}
                  <form method="post" action="{{ url_for('assign', oid=o['id']) }}">
                    <input type="hidden" name="livreur_id" value="{{ lid }}">
                    <button class="btn btn-sm btn-primary">Assigner</button>
                  </form>
                {% endif %}
              </td>
            </tr>
          {% endfor %}
        </tbody>
      </table>
    {% else %}
      <p class="muted">Aucun intérêt pour l’instant.</p>
    {% endif %}
  </div>
  <div class="col-md-5">
    <h5>Actions</h5>

    {% set can_cancel_client = (current_user.role=='CLIENT' and o['client']['id']==current_user.id and o['statut'] in ['CREEE','ANONCEE']) %}
    {% set can_cancel_rest = (current_user.role=='RESTAURANT' and o['restaurant']['id']==current_user.id and o['statut'] in ['CREEE','ANONCEE','ASSIGNEE']) %}

    {% if current_user.role=='CLIENT' and o['client']['id']==current_user.id and o['statut'] in ['CREEE','ANONCEE'] %}
      <form class="mb-2" method="post" action="{{ url_for('cancel', oid=o['id']) }}">
        <div class="input-group input-group-sm">
          <input class="form-control" name="motif" placeholder="motif">
          <input type="hidden" name="role" value="CLIENT">
          <button class="btn btn-danger btn-sm">Annuler (client)</button>
        </div>
      </form>
    {% endif %}

    {% if current_user.role=='RESTAURANT' and o['restaurant']['id']==current_user.id and o['statut']=='CREEE' %}
      <form class="mb-2" method="post" action="{{ url_for('publish', oid=o['id']) }}">
        <button class="btn btn-secondary btn-sm">Publier</button>
      </form>
    {% endif %}

    {% if current_user.role=='RESTAURANT' and o['restaurant']['id']==current_user.id and o['statut'] in ['CREEE','ANONCEE','ASSIGNEE'] %}
      <form class="mb-2" method="post" action="{{ url_for('cancel', oid=o['id']) }}">
        <div class="input-group input-group-sm">
          <input class="form-control" name="motif" placeholder="motif">
          <input type="hidden" name="role" value="RESTAURANT">
          <button class="btn btn-outline-danger btn-sm">Annuler (restaurant)</button>
        </div>
      </form>
    {% endif %}

    {% if current_user.role=='LIVREUR' and o['statut']=='ANONCEE' %}
      {% if my_interest %}
        <form class="mb-2" method="post" action="{{ url_for('interest_remove', oid=o['id']) }}">
          <button class="btn btn-outline-warning btn-sm">Retirer mon intérêt</button>
        </form>
      {% else %}
        <form class="mb-2" method="post" action="{{ url_for('interest', oid=o['id']) }}">
          <div class="input-group input-group-sm">
            <input class="form-control" name="eta" type="number" step="1" placeholder="ETA (min)">
            <input class="form-control" name="comment" placeholder="commentaire">
            <button class="btn btn-outline-secondary btn-sm">Je suis intéressé</button>
          </div>
        </form>
      {% endif %}
    {% endif %}

    {% if current_user.role=='LIVREUR' and o['livreur_assigne']==current_user.id and o['statut'] in ['ASSIGNEE','EN_LIVRAISON'] %}
      <form class="d-inline" method="post" action="{{ url_for('deliver', oid=o['id']) }}">
        <button class="btn btn-success btn-sm">Marquer LIVRÉE</button>
      </form>
    {% endif %}

    {% if not (can_cancel_client or can_cancel_rest) and not (current_user.role=='LIVREUR' and o['statut'] in ['ANONCEE','ASSIGNEE','EN_LIVRAISON']) %}
      <p class="muted">Aucune action disponible.</p>
    {% endif %}
  </div>
</div>

<a class="btn btn-link mt-3" href="{{ url_for('home') }}">&larr; Accueil</a>
{% endblock %}
"""

# injection template
app.jinja_loader = type("Loader", (), {
    "get_source": lambda self, env, template: (BASE, template, lambda: True)
})()

@app.context_processor
def inject_user():
    try:
        if request.cookies.get("auth"):
            u = json.loads(request.cookies["auth"])
            return {"current_user": type("U", (), u)}
    except:
        pass
    return {"current_user": None}

# -----------------------------
# Auth
# -----------------------------
@app.route("/")
def home():
    return render_template_string(HOME)

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "GET":
        return render_template_string(LOGIN)
    role = request.form["role"].strip().upper()
    username = request.form["username"].strip()
    password = request.form["password"]

    uid = REDIS.hget(k_user_index(role), username)
    if not uid:
        flash("Utilisateur introuvable"); return render_template_string(LOGIN)
    u = load_json(k_user(role, uid))
    if not u or u.get("password") != password:
        flash("Identifiants invalides"); return render_template_string(LOGIN)

    resp = redirect(url_for(
        "client_dashboard" if role=="CLIENT" else
        "restaurant_dashboard" if role=="RESTAURANT" else
        "livreur_dashboard"
    ))
    resp.set_cookie("auth", json.dumps({"role":role, "id":u["id"], "username":u["username"]}), httponly=False)
    return resp

@app.route("/logout")
def logout():
    resp = redirect(url_for("home"))
    resp.delete_cookie("auth")
    flash("Déconnecté")
    return resp

# -----------------------------
# CLIENT
# -----------------------------
@app.get("/client")
@require_login
@role_required("CLIENT")
def client_dashboard():
    rest = request.args.get("rest")
    menu = None
    if rest:
        menu = load_json(k_menu(rest)) or []
    panier = load_json(f"panier:{request.user['id']}") or []
    my_orders = []
    cursor = 0
    while True:
        cursor, keys = REDIS.scan(cursor=cursor, match="order:*", count=200)
        for k in keys:
            o = load_json(k)
            if o and o.get("client", {}).get("id")==request.user["id"]:
                my_orders.append(o)
        if cursor == 0: break
    my_orders.sort(key=lambda x: x["timestamps"]["creation"] or 0, reverse=True)
    return render_template_string(CLIENT_DASH, rest=rest, menu=menu, panier=panier, commandes=my_orders)

@app.post("/client/panier/add")
@require_login
@role_required("CLIENT")
def client_add_line():
    rest = request.form["rest"].strip()
    id_plat = request.form["id_plat"]; nom = request.form["nom"]; pu = float(request.form["pu"]); qty = int(request.form["qty"])
    panier_key = f"panier:{request.user['id']}"
    panier = load_json(panier_key) or []
    panier.append({"id_plat":id_plat, "nom":nom, "pu":pu, "qty":qty})
    save_json(panier_key, panier)
    flash("Plat ajouté")
    return redirect(url_for("client_dashboard", rest=rest))

@app.post("/client/panier/clear")
@require_login
@role_required("CLIENT")
def client_clear_panier():
    save_json(f"panier:{request.user['id']}", [])
    flash("Panier vidé")
    return redirect(url_for("client_dashboard"))

@app.post("/client/orders")
@require_login
@role_required("CLIENT")
def client_create_order():
    rest = request.form["id_restaurant"].strip()
    zone = request.form["zone"].strip()
    adresse = request.form["adresse"].strip()
    panier = load_json(f"panier:{request.user['id']}") or []
    if not panier:
        flash("Panier vide"); return redirect(url_for("client_dashboard", rest=rest))

    oid = new_order_id()
    total = round(sum(l["qty"]*l["pu"] for l in panier), 2)
    o = {
        "id": oid,
        "version": 1,
        "statut": "CREEE",
        "timestamps": {"creation": now(), "publiee": None, "assignee": None, "cloture": None},
        "zone": zone,
        "livraison": {"adresse": adresse, "lat": None, "lon": None},
        "client": {"id": request.user["id"], "nom": request.user["username"]},
        "restaurant": {"id": rest, "nom": rest, "zone": zone},
        "livreur_assigne": None,
        "livree_par": None,
        "remuneration": 0.0,
        "montant_total_client": total,
        "lignes": panier,
        "annule_par": None,
        "motif_annulation": None,
        "interets": {},
        "events": [{"type":"CREATION","acteur_role":"CLIENT","acteur_id":request.user["id"],"ts":now(),"details":"Commande créée"}]
    }
    save_json(k_order(oid), o)
    rpub("orders.created", {"id": oid, "client": o["client"]["id"], "rest": rest, "zone": zone})
    save_json(f"panier:{request.user['id']}", [])
    flash("Commande créée")
    return redirect(url_for("order_details", oid=oid))

# -----------------------------
# RESTAURANT
# -----------------------------
@app.get("/restaurant")
@require_login
@role_required("RESTAURANT")
def restaurant_dashboard():
    statut = request.args.get("statut", "CREEE")
    my_orders = []
    cursor = 0
    while True:
        cursor, keys = REDIS.scan(cursor=cursor, match="order:*", count=200)
        for k in keys:
            o = load_json(k)
            if o and o.get("restaurant", {}).get("id")==request.user["id"] and o.get("statut")==statut:
                my_orders.append(o)
        if cursor == 0: break
    my_orders.sort(key=lambda x: x["timestamps"]["creation"] or 0, reverse=True)
    return render_template_string(RESTAURANT_DASH, commandes=my_orders)

@app.post("/orders/<oid>/publish")
@require_login
@role_required("RESTAURANT")
def publish(oid):
    o = load_json(k_order(oid))
    if not o: flash("Introuvable"); return redirect(url_for("home"))
    if o["restaurant"]["id"] != request.user["id"] or o["statut"]!="CREEE":
        flash("Publication impossible"); return redirect(url_for("order_details", oid=oid))
    remuner = float(request.form.get("remuneration", "8.50") or 0)
    o["statut"]="ANONCEE"; o["timestamps"]["publiee"]=now(); o["remuneration"]=remuner
    save_json(k_order(oid), o)
    REDIS.zadd(k_zone_annonces(o["zone"]), {oid: o["timestamps"]["publiee"]})
    o["events"].append({"type":"PUBLICATION","acteur_role":"RESTAURANT","acteur_id":request.user["id"],"ts":now(),"details":"Commande publiée"})
    save_json(k_order(oid), o)
    rpub(f"orders.published.{o['zone']}", {"id": oid, "zone": o["zone"], "remuneration": remuner})
    flash("Commande publiée")
    return redirect(url_for("order_details", oid=oid))

@app.post("/orders/<oid>/assign")
@require_login
@role_required("RESTAURANT")
def assign(oid):
    o = load_json(k_order(oid))
    if not o: flash("Introuvable"); return redirect(url_for("home"))
    if o["restaurant"]["id"] != request.user["id"] or o["statut"]!="ANONCEE":
        flash("Assignation impossible"); return redirect(url_for("order_details", oid=oid))
    lid = request.form["livreur_id"].strip()
    if lid not in (o.get("interets") or {}):
        flash("Livreur non intéressé"); return redirect(url_for("order_details", oid=oid))
    o["livreur_assigne"]=lid; o["statut"]="ASSIGNEE"; o["timestamps"]["assignee"]=now()
    save_json(k_order(oid), o)
    REDIS.zadd(k_courier_assigned(lid), {oid: o["timestamps"]["assignee"]})
    o["events"].append({"type":"ASSIGNATION","acteur_role":"RESTAURANT","acteur_id":request.user["id"],"ts":now(),"details":f"Assignée à {lid}"})
    save_json(k_order(oid), o)
    rpub("orders.assigned", {"id": oid, "livreur": lid})
    flash("Livreur assigné")
    return redirect(url_for("order_details", oid=oid))

@app.post("/orders/<oid>/cancel")
@require_login
def cancel(oid):
    role = request.form.get("role","").upper()
    o = load_json(k_order(oid))
    if not o: flash("Introuvable"); return redirect(url_for("home"))

    if o["statut"] == "EN_LIVRAISON":
        flash("Annulation interdite (course démarrée)"); return redirect(url_for("order_details", oid=oid))

    motif = request.form.get("motif") or "Annulation"
    if role=="CLIENT" and request.user["role"]=="CLIENT" and o["client"]["id"]==request.user["id"] and o["statut"] in ["CREEE","ANONCEE"]:
        o["statut"]="ANNULEE"; o["timestamps"]["cloture"]=now(); o["annule_par"]="CLIENT"; o["motif_annulation"]=motif
    elif role=="RESTAURANT" and request.user["role"]=="RESTAURANT" and o["restaurant"]["id"]==request.user["id"] and o["statut"] in ["CREEE","ANONCEE","ASSIGNEE"]:
        o["statut"]="ANNULEE"; o["timestamps"]["cloture"]=now(); o["annule_par"]="RESTAURANT"; o["motif_annulation"]=motif
    else:
        flash("Annulation refusée"); return redirect(url_for("order_details", oid=oid))

    save_json(k_order(oid), o)
    o["events"].append({"type":"ANNULATION","acteur_role":role,"acteur_id":request.user["id"],"ts":now(),"details":motif})
    save_json(k_order(oid), o)
    rpub("orders.canceled", {"id": oid, "role": role})
    flash("Commande annulée")
    return redirect(url_for("order_details", oid=oid))

# -----------------------------
# LIVREUR
# -----------------------------
@app.get("/livreur")
@require_login
@role_required("LIVREUR")
def livreur_dashboard():
    return render_template_string(LIVREUR_DASH)

@app.get("/livreur/annonces")
@require_login
@role_required("LIVREUR")
def livreur_annonces():
    zone = request.args.get("zone")
    if not zone:
        flash("Précise une zone (ex: paris-1)")
        return redirect(url_for("livreur_dashboard"))
    ids = REDIS.zrevrange(k_zone_annonces(zone), 0, -1)
    rows = [load_json(k_order(i)) for i in ids]
    rows = [r for r in rows if r]
    html = """{% extends "base.html" %}{% block content %}
      <h4>Annonces (zone: {{ zone }})</h4>
      {% if rows %}
      <ul class="list-group">
      {% for r in rows %}
        <li class="list-group-item d-flex justify-content-between align-items-center">
          {{ r['id'] }} — {{ r['livraison']['adresse'] }} — {{ '%.2f'|format(r['remuneration']) }}€
          <a class="btn btn-sm btn-outline-primary" href="{{ url_for('order_details', oid=r['id']) }}">Ouvrir</a>
        </li>
      {% endfor %}
      </ul>
      {% else %}
        <p class="muted">Aucune annonce actuellement.</p>
      {% endif %}
    {% endblock %}"""
    return render_template_string(html, rows=rows, zone=zone)

@app.get("/livreur/interets")
@require_login
@role_required("LIVREUR")
def livreur_mes_interets():
    ids = REDIS.smembers(k_interest_by_courier(request.user["id"]))
    rows = [load_json(k_order(i)) for i in ids]
    rows = [r for r in rows if r]
    html = """{% extends "base.html" %}{% block content %}
      <h4>Mes intérêts</h4>
      {% if rows %}
      <ul class="list-group">
      {% for r in rows %}
        <li class="list-group-item d-flex justify-content-between align-items-center">
          {{ r['id'] }} — {{ r['statut'] }} — {{ r['zone'] }}
          <a class="btn btn-sm btn-outline-primary" href="{{ url_for('order_details', oid=r['id']) }}">Ouvrir</a>
        </li>
      {% endfor %}
      </ul>
      {% else %}
        <p class="muted">Vous n'avez pas encore manifesté d'intérêt.</p>
      {% endif %}
    {% endblock %}"""
    return render_template_string(html, rows=rows)

@app.get("/livreur/courses")
@require_login
@role_required("LIVREUR")
def livreur_mes_courses():
    ids = REDIS.zrevrange(k_courier_assigned(request.user["id"]), 0, -1)
    rows = [load_json(k_order(i)) for i in ids]
    rows = [r for r in rows if r]
    html = """{% extends "base.html" %}{% block content %}
      <h4>Mes courses (assignées)</h4>
      {% if rows %}
      <ul class="list-group">
      {% for r in rows %}
        <li class="list-group-item d-flex justify-content-between align-items-center">
          {{ r['id'] }} — {{ r['statut'] }} — {{ r['livraison']['adresse'] }}
          <a class="btn btn-sm btn-outline-primary" href="{{ url_for('order_details', oid=r['id']) }}">Ouvrir</a>
        </li>
      {% endfor %}
      </ul>
      {% else %}
        <p class="muted">Aucune course assignée pour l’instant.</p>
      {% endif %}
    {% endblock %}"""
    return render_template_string(html, rows=rows)

@app.post("/orders/<oid>/interest")
@require_login
@role_required("LIVREUR")
def interest(oid):
    o = load_json(k_order(oid))
    if not o or o["statut"]!="ANONCEE":
        flash("Commande non ANONCEE"); return redirect(url_for("order_details", oid=oid))
    lid = request.user["id"]
    if lid in (o.get("interets") or {}):
        flash("Intérêt déjà enregistré"); return redirect(url_for("order_details", oid=oid))
    eta = request.form.get("eta"); eta = int(eta) if (eta and eta.isdigit()) else None
    comment = request.form.get("comment") or ""
    o.setdefault("interets", {})[lid] = {"eta": eta, "comment": comment, "ts": now()}
    save_json(k_order(oid), o)
    REDIS.sadd(k_interest_by_order(oid), lid)
    REDIS.sadd(k_interest_by_courier(lid), oid)
    rpub("interests.created", {"order": oid, "livreur": lid})
    flash("Intérêt enregistré")
    return redirect(url_for("order_details", oid=oid))

@app.post("/orders/<oid>/interest/remove")
@require_login
@role_required("LIVREUR")
def interest_remove(oid):
    o = load_json(k_order(oid))
    if not o or o["statut"]!="ANONCEE":
        flash("Impossible"); return redirect(url_for("order_details", oid=oid))
    lid = request.user["id"]
    if lid in (o.get("interets") or {}):
        o["interets"].pop(lid, None)
        save_json(k_order(oid), o)
        REDIS.srem(k_interest_by_order(oid), lid)
        REDIS.srem(k_interest_by_courier(lid), oid)
        rpub("interests.removed", {"order": oid, "livreur": lid})
        flash("Intérêt retiré")
    else:
        flash("Aucun intérêt à retirer")
    return redirect(url_for("order_details", oid=oid))

@app.post("/orders/<oid>/deliver")
@require_login
@role_required("LIVREUR")
def deliver(oid):
    o = load_json(k_order(oid))
    if not o:
        flash("Introuvable"); return redirect(url_for("home"))
    if o.get("livreur_assigne") != request.user["id"] or o["statut"] not in ["ASSIGNEE","EN_LIVRAISON"]:
        flash("Action impossible"); return redirect(url_for("order_details", oid=oid))
    o["statut"]="LIVREE"; o["timestamps"]["cloture"]=now(); o["livree_par"]=request.user["id"]
    save_json(k_order(oid), o)
    o["events"].append({"type":"LIVRAISON","acteur_role":"LIVREUR","acteur_id":request.user["id"],"ts":now(),"details":"Commande livrée"})
    save_json(k_order(oid), o)
    rpub("orders.delivered", {"id": oid, "livreur": request.user["id"]})
    flash("Commande marquée LIVRÉE")
    return redirect(url_for("order_details", oid=oid))

# -----------------------------
# Détails commande (commune)
# -----------------------------
@app.get("/orders/<oid>")
@require_login
def order_details(oid):
    o = load_json(k_order(oid))
    if not o:
        flash("Commande introuvable"); return redirect(url_for("home"))
    my_interest = None
    if request.user["role"]=="LIVREUR":
        my_interest = (o.get("interets") or {}).get(request.user["id"])
    interets = o.get("interets") if (request.user["role"]!="CLIENT") else {}
    return render_template_string(DETAILS, o=o, interets=interets, my_interest=my_interest)

# -----------------------------
# SSE: events pub/sub
# -----------------------------
@app.get("/events")
def events_page():
    def stream():
        ps = REDIS.pubsub()
        ps.subscribe("orders.created","interests.created","interests.removed",
                     "orders.assigned","orders.delivered","orders.canceled")
        ps.psubscribe("orders.published.*")
        try:
            for m in ps.listen():
                if m["type"] in ("message","pmessage"):
                    data = m.get("data")
                    if isinstance(data, (bytes, bytearray)): data = data.decode("utf-8","ignore")
                    yield f"data: {data}\n\n"
        finally:
            ps.close()
    return Response(stream(), mimetype="text/event-stream")

# -----------------------------
# Run
# -----------------------------
if __name__ == "__main__":
    app.run(debug=True, port=5001)


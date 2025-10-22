#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
redis_poc.py — POC UberEats avec Redis (lecture JSONL existant + Pub/Sub + SSE)
"""

import json, time, uuid
from functools import wraps
from flask import (
    Flask, request, redirect, url_for, render_template_string,
    flash, Response, make_response
)
from redis import Redis
from jinja2 import DictLoader

# -----------------------------
# Config Redis
# -----------------------------
REDIS = Redis(host="127.0.0.1", port=6379, decode_responses=True)
APP_SECRET = "change-me-please"

# -----------------------------
# Helpers
# -----------------------------
def now(): return int(time.time())
def new_order_id(): return "cmd_" + uuid.uuid4().hex[:8]

def k_user(role, uid): return f"user:{role}:{uid}"
def k_user_index(role): return f"user:index:{role}"
def k_menu(rest_id): return f"restaurant:{rest_id}"   # 🔧 adapté à tes JSONL
def k_order(oid): return f"order:{oid}"

def load_json(k): 
    s = REDIS.get(k)
    return json.loads(s) if s else None

def save_json(k, v): 
    REDIS.set(k, json.dumps(v, ensure_ascii=False))

def rpub(ch, p): 
    REDIS.publish(ch, json.dumps(p, ensure_ascii=False))

# -----------------------------
# Indexation utilisateurs (auto)
# -----------------------------
def rebuild_user_indexes():
    """Reconstruit les index username → id par rôle"""
    for k in REDIS.keys("user:*"):
        u = load_json(k)
        if not u:
            continue
        role = u["role"]
        username = u["username"]
        uid = u["id"]
        REDIS.hset(f"user:index:{role}", username, uid)
    print("✅ Index utilisateurs reconstruits.")

rebuild_user_indexes()

# -----------------------------
# Flask setup
# -----------------------------
app = Flask(__name__)
app.secret_key = APP_SECRET

# -----------------------------
# Base HTML (template intégré)
# -----------------------------
BASE = """<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <title>POC Redis UberEats</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
  <style>
    body{padding:2rem}
    footer{margin-top:2rem;font-size:.9em;color:#777}
  </style>
</head>
<body>
<nav class="mb-3">
  <a href="{{ url_for('home') }}" class="btn btn-outline-dark btn-sm">Accueil</a>
  <a href="{{ url_for('events_page') }}" class="btn btn-outline-info btn-sm">SSE Flux</a>
  {% if request.cookies.get('auth') %}
    <a href="{{ url_for('logout') }}" class="btn btn-outline-danger btn-sm">Déconnexion</a>
  {% endif %}
</nav>
<div class="container">
  {% with msgs = get_flashed_messages() %}
    {% if msgs %}<div class="alert alert-info">{{ msgs[0] }}</div>{% endif %}
  {% endwith %}
  {% block content %}{% endblock %}
</div>
<footer>POC Redis UberEats — Flask + Redis + SSE</footer>
</body>
</html>"""

# ✅ Fix du bug "TemplateNotFound"
app.jinja_loader = DictLoader({"base.html": BASE})

# -----------------------------
# Auth decorators
# -----------------------------
def require_login(f):
    @wraps(f)
    def w(*a, **kw):
        auth = request.cookies.get("auth")
        if not auth:
            return redirect(url_for("login"))
        try:
            u = json.loads(auth)
            request.user = u
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
                flash("Accès refusé pour ce rôle.")
                return redirect(url_for("home"))
            return f(*a, **kw)
        return w
    return deco

# -----------------------------
# Routes
# -----------------------------
@app.route("/")
def home():
    html = """{% extends "base.html" %}{% block content %}
    <h2>🚀 POC Redis UberEats</h2>
    <p>Bienvenue ! Choisissez un rôle :</p>
    <div>
      <a href="{{ url_for('login') }}" class="btn btn-primary">Connexion</a>
    </div>
    {% endblock %}"""
    return render_template_string(html)

# -----------------------------
# Login
# -----------------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    html = """{% extends "base.html" %}{% block content %}
    <h3>Connexion</h3>
    <form method="post" class="w-50">
      <div class="mb-2">
        <label>Rôle</label>
        <select name="role" class="form-select">
          <option>CLIENT</option><option>RESTAURANT</option><option>LIVREUR</option>
        </select>
      </div>
      <div class="mb-2"><label>Nom d'utilisateur</label><input name="username" class="form-control"></div>
      <div class="mb-2"><label>Mot de passe</label><input name="password" type="password" class="form-control"></div>
      <button class="btn btn-success">Connexion</button>
    </form>{% endblock %}"""
    if request.method == "GET":
        return render_template_string(html)

    role = request.form["role"].upper()
    username = request.form["username"].strip()
    password = request.form["password"].strip()
    uid = REDIS.hget(k_user_index(role), username)
    if not uid:
        flash("Utilisateur inconnu")
        return render_template_string(html)
    u = load_json(k_user(role, uid))
    if not u or u.get("password") != password:
        flash("Identifiants invalides")
        return render_template_string(html)
    resp = make_response(redirect(url_for(
        "client_dashboard" if role=="CLIENT" else
        "restaurant_dashboard" if role=="RESTAURANT" else
        "livreur_dashboard"
    )))
    resp.set_cookie("auth", json.dumps(u))
    return resp

@app.route("/logout")
def logout():
    resp = redirect(url_for("home"))
    resp.delete_cookie("auth")
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
        m = load_json(k_menu(rest))
        if m:
            menu = m.get("menu")  # 🔧 adapte au JSON existant
    html = """{% extends "base.html" %}{% block content %}
    <h3>🍽 Espace Client</h3>
    <form method="get" class="mb-3">
      <input name="rest" placeholder="id restaurant" value="{{ rest or '' }}" class="form-control w-50 d-inline">
      <button class="btn btn-sm btn-outline-primary">Charger menu</button>
    </form>
    {% if menu %}
      <h5>Menu du restaurant {{ rest }}</h5>
      <ul class="list-group">
      {% for p in menu %}
        <li class="list-group-item d-flex justify-content-between">
          {{ p['nom'] }} — <strong>{{ p['pu'] }}€</strong>
          <a href="{{ url_for('order_create', rest_id=rest, item=p['nom'], price=p['pu']) }}" class="btn btn-sm btn-outline-success">Commander</a>
        </li>
      {% endfor %}
      </ul>
    {% else %}
      <p class="text-muted">Aucun menu trouvé pour ce restaurant.</p>
    {% endif %}
    {% endblock %}"""
    return render_template_string(html, rest=rest, menu=menu)

@app.get("/order/create/<rest_id>/<item>/<price>")
@require_login
@role_required("CLIENT")
def order_create(rest_id, item, price):
    oid = new_order_id()
    order = {
        "id": oid, "ts": now(),
        "client": request.user["username"],
        "restaurant": rest_id,
        "item": item, "price": price, "statut": "CREEE"
    }
    save_json(k_order(oid), order)
    rpub("orders.created", order)
    flash(f"Commande créée : {oid}")
    return redirect(url_for("client_dashboard"))

# -----------------------------
# RESTAURANT
# -----------------------------
@app.get("/restaurant")
@require_login
@role_required("RESTAURANT")
def restaurant_dashboard():
    rest_id = request.user["id"]
    rest_data = load_json(k_menu(rest_id))
    menu = rest_data.get("menu") if rest_data else []
    orders = [load_json(k) for k in REDIS.keys("order:*") if load_json(k) and load_json(k).get("restaurant", {}).get("id") == rest_id]
    html = """{% extends "base.html" %}{% block content %}
    <h3>👨‍🍳 Espace Restaurant</h3>
    <h5>Menu</h5>
    <ul class="list-group mb-3">
      {% for p in menu %}
        <li class="list-group-item">{{ p['nom'] }} — {{ p['pu'] }}€</li>
      {% endfor %}
    </ul>
    <h5>Commandes reçues</h5>
    <ul class="list-group">
      {% for o in orders %}
        <li class="list-group-item">
          {{ o['id'] }} — {{ o.get('item') or o['restaurant']['nom'] }}
          ({{ o.get('client', {}).get('nom', o.get('client')) }})
          <small class="text-muted">{{ o.get('statut') or o.get('status') }}</small>
          <a href="{{ url_for('restaurant_publish_order', oid=o['id']) }}" class="btn btn-sm btn-outline-primary float-end">Publier</a>
        </li>
      {% else %}
        <li class="list-group-item text-muted">Aucune commande.</li>
      {% endfor %}
    </ul>
    {% endblock %}"""
    return render_template_string(html, menu=menu, orders=orders)

@app.get("/restaurant/publish/<oid>")
@require_login
@role_required("RESTAURANT")
def restaurant_publish_order(oid):
    o = load_json(k_order(oid))
    if o:
        o["statut"] = "PUBLIEE"
        save_json(k_order(oid), o)
        rpub("orders.published.*", o)
        flash(f"Commande publiée : {oid}")
    return redirect(url_for("restaurant_dashboard"))

# -----------------------------
# LIVREUR
# -----------------------------
@app.get("/livreur")
@require_login
@role_required("LIVREUR")
def livreur_dashboard():
    html = """{% extends "base.html" %}{% block content %}
    <h3>🚴 Espace Livreur</h3>
    <p>Les commandes publiées apparaîtront ici (flux SSE en direct).</p>
    <ul id="orders" class="list-group"></ul>
    <script>
      const ev = new EventSource("/events");
      ev.onmessage = e => {
        const d = JSON.parse(e.data);
        if (d.statut === "PUBLIEE" || d.status === "published") {
          const li = document.createElement("li");
          li.className = "list-group-item";
          li.innerText = `Commande ${d.id}: ${d.restaurant?.nom || d.restaurant} (${d.zone || ''})`;
          document.querySelector("#orders").prepend(li);
        }
      };
    </script>
    {% endblock %}"""
    return render_template_string(html)

# -----------------------------
# SSE Events (Pub/Sub)
# -----------------------------
@app.get("/events")
def events_page():
    def stream():
        ps = REDIS.pubsub()
        ps.psubscribe("orders.*")
        for m in ps.listen():
            if m["type"] not in ("message", "pmessage"):
                continue
            data = m["data"]
            if isinstance(data, bytes): data = data.decode()
            yield f"data: {data}\n\n"
    return Response(stream(), mimetype="text/event-stream")

@app.get("/events/test")
def events_test():
    msg = {"ts": now(), "info": "Test SSE OK"}
    rpub("orders.created", msg)
    return f"Événement publié : {msg}"

# -----------------------------
# Run
# -----------------------------
if __name__ == "__main__":
    app.run(debug=True, port=5001)

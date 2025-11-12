# 🍽️ UberEats‑like — POC comparatif **MySQL vs Redis vs MongoDB**

Projet universitaire — **IUT de Villetaneuse, Université Sorbonne Paris Nord**  
**Auteur :** Yassine Ben Aba • **Encadrant :** Christophe Cérin • **Année :** 2025–2026

> **But** : comparer trois paradigmes de persistance (SQL, clé‑valeur, document) dans un même contexte applicatif temps réel (type UberEats) avec un **frontend Flask unique** et **trois backends interchangeables**.

---

## 🧭 Vue d’ensemble

- **Frontend** : Flask (HTML/JS), SSE pour le temps réel
- **Backends** (mêmes routes, même UX) :
  - **MySQL** — modèle **normalisé**, transactions **ACID**
  - **Redis** — modèle **dénormalisé** en mémoire + **Pub/Sub**
  - **MongoDB** — modèle **document** + **Change Streams**
- **Focus du projet** : architecture, cohérence/latence/débit, design des modèles, **pas** la donnée elle‑même.

```
         Frontend (Flask + SSE)
                 │
   ┌─────────────┼─────────────┐
   │             │             │
 MySQL        Redis         MongoDB
 ACID       Pub/Sub       Change Streams
```

---

## 🗂️ Structure du dépôt (suggestion)

```
.
├─ app_frontend/            # templates, static, endpoints communs
├─ poc_mysql/               # code + config MySQL
├─ poc_redis/               # code + config Redis
├─ poc_mongo/               # code + config MongoDB
├─ data/
│  ├─ restaurants-casvp.json         # export Paris Data (source ouverte)
│  ├─ restaurants.jsonl               # restaurants adaptés (clé/valeur/doc)
│  └─ restaurants_menus.jsonl         # menus fictifs (42 × 4 plats)
├─ scripts/
│  ├─ prepare_restaurants.py          # conversion CASVP → JSONL
│  └─ generate_menus.py               # génération de 4 plats/restaurant
└─ README.md
```

> **Dataset** utilisé : _Restaurants CASVP — Paris Data_ → https://opendata.paris.fr/explore/dataset/restaurants-casvp/export/  
> **Remarque** : la donnée sert à **faire tourner le POC**, l’analyse porte sur l’**architecture**.

---

## ⚙️ Prérequis

- Python **3.11+**
- MySQL **8+**
- Redis **7+**
- MongoDB Atlas (ou local **6+**)

---

## 🚀 Quickstart (chaque backend)

### 0) Créer le venv commun
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -U pip
```

### 1) Frontend commun
```bash
pip install Flask python-dotenv
# lancement
python app_frontend/app.py  # expose les routes communes (SSE inclus)
```

### 2) MySQL (POC SQL normalisé)
```bash
cd poc_mysql
pip install mysql-connector-python
# variables (ex. .env)
# MYSQL_HOST=127.0.0.1  MYSQL_DB=ubereats  MYSQL_USER=ubereats  MYSQL_PASS=******
python init_mysql_schema.py          # crée tables, indexes, FK
python load_from_jsonl_mysql.py      # charge restaurants + menus
python mysql_poc.py                  # démarre le backend (port ex. 5000)
```

### 3) Redis (POC dénormalisé + Pub/Sub)
```bash
cd poc_redis
pip install redis Flask
python load_from_jsonl_redis.py      # charge restaurants + menus
python redis_poc.py                  # démarre le backend (port ex. 5001)
```

### 4) MongoDB (POC document + Change Streams)
```bash
cd poc_mongo
pip install pymongo Flask python-dotenv
# .env : MONGODB_URI=..., DB_NAME=ubereats_poc
python load_from_jsonl_mongo.py      # charge restaurants + menus
python mongo_poc.py                  # démarre le backend (port ex. 5002)
```

> Ouvrir l’UI : `http://127.0.0.1:5000` (ou le port configuré). Le frontend cible dynamiquement l’un des backends.

---

## 🔌 API (exemples d’endpoints communs)

| Rôle | Méthode & Route | Description |
|------|------------------|-------------|
| Client | `POST /client/cart` | Crée une commande à partir du panier |
| Restaurant | `POST /restaurant/order/<id>/publish` | Publie une commande (visible livreurs) |
| Livreur | `POST /livreur/interet/<id>` | Manifeste l’intérêt pour une commande |
| Restaurant | `POST /restaurant/order/<id>/assign` | Assigne un livreur |
| Livreur | `POST /livreur/demarrer` / `terminer` | Change le statut (coursier) |
| Global | `GET /events` | Flux SSE (temps réel) |

Backends : même contrat, **implémentations internes différentes** (ACID, Pub/Sub, Change Streams).

---

## 🧱 Modèles de données (résumé)

- **MySQL (normalisé)** : `client`, `restaurant`, `plat`, `commande`, `commande_ligne`, `livreur`, `interet`, `commande_evenement` (+ indexes/PK/FK).  
- **Redis (dénormalisé)** : clés `user:*`, `menu:<rest>`, `order:<id>`, sets/zsets pour zones et intérêts, Pub/Sub `orders.*`.  
- **MongoDB (document)** : collections `users`, `menus`, `orders` (document « riche » avec sous-objets, + Change Streams).

---

## ⏱️ Résumé benchmark (multi‑threads)

| Critère | MySQL | Redis | MongoDB |
|---|---:|---:|---:|
| Latence écriture (ms) | 27.9 | **22.6** | 23.6 |
| Latence lecture (ms)  | 28.0 | **22.5** | 22.7 |
| Débit (req/s)         | 36   | **44**   | 43   |

> Mesure indicative issue du rapport : 200 req lecture/écriture, 20 threads, mêmes endpoints.  
> Redis domine en latence brute (mémoire), MongoDB proche (moteur doc), MySQL plus rigoureux (ACID).

---

## 📦 Données (minimal, non central)

- Source ouverte : **Restaurants CASVP — Paris Data**  
  https://opendata.paris.fr/explore/dataset/restaurants-casvp/export/
- Adaptation → `data/restaurants.jsonl`
- Menus fictifs (42 × 4 plats) → `data/restaurants_menus.jsonl`

> Les scripts sont fournis pour **reproduire** la génération, mais la **valeur** du projet est l’**architecture** et la **comparaison**.

---

## 🔒 Config exemples

**MySQL (.env)**
```
MYSQL_HOST=127.0.0.1
MYSQL_DB=ubereats
MYSQL_USER=ubereats
MYSQL_PASS=change-me
```

**MongoDB (.env)**
```
MONGODB_URI=mongodb+srv://ubereats_user:***@cluster.mongodb.net
DB_NAME=ubereats_poc
```

---

## 📚 Références

- Redis Pub/Sub : https://redis.io/docs/latest/develop/interact/pubsub/  
- MongoDB Change Streams : https://www.mongodb.com/docs/manual/changeStreams/  
- MySQL Manual : https://dev.mysql.com/doc/  
- Flask : https://flask.palletsprojects.com/  
- Python : https://docs.python.org/3/  
- Leaflet.js : https://leafletjs.com/  
- Dataset Paris Data (CASVP) : https://opendata.paris.fr/explore/dataset/restaurants-casvp/export/

---

## 📝 Licence

Projet à visée **pédagogique**. Scripts sous **MIT**. Données **ouvertes** (Paris Data).


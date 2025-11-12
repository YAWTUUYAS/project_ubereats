# 🍽️ Étude comparative SQL / Redis / MongoDB — Application de type UberEats

Projet universitaire réalisé à l’**IUT de Villetaneuse — Université Sorbonne Paris Nord**  
**Auteur :** Yassine Ben Aba  
**Encadrant :** Christophe Cérin  
**Année universitaire :** 2025–2026

---

## 🎯 Objectif du projet

Ce projet vise à comparer trois architectures de bases de données dans le contexte d’une application de type **UberEats** :

- **MySQL** : modèle relationnel normalisé (ACID)  
- **Redis** : approche clé-valeur en mémoire, dénormalisée  
- **MongoDB** : base documentaire avec suivi temps réel via *Change Streams*  

L’objectif est d’évaluer les compromis entre **cohérence**, **performance**, **flexibilité** et **scalabilité** à travers trois POC indépendants, intégrés à un frontend unique développé avec Flask.

---

## 🧩 Architecture globale

Frontend (Flask + SSE)
│
├── Backend MySQL     → modèle transactionnel ACID
├── Backend Redis     → Pub/Sub en mémoire
└── Backend MongoDB   → Change Streams documentaires

Chaque backend expose les mêmes endpoints REST (`/client`, `/restaurant`, `/livreur`) pour garantir une comparaison équitable.

---

## 🗂️ Dataset utilisé

Les données proviennent d’un **jeu de données officiel** de la Ville de Paris :  
📎 [Dataset Restaurants CASVP — Paris Data](https://opendata.paris.fr/explore/dataset/restaurants-casvp/export/)

Ce jeu de données répertorie les restaurants parisiens gérés par le **Centre d’Action Sociale de la Ville de Paris (CASVP)**.  
Il a été adapté pour correspondre à notre modèle de base de données, puis enrichi avec des menus générés automatiquement.

---

## 🔧 Préparation des données

### Étape 1 : Conversion du dataset CASVP

```python
import json
from pathlib import Path

input_file = Path("restaurants-casvp.json")
output_file = Path("restaurants.jsonl")

with open(input_file, "r", encoding="utf-8") as f:
    data = json.load(f)

with open(output_file, "w", encoding="utf-8") as f_out:
    for i, r in enumerate(data, start=1):
        if not r.get("nom_restaurant") or not r.get("adresse"):
            continue
        rest_id = f"rest_{i:03d}"
        code = r.get("code", "")
        zone = f"paris-{code[-2:]}" if code else "paris-unknown"
        doc = {
            "key": f"restaurant:{rest_id}",
            "restaurant": {
                "id": rest_id,
                "nom": r["nom_restaurant"].title(),
                "adresse": f"{r['adresse']}, {r.get('ville', 'Paris')}",
                "zone": zone.lower(),
                "telephone": "0100000000",
                "username": rest_id,
                "password": "demo123"
            }
        }
        f_out.write(json.dumps(doc, ensure_ascii=False) + "\n")
print("✅ restaurants.jsonl généré")
```

---

### Étape 2 : Génération de menus fictifs

```python
import json, random
from pathlib import Path

rest_file = Path("restaurants.jsonl")
out_file = Path("restaurants_menus.jsonl")

plats_base = [
    ("Pizza Margherita", 10.5),
    ("Burger Classique", 11.0),
    ("Pâtes Carbonara", 13.5),
    ("Salade César", 9.5),
    ("Tacos Poulet", 8.9),
    ("Wrap Végétarien", 8.5),
    ("Steak Frites", 14.0),
    ("Curry de Légumes", 10.8),
    ("Lasagnes Maison", 13.2),
    ("Soupe du Jour", 6.5),
    ("Crème Brûlée", 6.0)
]

with open(rest_file, "r", encoding="utf-8") as f_rest, open(out_file, "w", encoding="utf-8") as f_out:
    for i, line in enumerate(f_rest, start=1):
        if i > 42:
            break
        r = json.loads(line)
        rest_id = r["restaurant"]["id"]
        rest_nom = r["restaurant"]["nom"]
        menu = random.sample(plats_base, 4)
        doc = {
            "restaurant": {
                "id": rest_id,
                "nom": rest_nom,
                "menu": [{"nom": p, "prix": prix} for (p, prix) in menu]
            }
        }
        f_out.write(json.dumps(doc, ensure_ascii=False) + "\n")
print("✅ restaurants_menus.jsonl généré")
```

---

## 🧠 Points clés des trois POC

| Base de données | Caractéristiques principales | Avantages | Limites |
|------------------|------------------------------|------------|----------|
| **MySQL** | Schéma normalisé, transactions ACID | Cohérence, fiabilité | Moins flexible |
| **Redis** | Stockage clé-valeur en mémoire, Pub/Sub | Très rapide, temps réel | Pas de transactions multi-clés |
| **MongoDB** | Documents JSON, Change Streams | Temps réel, modèle flexible | Consistance éventuelle |

---

## 🌍 Intégration cartographique

L’application intègre **OpenStreetMap** via la bibliothèque **Leaflet.js** pour visualiser la position des restaurants et zones de livraison.  
Les coordonnées GPS du dataset CASVP ont été directement utilisées dans cette représentation.

---

## 🚀 Exécution

### 1️⃣ MySQL
```bash
cd poc_mysql
python mysql_poc.py
```

### 2️⃣ Redis
```bash
cd poc_redis
python redis_poc.py
```

### 3️⃣ MongoDB
```bash
cd poc_mongo
python mongo_poc.py
```

Chaque backend expose une interface Flask locale accessible via :  
👉 [http://localhost:5000](http://localhost:5000)

---

## 📊 Benchmark et analyse comparative

Un benchmark multi-threads a été mené pour évaluer les temps de réponse lors de :
- la création de commandes,
- la publication par les restaurants,
- et l’assignation par les livreurs.

Les résultats montrent :
- **Redis** : latence < 1 ms (écriture en mémoire)
- **MongoDB** : 10–15 ms avec *Change Streams*
- **MySQL** : 20–30 ms avec contraintes ACID

---

## 📚 Références

- [Redis Documentation](https://redis.io/documentation)
- [MongoDB Manual](https://www.mongodb.com/docs/)
- [MySQL Reference Manual](https://dev.mysql.com/doc/)
- [Flask Web Framework](https://flask.palletsprojects.com/)
- [Python Official Documentation](https://docs.python.org/3/)
- [Leaflet.js Library](https://leafletjs.com/)
- [Dataset Restaurants CASVP — Paris Data](https://opendata.paris.fr/explore/dataset/restaurants-casvp/export/)

---

## 🧾 Licence

Ce projet est publié à des fins **pédagogiques** et **comparatives**.  
Les données proviennent de sources **ouvertes** (Paris Data, CASVP) et les scripts sont diffusés sous licence **MIT**.

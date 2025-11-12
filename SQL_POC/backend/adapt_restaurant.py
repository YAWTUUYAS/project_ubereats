import json
from pathlib import Path

# --- Dossiers ---
input_file = Path("restaurants-casvp.json")   # ton fichier téléchargé
output_file = Path("restaurants.jsonl")       # fichier final compatible POC

# --- Lecture du dataset ---
with open(input_file, "r", encoding="utf-8") as f:
    data = json.load(f)

# --- Conversion au format JSONL compatible avec ton POC ---
with open(output_file, "w", encoding="utf-8") as f_out:
    for i, r in enumerate(data, start=1):
        # Ignorer les lignes incomplètes
        if not r.get("nom_restaurant") or not r.get("adresse"):
            continue

        rest_id = f"rest_{i:04d}"
        zone = f"paris-{r.get('code')[-2:]}" if r.get("code") else "paris-unknown"
        coords = r.get("tt") or {}

        doc = {
            "key": f"restaurant:{rest_id}",
            "restaurant": {
                "id": rest_id,
                "nom": r.get("nom_restaurant", "").title(),
                "adresse": f"{r.get('adresse', '')}, {r.get('ville', 'Paris')}",
                "zone": zone.lower(),
                "telephone": "0100000000",  # valeur fictive
                "username": rest_id,
                "password": "demo123",
                "lat": coords.get("lat"),
                "lon": coords.get("lon"),
            }
        }

        f_out.write(json.dumps(doc, ensure_ascii=False) + "\n")

print(f"✅ Fichier JSONL généré : {output_file.resolve()}")


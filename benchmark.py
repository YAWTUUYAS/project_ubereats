import requests
import time
import csv
import statistics
from datetime import datetime

# === CONFIGURATION DES SERVEURS ET ENDPOINTS ===
POC_CONFIG = {
    "MySQL": {
        "base": "http://127.0.0.1:5000",
        "create": "/client/cart",
        "read": "/client/orders"  # selon app.py
    },
    "Redis": {
        "base": "http://127.0.0.1:5001",
        "create": "/client/cart",
        "read": "/client/orders"  # selon redis_poc.py
    },
    "MongoDB": {
        "base": "http://127.0.0.1:5002",
        "create": "/client/cart",
        "read": "/client/orders"  # selon mongo_poc.py
    },
}

# === PARAMÈTRES DU TEST ===
N = 200  # nombre de requêtes lecture/écriture
RESULTS_FILE = "benchmark_results.csv"

SAMPLE_ORDER = {
    "id": "cmd_bench",
    "zone": "paris-1",
    "id_client": "clt_001",
    "id_restaurant": "rest_001",
    "livraison": {"adresse": "25 rue de la Paix, Paris"},
    "montant_total_client": 12.5,
}


# === OUTILS ===
def measure_request(url, method="GET", json=None):
    """Mesure la durée (en ms) d'une requête HTTP."""
    start = time.perf_counter()
    try:
        if method == "POST":
            response = requests.post(url, json=json)
        else:
            response = requests.get(url)
        latency = (time.perf_counter() - start) * 1000
        return latency, response.status_code
    except requests.exceptions.ConnectionError:
        print(f"❌ Impossible de se connecter à {url}")
        return 0, 0


def benchmark_backend(name, config):
    """Exécute un benchmark complet sur un backend donné."""
    base_url = config["base"]
    create_url = base_url + config["create"]
    read_url = base_url + config["read"]

    read_latencies = []
    write_latencies = []

    print(f"\n🚀 Benchmark {name} ({base_url})")

    # --- TESTS D'ÉCRITURE ---
    for i in range(N):
        order = SAMPLE_ORDER.copy()
        order["id"] = f"cmd_{i:04d}"
        latency, status = measure_request(create_url, method="POST", json=order)
        if status == 200:
            write_latencies.append(latency)
        else:
            print(f"⚠️ POST erreur {status} sur {create_url}")
        time.sleep(0.005)

    # --- TESTS DE LECTURE ---
    for i in range(N):
        latency, status = measure_request(read_url)
        if status == 200:
            read_latencies.append(latency)
        else:
            print(f"⚠️ GET erreur {status} sur {read_url}")
        time.sleep(0.005)

    # --- CALCULS ---
    if not write_latencies or not read_latencies:
        print(f"❌ Pas assez de données valides pour {name}")
        return None

    mean_write = statistics.mean(write_latencies)
    mean_read = statistics.mean(read_latencies)
    throughput = N / ((sum(write_latencies) + sum(read_latencies)) / 1000)

    print(f"  ↳ Écriture : {mean_write:.2f} ms")
    print(f"  ↳ Lecture  : {mean_read:.2f} ms")
    print(f"  ↳ Débit    : {throughput:.0f} req/s")

    # --- SAUVEGARDE CSV ---
    with open(RESULTS_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            name,
            round(mean_read, 2),
            round(mean_write, 2),
            round(throughput, 0),
            N
        ])

    return mean_read, mean_write, throughput


# === SCRIPT PRINCIPAL ===
if __name__ == "__main__":
    print("=== Benchmark comparatif POC UberEats ===")
    print(f"(Chaque test : {N} lectures et {N} écritures)\n")

    # En-tête du fichier CSV
    with open(RESULTS_FILE, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "backend", "read_ms", "write_ms", "throughput_rps", "n_ops"])

    results = {}

    for name, cfg in POC_CONFIG.items():
        try:
            results[name] = benchmark_backend(name, cfg)
        except Exception as e:
            print(f"❌ Erreur avec {name} : {e}")

    print("\n✅ Benchmark terminé. Résultats enregistrés dans benchmark_results.csv")


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Benchmark avancé UberEats POC
- Compare MySQL / Redis / MongoDB
- Mesure temps moyen, écart-type, débit (RPS)
- Exécute des requêtes simultanées (multi-threads)
- Sauvegarde résultats dans benchmark_results.csv
"""

import requests
import time
import csv
import statistics
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# === CONFIGURATION DES SERVEURS ===
POC_CONFIG = {
    "MySQL": {
        "base": "http://127.0.0.1:5000",
        "create": "/client/cart",
        "read": "/client/orders"
    },
    "Redis": {
        "base": "http://127.0.0.1:5001",
        "create": "/client/cart",
        "read": "/client/orders"
    },
    "MongoDB": {
        "base": "http://127.0.0.1:5002",
        "create": "/client/cart",
        "read": "/client/orders"
    },
}

# === PARAMÈTRES DU TEST ===
N = 200              # nombre total de requêtes lecture/écriture
THREADS = 20         # nombre de threads simultanés
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
    """Mesure la durée d’une requête HTTP (ms)."""
    start = time.perf_counter()
    try:
        if method == "POST":
            r = requests.post(url, json=json, timeout=5)
        else:
            r = requests.get(url, timeout=5)
        latency = (time.perf_counter() - start) * 1000
        return latency, r.status_code
    except requests.exceptions.RequestException:
        return None, 0


def run_parallel_requests(url, method="GET", json=None, n=100):
    """Exécute des requêtes simultanées et retourne la liste des latences."""
    latencies = []
    with ThreadPoolExecutor(max_workers=THREADS) as executor:
        futures = []
        for i in range(n):
            data = None
            if json:
                data = dict(json)
                data["id"] = f"cmd_{i:05d}"
            futures.append(executor.submit(measure_request, url, method, data))

        for future in as_completed(futures):
            latency, status = future.result()
            if latency is not None and status == 200:
                latencies.append(latency)
    return latencies


def benchmark_backend(name, config):
    """Benchmark complet lecture/écriture pour un backend donné."""
    base = config["base"]
    create_url = base + config["create"]
    read_url = base + config["read"]

    print(f"\n🚀 Benchmark {name} ({base})")

    # --- ÉCRITURES ---
    write_latencies = run_parallel_requests(create_url, "POST", SAMPLE_ORDER, N)

    # --- LECTURES ---
    read_latencies = run_parallel_requests(read_url, "GET", None, N)

    if not write_latencies or not read_latencies:
        print(f"❌ Aucune donnée valide pour {name}")
        return None

    write_mean = statistics.mean(write_latencies)
    write_std = statistics.pstdev(write_latencies)
    read_mean = statistics.mean(read_latencies)
    read_std = statistics.pstdev(read_latencies)

    total_time_s = (sum(write_latencies) + sum(read_latencies)) / 1000
    throughput = (2 * N) / total_time_s

    print(f"  ↳ Écriture : {write_mean:.2f} ms (σ={write_std:.2f})")
    print(f"  ↳ Lecture  : {read_mean:.2f} ms (σ={read_std:.2f})")
    print(f"  ↳ Débit    : {throughput:.0f} req/s")

    # --- SAUVEGARDE ---
    with open(RESULTS_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            name,
            round(read_mean, 2),
            round(read_std, 2),
            round(write_mean, 2),
            round(write_std, 2),
            round(throughput, 1),
            N
        ])

    return read_mean, write_mean, throughput


# === SCRIPT PRINCIPAL ===
if __name__ == "__main__":
    print("=== Benchmark avancé POC UberEats ===")
    print(f"(Tests : {N} lectures/écritures simultanées avec {THREADS} threads)\n")

    # Init CSV
    with open(RESULTS_FILE, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "backend", "read_mean", "read_std", "write_mean", "write_std", "throughput_rps", "n_ops"])

    results = {}
    for name, cfg in POC_CONFIG.items():
        try:
            results[name] = benchmark_backend(name, cfg)
        except Exception as e:
            print(f"❌ Erreur avec {name} : {e}")

    print("\n✅ Benchmark terminé — résultats enregistrés dans benchmark_results.csv")

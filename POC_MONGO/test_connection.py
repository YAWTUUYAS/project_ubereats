from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi
from dotenv import load_dotenv
from os import getenv


load_dotenv()
uri = getenv("MONGODB_URI")

client = MongoClient(uri, server_api=ServerApi('1'))

try:
    client.admin.command('ping')
    print(" Connexion réussie à MongoDB !")
except Exception as e:
    print(" Erreur de connexion :", e)
    exit()

db = client["test"]

collection = db["utilisateurs"]

utilisateur = {
    "nom": "Ben Aba",
    "prenom": "Youssef",
    "email": "youssefbenaba17@gmail.com",
    "age": 16
}

resultat = collection.insert_one(utilisateur)
print("Document inséré avec l'ID :", resultat.inserted_id)
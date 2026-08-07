import requests
import os
from dotenv import load_dotenv

load_dotenv()
api_key = os.environ.get("GROQ_API_KEY")
url = "https://api.groq.com/openai/v1/models"
headers = {
    "Authorization": f"Bearer {api_key}",
    "Content-Type": "application/json"
}

response = requests.get(url, headers=headers)
data = response.json()

# Solo los IDs, para no pelear con el JSON crudo
modelos = sorted(m["id"] for m in data.get("data", []))
for m in modelos:
    print(m)

# Filtro rápido para ver si sigue vivo algo con "llama-3.1" o "3.3"
print("\n--- Relacionados con Llama 3.x ---")
for m in modelos:
    if "llama" in m.lower():
        print(m)
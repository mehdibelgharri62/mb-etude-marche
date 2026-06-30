# DÉPLOIEMENT — Guide étape par étape

## Ce que tu as dans ce dossier

- main.py              → le backend FastAPI (webhook Stripe, génération, email)
- index.html           → le formulaire client (à servir depuis Netlify ou Render)
- requirements.txt     → dépendances Python
- generate_content.py  → ton script de génération de contenu (déjà écrit)
- generate_report.py   → ton script de génération PDF (déjà écrit)

---

## Étape 1 — Tester en local

1. Installe les dépendances :
   pip install -r requirements.txt

2. Copie le .env.example en .env et remplis les valeurs (voir ci-dessous)

3. Lance le serveur :
   uvicorn main:app --reload --port 8000

4. Ouvre http://localhost:8000/health → doit retourner {"status": "ok"}

---

## Étape 2 — Créer le produit Stripe

1. Va sur https://dashboard.stripe.com
2. Catalogue → Produits → Créer un produit
   - Nom : "Étude de marché MB Consulting"
   - Prix : 59 € / paiement unique
3. Note l'ID du Price (format : price_1Abc...)

---

## Étape 3 — Créer le compte Render

1. Va sur https://render.com → Sign up avec GitHub
2. New → Web Service
3. Connecte ton repo GitHub (ou upload les fichiers)
4. Paramètres :
   - Environment : Python
   - Build Command : pip install -r requirements.txt
   - Start Command : uvicorn main:app --host 0.0.0.0 --port $PORT
   - Plan : Free

---

## Étape 4 — Variables d'environnement sur Render

Dans Render → Environment → Add Environment Variables :

| Clé                  | Valeur                        |
|----------------------|-------------------------------|
| GEMINI_API_KEY       | ta clé Gemini                 |
| STRIPE_SECRET_KEY    | sk_live_... (ou sk_test_...)  |
| STRIPE_WEBHOOK_SECRET| whsec_... (voir étape 5)      |
| BREVO_API_KEY        | ta clé Brevo                  |
| OWNER_EMAIL          | ton adresse email             |
| PRICE_ID             | price_1Abc... (étape 2)       |

---

## Étape 5 — Configurer le webhook Stripe

1. Stripe Dashboard → Développeurs → Webhooks → Ajouter un endpoint
2. URL : https://ton-app.onrender.com/webhook
3. Événements à écouter : checkout.session.completed
4. Note le "Signing secret" (whsec_...) → c'est STRIPE_WEBHOOK_SECRET

---

## Étape 6 — Héberger le formulaire

Option A (plus simple) : Render sert aussi le HTML
  Ajoute dans main.py :
    from fastapi.staticfiles import StaticFiles
    app.mount("/", StaticFiles(directory=".", html=True), name="static")
  Et mets index.html dans le même dossier.

Option B : Netlify pour le formulaire, Render pour le backend
  - Mets index.html sur Netlify
  - Change action="/submit" par action="https://ton-app.onrender.com/submit"
  - Ajoute ton domaine Netlify dans les CORS de main.py

---

## Étape 7 — Sous-domaine (optionnel mais recommandé)

Sur ton registrar DNS, ajoute un CNAME :
  etude.mbconsulting.fr → ton-app.onrender.com

Puis dans Render → Settings → Custom Domains → ajoute etude.mbconsulting.fr

---

## Variables d'environnement en local (.env)

Crée un fichier .env (jamais commité sur GitHub) :

GEMINI_API_KEY=ta_cle
STRIPE_SECRET_KEY=sk_test_ta_cle
STRIPE_WEBHOOK_SECRET=whsec_ta_cle
BREVO_API_KEY=ta_cle_brevo
OWNER_EMAIL=toi@mbconsulting.fr
PRICE_ID=price_1Abc

Pour charger le .env en local, installe python-dotenv et ajoute en haut de main.py :
  from dotenv import load_dotenv
  load_dotenv()

"""
BACKEND FastAPI — main.py
=========================
Compatible avec generate_content.py V5 et generate_report.py V4.1 bis de MB Consulting.

Flux :
  1. POST /submit   → reçoit le formulaire, crée session Stripe Checkout
  2. GET  /success  → page "rapport en cours"
  3. POST /webhook  → Stripe confirme → génération PDF → email à OWNER_EMAIL (toi)

Variables d'environnement (fichier .env en local, Render en prod) :
  GEMINI_API_KEY
  STRIPE_SECRET_KEY
  STRIPE_WEBHOOK_SECRET
  BREVO_API_KEY
  OWNER_EMAIL
  PRICE_ID
"""

import os
import json
import base64
import tempfile
import traceback
import importlib
import sys
from pathlib import Path

import stripe
import sib_api_v3_sdk
from sib_api_v3_sdk.rest import ApiException
from fastapi import FastAPI, Request, Form, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# CONFIG — lues depuis .env en local, variables Render en prod
# ---------------------------------------------------------------------------
stripe.api_key = os.environ["STRIPE_SECRET_KEY"]
STRIPE_WEBHOOK_SECRET = os.environ["STRIPE_WEBHOOK_SECRET"]
BREVO_API_KEY = os.environ["BREVO_API_KEY"]
OWNER_EMAIL = os.environ["OWNER_EMAIL"]
PRICE_ID = os.environ["PRICE_ID"]

app = FastAPI()

# Sert index.html sur /
app.mount("/static", StaticFiles(directory="."), name="static")


@app.get("/", response_class=HTMLResponse)
async def index():
    with open("index.html", encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------
# EMAIL — envoie le PDF à OWNER_EMAIL (toi)
# ---------------------------------------------------------------------------
def envoyer_pdf(pdf_path: str, form_data: dict, project_name: str):
    configuration = sib_api_v3_sdk.Configuration()
    configuration.api_key["api-key"] = BREVO_API_KEY
    api = sib_api_v3_sdk.TransactionalEmailsApi(
        sib_api_v3_sdk.ApiClient(configuration)
    )

    with open(pdf_path, "rb") as f:
        pdf_b64 = base64.b64encode(f.read()).decode("utf-8")

    corps = f"""Nouveau rapport généré — {project_name}

Client   : {form_data.get('name', '')}
Email    : {form_data.get('email', '')}
Tel      : {form_data.get('phone', 'Non renseigné')}
Stade    : {form_data.get('stage', '')}
Objectif : {form_data.get('main_goal', '')}
Concept  : {form_data.get('concept', '')[:300]}

Le rapport est en pièce jointe. Relis-le et envoie-le au client.
"""

    email = sib_api_v3_sdk.SendSmtpEmail(
        to=[{"email": OWNER_EMAIL}],
        sender={"email": OWNER_EMAIL, "name": "MB Consulting — Système"},
        subject=f"[RAPPORT] {project_name} — {form_data.get('name', 'Client')}",
        text_content=corps,
        attachment=[{
            "name": f"etude_marche_{project_name.replace(' ', '_')[:40]}.pdf",
            "content": pdf_b64,
        }],
    )
    try:
        api.send_transac_email(email)
        print(f"✅ Email envoyé à {OWNER_EMAIL}")
    except ApiException as e:
        print(f"❌ Erreur Brevo : {e}")


# ---------------------------------------------------------------------------
# GÉNÉRATION — tourne en background après confirmation Stripe
# ---------------------------------------------------------------------------
def generer_et_envoyer(form_data: dict):
    """
    1. Injecte les données du formulaire dans PROJECT_INPUT de generate_content.py
    2. Lance la génération Gemini section par section
    3. Lance la génération PDF
    4. Envoie le PDF par email à OWNER_EMAIL
    Tout tourne dans un dossier temporaire isolé pour chaque commande.
    """
    project_name = form_data.get("project_name", "Projet client")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        content_file = str(tmpdir / "contenu_genere_v5.json")
        output_pdf   = str(tmpdir / "etude_de_marche_V5.pdf")

        try:
            # --- Charger generate_content en module frais ---
            spec = importlib.util.spec_from_file_location(
                "generate_content_tmp",
                Path(__file__).parent / "generate_content.py"
            )
            gc = importlib.util.module_from_spec(spec)
            sys.modules["generate_content_tmp"] = gc
            spec.loader.exec_module(gc)

            # --- Injecter les données du formulaire dans PROJECT_INPUT ---
            gc.PROJECT_INPUT["concept"]          = form_data.get("concept", "")
            gc.PROJECT_INPUT["project_type"]     = form_data.get("project_type", "projet_hybride")
            gc.PROJECT_INPUT["zone"]             = form_data.get("zone", "France")
            gc.PROJECT_INPUT["target_customer"]  = form_data.get("target_customer", "")
            gc.PROJECT_INPUT["main_offer"]       = form_data.get("main_offer", "")
            gc.PROJECT_INPUT["revenue_model"]    = form_data.get("revenue_model", "")
            gc.PROJECT_INPUT["additional_context"] = form_data.get("additional_context", "")
            gc.PROJECT_INPUT["stage"]            = form_data.get("stage", "")

            # Budget : convertit en int si renseigné
            budget_raw = form_data.get("budget_eur", "")
            if budget_raw and str(budget_raw).strip().isdigit():
                gc.PROJECT_INPUT["budget_eur"] = int(budget_raw)
            else:
                gc.PROJECT_INPUT.pop("budget_eur", None)

            # Spécificités : peut être une liste ou une string séparée par virgules
            specs = form_data.get("specificities", "")
            if isinstance(specs, str):
                specs = [s.strip() for s in specs.split(",") if s.strip()]
            gc.PROJECT_INPUT["specificities"] = specs

            # Chemin du fichier JSON de contenu → dossier temporaire
            gc.CONTENT_FILE = content_file

            # --- Lancer la génération du contenu ---
            gc.main()

            # --- Charger generate_report en module frais ---
            spec2 = importlib.util.spec_from_file_location(
                "generate_report_tmp",
                Path(__file__).parent / "generate_report.py"
            )
            gr = importlib.util.module_from_spec(spec2)

            # Surcharge des chemins avant exec_module
            # (generate_report lit CONTENT_FILE et OUTPUT_PATH au niveau module)
            import generate_report as gr_orig
            gr_orig.CONTENT_FILE = content_file
            gr_orig.OUTPUT_PATH  = output_pdf

            # Recharge le contenu JSON dans le module
            with open(content_file, "r", encoding="utf-8") as f:
                gr_orig.REAL_CONTENT = json.load(f)

            gr_orig.PROJECT_INPUT  = gr_orig.REAL_CONTENT.get("_project_input", {})
            gr_orig.PROJECT_PROFILE = gr_orig.REAL_CONTENT.get("_project_profile", {})
            gr_orig.CLIENT_VILLE   = gr_orig.PROJECT_INPUT.get("zone", "Zone à préciser")
            gr_orig.CLIENT_CONCEPT = gr_orig.PROJECT_INPUT.get("concept", "Concept à préciser")
            gr_orig.SECTION_TITLES = gr_orig.REAL_CONTENT.get("_section_titles", {})
            gr_orig.SECTIONS_PDF   = [
                (sid, gr_orig.SECTION_TITLES.get(sid, title))
                for sid, title in gr_orig.DEFAULT_SECTIONS_PDF
            ]

            # --- Générer le PDF ---
            from reportlab.platypus import SimpleDocTemplate
            from reportlab.lib.pagesizes import A4
            from reportlab.lib.units import cm

            doc = SimpleDocTemplate(
                output_pdf,
                pagesize=A4,
                topMargin=1.75 * cm,
                bottomMargin=2.15 * cm,
                leftMargin=2 * cm,
                rightMargin=2 * cm,
                title=f"Étude de marché - {gr_orig.CLIENT_CONCEPT}",
                author="MB Consulting",
            )
            doc.build(
                gr_orig.build_story(),
                onFirstPage=gr_orig.add_page_furniture,
                onLaterPages=gr_orig.add_page_furniture,
            )
            print(f"✅ PDF généré : {output_pdf}")

            # --- Envoyer le PDF par email ---
            envoyer_pdf(output_pdf, form_data, project_name)

        except Exception as e:
            print(f"❌ Erreur génération : {e}")
            traceback.print_exc()


# ---------------------------------------------------------------------------
# ROUTES
# ---------------------------------------------------------------------------

@app.post("/submit")
async def submit_form(
    request: Request,
    # Obligatoires
    email:           str = Form(...),
    name:            str = Form(...),
    project_name:    str = Form(...),
    concept:         str = Form(...),
    project_type:    str = Form(...),
    zone:            str = Form(...),
    target_customer: str = Form(...),
    main_offer:      str = Form(...),
    stage:           str = Form(...),
    # Optionnels
    budget_eur:         str = Form(default=""),
    revenue_model:      str = Form(default=""),
    specificities:      str = Form(default=""),
    additional_context: str = Form(default=""),
    main_goal:          str = Form(default=""),
    phone:              str = Form(default=""),
):
    # Stripe accepte max 500 chars par metadata value
    def trunc(v): return str(v)[:500]

    metadata = {
        "email":              trunc(email),
        "name":               trunc(name),
        "project_name":       trunc(project_name),
        "concept":            trunc(concept),
        "project_type":       trunc(project_type),
        "zone":               trunc(zone),
        "target_customer":    trunc(target_customer),
        "main_offer":         trunc(main_offer),
        "stage":              trunc(stage),
        "budget_eur":         trunc(budget_eur),
        "revenue_model":      trunc(revenue_model),
        "specificities":      trunc(specificities),
        "additional_context": trunc(additional_context),
        "main_goal":          trunc(main_goal),
        "phone":              trunc(phone),
    }

    session = stripe.checkout.Session.create(
        payment_method_types=["card"],
        line_items=[{"price": PRICE_ID, "quantity": 1}],
        mode="payment",
        success_url="https://mb-etude-marche.onrender.com/success",
cancel_url="https://mb-etude-marche.onrender.com/cancel",
        customer_email=email,
        metadata=metadata,
    )
    return RedirectResponse(session.url, status_code=303)


@app.post("/webhook")
async def stripe_webhook(request: Request, background_tasks: BackgroundTasks):
    payload    = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET
        )
    except stripe.error.SignatureVerificationError:
        return JSONResponse({"error": "Signature invalide"}, status_code=400)

    if event["type"] == "checkout.session.completed":
        session   = event["data"]["object"]
        form_data = dict(session["metadata"]) if session["metadata"] else {}
        background_tasks.add_task(generer_et_envoyer, form_data)

    return JSONResponse({"status": "ok"})


@app.get("/success", response_class=HTMLResponse)
async def success():
    return """<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Commande confirmée — MB Consulting</title>
  <style>
    *{box-sizing:border-box;margin:0;padding:0}
    body{font-family:'Segoe UI',system-ui,sans-serif;background:#F4F6F8;
         display:flex;align-items:center;justify-content:center;min-height:100vh;padding:2rem}
    .card{background:#fff;border-radius:12px;padding:3rem 2.5rem;max-width:520px;
          width:100%;text-align:center;box-shadow:0 4px 24px rgba(0,0,0,.08)}
    .icon{font-size:3rem;margin-bottom:1.5rem}
    h1{color:#1B3A57;font-size:1.6rem;margin-bottom:1rem}
    p{color:#555;line-height:1.7;margin-bottom:.75rem}
    .box{background:#F0F7FF;border-left:3px solid #1B3A57;padding:1rem 1.25rem;
         border-radius:4px;margin:1.5rem 0;text-align:left;color:#1B3A57;font-size:.95rem}
  </style>
</head>
<body>
  <div class="card">
    <div class="icon">✅</div>
    <h1>Paiement confirmé</h1>
    <p>Votre étude de marché est en cours de génération.</p>
    <div class="box">
      Vous recevrez votre rapport par email <strong>dans les 30 à 60 minutes</strong>.<br><br>
      Pensez à vérifier vos spams si vous ne le voyez pas arriver.
    </div>
    <p>Une question ? <strong>contact@mbconsulting.fr</strong></p>
  </div>
</body>
</html>"""


@app.get("/cancel", response_class=HTMLResponse)
async def cancel():
    return """<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <title>Annulé — MB Consulting</title>
  <style>
    *{box-sizing:border-box;margin:0;padding:0}
    body{font-family:'Segoe UI',system-ui,sans-serif;background:#F4F6F8;
         display:flex;align-items:center;justify-content:center;min-height:100vh;padding:2rem}
    .card{background:#fff;border-radius:12px;padding:3rem 2.5rem;max-width:520px;
          width:100%;text-align:center;box-shadow:0 4px 24px rgba(0,0,0,.08)}
    h1{color:#1B3A57;font-size:1.5rem;margin-bottom:1rem}
    p{color:#555;line-height:1.7}
    a{display:inline-block;margin-top:1.5rem;background:#1B3A57;color:#fff;
      padding:.75rem 2rem;border-radius:6px;text-decoration:none;font-weight:600}
  </style>
</head>
<body>
  <div class="card">
    <div class="icon" style="font-size:3rem;margin-bottom:1.5rem">↩️</div>
    <h1>Paiement non finalisé</h1>
    <p>Aucun montant n'a été débité.</p>
    <a href="/">Recommencer</a>
  </div>
</body>
</html>"""


@app.get("/health")
async def health():
    return {"status": "ok"}

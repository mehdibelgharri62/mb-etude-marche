"""
BACKEND FastAPI — main.py
=========================
Compatible avec le contrat final validé pour generate_content.py et generate_report.py :
  gc.main(project_input: dict, output_file: str | None = None) -> dict
  gr.generate_pdf(content_file: str, output_path: str, quality_file: str | None = None) -> dict

Flux :
  1. POST /submit   → reçoit le formulaire, crée session Stripe Checkout
  2. GET  /success  → page "rapport en cours"
  3. POST /webhook  → Stripe confirme → génération PDF → email TOUJOURS envoyé à OWNER_EMAIL

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
import importlib.util
import sys
from pathlib import Path
from datetime import datetime

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
# CHAMPS QUI NE DOIVENT JAMAIS ÊTRE INJECTÉS DANS LES PROMPTS DE GÉNÉRATION
# (utilisés uniquement pour le suivi commercial / l'email admin)
# ---------------------------------------------------------------------------
CHAMPS_COMMERCIAUX_EXCLUS_DU_RAPPORT = {"name", "email", "phone"}


def construire_project_input(form_data: dict) -> dict:
    """
    Construit le dict project_input propre, attendu par gc.main().
    Exclut explicitement name / email / phone (suivi commercial uniquement).
    Convertit budget_eur en int si fourni.
    Convertit specificities (string "a,b,c") en vraie liste.
    """
    project_input = {
        k: v for k, v in form_data.items()
        if k not in CHAMPS_COMMERCIAUX_EXCLUS_DU_RAPPORT
    }

    budget_raw = project_input.get("budget_eur", "")
    if budget_raw and str(budget_raw).strip().isdigit():
        project_input["budget_eur"] = int(budget_raw)
    else:
        project_input.pop("budget_eur", None)

    specs = project_input.get("specificities", "")
    if isinstance(specs, str):
        specs = [s.strip() for s in specs.split(",") if s.strip()]
    project_input["specificities"] = specs

    return project_input


# ---------------------------------------------------------------------------
# EMAIL — TOUJOURS envoyé à OWNER_EMAIL, quel que soit le résultat
# ---------------------------------------------------------------------------
def construire_corps_email(
    form_data: dict,
    project_name: str,
    order_id: str,
    quality_report: dict | None,
    generation_errors: list[str],
    pdf_ok: bool,
    pieces_jointes: list[str],
) -> str:
    statut = "RAPPORT PRÊT"
    if not pdf_ok:
        statut = "ERREUR GÉNÉRATION — AUCUN PDF"
    elif quality_report and quality_report.get("status") != "ready":
        statut = "RAPPORT À VÉRIFIER"
    elif generation_errors:
        statut = "RAPPORT À VÉRIFIER (erreurs partielles)"

    issues = (quality_report or {}).get("issues", [])
    if issues:
        lignes_issues = "\n".join(
            f"- {i.get('section_title', i.get('section_id', '?'))} "
            f"[{i.get('severity', '?')}] : {i.get('reason', '')}"
            for i in issues
        )
    else:
        lignes_issues = "Aucun problème détecté." if pdf_ok else "—"

    lignes_erreurs = "\n".join(f"- {e}" for e in generation_errors) or "Aucune."
    lignes_pj = "\n".join(f"- {Path(p).name}" for p in pieces_jointes) or "Aucune."

    return f"""Nouveau rapport généré — {project_name}

Projet   : {project_name}
Concept  : {form_data.get('concept', '')[:300]}
Type     : {form_data.get('project_type', '')}
Client   : {form_data.get('name', '')}
Email    : {form_data.get('email', '')}
Tel      : {form_data.get('phone', 'Non renseigné')}
Stade    : {form_data.get('stage', '')}
Objectif : {form_data.get('main_goal', '')}
Order ID : {order_id}

Statut : {statut}

Problèmes détectés :
{lignes_issues}

Erreurs techniques :
{lignes_erreurs}

Pièces jointes :
{lignes_pj}
"""


def envoyer_email_final(
    subject: str,
    body: str,
    fichiers_a_joindre: list[str],
):
    """Envoie l'email admin avec toutes les pièces jointes disponibles.
    Ne lève jamais d'exception bloquante : si Brevo échoue, on log seulement."""
    configuration = sib_api_v3_sdk.Configuration()
    configuration.api_key["api-key"] = BREVO_API_KEY
    api = sib_api_v3_sdk.TransactionalEmailsApi(
        sib_api_v3_sdk.ApiClient(configuration)
    )

    attachments = []
    for path in fichiers_a_joindre:
        if path and os.path.exists(path):
            with open(path, "rb") as f:
                attachments.append({
                    "name": Path(path).name,
                    "content": base64.b64encode(f.read()).decode("utf-8"),
                })

    email = sib_api_v3_sdk.SendSmtpEmail(
        to=[{"email": OWNER_EMAIL}],
        sender={"email": OWNER_EMAIL, "name": "MB Consulting — Système"},
        subject=subject,
        text_content=body,
        attachment=attachments if attachments else None,
    )
    try:
        api.send_transac_email(email)
        print(f"✅ Email envoyé à {OWNER_EMAIL} ({len(attachments)} pièce(s) jointe(s))")
    except ApiException as e:
        print(f"❌ Erreur Brevo : {e}")


# ---------------------------------------------------------------------------
# GÉNÉRATION — tourne en background après confirmation Stripe
# Respecte le contrat : gc.main(project_input, output_file=...)
#                       gr.generate_pdf(content_file, output_path, quality_file)
# Envoie TOUJOURS un email, quoi qu'il arrive.
# ---------------------------------------------------------------------------
def generer_et_envoyer(form_data: dict):
    project_name = form_data.get("project_name", "Projet client")
    order_id = form_data.get("stripe_session_id") or datetime.utcnow().strftime("%Y%m%d%H%M%S")

    content_ok = False
    pdf_ok = False
    generation_errors: list[str] = []
    quality_report: dict | None = None

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        content_path = str(tmpdir / f"contenu_{order_id}.json")
        pdf_path      = str(tmpdir / f"rapport_{order_id}.pdf")
        quality_path  = str(tmpdir / f"quality_{order_id}.json")

        project_input = construire_project_input(form_data)

        # --- 1. Génération du contenu ---
        try:
            spec = importlib.util.spec_from_file_location(
                "generate_content_tmp",
                Path(__file__).parent / "generate_content.py"
            )
            gc = importlib.util.module_from_spec(spec)
            sys.modules["generate_content_tmp"] = gc
            spec.loader.exec_module(gc)

            gc.main(project_input, output_file=content_path)
            content_ok = True
            print("✅ Contenu généré")
        except Exception as e:
            generation_errors.append(f"Erreur generate_content.py : {type(e).__name__} - {e}")
            print(f"❌ Erreur génération contenu : {e}")
            traceback.print_exc()

        # --- 2. Génération du PDF — tentée même si le contenu est partiel ---
        if os.path.exists(content_path):
            try:
                spec2 = importlib.util.spec_from_file_location(
                    "generate_report_tmp",
                    Path(__file__).parent / "generate_report.py"
                )
                gr = importlib.util.module_from_spec(spec2)
                sys.modules["generate_report_tmp"] = gr
                spec2.loader.exec_module(gr)

                quality_report = gr.generate_pdf(
                    content_file=content_path,
                    output_path=pdf_path,
                    quality_file=quality_path,
                )
                pdf_ok = os.path.exists(pdf_path)
                print(f"✅ PDF généré : {pdf_path}" if pdf_ok else "❌ PDF non créé")
            except Exception as e:
                generation_errors.append(f"Erreur generate_report.py : {type(e).__name__} - {e}")
                print(f"❌ Erreur génération PDF : {e}")
                traceback.print_exc()
        else:
            generation_errors.append("Aucun fichier de contenu disponible : generate_content.py n'a produit aucune sortie exploitable.")

        # --- 3. Objet email selon le statut réel ---
        if not pdf_ok:
            subject_prefix = "[ERREUR GÉNÉRATION]"
        elif quality_report and quality_report.get("status") == "ready" and not generation_errors:
            subject_prefix = "[RAPPORT PRÊT]"
        else:
            subject_prefix = "[RAPPORT À VÉRIFIER]"

        subject = f"{subject_prefix} Étude de marché - {project_name}"
        body = construire_corps_email(
            form_data=form_data,
            project_name=project_name,
            order_id=order_id,
            quality_report=quality_report,
            generation_errors=generation_errors,
            pdf_ok=pdf_ok,
            pieces_jointes=[pdf_path],
        )

        # --- 4. Email envoyé dans TOUS les cas ---
        envoyer_email_final(
            subject=subject,
            body=body,
            fichiers_a_joindre=[pdf_path],
        )


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
        form_data["stripe_session_id"] = session["id"] if "id" in session else ""
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

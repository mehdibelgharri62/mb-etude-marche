# VERSION FINALE SECURISEE - 12H/72H - MAIL IMMEDIAT
"""
BACKEND FastAPI — main.py
=========================

Flux :
  1. POST /submit   → reçoit le formulaire, crée session Stripe Checkout
  2. GET  /success  → page de confirmation
  3. POST /webhook  → Stripe confirme le paiement
                    → email immédiat de sécurité
                    → génération PDF
                    → email final avec PDF si le PDF existe, même en mode brouillon / à vérifier

Sécurités ajoutées :
  - email immédiat dès paiement validé, avec coordonnées client et téléphone
  - email final envoyé dans tous les cas possibles
  - PDF joint même si le rapport est en mode draft / à vérifier
  - traitement robuste des alertes quality_report["issues"] : dict ou texte simple
  - logs Render exploitables si Brevo échoue
  - anti-double traitement basique des webhooks répétés sur la même instance
"""

import os
import json
import base64
import tempfile
import traceback
import importlib.util
import sys
from pathlib import Path
from datetime import datetime

import stripe
import sib_api_v3_sdk
from fastapi import FastAPI, Request, Form, BackgroundTasks, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# CONFIG — lues depuis .env en local, variables Render en prod
# ---------------------------------------------------------------------------
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")
BREVO_API_KEY = os.environ.get("BREVO_API_KEY")
OWNER_EMAIL = os.environ.get("OWNER_EMAIL")

# Compatibilité : certains déploiements utilisent PRICE_ID, d'autres STRIPE_PRICE_ID.
PRICE_ID = os.environ.get("PRICE_ID") or os.environ.get("STRIPE_PRICE_ID")

missing_vars = [
    name for name, value in {
        "STRIPE_SECRET_KEY": STRIPE_SECRET_KEY,
        "STRIPE_WEBHOOK_SECRET": STRIPE_WEBHOOK_SECRET,
        "BREVO_API_KEY": BREVO_API_KEY,
        "OWNER_EMAIL": OWNER_EMAIL,
        "PRICE_ID ou STRIPE_PRICE_ID": PRICE_ID,
    }.items()
    if not value
]
if missing_vars:
    raise RuntimeError(f"Variables d'environnement manquantes : {', '.join(missing_vars)}")

stripe.api_key = STRIPE_SECRET_KEY

app = FastAPI()
app.mount("/static", StaticFiles(directory="."), name="static")

# Anti-double traitement simple : évite qu'un même webhook Stripe relancé manuellement
# regénère plusieurs fois le même rapport tant que l'instance Render reste active.
PROCESSED_SESSIONS: set[str] = set()


@app.get("/", response_class=HTMLResponse)
async def index():
    with open("index.html", encoding="utf-8") as f:
        return f.read()


@app.get("/extrait-gratuit-etude-marche.pdf")
async def telecharger_extrait_gratuit():
    pdf_path = Path(__file__).parent / "extrait-gratuit-etude-marche.pdf"

    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="PDF extrait gratuit introuvable")

    return FileResponse(
        path=pdf_path,
        media_type="application/pdf",
        filename="extrait-gratuit-etude-marche.pdf",
    )


# ---------------------------------------------------------------------------
# CHAMPS QUI NE DOIVENT JAMAIS ÊTRE INJECTÉS DANS LES PROMPTS DE GÉNÉRATION
# ---------------------------------------------------------------------------
CHAMPS_COMMERCIAUX_EXCLUS_DU_RAPPORT = {"name", "email", "phone"}


def construire_project_input(form_data: dict) -> dict:
    """
    Construit le dict project_input attendu par generate_content.py.
    Exclut name / email / phone : suivi commercial uniquement.
    Convertit budget_eur en int si possible.
    Convertit specificities (string "a,b,c") en liste.
    """
    project_input = {
        k: v for k, v in form_data.items()
        if k not in CHAMPS_COMMERCIAUX_EXCLUS_DU_RAPPORT
    }

    budget_raw = project_input.get("budget_eur", "")
    if budget_raw and str(budget_raw).strip().isdigit():
        project_input["budget_eur"] = int(str(budget_raw).strip())
    else:
        project_input.pop("budget_eur", None)

    specs = project_input.get("specificities", "")
    if isinstance(specs, str):
        specs = [s.strip() for s in specs.split(",") if s.strip()]
    project_input["specificities"] = specs

    return project_input


def format_form_data_for_logs(form_data: dict) -> str:
    """Résumé lisible dans les logs Render, utile si Brevo tombe."""
    safe = {
        "name": form_data.get("name", ""),
        "email": form_data.get("email", ""),
        "phone": form_data.get("phone", ""),
        "project_name": form_data.get("project_name", ""),
        "concept": form_data.get("concept", "")[:1000],
        "project_type": form_data.get("project_type", ""),
        "zone": form_data.get("zone", ""),
        "target_customer": form_data.get("target_customer", ""),
        "main_offer": form_data.get("main_offer", ""),
        "budget_eur": form_data.get("budget_eur", ""),
        "stage": form_data.get("stage", ""),
        "main_goal": form_data.get("main_goal", ""),
        "stripe_session_id": form_data.get("stripe_session_id", ""),
    }
    return json.dumps(safe, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# EMAILS
# ---------------------------------------------------------------------------
def envoyer_email_final(subject: str, body: str, fichiers_a_joindre: list[str] | None = None):
    """
    Envoie un email admin via Brevo.
    Ne lève jamais d'exception bloquante : si Brevo échoue, on loggue tout.
    """
    fichiers_a_joindre = fichiers_a_joindre or []

    try:
        print(f"📧 Préparation email Brevo : {subject}")

        configuration = sib_api_v3_sdk.Configuration()
        configuration.api_key["api-key"] = BREVO_API_KEY
        api = sib_api_v3_sdk.TransactionalEmailsApi(
            sib_api_v3_sdk.ApiClient(configuration)
        )

        attachments = []
        for path in fichiers_a_joindre:
            if path and os.path.exists(path):
                print(f"📎 Pièce jointe ajoutée : {path}")
                with open(path, "rb") as f:
                    attachments.append({
                        "name": Path(path).name,
                        "content": base64.b64encode(f.read()).decode("utf-8"),
                    })
            elif path:
                print(f"⚠️ Pièce jointe introuvable : {path}")

        email = sib_api_v3_sdk.SendSmtpEmail(
            to=[{"email": OWNER_EMAIL}],
            sender={"email": OWNER_EMAIL, "name": "MB Consulting — Système"},
            subject=subject,
            text_content=body,
            attachment=attachments if attachments else None,
        )

        print(f"📨 Envoi Brevo en cours vers {OWNER_EMAIL}...")
        api.send_transac_email(email)
        print(f"✅ Email envoyé à {OWNER_EMAIL} ({len(attachments)} pièce(s) jointe(s))")

    except Exception as e:
        print(f"❌ ERREUR EMAIL/BREVO : {type(e).__name__} - {e}")
        print("--- Corps email qui aurait dû partir ---")
        print(body)
        print("--- Fin corps email ---")
        traceback.print_exc()


def construire_corps_email_commande(form_data: dict, order_id: str) -> str:
    """Email immédiat de sécurité dès paiement validé."""
    return f"""Paiement validé — nouvelle commande étude de marché

Projet   : {form_data.get('project_name', '')}
Concept  : {form_data.get('concept', '')[:1000]}
Type     : {form_data.get('project_type', '')}
Zone     : {form_data.get('zone', '')}
Cible    : {form_data.get('target_customer', '')}
Offre    : {form_data.get('main_offer', '')}
Budget   : {form_data.get('budget_eur', '')}
Stade    : {form_data.get('stage', '')}
Objectif : {form_data.get('main_goal', '')}

Client   : {form_data.get('name', '')}
Email    : {form_data.get('email', '')}
Tel      : {form_data.get('phone', 'Non renseigné')}
Order ID : {order_id}

Message automatique de sécurité : le paiement est validé et la génération va démarrer.
Si la génération ou l'envoi final échoue, ces informations permettent de recontacter le client.
"""


def format_issue(issue) -> str:
    """Robuste : quality_report['issues'] peut contenir des dicts ou du texte simple."""
    if isinstance(issue, dict):
        titre = issue.get("section_title") or issue.get("section_id") or "Section non précisée"
        severity = issue.get("severity") or issue.get("level") or "info"
        reason = issue.get("reason") or issue.get("message") or issue.get("detail") or ""
        return f"- {titre} [{severity}] : {reason}".strip()
    return f"- {str(issue)}"


def construire_corps_email_rapport(
    form_data: dict,
    project_name: str,
    order_id: str,
    quality_report: dict | None,
    generation_errors: list[str],
    pdf_ok: bool,
    pieces_jointes: list[str],
    cout_api_estime: dict | None = None,
) -> str:
    """Email final robuste : part même si le PDF est en brouillon / à vérifier."""
    statut = "RAPPORT PRÊT"
    if not pdf_ok:
        statut = "ERREUR GÉNÉRATION — AUCUN PDF"
    elif quality_report and quality_report.get("status") != "ready":
        statut = "RAPPORT À VÉRIFIER"
    elif generation_errors:
        statut = "RAPPORT À VÉRIFIER (erreurs partielles)"

    issues = (quality_report or {}).get("issues", [])
    if isinstance(issues, dict):
        issues = [issues]
    elif isinstance(issues, str):
        issues = [issues]
    elif not isinstance(issues, list):
        issues = [str(issues)]

    lignes_issues = "\n".join(format_issue(i) for i in issues) if issues else (
        "Aucun problème détecté." if pdf_ok else "—"
    )

    lignes_erreurs = "\n".join(f"- {e}" for e in generation_errors) or "Aucune."

    fichiers_existants = []
    fichiers_absents = []
    for p in pieces_jointes:
        if p and os.path.exists(p):
            fichiers_existants.append(Path(p).name)
        elif p:
            fichiers_absents.append(str(p))

    lignes_pj = "\n".join(f"- {nom}" for nom in fichiers_existants) if fichiers_existants else "Aucune pièce jointe disponible."
    if fichiers_absents:
        lignes_pj += "\n\nFichiers attendus mais introuvables :\n" + "\n".join(f"- {p}" for p in fichiers_absents)

    if cout_api_estime:
        cout_api_ligne = (
            f"Coût API estimé : ~{float(cout_api_estime.get('cout_total_usd', 0) or 0):.3f} $ US\n"
            f"Tokens entrée/sortie : {cout_api_estime.get('tokens_in', 0)} / {cout_api_estime.get('tokens_out', 0)}\n"
            f"Requêtes recherche web : {cout_api_estime.get('requetes_recherche', 0)}"
        )
    else:
        cout_api_ligne = "Coût API estimé : non disponible."

    return f"""Nouveau rapport généré — {project_name}

Projet   : {project_name}
Concept  : {form_data.get('concept', '')[:1000]}
Type     : {form_data.get('project_type', '')}
Zone     : {form_data.get('zone', '')}
Cible    : {form_data.get('target_customer', '')}
Offre    : {form_data.get('main_offer', '')}
Budget   : {form_data.get('budget_eur', '')}
Stade    : {form_data.get('stage', '')}
Objectif : {form_data.get('main_goal', '')}

Client   : {form_data.get('name', '')}
Email    : {form_data.get('email', '')}
Tel      : {form_data.get('phone', 'Non renseigné')}
Order ID : {order_id}

{cout_api_ligne}

Statut : {statut}

Problèmes détectés :
{lignes_issues}

Erreurs techniques :
{lignes_erreurs}

Pièces jointes :
{lignes_pj}
"""


def construire_corps_email_rapport_fallback(
    form_data: dict,
    project_name: str,
    order_id: str,
    erreur: Exception,
    pieces_jointes: list[str],
) -> str:
    """Dernier filet de sécurité si construire_corps_email_rapport plante."""
    fichiers = []
    for p in pieces_jointes:
        fichiers.append(f"- {Path(p).name if p else '—'} | existe={bool(p and os.path.exists(p))}")

    return f"""Rapport généré — email fallback de sécurité

Projet   : {project_name}
Concept  : {form_data.get('concept', '')[:1000]}
Client   : {form_data.get('name', '')}
Email    : {form_data.get('email', '')}
Tel      : {form_data.get('phone', 'Non renseigné')}
Order ID : {order_id}

Le rapport a atteint l'étape d'envoi email, mais la construction du mail détaillé a rencontré une erreur.
Erreur : {type(erreur).__name__} - {erreur}

Pièces jointes prévues :
{chr(10).join(fichiers) if fichiers else 'Aucune.'}
"""


# ---------------------------------------------------------------------------
# GÉNÉRATION — tourne en background après confirmation Stripe
# ---------------------------------------------------------------------------
def generer_et_envoyer(form_data: dict):
    project_name = form_data.get("project_name", "Projet client")
    order_id = form_data.get("stripe_session_id") or datetime.utcnow().strftime("%Y%m%d%H%M%S")

    print("🚀 Début génération rapport")
    print("--- COMMANDE CLIENT ---")
    print(format_form_data_for_logs(form_data))
    print("--- FIN COMMANDE CLIENT ---")

    pdf_ok = False
    generation_errors: list[str] = []
    quality_report: dict | None = None
    cout_api_estime: dict | None = None

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        content_path = str(tmpdir / f"contenu_{order_id}.json")
        pdf_path = str(tmpdir / f"rapport_{order_id}.pdf")
        quality_path = str(tmpdir / f"quality_{order_id}.json")

        project_input = construire_project_input(form_data)

        # --- 1. Génération du contenu ---
        try:
            spec = importlib.util.spec_from_file_location(
                "generate_content_tmp",
                Path(__file__).parent / "generate_content.py",
            )
            if spec is None or spec.loader is None:
                raise RuntimeError("Impossible de charger generate_content.py")

            gc = importlib.util.module_from_spec(spec)
            sys.modules["generate_content_tmp"] = gc
            spec.loader.exec_module(gc)

            content_result = gc.main(project_input, output_file=content_path)

            if isinstance(content_result, dict):
                cout_api_estime = content_result.get("_cout_api_estime")

            if cout_api_estime is None and os.path.exists(content_path):
                try:
                    with open(content_path, "r", encoding="utf-8") as f:
                        loaded_content = json.load(f)
                    if isinstance(loaded_content, dict):
                        cout_api_estime = loaded_content.get("_cout_api_estime")
                except Exception:
                    cout_api_estime = None

            print("✅ Contenu généré")

        except Exception as e:
            generation_errors.append(f"Erreur generate_content.py : {type(e).__name__} - {e}")
            print(f"❌ Erreur génération contenu : {e}")
            traceback.print_exc()

        # --- 2. Génération du PDF — tentée dès qu'un contenu existe ---
        if os.path.exists(content_path):
            try:
                spec2 = importlib.util.spec_from_file_location(
                    "generate_report_tmp",
                    Path(__file__).parent / "generate_report.py",
                )
                if spec2 is None or spec2.loader is None:
                    raise RuntimeError("Impossible de charger generate_report.py")

                gr = importlib.util.module_from_spec(spec2)
                sys.modules["generate_report_tmp"] = gr
                spec2.loader.exec_module(gr)

                quality_report = gr.generate_pdf(
                    content_file=content_path,
                    output_path=pdf_path,
                    quality_file=quality_path,
                )

                if quality_report is None and os.path.exists(quality_path):
                    try:
                        with open(quality_path, "r", encoding="utf-8") as f:
                            quality_report = json.load(f)
                    except Exception:
                        quality_report = None

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
        elif quality_report and isinstance(quality_report, dict) and quality_report.get("status") == "ready" and not generation_errors:
            subject_prefix = "[RAPPORT PRÊT]"
        else:
            subject_prefix = "[RAPPORT À VÉRIFIER]"

        subject = f"{subject_prefix} Étude de marché - {project_name}"

        try:
            body = construire_corps_email_rapport(
                form_data=form_data,
                project_name=project_name,
                order_id=order_id,
                quality_report=quality_report if isinstance(quality_report, dict) else None,
                generation_errors=generation_errors,
                pdf_ok=pdf_ok,
                pieces_jointes=[pdf_path],
                cout_api_estime=cout_api_estime,
            )
        except Exception as e:
            print(f"⚠️ Erreur construction mail détaillé : {type(e).__name__} - {e}")
            traceback.print_exc()
            body = construire_corps_email_rapport_fallback(
                form_data=form_data,
                project_name=project_name,
                order_id=order_id,
                erreur=e,
                pieces_jointes=[pdf_path],
            )
            subject = f"[RAPPORT - EMAIL FALLBACK] Étude de marché - {project_name}"

        # --- 4. Email final envoyé dans tous les cas ; PDF joint s'il existe ---
        envoyer_email_final(
            subject=subject,
            body=body,
            fichiers_a_joindre=[pdf_path],
        )

        print("🏁 Fin génération rapport")


# ---------------------------------------------------------------------------
# ROUTES
# ---------------------------------------------------------------------------
@app.post("/submit")
async def submit_form(
    request: Request,
    # Obligatoires
    email: str = Form(...),
    name: str = Form(...),
    project_name: str = Form(...),
    concept: str = Form(...),
    project_type: str = Form(...),
    zone: str = Form(...),
    target_customer: str = Form(...),
    main_offer: str = Form(...),
    stage: str = Form(...),
    # Optionnels
    budget_eur: str = Form(default=""),
    revenue_model: str = Form(default=""),
    specificities: str = Form(default=""),
    additional_context: str = Form(default=""),
    main_goal: str = Form(default=""),
    phone: str = Form(default=""),
):
    def trunc(v):
        return str(v)[:500]

    metadata = {
        "email": trunc(email),
        "name": trunc(name),
        "project_name": trunc(project_name),
        "concept": trunc(concept),
        "project_type": trunc(project_type),
        "zone": trunc(zone),
        "target_customer": trunc(target_customer),
        "main_offer": trunc(main_offer),
        "stage": trunc(stage),
        "budget_eur": trunc(budget_eur),
        "revenue_model": trunc(revenue_model),
        "specificities": trunc(specificities),
        "additional_context": trunc(additional_context),
        "main_goal": trunc(main_goal),
        "phone": trunc(phone),
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
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET
        )
    except stripe.error.SignatureVerificationError:
        print("❌ Signature webhook Stripe invalide")
        return JSONResponse({"error": "Signature invalide"}, status_code=400)
    except Exception as e:
        print(f"❌ Erreur lecture webhook Stripe : {type(e).__name__} - {e}")
        traceback.print_exc()
        return JSONResponse({"error": "Webhook invalide"}, status_code=400)

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        session_id = session.get("id", "")

        if session_id and session_id in PROCESSED_SESSIONS:
            print(f"ℹ️ Webhook déjà traité pour session {session_id} — ignoré")
            return JSONResponse({"status": "ok", "duplicate": True})

        if session_id:
            PROCESSED_SESSIONS.add(session_id)

        form_data = dict(session.get("metadata") or {})
        form_data["stripe_session_id"] = session_id

        print("✅ Paiement validé Stripe — commande reçue")
        print("--- COMMANDE CLIENT APRÈS PAIEMENT ---")
        print(format_form_data_for_logs(form_data))
        print("--- FIN COMMANDE CLIENT APRÈS PAIEMENT ---")

        # Email immédiat de sécurité : coordonnées client + téléphone + projet.
        # IMPORTANT : envoi direct, AVANT la génération, pour ne jamais dépendre du PDF.
        # envoyer_email_final ne lève pas d'exception bloquante : en cas d'échec Brevo,
        # l'erreur est logguée et la génération démarre quand même.
        print("📩 Envoi immédiat du mail de sécurité commande...")
        envoyer_email_final(
            subject=f"[PAIEMENT VALIDÉ] Nouvelle commande - {form_data.get('project_name', 'Projet client')}",
            body=construire_corps_email_commande(form_data, session_id),
            fichiers_a_joindre=[],
        )
        print("✅ Étape mail de sécurité terminée — lancement génération en arrière-plan")

        # Génération + email final avec PDF si disponible.
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
    <p>Votre commande est confirmée.</p>
    <div class="box">
      MB Consulting vous recontactera <strong>sous 12h</strong> pour cadrer votre projet et ajuster l'angle de l'étude.<br><br>
      Votre étude de marché personnalisée sera ensuite livrée par email <strong>sous 72h</strong> après cadrage.
    </div>
    <p>Une question ? <strong>contact@mbconsulting-formation.fr</strong></p>
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

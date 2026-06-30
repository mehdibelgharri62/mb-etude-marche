"""
MB CONSULTING — Génération PDF du rapport final
============================================
Lit contenu_genere.json puis génère un PDF plus aéré :
  - 15 sections au lieu de 30.
  - Pas de PageBreak systématique après chaque section.
  - Tableaux limités : 4 colonnes max, cellules courtes, repli en fiches si trop dense.
  - Vraies listes à puces.
  - Styles plus lisibles : corps 10.8 pt, interligne 16 pt, espacements réguliers.
  - V4.1 bis : compactage éditorial des tableaux larges, suppression des résidus G/Markdown.
  - Contrôle qualité non bloquant : PDF brouillon généré et diagnostics exportés.

Aucun appel API ici.
"""

from __future__ import annotations

from datetime import date
import json
import os
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    CondPageBreak,
    HRFlowable,
    KeepTogether,
    ListFlowable,
    ListItem,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# ----------------------------------------------------------------------------
# 0. FICHIERS ET CONTENU
# ----------------------------------------------------------------------------

CONTENT_FILE = os.environ.get("CONTENT_FILE", "contenu_genere.json")
OUTPUT_PATH = os.environ.get("OUTPUT_PATH", "etude_de_marche.pdf")

REAL_CONTENT: Dict[str, Any] = {}
if os.path.exists(CONTENT_FILE):
    with open(CONTENT_FILE, "r", encoding="utf-8") as f:
        REAL_CONTENT = json.load(f)
    print(f"Contenu trouvé : {CONTENT_FILE}")
else:
    print(f"Aucun {CONTENT_FILE} trouvé — PDF brouillon de diagnostic généré.")

PROJECT_INPUT = REAL_CONTENT.get("_project_input", {})
PROJECT_PROFILE = REAL_CONTENT.get("_project_profile", {})

CLIENT_VILLE = PROJECT_INPUT.get("zone", "Zone à préciser")
CLIENT_CONCEPT = PROJECT_INPUT.get("concept", "Concept à préciser")
CLIENT_PROJECT_NAME = PROJECT_INPUT.get("project_name", "")
DATE_RAPPORT = date.today().strftime("%d/%m/%Y")
NOM_CABINET = "MB Consulting"

SECTION_TITLES = REAL_CONTENT.get("_section_titles", {})
DEFAULT_SECTIONS_PDF: List[Tuple[str, str]] = [
    ("s01_synthese_decisionnelle", "Synthèse décisionnelle"),
    ("s02_concept_perimetre", "Concept, périmètre et hypothèses de départ"),
    ("s03_hypotheses_chiffrees", "Dictionnaire unique des hypothèses chiffrées"),
    ("s04_demande_acces_marche", "Demande, accès au marché et zone utile"),
    ("s05_tendances_utiles", "Tendances marché utiles au projet"),
    ("s06_concurrence_synthese", "Concurrence, benchmark et synthèse stratégique intégrée"),
    ("s07_personas", "Personas clients et situations d'achat"),
    ("s08_positionnement", "Positionnement recommandé et différenciation"),
    ("s09_offre_prix_revenus", "Offre, prix, packs et sources de revenus"),
    ("s10_operations_contraintes", "Canaux, emplacement et contraintes opérationnelles"),
    ("s11_modele_economique", "Modèle économique et structure de coûts"),
    ("s12_scenarios_financiers", "Scénarios financiers, point mort et KPIs"),
    ("s13_validation_mvp", "Validation terrain / MVP avant investissement complet"),
    ("s14_marketing_90j", "Plan marketing de lancement et calendrier 90 jours"),
    ("s15_verdict_plan_action", "Verdict d'opportunité, risques et plan d'action final"),
]

SECTIONS_PDF = [(sid, SECTION_TITLES.get(sid, title)) for sid, title in DEFAULT_SECTIONS_PDF]

# V4.1 : pas de contenu de démonstration dans un PDF réel.
DEMO_MODE = False
STRICT_MODE = False  # En production : ne bloque jamais le PDF ; les issues passent en brouillon.
REPAIRABLE_ERROR_PREFIXES = ("Contrôle qualité à revoir", "Contrôle qualité")

# ----------------------------------------------------------------------------
# 1. CHARTE ET STYLES
# ----------------------------------------------------------------------------

COLOR_PRIMARY = colors.HexColor("#1B3A6B")      # navy MB Consulting
COLOR_ACCENT = colors.HexColor("#D4891A")       # amber MB Consulting
COLOR_TEXT = colors.HexColor("#1F2933")
COLOR_MUTED = colors.HexColor("#667085")
COLOR_LIGHT_BG = colors.HexColor("#F5F7FA")
COLOR_BOX_BG = colors.HexColor("#FFF8ED")
COLOR_BORDER = colors.HexColor("#D9E2EC")
COLOR_ALERT_BG = colors.HexColor("#FBEAEA")
COLOR_ALERT_BORDER = colors.HexColor("#C0392B")

BASE_FONT = "Helvetica"
BASE_FONT_BOLD = "Helvetica-Bold"

styles = getSampleStyleSheet()
styles.add(ParagraphStyle(
    name="CoverKicker", fontName=BASE_FONT_BOLD, fontSize=12,
    textColor=COLOR_ACCENT, alignment=TA_CENTER, spaceAfter=8, leading=14,
))
styles.add(ParagraphStyle(
    name="CoverTitle", fontName=BASE_FONT_BOLD, fontSize=27,
    textColor=COLOR_PRIMARY, alignment=TA_CENTER, spaceAfter=8, leading=32,
))
styles.add(ParagraphStyle(
    name="CoverSubtitle", fontName=BASE_FONT, fontSize=13,
    textColor=COLOR_MUTED, alignment=TA_CENTER, spaceAfter=5, leading=17,
))
styles.add(ParagraphStyle(
    name="SectionTitle", fontName=BASE_FONT_BOLD, fontSize=17,
    textColor=colors.white, leading=21, alignment=TA_LEFT,
))
styles.add(ParagraphStyle(
    name="SubHeading", fontName=BASE_FONT_BOLD, fontSize=13,
    textColor=colors.white, leading=16, alignment=TA_LEFT,
))
styles.add(ParagraphStyle(
    name="SubSubHeading", fontName=BASE_FONT_BOLD, fontSize=11.6,
    textColor=COLOR_PRIMARY, leading=15, spaceBefore=10, spaceAfter=5,
))
styles.add(ParagraphStyle(
    name="Body", fontName=BASE_FONT, fontSize=10.8,
    leading=16.2, textColor=COLOR_TEXT, alignment=TA_LEFT, spaceAfter=8,
))
styles.add(ParagraphStyle(
    name="BulletText", fontName=BASE_FONT, fontSize=10.6,
    leading=15.3, textColor=COLOR_TEXT, leftIndent=0, spaceAfter=4,
))
styles.add(ParagraphStyle(
    name="Footer", fontName=BASE_FONT, fontSize=8,
    textColor=COLOR_MUTED,
))
styles.add(ParagraphStyle(
    name="TableCell", fontName=BASE_FONT, fontSize=8.6,
    leading=11.2, textColor=COLOR_TEXT, alignment=TA_LEFT,
))
styles.add(ParagraphStyle(
    name="TableCellHeader", fontName=BASE_FONT_BOLD, fontSize=8.5,
    leading=10.8, textColor=colors.white, alignment=TA_LEFT,
))
styles.add(ParagraphStyle(
    name="BoxTitle", fontName=BASE_FONT_BOLD, fontSize=11.2,
    leading=14, textColor=COLOR_PRIMARY, spaceAfter=4,
))
styles.add(ParagraphStyle(
    name="AlertTitle", fontName=BASE_FONT_BOLD, fontSize=11.2,
    textColor=COLOR_ALERT_BORDER, leading=14, spaceAfter=4,
))
styles.add(ParagraphStyle(
    name="AlertText", fontName=BASE_FONT, fontSize=10.4,
    leading=14.5, textColor=colors.HexColor("#7A1F1F"),
))
styles.add(ParagraphStyle(
    name="DraftBanner", fontName=BASE_FONT_BOLD, fontSize=12,
    textColor=colors.white, alignment=TA_CENTER, leading=15, spaceAfter=0,
))
styles.add(ParagraphStyle(
    name="DiagnosticText", fontName=BASE_FONT, fontSize=9.2,
    leading=12.4, textColor=COLOR_TEXT,
))
styles.add(ParagraphStyle(
    name="TOCLine", fontName=BASE_FONT, fontSize=10.5,
    leading=15, textColor=COLOR_TEXT, spaceAfter=4,
))
styles.add(ParagraphStyle(
    name="CTATitle", fontName=BASE_FONT_BOLD, fontSize=16,
    textColor=colors.white, alignment=TA_CENTER, spaceAfter=8, leading=19,
))
styles.add(ParagraphStyle(
    name="CTABody", fontName=BASE_FONT, fontSize=11,
    textColor=colors.white, alignment=TA_CENTER, leading=15.5, spaceAfter=10,
))

# ----------------------------------------------------------------------------
# 2. NETTOYAGE / MARKDOWN SIMPLE
# ----------------------------------------------------------------------------

def escape_xml(text: Any) -> str:
    text = str(text)
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def md_inline_to_xml(text: Any) -> str:
    """Markdown inline minimal -> ReportLab XML."""
    text = escape_xml(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    # Italique Markdown simple : utile pour nettoyer des résidus comme *votre*.
    # On évite de transformer les astérisques de début de ligne : les listes sont gérées ailleurs.
    text = re.sub(r"(?<!\*)\*([^*\n]{1,80})\*(?!\*)", r"<i>\1</i>", text)
    text = re.sub(r"`([^`]+)`", r"<font name='Helvetica'>\1</font>", text)
    return text


def strip_markdown_noise(text: str) -> str:
    text = str(text or "")
    # Artefacts HTML fréquents : réparables, jamais bloquants.
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"&lt;br\s*/?&gt;", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"\\u003cbr\s*/?\\u003e", "\n", text, flags=re.IGNORECASE)
    text = text.replace("\u2013", "-").replace("\u2014", "-")
    # Supprime les séparateurs Markdown horizontaux (---) qui ne doivent pas apparaître dans un rapport final.
    text = re.sub(r"(?m)^\s*-{3,}\s*$", "", text)
    text = re.sub(r"(?m)^\s*---\s*(?=\w)", "", text)
    # Nettoyage de résidus observés dans la V4.1 :
    # - lignes isolées "G" ou "G ..." issues d'une coche mal convertie ;
    # - titres markdown restant en gras.
    text = re.sub(r"(?m)^\s*G\s*$", "", text)
    text = re.sub(r"(?m)^\s*G\s+", "- ", text)
    text = text.replace("*Définition :*", "Définition :").replace("*Définition:*", "Définition :")
    # Neutralise les fences de code si Gemini en laisse.
    text = text.replace("```", "")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

# ----------------------------------------------------------------------------
# 3. COMPOSANTS VISUELS
# ----------------------------------------------------------------------------

def section_header(num: Optional[int], title: str) -> Table:
    heading = f"{num}. {title}" if num is not None else title
    content = Paragraph(heading, styles["SectionTitle"])
    table = Table([[content]], colWidths=[17 * cm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), COLOR_PRIMARY),
        ("BOX", (0, 0), (-1, -1), 0, COLOR_PRIMARY),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
    ]))
    return table


def subheading_banner(title: str) -> Table:
    table = Table([[Paragraph(title, styles["SubHeading"])]], colWidths=[17 * cm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), COLOR_ACCENT),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
    ]))
    return table


def callout_box(title: str, body: Sequence[str], alert: bool = False) -> Table:
    title_style = styles["AlertTitle"] if alert else styles["BoxTitle"]
    body_style = styles["AlertText"] if alert else styles["Body"]
    bg = COLOR_ALERT_BG if alert else COLOR_BOX_BG
    border = COLOR_ALERT_BORDER if alert else COLOR_ACCENT
    content: List[Any] = [Paragraph(md_inline_to_xml(title), title_style)]
    for line in body:
        if line.strip():
            content.append(Paragraph(md_inline_to_xml(line.strip()), body_style))
    table = Table([[content]], colWidths=[17 * cm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg),
        ("BOX", (0, 0), (-1, -1), 0.8, border),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    return table



def draft_banner(issues: Sequence[str]) -> Table:
    title = "BROUILLON - À VÉRIFIER"
    subtitle = f"{len(issues)} point(s) de contrôle détecté(s). Rapport généré pour relecture interne uniquement."
    table = Table([
        [Paragraph(title, styles["DraftBanner"])],
        [Paragraph(md_inline_to_xml(subtitle), styles["CTABody"])],
    ], colWidths=[17 * cm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), COLOR_ALERT_BORDER),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
    ]))
    return table


def issues_diagnostic_block(issues: Sequence[str]) -> List[Any]:
    if not issues:
        return []
    body = [f"- {issue}" for issue in issues[:30]]
    if len(issues) > 30:
        body.append(f"- {len(issues) - 30} autre(s) point(s) non affiché(s) ici.")
    return [callout_box("Diagnostic interne - points à vérifier", body, alert=True), Spacer(1, 12)]


def cta_block() -> Table:
    content = [
        Paragraph("Besoin d'aller plus loin ?", styles["CTATitle"]),
        Paragraph(
            "MB Consulting peut vous accompagner pour transformer cette étude en plan d'action, "
            "offre commerciale, stratégie de lancement ou dossier de financement.",
            styles["CTABody"],
        ),
    ]
    table = Table([[content]], colWidths=[17 * cm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), COLOR_PRIMARY),
        ("TOPPADDING", (0, 0), (-1, -1), 22),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 22),
        ("LEFTPADDING", (0, 0), (-1, -1), 22),
        ("RIGHTPADDING", (0, 0), (-1, -1), 22),
    ]))
    return table

# ----------------------------------------------------------------------------
# 4. TABLEAUX : cellules courtes, fallback en fiches si trop dense
# ----------------------------------------------------------------------------

def parse_markdown_table(block: str) -> Tuple[Optional[List[str]], List[List[str]]]:
    lines = [l.strip() for l in block.strip().split("\n") if l.strip()]
    lines = [l for l in lines if not re.match(r"^\|?[\s:|-]+\|?$", l)]
    rows: List[List[str]] = []
    for line in lines:
        if "|" not in line:
            continue
        rows.append([c.strip() for c in line.strip("|").split("|")])
    if not rows:
        return None, []
    return rows[0], rows[1:]


def table_is_too_dense(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> bool:
    if len(headers) > 4:
        return True
    if len(rows) > 10:
        # On accepte légèrement plus que le prompt, mais on évite les monstres.
        return True
    for row in rows:
        for cell in row:
            words = str(cell).split()
            if len(words) > 38 or len(str(cell)) > 260:
                return True
    return False




def compact_cell(text: Any, max_words: int = 34) -> str:
    """Raccourcit une cellule sans créer de pavé illisible."""
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    words = s.split()
    if len(words) <= max_words:
        return s
    return " ".join(words[:max_words]).rstrip(" ,;:") + "…"


def compact_wide_table(
    headers: Sequence[str],
    rows: Sequence[Sequence[str]],
    section_id: str = "",
) -> Tuple[List[str], List[List[str]]]:
    """Ramène les tableaux >4 colonnes à une structure imprimable.

    V4.1 rejetait ces tableaux ou les transformait en fiches trop brutes.
    La V4.1 bis tente d'abord une vraie réduction éditoriale :
    - section 2 : cadrage en 3 colonnes ;
    - section 12 : scénarios financiers en 4 colonnes ;
    - autres sections : 4 colonnes max avec fusion des colonnes finales.
    """
    clean_rows = [list(r) for r in rows if r]
    if len(headers) <= 4:
        return list(headers), [[compact_cell(c, 38) for c in r] for r in clean_rows]

    sid = section_id.lower()
    header_text = " ".join(headers).lower()

    if "s02" in sid or "concept" in sid or "périmètre" in sid:
        new_headers = ["Élément", "Hypothèse retenue", "Impact sur l'analyse"]
        new_rows: List[List[str]] = []
        for row in clean_rows:
            element = row[0] if len(row) > 0 else ""
            hypothese = row[1] if len(row) > 1 else ""
            impact = " ; ".join(str(c).strip() for c in row[2:] if str(c).strip())
            new_rows.append([compact_cell(element, 14), compact_cell(hypothese, 24), compact_cell(impact, 34)])
        return new_headers, new_rows

    if "s12" in sid or "scénario" in header_text or "resultat" in header_text or "résultat" in header_text:
        # Cas type : Scénario | CA | Charges | Résultat | Lecture
        if "scénario" in str(headers[0]).lower() or "scenario" in str(headers[0]).lower():
            new_headers = ["Scénario", "CA mensuel", "Résultat", "Lecture"]
            new_rows = []
            for row in clean_rows:
                scenario = row[0] if len(row) > 0 else ""
                ca = row[1] if len(row) > 1 else ""
                resultat = row[-2] if len(row) >= 4 else (row[2] if len(row) > 2 else "")
                lecture = row[-1] if len(row) >= 2 else ""
                new_rows.append([compact_cell(scenario, 10), compact_cell(ca, 14), compact_cell(resultat, 14), compact_cell(lecture, 32)])
            return new_headers, new_rows

    # Réduction générique : 1re, 2e, 3e colonne, puis fusion du reste.
    new_headers = [compact_cell(headers[0], 8), compact_cell(headers[1], 8), compact_cell(headers[2], 8), "Analyse / point clé"]
    new_rows = []
    for row in clean_rows:
        a = row[0] if len(row) > 0 else ""
        b = row[1] if len(row) > 1 else ""
        c = row[2] if len(row) > 2 else ""
        d = " ; ".join(str(x).strip() for x in row[3:] if str(x).strip())
        new_rows.append([compact_cell(a, 12), compact_cell(b, 16), compact_cell(c, 16), compact_cell(d, 32)])
    return new_headers, new_rows

def fallback_table_cards(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> List[Any]:
    """Convertit un tableau trop large/dense en fiches lisibles."""
    flowables: List[Any] = []
    for row in rows:
        if not row:
            continue
        first_label = str(headers[0]).strip() if headers else "Élément"
        first_value = str(row[0]).strip()
        title = f"{first_label} : {first_value}" if first_value else "Point à retenir"
        lines: List[str] = []
        for h, c in zip(headers[1:], row[1:]):
            if str(c).strip():
                lines.append(f"**{h} :** {c}")
        if not lines:
            for h, c in zip(headers, row):
                if str(c).strip():
                    lines.append(f"**{h} :** {c}")
        flowables.append(callout_box(title, lines, alert=False))
        flowables.append(Spacer(1, 8))
    return flowables


def compute_col_widths(headers: Sequence[str]) -> List[float]:
    n = len(headers)
    total = 17 * cm
    lower_headers = " ".join(headers).lower()
    if n == 2:
        weights = [1.0, 1.8]
    elif n == 3:
        weights = [1.05, 1.25, 1.55]
    elif n == 4:
        # La dernière colonne porte souvent l'analyse / impact.
        weights = [0.95, 1.05, 1.05, 1.35]
    else:
        weights = [1] * n
    s = sum(weights)
    return [total * w / s for w in weights]


def data_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> Table:
    header_row = [Paragraph(md_inline_to_xml(h), styles["TableCellHeader"]) for h in headers]
    data: List[List[Any]] = [header_row]
    for row in rows:
        data.append([Paragraph(md_inline_to_xml(cell), styles["TableCell"]) for cell in row])
    table = Table(data, colWidths=compute_col_widths(headers), repeatRows=1, splitByRow=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), COLOR_PRIMARY),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, COLOR_LIGHT_BG]),
        ("GRID", (0, 0), (-1, -1), 0.4, COLOR_BORDER),
        ("TOPPADDING", (0, 0), (-1, -1), 5.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 5.5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5.5),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return table


def table_flowables(headers: Sequence[str], rows: Sequence[Sequence[str]], section_id: str = "") -> List[Any]:
    # Cas fréquent : Gemini génère 5 colonnes, mais le PDF doit rester lisible.
    headers2, rows2 = compact_wide_table(headers, rows, section_id=section_id)
    normalized_rows = [list(row) for row in rows2 if len(row) == len(headers2)]
    if not normalized_rows:
        return []
    # Après compactage, on privilégie encore un vrai tableau.
    # Le fallback en fiches ne doit servir que pour des contenus vraiment trop longs.
    if table_is_too_dense(headers2, normalized_rows):
        return fallback_table_cards(headers2, normalized_rows)
    return [data_table(headers2, normalized_rows), Spacer(1, 12)]

# ----------------------------------------------------------------------------
# 5. LISTES ET BLOCS MARKDOWN
# ----------------------------------------------------------------------------

def is_bullet_line(line: str) -> bool:
    return bool(re.match(r"^\s*[-•*]\s+", line))


def bullet_flowable(lines: Sequence[str]) -> ListFlowable:
    items = []
    for line in lines:
        clean = re.sub(r"^\s*[-•*]\s+", "", line).strip()
        if clean:
            items.append(ListItem(Paragraph(md_inline_to_xml(clean), styles["BulletText"]), leftIndent=12))
    return ListFlowable(items, bulletType="bullet", start="circle", leftIndent=16, bulletFontSize=7, bulletOffsetY=1)


def split_blocks(markdown_text: str) -> List[str]:
    markdown_text = strip_markdown_noise(markdown_text)
    return [b.strip() for b in re.split(r"\n\s*\n", markdown_text) if b.strip()]


def render_paragraph_block(block: str) -> List[Any]:
    lines = [l.rstrip() for l in block.splitlines() if l.strip()]
    if lines and all(is_bullet_line(l) for l in lines):
        return [bullet_flowable(lines), Spacer(1, 6)]
    # Si un bloc mélange phrase + puces, on le découpe.
    if any(is_bullet_line(l) for l in lines):
        flowables: List[Any] = []
        current_text: List[str] = []
        current_bullets: List[str] = []
        def flush_text() -> None:
            if current_text:
                flowables.append(Paragraph(md_inline_to_xml(" ".join(current_text)), styles["Body"]))
                flowables.append(Spacer(1, 6))
                current_text.clear()
        def flush_bullets() -> None:
            if current_bullets:
                flowables.append(bullet_flowable(current_bullets))
                flowables.append(Spacer(1, 6))
                current_bullets.clear()
        for line in lines:
            if is_bullet_line(line):
                flush_text()
                current_bullets.append(line)
            else:
                flush_bullets()
                current_text.append(line)
        flush_text(); flush_bullets()
        return flowables
    return [Paragraph(md_inline_to_xml(" ".join(lines)), styles["Body"]), Spacer(1, 7)]


def normalize_heading_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower().rstrip(" .:;!")


def render_heading_and_rest(stripped: str, section_title: str = "") -> Optional[List[Any]]:
    """Gère les blocs où Gemini écrit `### Titre\nParagraphe`.

    V4 transformait parfois tout le bloc en sous-titre bleu gras. V4.1 ne stylise
    que la première ligne, puis rend le reste en corps de texte.
    """
    lines = [l.strip() for l in stripped.splitlines() if l.strip()]
    if not lines:
        return []
    first = lines[0]
    if not first.startswith("##"):
        return None
    level = 3 if first.startswith("###") else 2
    title = first.lstrip("#").strip()
    # Supprime le titre répété immédiatement après le bandeau bleu de section.
    skip_title = normalize_heading_text(title) == normalize_heading_text(section_title)
    flowables: List[Any] = []
    rest = "\n".join(lines[1:]).strip()
    if not skip_title:
        if level == 2 and title.lower().startswith(("prudence", "attention", "limite", "avertissement")):
            # En V4.1 bis, le titre d'alerte et son texte restent dans le même encadré.
            flowables.append(callout_box(title, [rest] if rest else [], alert=True))
            flowables.append(Spacer(1, 8))
            return flowables
        else:
            flowables.append(Paragraph(md_inline_to_xml(title), styles["SubSubHeading"]))
            flowables.append(Spacer(1, 3))
    if rest:
        flowables.extend(render_paragraph_block(rest))
    return flowables


def real_section_flowables(markdown_text: str, section_title: str = "", section_id: str = "") -> List[Any]:
    flowables: List[Any] = []
    first_content_block = True
    for block in split_blocks(markdown_text):
        if "|" in block and "\n" in block:
            headers, rows = parse_markdown_table(block)
            if headers and rows:
                flowables.extend(table_flowables(headers, rows, section_id=section_id))
                continue
        stripped = block.strip()
        # Supprime aussi les titres répétés sans ## juste après le bandeau de section.
        if first_content_block and normalize_heading_text(stripped) == normalize_heading_text(section_title):
            first_content_block = False
            continue
        first_content_block = False
        rendered_heading = render_heading_and_rest(stripped, section_title)
        if rendered_heading is not None:
            flowables.extend(rendered_heading)
            continue
        flowables.extend(render_paragraph_block(block))
    return flowables

# ----------------------------------------------------------------------------
# 6. QUALITÉ AVANT PDF
# ----------------------------------------------------------------------------

FORBIDDEN_PATTERNS = [
    # Placeholders client-visibles très probables.
    r"\[[A-Za-zÀ-ÿ ]{1,30}\]",
    r"\bX\s*(prises de contact|RDV|rendez-vous|contrats|nouveaux leads|leads|clients|ventes|euros)\b",
    r"\bX\s*(milliers|millions|milliards)\b",
    r"\bY\s*(milliers|millions|milliards)\b",
    r"\bZ\s*(milliers|millions|milliards)\b",
    r"à compléter",
    r"TODO",
    r"Cette section n['’]a pas pu être générée",
    # Fuites de prompt / consignes internes.
    r"Color know the following code snippet",
    r"Code Snippet provides additional context",
    r"complete the analysis on existing input",
    r"user is asking to complete",
    r"existing input",
    r"continue from where",
    r"Do not regenerate",
]


def detect_content_issues() -> List[str]:
    issues: List[str] = []
    if not REAL_CONTENT:
        return ["Aucun contenu JSON disponible."]
    embedded_qr = REAL_CONTENT.get("_quality_report", {})
    if isinstance(embedded_qr, dict):
        for sid, payload in embedded_qr.get("sections", {}).items():
            title = SECTION_TITLES.get(sid, sid)
            for issue in payload.get("blocking", []) or []:
                issues.append(f"{title} : {issue}")
    for sid, title in SECTIONS_PDF:
        item = REAL_CONTENT.get(sid)
        if not item or not item.get("texte"):
            issues.append(f"{title} : section manquante.")
            continue
        err = item.get("erreur")
        if err and not str(err).startswith(REPAIRABLE_ERROR_PREFIXES):
            issues.append(f"{title} : section en échec ({err}).")
        text = strip_markdown_noise(item.get("texte", ""))
        for pattern in FORBIDDEN_PATTERNS:
            if re.search(pattern, text, flags=re.IGNORECASE):
                issues.append(f"{title} : élément interdit détecté ({pattern}).")
                break
        # Détection prudente : on évite les faux positifs comme "à vérifier" ou "à valider".
        # On signale surtout les fins visiblement coupées par une ellipse après un connecteur.
        if re.search(r"(hypothèse de|source|notamment|avec|pour|afin de|en cas de)\s*…\s*$", text.strip(), flags=re.IGNORECASE):
            issues.append(f"{title} : texte probablement tronqué en fin de section.")
    # Déduplique en conservant l'ordre.
    return list(dict.fromkeys(issues))


PDF_ISSUES: List[str] = detect_content_issues()
PDF_STATUS = "draft" if PDF_ISSUES else "ready"


def _issue_to_object(issue: str) -> Dict[str, Any]:
    section_title = "Général"
    reason = issue
    if " : " in issue:
        section_title, reason = issue.split(" : ", 1)
    severity = "warning"
    low = reason.lower()
    if any(marker in low for marker in ["fuite", "interdit", "placeholder", "section manquante", "échec", "tronqué", "aucun contenu"]):
        severity = "blocking"
    elif any(marker in low for marker in ["répar", "markdown", "tableau", "source trop vague"]):
        severity = "repairable"
    return {"section_title": section_title, "severity": severity, "reason": reason}


def export_pdf_issues(path: str = "rapport_quality_issues.json") -> Dict[str, Any]:
    """Écrit et retourne un diagnostic lisible par main.py pour composer l'objet/corps d'email."""
    payload = {
        "status": PDF_STATUS,
        "email_subject_prefix": "[RAPPORT À VÉRIFIER]" if PDF_ISSUES else "[RAPPORT PRÊT]",
        "issues": PDF_ISSUES,
        "issues_detailed": [_issue_to_object(issue) for issue in PDF_ISSUES],
        "issues_count": len(PDF_ISSUES),
        "output_path": OUTPUT_PATH,
        "content_file": CONTENT_FILE,
    }
    if path:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    return payload

# ----------------------------------------------------------------------------
# 7. STORY PDF
# ----------------------------------------------------------------------------

def render_section(section_id: str, section_title: str = "") -> List[Any]:
    item = REAL_CONTENT.get(section_id)
    if item and item.get("texte"):
        err = item.get("erreur")
        flowables = real_section_flowables(strip_markdown_noise(item["texte"]), section_title=section_title, section_id=section_id)
        if err and not str(err).startswith(REPAIRABLE_ERROR_PREFIXES):
            return [callout_box("Section à vérifier", [f"Problème détecté : {err}"], alert=True), Spacer(1, 8)] + flowables
        return flowables

    if DEMO_MODE:
        demo = (
            "## Contenu de démonstration\n\n"
            "Cette section sera remplacée par le texte généré par l'IA. "
            "Le rendu simule une mise en page aérée avec paragraphes courts, puces et tableaux.\n\n"
            "| Critère | Lecture | Impact |\n"
            "|---|---|---|\n"
            "| Marché | À analyser | Détermine l'opportunité |\n"
            "| Concurrence | À comparer | Détermine le positionnement |\n"
            "| Budget | À cadrer | Détermine le point mort |\n\n"
            "## Recommandations\n\n"
            "- Garder les paragraphes courts.\n"
            "- Limiter les tableaux aux comparaisons utiles.\n"
            "- Vérifier les chiffres avant publication."
        )
        return real_section_flowables(demo, section_title=section_title, section_id=section_id)

    return [
        callout_box(
            "Section indisponible dans ce brouillon",
            [
                "La génération de cette section a échoué ou le contenu est absent.",
                "Le rapport est tout de même généré pour permettre la relecture et le diagnostic.",
            ],
            alert=True,
        ),
        Spacer(1, 8),
    ]

def build_cover(story: List[Any]) -> None:
    story.append(Spacer(1, 3.5 * cm if PDF_ISSUES else 4.5 * cm))
    if PDF_ISSUES:
        story.append(draft_banner(PDF_ISSUES))
        story.append(Spacer(1, 0.7 * cm))
    story.append(Paragraph(NOM_CABINET.upper(), styles["CoverKicker"]))
    story.append(Spacer(1, 0.4 * cm))
    story.append(Paragraph("Étude de marché", styles["CoverTitle"]))
    story.append(Paragraph("& plan de lancement stratégique", styles["CoverTitle"]))
    story.append(Spacer(1, 0.7 * cm))
    story.append(HRFlowable(width="42%", color=COLOR_ACCENT, thickness=2, hAlign="CENTER"))
    story.append(Spacer(1, 0.7 * cm))
    if CLIENT_PROJECT_NAME:
        story.append(Paragraph(f"Projet : {md_inline_to_xml(CLIENT_PROJECT_NAME)}", styles["CoverSubtitle"]))
    story.append(Paragraph(f"Concept : {md_inline_to_xml(CLIENT_CONCEPT)}", styles["CoverSubtitle"]))
    story.append(Paragraph(f"Zone / marché : {md_inline_to_xml(CLIENT_VILLE)}", styles["CoverSubtitle"]))
    story.append(Spacer(1, 4.1 * cm if PDF_ISSUES else 4.8 * cm))
    story.append(Paragraph(f"Document confidentiel - {DATE_RAPPORT}", styles["Footer"]))
    story.append(PageBreak())

def build_toc(story: List[Any]) -> None:
    story.append(section_header(None, "Sommaire"))
    story.append(Spacer(1, 14))
    for i, (_, title) in enumerate(SECTIONS_PDF, start=1):
        story.append(Paragraph(f"{i}. {md_inline_to_xml(title)}", styles["TOCLine"]))
    if PDF_ISSUES:
        story.append(Spacer(1, 12))
        story.extend(issues_diagnostic_block(PDF_ISSUES))
    story.append(PageBreak())


def build_story() -> List[Any]:
    issues = PDF_ISSUES
    if issues:
        print("⚠️ PDF généré en mode BROUILLON - À VÉRIFIER :")
        for issue in issues[:30]:
            print(" -", issue)
    else:
        print("✅ Aucun problème bloquant détecté côté PDF.")

    story: List[Any] = []
    build_cover(story)
    build_toc(story)

    for numero, (section_id, title) in enumerate(SECTIONS_PDF, start=1):
        story.append(CondPageBreak(7.5 * cm))
        story.append(section_header(numero, title))
        story.append(Spacer(1, 12))
        story.extend(render_section(section_id, section_title=title))
        story.append(Spacer(1, 14))

    story.append(CondPageBreak(6 * cm))
    story.append(cta_block())
    return story

# ----------------------------------------------------------------------------
# 8. PIED DE PAGE
# ----------------------------------------------------------------------------

def add_page_furniture(canvas, doc) -> None:
    canvas.saveState()
    if doc.page > 1:
        canvas.setFont(BASE_FONT, 8)
        canvas.setFillColor(COLOR_MUTED)
        canvas.drawString(2 * cm, 1.25 * cm, f"{NOM_CABINET} - Étude confidentielle")
        canvas.drawRightString(19 * cm, 1.25 * cm, f"Page {doc.page - 1}")
        canvas.setStrokeColor(COLOR_ACCENT)
        canvas.setLineWidth(0.8)
        canvas.line(2 * cm, 1.55 * cm, 19 * cm, 1.55 * cm)
    canvas.restoreState()


def _load_runtime_content(content_file: Optional[str] = None, output_path: Optional[str] = None) -> None:
    """Recharge le JSON et recalcule les variables globales pour un appel par commande.

    Cette fonction permet à main.py d'appeler generate_pdf(content_file=...,
    output_path=...) sans dépendre des valeurs chargées au moment de l'import.
    """
    global CONTENT_FILE, OUTPUT_PATH, REAL_CONTENT, PROJECT_INPUT, PROJECT_PROFILE
    global CLIENT_VILLE, CLIENT_CONCEPT, CLIENT_PROJECT_NAME, SECTION_TITLES, SECTIONS_PDF
    global PDF_ISSUES, PDF_STATUS

    if content_file:
        CONTENT_FILE = content_file
    if output_path:
        OUTPUT_PATH = output_path

    if os.path.exists(CONTENT_FILE):
        with open(CONTENT_FILE, "r", encoding="utf-8") as f:
            REAL_CONTENT = json.load(f)
        print(f"Contenu trouvé : {CONTENT_FILE}")
    else:
        REAL_CONTENT = {}
        print(f"Aucun contenu JSON trouvé : {CONTENT_FILE} — PDF brouillon de diagnostic.")

    PROJECT_INPUT = REAL_CONTENT.get("_project_input", {})
    PROJECT_PROFILE = REAL_CONTENT.get("_project_profile", {})
    CLIENT_VILLE = PROJECT_INPUT.get("zone", "Zone à préciser")
    CLIENT_CONCEPT = PROJECT_INPUT.get("concept", "Concept à préciser")
    CLIENT_PROJECT_NAME = PROJECT_INPUT.get("project_name", "")
    SECTION_TITLES = REAL_CONTENT.get("_section_titles", {})
    SECTIONS_PDF = [(sid, SECTION_TITLES.get(sid, title)) for sid, title in DEFAULT_SECTIONS_PDF]
    PDF_ISSUES = detect_content_issues()
    PDF_STATUS = "draft" if PDF_ISSUES else "ready"


def generate_pdf(
    content_file: Optional[str] = None,
    output_path: Optional[str] = None,
    quality_file: Optional[str] = None,
) -> Dict[str, Any]:
    """Génère le PDF et retourne le diagnostic qualité.

    Contrat production : cette fonction ne bloque pas pour des issues de contenu.
    Si des problèmes sont détectés, le PDF est marqué BROUILLON - À VÉRIFIER et
    un fichier quality_file est écrit pour permettre à main.py de composer l'email.
    """
    _load_runtime_content(content_file=content_file, output_path=output_path)
    doc = SimpleDocTemplate(
        OUTPUT_PATH,
        pagesize=A4,
        topMargin=1.75 * cm,
        bottomMargin=2.15 * cm,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        title=f"Étude de marché - {CLIENT_CONCEPT}",
        author=NOM_CABINET,
    )
    doc.build(build_story(), onFirstPage=add_page_furniture, onLaterPages=add_page_furniture)
    report = export_pdf_issues(quality_file or "rapport_quality_issues.json")
    print(f"PDF généré : {OUTPUT_PATH}")
    print(f"Statut PDF : {PDF_STATUS} — {len(PDF_ISSUES)} issue(s)")
    return report


if __name__ == "__main__":
    generate_pdf()

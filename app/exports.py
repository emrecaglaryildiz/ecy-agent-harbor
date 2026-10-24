"""Authenticated Markdown/PDF exports with Turkish fonts and safe text rendering."""
import io
import re
import threading
from pathlib import Path
from xml.sax.saxutils import escape

from .localization import translate

import reportlab
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer

FONT_LOCK = threading.Lock()


def markdown_report(task, runs, agents, single=False):
    lang = task.get('language', 'tr')
    def label(tr, en):
        return en if lang == 'en' else tr
    status = {"incomplete": label("Yanıt eksik", "Incomplete response"), "completed": label("Tamamlandı", "Completed"),
              "running": label("Çalışıyor", "Running"), "failed": label("Başarısız", "Failed"),
              "queued": label("Kuyrukta", "Queued"), "awaiting_approval": label("Onay bekliyor", "Awaiting approval"),
              "cancelled": label("İptal edildi", "Cancelled")}
    lines = ["# ECY Agent Harbor - " + label("Görev raporu", "Task report"), "",
             f"**{label('Görev', 'Task')}:** {task['title']}", f"**{label('Kimlik', 'ID')}:** {task['id']}",
             f"**{label('Oluşturulma (UTC)', 'Created (UTC)')}:** {task['created_at']}",
             f"**{label('Durum', 'Status')}:** {status.get(task['status'], task['status'])}",
             f"**{label('Maliyet', 'Cost')}:** ${task['cost']:.6f}", "", "## " + label("Görev girdisi", "Task input"), "", task["prompt"], ""]
    if task.get("synthesis") and not single:
        lines += ["## " + label("Baş uzman sentezi", "Lead expert synthesis"), "", task["synthesis"], ""]
    for run in runs:
        if run["kind"] == "judge" and task.get("synthesis") and not single:
            continue
        name = agents.get(run["agent_id"], {}).get("name", run["agent_id"])
        kind = label('Baş uzman', 'Lead expert') if run['kind'] == 'judge' else label('Ajan çıktısı', 'Agent output')
        lines += [f"## {name} - {kind}", "", f"{label('Durum', 'Status')}: {status.get(run['status'], run['status'])}",
                  f"Token: {run['input_tokens']} {label('giriş', 'input')} / {run['output_tokens']} {label('çıkış', 'output')}",
                  f"{label('Bitiş bilgisi', 'Finish reason')}: {run.get('finish_reason') or label('kaydedilmemiş', 'not recorded')}", "",
                  run.get('output') or translate(run.get('error'), lang) or label('Henüz çıktı yok.', 'No output yet.'), ""]
    if task.get('sources'):
        lines += ['## ' + label('Kaynak envanteri', 'Source inventory'), '',
                  label('Kaynak aralığının varlığı iddianın doğruluğunu kanıtlamaz.', 'The presence of a source range does not prove a claim is correct.'), '']
        for source in task['sources']:
            if source['kind'] == 'file':
                lines.append(f"- {source['name']} · L{source['first']}–L{source['last']} · {translate(source['label'], lang)}")
            else:
                lines.append(f"- {source['url']} · {translate(source['label'], lang)}")
    return "\n".join(lines)


def pdf_bytes(markdown, lang="tr"):
    with FONT_LOCK:
        if "HarborSans" not in pdfmetrics.getRegisteredFontNames():
            folder = Path(reportlab.__file__).parent / "fonts"
            pdfmetrics.registerFont(TTFont("HarborSans", str(folder / "Vera.ttf")))
            pdfmetrics.registerFont(TTFont("HarborBold", str(folder / "VeraBd.ttf")))
            pdfmetrics.registerFontFamily("HarborSans", normal="HarborSans", bold="HarborBold", italic="HarborSans", boldItalic="HarborBold")
    body = ParagraphStyle("HarborBody", fontName="HarborSans", fontSize=9, leading=15,
                          textColor=colors.HexColor("#344b40"), spaceAfter=5, splitLongWords=True,
                          alignment=TA_LEFT)
    heading1 = ParagraphStyle("HarborH1", parent=body, fontName="HarborBold", fontSize=22, leading=29, spaceBefore=10, spaceAfter=16, textColor=colors.HexColor("#255943"))
    heading2 = ParagraphStyle("HarborH2", parent=body, fontName="HarborBold", fontSize=13, leading=19, spaceBefore=16, spaceAfter=9, keepWithNext=True)
    heading3 = ParagraphStyle("HarborH3", parent=heading2, fontSize=10, leading=16, spaceBefore=10)
    code = ParagraphStyle("HarborCode", parent=body, fontSize=8, leading=12, backColor=colors.HexColor("#f0f4ef"), borderPadding=6)
    story, in_code = [], False
    for line in markdown.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
            continue
        if not line.strip():
            story.append(Spacer(1, 4))
            continue
        style = code if in_code else body
        if not in_code:
            if line.startswith("# "):
                style, line = heading1, line[2:]
            elif line.startswith("## "):
                style, line = heading2, line[3:]
            elif line.startswith("### "):
                style, line = heading3, line[4:]
        # Escape all model HTML before allowing our own bold markup.
        # Preserve unsupported Unicode explicitly instead of painting missing-glyph boxes.
        glyphs = pdfmetrics.getFont("HarborSans").face.charToGlyph
        line = "".join(c if ord(c) in glyphs or c == "\t" else f"[U+{ord(c):04X}]" for c in line)
        text = escape(line).replace("\t", "    ")
        if not in_code:
            text = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", text)
        story.append(Paragraph(text, style))
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=(210 * mm, 297 * mm), rightMargin=20 * mm,
                           leftMargin=20 * mm, topMargin=23 * mm, bottomMargin=21 * mm,
                           title="ECY Agent Harbor - " + ("Task report" if lang == "en" else "Görev raporu"), author="ECY Agent Harbor")
    def footer(canvas, document):
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor("#dfe8df"))
        canvas.line(20 * mm, 17 * mm, 190 * mm, 17 * mm)
        canvas.setFont("HarborSans", 8)
        canvas.setFillColor(colors.HexColor("#829283"))
        canvas.drawString(20 * mm, 12 * mm, "ECY AGENT HARBOR  /  " + ("Task report" if lang == "en" else "Görev raporu"))
        canvas.drawRightString(190 * mm, 12 * mm, str(document.page))
        canvas.restoreState()
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()


def export_report(task, runs, fmt, agents, single=False):
    if fmt not in ("md", "pdf"):
        raise ValueError("Çıktı biçimi md veya pdf olmalı")
    markdown = markdown_report(task, runs, agents, single)
    if fmt == "md":
        return markdown.encode("utf-8"), "text/markdown; charset=utf-8"
    return pdf_bytes(markdown, task.get("language", "tr")), "application/pdf"

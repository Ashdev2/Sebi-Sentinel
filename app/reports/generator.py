from datetime import date
from html import escape
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.config import settings
from app.database import SessionLocal
from app.models import GeneratedReport
from app.reports.charts import save_market_activity_chart, save_price_chart, save_risk_chart, save_volume_chart
from app.services.analysis import analyze_stock
from app.services.cases import find_cases
from app.services.summary import build_analysis_payload


def _page_number(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.drawCentredString(A4[0] / 2, 10 * mm, f"SEBI Sentinel Research Report | Page {doc.page}")
    canvas.restoreState()


def _safe(value, fallback="N/A"):
    return fallback if value is None else str(value)


def _esc(value, fallback="N/A"):
    return escape(_safe(value, fallback), quote=False)


def create_report(symbol: str, start_date: date, end_date: date) -> Path:
    stock, df, meta = analyze_stock(symbol, start_date, end_date)
    if df.empty:
        raise ValueError("No data available in the selected period.")
    cases = find_cases(stock.symbol, start_date, end_date)
    payload = build_analysis_payload(stock, df, meta, cases)

    output_dir = Path(settings.reports_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    temp_dir = output_dir / "_temp"
    temp_dir.mkdir(parents=True, exist_ok=True)

    price_png = temp_dir / f"{stock.symbol}_price.png"
    volume_png = temp_dir / f"{stock.symbol}_volume.png"
    risk_png = temp_dir / f"{stock.symbol}_risk.png"
    activity_png = temp_dir / f"{stock.symbol}_activity.png"
    save_price_chart(df, price_png)
    save_volume_chart(df, volume_png)
    save_risk_chart(df, risk_png)
    save_market_activity_chart(df, activity_png)

    output = output_dir / f"{stock.symbol}_{start_date}_{end_date}_Investigation_Report.pdf"
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("TitleCenter", parent=styles["Title"], alignment=TA_CENTER, fontSize=28, leading=34, spaceAfter=12)
    center_style = ParagraphStyle("Center", parent=styles["Normal"], alignment=TA_CENTER, fontSize=11, leading=16)
    small = ParagraphStyle("Small", parent=styles["Normal"], fontSize=9, leading=12)
    body = styles["BodyText"]

    doc = SimpleDocTemplate(str(output), pagesize=A4, rightMargin=18 * mm, leftMargin=18 * mm, topMargin=18 * mm, bottomMargin=18 * mm)
    story = [
        Spacer(1, 42 * mm),
        Paragraph("SEBI SENTINEL", title_style),
        Paragraph("Market Surveillance Investigation Report", ParagraphStyle("Sub", parent=styles["Heading2"], alignment=TA_CENTER)),
        Spacer(1, 12 * mm),
        Paragraph(f"<b>{_esc(stock.company_name)}</b>", center_style),
        Paragraph(f"NSE Symbol: {_esc(stock.symbol)}", center_style),
        Paragraph(f"Investigation Period: {start_date} to {end_date}", center_style),
        Spacer(1, 10 * mm),
        Paragraph("Academic/research surveillance report. Model alerts are not findings of fraud or regulatory violations.", small),
        PageBreak(),
    ]

    story.append(Paragraph("1. Executive Summary", styles["Heading1"]))
    s = payload["summary"]
    summary_table = Table([
        ["Overall surveillance risk", f"{payload['highest_risk_score']:.1f}/100"],
        ["Risk level", payload["risk_level"]],
        ["Peak-risk date", payload["highest_risk_date"]],
        ["Pattern similarity", payload["possible_pattern"]],
        ["Price change in selected period", f"{_safe(s['price_change_percent'])}%" if s["price_change_percent"] is not None else "N/A"],
        ["Maximum 20-day volume ratio", f"{_safe(s['max_volume_ratio'])}x" if s["max_volume_ratio"] is not None else "N/A"],
        ["Official matching case records", str(payload["official_case_count"])],
        ["Model stack", ", ".join(payload["model"]["model_stack"])],
    ], colWidths=[80 * mm, 75 * mm])
    summary_table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("BACKGROUND", (0, 0), (0, -1), colors.lightgrey),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("PADDING", (0, 0), (-1, -1), 7),
    ]))
    story.extend([summary_table, Spacer(1, 7 * mm), Paragraph(_esc(payload["report_preview"]["summary"]), body)])

    story.append(Paragraph("2. Why This Period Was Flagged", styles["Heading1"]))
    for reason in payload["reasons"]:
        story.extend([Paragraph(f"• {_esc(reason)}", body), Spacer(1, 2 * mm)])
    story.extend([Spacer(1, 4 * mm), Paragraph("Interpretation", styles["Heading2"]), Paragraph(_esc(payload["report_preview"]["interpretation"]), body)])

    story.append(Paragraph("3. Possible Sequence of Events", styles["Heading1"]))
    for phase in payload["possible_sequence"]:
        story.extend([Paragraph(f"• {_esc(phase)}", body), Spacer(1, 2 * mm)])

    story.extend([PageBreak(), Paragraph("4. Price / VWAP History", styles["Heading1"]), Image(str(price_png), width=170 * mm, height=70 * mm)])
    story.extend([Spacer(1, 5 * mm), Paragraph("5. Trading Volume", styles["Heading1"]), Image(str(volume_png), width=170 * mm, height=70 * mm)])
    story.extend([PageBreak(), Paragraph("6. Surveillance Scores", styles["Heading1"]), Image(str(risk_png), width=170 * mm, height=70 * mm)])
    story.extend([Spacer(1, 5 * mm), Paragraph("7. Delivery / Trading Activity", styles["Heading1"]), Image(str(activity_png), width=170 * mm, height=70 * mm)])

    story.extend([PageBreak(), Paragraph("8. Highest-Risk Trading Days", styles["Heading1"])])
    rows = [["Date", "Close", "Vol ratio", "Risk", "Pattern"]]
    for event in payload["top_anomalies"][:10]:
        rows.append([
            event["date"], _safe(event["close"]), _safe(event["volume_ratio"]), _safe(event["risk_score"]), event["pattern"]
        ])
    table = Table(rows, colWidths=[27 * mm, 26 * mm, 25 * mm, 22 * mm, 65 * mm], repeatRows=1)
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("PADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(table)

    story.extend([Spacer(1, 8 * mm), Paragraph("9. Official Regulatory Records", styles["Heading1"])])
    if payload["official_cases"]:
        for case in payload["official_cases"]:
            story.extend([
                Paragraph(f"<b>{_esc(case['case_title'])}</b>", styles["Heading3"]),
                Paragraph(f"Regulator: {_esc(case['regulator'])} | Order date: {_esc(case['order_date'])}", body),
                Paragraph(f"Relevant period: {_esc(case['period_start'])} to {_esc(case['period_end'])}", body),
                Paragraph(f"Regulatory finding/description: {_esc(case['violation'])}", body),
                Paragraph(f"Summary: {_esc(case['summary'])}", body),
                Paragraph(f"Source reference: {_esc(case['source_reference'])}", small),
                Paragraph(f"Source URL: {_esc(case['source_url'])}", small),
                Spacer(1, 5 * mm),
            ])
    else:
        story.append(Paragraph("No matching official regulatory case stored locally for this stock and selected period.", body))

    story.extend([
        Spacer(1, 8 * mm),
        Paragraph("10. Limitations and Research Disclaimer", styles["Heading1"]),
        Paragraph(
            "SEBI Sentinel is an academic market-surveillance prototype. Price, volume, delivery, VWAP, trade-count and machine-learning signals can identify unusual trading behaviour, but they cannot establish trader identity, intent, collusion, insider information, or a legal violation. A model-detected alert must not be presented as confirmed fraud. Official misconduct is reported only when supported by an authoritative regulatory record stored in the case database.",
            body,
        ),
    ])

    doc.build(story, onFirstPage=_page_number, onLaterPages=_page_number)

    db = SessionLocal()
    try:
        db.add(GeneratedReport(stock_id=stock.id, start_date=start_date, end_date=end_date, risk_score=payload["highest_risk_score"], file_path=str(output)))
        db.commit()
    finally:
        db.close()
    return output

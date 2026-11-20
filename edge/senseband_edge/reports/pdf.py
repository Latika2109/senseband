"""Render a Report to PDF with reportlab (pure Python: fine on a Pi, no matplotlib)."""
from __future__ import annotations

import io
import time
from typing import Any

from reportlab.graphics.shapes import Drawing, Line, PolyLine, Rect, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .builder import Report

TEAL, GREY, RED, AMBER, GREEN = (colors.HexColor(c) for c in ("#0f766e", "#6b7280", "#b91c1c", "#b45309", "#15803d"))
DISCLAIMER = ("SenseBand is a monitoring aid, not a medical device. This report states measured values and alerts; "
              "it does not diagnose. Please share it with your doctor and call your local emergency number in an emergency.")
W = A4[0] - 30 * mm


def _t(ms: int, fmt: str = "%d %b %Y %H:%M") -> str:
    return time.strftime(fmt, time.localtime(ms / 1000))


def _line_chart(series: list[list[tuple[float, float | None]]], styles: list[dict[str, Any]], w: float, h: float,
                ylabel: str, hlines: list[tuple[float, str]] | None = None, xfmt: str = "%H:%M") -> Drawing:
    d = Drawing(w, h)
    pts = [p for s in series for p in s if p[1] is not None]
    d.add(Rect(36, 18, w - 42, h - 30, strokeColor=colors.lightgrey, fillColor=None))
    d.add(String(2, h - 10, ylabel, fontSize=7, fillColor=GREY))
    if not pts:
        d.add(String(w / 2 - 30, h / 2, "no data", fontSize=9, fillColor=GREY))
        return d
    xs, ys = [p[0] for p in pts], [p[1] for p in pts if p[1] is not None]
    x0, x1 = min(xs), max(xs) or 1
    lo, hi = min(ys + [v for v, _ in (hlines or [])]), max(ys + [v for v, _ in (hlines or [])])
    lo, hi = lo - (hi - lo) * 0.1 - 1e-6, hi + (hi - lo) * 0.1 + 1e-6
    sx = lambda x: 36 + (x - x0) / max(x1 - x0, 1e-9) * (w - 42)  # noqa: E731
    sy = lambda y: 18 + (y - lo) / (hi - lo) * (h - 30)  # noqa: E731
    for frac in (0, 0.5, 1):
        v = lo + frac * (hi - lo)
        d.add(String(2, sy(v) - 2, f"{v:.0f}", fontSize=6, fillColor=GREY))
        d.add(Line(36, sy(v), w - 6, sy(v), strokeColor=colors.HexColor("#e5e7eb"), strokeWidth=0.3))
    for frac in (0, 0.5, 1):
        d.add(String(sx(x0 + frac * (x1 - x0)) - 12, 6, _t(int(x0 + frac * (x1 - x0)), xfmt), fontSize=6, fillColor=GREY))
    for v, label in hlines or []:
        d.add(Line(36, sy(v), w - 6, sy(v), strokeColor=GREEN, strokeWidth=0.8, strokeDashArray=[3, 2]))
        d.add(String(w - 70, sy(v) + 2, label, fontSize=6, fillColor=GREEN))
    for s, st in zip(series, styles, strict=True):
        run: list[float] = []
        for x, y in s:
            if y is None:
                if len(run) >= 4:
                    d.add(PolyLine(run, strokeColor=st["color"], strokeWidth=st.get("w", 1), strokeDashArray=st.get("dash")))
                run = []
            else:
                run += [sx(x), sy(y)]
        if len(run) >= 4:
            d.add(PolyLine(run, strokeColor=st["color"], strokeWidth=st.get("w", 1), strokeDashArray=st.get("dash")))
    return d


def _strip_drawing(samples: list[float], fs: float, w: float, h: float = 60) -> Drawing:
    d = Drawing(w, h)
    d.add(Rect(0, 0, w, h, strokeColor=colors.lightgrey, fillColor=colors.HexColor("#f9fafb")))
    if len(samples) < 2:
        return d
    lo, hi = min(samples), max(samples)
    span = max(hi - lo, 0.5)
    pts: list[float] = []
    for i, v in enumerate(samples):
        pts += [2 + i / (len(samples) - 1) * (w - 4), 4 + (v - lo) / span * (h - 8)]
    d.add(PolyLine(pts, strokeColor=TEAL, strokeWidth=0.6))
    d.add(String(w - 40, 2, f"{len(samples) / fs:.0f} s", fontSize=6, fillColor=GREY))
    return d


def _activity_bar(mins: dict[str, int], w: float) -> Drawing:
    palette = {"sleeping": "#6366f1", "lying": "#818cf8", "sitting": "#94a3b8", "standing": "#64748b", "walking": "#14b8a6",
               "stairs": "#0ea5e9", "exercising": "#f59e0b", "running": "#ef4444", "unknown": "#e5e7eb"}
    d = Drawing(w, 34)
    total = sum(mins.values()) or 1
    x = 0.0
    for k, v in sorted(mins.items(), key=lambda kv: -kv[1]):
        seg = v / total * w
        d.add(Rect(x, 14, seg, 18, fillColor=colors.HexColor(palette.get(k, "#cbd5e1")), strokeColor=colors.white, strokeWidth=0.5))
        if seg > 40:
            d.add(String(x + 2, 4, f"{k} {v}m", fontSize=6, fillColor=GREY))
        x += seg
    return d


def _tbl(data: list[list[Any]], widths: list[float] | None = None, header: bool = True) -> Table:
    t = Table(data, colWidths=widths, repeatRows=1 if header else 0)
    st = [("FONTSIZE", (0, 0), (-1, -1), 8), ("VALIGN", (0, 0), (-1, -1), "TOP"),
          ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d1d5db")),
          ("ROWBACKGROUNDS", (0, 1 if header else 0), (-1, -1), [colors.white, colors.HexColor("#f9fafb")])]
    if header:
        st += [("BACKGROUND", (0, 0), (-1, 0), TEAL), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white)]
    t.setStyle(TableStyle(st))
    return t


def render_pdf(rep: Report) -> bytes:
    buf = io.BytesIO()
    ss = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=ss["Heading1"], textColor=TEAL, fontSize=18, spaceAfter=2)
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], textColor=TEAL, fontSize=12, spaceBefore=8, spaceAfter=3)
    body = ParagraphStyle("b", parent=ss["BodyText"], fontSize=9, leading=12)
    small = ParagraphStyle("s", parent=body, fontSize=7, textColor=GREY, leading=9)
    box = ParagraphStyle("box", parent=body, backColor=colors.HexColor("#fef3c7"), borderPadding=4, fontSize=8)

    def footer(c, doc):  # noqa: ANN001
        c.saveState()
        c.setFont("Helvetica", 7)
        c.setFillColor(GREY)
        c.drawString(15 * mm, 8 * mm, "SenseBand report: monitoring aid, not a medical device")
        c.drawRightString(A4[0] - 15 * mm, 8 * mm, f"page {doc.page}")
        c.restoreState()

    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=14 * mm, bottomMargin=16 * mm,
                            title=f"SenseBand report - {rep.patient.get('name', '')}", author="SenseBand")
    p, v = rep.patient, rep.vitals
    out: list[Any] = [Paragraph("SenseBand health report", h1),
                      Paragraph(f"<b>{p.get('name', '')}</b> &nbsp; period {_t(rep.since_ms)} to {_t(rep.until_ms)} &nbsp; "
                                f"(generated {_t(rep.generated_ms)})", body)]
    if rep.program:
        out.append(Paragraph(f"Program: {rep.program['type']} - {rep.program.get('purpose') or ''}", body))
    out += [Spacer(1, 4), Paragraph(DISCLAIMER, box), Paragraph("Summary", h2)]
    f = lambda x, u="": "n/a" if x is None else f"{x}{u}"  # noqa: E731
    out.append(_tbl([["Measure", "Min", "Average", "Max"],
                     ["Heart rate (bpm)", f(v.get("hr_min")), f(v.get("hr_avg")), f(v.get("hr_max"))],
                     ["Resting heart rate (bpm)", "", f(v.get("hr_resting_avg")), ""],
                     ["SpO2 (%)", f(v.get("spo2_min")), f(v.get("spo2_avg")), ""],
                     ["Skin temperature (C)", f(v.get("temp_min")), f(v.get("temp_avg")), f(v.get("temp_max"))]],
                    [W * 0.4, W * 0.2, W * 0.2, W * 0.2]))
    q = rep.data_quality
    out.append(Paragraph(f"Data coverage {q['coverage_pct']}% ({v['minutes_with_data']} minutes with data). Minutes with "
                         f"signal problems: {q['minutes_with_issues'] or 'none'}. Readings from poor-quality signals are "
                         "left out, never guessed.", small))
    out.append(Paragraph("Heart rate over the period (solid) against what is expected for the activity (dashed)", h2))
    out.append(_line_chart([[(t, h) for t, h, _ in rep.hr_series], [(t, e) for t, _, e in rep.hr_series]],
                           [{"color": TEAL, "w": 1}, {"color": GREY, "w": 0.7, "dash": [3, 2]}], W, 130, "bpm"))
    if rep.spo2_series:
        out += [Paragraph("Blood oxygen (SpO2)", h2),
                _line_chart([[(t, s) for t, s in rep.spo2_series]], [{"color": colors.HexColor("#2563eb"), "w": 1}], W, 90, "%",
                            hlines=[(92, "92% warning level")])]
    out += [Paragraph("Activity", h2), _activity_bar(rep.activity_minutes, W)]
    s = rep.sleep
    if s.get("minutes"):
        out.append(Paragraph(f"Sleep: {s['minutes'] / 60:.1f} h detected, average heart rate {f(s.get('hr_avg'))} bpm, lowest SpO2 "
                             f"{f(s.get('spo2_min'))}%, breathing-dip alerts {s.get('desat_alerts', 0)}. Breathing dips during sleep "
                             "are worth discussing with a doctor.", body))
    out.append(Paragraph("Alerts and events", h2))
    if rep.events:
        rows = [["Time", "Level", "What", "SMS / follow-up"]]
        for e in rep.events[-40:]:
            fu = {"sms_sent": f"SMS sent to {e['sms_sent']} contact(s)", "sms_failed": "SMS could not be sent",
                  "cancelled": "Cancelled by 'I'm OK'"}.get(e["status"] or "", "")
            if e["acknowledged_by"]:
                fu = (fu + "; " if fu else "") + f"seen by {e['acknowledged_by']}"
            rows.append([_t(e["ts"], "%d %b %H:%M"), e["level"], Paragraph(e["message"] or e["kind"], small), Paragraph(fu, small)])
        out.append(_tbl(rows, [W * 0.17, W * 0.12, W * 0.45, W * 0.26]))
        out.append(Paragraph(f"Counts: {rep.event_counts}", small))
    else:
        out.append(Paragraph("No alerts in this period.", body))
    if rep.strips:
        out.append(Paragraph("ECG strips saved with the most serious alerts", h2))
        for st in rep.strips:
            out.append(KeepTogether([Paragraph(f"{_t(st['ts'])} - {st['level']}: {st['message'] or st['kind']}", small),
                                     _strip_drawing(st["samples"], st["fs"], W), Spacer(1, 4)]))
        out.append(Paragraph("A single-lead ECG strip from a wearable cannot show every heart problem.", small))
    if rep.notes:
        out.append(Paragraph("Notes from your care team", h2))
        out += [Paragraph(f"{_t(n['ts'])} - {n.get('author_name') or ''}: {n['text']}", body) for n in rep.notes]
    if rep.rehab and rep.rehab["progress"].get("case"):
        out += [PageBreak(), Paragraph("Recovery progress", h1)]
        pr = rep.rehab["progress"]
        c = pr["case"]
        out.append(Paragraph(f"{c['joint']} ({c['side']}), injury date {c.get('injury_date') or 'n/a'}. Movement tracked: "
                             f"{pr.get('movement')}, normal range {pr.get('normal_rom')} degrees. Doctor's limits: "
                             f"{c.get('max_angle_limits_json') or 'none set'}.", body))
        if pr.get("rom"):
            out.append(_line_chart([[(r["day"], r["rom"]) for r in pr["rom"]]], [{"color": TEAL, "w": 1.4}], W, 120, "ROM degrees",
                                   hlines=[(pr["target"], "target")] if pr.get("target") else None, xfmt="%d %b"))
        out.append(Paragraph(f"Sessions done in the period: {len(rep.rehab['sessions'])}. Adherence: "
                             f"{'n/a' if pr.get('adherence') is None else str(round(pr['adherence'] * 100)) + '%'} of planned days.", body))
        fc = pr.get("forecast") or {}
        if fc.get("ok") and fc.get("est_ts"):
            lo, hi = fc.get("ci_low_ts"), fc.get("ci_high_ts")
            rng = f" (between {_t(lo, '%d %b')} and {_t(hi, '%d %b')})" if lo and hi else ""
            out.append(Paragraph(f"Estimated date to reach the target range: about {_t(fc['est_ts'], '%d %b %Y')}{rng}, based on "
                                 f"{fc['n']} sessions. This is only an estimate and can change.", body))
        else:
            out.append(Paragraph("No recovery-date estimate yet (needs at least 5 days of range-of-movement tests, or progress has "
                                 "levelled off).", body))
        if rep.rehab["sessions"]:
            rows = [["Date", "Exercise", "Reps", "Avg peak", "On target", "Too fast", "Pain before/after", "Ended"]]
            for sx in rep.rehab["sessions"][-30:]:
                rows.append([_t(sx["start_ts"], "%d %b %H:%M"), sx["exercise_id"].replace("_", " "), sx["reps_done"],
                             f(sx["avg_peak_angle"]), f(sx["target_hit_pct"], "%"), sx["too_fast_count"],
                             f"{f(sx['pain_before'])}/{f(sx['pain_after'])}", sx["stopped_reason"]])
            out.append(_tbl(rows))
    out += [Spacer(1, 8), Paragraph(DISCLAIMER, small)]
    doc.build(out, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()

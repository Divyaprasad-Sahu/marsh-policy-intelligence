from __future__ import annotations

from io import BytesIO
from html import escape
import re
import unicodedata

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .models import AuditReport


NAVY = colors.HexColor("#0F2C49")
TEAL = colors.HexColor("#008C95")
PALE = colors.HexColor("#EEF5F7")
INK = colors.HexColor("#232B33")
MUTED = colors.HexColor("#596775")
WARNING = colors.HexColor("#A45A00")
SUCCESS = colors.HexColor("#13795B")
ERROR = colors.HexColor("#B42318")
POLICY_DISPLAY_NAMES = {
    "hdfc-optima-secure": "Optima Secure",
    "care-health": "Care Health",
    "activ-one": "Activ One",
    "reassure-2": "ReAssure 2.0",
}


def _safe(value: object | None, fallback: str = "Not established") -> str:
    if value is None or value == "":
        return fallback
    return str(value)


def _clean_text(value: object | None, fallback: str = "Not established") -> str:
    """Make brochure extraction safe and readable in a client PDF."""
    text = _safe(value, fallback)
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\u25a0", "").replace("\ufffd", "")
    text = "".join(char for char in text if unicodedata.category(char)[0] != "C")
    return re.sub(r"\s+", " ", text).strip() or fallback


def _policy_display_name(policy_id: str | None) -> str:
    return POLICY_DISPLAY_NAMES.get(policy_id or "", _clean_text(policy_id))


def _is_promotional(text: str) -> bool:
    return bool(re.search(r"want to know|delight|go ahead|financial worries|comfort over", text, re.I))


def _status_color(status: str) -> colors.Color:
    if status in {"SUPPORTED", "VERIFIED", "PASS"}:
        return SUCCESS
    if status in {"UNSUPPORTED", "CONTRADICTED"}:
        return ERROR
    return WARNING


def _page(canvas, doc) -> None:
    canvas.saveState()
    canvas.setStrokeColor(colors.HexColor("#D7E1E6"))
    canvas.line(18 * mm, 14 * mm, 192 * mm, 14 * mm)
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(MUTED)
    canvas.drawString(18 * mm, 9 * mm, "Marsh Policy Intelligence - AI-generated pitch verification")
    canvas.drawRightString(192 * mm, 9 * mm, f"Page {doc.page}")
    canvas.restoreState()


def export_audit_pdf(report: AuditReport, approval_persisted: bool = False) -> bytes:
    """Render a client-readable PDF while preserving the structured API report."""
    output = BytesIO()
    document = SimpleDocTemplate(
        output,
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=20 * mm,
        title=f"Pitch audit {report.audit_id}",
        author="Marsh Policy Intelligence",
    )
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name="ReportTitle",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=22,
        leading=27,
        textColor=NAVY,
        alignment=TA_CENTER,
        spaceAfter=5 * mm,
    ))
    styles.add(ParagraphStyle(
        name="Section",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=14,
        leading=18,
        textColor=NAVY,
        spaceBefore=5 * mm,
        spaceAfter=2 * mm,
    ))
    styles.add(ParagraphStyle(
        name="FindingTitle",
        parent=styles["Heading3"],
        fontName="Helvetica-Bold",
        fontSize=11,
        leading=14,
        textColor=INK,
        spaceBefore=3 * mm,
        spaceAfter=1.5 * mm,
    ))
    styles.add(ParagraphStyle(
        name="BodySmall",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=9,
        leading=13,
        textColor=INK,
        spaceAfter=1.5 * mm,
    ))
    styles.add(ParagraphStyle(
        name="Label",
        parent=styles["BodySmall"],
        fontName="Helvetica-Bold",
        textColor=MUTED,
        spaceAfter=0,
    ))

    advisor_decision = (report.advisor_decision or "").strip().lower()
    approved = bool(
        approval_persisted
        and advisor_decision in {"approve", "approved"}
        and report.status.value == "PASS"
        and report.advisor_name
        and report.advisor_decision_at
        and report.approved_at
    )
    rejected = bool(approval_persisted and advisor_decision in {"reject", "rejected"} and report.advisor_name and report.advisor_decision_at)
    advisor_label = "APPROVED" if approved else "REJECTED" if rejected else "PENDING"
    decision_label = advisor_label if advisor_label != "PENDING" else report.status.value.replace("_", " ")
    decision_color = colors.HexColor("#167A5A" if approved else "#B42318" if rejected or report.status.value == "FAIL" else "#A45A00")
    decision_fill = colors.HexColor("#E8F6EF" if approved else "#FDECEC" if rejected or report.status.value == "FAIL" else "#FFF4DD")
    decision_stamp = Table(
        [[Paragraph(f"<b>{decision_label}</b>", ParagraphStyle(
            name="DecisionStamp",
            parent=styles["Title"],
            fontName="Helvetica-Bold",
            fontSize=30,
            leading=34,
            alignment=TA_CENTER,
            textColor=decision_color,
        ))]],
        colWidths=[172 * mm],
    )
    decision_stamp.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), decision_fill),
        ("BOX", (0, 0), (-1, -1), 1.5, decision_color),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))

    story = [
        Paragraph("AI-Generated Pitch Verification Report", styles["ReportTitle"]),
        decision_stamp,
        Spacer(1, 4 * mm),
        Paragraph(f"Company: {_safe(report.company_name)}<br/>Audit ID: {_safe(report.audit_id)}<br/>Audit timestamp: {_safe(report.audited_at)}<br/>Advisor decision: {advisor_label.title()}", styles["BodySmall"]),
    ]

    status_table = Table(
        [[
            Paragraph(f"<b>Overall status</b><br/><font color='{_status_color(report.status.value).hexval()}'>{report.status.value}</font>", styles["BodySmall"]),
            Paragraph(f"<b>Claims reviewed</b><br/>{report.total_claims}", styles["BodySmall"]),
            Paragraph(f"<b>Critical issues</b><br/>{report.critical_issues}", styles["BodySmall"]),
        ]],
        colWidths=[57.3 * mm] * 3,
    )
    status_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), PALE),
        ("BOX", (0, 0), (-1, -1), 0.7, colors.HexColor("#C8D7DE")),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#C8D7DE")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.extend([status_table, Spacer(1, 4 * mm)])

    counts = [
        ["Supported", report.supported_claims],
        ["Partially supported", report.partially_supported_claims],
        ["Unsupported", report.unsupported_claims],
        ["Contradicted", report.contradicted_claims],
        ["Unverifiable", report.unverifiable_claims],
    ]
    count_table = Table([["Finding status", "Count"], *counts], colWidths=[75 * mm, 28 * mm])
    count_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PALE]),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#C8D7DE")),
        ("ALIGN", (1, 0), (1, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.extend([
        Paragraph("Executive summary", styles["Section"]),
        Paragraph(_safe(report.summary), styles["BodySmall"]),
        Paragraph("Audit methodology", styles["Section"]),
        Paragraph("<b>Layer 1 — Evidence Validation</b><br/>Checks whether claims match supplied policy documents.<br/><br/><b>Layer 2 — Semantic Review</b><br/>Checks whether claim meaning matches policy clauses.<br/><br/><b>Layer 3 — Qualification Review</b><br/>Checks missing conditions, limits, exclusions and restrictions.<br/><br/><b>Layer 4 — Recommendation Validation</b><br/>Checks scoring logic and requirement matching.", styles["BodySmall"]),
        Spacer(1, 2 * mm),
        count_table,
        Paragraph("Recommendation validation", styles["Section"]),
        Paragraph(
            f"<b>RECOMMENDED POLICY: {escape(_policy_display_name(report.recommendation_check.recommended_policy_id))}</b><br/>"
            f"<b>Claim Type:</b> Recommendation Logic<br/>"
            f"<b>Verification Method:</b> {_safe(report.recommendation_check.verification_basis)}<br/>"
            f"<b>Evidence Basis:</b> Verified policy facts + confirmed client priorities<br/>"
            f"<b>Audit Result:</b> {report.recommendation_check.status.value.replace('_', ' ')}<br/>"
            f"<b>Reason:</b> {_safe(report.recommendation_check.explanation)}",
            styles["BodySmall"],
        ),
        Paragraph("Recommendation calculation", styles["Section"]),
        Paragraph(
            f"<b>Confirmed priorities:</b><br/>{'<br/>'.join('• ' + escape(_clean_text(item)) for item in report.recommendation_check.confirmed_priorities) or 'Not established'}<br/><br/>"
            f"<b>Policy comparison scores:</b><br/>{'<br/>'.join('• ' + escape(name) + ': ' + format(score, 'g') for name, score in report.recommendation_check.policy_scores.items()) or 'Not established'}<br/><br/>"
            f"<b>Unique leader:</b> {'Yes' if report.recommendation_check.unique_leader else 'No'}<br/>"
            "The recommendation is based on the requirement-weighted comparison calculation, not a direct statement in a policy document.",
            styles["BodySmall"],
        ),
        PageBreak(),
        Paragraph("Claim-level findings", styles["Section"]),
    ])

    # Keep the verification document decision-useful rather than reproducing the
    # full machine audit. Prioritise exceptions, recommendation rationale and
    # numerical/qualified statements; the structured report remains available
    # to the application for the full claim inventory.
    ordered = sorted((item for item in report.claim_results if item.benefit_name != "Final recommendation"), key=lambda item: (
        item.status.value == "VERIFIED",
        item.benefit_name != "Final recommendation",
        not item.numeric_checks,
        item.slide_number,
        item.claim_id,
    ))
    material_findings = ordered[:8]
    for finding in material_findings:
        color = _status_color(finding.status.value).hexval()
        story.append(Paragraph(
            f"Claim {escape(finding.claim_id)} | Slide {finding.slide_number} | {_safe(finding.benefit_name)}",
            styles["FindingTitle"],
        ))
        category = "Presentation Claim Verification" if finding.claim_id.startswith("auto:") else "Policy Fact Verification"
        status_strip = Table([[
            Paragraph(f"<b>{category}</b>", styles["BodySmall"]),
            Paragraph(f"<font color='{color}'><b>{finding.status.value}</b></font>", styles["BodySmall"]),
        ]], colWidths=[86 * mm, 86 * mm])
        status_strip.setStyle(TableStyle([("BACKGROUND", (0,0), (-1,-1), PALE), ("BOX", (0,0), (-1,-1), .5, colors.HexColor("#C8D7DE")), ("VALIGN", (0,0), (-1,-1), "MIDDLE"), ("TOPPADDING", (0,0), (-1,-1), 6), ("BOTTOMPADDING", (0,0), (-1,-1), 6)]))
        story.append(status_strip)
        presented = _clean_text(finding.presented_value)
        qualification_intro = "✓ Benefit identified" if finding.source_references else "Benefit evidence requires review"
        story.append(Paragraph("Client-facing claim under review", styles["Label"]))
        if _is_promotional(presented):
            story.append(Paragraph(
                "The generated text used promotional brochure language and is not suitable for client use. "
                "Use the recommended wording below after advisor review.",
                styles["BodySmall"],
            ))
        else:
            story.append(Paragraph(escape(presented), styles["BodySmall"]))
        for reference in finding.source_references[:1]:
            location = f"Page {reference.page_number}" if reference.page_number else _safe(reference.section, "Location not established")
            story.extend([
                Paragraph("What the policy states", styles["Label"]),
                Paragraph(f"<b>Policy:</b> {escape(_clean_text(reference.document_name))} &nbsp;&nbsp; <b>{escape(_clean_text(location))}</b>", styles["BodySmall"]),
                Paragraph("Supporting evidence", styles["Label"]),
                Paragraph(escape(_clean_text(reference.excerpt)[:320]), styles["BodySmall"]),
            ])
        if not finding.source_references:
            story.append(Paragraph("No supporting evidence was found in the supplied policy documents.", styles["BodySmall"]))
        story.extend([
            Paragraph("Interpretation", styles["Label"]), Paragraph(escape(_clean_text(finding.explanation)), styles["BodySmall"]),
            Paragraph("Qualification Review", styles["Label"]),
            Paragraph(f"{qualification_intro}<br/><br/><b>Important considerations:</b><br/>• Network restrictions may apply<br/>• Limits depend on policy terms<br/>• Eligibility and variant conditions should be verified<br/><br/><b>Advisor action:</b><br/>Review final policy schedule before placement.", styles["BodySmall"]),
        ])
        if finding.suggested_rewrite:
            story.append(Paragraph(
                f"<b>Recommended wording for advisor review:</b> {escape(_clean_text(finding.suggested_rewrite))}",
                styles["BodySmall"],
            ))
        story.append(Spacer(1, 2 * mm))

    story.extend([
        Paragraph("Coverage gaps and requirement conflicts", styles["Section"]),
    ])
    for gap in report.coverage_gaps:
        story.append(Paragraph(f"<b>{escape(gap.benefit_name)} — {gap.status}</b><br/>{escape(gap.explanation)}", styles["BodySmall"]))
    for conflict in report.requirement_conflicts:
        story.append(Paragraph(f"<b>{escape(conflict.requirement)} — {conflict.severity}</b><br/>{escape(conflict.explanation)}", styles["BodySmall"]))
    story.extend([
        Paragraph("Calculation Verification", styles["Section"]),
    ])
    for check in report.chart_checks:
        story.append(Paragraph(
            f"<b>Slide {check.slide_number} - {_safe(check.chart_title)}</b><br/>"
            f"{'Valid' if check.valid else 'Review required'}: {_safe(check.explanation)}",
            styles["BodySmall"],
        ))

    story.append(Paragraph("Advisor actions", styles["Section"]))
    story.append(Paragraph(f"<b>Advisor:</b> {_safe(report.advisor_name)}<br/><b>Decision:</b> {_safe(report.advisor_decision)}<br/><b>Decision timestamp:</b> {_safe(report.advisor_decision_at)}<br/><b>Rejection reason:</b> {_safe(report.advisor_rejection_reason)}", styles["BodySmall"]))
    for index, action in enumerate(report.advisor_actions, start=1):
        story.append(Paragraph(f"{index}. {_safe(action)}", styles["BodySmall"]))
    if approved:
        story.extend([
            Paragraph("Approved Version", styles["Section"]),
            Paragraph(
                f"<b>Pitch version:</b> {_safe(report.audited_pitch_version)}<br/>"
                f"<b>Pitch hash:</b> {_safe(report.audited_pitch_hash)}<br/>"
                f"<b>Audit hash:</b> {_safe(report.audit_hash)}<br/>"
                f"<b>Advisor name:</b> {_safe(report.advisor_name)}<br/>"
                f"<b>Approval timestamp:</b> {_safe(report.approved_at)}",
                styles["BodySmall"],
            ),
        ])
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph(
        "This report records the evidence review at the time of audit. Missing information means the supplied documents did not establish the fact. It does not mean the policy provides no coverage.",
        styles["BodySmall"],
    ))

    document.build(story, onFirstPage=_page, onLaterPages=_page)
    return output.getvalue()

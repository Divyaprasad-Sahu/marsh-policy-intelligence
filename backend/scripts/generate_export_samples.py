"""Generate representative local exports for visual QA of the export engines."""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.audit import auditPitchContent
from app.documents import cached_preloaded_policies
from app.generation import generateMarketingPitch
from app.pdf_export import export_audit_pdf
from app.pptx_export import export_pitch_pptx


async def main() -> None:
    policies = list(cached_preloaded_policies())
    pitch = await generateMarketingPitch(
        company_name="Reliance Jio",
        policy_docs=policies,
        selected_policy_ids=[policy.document_id for policy in policies],
        client_requirements=[
            "Hospitalisation coverage",
            "Emergency ambulance support",
            "Room rent and ICU clarity",
            "Preventive healthcare",
            "Modern treatments",
        ],
    )
    report = await auditPitchContent(
        pitch.pitch_slides,
        policies,
        pitch.recommended_policy_id,
    )
    output = BACKEND_ROOT / "output" / "export_qa"
    output.mkdir(parents=True, exist_ok=True)
    (output / "policy-recommendation.pptx").write_bytes(export_pitch_pptx(pitch))
    (output / "pitch-verification-report.pdf").write_bytes(export_audit_pdf(report))
    print(f"PPTX={output / 'policy-recommendation.pptx'}")
    print(f"PDF={output / 'pitch-verification-report.pdf'}")
    print(f"AUDIT_STATUS={report.status.value}; CLAIMS={report.total_claims}")


if __name__ == "__main__":
    asyncio.run(main())

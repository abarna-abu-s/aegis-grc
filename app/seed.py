"""Seed commands.

    python -m app.seed                # load framework templates (idempotent)
    python -m app.seed --demo         # also create a clearly labelled DEMO organization with sample data

Demo data is fictional and lives in an organization flagged is_demo=True."""
import os
import secrets
import sys
import uuid
from datetime import date, timedelta

from sqlalchemy import select

from . import rag, scoring
from .config import settings
from .db import Base, SessionLocal, engine
from .frameworks_data import FRAMEWORKS, THEMES, suggested_evidence
from .models import (AlertRule, Asset, Assessment, AssessmentItem, AuditEngagement, AutomationRule, Control, ControlMapping,
                     ControlTest, CorrectiveAction, Evidence, EvidenceControlLink, EvidenceRequest, EvidenceVersion,
                     ExceptionRecord, Finding, Framework, FrameworkControl, Membership, Organization, Policy, PolicyControlLink,
                     PolicyVersion, PrivacyRecord, Risk, RiskControlLink, ScoreSnapshot, SourceDocument, Task, TreatmentPlan,
                     User, Vendor, now)
from .security import audit, hash_password


def seed_frameworks(db) -> int:
    if db.scalar(select(Framework.id).where(Framework.org_id.is_(None)).limit(1)):
        return 0
    index = {}
    for f in FRAMEWORKS:
        fw = Framework(org_id=None, key=f["key"], name=f["name"], version=f["version"], publisher=f["publisher"], jurisdiction=f["jurisdiction"],
                       kind=f["kind"], source_status="template_unverified", description=f["description"])
        db.add(fw)
        db.flush()
        for ref, title, domain in f["controls"]:
            fc = FrameworkControl(framework_id=fw.id, ref_id=ref, title=title, domain=domain, evidence_requirements=[], requirement_text="")
            db.add(fc)
            db.flush()
            index[(f["key"], ref)] = fc.id
    n = 0
    for theme, members in THEMES.items():
        ids = [index[m] for m in members if m in index]
        for a, b in zip(ids, ids[1:]):
            if a != b:
                db.add(ControlMapping(org_id=None, from_id=a, to_id=b, relation="related", source="seed", status="template", note=f"Theme: {theme}"))
                n += 1
    db.commit()
    return n


def _fw(db, key):
    return db.scalar(select(Framework).where(Framework.key == key, Framework.org_id.is_(None)))


def seed_demo(db) -> dict:
    if db.scalar(select(Organization.id).where(Organization.is_demo.is_(True))):
        return {"skipped": "demo organization already exists"}
    pw = os.getenv("DEMO_PASSWORD") or ("Demo-" + secrets.token_urlsafe(9) + "1a")
    org = Organization(name="Northwind Demo Co. (SAMPLE DATA)", industry="Software", is_demo=True)
    db.add(org)
    db.flush()
    users = {}
    for role, email, name in (("owner", "owner@demo.aegis.example", "Olivia Owner"), ("compliance_manager", "manager@demo.aegis.example", "Marcus Manager"),
                              ("control_owner", "engineer@demo.aegis.example", "Elena Engineer"), ("auditor", "auditor@demo.aegis.example", "Arjun Auditor"),
                              ("viewer", "viewer@demo.aegis.example", "Vera Viewer")):
        u = User(email=email, full_name=name, password_hash=hash_password(pw))
        db.add(u)
        db.flush()
        db.add(Membership(org_id=org.id, user_id=u.id, role=role))
        users[role] = u
    o, m, e, a = users["owner"], users["compliance_manager"], users["control_owner"], users["auditor"]
    today = date.today()

    # internal controls (cross-framework, reused by multiple requirements)
    spec = [("AC-01", "User access reviews", "Quarterly review of user and privileged access.", e, ["iso27001:5.15", "soc2:CC6", "nistcsf2:PR.AA", "cis8:CIS 6"]),
            ("AC-02", "Multi-factor authentication", "MFA enforced for workforce and administrators.", e, ["iso27001:8.5", "iso27001:8.2"]),
            ("LG-01", "Centralised logging and monitoring", "Security logs collected and reviewed.", e, ["iso27001:8.15", "cis8:CIS 8", "nistcsf2:DE.CM"]),
            ("BK-01", "Backups and restore testing", "Backups run daily and restores are tested.", e, ["iso27001:8.13", "cis8:CIS 11", "soc2:A1"]),
            ("VM-01", "Vulnerability management", "Scanning and timely patching.", e, ["iso27001:8.8", "cis8:CIS 7"]),
            ("IR-01", "Incident response plan", "Documented and exercised incident response.", m, ["iso27001:5.24", "soc2:CC7", "gdpr:Art.33", "nistcsf2:RS.MA"]),
            ("TP-01", "Third-party risk management", "Vendors assessed before onboarding and periodically.", m, ["iso27001:5.19", "cis8:CIS 15", "gdpr:Art.28", "nistcsf2:GV.SC"]),
            ("PL-01", "Information security policy", "Policy approved and reviewed annually.", m, ["iso27001:5.1", "nistcsf2:GV.PO", "soc2:CC1"]),
            ("RK-01", "Risk assessment process", "Risk register maintained and treated.", m, ["iso27001:6", "soc2:CC3", "nistcsf2:GV.RM"]),
            ("AM-01", "Asset inventory", "Assets inventoried with owners.", e, ["iso27001:5.9", "cis8:CIS 1", "nistcsf2:ID.AM"]),
            ("TR-01", "Security awareness training", "Annual training for all staff.", m, ["iso27001:6.3", "cis8:CIS 14", "nistcsf2:PR.AT"]),
            ("PV-01", "Records of processing", "Processing activities recorded and reviewed.", m, ["gdpr:Art.30", "gdpr:Art.24"]),
            ("PV-02", "Data subject request handling", "Requests handled within statutory time.", m, ["gdpr:Art.15", "gdpr:Art.17"])]
    ctrls = {}
    fc_ref = {}
    for key, title, desc, owner, refs in spec:
        c = Control(org_id=org.id, control_key=key, title=title, description=desc, owner_id=owner.id, implementation_status="implemented",
                    evidence_requirements=suggested_evidence(title), next_review_date=today + timedelta(days=60))
        db.add(c)
        db.flush()
        ctrls[key] = c
        for r in refs:
            fc_ref[r] = c
    for key in ("iso27001", "soc2", "gdpr", "nistcsf2"):
        fw = _fw(db, key)
        asm = Assessment(org_id=org.id, name=f"{fw.name} readiness (demo)", framework_id=fw.id, due_date=today + timedelta(days=120))
        db.add(asm)
        db.flush()
        for fc in db.scalars(select(FrameworkControl).where(FrameworkControl.framework_id == fw.id)):
            c = fc_ref.get(f"{key}:{fc.ref_id}")
            db.add(AssessmentItem(org_id=org.id, assessment_id=asm.id, framework_control_id=fc.id, control_id=c.id if c else None))
    db.flush()

    # evidence in a mix of states
    def ev(title, ctrl_keys, valid_days, status="approved", sens="standard", body=None, source="manual upload"):
        x = Evidence(org_id=org.id, title=title, description=f"[DEMO] {title}", source=source, owner_id=e.id, collected_on=today - timedelta(days=40),
                     valid_until=today + timedelta(days=valid_days) if valid_days is not None else None, review_status=status,
                     reviewed_by=m.id if status != "pending" else None, reviewed_at=now() if status != "pending" else None, sensitivity=sens)
        db.add(x)
        db.flush()
        if body:
            raw = body.encode()
            name = f"{uuid.uuid4().hex}.txt"
            (settings.upload_dir / str(org.id)).mkdir(parents=True, exist_ok=True)
            (settings.upload_dir / str(org.id) / name).write_bytes(raw)
            import hashlib
            db.add(EvidenceVersion(org_id=org.id, evidence_id=x.id, version=1, filename=title.replace(" ", "_") + ".txt", stored_name=f"{org.id}/{name}",
                                   mime="text/plain", size=len(raw), sha256=hashlib.sha256(raw).hexdigest(), extracted_text=body, uploaded_by=e.id))
            if status != "rejected":
                rag.index_source(db, org.id, "evidence", x.id, title, f"{title}\n\n{body}", 1, sens)
        for k in ctrl_keys:
            db.add(EvidenceControlLink(org_id=org.id, evidence_id=x.id, control_id=ctrls[k].id, linked_by=e.id))
        return x
    ev("Q3 user access review", ["AC-01"], 120, body="Quarterly access review completed. Privileged accounts were reviewed by the system owner; two stale accounts were removed.")
    ev("MFA enforcement configuration", ["AC-02"], 200, body="Identity provider configuration shows multi-factor authentication required for all users and administrators.")
    ev("Log retention configuration", ["LG-01"], 20, body="Logs are retained for 365 days and alerts are reviewed daily by the security team.")
    ev("Backup restore test", ["BK-01"], -15, body="Restore test of the production database completed successfully within the recovery time objective.")
    ev("Vulnerability scan report", ["VM-01"], 90, status="pending", body="Monthly authenticated vulnerability scan: 0 critical, 3 high findings with remediation tickets.")
    ev("Incident response tabletop record", ["IR-01"], 300, body="Tabletop exercise held with engineering and legal. Actions captured for communication templates.")
    ev("Vendor assessment - Acme Cloud", ["TP-01"], 150)
    ev("Asset inventory export", ["AM-01"], 45)
    ev("Pen test summary (restricted)", ["VM-01"], 200, sens="restricted", body="External penetration test summary: no critical issues; two medium issues in a legacy admin portal.")
    ev("Duplicate: Q3 access review copy", ["AC-01"], 120, body="Quarterly access review completed. Privileged accounts were reviewed by the system owner; two stale accounts were removed.")

    # a few human-reviewed results, only where evidence is current
    d = scoring.load(db, org.id)
    for it in d.items:
        if it.control_id and scoring.control_state(d, it.control_id)["current_evidence"]:
            db_it = db.get(AssessmentItem, it.id)
            db_it.result, db_it.reviewed_by, db_it.reviewed_at = "compliant", m.id, now()
    for it in db.scalars(select(AssessmentItem).where(AssessmentItem.org_id == org.id, AssessmentItem.control_id == ctrls["BK-01"].id)):
        it.result, it.reviewed_by, it.reviewed_at = "partial", m.id, now()
    db.add(ControlTest(org_id=org.id, control_id=ctrls["AC-01"].id, result="pass", notes="Sampled 25 accounts", tested_by=a.id, tested_at=today - timedelta(days=10)))
    db.add(ControlTest(org_id=org.id, control_id=ctrls["BK-01"].id, result="fail", notes="Restore exceeded RTO in latest test", tested_by=a.id, tested_at=today - timedelta(days=5)))

    # policies
    pol = Policy(org_id=org.id, title="Information Security Policy", category="policy", status="approved", version=1, owner_id=m.id, approved_by=o.id, approved_at=now(),
                 next_review_date=today + timedelta(days=200),
                 body="All access to systems must follow least privilege. Privileged access is reviewed quarterly by the system owner.\n\nMulti-factor authentication is required for all administrative access.\n\nSecurity incidents must be reported immediately and handled under the incident response plan.\n\nSuppliers that process company data are assessed before onboarding.")
    pol2 = Policy(org_id=org.id, title="Access Control Standard (draft)", category="standard", status="draft", version=1, owner_id=e.id,
                  body="User accounts are created through an approved request. Access rights are removed within one business day of leaving.\n\nShared accounts are prohibited.")
    db.add_all([pol, pol2])
    db.flush()
    for p in (pol, pol2):
        db.add(PolicyVersion(org_id=org.id, policy_id=p.id, version=1, body=p.body, changed_by=m.id))
    rag.index_source(db, org.id, "policy", pol.id, pol.title, pol.body, 1)
    for k in ("PL-01", "AC-01", "AC-02", "IR-01", "TP-01"):
        db.add(PolicyControlLink(org_id=org.id, policy_id=pol.id, control_id=ctrls[k].id))
    doc_text = "Customer security questionnaire guidance: data is encrypted in transit using TLS 1.2 or higher and at rest using AES-256. Backups are encrypted."
    sd = SourceDocument(org_id=org.id, title="Security whitepaper (demo)", filename="whitepaper.txt", stored_name="", sha256="demo", text=doc_text, uploaded_by=m.id)
    db.add(sd)
    db.flush()
    rag.index_source(db, org.id, "document", sd.id, sd.title, doc_text)

    # assets, vendors, privacy
    assets = [Asset(org_id=org.id, name=n, asset_type=t, criticality=c, owner_id=e.id, data_classification=dc) for n, t, c, dc in
              (("Customer web application", "application", 5, "confidential"), ("Production database", "data_store", 5, "restricted"),
               ("Corporate laptops", "device", 3, "internal"), ("Support ticketing system", "service", 3, "confidential"))]
    db.add_all(assets)
    vendors = [Vendor(org_id=org.id, name="Acme Cloud (fictional)", service="IaaS hosting", criticality=5, data_access="personal", risk_tier="high", owner_id=m.id,
                      last_assessed=today - timedelta(days=200), next_assessment=today + timedelta(days=10), contract_end=today + timedelta(days=300)),
               Vendor(org_id=org.id, name="MailRelay Ltd (fictional)", service="Transactional email", criticality=3, data_access="personal", risk_tier="medium", owner_id=m.id,
                      next_assessment=today + timedelta(days=90))]
    db.add_all(vendors)
    db.flush()
    db.add(PrivacyRecord(org_id=org.id, name="Customer account data", purpose="Provide the service", data_categories="Name, email, usage data", data_subjects="Customers",
                         lawful_basis="Contract", retention_period="Account life + 12 months", cross_border=True, asset_id=assets[1].id, vendor_id=vendors[0].id,
                         owner_id=m.id, dpia_required=True, dpia_status="in_progress"))

    # risks
    r1 = Risk(org_id=org.id, title="Ransomware affecting production systems", category="security", asset_id=assets[1].id, owner_id=o.id, likelihood=4, impact=5,
              residual_likelihood=2, residual_impact=5, treatment="mitigate", status="treating")
    r2 = Risk(org_id=org.id, title="Cloud provider outage", category="availability", vendor_id=vendors[0].id, owner_id=m.id, likelihood=3, impact=4, treatment="mitigate")
    r3 = Risk(org_id=org.id, title="Unmanaged laptops lose data", category="security", asset_id=assets[2].id, owner_id=e.id, likelihood=2, impact=3, treatment="accept", status="open")
    db.add_all([r1, r2, r3])
    db.flush()
    db.add(RiskControlLink(org_id=org.id, risk_id=r1.id, control_id=ctrls["BK-01"].id))
    db.add(TreatmentPlan(org_id=org.id, risk_id=r1.id, action="Implement immutable backups", owner_id=e.id, due_date=today + timedelta(days=30), status="in_progress"))

    # audit, findings, tasks, exceptions, requests
    iso = _fw(db, "iso27001")
    au = AuditEngagement(org_id=org.id, name="Internal ISO 27001 audit (demo)", framework_id=iso.id, audit_type="internal", scope="ISMS scope", lead_id=a.id,
                         start_date=today + timedelta(days=21), end_date=today + timedelta(days=35), status="planned")
    db.add(au)
    db.flush()
    f1 = Finding(org_id=org.id, title="Restore test exceeded recovery time objective", description="Latest restore took longer than the documented RTO.", audit_id=au.id,
                 control_id=ctrls["BK-01"].id, severity="high", status="in_remediation", owner_id=e.id, due_date=today + timedelta(days=14))
    f2 = Finding(org_id=org.id, title="Vendor reassessment overdue", description="High-risk supplier was last assessed over 6 months ago.", audit_id=au.id,
                 control_id=ctrls["TP-01"].id, severity="medium", status="open", due_date=today - timedelta(days=3))
    db.add_all([f1, f2])
    db.flush()
    db.add(CorrectiveAction(org_id=org.id, finding_id=f1.id, title="Tune restore procedure and retest", owner_id=e.id, due_date=today + timedelta(days=10), status="in_progress"))
    db.add_all([Task(org_id=org.id, title="Collect Q4 access review evidence", assignee_id=e.id, due_date=today + timedelta(days=12), priority="medium", related_type="control", related_id=ctrls["AC-01"].id),
                Task(org_id=org.id, title="Update incident contact list", assignee_id=m.id, due_date=today - timedelta(days=2), priority="high")])
    db.add(ExceptionRecord(org_id=org.id, title="Legacy admin portal lacks MFA", control_id=ctrls["AC-02"].id, justification="Vendor product cannot support MFA until upgrade.",
                           compensating_controls="IP allow-listing and monthly review", requested_by=e.id, status="requested", expires_on=today + timedelta(days=90)))
    db.add(EvidenceRequest(org_id=org.id, control_id=ctrls["TR-01"].id, title="Upload annual training completion report", instructions="Export from LMS.", assignee_id=e.id,
                           due_date=today + timedelta(days=7), requested_by=m.id))
    db.add_all([AlertRule(org_id=org.id, name="Evidence expiring within 30 days", metric="evidence_expiring_days", threshold=30),
                AlertRule(org_id=org.id, name="Readiness below 60", metric="readiness_below", threshold=60),
                AutomationRule(org_id=org.id, name="Task for expired evidence", trigger="evidence_expired", action="create_task"),
                AutomationRule(org_id=org.id, name="Task for high risks", trigger="risk_high", action="create_task", params={"min_score": 12})])
    db.flush()
    audit(db, "demo.seeded", "organization", org.id, "Fictional sample data", org_id=org.id, actor=o)
    # clearly labelled backdated demo snapshots so trend charts have shape; real snapshots begin today
    db.commit()
    d = scoring.load(db, org.id)
    cur = scoring.readiness(d)["score"]
    for i, days in enumerate((60, 45, 30, 15)):
        db.add(ScoreSnapshot(org_id=org.id, scope="readiness", snap_date=today - timedelta(days=days), score=max(0, round(cur - 24 + i * 5.5, 1)), detail={"demo_seed": True}))
    scoring.record_snapshots(db, org.id, d)
    db.commit()
    return {"organization": org.name, "password": pw, "users": [u.email for u in users.values()]}


def main() -> None:
    Base.metadata.create_all(engine) if settings.auto_create_schema else None
    with SessionLocal() as db:
        n = seed_frameworks(db)
        print(f"Framework templates: {'loaded' if n else 'already present'} ({n} template mappings)")
        if "--demo" in sys.argv:
            res = seed_demo(db)
            print("Demo:", {k: v for k, v in res.items() if k != "password"})
            if "password" in res:
                print(f"Demo users share this password (shown once): {res['password']}")


if __name__ == "__main__":
    main()

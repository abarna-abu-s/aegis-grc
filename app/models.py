"""Database models. Every tenant-owned table carries org_id and is only ever queried through org-scoped helpers."""
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Stamped:
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class OrgOwned(Stamped):
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)


# ---------------------------------------------------------------- identity & tenancy
class Organization(Stamped, Base):
    __tablename__ = "organizations"
    name: Mapped[str] = mapped_column(String(160))
    industry: Mapped[str] = mapped_column(String(120), default="")
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class User(Stamped, Base):
    __tablename__ = "users"
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255))
    token_version: Mapped[int] = mapped_column(Integer, default=0)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Membership(Stamped, Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("org_id", "user_id"),)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String(30), default="viewer")
    user: Mapped[User] = relationship()


class Workspace(OrgOwned, Base):
    __tablename__ = "workspaces"
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")
    framework_id: Mapped[Optional[int]] = mapped_column(ForeignKey("frameworks.id"), nullable=True)


# ---------------------------------------------------------------- framework registry
class Framework(Stamped, Base):
    """org_id NULL => global template shipped with the product. source_status distinguishes verification level."""
    __tablename__ = "frameworks"
    __table_args__ = (UniqueConstraint("org_id", "key", "version"),)
    org_id: Mapped[Optional[int]] = mapped_column(ForeignKey("organizations.id"), nullable=True, index=True)
    key: Mapped[str] = mapped_column(String(60), index=True)
    name: Mapped[str] = mapped_column(String(180))
    version: Mapped[str] = mapped_column(String(40), default="1")
    publisher: Mapped[str] = mapped_column(String(160), default="")
    jurisdiction: Mapped[str] = mapped_column(String(80), default="")
    kind: Mapped[str] = mapped_column(String(20), default="standard")  # standard | regulation | custom
    # template_unverified: identifiers/titles only, entered from public knowledge, NOT legally verified
    # imported: loaded by the org from its own licensed material; custom: authored by the org
    source_status: Mapped[str] = mapped_column(String(30), default="template_unverified")
    description: Mapped[str] = mapped_column(Text, default="")
    controls: Mapped[list["FrameworkControl"]] = relationship(back_populates="framework", cascade="all, delete-orphan")


class FrameworkControl(Stamped, Base):
    __tablename__ = "framework_controls"
    __table_args__ = (UniqueConstraint("framework_id", "ref_id"),)
    framework_id: Mapped[int] = mapped_column(ForeignKey("frameworks.id"), index=True)
    ref_id: Mapped[str] = mapped_column(String(60))
    title: Mapped[str] = mapped_column(String(300))
    domain: Mapped[str] = mapped_column(String(160), default="")
    requirement_text: Mapped[str] = mapped_column(Text, default="")  # empty unless imported/authored by org
    evidence_requirements: Mapped[list] = mapped_column(JSON, default=list)  # suggested artefact types
    test_procedure: Mapped[str] = mapped_column(Text, default="")
    framework: Mapped[Framework] = relationship(back_populates="controls")


class ControlMapping(Stamped, Base):
    __tablename__ = "control_mappings"
    __table_args__ = (UniqueConstraint("org_id", "from_id", "to_id"),)
    org_id: Mapped[Optional[int]] = mapped_column(ForeignKey("organizations.id"), nullable=True, index=True)
    from_id: Mapped[int] = mapped_column(ForeignKey("framework_controls.id"), index=True)
    to_id: Mapped[int] = mapped_column(ForeignKey("framework_controls.id"), index=True)
    relation: Mapped[str] = mapped_column(String(20), default="related")  # equivalent | related | partial
    source: Mapped[str] = mapped_column(String(20), default="seed")  # seed | manual | ai_suggested
    status: Mapped[str] = mapped_column(String(20), default="template")  # template | proposed | approved | rejected
    note: Mapped[str] = mapped_column(String(300), default="")


# ---------------------------------------------------------------- controls, assessments
class Control(OrgOwned, Base):
    __tablename__ = "controls"
    __table_args__ = (UniqueConstraint("org_id", "control_key"),)
    control_key: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    owner_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    implementation_status: Mapped[str] = mapped_column(String(30), default="not_started")
    review_frequency_days: Mapped[int] = mapped_column(Integer, default=90)
    next_review_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    evidence_requirements: Mapped[list] = mapped_column(JSON, default=list)
    test_procedure: Mapped[str] = mapped_column(Text, default="")


class Assessment(OrgOwned, Base):
    __tablename__ = "assessments"
    name: Mapped[str] = mapped_column(String(200))
    framework_id: Mapped[int] = mapped_column(ForeignKey("frameworks.id"))
    workspace_id: Mapped[Optional[int]] = mapped_column(ForeignKey("workspaces.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="in_progress")  # in_progress | in_review | closed
    due_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    framework: Mapped[Framework] = relationship()
    items: Mapped[list["AssessmentItem"]] = relationship(back_populates="assessment", cascade="all, delete-orphan")


class AssessmentItem(Stamped, Base):
    __tablename__ = "assessment_items"
    __table_args__ = (UniqueConstraint("assessment_id", "framework_control_id"),)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    assessment_id: Mapped[int] = mapped_column(ForeignKey("assessments.id"), index=True)
    framework_control_id: Mapped[int] = mapped_column(ForeignKey("framework_controls.id"), index=True)
    control_id: Mapped[Optional[int]] = mapped_column(ForeignKey("controls.id"), nullable=True, index=True)
    applicability: Mapped[str] = mapped_column(String(20), default="applicable")  # applicable | not_applicable
    justification: Mapped[str] = mapped_column(Text, default="")
    result: Mapped[str] = mapped_column(String(20), default="not_assessed")  # not_assessed|compliant|partial|non_compliant
    notes: Mapped[str] = mapped_column(Text, default="")
    reviewed_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    assessment: Mapped[Assessment] = relationship(back_populates="items")
    framework_control: Mapped[FrameworkControl] = relationship()
    control: Mapped[Optional[Control]] = relationship()


class ControlTest(OrgOwned, Base):
    __tablename__ = "control_tests"
    control_id: Mapped[int] = mapped_column(ForeignKey("controls.id"), index=True)
    result: Mapped[str] = mapped_column(String(20))  # pass | fail | inconclusive
    notes: Mapped[str] = mapped_column(Text, default="")
    tested_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    tested_at: Mapped[date] = mapped_column(Date)


# ---------------------------------------------------------------- evidence
class Evidence(OrgOwned, Base):
    __tablename__ = "evidence"
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(120), default="manual upload")  # provenance
    owner_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    collected_on: Mapped[date] = mapped_column(Date, default=date.today)
    valid_until: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    review_status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | approved | rejected
    reviewed_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    sensitivity: Mapped[str] = mapped_column(String(20), default="standard")  # standard | restricted
    classification: Mapped[str] = mapped_column(String(80), default="")
    versions: Mapped[list["EvidenceVersion"]] = relationship(back_populates="evidence", cascade="all, delete-orphan",
                                                              order_by="EvidenceVersion.version")


class EvidenceVersion(Stamped, Base):
    __tablename__ = "evidence_versions"
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    evidence_id: Mapped[int] = mapped_column(ForeignKey("evidence.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    filename: Mapped[str] = mapped_column(String(255))
    stored_name: Mapped[str] = mapped_column(String(255))
    mime: Mapped[str] = mapped_column(String(120), default="")
    size: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    extracted_text: Mapped[str] = mapped_column(Text, default="")
    uploaded_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    evidence: Mapped[Evidence] = relationship(back_populates="versions")


class EvidenceControlLink(Stamped, Base):
    __tablename__ = "evidence_control_links"
    __table_args__ = (UniqueConstraint("evidence_id", "control_id"),)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    evidence_id: Mapped[int] = mapped_column(ForeignKey("evidence.id"), index=True)
    control_id: Mapped[int] = mapped_column(ForeignKey("controls.id"), index=True)
    linked_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    origin: Mapped[str] = mapped_column(String(20), default="manual")  # manual | ai_suggested_accepted


class EvidenceRequest(OrgOwned, Base):
    __tablename__ = "evidence_requests"
    control_id: Mapped[int] = mapped_column(ForeignKey("controls.id"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    instructions: Mapped[str] = mapped_column(Text, default="")
    assignee_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    due_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open | submitted | closed | cancelled
    evidence_id: Mapped[Optional[int]] = mapped_column(ForeignKey("evidence.id"), nullable=True)
    requested_by: Mapped[int] = mapped_column(ForeignKey("users.id"))


# ---------------------------------------------------------------- policies, documents, retrieval index
class Policy(OrgOwned, Base):
    __tablename__ = "policies"
    title: Mapped[str] = mapped_column(String(255))
    category: Mapped[str] = mapped_column(String(80), default="policy")  # policy | standard | procedure
    body: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft | in_review | approved | retired
    owner_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    approved_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    next_review_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)


class PolicyVersion(Stamped, Base):
    __tablename__ = "policy_versions"
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    policy_id: Mapped[int] = mapped_column(ForeignKey("policies.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    body: Mapped[str] = mapped_column(Text)
    changed_by: Mapped[int] = mapped_column(ForeignKey("users.id"))


class PolicyControlLink(Stamped, Base):
    __tablename__ = "policy_control_links"
    __table_args__ = (UniqueConstraint("policy_id", "control_id"),)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    policy_id: Mapped[int] = mapped_column(ForeignKey("policies.id"), index=True)
    control_id: Mapped[int] = mapped_column(ForeignKey("controls.id"), index=True)


class SourceDocument(OrgOwned, Base):
    """Uploaded reference documents (standards excerpts, prior audit reports, questionnaires) used for retrieval."""
    __tablename__ = "source_documents"
    title: Mapped[str] = mapped_column(String(255))
    filename: Mapped[str] = mapped_column(String(255))
    stored_name: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(Text, default="")
    sensitivity: Mapped[str] = mapped_column(String(20), default="standard")
    uploaded_by: Mapped[int] = mapped_column(ForeignKey("users.id"))


class Chunk(Stamped, Base):
    """Retrieval index. org_id + sensitivity enforce permission-aware retrieval."""
    __tablename__ = "chunks"
    org_id: Mapped[Optional[int]] = mapped_column(ForeignKey("organizations.id"), nullable=True, index=True)
    source_type: Mapped[str] = mapped_column(String(30), index=True)  # policy | evidence | document | framework
    source_id: Mapped[int] = mapped_column(Integer, index=True)
    source_title: Mapped[str] = mapped_column(String(300), default="")
    source_version: Mapped[int] = mapped_column(Integer, default=1)
    idx: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list] = mapped_column(JSON, default=list)
    embedding_model: Mapped[str] = mapped_column(String(80), default="local-hash-v1")
    sensitivity: Mapped[str] = mapped_column(String(20), default="standard")
    verified: Mapped[bool] = mapped_column(Boolean, default=False)  # framework chunks from verified sources only


# ---------------------------------------------------------------- risk, assets, vendors, privacy
class Asset(OrgOwned, Base):
    __tablename__ = "assets"
    name: Mapped[str] = mapped_column(String(200))
    asset_type: Mapped[str] = mapped_column(String(40), default="application")
    criticality: Mapped[int] = mapped_column(Integer, default=3)
    owner_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    data_classification: Mapped[str] = mapped_column(String(30), default="internal")
    description: Mapped[str] = mapped_column(Text, default="")


class Risk(OrgOwned, Base):
    __tablename__ = "risks"
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(60), default="security")
    asset_id: Mapped[Optional[int]] = mapped_column(ForeignKey("assets.id"), nullable=True)
    vendor_id: Mapped[Optional[int]] = mapped_column(ForeignKey("vendors.id"), nullable=True)
    owner_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    likelihood: Mapped[int] = mapped_column(Integer, default=3)
    impact: Mapped[int] = mapped_column(Integer, default=3)
    residual_likelihood: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    residual_impact: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    treatment: Mapped[str] = mapped_column(String(20), default="mitigate")  # mitigate|accept|transfer|avoid
    status: Mapped[str] = mapped_column(String(20), default="open")  # open|treating|accepted|closed


class RiskControlLink(Stamped, Base):
    __tablename__ = "risk_control_links"
    __table_args__ = (UniqueConstraint("risk_id", "control_id"),)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    risk_id: Mapped[int] = mapped_column(ForeignKey("risks.id"), index=True)
    control_id: Mapped[int] = mapped_column(ForeignKey("controls.id"), index=True)


class TreatmentPlan(OrgOwned, Base):
    __tablename__ = "treatment_plans"
    risk_id: Mapped[int] = mapped_column(ForeignKey("risks.id"), index=True)
    action: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    owner_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    due_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="planned")  # planned|in_progress|done|cancelled


class Vendor(OrgOwned, Base):
    __tablename__ = "vendors"
    name: Mapped[str] = mapped_column(String(200))
    service: Mapped[str] = mapped_column(String(300), default="")
    criticality: Mapped[int] = mapped_column(Integer, default=3)
    data_access: Mapped[str] = mapped_column(String(40), default="none")  # none | internal | personal | sensitive
    risk_tier: Mapped[str] = mapped_column(String(20), default="medium")
    status: Mapped[str] = mapped_column(String(20), default="active")  # onboarding|active|offboarding|terminated
    owner_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    last_assessed: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    next_assessment: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    contract_end: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    assurance_notes: Mapped[str] = mapped_column(Text, default="")  # e.g. SOC 2 report received (organisation-entered)


class PrivacyRecord(OrgOwned, Base):
    __tablename__ = "privacy_records"
    name: Mapped[str] = mapped_column(String(200))
    purpose: Mapped[str] = mapped_column(Text, default="")
    data_categories: Mapped[str] = mapped_column(String(400), default="")
    data_subjects: Mapped[str] = mapped_column(String(300), default="")
    lawful_basis: Mapped[str] = mapped_column(String(80), default="")
    retention_period: Mapped[str] = mapped_column(String(120), default="")
    cross_border: Mapped[bool] = mapped_column(Boolean, default=False)
    asset_id: Mapped[Optional[int]] = mapped_column(ForeignKey("assets.id"), nullable=True)
    vendor_id: Mapped[Optional[int]] = mapped_column(ForeignKey("vendors.id"), nullable=True)
    owner_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    dpia_required: Mapped[bool] = mapped_column(Boolean, default=False)
    dpia_status: Mapped[str] = mapped_column(String(20), default="not_started")


# ---------------------------------------------------------------- audit, findings, tasks, workflow
class AuditEngagement(OrgOwned, Base):
    __tablename__ = "audit_engagements"
    name: Mapped[str] = mapped_column(String(200))
    framework_id: Mapped[Optional[int]] = mapped_column(ForeignKey("frameworks.id"), nullable=True)
    audit_type: Mapped[str] = mapped_column(String(30), default="internal")  # internal | external | readiness
    scope: Mapped[str] = mapped_column(Text, default="")
    lead_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    start_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    end_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="planned")  # planned|fieldwork|reporting|closed


class Finding(OrgOwned, Base):
    __tablename__ = "findings"
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    audit_id: Mapped[Optional[int]] = mapped_column(ForeignKey("audit_engagements.id"), nullable=True)
    control_id: Mapped[Optional[int]] = mapped_column(ForeignKey("controls.id"), nullable=True)
    severity: Mapped[str] = mapped_column(String(20), default="medium")  # low|medium|high|critical
    status: Mapped[str] = mapped_column(String(20), default="open")  # open|in_remediation|resolved|closed
    owner_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    due_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)


class CorrectiveAction(OrgOwned, Base):
    __tablename__ = "corrective_actions"
    finding_id: Mapped[int] = mapped_column(ForeignKey("findings.id"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    owner_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    due_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open|in_progress|done|verified
    verification_notes: Mapped[str] = mapped_column(Text, default="")
    ai_drafted: Mapped[bool] = mapped_column(Boolean, default=False)


class Task(OrgOwned, Base):
    __tablename__ = "tasks"
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    assignee_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    due_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    priority: Mapped[str] = mapped_column(String(20), default="medium")
    status: Mapped[str] = mapped_column(String(20), default="open")  # open|in_progress|done|cancelled
    related_type: Mapped[str] = mapped_column(String(30), default="")
    related_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    origin: Mapped[str] = mapped_column(String(30), default="manual")  # manual | recommendation | automation


class Approval(OrgOwned, Base):
    __tablename__ = "approvals"
    entity_type: Mapped[str] = mapped_column(String(30))  # policy | evidence | exception | assessment_item
    entity_id: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(255), default="")
    requested_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    approver_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|approved|rejected|cancelled
    comment: Mapped[str] = mapped_column(Text, default="")
    decided_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class ExceptionRecord(OrgOwned, Base):
    __tablename__ = "exceptions"
    title: Mapped[str] = mapped_column(String(255))
    control_id: Mapped[Optional[int]] = mapped_column(ForeignKey("controls.id"), nullable=True)
    justification: Mapped[str] = mapped_column(Text)
    compensating_controls: Mapped[str] = mapped_column(Text, default="")
    risk_id: Mapped[Optional[int]] = mapped_column(ForeignKey("risks.id"), nullable=True)
    requested_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(20), default="requested")  # requested|approved|rejected|expired|revoked
    expires_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    approved_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)


# ---------------------------------------------------------------- platform: audit trail, notifications, rules, reports
class AuditEvent(Base):
    """Append-only by convention + hash chain. Tamper-EVIDENT for edits through the app/ORM; a DB superuser can still
    rewrite history and the chain, so this is not tamper-proof (see docs)."""
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    actor_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    actor_label: Mapped[str] = mapped_column(String(255), default="")
    action: Mapped[str] = mapped_column(String(60), index=True)
    entity_type: Mapped[str] = mapped_column(String(40), default="")
    entity_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    detail: Mapped[str] = mapped_column(Text, default="")
    ip: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    prev_hash: Mapped[str] = mapped_column(String(64), default="")
    hash: Mapped[str] = mapped_column(String(64), default="")


class Notification(OrgOwned, Base):
    __tablename__ = "notifications"
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text, default="")
    link: Mapped[str] = mapped_column(String(200), default="")
    dedupe_key: Mapped[str] = mapped_column(String(120), default="", index=True)
    read: Mapped[bool] = mapped_column(Boolean, default=False)


class AlertRule(OrgOwned, Base):
    __tablename__ = "alert_rules"
    name: Mapped[str] = mapped_column(String(200))
    metric: Mapped[str] = mapped_column(String(40))
    threshold: Mapped[float] = mapped_column(Float, default=30)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class AutomationRule(OrgOwned, Base):
    __tablename__ = "automation_rules"
    name: Mapped[str] = mapped_column(String(200))
    trigger: Mapped[str] = mapped_column(String(40))
    action: Mapped[str] = mapped_column(String(40), default="create_task")
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class ScoreSnapshot(OrgOwned, Base):
    __tablename__ = "score_snapshots"
    __table_args__ = (UniqueConstraint("org_id", "scope", "snap_date"),)
    scope: Mapped[str] = mapped_column(String(60))  # "readiness" or "framework:<id>"
    snap_date: Mapped[date] = mapped_column(Date)
    score: Mapped[float] = mapped_column(Float)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


class Report(OrgOwned, Base):
    __tablename__ = "reports"
    title: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(40))
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    content: Mapped[str] = mapped_column(Text)
    generated_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    ai_assisted: Mapped[bool] = mapped_column(Boolean, default=False)


class AISuggestion(OrgOwned, Base):
    """Every AI/heuristic recommendation is persisted as a suggestion awaiting human review."""
    __tablename__ = "ai_suggestions"
    kind: Mapped[str] = mapped_column(String(40))
    target_type: Mapped[str] = mapped_column(String(30))
    target_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    sources: Mapped[list] = mapped_column(JSON, default=list)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    method: Mapped[str] = mapped_column(String(40), default="heuristic")  # heuristic | llm
    status: Mapped[str] = mapped_column(String(20), default="proposed")  # proposed|accepted|dismissed
    reviewed_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)

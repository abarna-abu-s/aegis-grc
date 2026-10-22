"""Risk, assets, vendors, privacy, audits, findings, corrective actions, tasks, exceptions, rules."""
from datetime import date

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import select

from .. import crud, scoring
from ..models import (Asset, AlertRule, AuditEngagement, AutomationRule, Control, CorrectiveAction, ExceptionRecord, Finding,
                      Framework, PrivacyRecord, Risk, RiskControlLink, Task, TreatmentPlan, User, Vendor)
from ..security import Ctx, audit, get_ctx

router = APIRouter()
R = router.include_router
S, T, I, D, E, B, J, FK = crud.S, crud.T, crud.I, crud.D, crud.E, crud.B, crud.J, crud.FK


# ---------------------------------------------------------------- assets / vendors / privacy
R(crud.make_router(crud.Res("assets", Asset, {
    "name": S(200, True), "asset_type": E(("application", "infrastructure", "data_store", "device", "service", "ai_system", "other")),
    "criticality": I(1, 5), "owner_id": FK(User), "data_classification": E(("public", "internal", "confidential", "restricted")),
    "description": T()}, search=("name", "description"), filters=("asset_type", "owner_id", "data_classification"), order="name", entity="asset")))

R(crud.make_router(crud.Res("vendors", Vendor, {
    "name": S(200, True), "service": S(300), "criticality": I(1, 5), "data_access": E(("none", "internal", "personal", "sensitive")),
    "risk_tier": E(("low", "medium", "high", "critical")), "status": E(("onboarding", "active", "offboarding", "terminated")),
    "owner_id": FK(User), "last_assessed": D(), "next_assessment": D(), "contract_end": D(), "assurance_notes": T()},
    search=("name", "service"), filters=("risk_tier", "status", "data_access"), order="name", entity="vendor")))

R(crud.make_router(crud.Res("privacy-records", PrivacyRecord, {
    "name": S(200, True), "purpose": T(), "data_categories": S(400), "data_subjects": S(300), "lawful_basis": S(80),
    "retention_period": S(120), "cross_border": B(), "asset_id": FK(Asset), "vendor_id": FK(Vendor), "owner_id": FK(User),
    "dpia_required": B(), "dpia_status": E(("not_started", "in_progress", "completed", "not_required"))},
    search=("name", "purpose", "data_categories"), filters=("dpia_status", "lawful_basis"), order="name", entity="privacy_record")))


# ---------------------------------------------------------------- risks
def _risk_enrich(ctx, rows, objs):
    links = {}
    for l in ctx.db.scalars(select(RiskControlLink).where(RiskControlLink.org_id == ctx.org_id)):
        links.setdefault(l.risk_id, []).append(l.control_id)
    plans = {}
    for p in ctx.db.scalars(select(TreatmentPlan).where(TreatmentPlan.org_id == ctx.org_id)):
        plans.setdefault(p.risk_id, []).append(p.status)
    for r in rows:
        r["inherent_score"] = scoring.risk_score(r["likelihood"], r["impact"])
        r["inherent_level"] = scoring.risk_level(r["inherent_score"])
        if r.get("residual_likelihood") and r.get("residual_impact"):
            r["residual_score"] = scoring.risk_score(r["residual_likelihood"], r["residual_impact"])
            r["residual_level"] = scoring.risk_level(r["residual_score"])
        r["control_ids"] = links.get(r["id"], [])
        r["treatment_plans"] = len(plans.get(r["id"], []))


def _risk_check(ctx, clean):
    if (clean.get("treatment") == "accept" or clean.get("status") == "accepted") and not ctx.can("approve"):
        raise HTTPException(403, "Only reviewers can accept a risk")


R(crud.make_router(crud.Res("risks", Risk, {
    "title": S(255, True), "description": T(), "category": S(60), "asset_id": FK(Asset), "vendor_id": FK(Vendor), "owner_id": FK(User),
    "likelihood": I(1, 5), "impact": I(1, 5), "residual_likelihood": I(1, 5), "residual_impact": I(1, 5),
    "treatment": E(("mitigate", "accept", "transfer", "avoid")), "status": E(("open", "treating", "accepted", "closed"))},
    search=("title", "description", "category"), filters=("status", "treatment", "category", "owner_id", "asset_id", "vendor_id"),
    order="-id", enrich=_risk_enrich, entity="risk", before_create=_risk_check, before_update=lambda c, o, cl: _risk_check(c, cl))))


@router.put("/api/risks/{rid}/controls", tags=["risks"])
def set_risk_controls(rid: int, body: dict = Body(...), ctx: Ctx = Depends(get_ctx)):
    ctx.require("manage")
    crud.scoped(ctx, Risk, rid)
    ids = {int(i) for i in body.get("control_ids", [])}
    for i in ids:
        crud.scoped(ctx, Control, i)
    for l in ctx.db.scalars(select(RiskControlLink).where(RiskControlLink.risk_id == rid)):
        ctx.db.delete(l)
    ctx.db.flush()
    for i in ids:
        ctx.db.add(RiskControlLink(org_id=ctx.org_id, risk_id=rid, control_id=i))
    audit(ctx, "risk.controls_linked", "risk", rid, f"{len(ids)} controls")
    ctx.db.commit()
    return {"control_ids": sorted(ids)}


@router.get("/api/risk-matrix", tags=["risks"])
def risk_matrix(ctx: Ctx = Depends(get_ctx), view: str = "inherent"):
    cells = {}
    for r in ctx.db.scalars(select(Risk).where(Risk.org_id == ctx.org_id, Risk.status != "closed")):
        l, i = (r.residual_likelihood, r.residual_impact) if view == "residual" and r.residual_likelihood and r.residual_impact else (r.likelihood, r.impact)
        cells.setdefault(f"{l},{i}", []).append({"id": r.id, "title": r.title})
    return {"view": view, "cells": cells, "formula": "Score = likelihood (1-5) x impact (1-5); low <6, medium 6-11, high 12-19, critical >=20"}


def _plan_enrich(ctx, rows, objs):
    ids = {r["risk_id"] for r in rows}
    t = {r.id: r.title for r in ctx.db.scalars(select(Risk).where(Risk.id.in_(ids)))} if ids else {}
    for r in rows:
        r["risk_title"] = t.get(r["risk_id"], "")


R(crud.make_router(crud.Res("treatment-plans", TreatmentPlan, {
    "risk_id": FK(Risk, True), "action": S(300, True), "description": T(), "owner_id": FK(User), "due_date": D(),
    "status": E(("planned", "in_progress", "done", "cancelled"))}, search=("action",), filters=("risk_id", "status", "owner_id"),
    order="due_date", write_perm="contribute", enrich=_plan_enrich, entity="treatment_plan")))


# ---------------------------------------------------------------- audits, findings, corrective actions
R(crud.make_router(crud.Res("audits", AuditEngagement, {
    "name": S(200, True), "framework_id": I(1), "audit_type": E(("internal", "external", "readiness")), "scope": T(),
    "lead_id": FK(User), "start_date": D(), "end_date": D(), "status": E(("planned", "fieldwork", "reporting", "closed"))},
    search=("name", "scope"), filters=("status", "audit_type"), order="-id", write_perm="audit", entity="audit",
    before_create=lambda c, cl: _check_fw(c, cl), before_update=lambda c, o, cl: _check_fw(c, cl))))


def _check_fw(ctx, clean):
    fid = clean.get("framework_id")
    if fid:
        f = ctx.db.get(Framework, fid)
        if not f or f.org_id not in (None, ctx.org_id):
            raise HTTPException(422, {"message": "Validation failed", "errors": {"framework_id": "Framework not found"}})
    d, e = clean.get("start_date"), clean.get("end_date")
    if d and e and e < d:
        raise HTTPException(422, {"message": "Validation failed", "errors": {"end_date": "End date is before start date"}})


def _find_enrich(ctx, rows, objs):
    ids = {r["id"] for r in rows}
    counts = {}
    if ids:
        for c in ctx.db.scalars(select(CorrectiveAction).where(CorrectiveAction.finding_id.in_(ids))):
            counts.setdefault(c.finding_id, [0, 0])
            counts[c.finding_id][0] += 1
            counts[c.finding_id][1] += c.status in ("done", "verified")
    for r in rows:
        n, done = counts.get(r["id"], [0, 0])
        r["actions_total"], r["actions_done"] = n, done
        r["overdue"] = bool(r["due_date"] and r["status"] in ("open", "in_remediation") and r["due_date"] < date.today().isoformat())


def _find_check(ctx, obj_or_clean, clean=None):
    c = clean if clean is not None else obj_or_clean
    if c.get("status") in ("resolved", "closed") and not ctx.can("audit"):
        raise HTTPException(403, "Only audit/compliance roles can resolve or close findings")


def _find_update(ctx, f, clean):
    _find_check(ctx, f, clean)
    if clean.get("status") in ("resolved", "closed"):
        open_actions = ctx.db.scalar(select(CorrectiveAction.id).where(CorrectiveAction.finding_id == f.id, CorrectiveAction.status.in_(("open", "in_progress"))))
        if open_actions:
            raise HTTPException(409, "Complete or verify all corrective actions before resolving this finding")


R(crud.make_router(crud.Res("findings", Finding, {
    "title": S(255, True), "description": T(), "audit_id": FK(AuditEngagement), "control_id": FK(Control),
    "severity": E(("low", "medium", "high", "critical")), "status": E(("open", "in_remediation", "resolved", "closed")),
    "owner_id": FK(User), "due_date": D()}, search=("title", "description"),
    filters=("status", "severity", "audit_id", "control_id", "owner_id"), order="-id", write_perm="contribute", enrich=_find_enrich,
    entity="finding", before_create=_find_check, before_update=_find_update)))


def _cap_enrich(ctx, rows, objs):
    ids = {r["finding_id"] for r in rows}
    t = {f.id: f.title for f in ctx.db.scalars(select(Finding).where(Finding.id.in_(ids)))} if ids else {}
    for r in rows:
        r["finding_title"] = t.get(r["finding_id"], "")


def _cap_update(ctx, cap, clean):
    if clean.get("status") == "verified" and not ctx.can("audit"):
        raise HTTPException(403, "Only audit/compliance roles can verify corrective actions")
    if clean.get("status") == "verified" and not (clean.get("verification_notes") or cap.verification_notes):
        raise HTTPException(422, {"message": "Verification notes are required", "errors": {"verification_notes": "Describe how closure was verified"}})


R(crud.make_router(crud.Res("corrective-actions", CorrectiveAction, {
    "finding_id": FK(Finding, True), "title": S(255, True), "description": T(), "owner_id": FK(User), "due_date": D(),
    "status": E(("open", "in_progress", "done", "verified")), "verification_notes": T(), "ai_drafted": B()},
    search=("title", "description"), filters=("finding_id", "status", "owner_id"), order="due_date", write_perm="contribute",
    enrich=_cap_enrich, entity="corrective_action", before_update=_cap_update)))


# ---------------------------------------------------------------- tasks, exceptions
def _task_enrich(ctx, rows, objs):
    for r in rows:
        r["overdue"] = bool(r["due_date"] and r["status"] in ("open", "in_progress") and r["due_date"] < date.today().isoformat())


R(crud.make_router(crud.Res("tasks", Task, {
    "title": S(255, True), "description": T(), "assignee_id": FK(User), "due_date": D(), "priority": E(("low", "medium", "high")),
    "status": E(("open", "in_progress", "done", "cancelled")), "related_type": S(30), "related_id": I(0)},
    search=("title", "description"), filters=("status", "priority", "assignee_id", "related_type"), order="due_date",
    write_perm="contribute", enrich=_task_enrich, entity="task")))


def _exc_create(ctx, clean):
    clean["requested_by"] = ctx.user.id
    clean["status"] = "requested"


def _exc_update(ctx, x, clean):
    if "status" in clean and clean["status"] in ("approved", "rejected") and x.status != clean["status"]:
        raise HTTPException(409, "Exceptions are approved or rejected through the approval workflow")
    if "status" in clean and clean["status"] == "revoked" and not ctx.can("approve"):
        raise HTTPException(403, "Only approvers can revoke an exception")


def _exc_enrich(ctx, rows, objs):
    ids = {r["control_id"] for r in rows if r.get("control_id")}
    k = {c.id: c.control_key for c in ctx.db.scalars(select(Control).where(Control.id.in_(ids)))} if ids else {}
    for r in rows:
        r["control_key"] = k.get(r.get("control_id"), "")
        r["expired"] = bool(r["expires_on"] and r["expires_on"] < date.today().isoformat() and r["status"] == "approved")


R(crud.make_router(crud.Res("exceptions", ExceptionRecord, {
    "title": S(255, True), "control_id": FK(Control), "justification": T(True), "compensating_controls": T(), "risk_id": FK(Risk),
    "status": E(("requested", "approved", "rejected", "expired", "revoked")), "expires_on": D()},
    search=("title", "justification"), filters=("status", "control_id"), order="-id", write_perm="contribute",
    enrich=_exc_enrich, entity="exception", before_create=_exc_create, before_update=_exc_update)))


# ---------------------------------------------------------------- alert & automation rules
ALERT_METRICS = ("evidence_expiring_days", "controls_without_evidence", "risk_score_above", "overdue_tasks", "readiness_below", "open_critical_findings")
AUTOMATION_TRIGGERS = ("evidence_expired", "risk_high", "finding_critical_unowned", "vendor_assessment_due", "policy_review_overdue")

R(crud.make_router(crud.Res("alert-rules", AlertRule, {
    "name": S(200, True), "metric": E(ALERT_METRICS, True), "threshold": I(0, 1000), "enabled": B(True)},
    search=("name",), write_perm="admin", entity="alert_rule")))

R(crud.make_router(crud.Res("automation-rules", AutomationRule, {
    "name": S(200, True), "trigger": E(AUTOMATION_TRIGGERS, True), "action": E(("create_task", "notify")), "params": J(), "enabled": B(True)},
    search=("name",), write_perm="admin", entity="automation_rule")))

"""Transparent scoring and analytics computed from persisted data. Every formula is returned with its result."""
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import select

from .frameworks_data import suggested_evidence
from .models import (Assessment, AssessmentItem, Control, ControlMapping, ControlTest, Evidence, EvidenceControlLink,
                     ExceptionRecord, Finding, FrameworkControl, Policy, Risk, Task, Vendor, EvidenceRequest,
                     AuditEngagement, ScoreSnapshot, TreatmentPlan)

EXPIRING_DAYS = 30
RESULT_WEIGHT = {"compliant": 1.0, "partial": 0.5, "non_compliant": 0.0, "not_assessed": 0.0}
SEV_WEIGHT = {"critical": 0.40, "high": 0.25, "medium": 0.10, "low": 0.03}

READINESS_FORMULA = (
    "Readiness = 40% x assessment score + 30% x evidence coverage + 15% x policy hygiene + 15% x findings health. "
    "Assessment score = (compliant + 0.5 x partial) / applicable items, results set by human reviewers only. "
    "Evidence coverage = applicable items whose linked control has at least one approved, unexpired evidence item. "
    "Policy hygiene = approved policies not past their review date / all non-retired policies. "
    "Findings health = max(0, 1 - sum of open-finding weights); critical 0.40, high 0.25, medium 0.10, low 0.03 "
    "(counted only once at least one requirement is in scope)."
)


def evidence_status(ev: Evidence, today: date, renew: bool = False) -> str:
    if ev.review_status == "rejected":
        return "rejected"
    if ev.valid_until and ev.valid_until < today and not renew:
        return "expired"
    if ev.review_status != "approved":
        return "pending"
    if ev.valid_until and ev.valid_until <= today + timedelta(days=EXPIRING_DAYS) and not renew:
        return "expiring"
    return "current"


@dataclass
class Data:
    today: date
    controls: dict = field(default_factory=dict)
    evidence: dict = field(default_factory=dict)
    ev_by_control: dict = field(default_factory=dict)   # control_id -> [Evidence]
    tests: dict = field(default_factory=dict)           # control_id -> latest ControlTest
    items: list = field(default_factory=list)
    fcs: dict = field(default_factory=dict)
    assessments: dict = field(default_factory=dict)
    policies: list = field(default_factory=list)
    findings: list = field(default_factory=list)
    risks: list = field(default_factory=list)
    exceptions: list = field(default_factory=list)


def load(db, org_id: int, today: Optional[date] = None) -> Data:
    d = Data(today=today or date.today())
    d.controls = {c.id: c for c in db.scalars(select(Control).where(Control.org_id == org_id))}
    d.evidence = {e.id: e for e in db.scalars(select(Evidence).where(Evidence.org_id == org_id))}
    for l in db.scalars(select(EvidenceControlLink).where(EvidenceControlLink.org_id == org_id)):
        if l.evidence_id in d.evidence:
            d.ev_by_control.setdefault(l.control_id, []).append(d.evidence[l.evidence_id])
    for t in db.scalars(select(ControlTest).where(ControlTest.org_id == org_id).order_by(ControlTest.tested_at, ControlTest.id)):
        d.tests[t.control_id] = t
    d.items = list(db.scalars(select(AssessmentItem).where(AssessmentItem.org_id == org_id)))
    ids = {i.framework_control_id for i in d.items}
    if ids:
        d.fcs = {f.id: f for f in db.scalars(select(FrameworkControl).where(FrameworkControl.id.in_(ids)))}
    d.assessments = {a.id: a for a in db.scalars(select(Assessment).where(Assessment.org_id == org_id))}
    d.policies = list(db.scalars(select(Policy).where(Policy.org_id == org_id)))
    d.findings = list(db.scalars(select(Finding).where(Finding.org_id == org_id)))
    d.risks = list(db.scalars(select(Risk).where(Risk.org_id == org_id)))
    d.exceptions = list(db.scalars(select(ExceptionRecord).where(ExceptionRecord.org_id == org_id)))
    return d


def control_state(d: Data, cid: int, ov: Optional[dict] = None) -> dict:
    ov = ov or {}
    evs = d.ev_by_control.get(cid, [])
    statuses = [evidence_status(e, d.today, ov.get("renew_expired", False)) for e in evs]
    test = d.tests.get(cid)
    current = any(s in ("current", "expiring") for s in statuses)
    if cid in ov.get("compliant_controls", set()):
        current, test = True, None
    if not evs and cid not in ov.get("compliant_controls", set()):
        health = "no_evidence"
    elif test and test.result == "fail":
        health = "failing"
    elif current:
        health = "healthy"
    else:
        health = "at_risk"
    return {"health": health, "evidence_count": len(evs), "current_evidence": current,
            "evidence_statuses": statuses, "latest_test": test.result if test else None}


def assessment_scores(d: Data, ov: Optional[dict] = None) -> list[dict]:
    ov = ov or {}
    out = []
    for a in d.assessments.values():
        its = [i for i in d.items if i.assessment_id == a.id]
        app = [i for i in its if i.applicability == "applicable"]
        counts = {"compliant": 0, "partial": 0, "non_compliant": 0, "not_assessed": 0}
        covered = 0
        for i in app:
            res = "compliant" if (i.control_id and i.control_id in ov.get("compliant_controls", set())) else i.result
            counts[res] += 1
            if i.control_id and control_state(d, i.control_id, ov)["current_evidence"]:
                covered += 1
        n = len(app)
        score = round(100 * sum(RESULT_WEIGHT[k] * v for k, v in counts.items()) / n, 1) if n else None
        out.append({"assessment_id": a.id, "name": a.name, "framework_id": a.framework_id, "status": a.status,
                    "total_items": len(its), "applicable": n, "not_applicable": len(its) - n, "counts": counts,
                    "assessment_score": score, "evidence_coverage": round(100 * covered / n, 1) if n else None,
                    "linked_controls": sum(1 for i in app if i.control_id)})
    return out


def policy_hygiene(d: Data) -> Optional[float]:
    live = [p for p in d.policies if p.status != "retired"]
    if not live:
        return None
    ok = [p for p in live if p.status == "approved" and not (p.next_review_date and p.next_review_date < d.today)]
    return round(100 * len(ok) / len(live), 1)


def findings_health(d: Data, ov: Optional[dict] = None) -> Optional[float]:
    if not any(i.applicability == "applicable" for i in d.items):
        return None  # nothing assessed yet: "no open findings" must not earn points
    if (ov or {}).get("close_findings"):
        return 100.0
    pen = sum(SEV_WEIGHT.get(f.severity, 0.1) for f in d.findings if f.status in ("open", "in_remediation"))
    return round(100 * max(0.0, 1 - pen), 1)


def readiness(d: Data, ov: Optional[dict] = None) -> dict:
    sc = [s for s in assessment_scores(d, ov) if s["applicable"]]
    tot = sum(s["applicable"] for s in sc)
    a_score = sum((s["assessment_score"] or 0) * s["applicable"] for s in sc) / tot if tot else None
    cov = sum((s["evidence_coverage"] or 0) * s["applicable"] for s in sc) / tot if tot else None
    ph, fh = policy_hygiene(d), findings_health(d, ov)
    comps = {"assessment_score": (a_score, 0.40), "evidence_coverage": (cov, 0.30), "policy_hygiene": (ph, 0.15),
             "findings_health": (fh, 0.15)}
    total = sum((v or 0) * w for v, w in comps.values())
    missing = [k for k, (v, _) in comps.items() if v is None]
    return {"score": round(total, 1), "formula": READINESS_FORMULA,
            "components": {k: {"value": None if v is None else round(v, 1), "weight": w, "contribution": round((v or 0) * w, 1)}
                           for k, (v, w) in comps.items()},
            "insufficient_data": missing,
            "note": "Components with no data contribute 0. This is an internal indicator, not an audit opinion or certification status."}


def heatmap(d: Data) -> dict:
    cols = [a for a in d.assessments.values()]
    domains = sorted({d.fcs[i.framework_control_id].domain for i in d.items if i.framework_control_id in d.fcs})
    cells = []
    for dom in domains:
        row = []
        for a in cols:
            its = [i for i in d.items if i.assessment_id == a.id and i.applicability == "applicable"
                   and d.fcs.get(i.framework_control_id) and d.fcs[i.framework_control_id].domain == dom]
            if not its:
                row.append(None)
                continue
            cov = sum(1 for i in its if i.control_id and control_state(d, i.control_id)["current_evidence"])
            row.append({"covered": cov, "total": len(its), "pct": round(100 * cov / len(its))})
        cells.append(row)
    return {"columns": [{"assessment_id": a.id, "name": a.name} for a in cols], "rows": domains, "cells": cells}


def missing_evidence(d: Data) -> list[dict]:
    out, seen = [], set()
    for i in d.items:
        if i.applicability != "applicable" or not i.control_id or i.control_id in seen:
            continue
        st = control_state(d, i.control_id)
        if not st["current_evidence"]:
            seen.add(i.control_id)
            c = d.controls[i.control_id]
            reason = "No evidence linked" if st["evidence_count"] == 0 else "Evidence expired, pending review or rejected"
            frameworks = sorted({d.assessments[x.assessment_id].name for x in d.items if x.control_id == c.id})
            out.append({"control_id": c.id, "control_key": c.control_key, "title": c.title, "reason": reason,
                        "frameworks": frameworks, "required_evidence": c.evidence_requirements or suggested_evidence(c.title)})
    return out


def control_graph(d: Data, db, org_id: int) -> dict:
    nodes, edges = {}, []
    for a in d.assessments.values():
        for i in d.items:
            if i.assessment_id != a.id:
                continue
            fc = d.fcs.get(i.framework_control_id)
            if not fc:
                continue
            nodes[f"f{fc.id}"] = {"id": f"f{fc.id}", "type": "framework", "group": a.name, "label": fc.ref_id, "title": fc.title}
            if i.control_id and i.control_id in d.controls:
                c = d.controls[i.control_id]
                st = control_state(d, c.id)
                nodes[f"c{c.id}"] = {"id": f"c{c.id}", "type": "control", "group": "Internal controls", "label": c.control_key,
                                     "title": c.title, "health": st["health"]}
                edges.append({"from": f"c{c.id}", "to": f"f{fc.id}", "kind": "implements"})
    fids = [int(n["id"][1:]) for n in nodes.values() if n["type"] == "framework"]
    if fids:
        best = {}
        for m in db.scalars(select(ControlMapping).where(ControlMapping.from_id.in_(fids), ControlMapping.to_id.in_(fids))):
            if m.org_id in (None, org_id) and (m.org_id or (m.from_id, m.to_id) not in best):
                best[(m.from_id, m.to_id)] = m  # org-specific decision overrides the global template row
        for m in best.values():
            if m.status != "rejected":
                edges.append({"from": f"f{m.from_id}", "to": f"f{m.to_id}", "kind": f"mapping:{m.status}"})
    return {"nodes": list(nodes.values()), "edges": edges}


def simulate(d: Data, body: dict, db, org_id: int) -> dict:
    ov = {"compliant_controls": set(body.get("assume_compliant_controls", [])),
          "renew_expired": bool(body.get("renew_expired_evidence")), "close_findings": bool(body.get("close_findings"))}
    before, after = readiness(d), readiness(d, ov)
    res = {"before": before, "after": after, "delta": round(after["score"] - before["score"], 1),
           "assumptions": {"assume_compliant_controls": sorted(ov["compliant_controls"]),
                           "renew_expired_evidence": ov["renew_expired"], "close_findings": ov["close_findings"]},
           "disclaimer": "Hypothetical projection only. Nothing is saved and no control status is changed."}
    fw = body.get("add_framework_id")
    if fw:
        target = [f.id for f in db.scalars(select(FrameworkControl).where(FrameworkControl.framework_id == int(fw)))]
        compliant_fc = {i.framework_control_id for i in d.items if i.result == "compliant" and i.applicability == "applicable"}
        reused = set()
        if target and compliant_fc:
            for m in db.scalars(select(ControlMapping).where(ControlMapping.status != "rejected")):
                if m.org_id not in (None, org_id):
                    continue
                if m.from_id in target and m.to_id in compliant_fc:
                    reused.add(m.from_id)
                if m.to_id in target and m.from_id in compliant_fc:
                    reused.add(m.to_id)
        res["new_framework"] = {"framework_id": int(fw), "controls": len(target), "with_mapped_compliant_control": len(reused),
                                "note": "Counts controls linked (via unverified template or approved mappings) to a control you already assessed "
                                        "as compliant. Mappings do not prove equivalence; each requirement still needs its own assessment."}
    return res


def calendar_events(d: Data, db, org_id: int) -> list[dict]:
    ev = []

    def add(dt, typ, title, link):
        if dt:
            ev.append({"date": dt.isoformat(), "type": typ, "title": title, "link": link})
    for e in d.evidence.values():
        add(e.valid_until, "evidence_expiry", f"Evidence expires: {e.title}", "evidence")
    for c in d.controls.values():
        add(c.next_review_date, "control_review", f"Control review: {c.control_key}", "controls")
    for p in d.policies:
        add(p.next_review_date, "policy_review", f"Policy review: {p.title}", "policies")
    for t in db.scalars(select(Task).where(Task.org_id == org_id, Task.status.in_(("open", "in_progress")))):
        add(t.due_date, "task_due", f"Task due: {t.title}", "tasks")
    for r in db.scalars(select(EvidenceRequest).where(EvidenceRequest.org_id == org_id, EvidenceRequest.status == "open")):
        add(r.due_date, "evidence_request", f"Evidence request due: {r.title}", "evidence")
    for a in db.scalars(select(AuditEngagement).where(AuditEngagement.org_id == org_id)):
        add(a.start_date, "audit_start", f"Audit starts: {a.name}", "audits")
        add(a.end_date, "audit_end", f"Audit ends: {a.name}", "audits")
    for v in db.scalars(select(Vendor).where(Vendor.org_id == org_id)):
        add(v.next_assessment, "vendor_assessment", f"Vendor assessment due: {v.name}", "vendors")
        add(v.contract_end, "contract_end", f"Contract ends: {v.name}", "vendors")
    for x in d.exceptions:
        if x.status == "approved":
            add(x.expires_on, "exception_expiry", f"Exception expires: {x.title}", "exceptions")
    for f in d.findings:
        if f.status in ("open", "in_remediation"):
            add(f.due_date, "finding_due", f"Finding due: {f.title}", "findings")
    for t in db.scalars(select(TreatmentPlan).where(TreatmentPlan.org_id == org_id, TreatmentPlan.status.in_(("planned", "in_progress")))):
        add(t.due_date, "treatment_due", f"Risk treatment due: {t.action}", "risks")
    ev.sort(key=lambda x: x["date"])
    return ev


def risk_score(l: int, i: int) -> int:
    return l * i


def risk_level(score: int) -> str:
    return "critical" if score >= 20 else "high" if score >= 12 else "medium" if score >= 6 else "low"


def recommendations(d: Data, db, org_id: int) -> list[dict]:
    recs = []
    for e in d.evidence.values():
        s = evidence_status(e, d.today)
        if s == "expired":
            recs.append(("evidence_expired", e.id, "high", f"Renew expired evidence: {e.title}", "Evidence validity period has ended.", "evidence"))
        elif s == "expiring":
            recs.append(("evidence_expiring", e.id, "medium", f"Refresh evidence before expiry: {e.title}", f"Valid until {e.valid_until}.", "evidence"))
        elif s == "pending":
            recs.append(("evidence_review", e.id, "medium", f"Review pending evidence: {e.title}", "Awaiting approval.", "evidence"))
    for c in d.controls.values():
        if not c.owner_id:
            recs.append(("control_owner", c.id, "medium", f"Assign an owner to control {c.control_key}", "Control has no owner.", "controls"))
        if control_state(d, c.id)["health"] == "failing":
            recs.append(("control_failing", c.id, "high", f"Remediate failing control {c.control_key}", "Latest test failed.", "controls"))
    for m in missing_evidence(d)[:25]:
        recs.append(("evidence_missing", m["control_id"], "high", f"Collect evidence for {m['control_key']}: {m['title']}", m["reason"], "controls"))
    for p in d.policies:
        if p.status == "approved" and p.next_review_date and p.next_review_date < d.today:
            recs.append(("policy_overdue", p.id, "medium", f"Policy review overdue: {p.title}", f"Review date {p.next_review_date}.", "policies"))
    for f in d.findings:
        if f.status in ("open", "in_remediation") and f.severity in ("critical", "high") and not f.owner_id:
            recs.append(("finding_unowned", f.id, "high", f"Assign owner to {f.severity} finding: {f.title}", "No owner.", "findings"))
    for r in d.risks:
        if risk_score(r.likelihood, r.impact) >= 12 and r.status == "open" and r.treatment == "mitigate":
            n = db.scalar(select(TreatmentPlan.id).where(TreatmentPlan.risk_id == r.id))
            if not n:
                recs.append(("risk_no_plan", r.id, "high", f"Create a treatment plan for risk: {r.title}", "High risk without treatment plan.", "risks"))
    open_titles = {t.title for t in db.scalars(
        select(Task).where(Task.org_id == org_id, Task.status.in_(("open", "in_progress"))))}
    out = []
    for kind, rid, prio, title, why, link in recs:
        out.append({"key": f"{kind}:{rid}", "kind": kind, "related_id": rid, "priority": prio, "title": title, "reason": why,
                    "link": link, "already_tasked": title in open_titles})
    order = {"high": 0, "medium": 1, "low": 2}
    out.sort(key=lambda r: order[r["priority"]])
    return out


def priority_matrix(d: Data) -> list[dict]:
    items = []
    sev = {"critical": 5, "high": 4, "medium": 3, "low": 1}

    def urgency(due):
        if not due:
            return 2
        days = (due - d.today).days
        return 5 if days < 0 else 4 if days <= 14 else 3 if days <= 45 else 2 if days <= 90 else 1
    for f in d.findings:
        if f.status in ("open", "in_remediation"):
            items.append({"type": "finding", "id": f.id, "title": f.title, "impact": sev.get(f.severity, 3), "urgency": urgency(f.due_date), "detail": f"{f.severity} finding"})
    for r in d.risks:
        if r.status in ("open", "treating"):
            s = risk_score(r.likelihood, r.impact)
            items.append({"type": "risk", "id": r.id, "title": r.title, "impact": max(1, min(5, round(s / 5))), "urgency": 4 if s >= 15 else 3 if s >= 8 else 2, "detail": f"risk score {s}"})
    for c in d.controls.values():
        if control_state(d, c.id)["health"] == "failing":
            items.append({"type": "control", "id": c.id, "title": f"{c.control_key} {c.title}", "impact": 4, "urgency": 4, "detail": "failing control test"})
    return items


def record_snapshots(db, org_id: int, d: Optional[Data] = None) -> None:
    d = d or load(db, org_id)
    today = date.today()
    scopes = {"readiness": readiness(d)}
    for s in assessment_scores(d):
        if s["assessment_score"] is not None:
            scopes[f"assessment:{s['assessment_id']}"] = {"score": s["assessment_score"], "components": s["counts"]}
    for scope, r in scopes.items():
        row = db.scalar(select(ScoreSnapshot).where(ScoreSnapshot.org_id == org_id, ScoreSnapshot.scope == scope, ScoreSnapshot.snap_date == today))
        if row:
            row.score, row.detail = r["score"], r.get("components", {}) if scope != "readiness" else {k: v["value"] for k, v in r["components"].items()}
        else:
            db.add(ScoreSnapshot(org_id=org_id, scope=scope, snap_date=today, score=r["score"],
                                 detail=r.get("components", {}) if scope != "readiness" else {k: v["value"] for k, v in r["components"].items()}))

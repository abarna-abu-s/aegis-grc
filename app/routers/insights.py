"""Dashboard, analytics, alerts/automation, reports, search and AI endpoints."""
from datetime import date, timedelta

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy import func, select

from .. import ai, crud, rag, scoring
from ..models import (AISuggestion, Approval, Assessment, AuditEvent, AutomationRule, AlertRule, Asset, Control, Evidence,
                      Finding, Framework, Notification, Policy, Report, Risk, ScoreSnapshot, Task, Vendor, now)
from ..security import Ctx, audit, get_ctx, rate_limited

router = APIRouter()


# ---------------------------------------------------------------- dashboard
@router.get("/api/dashboard", tags=["insights"])
def dashboard(ctx: Ctx = Depends(get_ctx)):
    d = scoring.load(ctx.db, ctx.org_id)
    scoring.record_snapshots(ctx.db, ctx.org_id, d)
    ctx.db.commit()
    health = {"healthy": 0, "at_risk": 0, "failing": 0, "no_evidence": 0}
    for c in d.controls.values():
        health[scoring.control_state(d, c.id)["health"]] += 1
    ev = {"current": 0, "expiring": 0, "expired": 0, "pending": 0, "rejected": 0}
    for e in d.evidence.values():
        if e.sensitivity == "restricted" and not ctx.can("restricted"):
            continue
        ev[scoring.evidence_status(e, d.today)] += 1
    risks = {"low": 0, "medium": 0, "high": 0, "critical": 0}
    for r in d.risks:
        if r.status != "closed":
            risks[scoring.risk_level(scoring.risk_score(r.likelihood, r.impact))] += 1
    fnd = {"low": 0, "medium": 0, "high": 0, "critical": 0}
    for f in d.findings:
        if f.status in ("open", "in_remediation"):
            fnd[f.severity] += 1
    tasks = ctx.db.scalars(select(Task).where(Task.org_id == ctx.org_id, Task.status.in_(("open", "in_progress")))).all()
    recs = scoring.recommendations(d, ctx.db, ctx.org_id)
    today = d.today
    upcoming = [e for e in scoring.calendar_events(d, ctx.db, ctx.org_id) if today.isoformat() <= e["date"] <= (today + timedelta(days=30)).isoformat()][:8]
    recent = ctx.db.scalars(select(AuditEvent).where(AuditEvent.org_id == ctx.org_id).order_by(AuditEvent.id.desc()).limit(8)).all()
    counts = {k: ctx.db.scalar(select(func.count()).select_from(m).where(m.org_id == ctx.org_id)) for k, m in
              (("controls", Control), ("evidence", Evidence), ("policies", Policy), ("risks", Risk), ("vendors", Vendor), ("assets", Asset),
               ("assessments", Assessment), ("findings", Finding))}
    return {"readiness": scoring.readiness(d), "assessments": scoring.assessment_scores(d), "control_health": health,
            "evidence_status": ev, "risks_by_level": risks, "open_findings": fnd,
            "tasks": {"open": len(tasks), "overdue": sum(1 for t in tasks if t.due_date and t.due_date < today)},
            "pending_approvals": ctx.db.scalar(select(func.count()).select_from(Approval).where(Approval.org_id == ctx.org_id, Approval.status == "pending")),
            "recommendations": recs[:5], "recommendation_count": len(recs), "upcoming": upcoming, "counts": counts,
            "recent_activity": [{"action": e.action, "actor": e.actor_label, "detail": e.detail, "at": e.created_at.isoformat()} for e in recent],
            "unread_notifications": ctx.db.scalar(select(func.count()).select_from(Notification).where(
                Notification.org_id == ctx.org_id, Notification.read.is_(False), (Notification.user_id == ctx.user.id) | Notification.user_id.is_(None)))}


@router.get("/api/insights/scorecards", tags=["insights"])
def scorecards(ctx: Ctx = Depends(get_ctx), days: int = Query(90, le=730)):
    d = scoring.load(ctx.db, ctx.org_id)
    since = date.today() - timedelta(days=days)
    snaps = ctx.db.scalars(select(ScoreSnapshot).where(ScoreSnapshot.org_id == ctx.org_id, ScoreSnapshot.snap_date >= since).order_by(ScoreSnapshot.snap_date)).all()
    series: dict[str, list] = {}
    for s in snaps:
        series.setdefault(s.scope, []).append({"date": s.snap_date.isoformat(), "score": s.score, "demo_seed": bool((s.detail or {}).get("demo_seed"))})
    names = {f"assessment:{a.id}": a.name for a in d.assessments.values()}
    return {"current": scoring.readiness(d), "assessments": scoring.assessment_scores(d), "series": series, "names": names,
            "note": "Trend points are recorded when the dashboard/scorecards are viewed (at most one per day); history builds up over time."}


@router.get("/api/insights/graph", tags=["insights"])
def graph(ctx: Ctx = Depends(get_ctx)):
    return scoring.control_graph(scoring.load(ctx.db, ctx.org_id), ctx.db, ctx.org_id)


@router.post("/api/insights/simulate", tags=["insights"])
def simulate(body: dict = Body(...), ctx: Ctx = Depends(get_ctx)):
    for cid in body.get("assume_compliant_controls", []):
        crud.scoped(ctx, Control, int(cid))
    if body.get("add_framework_id"):
        f = ctx.db.get(Framework, int(body["add_framework_id"]))
        if not f or f.org_id not in (None, ctx.org_id):
            raise HTTPException(404, "Framework not found")
    return scoring.simulate(scoring.load(ctx.db, ctx.org_id), body, ctx.db, ctx.org_id)


@router.get("/api/insights/calendar", tags=["insights"])
def calendar(ctx: Ctx = Depends(get_ctx), start: str = "", end: str = ""):
    ev = scoring.calendar_events(scoring.load(ctx.db, ctx.org_id), ctx.db, ctx.org_id)
    if start:
        ev = [e for e in ev if e["date"] >= start]
    if end:
        ev = [e for e in ev if e["date"] <= end]
    return {"items": ev}


@router.get("/api/insights/recommendations", tags=["insights"])
def recs(ctx: Ctx = Depends(get_ctx)):
    return {"items": scoring.recommendations(scoring.load(ctx.db, ctx.org_id), ctx.db, ctx.org_id)}


@router.post("/api/insights/recommendations/create-task", status_code=201, tags=["insights"])
def rec_to_task(body: dict = Body(...), ctx: Ctx = Depends(get_ctx)):
    ctx.require("contribute")
    key = str(body.get("key", ""))
    items = scoring.recommendations(scoring.load(ctx.db, ctx.org_id), ctx.db, ctx.org_id)
    rec = next((r for r in items if r["key"] == key), None)
    if not rec:
        raise HTTPException(404, "That recommendation no longer applies")
    if rec["already_tasked"]:
        raise HTTPException(409, "A task for this recommendation is already open")
    t = Task(org_id=ctx.org_id, title=rec["title"], description=rec["reason"], priority=rec["priority"], origin="recommendation",
             related_type=rec["kind"], related_id=rec["related_id"], assignee_id=ctx.user.id,
             due_date=date.today() + timedelta(days=7 if rec["priority"] == "high" else 21))
    ctx.db.add(t)
    ctx.db.flush()
    audit(ctx, "task.created_from_recommendation", "task", t.id, rec["title"])
    ctx.db.commit()
    return crud.serialize(t)


@router.get("/api/insights/priority-matrix", tags=["insights"])
def pmatrix(ctx: Ctx = Depends(get_ctx)):
    return {"items": scoring.priority_matrix(scoring.load(ctx.db, ctx.org_id)),
            "method": "Impact: finding severity / risk score / failing control = 4. Urgency: from due date (overdue=5) or risk score."}


# ---------------------------------------------------------------- alerts & automation
def run_alerts(ctx: Ctx) -> list[str]:
    d = scoring.load(ctx.db, ctx.org_id)
    fired = []
    r_now = scoring.readiness(d)["score"]
    for rule in ctx.db.scalars(select(AlertRule).where(AlertRule.org_id == ctx.org_id, AlertRule.enabled.is_(True))):
        m, th, msg = rule.metric, rule.threshold, None
        if m == "evidence_expiring_days":
            n = sum(1 for e in d.evidence.values() if e.valid_until and d.today <= e.valid_until <= d.today + timedelta(days=int(th)))
            msg = f"{n} evidence item(s) expire within {int(th)} days" if n else None
        elif m == "controls_without_evidence":
            n = sum(1 for c in d.controls.values() if scoring.control_state(d, c.id)["health"] == "no_evidence")
            msg = f"{n} control(s) have no evidence" if n > th else None
        elif m == "risk_score_above":
            n = sum(1 for r in d.risks if r.status != "closed" and scoring.risk_score(r.likelihood, r.impact) > th)
            msg = f"{n} open risk(s) score above {int(th)}" if n else None
        elif m == "overdue_tasks":
            n = ctx.db.scalar(select(func.count()).select_from(Task).where(Task.org_id == ctx.org_id, Task.status.in_(("open", "in_progress")), Task.due_date < d.today))
            msg = f"{n} task(s) are overdue" if n > th else None
        elif m == "readiness_below":
            msg = f"Audit-readiness score {r_now} is below {int(th)}" if r_now < th else None
        elif m == "open_critical_findings":
            n = sum(1 for f in d.findings if f.severity == "critical" and f.status in ("open", "in_remediation"))
            msg = f"{n} critical finding(s) open" if n > th else None
        if msg:
            key = f"alert:{rule.id}:{d.today.isoformat()}"
            if not ctx.db.scalar(select(Notification.id).where(Notification.org_id == ctx.org_id, Notification.dedupe_key == key)):
                ctx.db.add(Notification(org_id=ctx.org_id, user_id=None, title=f"Alert: {rule.name}", body=msg, link="dashboard", dedupe_key=key))
                fired.append(msg)
    return fired


def run_automation(ctx: Ctx) -> list[str]:
    d = scoring.load(ctx.db, ctx.org_id)
    made = []

    def add_task(rule, rel_type, rel_id, title, desc, prio="medium", days=7):
        exists = ctx.db.scalar(select(Task.id).where(Task.org_id == ctx.org_id, Task.related_type == rel_type, Task.related_id == rel_id,
                                                     Task.origin == "automation", Task.status.in_(("open", "in_progress"))))
        if exists:
            return
        if rule.action == "notify":
            ctx.db.add(Notification(org_id=ctx.org_id, title=title, body=desc, link="tasks", dedupe_key=f"auto:{rule.id}:{rel_type}:{rel_id}:{d.today}"))
        else:
            ctx.db.add(Task(org_id=ctx.org_id, title=title, description=desc, priority=prio, origin="automation", related_type=rel_type,
                            related_id=rel_id, assignee_id=(rule.params or {}).get("assignee_id"), due_date=d.today + timedelta(days=days)))
        made.append(title)
    for rule in ctx.db.scalars(select(AutomationRule).where(AutomationRule.org_id == ctx.org_id, AutomationRule.enabled.is_(True))):
        t = rule.trigger
        if t == "evidence_expired":
            for e in d.evidence.values():
                if scoring.evidence_status(e, d.today) == "expired":
                    add_task(rule, "evidence", e.id, f"Renew expired evidence: {e.title}", "Created by automation rule: " + rule.name, "high")
        elif t == "risk_high":
            th = (rule.params or {}).get("min_score", 12)
            for r in d.risks:
                if r.status == "open" and scoring.risk_score(r.likelihood, r.impact) >= th:
                    add_task(rule, "risk", r.id, f"Treat high risk: {r.title}", f"Score {scoring.risk_score(r.likelihood, r.impact)}", "high")
        elif t == "finding_critical_unowned":
            for f in d.findings:
                if f.severity == "critical" and f.status == "open" and not f.owner_id:
                    add_task(rule, "finding", f.id, f"Assign owner to critical finding: {f.title}", "No owner assigned", "high", 2)
        elif t == "vendor_assessment_due":
            for v in ctx.db.scalars(select(Vendor).where(Vendor.org_id == ctx.org_id, Vendor.next_assessment < d.today + timedelta(days=14), Vendor.status == "active")):
                add_task(rule, "vendor", v.id, f"Reassess vendor: {v.name}", f"Due {v.next_assessment}")
        elif t == "policy_review_overdue":
            for p in d.policies:
                if p.status == "approved" and p.next_review_date and p.next_review_date < d.today:
                    add_task(rule, "policy", p.id, f"Review overdue policy: {p.title}", f"Review date {p.next_review_date}")
        rule.last_run_at = now()
    return made


@router.post("/api/alerts/run", tags=["insights"])
def alerts_run(ctx: Ctx = Depends(get_ctx)):
    ctx.require("manage")
    fired = run_alerts(ctx)
    audit(ctx, "alerts.evaluated", "alert_rule", None, f"{len(fired)} fired")
    ctx.db.commit()
    return {"fired": fired}


@router.post("/api/automation/run", tags=["insights"])
def automation_run(ctx: Ctx = Depends(get_ctx)):
    ctx.require("manage")
    made = run_automation(ctx)
    audit(ctx, "automation.executed", "automation_rule", None, f"{len(made)} actions")
    ctx.db.commit()
    return {"actions": made}


# ---------------------------------------------------------------- reports
def _table(headers, rows):
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def build_report(ctx: Ctx, kind: str, assessment_id=None) -> tuple[str, str]:
    d = scoring.load(ctx.db, ctx.org_id)
    rd = scoring.readiness(d)
    head = f"_Generated {date.today().isoformat()} from live platform data. Scores are internal indicators, not audit opinions or certifications._\n\n"
    if kind == "risk_summary":
        rows = sorted(((scoring.risk_score(r.likelihood, r.impact), r) for r in d.risks if r.status != "closed"), key=lambda x: -x[0])
        body = ["# Risk summary", head, f"{len(rows)} open risks.", "", _table(["Risk", "Category", "Score", "Level", "Treatment", "Status"],
                [(r.title, r.category, s, scoring.risk_level(s), r.treatment, r.status) for s, r in rows[:25]])]
        return "Risk summary", "\n".join(body)
    if kind == "evidence_status":
        cnt: dict = {}
        for e in d.evidence.values():
            if e.sensitivity == "restricted" and not ctx.can("restricted"):
                continue
            cnt[scoring.evidence_status(e, d.today)] = cnt.get(scoring.evidence_status(e, d.today), 0) + 1
        miss = scoring.missing_evidence(d)
        body = ["# Evidence status", head, _table(["Status", "Count"], sorted(cnt.items())), "", f"## Controls missing current evidence ({len(miss)})",
                _table(["Control", "Title", "Reason", "Frameworks"], [(m["control_key"], m["title"], m["reason"], ", ".join(m["frameworks"])) for m in miss[:40]])]
        return "Evidence status", "\n".join(body)
    if kind == "assessment" and assessment_id:
        a = crud.scoped(ctx, Assessment, assessment_id)
        s = next(x for x in scoring.assessment_scores(d) if x["assessment_id"] == a.id)
        items = [i for i in d.items if i.assessment_id == a.id]
        body = [f"# Assessment report: {a.name}", head, f"Framework: **{a.framework.name} {a.framework.version}** ({a.framework.source_status.replace('_', ' ')}).",
                "", f"Assessment score: **{s['assessment_score']}** | Evidence coverage: **{s['evidence_coverage']}%** | Applicable: {s['applicable']} | Not applicable: {s['not_applicable']}", "",
                _table(["Requirement", "Applicability", "Result", "Linked control"], [(f"{d.fcs[i.framework_control_id].ref_id} {d.fcs[i.framework_control_id].title}", i.applicability, i.result,
                       d.controls[i.control_id].control_key if i.control_id in d.controls else "-") for i in items[:200]])]
        return f"Assessment report: {a.name}", "\n".join(body)
    if kind == "audit_readiness":
        c = rd["components"]
        body = ["# Audit readiness", head, f"**Readiness score: {rd['score']}/100**", "", f"_Formula:_ {rd['formula']}", "",
                _table(["Component", "Value", "Weight", "Contribution"], [(k, v["value"], v["weight"], v["contribution"]) for k, v in c.items()]), "",
                f"Open findings: {sum(1 for f in d.findings if f.status in ('open', 'in_remediation'))}"]
        if rd["insufficient_data"]:
            body.append(f"\nInsufficient data for: {', '.join(rd['insufficient_data'])}.")
        return "Audit readiness", "\n".join(body)
    body = ["# Compliance posture summary", head, f"**Audit-readiness score: {rd['score']}/100**", "",
            _table(["Assessment", "Score", "Evidence coverage", "Applicable"], [(s["name"], s["assessment_score"], s["evidence_coverage"], s["applicable"]) for s in scoring.assessment_scores(d)]), "",
            f"Controls: {len(d.controls)} | Evidence items: {len(d.evidence)} | Open risks: {sum(1 for r in d.risks if r.status != 'closed')} | "
            f"Open findings: {sum(1 for f in d.findings if f.status in ('open', 'in_remediation'))}", "",
            "## Top recommended actions", *[f"- {r['title']} ({r['priority']})" for r in scoring.recommendations(d, ctx.db, ctx.org_id)[:8]]]
    return "Compliance posture summary", "\n".join(body)


def choose_kind(prompt: str, ctx: Ctx):
    p = prompt.lower()
    for a in ctx.db.scalars(select(Assessment).where(Assessment.org_id == ctx.org_id)):
        if any(tok in p for tok in {a.framework.key.lower(), a.framework.name.lower()} | {w for w in a.name.lower().split() if len(w) > 3}):
            return "assessment", a.id
    if "risk" in p:
        return "risk_summary", None
    if "evidence" in p:
        return "evidence_status", None
    if "readiness" in p or "audit" in p:
        return "audit_readiness", None
    return "summary", None


@router.post("/api/reports/generate", status_code=201, tags=["reports"])
def generate_report(body: dict = Body(...), ctx: Ctx = Depends(get_ctx)):
    prompt = str(body.get("prompt", ""))[:500]
    kind, aid = body.get("kind"), body.get("assessment_id")
    if not kind:
        kind, aid = choose_kind(prompt, ctx)
    if kind not in ("summary", "risk_summary", "evidence_status", "audit_readiness", "assessment"):
        raise HTTPException(422, "Unknown report kind")
    if kind == "assessment" and not aid:
        raise HTTPException(422, {"message": "Select an assessment", "errors": {"assessment_id": "Required"}})
    title, content = build_report(ctx, kind, int(aid) if aid else None)
    used_ai = False
    if prompt and ai.settings.ai_enabled:
        s = ai.llm(ai.SYSTEM, "Write a 4-sentence executive summary of this report. Use only the facts below.\n<source n=\"1\" title=\"report\">\n" + content[:6000] + "\n</source>")
        if s:
            content += "\n\n## Executive summary (AI-generated, review before use)\n" + ai._clean_citations(s, 1)
            used_ai = True
    r = Report(org_id=ctx.org_id, title=title, kind=kind, params={"prompt": prompt, "assessment_id": aid}, content=content, generated_by=ctx.user.id, ai_assisted=used_ai)
    ctx.db.add(r)
    ctx.db.flush()
    audit(ctx, "report.generated", "report", r.id, f"{kind}: {prompt}")
    ctx.db.commit()
    return crud.serialize(r)


@router.get("/api/reports", tags=["reports"])
def list_reports(ctx: Ctx = Depends(get_ctx)):
    rows = ctx.db.scalars(select(Report).where(Report.org_id == ctx.org_id).order_by(Report.id.desc()).limit(100)).all()
    return {"items": [{k: v for k, v in crud.serialize(r).items() if k != "content"} for r in rows]}


@router.get("/api/reports/{rid}", tags=["reports"])
def get_report(rid: int, ctx: Ctx = Depends(get_ctx)):
    return crud.serialize(crud.scoped(ctx, Report, rid))


@router.get("/api/reports/{rid}/download", tags=["reports"])
def download_report(rid: int, ctx: Ctx = Depends(get_ctx)):
    r = crud.scoped(ctx, Report, rid)
    return PlainTextResponse(r.content, headers={"Content-Disposition": f'attachment; filename="report-{r.id}.md"'})


@router.delete("/api/reports/{rid}", status_code=204, tags=["reports"])
def delete_report(rid: int, ctx: Ctx = Depends(get_ctx)):
    ctx.require("manage")
    r = crud.scoped(ctx, Report, rid)
    audit(ctx, "report.deleted", "report", rid, r.title)
    ctx.db.delete(r)
    ctx.db.commit()


# ---------------------------------------------------------------- search (keyword + natural-language intents + semantic)
def _g(label, type_, link, items):
    return {"label": label, "type": type_, "link": link, "items": items}


@router.get("/api/search", tags=["search"])
def search(q: str = Query(..., min_length=2, max_length=200), ctx: Ctx = Depends(get_ctx)):
    ql, d = q.lower(), None
    groups, interpreted = [], None
    d = scoring.load(ctx.db, ctx.org_id)
    if "expired" in ql and "evidence" in ql or "expiring" in ql:
        interpreted = "Evidence that is expired or expiring soon"
        items = [{"id": e.id, "title": e.title, "sub": f"{scoring.evidence_status(e, d.today)} - valid until {e.valid_until}"} for e in d.evidence.values()
                 if scoring.evidence_status(e, d.today) in ("expired", "expiring") and (e.sensitivity == "standard" or ctx.can("restricted"))]
        groups.append(_g("Evidence", "evidence", "evidence", items))
    if ("without" in ql or "missing" in ql or "no evidence" in ql) and ("evidence" in ql or "control" in ql):
        interpreted = "Controls without current evidence"
        groups.append(_g("Controls", "control", "controls", [{"id": m["control_id"], "title": f"{m['control_key']} {m['title']}", "sub": m["reason"]} for m in scoring.missing_evidence(d)]))
    if "risk" in ql and any(w in ql for w in ("high", "critical", "top")):
        interpreted = "Open high and critical risks"
        groups.append(_g("Risks", "risk", "risks", [{"id": r.id, "title": r.title, "sub": f"score {scoring.risk_score(r.likelihood, r.impact)}"} for r in d.risks
                                                    if r.status != "closed" and scoring.risk_score(r.likelihood, r.impact) >= 12]))
    if "overdue" in ql and "task" in ql:
        interpreted = "Overdue tasks"
        groups.append(_g("Tasks", "task", "tasks", [{"id": t.id, "title": t.title, "sub": f"due {t.due_date}"} for t in ctx.db.scalars(
            select(Task).where(Task.org_id == ctx.org_id, Task.status.in_(("open", "in_progress")), Task.due_date < d.today))]))
    if "failing" in ql:
        interpreted = "Controls whose latest test failed"
        groups.append(_g("Controls", "control", "controls", [{"id": c.id, "title": f"{c.control_key} {c.title}", "sub": "failing"} for c in d.controls.values() if scoring.control_state(d, c.id)["health"] == "failing"]))
    like = f"%{ql}%"
    spec = [("Controls", "control", "controls", Control, ("control_key", "title", "description"), lambda x: f"{x.control_key} {x.title}"),
            ("Policies", "policy", "policies", Policy, ("title", "body"), lambda x: x.title),
            ("Risks", "risk", "risks", Risk, ("title", "description"), lambda x: x.title),
            ("Vendors", "vendor", "vendors", Vendor, ("name", "service"), lambda x: x.name),
            ("Assets", "asset", "assets", Asset, ("name", "description"), lambda x: x.name),
            ("Tasks", "task", "tasks", Task, ("title", "description"), lambda x: x.title),
            ("Findings", "finding", "findings", Finding, ("title", "description"), lambda x: x.title)]
    for label, type_, link, M, cols, title in spec:
        cond = None
        for c in cols:
            expr = func.lower(getattr(M, c)).like(like)
            cond = expr if cond is None else cond | expr
        rows = ctx.db.scalars(select(M).where(M.org_id == ctx.org_id, cond).limit(8)).all()
        if rows:
            groups.append(_g(label, type_, link, [{"id": r.id, "title": title(r), "sub": ""} for r in rows]))
    ev_rows = ctx.db.scalars(select(Evidence).where(Evidence.org_id == ctx.org_id, func.lower(Evidence.title + Evidence.description).like(like)).limit(8)).all()
    ev_rows = [e for e in ev_rows if e.sensitivity == "standard" or ctx.can("restricted")]
    if ev_rows:
        groups.append(_g("Evidence", "evidence", "evidence", [{"id": e.id, "title": e.title, "sub": e.review_status} for e in ev_rows]))
    fw = ctx.db.scalars(select(Framework).where((Framework.org_id.is_(None)) | (Framework.org_id == ctx.org_id), func.lower(Framework.name + Framework.key).like(like)).limit(6)).all()
    if fw:
        groups.append(_g("Frameworks", "framework", "frameworks", [{"id": f.id, "title": f"{f.name} {f.version}", "sub": f.source_status} for f in fw]))
    passages = rag.search(ctx, q, k=4, min_score=0.2)
    groups = [g for g in groups if g["items"]]
    return {"query": q, "interpreted": interpreted, "groups": groups, "passages": passages}


# ---------------------------------------------------------------- AI
def _ai_gate(ctx: Ctx):
    ctx.require("ai")
    if rate_limited(f"ai:{ctx.user.id}", 30):
        raise HTTPException(429, "AI request rate limit reached; try again in a minute")


@router.get("/api/ai/status", tags=["ai"])
def ai_status(ctx: Ctx = Depends(get_ctx)):
    return ai.provider_status()


@router.post("/api/ai/ask", tags=["ai"])
def ai_ask(body: dict = Body(...), ctx: Ctx = Depends(get_ctx)):
    _ai_gate(ctx)
    q = str(body.get("question", "")).strip()
    if len(q) < 3 or len(q) > 1000:
        raise HTTPException(422, {"message": "Ask a question of 3-1000 characters", "errors": {"question": "Invalid length"}})
    res = ai.answer(ctx, q)
    audit(ctx, "ai.asked", "copilot", None, f"{q[:200]} [{res['mode']}, {len(res['sources'])} sources]")
    ctx.db.commit()
    return res


@router.post("/api/ai/classify-evidence", tags=["ai"])
def ai_classify(body: dict = Body(...), ctx: Ctx = Depends(get_ctx)):
    _ai_gate(ctx)
    from .evidence import get_evidence
    res = ai.classify_evidence(ctx, get_evidence(ctx, int(body.get("evidence_id", 0))))
    ctx.db.commit()
    return res


@router.post("/api/ai/suggest-links", tags=["ai"])
def ai_links(body: dict = Body(...), ctx: Ctx = Depends(get_ctx)):
    _ai_gate(ctx)
    crud.scoped(ctx, Assessment, int(body.get("assessment_id", 0)))
    res = ai.suggest_item_links(ctx, int(body["assessment_id"]))
    ctx.db.commit()
    return res


@router.post("/api/ai/policy-gap", tags=["ai"])
def ai_gap(body: dict = Body(...), ctx: Ctx = Depends(get_ctx)):
    _ai_gate(ctx)
    p = crud.scoped(ctx, Policy, int(body.get("policy_id", 0)))
    crud.scoped(ctx, Assessment, int(body.get("assessment_id", 0)))
    res = ai.policy_gap(ctx, p, int(body["assessment_id"]))
    ctx.db.commit()
    return res


@router.post("/api/ai/risk-summary", tags=["ai"])
def ai_risk(ctx: Ctx = Depends(get_ctx)):
    _ai_gate(ctx)
    return ai.risk_summary(ctx)


@router.post("/api/ai/draft-remediation", tags=["ai"])
def ai_remed(body: dict = Body(...), ctx: Ctx = Depends(get_ctx)):
    _ai_gate(ctx)
    res = ai.draft_remediation(ctx, crud.scoped(ctx, Finding, int(body.get("finding_id", 0))))
    ctx.db.commit()
    return res


@router.post("/api/ai/compare-policies", tags=["ai"])
def ai_compare(body: dict = Body(...), ctx: Ctx = Depends(get_ctx)):
    _ai_gate(ctx)
    return ai.compare_policies(crud.scoped(ctx, Policy, int(body.get("a", 0))), crud.scoped(ctx, Policy, int(body.get("b", 0))))


@router.post("/api/ai/questionnaire", tags=["ai"])
def ai_quest(body: dict = Body(...), ctx: Ctx = Depends(get_ctx)):
    _ai_gate(ctx)
    qs = [l.strip() for l in str(body.get("questions", "")).splitlines() if len(l.strip()) > 5]
    if not qs:
        raise HTTPException(422, {"message": "Enter at least one question (one per line)", "errors": {"questions": "Required"}})
    return ai.questionnaire(ctx, qs)


@router.get("/api/ai/duplicates", tags=["ai"])
def ai_dups(ctx: Ctx = Depends(get_ctx)):
    _ai_gate(ctx)
    return ai.duplicate_evidence(ctx)


@router.get("/api/ai/freshness", tags=["ai"])
def ai_fresh(ctx: Ctx = Depends(get_ctx)):
    return ai.freshness(ctx)


@router.get("/api/ai/suggestions", tags=["ai"])
def ai_list(ctx: Ctx = Depends(get_ctx), status: str = "proposed", kind: str = ""):
    stmt = select(AISuggestion).where(AISuggestion.org_id == ctx.org_id, AISuggestion.status == status)
    if kind:
        stmt = stmt.where(AISuggestion.kind == kind)
    return {"items": [crud.serialize(s) for s in ctx.db.scalars(stmt.order_by(AISuggestion.id.desc()).limit(100))]}


@router.post("/api/ai/suggestions/{sid}/decision", tags=["ai"])
def ai_decide(sid: int, body: dict = Body(...), ctx: Ctx = Depends(get_ctx)):
    s = crud.scoped(ctx, AISuggestion, sid)
    if s.status != "proposed":
        raise HTTPException(409, "Already decided")
    ctx.require("manage" if s.kind != "evidence_link" else "contribute")
    decision = body.get("decision")
    if decision not in ("accepted", "dismissed"):
        raise HTTPException(422, "Decision must be accepted or dismissed")
    msg = "Dismissed"
    if decision == "accepted":
        if body.get("chosen_control_id"):
            s.payload = {**(s.payload or {}), "chosen_control_id": body["chosen_control_id"]}
        try:
            msg = ai.accept_suggestion(ctx, s)
        except ValueError as e:
            raise HTTPException(422, str(e))
    s.status, s.reviewed_by = decision, ctx.user.id
    audit(ctx, f"ai_suggestion.{decision}", s.target_type, s.target_id, f"{s.kind}: {msg}")
    ctx.db.commit()
    return {"status": s.status, "result": msg}

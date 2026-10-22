"""Framework registry, mappings, assessments and the internal control library."""
import csv
import io
from datetime import date, timedelta

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy import func, or_, select

from .. import crud, rag, scoring
from ..frameworks_data import suggested_evidence
from ..models import (Assessment, AssessmentItem, Control, ControlMapping, ControlTest, Evidence, EvidenceControlLink,
                      Framework, FrameworkControl, Policy, PolicyControlLink, Risk, RiskControlLink, User, now)
from ..security import Ctx, audit, get_ctx

router = APIRouter()


def visible_framework(ctx: Ctx, fid: int) -> Framework:
    f = ctx.db.get(Framework, fid)
    if not f or f.org_id not in (None, ctx.org_id):
        raise HTTPException(404, "Framework not found")
    return f


def _fw_out(f: Framework, n: int, adopted: int = 0) -> dict:
    return {"id": f.id, "key": f.key, "name": f.name, "version": f.version, "publisher": f.publisher, "jurisdiction": f.jurisdiction,
            "kind": f.kind, "source_status": f.source_status, "description": f.description, "control_count": n,
            "global_template": f.org_id is None, "adopted_assessments": adopted}


STATUS_LABELS = {"template_unverified": "Template - titles only, not legally verified", "imported": "Imported by your organization",
                 "custom": "Custom framework authored by your organization"}


# ---------------------------------------------------------------- frameworks
@router.get("/api/frameworks", tags=["frameworks"])
def list_frameworks(ctx: Ctx = Depends(get_ctx), q: str = "", kind: str = ""):
    stmt = select(Framework).where(or_(Framework.org_id.is_(None), Framework.org_id == ctx.org_id))
    if q:
        stmt = stmt.where(func.lower(Framework.name + Framework.key).like(f"%{q.lower()}%"))
    if kind:
        stmt = stmt.where(Framework.kind == kind)
    fws = ctx.db.scalars(stmt.order_by(Framework.name)).all()
    counts = dict(ctx.db.execute(select(FrameworkControl.framework_id, func.count()).group_by(FrameworkControl.framework_id)).all())
    adopted = dict(ctx.db.execute(select(Assessment.framework_id, func.count()).where(Assessment.org_id == ctx.org_id).group_by(Assessment.framework_id)).all())
    return {"items": [{**_fw_out(f, counts.get(f.id, 0), adopted.get(f.id, 0)), "status_label": STATUS_LABELS.get(f.source_status, "")} for f in fws]}


@router.get("/api/frameworks/{fid}", tags=["frameworks"])
def get_framework(fid: int, ctx: Ctx = Depends(get_ctx), q: str = ""):
    f = visible_framework(ctx, fid)
    stmt = select(FrameworkControl).where(FrameworkControl.framework_id == fid)
    if q:
        stmt = stmt.where(func.lower(FrameworkControl.ref_id + FrameworkControl.title).like(f"%{q.lower()}%"))
    ctrls = ctx.db.scalars(stmt.order_by(FrameworkControl.id)).all()
    mapped = {}
    ids = [c.id for c in ctrls]
    if ids:
        for m in ctx.db.scalars(select(ControlMapping).where(or_(ControlMapping.from_id.in_(ids), ControlMapping.to_id.in_(ids)),
                                                             or_(ControlMapping.org_id.is_(None), ControlMapping.org_id == ctx.org_id))):
            for k in (m.from_id, m.to_id):
                mapped[k] = mapped.get(k, 0) + 1
    return {**_fw_out(f, len(ctrls)), "status_label": STATUS_LABELS.get(f.source_status, ""),
            "controls": [{"id": c.id, "ref_id": c.ref_id, "title": c.title, "domain": c.domain, "has_requirement_text": bool(c.requirement_text),
                          "requirement_text": c.requirement_text, "evidence_requirements": c.evidence_requirements or suggested_evidence(c.title),
                          "evidence_requirements_are_suggestions": not c.evidence_requirements, "test_procedure": c.test_procedure,
                          "mapping_count": mapped.get(c.id, 0)} for c in ctrls]}


def _add_controls(ctx: Ctx, f: Framework, rows: list[dict]) -> int:
    existing = {c.ref_id: c for c in ctx.db.scalars(select(FrameworkControl).where(FrameworkControl.framework_id == f.id))}
    n = 0
    for r in rows:
        ref, title = str(r.get("ref_id", "")).strip()[:60], str(r.get("title", "")).strip()[:300]
        if not ref or not title:
            continue
        ev = r.get("evidence_requirements") or []
        if isinstance(ev, str):
            ev = [x.strip() for x in ev.split(";") if x.strip()]
        fc = existing.get(ref)
        if fc:
            fc.title, fc.domain = title, str(r.get("domain", fc.domain))[:160]
            fc.requirement_text = str(r.get("requirement_text", fc.requirement_text))[:20000]
            fc.test_procedure = str(r.get("test_procedure", fc.test_procedure))[:5000]
            fc.evidence_requirements = ev or fc.evidence_requirements
        else:
            fc = FrameworkControl(framework_id=f.id, ref_id=ref, title=title, domain=str(r.get("domain", ""))[:160],
                                  requirement_text=str(r.get("requirement_text", ""))[:20000], evidence_requirements=ev,
                                  test_procedure=str(r.get("test_procedure", ""))[:5000])
            ctx.db.add(fc)
            existing[ref] = fc
        ctx.db.flush()
        if fc.requirement_text:
            rag.index_source(ctx.db, ctx.org_id, "framework", fc.id, f"{f.name} {fc.ref_id} {fc.title}", fc.requirement_text,
                             verified=f.source_status == "imported")
        n += 1
    return n


@router.post("/api/frameworks", status_code=201, tags=["frameworks"])
def create_framework(body: dict = Body(...), ctx: Ctx = Depends(get_ctx)):
    ctx.require("manage")
    clean = crud.validate({"key": crud.S(60, True), "name": crud.S(180, True), "version": crud.S(40), "publisher": crud.S(160),
                           "jurisdiction": crud.S(80), "description": crud.T()}, body, ctx)
    if ctx.db.scalar(select(Framework.id).where(Framework.org_id == ctx.org_id, Framework.key == clean["key"], Framework.version == clean.get("version", "1"))):
        raise HTTPException(409, "A framework with this key and version already exists")
    f = Framework(org_id=ctx.org_id, kind="custom", source_status="custom", **clean)
    ctx.db.add(f)
    ctx.db.flush()
    n = _add_controls(ctx, f, body.get("controls", []))
    audit(ctx, "framework.created", "framework", f.id, f"{f.name} with {n} controls")
    ctx.db.commit()
    return _fw_out(f, n)


@router.post("/api/frameworks/{fid}/clone", status_code=201, tags=["frameworks"])
def clone_framework(fid: int, body: dict = Body(default={}), ctx: Ctx = Depends(get_ctx)):
    """Create an editable, versioned org-owned copy of a template (so requirement text can be imported/edited)."""
    ctx.require("manage")
    src = visible_framework(ctx, fid)
    version = str(body.get("version") or f"{src.version}-org")[:40]
    if ctx.db.scalar(select(Framework.id).where(Framework.org_id == ctx.org_id, Framework.key == src.key, Framework.version == version)):
        raise HTTPException(409, "A copy with this version label already exists; choose another version label")
    f = Framework(org_id=ctx.org_id, key=src.key, name=src.name, version=version, publisher=src.publisher, jurisdiction=src.jurisdiction,
                  kind=src.kind, source_status="custom" if src.org_id else "template_unverified", description=src.description)
    ctx.db.add(f)
    ctx.db.flush()
    for c in ctx.db.scalars(select(FrameworkControl).where(FrameworkControl.framework_id == src.id)):
        ctx.db.add(FrameworkControl(framework_id=f.id, ref_id=c.ref_id, title=c.title, domain=c.domain, requirement_text=c.requirement_text,
                                    evidence_requirements=c.evidence_requirements, test_procedure=c.test_procedure))
    audit(ctx, "framework.cloned", "framework", f.id, f"from {src.id} as version {version}")
    ctx.db.commit()
    return _fw_out(f, ctx.db.scalar(select(func.count()).select_from(FrameworkControl).where(FrameworkControl.framework_id == f.id)))


@router.post("/api/frameworks/{fid}/import", tags=["frameworks"])
def import_controls(fid: int, body: dict = Body(...), ctx: Ctx = Depends(get_ctx)):
    """Import control rows from CSV text (ref_id,title,domain,requirement_text,evidence_requirements,test_procedure)."""
    ctx.require("manage")
    f = visible_framework(ctx, fid)
    if f.org_id != ctx.org_id:
        raise HTTPException(409, "Global templates are read-only. Create an editable copy first.")
    text = str(body.get("csv", ""))
    if len(text) > 2_000_000:
        raise HTTPException(413, "CSV too large")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or "ref_id" not in reader.fieldnames or "title" not in reader.fieldnames:
        raise HTTPException(422, {"message": "CSV must have header columns ref_id and title", "errors": {"csv": "Missing ref_id/title header"}})
    n = _add_controls(ctx, f, list(reader))
    if body.get("mark_as_licensed_text") and n:
        f.source_status = "imported"
    audit(ctx, "framework.imported", "framework", f.id, f"{n} rows")
    ctx.db.commit()
    return {"imported": n, "source_status": f.source_status}


@router.delete("/api/frameworks/{fid}", status_code=204, tags=["frameworks"])
def delete_framework(fid: int, ctx: Ctx = Depends(get_ctx)):
    ctx.require("manage")
    f = visible_framework(ctx, fid)
    if f.org_id != ctx.org_id:
        raise HTTPException(409, "Global templates cannot be deleted")
    if ctx.db.scalar(select(Assessment.id).where(Assessment.framework_id == fid)):
        raise HTTPException(409, "Framework is used by an assessment")
    ids = [c.id for c in f.controls]
    if ids:
        for m in ctx.db.scalars(select(ControlMapping).where(or_(ControlMapping.from_id.in_(ids), ControlMapping.to_id.in_(ids)), ControlMapping.org_id == ctx.org_id)):
            ctx.db.delete(m)
    audit(ctx, "framework.deleted", "framework", fid, f.name)
    ctx.db.delete(f)
    ctx.db.commit()


# ---------------------------------------------------------------- mappings
def _map_out(ctx, m: ControlMapping, names: dict) -> dict:
    return {"id": m.id, "from_id": m.from_id, "to_id": m.to_id, "relation": m.relation, "source": m.source, "status": m.status,
            "note": m.note, "org_specific": m.org_id is not None, "from": names.get(m.from_id), "to": names.get(m.to_id)}


@router.get("/api/mappings", tags=["frameworks"])
def list_mappings(framework_control_id: int, ctx: Ctx = Depends(get_ctx)):
    ms = ctx.db.scalars(select(ControlMapping).where(or_(ControlMapping.from_id == framework_control_id, ControlMapping.to_id == framework_control_id),
                                                     or_(ControlMapping.org_id.is_(None), ControlMapping.org_id == ctx.org_id))).all()
    ids = {x for m in ms for x in (m.from_id, m.to_id)}
    names = {}
    for fc, f in ctx.db.execute(select(FrameworkControl, Framework).join(Framework, Framework.id == FrameworkControl.framework_id).where(FrameworkControl.id.in_(ids))):
        names[fc.id] = {"ref_id": fc.ref_id, "title": fc.title, "framework": f.name}
    override = {(m.from_id, m.to_id) for m in ms if m.org_id}
    return {"items": [_map_out(ctx, m, names) for m in ms if m.org_id or (m.from_id, m.to_id) not in override],
            "note": "Template mappings are indicative suggestions, not verified equivalences."}


@router.post("/api/mappings", status_code=201, tags=["frameworks"])
def create_mapping(body: dict = Body(...), ctx: Ctx = Depends(get_ctx)):
    ctx.require("manage")
    clean = crud.validate({"from_id": crud.I(1, req=True), "to_id": crud.I(1, req=True),
                           "relation": crud.E(("equivalent", "related", "partial"), default="related"), "note": crud.S(300)}, body, ctx)
    for k in ("from_id", "to_id"):
        fc = ctx.db.get(FrameworkControl, clean[k])
        if not fc:
            raise HTTPException(404, "Framework control not found")
        visible_framework(ctx, fc.framework_id)
    if clean["from_id"] == clean["to_id"]:
        raise HTTPException(422, "A control cannot map to itself")
    if ctx.db.scalar(select(ControlMapping.id).where(ControlMapping.org_id == ctx.org_id, ControlMapping.from_id == clean["from_id"], ControlMapping.to_id == clean["to_id"])):
        raise HTTPException(409, "Mapping already exists")
    m = ControlMapping(org_id=ctx.org_id, source="manual", status="approved", **clean)
    ctx.db.add(m)
    ctx.db.flush()
    audit(ctx, "mapping.created", "mapping", m.id, f"{m.from_id}->{m.to_id}")
    ctx.db.commit()
    return {"id": m.id}


@router.post("/api/mappings/decide", tags=["frameworks"])
def decide_mapping(body: dict = Body(...), ctx: Ctx = Depends(get_ctx)):
    """Approve/reject a (template) mapping for this organization only; creates an org-specific override row."""
    ctx.require("manage")
    clean = crud.validate({"from_id": crud.I(1, req=True), "to_id": crud.I(1, req=True), "decision": crud.E(("approved", "rejected"), req=True)}, body, ctx)
    row = ctx.db.scalar(select(ControlMapping).where(ControlMapping.org_id == ctx.org_id, ControlMapping.from_id == clean["from_id"], ControlMapping.to_id == clean["to_id"]))
    if not row:
        glob = ctx.db.scalar(select(ControlMapping).where(ControlMapping.org_id.is_(None), ControlMapping.from_id == clean["from_id"], ControlMapping.to_id == clean["to_id"]))
        if not glob:
            raise HTTPException(404, "Mapping not found")
        row = ControlMapping(org_id=ctx.org_id, from_id=glob.from_id, to_id=glob.to_id, relation=glob.relation, source=glob.source)
        ctx.db.add(row)
    row.status = clean["decision"]
    audit(ctx, f"mapping.{clean['decision']}", "mapping", row.id, f"{row.from_id}->{row.to_id}")
    ctx.db.commit()
    return {"status": row.status}


# ---------------------------------------------------------------- assessments
@router.post("/api/assessments", status_code=201, tags=["assessments"])
def create_assessment(body: dict = Body(...), ctx: Ctx = Depends(get_ctx)):
    ctx.require("manage")
    from ..models import Workspace
    clean = crud.validate({"name": crud.S(200, True), "framework_id": crud.I(1, req=True), "workspace_id": crud.FK(Workspace), "due_date": crud.D()}, body, ctx)
    f = visible_framework(ctx, clean["framework_id"])
    ctrls = ctx.db.scalars(select(FrameworkControl).where(FrameworkControl.framework_id == f.id)).all()
    if not ctrls:
        raise HTTPException(422, {"message": "This framework has no controls loaded. Import your licensed control list first.", "errors": {"framework_id": "No controls"}})
    a = Assessment(org_id=ctx.org_id, **clean)
    ctx.db.add(a)
    ctx.db.flush()
    for c in ctrls:
        ctx.db.add(AssessmentItem(org_id=ctx.org_id, assessment_id=a.id, framework_control_id=c.id))
    audit(ctx, "assessment.created", "assessment", a.id, f"{a.name} ({len(ctrls)} items)")
    ctx.db.commit()
    return {"id": a.id, "items": len(ctrls)}


@router.get("/api/assessments", tags=["assessments"])
def list_assessments(ctx: Ctx = Depends(get_ctx)):
    d = scoring.load(ctx.db, ctx.org_id)
    scores = {s["assessment_id"]: s for s in scoring.assessment_scores(d)}
    rows = ctx.db.scalars(select(Assessment).where(Assessment.org_id == ctx.org_id).order_by(Assessment.id.desc())).all()
    return {"items": [{**crud.serialize(a), "framework_name": a.framework.name, "framework_source_status": a.framework.source_status,
                       "score": scores.get(a.id)} for a in rows], "total": len(rows)}


def _assessment(ctx, aid) -> Assessment:
    a = ctx.db.get(Assessment, aid)
    if not a or a.org_id != ctx.org_id:
        raise HTTPException(404, "Not found")
    return a


@router.get("/api/assessments/{aid}", tags=["assessments"])
def get_assessment(aid: int, ctx: Ctx = Depends(get_ctx)):
    a = _assessment(ctx, aid)
    d = scoring.load(ctx.db, ctx.org_id)
    s = next(x for x in scoring.assessment_scores(d) if x["assessment_id"] == aid)
    return {**crud.serialize(a), "framework_name": a.framework.name, "framework_source_status": a.framework.source_status, "score": s,
            "formula": "Score = (compliant + 0.5 x partial) / applicable. Only human reviewers can set results."}


@router.patch("/api/assessments/{aid}", tags=["assessments"])
def update_assessment(aid: int, body: dict = Body(...), ctx: Ctx = Depends(get_ctx)):
    ctx.require("manage")
    a = _assessment(ctx, aid)
    clean = crud.validate({"name": crud.S(200), "status": crud.E(("in_progress", "in_review", "closed")), "due_date": crud.D()}, body, ctx, partial=True)
    for k, v in clean.items():
        setattr(a, k, v)
    audit(ctx, "assessment.updated", "assessment", a.id, ", ".join(clean))
    ctx.db.commit()
    return crud.serialize(a)


@router.delete("/api/assessments/{aid}", status_code=204, tags=["assessments"])
def delete_assessment(aid: int, ctx: Ctx = Depends(get_ctx)):
    ctx.require("manage")
    a = _assessment(ctx, aid)
    audit(ctx, "assessment.deleted", "assessment", a.id, a.name)
    ctx.db.delete(a)
    ctx.db.commit()


@router.get("/api/assessments/{aid}/items", tags=["assessments"])
def assessment_items(aid: int, ctx: Ctx = Depends(get_ctx), page: int = Query(1, ge=1), page_size: int = Query(50, le=200),
                     q: str = "", result: str = "", applicability: str = "", domain: str = "", unlinked: bool = False):
    _assessment(ctx, aid)
    stmt = select(AssessmentItem, FrameworkControl).join(FrameworkControl, FrameworkControl.id == AssessmentItem.framework_control_id).where(AssessmentItem.assessment_id == aid, AssessmentItem.org_id == ctx.org_id)
    if q:
        stmt = stmt.where(func.lower(FrameworkControl.ref_id + FrameworkControl.title).like(f"%{q.lower()}%"))
    if result:
        stmt = stmt.where(AssessmentItem.result == result)
    if applicability:
        stmt = stmt.where(AssessmentItem.applicability == applicability)
    if domain:
        stmt = stmt.where(FrameworkControl.domain == domain)
    if unlinked:
        stmt = stmt.where(AssessmentItem.control_id.is_(None))
    total = ctx.db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = ctx.db.execute(stmt.order_by(AssessmentItem.id).offset((page - 1) * page_size).limit(page_size)).all()
    d = scoring.load(ctx.db, ctx.org_id)
    ctrl_names = {c.id: c for c in d.controls.values()}
    items = []
    for it, fc in rows:
        st = scoring.control_state(d, it.control_id) if it.control_id else None
        c = ctrl_names.get(it.control_id)
        items.append({"id": it.id, "ref_id": fc.ref_id, "title": fc.title, "domain": fc.domain, "requirement_text": fc.requirement_text,
                      "applicability": it.applicability, "justification": it.justification, "result": it.result, "notes": it.notes,
                      "control_id": it.control_id, "control_key": c.control_key if c else None, "control_health": st["health"] if st else None,
                      "evidence_count": st["evidence_count"] if st else 0, "reviewed_at": it.reviewed_at.isoformat() if it.reviewed_at else None,
                      "reviewed_by": it.reviewed_by})
    domains = [x for x in ctx.db.scalars(select(FrameworkControl.domain).join(AssessmentItem, AssessmentItem.framework_control_id == FrameworkControl.id).where(AssessmentItem.assessment_id == aid).distinct())]
    return {"items": items, "total": total, "page": page, "page_size": page_size, "domains": sorted(domains)}


@router.patch("/api/assessment-items/{iid}", tags=["assessments"])
def update_item(iid: int, body: dict = Body(...), ctx: Ctx = Depends(get_ctx)):
    ctx.require("manage")
    it = ctx.db.get(AssessmentItem, iid)
    if not it or it.org_id != ctx.org_id:
        raise HTTPException(404, "Not found")
    clean = crud.validate({"applicability": crud.E(("applicable", "not_applicable")), "justification": crud.T(),
                           "result": crud.E(("not_assessed", "compliant", "partial", "non_compliant")), "notes": crud.T(),
                           "control_id": crud.FK(Control)}, body, ctx, partial=True)
    app_ = clean.get("applicability", it.applicability)
    if app_ == "not_applicable" and not (clean.get("justification", it.justification) or "").strip():
        raise HTTPException(422, {"message": "A justification is required to mark a requirement not applicable",
                                  "errors": {"justification": "Required for not applicable"}})
    if "result" in clean and clean["result"] != it.result:
        cid = clean.get("control_id", it.control_id)
        if clean["result"] == "compliant":
            d = scoring.load(ctx.db, ctx.org_id)
            if not cid or not scoring.control_state(d, cid)["current_evidence"]:
                raise HTTPException(422, {"message": "Cannot mark compliant: the linked control has no approved, unexpired evidence. "
                                                     "Link a control and approve evidence first, or choose partial.",
                                          "errors": {"result": "Evidence required"}})
        it.reviewed_by, it.reviewed_at = ctx.user.id, now()
        audit(ctx, "assessment_item.result", "assessment_item", it.id, f"{it.result} -> {clean['result']}")
    for k, v in clean.items():
        setattr(it, k, v)
    ctx.db.commit()
    return {"id": it.id, "result": it.result, "applicability": it.applicability, "control_id": it.control_id}


@router.post("/api/assessments/{aid}/auto-link", tags=["assessments"])
def auto_link(aid: int, ctx: Ctx = Depends(get_ctx)):
    """Create or reuse internal controls for unlinked requirements. Reuses a control when a mapped requirement
    (template or approved mapping) in another assessment is already linked => cross-framework evidence reuse."""
    ctx.require("manage")
    _assessment(ctx, aid)
    items = ctx.db.scalars(select(AssessmentItem).where(AssessmentItem.assessment_id == aid, AssessmentItem.control_id.is_(None), AssessmentItem.applicability == "applicable")).all()
    linked = {i.framework_control_id: i.control_id for i in ctx.db.scalars(select(AssessmentItem).where(AssessmentItem.org_id == ctx.org_id, AssessmentItem.control_id.is_not(None)))}
    maps = ctx.db.scalars(select(ControlMapping).where(or_(ControlMapping.org_id.is_(None), ControlMapping.org_id == ctx.org_id), ControlMapping.status != "rejected")).all()
    rejected = {(m.from_id, m.to_id) for m in maps if m.org_id and m.status == "rejected"}
    neighbours: dict[int, set[int]] = {}
    for m in maps:
        if (m.from_id, m.to_id) in rejected:
            continue
        neighbours.setdefault(m.from_id, set()).add(m.to_id)
        neighbours.setdefault(m.to_id, set()).add(m.from_id)
    created = reused = 0
    existing_keys = set(ctx.db.scalars(select(Control.control_key).where(Control.org_id == ctx.org_id)))
    for it in items:
        fc = it.framework_control
        cid = next((linked[n] for n in neighbours.get(fc.id, ()) if n in linked), None)
        if cid:
            reused += 1
        else:
            fw_key = fc.framework.key.upper()[:8]
            key = f"{fw_key}-{fc.ref_id}".replace(" ", "")[:40]
            n = 1
            while key in existing_keys:
                n += 1
                key = f"{fw_key}-{fc.ref_id}".replace(" ", "")[:36] + f"-{n}"
            existing_keys.add(key)
            c = Control(org_id=ctx.org_id, control_key=key, title=fc.title, description=fc.requirement_text,
                        evidence_requirements=fc.evidence_requirements or suggested_evidence(fc.title), test_procedure=fc.test_procedure)
            ctx.db.add(c)
            ctx.db.flush()
            cid = c.id
            created += 1
        it.control_id = cid
        linked[fc.id] = cid
    audit(ctx, "assessment.auto_linked", "assessment", aid, f"created {created}, reused {reused}")
    ctx.db.commit()
    return {"created_controls": created, "reused_controls": reused}


# ---------------------------------------------------------------- control library
def _ctrl_enrich(ctx, rows, objs):
    d = scoring.load(ctx.db, ctx.org_id)
    fw = {}
    for it, fc, f in ctx.db.execute(select(AssessmentItem, FrameworkControl, Framework).join(FrameworkControl, FrameworkControl.id == AssessmentItem.framework_control_id)
                                    .join(Framework, Framework.id == FrameworkControl.framework_id).where(AssessmentItem.org_id == ctx.org_id, AssessmentItem.control_id.is_not(None))):
        fw.setdefault(it.control_id, set()).add(f"{f.name} {fc.ref_id}")
    for r in rows:
        st = scoring.control_state(d, r["id"])
        r.update(health=st["health"], evidence_count=st["evidence_count"], frameworks=sorted(fw.get(r["id"], [])), latest_test=st["latest_test"])


def _ctrl_before(ctx, clean):
    if "review_frequency_days" in clean and clean.get("review_frequency_days") and not clean.get("next_review_date"):
        clean["next_review_date"] = date.today() + timedelta(days=clean["review_frequency_days"])


router.include_router(crud.make_router(crud.Res(
    "controls", Control,
    {"control_key": crud.S(40, True), "title": crud.S(200, True), "description": crud.T(), "owner_id": crud.FK(User),
     "implementation_status": crud.E(("not_started", "in_progress", "implemented", "not_applicable")),
     "review_frequency_days": crud.I(7, 3650), "next_review_date": crud.D(), "evidence_requirements": crud.J(), "test_procedure": crud.T()},
    search=("control_key", "title", "description"), filters=("owner_id", "implementation_status"), order="control_key",
    tag="controls", entity="control", enrich=_ctrl_enrich, before_create=_ctrl_before)))


@router.get("/api/controls/{cid}/detail", tags=["controls"])
def control_detail(cid: int, ctx: Ctx = Depends(get_ctx)):
    c = crud.scoped(ctx, Control, cid)
    d = scoring.load(ctx.db, ctx.org_id)
    st = scoring.control_state(d, cid)
    evs = []
    for e in d.ev_by_control.get(cid, []):
        if e.sensitivity == "restricted" and not ctx.can("restricted"):
            evs.append({"id": e.id, "title": "Restricted evidence", "restricted": True})
            continue
        owner = ctx.db.get(User, e.owner_id) if e.owner_id else None
        evs.append({"id": e.id, "title": e.title, "collected_on": e.collected_on.isoformat(), "valid_until": e.valid_until.isoformat() if e.valid_until else None,
                    "review_status": e.review_status, "status": scoring.evidence_status(e, d.today), "owner": owner.full_name if owner else None,
                    "source": e.source, "restricted": False})
    tests = [crud.serialize(t) for t in ctx.db.scalars(select(ControlTest).where(ControlTest.control_id == cid).order_by(ControlTest.tested_at.desc(), ControlTest.id.desc()))]
    reqs = [{"item_id": it.id, "assessment": it.assessment.name, "framework": f.name, "ref_id": fc.ref_id, "title": fc.title, "result": it.result,
             "required_evidence": fc.evidence_requirements or suggested_evidence(fc.title)}
            for it, fc, f in ctx.db.execute(select(AssessmentItem, FrameworkControl, Framework).join(FrameworkControl, FrameworkControl.id == AssessmentItem.framework_control_id)
                                            .join(Framework, Framework.id == FrameworkControl.framework_id).where(AssessmentItem.control_id == cid, AssessmentItem.org_id == ctx.org_id))]
    pols = [{"id": p.id, "title": p.title, "status": p.status} for p in ctx.db.scalars(select(Policy).join(PolicyControlLink, PolicyControlLink.policy_id == Policy.id).where(PolicyControlLink.control_id == cid))]
    risks = [{"id": r.id, "title": r.title, "score": scoring.risk_score(r.likelihood, r.impact)} for r in ctx.db.scalars(select(Risk).join(RiskControlLink, RiskControlLink.risk_id == Risk.id).where(RiskControlLink.control_id == cid))]
    owner = ctx.db.get(User, c.owner_id) if c.owner_id else None
    return {**crud.serialize(c), "owner_name": owner.full_name if owner else None, "health": st["health"],
            "required_evidence": c.evidence_requirements or suggested_evidence(c.title),
            "required_evidence_are_suggestions": not c.evidence_requirements, "evidence": evs, "tests": tests,
            "requirements": reqs, "policies": pols, "risks": risks}


@router.post("/api/controls/{cid}/tests", status_code=201, tags=["controls"])
def add_test(cid: int, body: dict = Body(...), ctx: Ctx = Depends(get_ctx)):
    ctx.require("contribute")
    crud.scoped(ctx, Control, cid)
    clean = crud.validate({"result": crud.E(("pass", "fail", "inconclusive"), True), "notes": crud.T(), "tested_at": crud.D()}, body, ctx)
    t = ControlTest(org_id=ctx.org_id, control_id=cid, tested_by=ctx.user.id, tested_at=clean.get("tested_at") or date.today(), result=clean["result"], notes=clean.get("notes", ""))
    ctx.db.add(t)
    ctx.db.flush()
    audit(ctx, "control.tested", "control", cid, clean["result"])
    ctx.db.commit()
    return crud.serialize(t)


@router.post("/api/controls/{cid}/evidence-links", status_code=201, tags=["controls"])
def link_evidence(cid: int, body: dict = Body(...), ctx: Ctx = Depends(get_ctx)):
    ctx.require("contribute")
    crud.scoped(ctx, Control, cid)
    eid = crud.validate({"evidence_id": crud.FK(Evidence, True)}, body, ctx)["evidence_id"]
    if ctx.db.scalar(select(EvidenceControlLink.id).where(EvidenceControlLink.evidence_id == eid, EvidenceControlLink.control_id == cid)):
        raise HTTPException(409, "Already linked")
    ctx.db.add(EvidenceControlLink(org_id=ctx.org_id, evidence_id=eid, control_id=cid, linked_by=ctx.user.id))
    audit(ctx, "evidence.linked", "control", cid, f"evidence {eid}")
    ctx.db.commit()
    return {"ok": True}


@router.delete("/api/controls/{cid}/evidence-links/{eid}", status_code=204, tags=["controls"])
def unlink_evidence(cid: int, eid: int, ctx: Ctx = Depends(get_ctx)):
    ctx.require("contribute")
    l = ctx.db.scalar(select(EvidenceControlLink).where(EvidenceControlLink.org_id == ctx.org_id, EvidenceControlLink.evidence_id == eid, EvidenceControlLink.control_id == cid))
    if not l:
        raise HTTPException(404, "Not found")
    ctx.db.delete(l)
    audit(ctx, "evidence.unlinked", "control", cid, f"evidence {eid}")
    ctx.db.commit()

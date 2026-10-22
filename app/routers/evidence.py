"""Evidence repository, versions/files, collection requests, policies and source documents."""
import hashlib
import re
import uuid
from datetime import date

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import func, select, update

from .. import crud, rag, scoring
from ..config import settings
from ..models import (Approval, Chunk, Control, Evidence, EvidenceControlLink, EvidenceRequest, EvidenceVersion, Policy,
                      PolicyControlLink, PolicyVersion, SourceDocument, User, now)
from ..security import Ctx, audit, get_ctx

router = APIRouter()

ALLOWED = {".pdf": b"%PDF", ".png": b"\x89PNG", ".jpg": b"\xff\xd8\xff", ".jpeg": b"\xff\xd8\xff", ".docx": b"PK\x03\x04",
           ".xlsx": b"PK\x03\x04", ".txt": None, ".md": None, ".csv": None}


async def read_upload(file: UploadFile, allowed=ALLOWED) -> tuple[str, bytes]:
    name = re.sub(r"[^A-Za-z0-9._ -]", "_", (file.filename or "").split("/")[-1].split("\\")[-1])[:200] or "file"
    ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
    if ext not in allowed:
        raise HTTPException(415, f"File type {ext or '(none)'} is not allowed. Allowed: {', '.join(sorted(allowed))}")
    buf = bytearray()
    while chunk := await file.read(1024 * 1024):
        buf += chunk
        if len(buf) > settings.max_upload_bytes:
            raise HTTPException(413, f"File exceeds the {settings.max_upload_bytes // (1024 * 1024)} MB limit")
    raw = bytes(buf)
    if not raw:
        raise HTTPException(400, "The file is empty")
    magic = allowed[ext]
    if magic and not raw.startswith(magic):
        raise HTTPException(415, "File content does not match its extension")
    if magic is None:
        if b"\x00" in raw[:4096]:
            raise HTTPException(415, "Binary content is not allowed in text files")
        try:
            raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise HTTPException(400, "Text files must be UTF-8 encoded")
    return name, raw


def store(ctx: Ctx, name: str, raw: bytes) -> str:
    folder = settings.upload_dir / str(ctx.org_id)
    folder.mkdir(parents=True, exist_ok=True)
    stored = f"{uuid.uuid4().hex}{('.' + name.rsplit('.', 1)[-1].lower()) if '.' in name else ''}"
    (folder / stored).write_bytes(raw)
    return f"{ctx.org_id}/{stored}"


def visible(ctx: Ctx, e: Evidence) -> bool:
    return e.sensitivity == "standard" or ctx.can("restricted")


def get_evidence(ctx: Ctx, eid: int) -> Evidence:
    e = ctx.db.get(Evidence, eid)
    if not e or e.org_id != ctx.org_id or not visible(ctx, e):
        raise HTTPException(404, "Not found")
    return e


def evidence_out(ctx: Ctx, e: Evidence, d: "scoring.Data") -> dict:
    links = [l.control_id for l in ctx.db.scalars(select(EvidenceControlLink).where(EvidenceControlLink.evidence_id == e.id))]
    owner = ctx.db.get(User, e.owner_id) if e.owner_id else None
    rev = ctx.db.get(User, e.reviewed_by) if e.reviewed_by else None
    last = e.versions[-1] if e.versions else None
    return {**crud.serialize(e), "status": scoring.evidence_status(e, d.today), "owner_name": owner.full_name if owner else None,
            "reviewer_name": rev.full_name if rev else None, "control_ids": links,
            "control_keys": [d.controls[c].control_key for c in links if c in d.controls],
            "version_count": len(e.versions), "latest_version": last.version if last else None,
            "filename": last.filename if last else None, "size": last.size if last else None, "sha256": last.sha256 if last else None,
            "days_until_expiry": (e.valid_until - d.today).days if e.valid_until else None}


@router.get("/api/evidence", tags=["evidence"])
def list_evidence(ctx: Ctx = Depends(get_ctx), page: int = 1, page_size: int = 25, q: str = "", status: str = "", control_id: int = 0,
                  review_status: str = "", sensitivity: str = ""):
    stmt = select(Evidence).where(Evidence.org_id == ctx.org_id)
    if not ctx.can("restricted"):
        stmt = stmt.where(Evidence.sensitivity == "standard")
    if q:
        stmt = stmt.where(func.lower(Evidence.title + Evidence.description).like(f"%{q.lower()}%"))
    if review_status:
        stmt = stmt.where(Evidence.review_status == review_status)
    if sensitivity:
        stmt = stmt.where(Evidence.sensitivity == sensitivity)
    if control_id:
        stmt = stmt.join(EvidenceControlLink, EvidenceControlLink.evidence_id == Evidence.id).where(EvidenceControlLink.control_id == control_id)
    d = scoring.load(ctx.db, ctx.org_id)
    rows = ctx.db.scalars(stmt.order_by(Evidence.id.desc())).all()
    if status:
        rows = [e for e in rows if scoring.evidence_status(e, d.today) == status]
    total = len(rows)
    rows = rows[(page - 1) * page_size: page * page_size]
    return {"items": [evidence_out(ctx, e, d) for e in rows], "total": total, "page": page, "page_size": page_size}


@router.get("/api/evidence/{eid}", tags=["evidence"])
def get_one(eid: int, ctx: Ctx = Depends(get_ctx)):
    e = get_evidence(ctx, eid)
    d = scoring.load(ctx.db, ctx.org_id)
    out = evidence_out(ctx, e, d)
    out["versions"] = [{"version": v.version, "filename": v.filename, "size": v.size, "sha256": v.sha256, "uploaded_at": v.created_at.isoformat(), "uploaded_by": v.uploaded_by} for v in e.versions]
    return out


def _evidence_fields():
    return {"title": crud.S(255, True), "description": crud.T(), "source": crud.S(120), "owner_id": crud.FK(User), "collected_on": crud.D(),
            "valid_until": crud.D(), "sensitivity": crud.E(("standard", "restricted")), "classification": crud.S(80)}


async def _create_version(ctx: Ctx, e: Evidence, file: UploadFile) -> EvidenceVersion:
    name, raw = await read_upload(file)
    sha = hashlib.sha256(raw).hexdigest()
    text = rag.extract_text(name, raw)
    n = (e.versions[-1].version + 1) if e.versions else 1
    v = EvidenceVersion(org_id=ctx.org_id, evidence_id=e.id, version=n, filename=name, stored_name=store(ctx, name, raw),
                        mime=file.content_type or "", size=len(raw), sha256=sha, extracted_text=text[:200000], uploaded_by=ctx.user.id)
    ctx.db.add(v)
    ctx.db.flush()
    e.versions.append(v)
    rag.index_source(ctx.db, ctx.org_id, "evidence", e.id, e.title, f"{e.title}\n\n{e.description}\n\n{text}", n, e.sensitivity)
    return v


@router.post("/api/evidence", status_code=201, tags=["evidence"])
async def create_evidence(title: str = Form(...), description: str = Form(""), source: str = Form("manual upload"), owner_id: str = Form(""),
                          collected_on: str = Form(""), valid_until: str = Form(""), sensitivity: str = Form("standard"),
                          control_ids: str = Form(""), request_id: str = Form(""), file: UploadFile | None = File(None), ctx: Ctx = Depends(get_ctx)):
    ctx.require("contribute")
    raw = {"title": title, "description": description, "source": source, "sensitivity": sensitivity}
    for k, v in (("owner_id", owner_id), ("collected_on", collected_on), ("valid_until", valid_until)):
        if v:
            raw[k] = v
    clean = crud.validate(_evidence_fields(), raw, ctx)
    if clean.get("valid_until") and clean.get("collected_on") and clean["valid_until"] < clean["collected_on"]:
        raise HTTPException(422, {"message": "Validity end cannot be before the collection date", "errors": {"valid_until": "Before collection date"}})
    cids = [int(x) for x in control_ids.split(",") if x.strip().isdigit()]
    for cid in cids:
        crud.scoped(ctx, Control, cid)
    e = Evidence(org_id=ctx.org_id, **clean)
    e.collected_on = clean.get("collected_on") or date.today()
    e.owner_id = clean.get("owner_id") or ctx.user.id
    ctx.db.add(e)
    ctx.db.flush()
    if file is not None and file.filename:
        await _create_version(ctx, e, file)
    for cid in cids:
        ctx.db.add(EvidenceControlLink(org_id=ctx.org_id, evidence_id=e.id, control_id=cid, linked_by=ctx.user.id))
    if request_id.isdigit():
        req = ctx.db.get(EvidenceRequest, int(request_id))
        if req and req.org_id == ctx.org_id and req.status == "open":
            req.status, req.evidence_id = "submitted", e.id
            if req.control_id not in cids:
                ctx.db.add(EvidenceControlLink(org_id=ctx.org_id, evidence_id=e.id, control_id=req.control_id, linked_by=ctx.user.id))
    audit(ctx, "evidence.created", "evidence", e.id, e.title)
    ctx.db.commit()
    return evidence_out(ctx, e, scoring.load(ctx.db, ctx.org_id))


@router.patch("/api/evidence/{eid}", tags=["evidence"])
def update_evidence(eid: int, body: dict = Body(...), ctx: Ctx = Depends(get_ctx)):
    ctx.require("contribute")
    e = get_evidence(ctx, eid)
    clean = crud.validate(_evidence_fields(), body, ctx, partial=True)
    if clean.get("sensitivity") and clean["sensitivity"] != e.sensitivity and not ctx.can("approve"):
        raise HTTPException(403, "Only reviewers can change sensitivity")
    for k, v in clean.items():
        setattr(e, k, v)
    if set(clean) & {"valid_until", "collected_on", "title"}:
        e.review_status = "pending"  # material changes need re-review
    ctx.db.execute(update(Chunk).where(Chunk.org_id == e.org_id, Chunk.source_type == "evidence", Chunk.source_id == e.id)
                   .values(sensitivity=e.sensitivity))
    audit(ctx, "evidence.updated", "evidence", e.id, ", ".join(clean))
    ctx.db.commit()
    return evidence_out(ctx, e, scoring.load(ctx.db, ctx.org_id))


@router.post("/api/evidence/{eid}/versions", status_code=201, tags=["evidence"])
async def new_version(eid: int, file: UploadFile = File(...), ctx: Ctx = Depends(get_ctx)):
    ctx.require("contribute")
    e = get_evidence(ctx, eid)
    v = await _create_version(ctx, e, file)
    e.review_status, e.reviewed_by, e.reviewed_at = "pending", None, None
    audit(ctx, "evidence.version_added", "evidence", e.id, f"v{v.version} sha256={v.sha256[:12]}")
    ctx.db.commit()
    return {"version": v.version, "sha256": v.sha256}


@router.get("/api/evidence/{eid}/versions/{ver}/download", tags=["evidence"])
def download(eid: int, ver: int, ctx: Ctx = Depends(get_ctx)):
    e = get_evidence(ctx, eid)
    v = next((x for x in e.versions if x.version == ver), None)
    if not v:
        raise HTTPException(404, "Not found")
    path = (settings.upload_dir / v.stored_name).resolve()
    if settings.upload_dir.resolve() not in path.parents or not path.exists() or not v.stored_name.startswith(f"{ctx.org_id}/"):
        raise HTTPException(404, "File missing")
    audit(ctx, "evidence.downloaded", "evidence", e.id, f"v{v.version}")
    ctx.db.commit()
    return FileResponse(path, filename=v.filename, media_type="application/octet-stream", headers={"Cache-Control": "private, no-store"})


@router.post("/api/evidence/{eid}/review", tags=["evidence"])
def review_evidence(eid: int, body: dict = Body(...), ctx: Ctx = Depends(get_ctx)):
    ctx.require("approve")
    e = get_evidence(ctx, eid)
    decision = body.get("decision")
    if decision not in ("approved", "rejected"):
        raise HTTPException(422, {"message": "Decision must be approved or rejected", "errors": {"decision": "Invalid"}})
    if decision == "approved" and e.owner_id == ctx.user.id and _other_approver_exists(ctx):
        raise HTTPException(403, "You own this evidence; another reviewer must approve it")
    e.review_status, e.reviewed_by, e.reviewed_at = decision, ctx.user.id, now()
    if decision == "rejected":
        rag.drop_source(ctx.db, ctx.org_id, "evidence", e.id)
    audit(ctx, f"evidence.{decision}", "evidence", e.id, str(body.get("comment", ""))[:500])
    ctx.db.commit()
    return {"review_status": e.review_status}


def _other_approver_exists(ctx: Ctx) -> bool:
    from ..models import Membership
    from ..security import PERMISSIONS
    return ctx.db.scalar(select(func.count()).select_from(Membership).where(Membership.org_id == ctx.org_id, Membership.user_id != ctx.user.id, Membership.role.in_(PERMISSIONS["approve"]))) > 0


@router.delete("/api/evidence/{eid}", status_code=204, tags=["evidence"])
def delete_evidence(eid: int, ctx: Ctx = Depends(get_ctx)):
    ctx.require("approve")
    e = get_evidence(ctx, eid)
    for l in ctx.db.scalars(select(EvidenceControlLink).where(EvidenceControlLink.evidence_id == eid)):
        ctx.db.delete(l)
    for r in ctx.db.scalars(select(EvidenceRequest).where(EvidenceRequest.evidence_id == eid)):
        r.evidence_id = None
    rag.drop_source(ctx.db, ctx.org_id, "evidence", eid)
    audit(ctx, "evidence.deleted", "evidence", eid, e.title)  # file bytes remain on disk for retention; audited
    ctx.db.delete(e)
    ctx.db.commit()


@router.get("/api/evidence-intel/missing", tags=["evidence"])
def missing(ctx: Ctx = Depends(get_ctx)):
    return {"items": scoring.missing_evidence(scoring.load(ctx.db, ctx.org_id))}


@router.get("/api/evidence-intel/heatmap", tags=["evidence"])
def heat(ctx: Ctx = Depends(get_ctx)):
    return scoring.heatmap(scoring.load(ctx.db, ctx.org_id))


# ---------------------------------------------------------------- collection requests
def _req_before(ctx, clean):
    if not clean.get("assignee_id"):
        clean["assignee_id"] = None


def _req_enrich(ctx, rows, objs):
    ids = {r["control_id"] for r in rows}
    names = {c.id: c.control_key for c in ctx.db.scalars(select(Control).where(Control.id.in_(ids)))} if ids else {}
    for r in rows:
        r["control_key"] = names.get(r["control_id"], "")
        r["overdue"] = bool(r["due_date"] and r["status"] == "open" and r["due_date"] < date.today().isoformat())


def _req_create(ctx, clean):
    clean["requested_by"] = ctx.user.id


router.include_router(crud.make_router(crud.Res(
    "evidence-requests", EvidenceRequest,
    {"control_id": crud.FK(Control, True), "title": crud.S(255, True), "instructions": crud.T(), "assignee_id": crud.FK(User), "due_date": crud.D(),
     "status": crud.E(("open", "submitted", "closed", "cancelled"))},
    write_perm="contribute", search=("title",), filters=("status", "control_id", "assignee_id"), order="-id", tag="evidence-requests",
    entity="evidence_request", enrich=_req_enrich, before_create=_req_create)))


# ---------------------------------------------------------------- policies
def _policy_body_changed(ctx, p: Policy, body: str):
    ctx.db.add(PolicyVersion(org_id=ctx.org_id, policy_id=p.id, version=p.version, body=body, changed_by=ctx.user.id))


def _pol_before_update(ctx, p, clean):
    if clean.get("status") in ("approved",) and p.status != "approved":
        raise HTTPException(409, "Policies are approved through the approval workflow (Request approval)")
    if "body" in clean and clean["body"] != p.body:
        _policy_body_changed(ctx, p, p.body)
        p.version += 1
        if p.status == "approved":
            clean["status"] = "draft"  # edits to an approved policy require a new approval
            p.approved_by = p.approved_at = None
        rag.drop_source(ctx.db, ctx.org_id, "policy", p.id)


def _pol_after_update(ctx, p):
    if p.status == "retired":
        rag.drop_source(ctx.db, ctx.org_id, "policy", p.id)


def _pol_before_create(ctx, clean):
    if clean.get("status", "draft") not in ("draft",):
        clean["status"] = "draft"


def _pol_after_create(ctx, p):
    _policy_body_changed(ctx, p, p.body)


def _pol_enrich(ctx, rows, objs):
    links = {}
    for l in ctx.db.scalars(select(PolicyControlLink).where(PolicyControlLink.org_id == ctx.org_id)):
        links.setdefault(l.policy_id, []).append(l.control_id)
    for r in rows:
        r["control_ids"] = links.get(r["id"], [])
        r["review_overdue"] = bool(r["next_review_date"] and r["status"] == "approved" and r["next_review_date"] < date.today().isoformat())


router.include_router(crud.make_router(crud.Res(
    "policies", Policy,
    {"title": crud.S(255, True), "category": crud.E(("policy", "standard", "procedure")), "body": crud.T(), "owner_id": crud.FK(User),
     "status": crud.E(("draft", "in_review", "approved", "retired")), "next_review_date": crud.D()},
    search=("title", "body"), filters=("status", "category", "owner_id"), order="title", tag="policies", entity="policy",
    enrich=_pol_enrich, before_create=_pol_before_create, after_create=_pol_after_create, before_update=_pol_before_update,
    after_update=_pol_after_update)))


@router.get("/api/policies/{pid}/versions", tags=["policies"])
def policy_versions(pid: int, ctx: Ctx = Depends(get_ctx)):
    crud.scoped(ctx, Policy, pid)
    rows = ctx.db.scalars(select(PolicyVersion).where(PolicyVersion.policy_id == pid, PolicyVersion.org_id == ctx.org_id).order_by(PolicyVersion.version.desc())).all()
    return {"items": [crud.serialize(v) for v in rows]}


@router.put("/api/policies/{pid}/controls", tags=["policies"])
def set_policy_controls(pid: int, body: dict = Body(...), ctx: Ctx = Depends(get_ctx)):
    ctx.require("manage")
    crud.scoped(ctx, Policy, pid)
    ids = {int(i) for i in body.get("control_ids", [])}
    for i in ids:
        crud.scoped(ctx, Control, i)
    for l in ctx.db.scalars(select(PolicyControlLink).where(PolicyControlLink.policy_id == pid)):
        ctx.db.delete(l)
    ctx.db.flush()
    for i in ids:
        ctx.db.add(PolicyControlLink(org_id=ctx.org_id, policy_id=pid, control_id=i))
    audit(ctx, "policy.controls_linked", "policy", pid, f"{len(ids)} controls")
    ctx.db.commit()
    return {"control_ids": sorted(ids)}


@router.get("/api/policy-traceability", tags=["policies"])
def traceability(ctx: Ctx = Depends(get_ctx)):
    """Policy -> control -> framework requirement chain."""
    from ..models import AssessmentItem, Framework, FrameworkControl
    pols = ctx.db.scalars(select(Policy).where(Policy.org_id == ctx.org_id)).all()
    out = []
    for p in pols:
        ctrls = ctx.db.scalars(select(Control).join(PolicyControlLink, PolicyControlLink.control_id == Control.id).where(PolicyControlLink.policy_id == p.id)).all()
        chain = []
        for c in ctrls:
            reqs = [f"{f.name} {fc.ref_id}" for fc, f in ctx.db.execute(select(FrameworkControl, Framework).join(AssessmentItem, AssessmentItem.framework_control_id == FrameworkControl.id)
                                                                         .join(Framework, Framework.id == FrameworkControl.framework_id).where(AssessmentItem.control_id == c.id, AssessmentItem.org_id == ctx.org_id))]
            chain.append({"control_id": c.id, "control_key": c.control_key, "title": c.title, "requirements": sorted(set(reqs))})
        out.append({"policy_id": p.id, "title": p.title, "status": p.status, "controls": chain})
    return {"items": out}


# ---------------------------------------------------------------- source documents (reference material for retrieval)
SRC_ALLOWED = {k: v for k, v in ALLOWED.items() if k in (".pdf", ".docx", ".txt", ".md", ".csv")}


@router.get("/api/documents", tags=["documents"])
def list_documents(ctx: Ctx = Depends(get_ctx)):
    q = select(SourceDocument).where(SourceDocument.org_id == ctx.org_id)
    if not ctx.can("restricted"):
        q = q.where(SourceDocument.sensitivity == "standard")
    rows = ctx.db.scalars(q.order_by(SourceDocument.id.desc())).all()
    return {"items": [{"id": r.id, "title": r.title, "filename": r.filename, "sensitivity": r.sensitivity, "characters": len(r.text),
                       "indexed": bool(r.text), "created_at": r.created_at.isoformat()} for r in rows]}


@router.post("/api/documents", status_code=201, tags=["documents"])
async def upload_document(title: str = Form(""), sensitivity: str = Form("standard"), file: UploadFile = File(...), ctx: Ctx = Depends(get_ctx)):
    ctx.require("manage")
    if sensitivity not in ("standard", "restricted"):
        raise HTTPException(422, "Invalid sensitivity")
    name, raw = await read_upload(file, SRC_ALLOWED)
    text = rag.extract_text(name, raw)
    d = SourceDocument(org_id=ctx.org_id, title=(title.strip() or name)[:255], filename=name, stored_name=store(ctx, name, raw),
                       sha256=hashlib.sha256(raw).hexdigest(), text=text[:500000], sensitivity=sensitivity, uploaded_by=ctx.user.id)
    ctx.db.add(d)
    ctx.db.flush()
    n = rag.index_source(ctx.db, ctx.org_id, "document", d.id, d.title, text, 1, sensitivity)
    audit(ctx, "document.uploaded", "document", d.id, f"{name}, {n} chunks")
    ctx.db.commit()
    return {"id": d.id, "title": d.title, "chunks": n, "warning": None if text else "No text could be extracted (scanned PDFs/images need OCR, which is not configured)."}


@router.delete("/api/documents/{did}", status_code=204, tags=["documents"])
def delete_document(did: int, ctx: Ctx = Depends(get_ctx)):
    ctx.require("manage")
    d = crud.scoped(ctx, SourceDocument, did)
    rag.drop_source(ctx.db, ctx.org_id, "document", did)
    audit(ctx, "document.deleted", "document", did, d.title)
    ctx.db.delete(d)
    ctx.db.commit()


# ---------------------------------------------------------------- approvals
@router.post("/api/approvals/request", status_code=201, tags=["approvals"])
def request_approval(body: dict = Body(...), ctx: Ctx = Depends(get_ctx)):
    ctx.require("contribute")
    from ..models import ExceptionRecord
    clean = crud.validate({"entity_type": crud.E(("policy", "evidence", "exception"), True), "entity_id": crud.I(1, req=True),
                           "approver_id": crud.FK(User), "comment": crud.T()}, body, ctx)
    model = {"policy": Policy, "evidence": Evidence, "exception": ExceptionRecord}[clean["entity_type"]]
    obj = crud.scoped(ctx, model, clean["entity_id"])
    if ctx.db.scalar(select(Approval.id).where(Approval.org_id == ctx.org_id, Approval.entity_type == clean["entity_type"], Approval.entity_id == obj.id, Approval.status == "pending")):
        raise HTTPException(409, "An approval is already pending for this record")
    title = getattr(obj, "title", "")
    a = Approval(org_id=ctx.org_id, requested_by=ctx.user.id, title=title, **clean)
    ctx.db.add(a)
    if clean["entity_type"] == "policy":
        obj.status = "in_review"
    ctx.db.flush()
    audit(ctx, "approval.requested", clean["entity_type"], obj.id, title)
    ctx.db.commit()
    return crud.serialize(a)


@router.get("/api/approvals", tags=["approvals"])
def list_approvals(ctx: Ctx = Depends(get_ctx), status: str = "", mine: bool = False):
    stmt = select(Approval).where(Approval.org_id == ctx.org_id)
    if status:
        stmt = stmt.where(Approval.status == status)
    if mine:
        stmt = stmt.where((Approval.approver_id == ctx.user.id) | Approval.approver_id.is_(None))
    rows = ctx.db.scalars(stmt.order_by(Approval.id.desc()).limit(200)).all()
    return {"items": crud.with_user_names(ctx.db, [crud.serialize(a) for a in rows]), "total": len(rows)}


@router.post("/api/approvals/{aid}/decision", tags=["approvals"])
def decide(aid: int, body: dict = Body(...), ctx: Ctx = Depends(get_ctx)):
    ctx.require("approve")
    from ..models import ExceptionRecord
    a = crud.scoped(ctx, Approval, aid)
    if a.status != "pending":
        raise HTTPException(409, "This request has already been decided")
    decision = body.get("decision")
    if decision not in ("approved", "rejected"):
        raise HTTPException(422, {"message": "Decision must be approved or rejected", "errors": {"decision": "Invalid"}})
    if a.approver_id and a.approver_id != ctx.user.id:
        raise HTTPException(403, "This request is assigned to another approver")
    if a.requested_by == ctx.user.id and _other_approver_exists(ctx):
        raise HTTPException(403, "You cannot approve your own request; another approver is available")
    model = {"policy": Policy, "evidence": Evidence, "exception": ExceptionRecord}[a.entity_type]
    obj = ctx.db.get(model, a.entity_id)
    if not obj or obj.org_id != ctx.org_id:
        raise HTTPException(404, "The record no longer exists")
    comment = str(body.get("comment", ""))[:1000]
    if decision == "approved":
        if a.entity_type == "policy":
            obj.status, obj.approved_by, obj.approved_at = "approved", ctx.user.id, now()
            if not obj.next_review_date:
                obj.next_review_date = date.today().replace(year=date.today().year + 1) if not (date.today().month == 2 and date.today().day == 29) else date.today().replace(year=date.today().year + 1, day=28)
            rag.index_source(ctx.db, ctx.org_id, "policy", obj.id, obj.title, obj.body, obj.version)
        elif a.entity_type == "evidence":
            obj.review_status, obj.reviewed_by, obj.reviewed_at = "approved", ctx.user.id, now()
        else:
            obj.status, obj.approved_by = "approved", ctx.user.id
    else:
        if a.entity_type == "policy":
            obj.status = "draft"
        elif a.entity_type == "evidence":
            obj.review_status = "rejected"
        else:
            obj.status = "rejected"
    a.status, a.decided_by, a.decided_at, a.comment = decision, ctx.user.id, now(), comment
    audit(ctx, f"approval.{decision}", a.entity_type, a.entity_id, comment)
    ctx.db.commit()
    return crud.serialize(a)

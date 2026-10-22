"""Auth, organizations, members, workspaces, notifications, audit trail."""
from datetime import timedelta

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import crud
from ..config import settings
from ..db import get_db
from ..models import AuditEvent, Membership, Notification, Organization, User, Workspace, now
from ..security import (ROLES, Ctx, audit, create_token, get_ctx, hash_password, password_problem, perm, rate_limited,
                        verify_chain, verify_password)

router = APIRouter()
_DUMMY_HASH = hash_password("Dummy-password-1")  # constant-time-ish login for unknown emails


class RegisterIn(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=2, max_length=120)
    password: str = Field(min_length=10, max_length=128)
    organization_name: str = Field(min_length=2, max_length=160)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(max_length=128)


def _me(db: Session, user: User) -> dict:
    ms = db.execute(select(Membership, Organization).join(Organization, Organization.id == Membership.org_id)
                    .where(Membership.user_id == user.id).order_by(Membership.id)).all()
    return {"id": user.id, "email": user.email, "full_name": user.full_name,
            "memberships": [{"org_id": o.id, "org_name": o.name, "role": m.role, "is_demo": o.is_demo} for m, o in ms]}


def _throttle(request: Request):
    ip = request.client.host if request.client else "?"
    if rate_limited(f"auth:{ip}", settings.auth_rate_limit_per_minute):
        raise HTTPException(429, "Too many attempts. Please wait a minute and try again.")


@router.post("/api/auth/register", status_code=201, tags=["auth"])
def register(data: RegisterIn, request: Request, db: Session = Depends(get_db)):
    _throttle(request)
    if not settings.allow_registration:
        raise HTTPException(403, "Self-registration is disabled")
    problem = password_problem(data.password)
    if problem:
        raise HTTPException(422, {"message": problem, "errors": {"password": problem}})
    if db.scalar(select(User.id).where(User.email == data.email.lower())):
        raise HTTPException(409, "An account with this email already exists")
    org = Organization(name=data.organization_name.strip())
    user = User(email=data.email.lower(), full_name=data.full_name.strip(), password_hash=hash_password(data.password))
    db.add_all([org, user])
    db.flush()
    db.add(Membership(org_id=org.id, user_id=user.id, role="owner"))
    audit(db, "auth.registered", "user", user.id, f"organization {org.name}", org_id=org.id, actor=user, ip=request.client.host if request.client else "")
    db.commit()
    return {"access_token": create_token(user), "token_type": "bearer", "user": _me(db, user)}


@router.post("/api/auth/login", tags=["auth"])
def login(data: LoginIn, request: Request, db: Session = Depends(get_db)):
    _throttle(request)
    user = db.scalar(select(User).where(User.email == data.email.lower()))
    if user and user.locked_until and user.locked_until > now():
        raise HTTPException(423, "Account temporarily locked after repeated failed sign-ins. Try again later.")
    ok = verify_password(data.password, user.password_hash if user else _DUMMY_HASH)
    if not user or not ok or not user.is_active:
        if user:
            user.failed_logins += 1
            if user.failed_logins >= 5:
                user.locked_until, user.failed_logins = now() + timedelta(minutes=15), 0
            db.commit()
        raise HTTPException(401, "Incorrect email or password")
    user.failed_logins, user.locked_until = 0, None
    m = db.scalar(select(Membership).where(Membership.user_id == user.id))
    if m:
        audit(db, "auth.login", "user", user.id, "", org_id=m.org_id, actor=user, ip=request.client.host if request.client else "")
    db.commit()
    return {"access_token": create_token(user), "token_type": "bearer", "user": _me(db, user)}


@router.post("/api/auth/logout", tags=["auth"])
def logout(ctx: Ctx = Depends(get_ctx)):
    ctx.user.token_version += 1  # invalidates every token issued before now
    audit(ctx, "auth.logout", "user", ctx.user.id)
    ctx.db.commit()
    return {"ok": True}


@router.get("/api/auth/me", tags=["auth"])
def me(ctx: Ctx = Depends(get_ctx)):
    return {**_me(ctx.db, ctx.user), "active_org_id": ctx.org_id, "role": ctx.role}


@router.post("/api/auth/change-password", tags=["auth"])
def change_password(body: dict = Body(...), ctx: Ctx = Depends(get_ctx)):
    cur, new = str(body.get("current_password", "")), str(body.get("new_password", ""))
    if not verify_password(cur, ctx.user.password_hash):
        raise HTTPException(403, "Current password is incorrect")
    problem = password_problem(new)
    if problem:
        raise HTTPException(422, {"message": problem, "errors": {"new_password": problem}})
    ctx.user.password_hash = hash_password(new)
    ctx.user.token_version += 1
    audit(ctx, "auth.password_changed", "user", ctx.user.id)
    ctx.db.commit()
    return {"access_token": create_token(ctx.user)}


# ---------------------------------------------------------------- organization & members
@router.get("/api/org", tags=["org"])
def org_info(ctx: Ctx = Depends(get_ctx)):
    o = ctx.db.get(Organization, ctx.org_id)
    return {"id": o.id, "name": o.name, "industry": o.industry, "is_demo": o.is_demo, "role": ctx.role}


@router.patch("/api/org", tags=["org"])
def org_update(body: dict = Body(...), ctx: Ctx = Depends(perm("admin"))):
    clean = crud.validate({"name": crud.S(160), "industry": crud.S(120)}, body, ctx, partial=True)
    o = ctx.db.get(Organization, ctx.org_id)
    for k, v in clean.items():
        setattr(o, k, v)
    audit(ctx, "org.updated", "organization", o.id, ", ".join(clean))
    ctx.db.commit()
    return {"id": o.id, "name": o.name, "industry": o.industry}


@router.get("/api/members", tags=["org"])
def members(ctx: Ctx = Depends(get_ctx)):
    rows = ctx.db.execute(select(Membership, User).join(User, User.id == Membership.user_id).where(Membership.org_id == ctx.org_id)).all()
    return [{"id": m.id, "user_id": u.id, "email": u.email, "full_name": u.full_name, "role": m.role} for m, u in rows]


@router.post("/api/members", status_code=201, tags=["org"])
def add_member(body: dict = Body(...), ctx: Ctx = Depends(perm("admin"))):
    email, role = str(body.get("email", "")).lower().strip(), body.get("role", "viewer")
    if role not in ROLES or (role == "owner" and ctx.role != "owner"):
        raise HTTPException(422, {"message": "Invalid role", "errors": {"role": "Invalid role"}})
    if "@" not in email:
        raise HTTPException(422, {"message": "Invalid email", "errors": {"email": "Enter a valid email"}})
    user = ctx.db.scalar(select(User).where(User.email == email))
    if not user:
        pw = str(body.get("temporary_password", ""))
        problem = password_problem(pw)
        if problem:
            raise HTTPException(422, {"message": problem, "errors": {"temporary_password": problem}})
        user = User(email=email, full_name=str(body.get("full_name") or email.split("@")[0])[:120], password_hash=hash_password(pw))
        ctx.db.add(user)
        ctx.db.flush()
    if ctx.db.scalar(select(Membership.id).where(Membership.org_id == ctx.org_id, Membership.user_id == user.id)):
        raise HTTPException(409, "Already a member")
    ctx.db.add(Membership(org_id=ctx.org_id, user_id=user.id, role=role))
    audit(ctx, "member.added", "user", user.id, f"{email} as {role}")
    ctx.db.commit()
    return {"user_id": user.id, "email": email, "role": role}


def _owners(ctx: Ctx) -> int:
    return ctx.db.scalar(select(func.count()).select_from(Membership).where(Membership.org_id == ctx.org_id, Membership.role == "owner"))


@router.patch("/api/members/{mid}", tags=["org"])
def change_role(mid: int, body: dict = Body(...), ctx: Ctx = Depends(perm("admin"))):
    m = ctx.db.get(Membership, mid)
    if not m or m.org_id != ctx.org_id:
        raise HTTPException(404, "Not found")
    role = body.get("role")
    if role not in ROLES or ((role == "owner" or m.role == "owner") and ctx.role != "owner"):
        raise HTTPException(403, "Not permitted to assign that role")
    if m.role == "owner" and role != "owner" and _owners(ctx) <= 1:
        raise HTTPException(409, "An organization must keep at least one owner")
    old, m.role = m.role, role
    audit(ctx, "member.role_changed", "user", m.user_id, f"{old} -> {role}")
    ctx.db.commit()
    return {"id": m.id, "role": m.role}


@router.delete("/api/members/{mid}", status_code=204, tags=["org"])
def remove_member(mid: int, ctx: Ctx = Depends(perm("admin"))):
    m = ctx.db.get(Membership, mid)
    if not m or m.org_id != ctx.org_id:
        raise HTTPException(404, "Not found")
    if m.role == "owner" and (ctx.role != "owner" or _owners(ctx) <= 1):
        raise HTTPException(409, "Cannot remove the last owner")
    audit(ctx, "member.removed", "user", m.user_id)
    ctx.db.delete(m)
    ctx.db.commit()


# ---------------------------------------------------------------- notifications
@router.get("/api/notifications", tags=["notifications"])
def notifications(ctx: Ctx = Depends(get_ctx), unread_only: bool = False, limit: int = Query(30, le=100)):
    q = select(Notification).where(Notification.org_id == ctx.org_id, (Notification.user_id == ctx.user.id) | Notification.user_id.is_(None))
    if unread_only:
        q = q.where(Notification.read.is_(False))
    rows = ctx.db.scalars(q.order_by(Notification.id.desc()).limit(limit)).all()
    return {"items": [crud.serialize(n) for n in rows], "unread": sum(1 for n in rows if not n.read)}


@router.post("/api/notifications/read-all", tags=["notifications"])
def read_all(ctx: Ctx = Depends(get_ctx)):
    for n in ctx.db.scalars(select(Notification).where(Notification.org_id == ctx.org_id, Notification.read.is_(False),
                                                       (Notification.user_id == ctx.user.id) | Notification.user_id.is_(None))):
        n.read = True
    ctx.db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- audit trail
@router.get("/api/audit-trail", tags=["audit-trail"])
def audit_trail(ctx: Ctx = Depends(get_ctx), page: int = Query(1, ge=1), page_size: int = Query(50, le=200), action: str = "", q: str = ""):
    ctx.require("audit")
    stmt = select(AuditEvent).where(AuditEvent.org_id == ctx.org_id)
    if action:
        stmt = stmt.where(AuditEvent.action.like(f"{action}%"))
    if q:
        stmt = stmt.where(func.lower(AuditEvent.detail + AuditEvent.actor_label).like(f"%{q.lower()}%"))
    total = ctx.db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = ctx.db.scalars(stmt.order_by(AuditEvent.id.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return {"items": [crud.serialize(e) for e in rows], "total": total, "page": page, "page_size": page_size}


@router.get("/api/audit-trail/verify", tags=["audit-trail"])
def audit_verify(ctx: Ctx = Depends(get_ctx)):
    ctx.require("audit")
    return {**verify_chain(ctx.db, ctx.org_id),
            "note": "Hash chain makes edits made through the application detectable. It is tamper-evident, not tamper-proof: "
                    "someone with direct database write access could rewrite the whole chain. Ship logs to an external immutable store for stronger guarantees."}


# ---------------------------------------------------------------- workspaces
from ..models import Framework  # noqa: E402


def _ws_enrich(ctx, rows, objs):
    ids = {r["framework_id"] for r in rows if r.get("framework_id")}
    names = {f.id: f.name for f in ctx.db.scalars(select(Framework).where(Framework.id.in_(ids)))} if ids else {}
    for r in rows:
        r["framework_name"] = names.get(r.get("framework_id"), "")


router.include_router(crud.make_router(crud.Res(
    "workspaces", Workspace, {"name": crud.S(160, True), "description": crud.T(), "framework_id": crud.I(1)},
    search=("name",), tag="workspaces", entity="workspace", enrich=_ws_enrich, before_create=lambda c, cl: _ws_check(c, cl),
    before_update=lambda c, o, cl: _ws_check(c, cl))))


def _ws_check(ctx, clean):
    fid = clean.get("framework_id")
    if fid:
        f = ctx.db.get(Framework, fid)
        if not f or f.org_id not in (None, ctx.org_id):
            raise HTTPException(422, {"message": "Validation failed", "errors": {"framework_id": "Framework not found"}})

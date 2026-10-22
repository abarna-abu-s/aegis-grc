"""Authentication, RBAC, tenant context, rate limiting, audit trail."""
import hashlib
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .db import get_db
from .models import AuditEvent, Membership, User, now

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)

ROLES = ("owner", "admin", "compliance_manager", "control_owner", "auditor", "viewer")
_MANAGE = {"owner", "admin", "compliance_manager"}
# permission -> roles allowed. "read" is implicit for every member (tenant-scoped).
PERMISSIONS = {
    "admin": {"owner", "admin"},
    "manage": _MANAGE,                                   # frameworks, assessments, policies, risk, vendors...
    "approve": _MANAGE,                                  # approvals, evidence review, policy approval
    "contribute": _MANAGE | {"control_owner"},           # evidence, tasks, tests, requests
    "audit": _MANAGE | {"auditor"},                      # audit engagements / findings
    "restricted": _MANAGE | {"auditor"},                 # read evidence flagged restricted
    "ai": _MANAGE | {"control_owner", "auditor"},
}


def hash_password(p: str) -> str:
    return pwd_context.hash(p)


def verify_password(p: str, h: str) -> bool:
    return pwd_context.verify(p, h)


def create_token(user: User) -> str:
    exp = datetime.now(timezone.utc) + timedelta(minutes=settings.token_minutes)
    return jwt.encode({"sub": str(user.id), "tv": user.token_version, "exp": exp},
                      settings.jwt_secret, algorithm=settings.jwt_algorithm)


def password_problem(p: str) -> Optional[str]:
    if len(p) < 10:
        return "Password must be at least 10 characters"
    if p.lower() == p or p.upper() == p or not any(c.isdigit() for c in p):
        return "Password must mix upper/lower case letters and include a digit"
    return None


@dataclass
class Ctx:
    db: Session
    user: User
    org_id: int
    role: str
    request: Request

    def can(self, perm: str) -> bool:
        return self.role in PERMISSIONS[perm]

    def require(self, perm: str) -> None:
        if not self.can(perm):
            raise HTTPException(403, f"Your role ({self.role}) does not allow this action")

    @property
    def ip(self) -> str:
        return self.request.client.host if self.request.client else ""


def get_ctx(request: Request, token: Optional[str] = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> Ctx:
    if not token:
        raise HTTPException(401, "Not authenticated", headers={"WWW-Authenticate": "Bearer"})
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        uid, tv = int(payload["sub"]), int(payload.get("tv", 0))
    except (JWTError, KeyError, ValueError):
        raise HTTPException(401, "Invalid or expired token")
    user = db.get(User, uid)
    if not user or not user.is_active or user.token_version != tv:
        raise HTTPException(401, "Session is no longer valid")
    requested = request.headers.get("X-Org-Id")
    q = select(Membership).where(Membership.user_id == user.id)
    if requested:
        if not requested.isdigit():
            raise HTTPException(400, "Invalid X-Org-Id")
        q = q.where(Membership.org_id == int(requested))
    m = db.scalars(q.order_by(Membership.id)).first()
    if not m:  # same response whether the org does not exist or the user is not a member (no enumeration)
        raise HTTPException(403, "You are not a member of this organization")
    return Ctx(db=db, user=user, org_id=m.org_id, role=m.role, request=request)


def perm(name: str):
    def dep(ctx: Ctx = Depends(get_ctx)) -> Ctx:
        ctx.require(name)
        return ctx
    return dep


# ---------------------------------------------------------------- rate limiting (single-process; use a shared store behind multiple workers)
_hits: dict[str, deque] = defaultdict(deque)


def rate_limited(key: str, limit: int, window: int = 60) -> bool:
    t = time.monotonic()
    q = _hits[key]
    while q and q[0] < t - window:
        q.popleft()
    if len(q) >= limit:
        return True
    q.append(t)
    return False


# ---------------------------------------------------------------- audit trail
def _digest(prev: str, e: AuditEvent) -> str:
    raw = "|".join([prev, str(e.org_id), str(e.actor_id), e.action, e.entity_type, str(e.entity_id),
                    e.detail, e.created_at.isoformat()])
    return hashlib.sha256(raw.encode()).hexdigest()


def audit(ctx_or_db, action: str, entity_type: str = "", entity_id: Optional[int] = None, detail: str = "",
          org_id: Optional[int] = None, actor: Optional[User] = None, ip: str = "") -> AuditEvent:
    if isinstance(ctx_or_db, Ctx):
        db, org_id, actor, ip = ctx_or_db.db, ctx_or_db.org_id, ctx_or_db.user, ctx_or_db.ip
    else:
        db = ctx_or_db
    last = db.scalars(select(AuditEvent).where(AuditEvent.org_id == org_id).order_by(AuditEvent.id.desc()).limit(1)).first()
    ev = AuditEvent(org_id=org_id, actor_id=actor.id if actor else None, actor_label=actor.email if actor else "system",
                    action=action, entity_type=entity_type, entity_id=entity_id, detail=detail[:2000], ip=ip,
                    created_at=now())
    ev.prev_hash = last.hash if last else ""
    ev.hash = _digest(ev.prev_hash, ev)
    db.add(ev)
    db.flush()
    return ev


def verify_chain(db: Session, org_id: int) -> dict:
    prev, count = "", 0
    for e in db.scalars(select(AuditEvent).where(AuditEvent.org_id == org_id).order_by(AuditEvent.id)):
        count += 1
        if e.prev_hash != prev or e.hash != _digest(prev, e):
            return {"valid": False, "checked": count, "broken_at_event_id": e.id}
        prev = e.hash
    return {"valid": True, "checked": count, "broken_at_event_id": None}

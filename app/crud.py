"""Declarative, tenant-safe CRUD with server-side validation, pagination, search and filtering."""
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy import String, delete, func, or_, select
from sqlalchemy.exc import IntegrityError

from .models import Membership, User
from .security import Ctx, audit, get_ctx

MISSING = object()


@dataclass
class Fld:
    kind: str
    req: bool = False
    max: Optional[int] = None
    lo: Optional[int] = None
    hi: Optional[int] = None
    choices: Optional[tuple] = None
    fk: Any = None
    default: Any = MISSING


def S(max=255, req=False, default=MISSING): return Fld("str", req, max=max, default=default)
def T(req=False, default=MISSING): return Fld("str", req, max=20000, default=default)
def I(lo=0, hi=1_000_000, req=False, default=MISSING): return Fld("int", req, lo=lo, hi=hi, default=default)
def D(req=False): return Fld("date", req)
def E(choices, req=False, default=MISSING): return Fld("enum", req, choices=tuple(choices), default=default)
def B(default=False): return Fld("bool", default=default)
def J(): return Fld("json")
def FK(model, req=False): return Fld("fk", req, fk=model)


def validate(spec: dict[str, Fld], data: dict, ctx: Ctx, partial: bool = False) -> dict:
    errors: dict[str, str] = {}
    out: dict[str, Any] = {}
    for name, f in spec.items():
        if name not in data:
            if f.req and not partial:
                errors[name] = "This field is required"
            continue
        v = data[name]
        if v is None or (isinstance(v, str) and v.strip() == "" and f.kind in ("date", "fk", "int")):
            if f.req:
                errors[name] = "This field is required"
            else:
                out[name] = None
            continue
        try:
            if f.kind == "str":
                if not isinstance(v, str):
                    raise ValueError("Must be text")
                v = v.strip()
                if f.req and not v:
                    raise ValueError("This field is required")
                if f.max and len(v) > f.max:
                    raise ValueError(f"Must be at most {f.max} characters")
            elif f.kind == "int":
                v = int(v)
                if v < f.lo or v > f.hi:
                    raise ValueError(f"Must be between {f.lo} and {f.hi}")
            elif f.kind == "date":
                v = date.fromisoformat(str(v)[:10])
            elif f.kind == "enum":
                if v not in f.choices:
                    raise ValueError("Must be one of: " + ", ".join(f.choices))
            elif f.kind == "bool":
                if not isinstance(v, bool):
                    raise ValueError("Must be true or false")
            elif f.kind == "json":
                if not isinstance(v, (list, dict)):
                    raise ValueError("Must be a list or object")
            elif f.kind == "fk":
                v = int(v)
                if f.fk is User:
                    ok = ctx.db.scalar(select(Membership.id).where(Membership.org_id == ctx.org_id, Membership.user_id == v))
                else:
                    ok = ctx.db.scalar(select(f.fk.id).where(f.fk.id == v, f.fk.org_id == ctx.org_id))
                if not ok:
                    raise ValueError("Referenced record was not found in your organization")
        except (ValueError, TypeError) as e:
            errors[name] = str(e) if isinstance(e, ValueError) and str(e) and "invalid literal" not in str(e) else "Invalid value"
            continue
        out[name] = v
    if errors:
        raise HTTPException(422, {"message": "Validation failed", "errors": errors})
    return out


def serialize(obj) -> dict:
    d = {}
    for c in obj.__table__.columns:
        v = getattr(obj, c.name)
        d[c.name] = v.isoformat() if isinstance(v, (date, datetime)) else v
    return d


USER_FIELDS = ("owner_id", "assignee_id", "lead_id", "requested_by", "approver_id", "approved_by", "reviewed_by", "decided_by")


def with_user_names(db, rows: list[dict]) -> list[dict]:
    ids = {r[k] for r in rows for k in USER_FIELDS if r.get(k)}
    names = {}
    if ids:
        names = {u.id: u.full_name for u in db.scalars(select(User).where(User.id.in_(ids)))}
    for r in rows:
        for k in USER_FIELDS:
            if r.get(k):
                r[k[:-3] + "_name"] = names.get(r[k], "")
    return rows


@dataclass
class Res:
    path: str
    model: Any
    fields: dict[str, Fld]
    write_perm: str = "manage"
    search: tuple = ()
    filters: tuple = ()
    order: str = "id"
    tag: str = ""
    entity: str = ""
    enrich: Optional[Callable] = None            # (ctx, rows: list[dict], objs) -> None
    before_create: Optional[Callable] = None     # (ctx, clean) -> None (may raise)
    after_create: Optional[Callable] = None      # (ctx, obj) -> None
    before_update: Optional[Callable] = None     # (ctx, obj, clean) -> None
    after_update: Optional[Callable] = None
    before_delete: Optional[Callable] = None
    defaults: dict = field(default_factory=dict)
    deletable: bool = True


def scoped(ctx: Ctx, model, oid: int):
    obj = ctx.db.get(model, oid)
    if not obj or getattr(obj, "org_id", None) != ctx.org_id:
        raise HTTPException(404, "Not found")  # identical for foreign-tenant and missing => no IDOR oracle
    return obj


def make_router(res: Res) -> APIRouter:
    r = APIRouter(prefix=f"/api/{res.path}", tags=[res.tag or res.path])
    M = res.model
    entity = res.entity or res.path.rstrip("s")

    def rows_for(ctx: Ctx, objs):
        rows = with_user_names(ctx.db, [serialize(o) for o in objs])
        if res.enrich:
            res.enrich(ctx, rows, objs)
        return rows

    @r.get("")
    def list_(ctx: Ctx = Depends(get_ctx), page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=200),
              q: str = "", sort: str = "", desc: bool = False):
        stmt = select(M).where(M.org_id == ctx.org_id)
        for k, v in ctx.request.query_params.items():
            if k in res.filters and v != "":
                col = getattr(M, k)
                stmt = stmt.where(col.is_(None) if v == "null" else col == (int(v) if v.lstrip("-").isdigit() and not isinstance(col.type, String) else v))
        if q:
            like = f"%{q.lower()}%"
            cols = [getattr(M, c) for c in res.search]
            if cols:
                stmt = stmt.where(or_(*[func.lower(c).like(like) for c in cols]))
        total = ctx.db.scalar(select(func.count()).select_from(stmt.subquery()))
        sort_col = getattr(M, sort) if sort and sort in M.__table__.columns else getattr(M, res.order.lstrip("-"))
        flip = res.order.startswith("-") != desc
        stmt = stmt.order_by(sort_col.desc() if flip else sort_col.asc()).offset((page - 1) * page_size).limit(page_size)
        objs = ctx.db.scalars(stmt).all()
        return {"items": rows_for(ctx, objs), "total": total, "page": page, "page_size": page_size}

    @r.get("/{oid}")
    def get_(oid: int, ctx: Ctx = Depends(get_ctx)):
        obj = scoped(ctx, M, oid)
        return rows_for(ctx, [obj])[0]

    @r.post("", status_code=201)
    def create(payload: dict = Body(...), ctx: Ctx = Depends(get_ctx)):
        ctx.require(res.write_perm)
        clean = validate(res.fields, payload, ctx)
        for k, v in res.defaults.items():
            clean.setdefault(k, v)
        if res.before_create:
            res.before_create(ctx, clean)
        obj = M(org_id=ctx.org_id, **clean)
        ctx.db.add(obj)
        try:
            ctx.db.flush()
        except IntegrityError:
            ctx.db.rollback()
            raise HTTPException(409, "A record with the same unique value already exists")
        if res.after_create:
            res.after_create(ctx, obj)
        audit(ctx, f"{entity}.created", entity, obj.id, str({k: v for k, v in clean.items() if k in ("title", "name", "control_key")}))
        ctx.db.commit()
        return rows_for(ctx, [obj])[0]

    @r.patch("/{oid}")
    def update(oid: int, payload: dict = Body(...), ctx: Ctx = Depends(get_ctx)):
        ctx.require(res.write_perm)
        obj = scoped(ctx, M, oid)
        clean = validate(res.fields, payload, ctx, partial=True)
        if res.before_update:
            res.before_update(ctx, obj, clean)
        for k, v in clean.items():
            setattr(obj, k, v)
        try:
            ctx.db.flush()
        except IntegrityError:
            ctx.db.rollback()
            raise HTTPException(409, "A record with the same unique value already exists")
        if res.after_update:
            res.after_update(ctx, obj)
        audit(ctx, f"{entity}.updated", entity, obj.id, "fields: " + ", ".join(sorted(clean)))
        ctx.db.commit()
        return rows_for(ctx, [obj])[0]

    if res.deletable:
        @r.delete("/{oid}", status_code=204)
        def delete_(oid: int, ctx: Ctx = Depends(get_ctx)):
            ctx.require(res.write_perm)
            obj = scoped(ctx, M, oid)
            if res.before_delete:
                res.before_delete(ctx, obj)
            label = getattr(obj, "title", None) or getattr(obj, "name", None) or str(oid)
            ctx.db.delete(obj)
            try:
                ctx.db.flush()
            except IntegrityError:
                ctx.db.rollback()
                raise HTTPException(409, "This record is referenced by other records; remove those links first")
            audit(ctx, f"{entity}.deleted", entity, oid, str(label))
            ctx.db.commit()

    return r


def purge_links(ctx: Ctx, model, **where):
    ctx.db.execute(delete(model).where(*[getattr(model, k) == v for k, v in where.items()]))

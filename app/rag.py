"""Document text extraction, chunking, embeddings and permission-aware retrieval.

Uploaded and retrieved text is untrusted data: it is only ever placed inside delimited <source> blocks and never
interpreted as instructions (see ai.py)."""
import hashlib
import io
import math
import re
import zipfile
from xml.etree import ElementTree

import httpx
from sqlalchemy import delete, select

from .config import settings
from .models import Chunk

LOCAL_MODEL = "local-hash-v1"
DIMS = 256
STOP = set("the a an and or of to in on for with is are be by as at from that this it its if not shall must should "
           "will may can any all each per into than then these those which who whom your our their there has have had".split())
ALLOWED_TEXT = {".txt", ".md", ".csv"}


def tokens(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9][a-z0-9\-]{1,}", text.lower()) if w not in STOP]


# ---------------------------------------------------------------- extraction
def extract_text(filename: str, raw: bytes) -> str:
    """Safe, in-process extraction. No macros executed, no external processes, no network."""
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in ALLOWED_TEXT:
        return raw.decode("utf-8-sig", errors="replace")
    if ext == ".pdf":
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(raw))
            return "\n\n".join((p.extract_text() or "") for p in reader.pages[:200])
        except Exception:
            return ""
    if ext == ".docx":
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                info = z.getinfo("word/document.xml")
                if info.file_size > 20_000_000:  # zip-bomb guard
                    return ""
                xml = z.read("word/document.xml")
            ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            root = ElementTree.fromstring(xml)  # stdlib parser does not resolve external entities
            paras = ["".join(t.text or "" for t in p.iter("{%s}t" % ns["w"])) for p in root.iter("{%s}p" % ns["w"])]
            return "\n\n".join(p for p in paras if p.strip())
        except Exception:
            return ""
    return ""  # images etc. are stored but not interpreted (no OCR configured)


def chunk_text(text: str, size: int = 900) -> list[str]:
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks, cur = [], ""
    for p in paras:
        while len(p) > size:  # hard-split very long paragraphs on sentence boundaries when possible
            cut = p.rfind(". ", 0, size)
            cut = cut + 1 if cut > size // 3 else size
            chunks.append(p[:cut].strip())
            p = p[cut:].strip()
        if len(cur) + len(p) + 2 <= size:
            cur = f"{cur}\n\n{p}".strip()
        else:
            if cur:
                chunks.append(cur)
            cur = p
    if cur:
        chunks.append(cur)
    return [c for c in chunks if len(c) >= 20]


# ---------------------------------------------------------------- embeddings
def local_embed(text: str) -> list[float]:
    vec = [0.0] * DIMS
    toks = tokens(text)
    grams = toks + [f"{a}_{b}" for a, b in zip(toks, toks[1:])]
    for g in grams:
        h = int.from_bytes(hashlib.blake2b(g.encode(), digest_size=8).digest(), "big")
        vec[h % DIMS] += 1.0 if (h >> 40) & 1 else -1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [round(v / norm, 5) for v in vec]


def embed_many(texts: list[str]) -> tuple[list[list[float]], str]:
    if settings.embedding_provider == "openai_compatible" and settings.embedding_api_key:
        try:
            r = httpx.post(settings.embedding_base_url.rstrip("/") + "/embeddings", timeout=30,
                           headers={"Authorization": f"Bearer {settings.embedding_api_key}"},
                           json={"model": settings.embedding_model, "input": texts})
            r.raise_for_status()
            data = sorted(r.json()["data"], key=lambda d: d["index"])
            return [d["embedding"] for d in data], settings.embedding_model
        except Exception:
            pass  # fall back to local so ingestion never silently fails
    return [local_embed(t) for t in texts], LOCAL_MODEL


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b))


# ---------------------------------------------------------------- indexing
def index_source(db, org_id, source_type: str, source_id: int, title: str, text: str, version: int = 1,
                 sensitivity: str = "standard", verified: bool = False) -> int:
    db.execute(delete(Chunk).where(Chunk.org_id == org_id, Chunk.source_type == source_type, Chunk.source_id == source_id))
    pieces = chunk_text(text)
    if not pieces:
        return 0
    vecs, model = embed_many(pieces)
    for i, (p, v) in enumerate(zip(pieces, vecs)):
        db.add(Chunk(org_id=org_id, source_type=source_type, source_id=source_id, source_title=title[:300],
                     source_version=version, idx=i, text=p, embedding=v, embedding_model=model,
                     sensitivity=sensitivity, verified=verified))
    return len(pieces)


def drop_source(db, org_id, source_type: str, source_id: int) -> None:
    db.execute(delete(Chunk).where(Chunk.org_id == org_id, Chunk.source_type == source_type, Chunk.source_id == source_id))


# ---------------------------------------------------------------- retrieval
def search(ctx, query: str, k: int = 6, source_types: tuple | None = None, min_score: float = 0.12) -> list[dict]:
    """Org-scoped (tenant isolation) and sensitivity-aware (permission-aware) hybrid retrieval."""
    stmt = select(Chunk).where(Chunk.org_id == ctx.org_id)
    if not ctx.can("restricted"):
        stmt = stmt.where(Chunk.sensitivity == "standard")
    if source_types:
        stmt = stmt.where(Chunk.source_type.in_(source_types))
    chunks = ctx.db.scalars(stmt).all()
    if not chunks:
        return []
    qvecs, model = embed_many([query])
    qv = qvecs[0]
    qtok = set(tokens(query))
    scored = []
    for c in chunks:
        cos = cosine(qv, c.embedding) if c.embedding_model == model else 0.0
        ct = set(tokens(c.text))
        kw = len(qtok & ct) / (len(qtok) or 1)
        s = 0.55 * max(cos, 0) + 0.45 * kw if c.embedding_model == model else kw
        if s >= min_score:
            scored.append((s, c))
    scored.sort(key=lambda x: -x[0])
    return [{"chunk_id": c.id, "source_type": c.source_type, "source_id": c.source_id, "title": c.source_title,
             "version": c.source_version, "location": f"chunk {c.idx + 1}", "excerpt": c.text[:900],
             "score": round(s, 3), "verified_framework_text": c.verified} for s, c in scored[:k]]

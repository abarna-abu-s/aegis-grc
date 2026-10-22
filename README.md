# Aegis GRC — Full-stack milestone demo

A separate, original learning/demo implementation of a small compliance workflow. It does **not** reuse GigaChad GRC code.

## Milestone features
- PostgreSQL persistence (SQLAlchemy schema auto-created on startup)
- Account registration/login with hashed passwords and signed JWTs
- Basic roles: first account becomes `admin`; subsequent accounts become `editor`
- Create controls with unique control IDs, framework, owner and description
- Ingest source documents by pasted text or `.txt`, `.md`, `.csv` upload
- Attach evidence metadata and upload evidence files
- Gap-analysis endpoint that retrieves matching source paragraphs and returns citation metadata
- Single-page responsive UI served by FastAPI
- Docker Compose for app + PostgreSQL; uploaded files stored in a persistent Docker volume

## Run locally (Docker recommended)
1. Install Docker Desktop / Docker Engine with Compose v2.
2. Copy `.env.example` to `.env`. Change `POSTGRES_PASSWORD` and `JWT_SECRET` for anything beyond a local demo.
3. From this directory run:

   ```bash
   docker compose up --build
   ```

4. Open <http://localhost:8000>.
5. Register an account (the first account becomes admin), then create a control, ingest a policy excerpt, attach evidence, and run gap analysis.
6. API docs: <http://localhost:8000/docs>.

To stop: `docker compose down`. To remove the demo database and uploaded files too: `docker compose down -v`.

## No Docker?
Use Python 3.12+, set `DATABASE_URL=sqlite:///./aegis_demo.db` for a local SQLite fallback, then:

```bash
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

PostgreSQL is the intended demo path. SQLite is only a convenience for local smoke testing.

## Demo walkthrough
1. Register with a strong demo password.
2. Under **Controls**, create `AC-001 — Privileged access is reviewed` and save it.
3. Under **Source documents**, paste a fictional policy excerpt such as:

   > Privileged accounts must use multi-factor authentication. Access to privileged roles is reviewed quarterly by the system owner. Review results and remediation actions are retained for at least one year.

4. Go back to Controls, attach an evidence record (optionally link the source document).
5. Click **Analyze gap**. Inspect the explanation and the source citation(s), including document title, paragraph location, and excerpt.

Use fictional/non-sensitive content only.

## API outline
- `POST /api/auth/register`, `POST /api/auth/login`, `GET /api/auth/me`
- `GET/POST /api/controls`
- `GET /api/documents`, `POST /api/documents/text`, `POST /api/documents/upload`
- `POST /api/controls/{id}/evidence`, `POST /api/controls/{id}/evidence/upload`
- `GET /api/controls/{id}/gap-analysis`
- `GET /api/dashboard`, `GET /api/health`

## Important limitations before production
- The retrieval layer is deliberately transparent keyword/paragraph matching, **not** vector-embedding RAG or an LLM. Citations point to stored document excerpts; analysis language is deterministic.
- Text document ingestion only for `.txt`, `.md`, `.csv`; no PDF parsing/OCR yet. Evidence attachments can include PDF/image files but are stored, not interpreted.
- File storage is local volume storage. No malware scanning, object-storage lifecycle, encryption-at-rest configuration, or file download route.
- No email verification, password reset, MFA, invitation workflow, tenant isolation, fine-grained permissions, rate limiting, audit log, CSRF/session cookie handling, or production secret management.
- First registered user is admin and the API uses a basic role check; do not expose the registration endpoint publicly for a real deployment.
- The default JWT secret and database password are for local development only. Change them before any shared environment.
- Schema creation is `create_all`; introduce Alembic migrations before evolving the schema.
- Add tests, secure deployment configuration, tenant boundaries, audit trail, backups, and threat modeling before handling real compliance evidence.

## Next technical upgrades
1. Add tenant/org tables and enforce organization scoping on every query.
2. Use Alembic migrations and stronger role/permission rules.
3. Store files in S3-compatible object storage with encryption, scanning and immutable audit metadata.
4. Add PDF/DOCX parsing and chunking; use embeddings plus PostgreSQL `pgvector` (or a managed vector DB) for semantic retrieval.
5. Call an LLM with retrieved chunks only, require structured outputs, and enforce citation IDs/excerpts in the response validator.
6. Add integrations through least-privilege OAuth/API tokens, collection schedules, source provenance and sync history.

## GigaChad GRC licensing note
The repository page currently identifies its license as **Elastic License 2.0 (ELv2)**. It permits certain internal/self-hosted use and modifications but restricts offering the software as a hosted/managed service and creating a competing commercial product based on it. Review the repository's current [`LICENSE`](https://github.com/grcengineering/gigachad-grc/blob/main/LICENSE) and obtain legal advice/licensing clarification before reusing its code. This starter was written separately and does not copy its source.

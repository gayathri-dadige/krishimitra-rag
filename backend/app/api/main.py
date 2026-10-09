from pathlib import Path
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.chat.service import answer_question
from app.core.config import get_settings
from app.db.models import Chunk
from app.db.session import get_db
from app.llm.client import LLMResponseError, LLMUnavailableError
from app.taxonomy.service import taxonomy_payload

settings = get_settings()
app = FastAPI(title=settings.app_name, version="0.1.0")
CURRENT_MIGRATION = "0005_slice4_session_version"


class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str = Field(..., min_length=1, max_length=1000)
    as_of_date: str | None = None

    @field_validator("as_of_date")
    @classmethod
    def validate_as_of_date(cls, value: str | None) -> str | None:
        if value is None:
            return value
        try:
            from datetime import date

            date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("as_of_date must be YYYY-MM-DD") from exc
        return value

    @field_validator("message")
    @classmethod
    def reject_control_characters(cls, value: str) -> str:
        if any(ord(character) < 32 and character not in "\n\t" for character in value):
            raise ValueError("message contains unsupported control characters")
        return value

if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response: Response = await call_next(request)
    response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self' 'unsafe-inline'"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["X-Trace-Id"] = str(uuid4())
    return response


@app.get("/healthz", tags=["ops"])
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz", tags=["ops"])
def readyz(db: Session = Depends(get_db)) -> dict[str, str]:  # noqa: B008
    try:
        db.execute(text("SELECT 1"))
        db.execute(text("SELECT 1 FROM crops LIMIT 1"))

        backend_name = db.bind.url.get_backend_name() if db.bind is not None else ""
        if backend_name != "sqlite":
            revision = db.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
            if revision != CURRENT_MIGRATION:
                raise RuntimeError("database migrations are not at head")
    except (RuntimeError, SQLAlchemyError, ValueError):
        return Response(
            content='{"status":"not_ready"}',
            media_type="application/json",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    return {"status": "ready"}


@app.get("/taxonomy", tags=["taxonomy"])
@app.get("/api/v1/taxonomy", include_in_schema=False, tags=["taxonomy"])
def get_taxonomy(
    lang: str = Query(default="en", pattern="^(en|te|hi)$"),
    db: Session = Depends(get_db),  # noqa: B008
):
    return taxonomy_payload(db, lang)


@app.post("/chat", tags=["chat"])
@app.post("/api/v1/chat", include_in_schema=False, tags=["chat"])
def chat(request: ChatRequest, db: Session = Depends(get_db)) -> dict[str, object]:  # noqa: B008
    try:
        return answer_question(db, request.message, request.as_of_date, request.session_id)
    except (LLMResponseError, LLMUnavailableError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The context extraction service is temporarily unavailable",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@app.get("/chunks/{chunk_id}", tags=["ingestion"])
@app.get("/api/v1/chunks/{chunk_id}", include_in_schema=False, tags=["ingestion"])
def get_chunk_detail(
    chunk_id: int, db: Session = Depends(get_db)  # noqa: B008
) -> dict[str, object]:
    chunk = db.scalar(select(Chunk).where(Chunk.id == chunk_id))
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chunk not found")

    document = chunk.document
    return {
        "id": chunk.id,
        "ordinal": chunk.ordinal,
        "text": chunk.text,
        "page_start": chunk.page_start,
        "page_end": chunk.page_end,
        "section_path": chunk.section_path,
        "document": {
            "id": document.id,
            "title": document.title,
            "publisher": document.publisher,
            "doc_type": document.doc_type,
            "language": document.language,
            "publication_date": document.publication_date.isoformat(),
            "version_label": document.version_label,
        },
        "claims": [
            {
                "id": claim.id,
                "action_key": claim.action_key,
                "value_num": claim.value_num,
                "value_unit": claim.value_unit,
                "timing_day_from": claim.timing_day_from,
                "timing_day_to": claim.timing_day_to,
                "timing_text": claim.timing_text,
                "source_span": claim.source_span,
            }
            for claim in chunk.claims
        ],
    }


frontend_dist = Path(__file__).resolve().parents[3] / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/assets", StaticFiles(directory=frontend_dist / "assets"), name="assets")

    @app.get("/", include_in_schema=False)
    def frontend_index():
        return FileResponse(frontend_dist / "index.html")

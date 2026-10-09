"""
reranker_service.py — Local cross-encoder reranker (bge-reranker-v2-m3)

A standalone FastAPI service that the Rust backend (or TS agent service) calls
via HTTP to rerank retrieved chunks. Matches the wire contract expected by
src/services/reranker.ts → `local()`:

    POST /rerank
    Request:  { "query": str, "passages": [str, ...], "top_k": int }
    Response: { "scores": [float, ...] }       # length == len(passages),
                                                # ALIGNED to input order,
                                                # caller is responsible for top-k slicing.

Production features:
  - Lazy single-load of the cross-encoder (FlagEmbedding's FlagReranker preferred,
    sentence-transformers CrossEncoder fallback).
  - Configurable batch size, max passage tokens, and device (cpu/cuda).
  - Hard request timeout via asyncio.wait_for + a thread-pool offload of the
    blocking model call.
  - In-process circuit breaker: opens after N consecutive failures,
    auto half-opens after a cooldown, returns 503 while open.
  - Concurrency limit (asyncio.Semaphore) prevents the model from being
    hammered into OOM under burst load.
  - /health (liveness, cheap) and /ready (waits for model warm-up) for k8s.
  - Structured JSON logging, request IDs, latency metrics.
  - Pydantic v2 request/response validation with strict bounds.
  - CORS disabled by default; INTERNAL_AUTH_TOKEN support for shared-secret auth.

Run locally:
    pip install -r requirements.txt
    uvicorn reranker_service:app --host 0.0.0.0 --port 8000 --workers 1
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import List, Optional

import secrets  # C10: constant-time token comparison

from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

# ─────────────────────────────────────────────────────────────────────────────
# Configuration (env-driven, validated at startup)
# ─────────────────────────────────────────────────────────────────────────────

MODEL_NAME       = os.getenv("RERANKER_MODEL", "BAAI/bge-reranker-v2-m3")
DEVICE           = os.getenv("RERANKER_DEVICE", "cpu")           # "cpu" or "cuda"
USE_FP16         = os.getenv("RERANKER_FP16", "false").lower() == "true"
BATCH_SIZE       = int(os.getenv("RERANKER_BATCH_SIZE", "16"))
MAX_PASSAGES     = int(os.getenv("RERANKER_MAX_PASSAGES", "200"))
MAX_PASSAGE_LEN  = int(os.getenv("RERANKER_MAX_PASSAGE_LEN", "8000"))   # characters
MAX_QUERY_LEN    = int(os.getenv("RERANKER_MAX_QUERY_LEN", "2000"))     # characters
REQUEST_TIMEOUT  = float(os.getenv("RERANKER_TIMEOUT_SEC", "20.0"))   # C13: was 300s; 20s is the safe upper bound on CPU
MAX_CONCURRENT   = int(os.getenv("RERANKER_MAX_CONCURRENT", "4"))
CB_FAIL_THRESHOLD = int(os.getenv("RERANKER_CB_FAIL_THRESHOLD", "5"))
CB_COOLDOWN_SEC   = float(os.getenv("RERANKER_CB_COOLDOWN_SEC", "30.0"))
AUTH_TOKEN        = os.getenv("RERANKER_AUTH_TOKEN", "")                # "" = no auth
LOG_LEVEL         = os.getenv("RERANKER_LOG_LEVEL", "INFO").upper()

# Structured logging — newline-delimited JSON is friendliest for log aggregators.
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    stream=sys.stdout,
    format='{"ts":"%(asctime)s","lvl":"%(levelname)s","msg":%(message)s}',
)
log = logging.getLogger("reranker")


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic schemas — strict bounds protect the model from pathological inputs
# ─────────────────────────────────────────────────────────────────────────────

class RerankRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=MAX_QUERY_LEN)
    passages: List[str] = Field(..., min_length=1, max_length=MAX_PASSAGES)
    top_k: int = Field(default=5, ge=1, le=MAX_PASSAGES)

    @field_validator("passages")
    @classmethod
    def _truncate_passages(cls, v: List[str]) -> List[str]:
        # Truncate over-long passages rather than rejecting — caller sent them
        # for a reason. Empty strings are replaced with a single space so the
        # model doesn't crash and the index alignment is preserved.
        return [(p[:MAX_PASSAGE_LEN] if p else " ") for p in v]


class RerankResponse(BaseModel):
    scores: List[float]
    # Optional diagnostics — clients can ignore.
    model: str
    batch_count: int
    elapsed_ms: float


class HealthResponse(BaseModel):
    status: str
    model: str
    device: str
    model_loaded: bool
    circuit_state: str


# ─────────────────────────────────────────────────────────────────────────────
# Circuit breaker — opens after consecutive failures, auto half-opens.
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CircuitBreaker:
    fail_threshold: int
    cooldown_sec: float
    failures: int = 0
    opened_at: float = 0.0
    state: str = "closed"       # closed | open | half_open
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def before(self) -> None:
        async with self._lock:
            if self.state == "open":
                if (time.monotonic() - self.opened_at) >= self.cooldown_sec:
                    self.state = "half_open"
                    log.info('"event":"circuit_half_open"')
                else:
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail="reranker circuit open — recently failing; retry shortly",
                    )

    async def record_success(self) -> None:
        async with self._lock:
            if self.state != "closed":
                log.info('"event":"circuit_close"')
            self.failures = 0
            self.state = "closed"

    async def record_failure(self) -> None:
        async with self._lock:
            self.failures += 1
            if self.failures >= self.fail_threshold:
                self.state = "open"
                self.opened_at = time.monotonic()
                log.warning(f'"event":"circuit_open","failures":{self.failures}')


circuit = CircuitBreaker(fail_threshold=CB_FAIL_THRESHOLD, cooldown_sec=CB_COOLDOWN_SEC)
sem = asyncio.Semaphore(MAX_CONCURRENT)


# ─────────────────────────────────────────────────────────────────────────────
# Model wrapper — supports FlagEmbedding (preferred) or sentence-transformers.
# ─────────────────────────────────────────────────────────────────────────────

class RerankerModel:
    """Thin facade over FlagReranker / CrossEncoder with a uniform .score() API."""

    def __init__(self) -> None:
        self._impl = None
        self._impl_kind = ""       # "flag" or "cross_encoder"
        self._ready = False

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def kind(self) -> str:
        return self._impl_kind

    def load(self) -> None:
        """Blocking load — called once in the lifespan startup task."""
        t0 = time.monotonic()
        log.info(f'"event":"model_load_start","model":"{MODEL_NAME}","device":"{DEVICE}"')
        try:
            # Preferred: FlagEmbedding's FlagReranker — natively supports bge-reranker-v2-m3.
            from FlagEmbedding import FlagReranker
            use_fp16 = USE_FP16 and DEVICE.startswith("cuda")
            self._impl = FlagReranker(
                MODEL_NAME,
                use_fp16=use_fp16,
                device=DEVICE,
            )
            self._impl_kind = "flag"
        except ImportError:
            # Fallback: sentence-transformers CrossEncoder — works with any HF cross-encoder
            # but FlagEmbedding gives better numbers on bge-reranker-v2-m3 specifically.
            log.warning('"event":"flag_embedding_unavailable","fallback":"cross_encoder"')
            from sentence_transformers import CrossEncoder
            # C9: was max_length=512; bge-reranker-v2-m3 supports 8192 tokens.
            # 512 silently truncates CSV row-batch chunks (~600 tokens) losing the last ~30%.
            self._impl = CrossEncoder(MODEL_NAME, device=DEVICE, max_length=8192)
            self._impl_kind = "cross_encoder"

        # Warm up with a single small forward pass so the first real request isn't slow.
        try:
            self._score_blocking("warm up query", ["warm up passage"], batch_size=1)
        except Exception as e:  # pragma: no cover  (defensive)
            log.warning(f'"event":"warmup_failed","err":{repr(str(e))[:200]}')

        self._ready = True
        log.info(f'"event":"model_load_complete","elapsed_ms":{(time.monotonic() - t0) * 1000:.1f},"impl":"{self._impl_kind}"')

    def _score_blocking(self, query: str, passages: List[str], batch_size: int) -> List[float]:
        """The blocking scoring call. Wrapped by score() in a thread pool."""
        import torch  # local import keeps startup fast when torch isn't needed
        pairs = [[query, p] for p in passages]
        if self._impl_kind == "flag":
            # FlagReranker.compute_score returns float or List[float] depending on input.
            # Pass max_length explicitly so the token limit matches the model's context window.
            raw = self._impl.compute_score(pairs, batch_size=batch_size, normalize=True, max_length=8192)
            scores = raw if isinstance(raw, list) else [float(raw)]
            return [float(s) for s in scores]

        # CrossEncoder path — returns raw logits (can be outside [0,1]).
        # C15: Apply sigmoid so scores are on [0,1] and comparable to the FlagReranker path.
        # Without this the downstream SIMILARITY_FLOOR=0.3 filter (tuned for cosine / probability)
        # can incorrectly admit or reject chunks when logits are negative or > 5.
        raw = self._impl.predict(
            pairs,
            batch_size=batch_size,
            show_progress_bar=False,
            convert_to_numpy=False,
            convert_to_tensor=True,
        )
        return [float(s) for s in torch.sigmoid(raw).tolist()]

    async def score(self, query: str, passages: List[str]) -> tuple[List[float], int]:
        """Async wrapper: offload to a thread, returns (scores, batch_count)."""
        if not self._ready or self._impl is None:
            raise RuntimeError("model not loaded")
        batch_count = (len(passages) + BATCH_SIZE - 1) // BATCH_SIZE
        loop = asyncio.get_running_loop()
        scores = await loop.run_in_executor(
            None, self._score_blocking, query, passages, BATCH_SIZE
        )
        return scores, batch_count


model = RerankerModel()


# ─────────────────────────────────────────────────────────────────────────────
# FastAPI lifespan — load the model on startup so /ready flips true.
# ─────────────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
    # Load model on startup in a background thread so we don't block the
    # event loop while torch initialises.
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, model.load)
    yield
    # No teardown needed — torch tensors are freed on process exit.


app = FastAPI(
    title="bge-reranker-v2-m3 service",
    version="1.0.0",
    docs_url="/docs",
    lifespan=lifespan,
)


# ─────────────────────────────────────────────────────────────────────────────
# Middleware — correlation ID + access log + optional shared-secret auth
# ─────────────────────────────────────────────────────────────────────────────

@app.middleware("http")
async def correlate_and_log(request: Request, call_next):  # type: ignore[no-untyped-def]
    req_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    t0 = time.monotonic()
    try:
        response = await call_next(request)
    except Exception as e:
        log.exception(f'"event":"unhandled_exception","req_id":"{req_id}","err":{repr(str(e))[:200]}')
        return JSONResponse(
            {"detail": "internal error"},
            status_code=500,
            headers={"x-request-id": req_id},
        )
    response.headers["x-request-id"] = req_id
    elapsed = (time.monotonic() - t0) * 1000
    log.info(
        f'"event":"http","req_id":"{req_id}","path":"{request.url.path}",'
        f'"method":"{request.method}","status":{response.status_code},'
        f'"ms":{elapsed:.1f}'
    )
    return response


def _check_auth(authorization: Optional[str]) -> None:
    if not AUTH_TOKEN:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    provided = authorization.removeprefix("Bearer ").strip()
    # C10: Use constant-time comparison to prevent timing-based token enumeration.
    # Python's != operator short-circuits on the first differing byte, leaking
    # information about how many characters matched.
    if not secrets.compare_digest(provided, AUTH_TOKEN):
        raise HTTPException(status_code=401, detail="invalid token")


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Liveness — cheap, always returns even before the model is loaded."""
    return HealthResponse(
        status="ok",
        model=MODEL_NAME,
        device=DEVICE,
        model_loaded=model.ready,
        circuit_state=circuit.state,
    )


@app.get("/ready")
async def ready() -> JSONResponse:
    """Readiness — returns 503 until the model is loaded."""
    if not model.ready:
        return JSONResponse({"ready": False}, status_code=503)
    if circuit.state == "open":
        return JSONResponse({"ready": False, "reason": "circuit_open"}, status_code=503)
    return JSONResponse({"ready": True})


@app.post("/rerank", response_model=RerankResponse)
async def rerank(
    req: RerankRequest,
    authorization: Optional[str] = Header(default=None),
) -> RerankResponse:
    _check_auth(authorization)

    if not model.ready:
        raise HTTPException(status_code=503, detail="model still loading")

    await circuit.before()

    t0 = time.monotonic()
    try:
        async with sem:
            scores, batch_count = await asyncio.wait_for(
                model.score(req.query, req.passages),
                timeout=REQUEST_TIMEOUT,
            )
    except asyncio.TimeoutError:
        await circuit.record_failure()
        log.warning(
            f'"event":"rerank_timeout","passages":{len(req.passages)},'
            f'"timeout_s":{REQUEST_TIMEOUT}'
        )
        raise HTTPException(status_code=504, detail="reranker timed out")
    except Exception as e:
        await circuit.record_failure()
        log.exception(f'"event":"rerank_error","err":{repr(str(e))[:200]}')
        raise HTTPException(status_code=500, detail=f"reranker error: {str(e)[:120]}")

    await circuit.record_success()

    if len(scores) != len(req.passages):
        # Defensive — should never happen, but a mismatch silently corrupts
        # downstream sort order.
        raise HTTPException(
            status_code=500,
            detail=f"score/passage count mismatch ({len(scores)} vs {len(req.passages)})",
        )

    elapsed_ms = (time.monotonic() - t0) * 1000
    return RerankResponse(
        scores=scores,
        model=MODEL_NAME,
        batch_count=batch_count,
        elapsed_ms=round(elapsed_ms, 2),
    )


@app.get("/")
async def root() -> dict:
    return {
        "service": "bge-reranker-v2-m3",
        "endpoints": ["/health", "/ready", "/rerank", "/docs"],
        "model": MODEL_NAME,
        "device": DEVICE,
    }

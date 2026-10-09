# krishimitra-rag
# KrishiMitra RAG

A context- and version-aware multilingual crop advisory system using taxonomy-validated metadata, conflict detection, and grounded verification.

## Local development without Docker

Docker is reserved for deployment verification. For local development, run PostgreSQL 16 with the `vector` extension and create a database named `krishimitra`.

First create `backend/.env` from the example and replace the username, password, host, port, and database name with the credentials for your local PostgreSQL installation:

```powershell
Copy-Item ..\.env.example .env
notepad .env
```

```powershell
cd backend
..\.venv\Scripts\python.exe -m pip install -r requirements.txt
..\.venv\Scripts\python.exe -m alembic upgrade head
..\.venv\Scripts\python.exe -m app.db.seed
..\.venv\Scripts\python.exe -m uvicorn app.api.main:app --reload
```

The chat endpoint accepts ordinary English questions as well as explicit context
fields such as `Crop: paddy | Region: Warangal`. Missing context is requested one
slot at a time with taxonomy-backed options. The returned `session_id` should be
sent with the next request so the clarification answer is merged with the
original question. Run `alembic upgrade head` after pulling database changes;
the current head includes persistent chat sessions and messages.

In a second terminal:

```powershell
cd frontend
npm install
npm run dev
```

The API is available at `http://localhost:8000`. The Vite frontend is available at the URL printed by `npm run dev`.

The connection string must use this format:

```text
DATABASE_URL=postgresql+psycopg://USERNAME:PASSWORD@localhost:5432/DATABASE_NAME
```

Do not commit `backend/.env`; it is ignored by Git.

Context extraction is deterministic by default. For Slice 4 provider testing,
set `LLM_PROVIDER=fake` and provide `LLM_FAKE_RESPONSE` as a JSON object. To use
the optional live Anthropic extractor, set `LLM_PROVIDER=anthropic`,
`LLM_MODEL`, and `ANTHROPIC_API_KEY`. Live failures return HTTP 503 without
producing a partial advisory.

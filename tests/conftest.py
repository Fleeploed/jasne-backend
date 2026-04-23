"""
Shared fixtures: FastAPI TestClient with mocked OpenAI and Supabase.
No network calls are made in the test suite.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import jwt
import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Fake Supabase client — emulates the fluent query builder in-memory
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, data: list[dict[str, Any]] | None):
        self.data = data


class _FakeQuery:
    """Minimal in-memory table query emulation."""

    def __init__(self, table: _FakeTable):
        self._table = table
        self._filters: list[tuple[str, Any]] = []
        self._op: str = "select"
        self._op_payload: Any = None
        self._order: str | None = None
        self._order_desc: bool = False
        self._limit: int | None = None
        self._range: tuple[int, int] | None = None

    # --- filters
    def eq(self, column: str, value: Any) -> _FakeQuery:
        self._filters.append((column, value))
        return self

    def order(self, column: str, desc: bool = False) -> _FakeQuery:
        self._order = column
        self._order_desc = desc
        return self

    def limit(self, n: int) -> _FakeQuery:
        self._limit = n
        return self

    def range(self, start: int, end: int) -> _FakeQuery:
        self._range = (start, end)
        return self

    # --- ops
    def select(self, *args, **kwargs) -> _FakeQuery:
        self._op = "select"
        return self

    def insert(self, payload: Any) -> _FakeQuery:
        self._op = "insert"
        self._op_payload = payload
        return self

    def update(self, payload: Any) -> _FakeQuery:
        self._op = "update"
        self._op_payload = payload
        return self

    def delete(self) -> _FakeQuery:
        self._op = "delete"
        return self

    async def execute(self) -> _FakeResponse:
        rows = self._table.rows
        # apply filters
        for col, val in self._filters:
            rows = [r for r in rows if str(r.get(col)) == str(val)]

        if self._op == "select":
            out = list(rows)
            if self._order:
                out.sort(key=lambda r: r.get(self._order, ""), reverse=self._order_desc)
            if self._range:
                s, e = self._range
                out = out[s : e + 1]
            elif self._limit is not None:
                out = out[: self._limit]
            return _FakeResponse(out)

        if self._op == "insert":
            payloads = self._op_payload if isinstance(self._op_payload, list) else [self._op_payload]
            inserted = []
            for p in payloads:
                row = dict(p)
                row.setdefault("id", str(uuid4()))
                row.setdefault("created_at", datetime.now(UTC).isoformat())
                row.setdefault("updated_at", datetime.now(UTC).isoformat())
                row.setdefault("reminder_days", [30, 14, 7, 1])
                row.setdefault("status", "active")
                row.setdefault("deadline_type", row.get("deadline_type", "action"))
                self._table.rows.append(row)
                inserted.append(row)
            return _FakeResponse(inserted)

        if self._op == "update":
            updated = []
            for row in rows:
                row.update(self._op_payload)
                row["updated_at"] = datetime.now(UTC).isoformat()
                updated.append(row)
            return _FakeResponse(updated)

        if self._op == "delete":
            ids_to_remove = {id(r) for r in rows}
            self._table.rows = [r for r in self._table.rows if id(r) not in ids_to_remove]
            return _FakeResponse(list(rows))

        return _FakeResponse([])


class _FakeTable:
    def __init__(self, name: str):
        self.name = name
        self.rows: list[dict[str, Any]] = []

    def select(self, *args, **kwargs) -> _FakeQuery:
        return _FakeQuery(self).select(*args, **kwargs)

    def insert(self, payload: Any) -> _FakeQuery:
        return _FakeQuery(self).insert(payload)

    def update(self, payload: Any) -> _FakeQuery:
        return _FakeQuery(self).update(payload)

    def delete(self) -> _FakeQuery:
        return _FakeQuery(self).delete()


class _FakeStorageBucket:
    async def create_signed_url(self, path: str, ttl: int) -> dict[str, str]:
        return {"signedURL": f"https://fake-storage/{path}?ttl={ttl}"}


class _FakeStorage:
    def from_(self, bucket: str) -> _FakeStorageBucket:
        return _FakeStorageBucket()


class FakeSupabase:
    """In-memory Supabase emulation sufficient for router tests."""

    def __init__(self):
        self._tables: dict[str, _FakeTable] = {}
        self.storage = _FakeStorage()
        self.rpc_result: list[dict[str, Any]] = []

    def table(self, name: str) -> _FakeTable:
        if name not in self._tables:
            self._tables[name] = _FakeTable(name)
        return self._tables[name]

    def rpc(self, name: str, params: dict[str, Any]) -> _FakeQuery:
        q = _FakeQuery(_FakeTable(f"rpc:{name}"))

        async def _execute():
            return _FakeResponse(list(self.rpc_result))

        q.execute = _execute  # type: ignore[assignment]
        return q


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_supabase() -> FakeSupabase:
    return FakeSupabase()


@pytest.fixture
def test_user_id() -> UUID:
    return UUID("11111111-1111-1111-1111-111111111111")


@pytest.fixture
def jwt_token(test_user_id: UUID) -> str:
    """Valid HS256 JWT signed with the test secret from pytest.ini env."""
    import os

    payload = {
        "sub": str(test_user_id),
        "aud": "authenticated",
        "exp": 9999999999,
        "iat": 1700000000,
        "email": "t@t.cz",
        "role": "authenticated",
    }
    return jwt.encode(payload, os.environ["SUPABASE_JWT_SECRET"], algorithm="HS256")


@pytest.fixture
def auth_headers(jwt_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {jwt_token}"}


@pytest.fixture
def mock_openai_classify(monkeypatch):
    """Patch classify_document to return a preset ClassifierResult."""
    from app.models.schemas import ClassifierResult

    result = ClassifierResult(
        ocr_text="Platební výměr č. 12345 VS 9876 částka 4 320 Kč",
        category="danove",
        doc_type="platebni_vymer",
        sender="Finanční úřad pro Prahu 3",
        document_language="cs",
        confidence=0.95,
    )

    async def fake(image_url: str):
        return result

    monkeypatch.setattr("app.routers.scan.classify_document", fake)
    return result


@pytest.fixture
def mock_openai_analyze(monkeypatch):
    """Patch analyze_document to return a preset AnalysisResult."""
    from datetime import date

    from app.models.schemas import AnalysisResult, DeadlineItem

    result = AnalysisResult(
        document_type="platebni_vymer",
        document_type_cz="Platební výměr",
        document_type_localized=None,
        sender="Finanční úřad",
        summary="Нужно оплатить 4 320 Kč до 15 мая.",
        translation=None,
        deadlines=[DeadlineItem(date=date(2026, 5, 15), description="Оплатить налог", type="payment")],
        actions_required=["Оплатить 4 320 Kč"],
        penalty="8% годовых",
        where_to_go="Finanční úřad",
        documents_needed=["Daňové přiznání"],
        confidence=0.92,
        tips=None,
    )

    async def fake(classifier, rag_chunks, language, has_datova_schranka):
        return result, "gpt-4o"

    monkeypatch.setattr("app.routers.scan.analyze_document", fake)
    return result


@pytest.fixture
def mock_rag_empty(monkeypatch):
    async def fake(ocr_text: str, category: str) -> list[str]:
        return []

    monkeypatch.setattr("app.routers.scan.retrieve_chunks", fake)


@pytest.fixture
def client(fake_supabase, monkeypatch):
    """FastAPI TestClient with Supabase patched to in-memory fake."""

    async def fake_get_supabase():
        return fake_supabase

    async def fake_init_supabase():
        return fake_supabase

    async def fake_close_supabase():
        return None

    monkeypatch.setattr("app.services.rag.get_supabase", fake_get_supabase)
    monkeypatch.setattr("app.services.rag.init_supabase", fake_init_supabase)
    monkeypatch.setattr("app.services.rag.close_supabase", fake_close_supabase)
    monkeypatch.setattr("app.routers.profile.get_supabase", fake_get_supabase)
    monkeypatch.setattr("app.routers.scan.get_supabase", fake_get_supabase)
    monkeypatch.setattr("app.routers.deadlines.get_supabase", fake_get_supabase)
    monkeypatch.setattr("app.main.init_supabase", fake_init_supabase)
    monkeypatch.setattr("app.main.close_supabase", fake_close_supabase)

    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def openai_chat_mock(monkeypatch):
    """Helper to stub AsyncOpenAI.chat.completions.create with a given JSON payload."""

    def _set(payload: dict[str, Any]):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content=json.dumps(payload)))]
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        mock_client.embeddings.create = AsyncMock(
            return_value=MagicMock(data=[MagicMock(embedding=[0.1] * 1536)])
        )
        # ocr.py / explain.py do `from app.services.embeddings import get_openai_client`,
        # so we must patch the re-bound reference in each consumer module.
        monkeypatch.setattr("app.services.embeddings.get_openai_client", lambda: mock_client)
        monkeypatch.setattr("app.services.ocr.get_openai_client", lambda: mock_client)
        monkeypatch.setattr("app.services.explain.get_openai_client", lambda: mock_client)
        return mock_client

    return _set

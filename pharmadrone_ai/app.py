"""FastAPI application for the standalone PharmaDrone AI SaaS product."""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import re
from typing import Any, Iterator, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from pharmadrone import db
from pharmadrone.pipeline import ai_bd_service
from pharmadrone_ai import chat, repository, security


WEB_ROOT = Path(__file__).resolve().parents[1] / "apps" / "pharmadrone-ai"


class RegisterRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=10, max_length=256)
    display_name: str = Field(min_length=1, max_length=120)
    workspace_name: str = Field(min_length=1, max_length=160)


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)


class LeadRequest(BaseModel):
    theme: str = Field(min_length=1, max_length=120)
    company_filters: list[str] = Field(default_factory=list, max_length=20)
    limit: int = Field(default=10, ge=1, le=20)


class PitchRequest(BaseModel):
    company: str = Field(min_length=1, max_length=160)
    theme: str = Field(min_length=1, max_length=120)
    case_type: str = Field(default=ai_bd_service.DEFAULT_CASE_TYPE, max_length=120)


class ChatRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=2_000)
    conversation_id: Optional[str] = Field(default=None, max_length=100)
    use_llm: bool = False


class ConversationRequest(BaseModel):
    title: str = Field(default="New conversation", min_length=1, max_length=160)


class SaveLeadRequest(BaseModel):
    lead: dict[str, Any]


class SaveReportRequest(BaseModel):
    report: dict[str, Any]


def _connection() -> Iterator[Any]:
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


def _bearer(authorization: Optional[str]) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required.")
    token = authorization[7:].strip()
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required.")
    return token


def _principal(
    authorization: Optional[str] = Header(default=None),
    conn=Depends(_connection),
) -> dict[str, Any]:
    try:
        token = security.decode_session_token(_bearer(authorization))
        return repository.current_user(conn, token["sub"], token["workspace_id"])
    except security.AuthenticationError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from None


def _safe_call(callable_, *args, **kwargs):
    try:
        return callable_(*args, **kwargs)
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from None
    except (ValueError, security.AuthenticationError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None


def _session_response(principal: dict[str, Any]) -> dict[str, Any]:
    return {
        "access_token": security.create_session_token(
            principal["user_id"], principal["workspace_id"],
        ),
        "token_type": "bearer",
        "user": principal,
    }


def create_app() -> FastAPI:
    app = FastAPI(
        title="PharmaDrone AI",
        description="AI business-development assistant for pharmaceutical opportunity discovery",
        version="0.1.0",
        docs_url="/api/docs" if os.getenv("PHARMADRONE_AI_API_DOCS", "1") == "1" else None,
        redoc_url=None,
    )
    origins = [
        item.strip()
        for item in os.getenv("PHARMADRONE_AI_ALLOWED_ORIGINS", "http://localhost:8000").split(",")
        if item.strip()
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.get("/api/health")
    def health(conn=Depends(_connection)):
        version = conn.execute("SELECT MAX(version) AS version FROM schema_migrations").fetchone()
        return {
            "status": "healthy",
            "product": "PharmaDrone AI",
            "database_backend": conn.backend,
            "schema_version": int((version or {}).get("version") or 0),
        }

    @app.post("/api/auth/register", status_code=status.HTTP_201_CREATED)
    def register(payload: RegisterRequest, conn=Depends(_connection)):
        principal = _safe_call(
            repository.register,
            conn, payload.email, payload.password, payload.display_name, payload.workspace_name,
        )
        return _session_response(principal)

    @app.post("/api/auth/login")
    def login(payload: LoginRequest, conn=Depends(_connection)):
        try:
            principal = repository.authenticate(conn, payload.email, payload.password)
        except (ValueError, security.AuthenticationError) as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from None
        return _session_response(principal)

    @app.post("/api/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
    def logout(_: dict[str, Any] = Depends(_principal)):
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/api/auth/me")
    def me(principal: dict[str, Any] = Depends(_principal)):
        return principal

    @app.post("/api/ai/search-target-companies")
    def search_companies(
        payload: LeadRequest,
        _: dict[str, Any] = Depends(_principal),
        conn=Depends(_connection),
    ):
        return _safe_call(ai_bd_service.search_target_companies, payload.theme, payload.limit, conn=conn)

    @app.post("/api/ai/generate-bd-leads")
    def generate_leads(
        payload: LeadRequest,
        principal: dict[str, Any] = Depends(_principal),
        conn=Depends(_connection),
    ):
        result = _safe_call(
            ai_bd_service.generate_bd_leads,
            payload.theme, payload.company_filters, payload.limit, conn=conn,
        )
        repository.record_usage(conn, principal["user_id"], principal["workspace_id"], "lead-generation", {"theme": payload.theme})
        return result

    @app.post("/api/ai/build-company-pitch")
    def build_pitch(
        payload: PitchRequest,
        principal: dict[str, Any] = Depends(_principal),
        conn=Depends(_connection),
    ):
        result = _safe_call(
            ai_bd_service.build_company_pitch,
            payload.company, payload.theme, payload.case_type, conn=conn,
        )
        repository.record_usage(conn, principal["user_id"], principal["workspace_id"], "pitch-report", {"company": payload.company, "theme": payload.theme})
        return result

    @app.post("/api/ai/get-lead-evidence")
    def evidence(
        payload: PitchRequest,
        _: dict[str, Any] = Depends(_principal),
        conn=Depends(_connection),
    ):
        return _safe_call(ai_bd_service.get_lead_evidence, payload.company, payload.theme, conn=conn)

    @app.post("/api/ai/chat")
    def chat_message(
        payload: ChatRequest,
        principal: dict[str, Any] = Depends(_principal),
        conn=Depends(_connection),
    ):
        conversation_id = payload.conversation_id
        if conversation_id:
            # Ownership is checked by save_message before any content is persisted.
            pass
        else:
            conversation = repository.create_conversation(
                conn, principal["user_id"], principal["workspace_id"], payload.prompt[:80],
            )
            conversation_id = conversation["conversation_id"]
        repository.save_message(
            conn, principal["user_id"], principal["workspace_id"], conversation_id,
            "user", {"text": payload.prompt},
        )
        result = _safe_call(chat.handle_chat, payload.prompt, conn=conn, use_llm=payload.use_llm)
        repository.save_message(
            conn, principal["user_id"], principal["workspace_id"], conversation_id,
            "assistant", result,
        )
        repository.record_usage(
            conn, principal["user_id"], principal["workspace_id"], "chat-message",
            {"intent": result["intent"], "mode": result["mode"]},
        )
        return {"conversation_id": conversation_id, **result}

    @app.get("/api/ai/conversations")
    def conversations(
        limit: int = Query(default=30, ge=1, le=50),
        principal: dict[str, Any] = Depends(_principal),
        conn=Depends(_connection),
    ):
        return {"data": repository.list_conversations(conn, principal["user_id"], principal["workspace_id"], limit)}

    @app.post("/api/ai/conversations", status_code=status.HTTP_201_CREATED)
    def new_conversation(
        payload: ConversationRequest,
        principal: dict[str, Any] = Depends(_principal),
        conn=Depends(_connection),
    ):
        return repository.create_conversation(conn, principal["user_id"], principal["workspace_id"], payload.title)

    @app.get("/api/ai/conversations/{conversation_id}/messages")
    def messages(
        conversation_id: str,
        principal: dict[str, Any] = Depends(_principal),
        conn=Depends(_connection),
    ):
        return {"data": _safe_call(repository.list_messages, conn, principal["user_id"], principal["workspace_id"], conversation_id)}

    @app.post("/api/ai/save-lead", status_code=status.HTTP_201_CREATED)
    def save_lead(
        payload: SaveLeadRequest,
        principal: dict[str, Any] = Depends(_principal),
        conn=Depends(_connection),
    ):
        return _safe_call(ai_bd_service.save_lead, principal["user_id"], principal["workspace_id"], payload.lead, conn=conn)

    @app.post("/api/ai/save-report", status_code=status.HTTP_201_CREATED)
    def save_report(
        payload: SaveReportRequest,
        principal: dict[str, Any] = Depends(_principal),
        conn=Depends(_connection),
    ):
        return _safe_call(ai_bd_service.save_report, principal["user_id"], principal["workspace_id"], payload.report, conn=conn)

    @app.get("/api/ai/saved-leads")
    def saved_leads(
        principal: dict[str, Any] = Depends(_principal),
        conn=Depends(_connection),
    ):
        return {"data": repository.list_saved_leads(conn, principal["user_id"], principal["workspace_id"])}

    @app.delete("/api/ai/saved-leads/{saved_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_lead(
        saved_id: str,
        principal: dict[str, Any] = Depends(_principal),
        conn=Depends(_connection),
    ):
        deleted = repository.delete_saved(conn, "saas_saved_leads", "saved_lead_id", saved_id, principal["user_id"], principal["workspace_id"])
        if not deleted:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Saved lead was not found.")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/api/ai/saved-reports")
    def saved_reports(
        principal: dict[str, Any] = Depends(_principal),
        conn=Depends(_connection),
    ):
        return {"data": repository.list_saved_reports(conn, principal["user_id"], principal["workspace_id"])}

    @app.delete("/api/ai/saved-reports/{saved_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_report(
        saved_id: str,
        principal: dict[str, Any] = Depends(_principal),
        conn=Depends(_connection),
    ):
        deleted = repository.delete_saved(conn, "saas_saved_reports", "saved_report_id", saved_id, principal["user_id"], principal["workspace_id"])
        if not deleted:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Saved report was not found.")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.post("/api/ai/export-report")
    def export_report(
        payload: SaveReportRequest,
        principal: dict[str, Any] = Depends(_principal),
        conn=Depends(_connection),
    ):
        report = payload.report
        markdown = str(report.get("markdown_report") or "")
        if not markdown or len(markdown) > 500_000:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="A bounded Markdown report is required.")
        repository.record_usage(conn, principal["user_id"], principal["workspace_id"], "export", {"format": "markdown"})
        name = re.sub(r"[^a-z0-9]+", "-", str(report.get("report_title") or "pharmadrone-report").casefold()).strip("-")[:80]
        return Response(
            content=markdown,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{name or "pharmadrone-report"}.md"'},
        )

    @app.get("/api/billing/status")
    def billing(
        principal: dict[str, Any] = Depends(_principal),
        conn=Depends(_connection),
    ):
        return repository.billing_status(conn, principal["user_id"], principal["workspace_id"])

    assets = WEB_ROOT / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(WEB_ROOT / "index.html")

    return app


app = create_app()

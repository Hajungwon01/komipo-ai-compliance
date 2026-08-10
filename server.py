"""
AI 컴플라이언스 시뮬레이터 - FastAPI + HTML/CSS/JS 버전

실행: uvicorn server:app --reload
브라우저: http://localhost:8000  (자동으로 /app/login.html 로 이동)

Streamlit 버전(app.py)과 기능은 동일하다 (로그인/회원가입 → 가이드 → 챗봇),
비즈니스 로직 모듈(db, auth, rule_engine, ai_checker, chatbot, vector_store,
required_checklist)은 프레임워크에 무관하므로 그대로 재사용한다.
UI만 Streamlit 위젯 대신 정적 HTML/CSS/JS + REST API(JSON)로 교체했다.

세션 구조 (프로토타입 데모 수준):
- 브라우저에는 서명되지 않은 랜덤 세션ID만 httponly 쿠키로 저장한다.
- 실제 세션 데이터(로그인 유저, 대화 이력, 업로드 문서의 벡터DB 컬렉션 등)는
  서버 프로세스 메모리(dict)에만 보관한다 (Streamlit의 st.session_state와 동일한 수준의
  "서버 재시작하면 초기화되는 인메모리 상태"). 여러 브라우저 탭/기기에서 동시 데모할 때도
  탭별로 독립된 세션이 유지된다.
- 프로덕션으로 발전시킬 경우 Redis 등 외부 세션 스토어 + HTTPS + 서명된 쿠키로 교체 필요.
"""

import io
import json
import os
import uuid
from typing import Optional

import pdfplumber
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Response, UploadFile, File, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import ai_checker
import auth
import chatbot
import db
import required_checklist
import rule_engine
import vector_store

load_dotenv()

app = FastAPI(title="AI 컴플라이언스 시뮬레이터")

COOKIE_NAME = "kompliance_sid"
SESSIONS: dict[str, dict] = {}  # 서버 메모리에만 보관 (프로토타입 데모용, 위 docstring 참고)


# ── 세션 헬퍼 ──────────────────────────────────────────────────────
def _new_session() -> dict:
    return {
        "user": None,  # {"username":..., "plant_name":...}
        "messages": [],  # [{"role","kind":"text"|"upload","text":...,"upload":{...}}]
        "doc_context": None,  # {"rule_results","ai_results","filename","doc_collection","reg_collection"}
        "chroma_client": None,
        "reg_collection": None,
        "all_regulations": None,
        "regulations_error": None,
    }


def get_session(request: Request, response: Response) -> dict:
    sid = request.cookies.get(COOKIE_NAME)
    if sid is None or sid not in SESSIONS:
        sid = uuid.uuid4().hex
        SESSIONS[sid] = _new_session()
        response.set_cookie(COOKIE_NAME, sid, httponly=True, samesite="lax")
    return SESSIONS[sid]


def require_user(session: dict) -> dict:
    if session.get("user") is None:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    return session["user"]


def load_all_regulations(session: dict) -> list[dict]:
    """DB 규정 전체를 세션당 1회만 읽어온다 (회원가입 발전소 목록·챗봇 대조에 공용으로 씀)."""
    if session["all_regulations"] is None and session["regulations_error"] is None:
        try:
            session["all_regulations"] = db.fetch_regulations()
        except Exception as e:
            session["regulations_error"] = str(e)
    return session["all_regulations"] or []


def get_plant_options(session: dict) -> list[str]:
    regs = load_all_regulations(session)
    return sorted({r["plant_name"] for r in regs if r.get("plant_name")})


def extract_text(filename: str, content: bytes) -> str:
    if filename.lower().endswith(".txt"):
        return content.decode("utf-8", errors="ignore")
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


STATUS_ICON = {"위반 위험": "🔴", "기준 이내": "🟢", "미기재(확인 필요)": "🟡"}


def _serialize_rule_results(rule_results: list) -> list[dict]:
    out = []
    for r in rule_results:
        out.append(
            {
                "item_name": r.item_name,
                "status": r.status,
                "icon": STATUS_ICON[r.status],
                "extracted_value": r.extracted_value,
                "unit": r.unit,
                "limit_value": r.limit_value,
                "comparison": r.comparison,
                "note": r.note,
            }
        )
    return out


def _categorize_ai_results(ai_results: list) -> list[dict]:
    safety_names = set(required_checklist.SAFETY_MANAGEMENT_CHECKLIST) | set(
        required_checklist.LEGAL_COMPLIANCE_CHECKLIST
    )
    out = []
    for item in ai_results:
        out.append(
            {
                "item": item.get("item"),
                "present": item.get("present"),
                "reason": item.get("reason"),
                "category": "safety" if item.get("item") in safety_names else "env",
            }
        )
    return out


# ── 정적 프론트엔드 ─────────────────────────────────────────────────
app.mount("/app", StaticFiles(directory="static", html=True), name="static")


@app.get("/")
def root():
    return RedirectResponse(url="/app/login.html")


# ── 인증 API ───────────────────────────────────────────────────────
class SignupBody(BaseModel):
    username: str
    password: str
    plant_name: str


class LoginBody(BaseModel):
    username: str
    password: str


@app.get("/api/plants")
def api_plants(request: Request, response: Response):
    session = get_session(request, response)
    plants = get_plant_options(session)
    return {"plants": plants, "error": session["regulations_error"]}


@app.post("/api/signup")
def api_signup(body: SignupBody, request: Request, response: Response):
    session = get_session(request, response)
    plant_options = get_plant_options(session)
    if body.plant_name not in plant_options:
        return {"ok": False, "error": "유효한 발전소를 선택해주세요."}
    try:
        ok, err = auth.create_user(body.username, body.password, body.plant_name)
    except Exception as e:
        return {
            "ok": False,
            "error": f"회원가입 중 오류가 발생했어요. DB 파일 접근 권한이나 users 테이블 상태를 확인해주세요. ({e})",
        }
    return {"ok": ok, "error": err}


@app.post("/api/login")
def api_login(body: LoginBody, request: Request, response: Response):
    session = get_session(request, response)
    try:
        user = auth.authenticate_user(body.username, body.password)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"로그인 중 오류가 발생했어요. DB 파일 접근 권한이나 users 테이블 상태를 확인해주세요. ({e})",
        )
    if not user:
        raise HTTPException(status_code=401, detail="아이디 또는 비밀번호가 올바르지 않습니다.")

    session["user"] = user
    session["messages"] = []
    session["doc_context"] = None
    return {"ok": True, "user": user}


@app.post("/api/logout")
def api_logout(request: Request, response: Response):
    session = get_session(request, response)
    sid = request.cookies.get(COOKIE_NAME)
    if sid in SESSIONS:
        SESSIONS[sid] = _new_session()
    return {"ok": True}


@app.get("/api/me")
def api_me(request: Request, response: Response):
    session = get_session(request, response)
    if session["user"] is None:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    return session["user"]


# ── 챗봇 API ───────────────────────────────────────────────────────
@app.get("/api/messages")
def api_messages(request: Request, response: Response):
    session = get_session(request, response)
    user = require_user(session)

    if not session["messages"]:
        session["messages"] = [
            {
                "role": "assistant",
                "kind": "text",
                "text": (
                    f"안녕하세요, {user['username']}님! 저는 AI 컴플라이언스 시뮬레이터입니다.\n\n"
                    f"{user['plant_name']} 기준으로 검토할 준비가 됐어요. 아래에서 인허가 서류"
                    "(PDF 또는 TXT)를 올려주시면, 등록된 법령 기준과 자동으로 대조해서 위반 위험·"
                    "누락 항목을 찾아드릴게요.\n\n"
                    "⚠️ 이 결과는 참고용 사전 검토이며, 최종 판단은 담당자가 내려야 합니다."
                ),
            }
        ]
    return {"user": user, "messages": session["messages"]}


@app.post("/api/upload")
async def api_upload(request: Request, response: Response, file: UploadFile = File(...)):
    session = get_session(request, response)
    user = require_user(session)
    selected_plant = user["plant_name"]

    content = await file.read()
    session["messages"].append(
        {"role": "user", "kind": "text", "text": f"📎 서류를 업로드했어요: {file.filename}"}
    )

    text = extract_text(file.filename, content)

    if not text.strip():
        msg = {
            "role": "assistant",
            "kind": "text",
            "text": "문서에서 텍스트를 추출하지 못했어요. 스캔본(이미지) PDF는 이번 MVP에서는 지원하지 않습니다.",
        }
        session["messages"].append(msg)
        return {"ok": False, "messages": session["messages"]}

    all_regulations = load_all_regulations(session)

    if session["regulations_error"]:
        msg = {
            "role": "assistant",
            "kind": "text",
            "text": f"⚠️ DB 연결에 실패했어요. SQLite DB 파일 경로(SQLITE_DB_PATH)를 확인해주세요. 오류: {session['regulations_error']}",
        }
        session["messages"].append(msg)
        return {"ok": False, "messages": session["messages"]}

    if not all_regulations:
        msg = {
            "role": "assistant",
            "kind": "text",
            "text": "DB에 등록된 기준 규정이 없어요. db_schema.sql과 sample_data.sql을 먼저 sqlite3로 반영해주세요.",
        }
        session["messages"].append(msg)
        return {"ok": False, "messages": session["messages"]}

    # 로그인한 계정의 소속 발전소 기준 + 발전소에 국한되지 않는 공통 기준(plant_name이 NULL인 행)만 대조
    regulations = [
        r for r in all_regulations if r.get("plant_name") is None or r.get("plant_name") == selected_plant
    ]

    rule_results = rule_engine.check_document(text, regulations)

    openai_key = os.environ.get("OPENAI_API_KEY")
    required_items = [reg["item_name"] for reg in regulations if reg.get("is_required")]
    required_items += required_checklist.SAFETY_MANAGEMENT_CHECKLIST
    required_items += required_checklist.LEGAL_COMPLIANCE_CHECKLIST
    ai_results = ai_checker.check_missing_items(text, required_items, openai_key) if openai_key else []

    doc_collection = None
    reg_collection = session["reg_collection"]
    rag_note = ""
    rag_note_status = None
    if openai_key:
        try:
            if session["chroma_client"] is None:
                session["chroma_client"] = vector_store.get_chroma_client()
            if reg_collection is None:
                reg_collection = vector_store.build_regulation_collection(
                    session["chroma_client"], regulations, openai_key
                )
                session["reg_collection"] = reg_collection
            doc_collection = vector_store.build_document_collection(
                session["chroma_client"], file.filename, text, openai_key
            )
            n_chunks = doc_collection.count()
            rag_note = f"벡터DB에 {n_chunks}개 조각으로 색인 완료 — 후속 질문은 RAG 검색으로 답할게요."
            rag_note_status = "ok"
        except Exception as e:
            rag_note = f"벡터DB 색인에 실패해 후속 질문은 제한적으로만 답할 수 있어요. ({e})"
            rag_note_status = "warn"

    session["doc_context"] = {
        "rule_results": rule_results,
        "ai_results": ai_results,
        "filename": file.filename,
        "doc_collection": doc_collection,
        "reg_collection": reg_collection,
    }

    upload_payload = {
        "filename": file.filename,
        "text_length": len(text),
        "plant": selected_plant,
        "rule_results": _serialize_rule_results(rule_results),
        "ai_results": _categorize_ai_results(ai_results),
        "ai_results_available": bool(openai_key),
        "rag_note": rag_note,
        "rag_note_status": rag_note_status,
    }
    session["messages"].append({"role": "assistant", "kind": "upload", "upload": upload_payload})

    try:
        db.save_check_history(
            filename=file.filename,
            rule_result_json=json.dumps([r.__dict__ for r in rule_results], ensure_ascii=False),
            ai_result_json=json.dumps(ai_results, ensure_ascii=False),
        )
    except Exception:
        pass  # 이력 저장 실패는 데모 진행에 영향 없음

    return {"ok": True, "messages": session["messages"]}


class ChatBody(BaseModel):
    question: str


@app.post("/api/chat")
def api_chat(body: ChatBody, request: Request, response: Response):
    session = get_session(request, response)
    require_user(session)

    question = (body.question or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="질문을 입력해주세요.")

    session["messages"].append({"role": "user", "kind": "text", "text": question})

    if session["doc_context"] is None:
        reply = "아직 업로드된 서류가 없어요. 먼저 인허가 서류를 올려주시겠어요?"
    else:
        openai_key = os.environ.get("OPENAI_API_KEY")
        if not openai_key:
            reply = "OpenAI API 키가 설정되지 않아 채팅 답변을 드릴 수 없어요. .env에 OPENAI_API_KEY를 설정해주세요."
        else:
            history = [
                {"role": m["role"], "content": m.get("text", "")}
                for m in session["messages"][:-1]
                if m["role"] in ("user", "assistant") and m.get("kind") == "text"
            ]
            reply = chatbot.answer_followup(
                question=question,
                rule_results=session["doc_context"]["rule_results"],
                ai_results=session["doc_context"]["ai_results"],
                doc_collection=session["doc_context"]["doc_collection"],
                reg_collection=session["doc_context"]["reg_collection"],
                chat_history=history,
                api_key=openai_key,
            )

    session["messages"].append({"role": "assistant", "kind": "text", "text": reply})
    return {"reply": reply, "messages": session["messages"]}

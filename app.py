"""
AI 컴플라이언스 시뮬레이터 - 챗봇형 MVP 데모 (Streamlit)

실행: streamlit run app.py

화면 흐름 (발전소별 관리자 로그인):
1. 로그인 / 회원가입 (회원가입 시 소속 발전소 선택 → users 테이블에 저장)
2. 로그인 성공 → 서비스 가이드 화면 (상단 우측에 소속 발전소 표시)
3. "문서 업로드하고 검토 시작하기" 버튼 → 챗봇 화면으로 이동
4. 챗봇 화면: 인허가 서류(PDF/TXT) 업로드 → 규칙 기반 대조 → AI 누락항목 점검 →
   채팅으로 후속 질문("왜 위반이야?", "어떻게 고쳐?") 가능
"""

import json
import os

import pdfplumber
import streamlit as st
from dotenv import load_dotenv

import ai_checker
import auth
import chatbot
import db
import required_checklist
import rule_engine
import vector_store

load_dotenv()

st.set_page_config(page_title="AI 컴플라이언스 시뮬레이터", page_icon="🏭", layout="wide")

STATUS_ICON = {"위반 위험": "🔴", "기준 이내": "🟢", "미기재(확인 필요)": "🟡"}


def extract_text(uploaded_file) -> str:
    if uploaded_file.name.lower().endswith(".txt"):
        return uploaded_file.read().decode("utf-8", errors="ignore")
    with pdfplumber.open(uploaded_file) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


def format_rule_results_md(rule_results) -> str:
    lines = ["**① 규칙 기반 수치 대조 결과**", ""]
    for r in rule_results:
        icon = STATUS_ICON[r.status]
        value_str = f"{r.extracted_value} {r.unit}" if r.extracted_value is not None else "미기재"
        lines.append(f"- {icon} **{r.item_name}** — {value_str} (기준: {r.comparison} {r.limit_value} {r.unit})")
        if r.note:
            lines.append(f"  ↳ {r.note}")
    return "\n".join(lines)


def format_ai_results_md(ai_results) -> str:
    if not ai_results:
        return "**② AI 보조 - 필수 항목 누락 점검**\n\n(OpenAI API 키가 없어 건너뛰었습니다. `.env`를 확인하세요.)"

    import required_checklist as _rc

    def render(items):
        out = []
        for item in items:
            present = item.get("present")
            icon = "🟢" if present is True else ("🔴" if present is False else "⚪")
            out.append(f"- {icon} **{item.get('item')}** — {item.get('reason')}")
        return "\n".join(out)

    safety_names = set(_rc.SAFETY_MANAGEMENT_CHECKLIST) | set(_rc.LEGAL_COMPLIANCE_CHECKLIST)
    safety_items = [i for i in ai_results if i.get("item") in safety_names]
    env_items = [i for i in ai_results if i.get("item") not in safety_names]

    lines = ["**② AI 보조 - 필수 항목 누락 점검**", ""]
    if env_items:
        lines.append("_대기환경보전법·물환경보전법 관련 (배출기준 항목)_")
        lines.append(render(env_items))
        lines.append("")
    if safety_items:
        lines.append("_중대재해처벌법 관련 (안전관리계획 필수 항목)_")
        lines.append(render(safety_items))
    return "\n".join(lines)


# ── 세션 상태 초기화 ──────────────────────────────────────────
if "user" not in st.session_state:
    st.session_state.user = None  # 로그인 성공하면 {"username":..., "plant_name":...}
if "page" not in st.session_state:
    st.session_state.page = "login"  # "login" | "guide" | "chat"
if "messages" not in st.session_state:
    st.session_state.messages = []
if "last_uploaded_name" not in st.session_state:
    st.session_state.last_uploaded_name = None
if "doc_context" not in st.session_state:
    st.session_state.doc_context = None  # {"rule_results":..., "ai_results":..., "filename":..., "doc_collection":..., "reg_collection":...}
if "chroma_client" not in st.session_state:
    st.session_state.chroma_client = vector_store.get_chroma_client()
if "reg_collection" not in st.session_state:
    st.session_state.reg_collection = None  # 법령 컬렉션은 세션당 1회만 색인(재사용)
if "all_regulations" not in st.session_state:
    st.session_state.all_regulations = None  # DB에서 가져온 전체 규정 (세션당 1회 캐시)
if "regulations_error" not in st.session_state:
    st.session_state.regulations_error = None


def load_all_regulations():
    """DB에 등록된 전체 규정을 세션당 1회만 읽어온다 (회원가입 발전소 목록·챗봇 대조에 공용으로 씀)."""
    if st.session_state.all_regulations is None and st.session_state.regulations_error is None:
        try:
            st.session_state.all_regulations = db.fetch_regulations()
        except Exception as e:
            st.session_state.regulations_error = str(e)
    return st.session_state.all_regulations or []


def get_plant_options() -> list[str]:
    """
    회원가입 드롭다운에 쓸 발전소 목록. regulations 테이블의 plant_name을 그대로 쓴다
    (KOMIPO API·CleanSYS로 실제 조회된 발전소가 자동으로 늘어나므로 하드코딩하지 않는다).
    ⚠️ 두 API가 같은 발전소를 다른 이름으로 등록하는 경우가 있다(예: "보령화력" vs "보령발전본부") —
    README.md "두 번째 실제 데이터 소스" 절 참고.
    """
    regs = load_all_regulations()
    return sorted({r["plant_name"] for r in regs if r.get("plant_name")})


# ── 페이지 1: 로그인 / 회원가입 ──────────────────────────────────
def render_login_page():
    st.title("🏭 AI 컴플라이언스 시뮬레이터")
    st.caption("발전소별 관리자 로그인")

    tab_login, tab_signup = st.tabs(["🔐 로그인", "📝 회원가입"])

    with tab_login:
        with st.form("login_form"):
            username = st.text_input("아이디")
            password = st.text_input("비밀번호", type="password")
            submitted = st.form_submit_button("로그인", type="primary", use_container_width=True)
        if submitted:
            try:
                user = auth.authenticate_user(username, password)
            except Exception as e:
                user = None
                st.error(f"로그인 중 오류가 발생했어요. DB 파일 접근 권한이나 users 테이블 상태를 확인해주세요.\n\n`{e}`")
            else:
                if user:
                    st.session_state.user = user
                    st.session_state.page = "guide"
                    st.rerun()
                else:
                    st.error("아이디 또는 비밀번호가 올바르지 않습니다.")

    with tab_signup:
        plant_options = get_plant_options()
        if st.session_state.regulations_error:
            st.warning(
                f"⚠️ DB 연결에 실패해 발전소 목록을 못 불러왔어요. SQLite DB 파일 경로를 확인해주세요.\n\n"
                f"오류: `{st.session_state.regulations_error}`"
            )
        elif not plant_options:
            st.info(
                "아직 DB에 발전소별 기준이 없어요. `refresh_komipo_data.py` 또는 "
                "`refresh_cleansys_data.py`를 먼저 실행해 발전소 데이터를 채워주세요."
            )

        with st.form("signup_form"):
            new_username = st.text_input("아이디", key="signup_username")
            new_password = st.text_input("비밀번호 (4자 이상)", type="password", key="signup_password")
            new_plant = (
                st.selectbox("소속 발전소", plant_options, key="signup_plant")
                if plant_options
                else None
            )
            signup_submitted = st.form_submit_button(
                "회원가입", use_container_width=True, disabled=not plant_options
            )
        if signup_submitted:
            try:
                ok, err = auth.create_user(new_username, new_password, new_plant)
            except Exception as e:
                ok, err = False, f"회원가입 중 오류가 발생했어요. DB 파일 접근 권한이나 users 테이블 상태를 확인해주세요.\n\n`{e}`"
            if ok:
                st.success("회원가입 완료! 위 '로그인' 탭에서 로그인해주세요.")
            else:
                st.error(err)


# ── 페이지 2: 서비스 가이드 ───────────────────────────────────────
def render_guide_page():
    top_left, top_right = st.columns([4, 1])
    with top_right:
        st.markdown(f"🏭 **{st.session_state.user['plant_name']}**")
        st.caption(f"{st.session_state.user['username']}님으로 로그인됨")
        if st.button("로그아웃", use_container_width=True):
            st.session_state.user = None
            st.session_state.page = "login"
            st.session_state.messages = []
            st.session_state.doc_context = None
            st.session_state.last_uploaded_name = None
            st.rerun()
    with top_left:
        st.title("🏭 AI 컴플라이언스 시뮬레이터")
        st.caption("발전소 인허가 서류를 법령 기준과 자동 대조하는 대화형 사전 검토 도우미 (MVP 데모)")

    st.divider()
    st.subheader("서비스 안내")
    st.markdown(
        "발전소 건설·정비 시 제출하는 인허가서류·환경영향평가서·안전계획서는 수백 페이지 규모이고, "
        "대기환경보전법·물환경보전법·중대재해처벌법 등 관련 법령이 수시로 개정됩니다. "
        "이 서비스는 서류를 업로드하면 등록된 법령·실제 허가기준과 자동으로 대조해 위반 위험과 "
        "누락 항목을 사전에 찾아드립니다."
    )
    col1, col2 = st.columns(2)
    with col1:
        st.markdown(
            "**① 규칙 기반 수치 대조**\n\n"
            "배출허용치처럼 숫자로 딱 떨어지는 기준(먼지·질소산화물·황산화물·BOD 등)은 "
            "규칙 엔진으로 정확하게 대조합니다 (AI 환각 위험 배제)."
        )
    with col2:
        st.markdown(
            "**② AI 보조 필수 항목 점검**\n\n"
            "중대재해처벌법 안전관리체계처럼 문맥 판단이 필요한 항목은 AI가 서류에 "
            "실제로 포함돼 있는지 확인합니다."
        )
    st.markdown(
        f"검토 결과가 나온 뒤에는 채팅으로 \"왜 위반이야?\", \"어떻게 고치면 돼?\" 같은 후속 질문도 "
        f"할 수 있어요. 현재 **{st.session_state.user['plant_name']}** 소속으로 로그인돼 있어, "
        "해당 발전소 기준(+ BOD·중대재해처벌법처럼 발전소 공통 기준)으로 서류를 대조합니다."
    )
    st.warning("⚠️ 이 결과는 참고용 사전 검토이며, 최종 판단은 담당자가 내려야 합니다.")

    st.divider()
    if st.button("📎 문서 업로드하고 검토 시작하기", type="primary", use_container_width=True):
        st.session_state.page = "chat"
        st.rerun()


# ── 페이지 3: 챗봇 ────────────────────────────────────────────────
def render_chat_page():
    all_regulations = load_all_regulations()
    selected_plant = st.session_state.user["plant_name"]

    with st.sidebar:
        st.markdown(f"🏭 **{selected_plant}**")
        st.caption(f"{st.session_state.user['username']}님으로 로그인됨")
        nav_col1, nav_col2 = st.columns(2)
        with nav_col1:
            if st.button("📖 가이드", use_container_width=True):
                st.session_state.page = "guide"
                st.rerun()
        with nav_col2:
            if st.button("로그아웃", use_container_width=True):
                st.session_state.user = None
                st.session_state.page = "login"
                st.session_state.messages = []
                st.session_state.doc_context = None
                st.session_state.last_uploaded_name = None
                st.rerun()

        st.divider()
        st.header("📎 서류 업로드")
        uploaded_file = st.file_uploader("인허가 서류 (PDF 또는 TXT)", type=["pdf", "txt"])
        st.caption(
            f"이 발전소({selected_plant})의 배출기준 + BOD·중대재해처벌법처럼 발전소에 "
            "국한되지 않는 공통 기준을 함께 대조합니다."
        )
        st.divider()
        with st.expander("ℹ️ 데모 안내"):
            st.markdown(
                "- 등록된 배출기준 수치는 검증 수준이 항목마다 다른 **데모용 데이터**입니다.\n"
                "- 정규식 기반 추출이라 서류 서식에 따라 인식이 안 될 수 있습니다.\n"
                "- 법적 효력이 있는 검토 결과가 아닙니다."
            )

    if not st.session_state.messages:
        st.session_state.messages = [
            {
                "role": "assistant",
                "content": (
                    f"안녕하세요, {st.session_state.user['username']}님! 저는 **AI 컴플라이언스 시뮬레이터**입니다. 🏭\n\n"
                    f"**{selected_plant}** 기준으로 검토할 준비가 됐어요. 왼쪽 사이드바에서 인허가 서류"
                    "(PDF 또는 TXT)를 올려주시면, 등록된 법령 기준과 자동으로 대조해서 위반 위험·"
                    "누락 항목을 찾아드릴게요.\n\n"
                    "⚠️ 이 결과는 참고용 사전 검토이며, 최종 판단은 담당자가 내려야 합니다."
                ),
            }
        ]

    st.title("🏭 AI 컴플라이언스 시뮬레이터")
    st.caption(f"발전소: {selected_plant}")

    # ── 새 파일 업로드 처리 ───────────────────────────────────────
    if uploaded_file is not None and uploaded_file.name != st.session_state.last_uploaded_name:
        st.session_state.last_uploaded_name = uploaded_file.name
        st.session_state.messages.append({"role": "user", "content": f"📎 서류를 업로드했어요: `{uploaded_file.name}`"})

        text = extract_text(uploaded_file)

        if not text.strip():
            st.session_state.messages.append(
                {
                    "role": "assistant",
                    "content": "문서에서 텍스트를 추출하지 못했어요. 스캔본(이미지) PDF는 이번 MVP에서는 지원하지 않습니다.",
                }
            )
        elif st.session_state.regulations_error:
            st.session_state.messages.append(
                {
                    "role": "assistant",
                    "content": f"⚠️ DB 연결에 실패했어요. SQLite DB 파일 경로를 확인해주세요.\n\n오류: `{st.session_state.regulations_error}`",
                }
            )
        elif not all_regulations:
            st.session_state.messages.append(
                {
                    "role": "assistant",
                    "content": "DB에 등록된 기준 규정이 없어요. `db_schema.sql`과 `sample_data.sql`을 먼저 sqlite3로 반영해주세요.",
                }
            )
        else:
            # 로그인한 계정의 소속 발전소 기준 + 발전소에 국한되지 않는 공통 기준(plant_name이 NULL인 행,
            # 예: BOD)만 골라 대조. 이렇게 안 하면 여러 발전소 데이터가 섞여 "같은 항목이 수십 번
            # 반복 출력"되는 문제가 재발한다.
            regulations = [
                r for r in all_regulations if r.get("plant_name") is None or r.get("plant_name") == selected_plant
            ]

            rule_results = rule_engine.check_document(text, regulations)

            openai_key = os.environ.get("OPENAI_API_KEY")
            # 대기/물환경보전법(숫자 기준 항목, DB) + 중대재해처벌법(체계·절차 항목, 별도 체크리스트)
            # 둘 다 AI 누락항목 점검으로 넘긴다 — 전자는 사실 rule_engine이 이미 수치로 대조하지만
            # "항목이 아예 서류에 없는지"는 AI 쪽이 문맥으로 더 잘 잡아내므로 중복 점검이 의미 있다.
            required_items = [reg["item_name"] for reg in regulations if reg.get("is_required")]
            required_items += required_checklist.SAFETY_MANAGEMENT_CHECKLIST
            required_items += required_checklist.LEGAL_COMPLIANCE_CHECKLIST
            ai_results = (
                ai_checker.check_missing_items(text, required_items, openai_key) if openai_key else []
            )

            doc_collection = None
            reg_collection = st.session_state.reg_collection
            rag_note = ""
            if openai_key:
                try:
                    # 법령 컬렉션은 세션당 1회만 색인해 재사용 (규정이 바뀌지 않는 한 재임베딩 불필요)
                    if reg_collection is None:
                        reg_collection = vector_store.build_regulation_collection(
                            st.session_state.chroma_client, regulations, openai_key
                        )
                        st.session_state.reg_collection = reg_collection

                    # 문서 컬렉션은 업로드마다 새로 색인
                    doc_collection = vector_store.build_document_collection(
                        st.session_state.chroma_client, uploaded_file.name, text, openai_key
                    )
                    n_chunks = doc_collection.count()
                    rag_note = f"\n\n🔎 벡터DB에 {n_chunks}개 조각으로 색인 완료 — 후속 질문은 RAG 검색으로 답할게요."
                except Exception as e:
                    rag_note = f"\n\n⚠️ 벡터DB 색인에 실패해 후속 질문은 제한적으로만 답할 수 있어요. (`{e}`)"

            st.session_state.doc_context = {
                "rule_results": rule_results,
                "ai_results": ai_results,
                "filename": uploaded_file.name,
                "doc_collection": doc_collection,
                "reg_collection": reg_collection,
            }

            reply = (
                f"텍스트 추출 완료 ({len(text)}자). 검토 결과를 알려드릴게요.\n\n"
                f"발전소: **{selected_plant}**\n\n"
                + format_rule_results_md(rule_results)
                + "\n\n"
                + format_ai_results_md(ai_results)
                + rag_note
                + "\n\n더 궁금한 점이 있으면 채팅으로 편하게 물어보세요. (예: \"왜 위반이야?\", \"어떻게 고치면 돼?\")"
            )
            st.session_state.messages.append({"role": "assistant", "content": reply})

            try:
                db.save_check_history(
                    filename=uploaded_file.name,
                    rule_result_json=json.dumps([r.__dict__ for r in rule_results], ensure_ascii=False),
                    ai_result_json=json.dumps(ai_results, ensure_ascii=False),
                )
            except Exception:
                pass  # 이력 저장 실패는 데모 진행에 영향 없음

    # ── 대화 이력 렌더링 ──────────────────────────────────────────
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"], avatar="🏭" if msg["role"] == "assistant" else None):
            st.markdown(msg["content"])

    # ── 채팅 입력 (후속 질문) ─────────────────────────────────────
    if question := st.chat_input("검토 결과에 대해 궁금한 점을 물어보세요..."):
        st.session_state.messages.append({"role": "user", "content": question})
        with st.chat_message("user"):
            st.markdown(question)

        with st.chat_message("assistant", avatar="🏭"):
            if st.session_state.doc_context is None:
                reply = "아직 업로드된 서류가 없어요. 먼저 사이드바에서 인허가 서류를 올려주시겠어요?"
                st.markdown(reply)
            else:
                openai_key = os.environ.get("OPENAI_API_KEY")
                if not openai_key:
                    reply = "OpenAI API 키가 설정되지 않아 채팅 답변을 드릴 수 없어요. `.env`에 `OPENAI_API_KEY`를 설정해주세요."
                    st.markdown(reply)
                else:
                    with st.spinner("벡터DB에서 관련 내용을 검색하는 중..."):
                        history = [
                            {"role": m["role"], "content": m["content"]}
                            for m in st.session_state.messages[:-1]
                            if m["role"] in ("user", "assistant")
                        ]
                        reply = chatbot.answer_followup(
                            question=question,
                            rule_results=st.session_state.doc_context["rule_results"],
                            ai_results=st.session_state.doc_context["ai_results"],
                            doc_collection=st.session_state.doc_context["doc_collection"],
                            reg_collection=st.session_state.doc_context["reg_collection"],
                            chat_history=history,
                            api_key=openai_key,
                        )
                    st.markdown(reply)

        st.session_state.messages.append({"role": "assistant", "content": reply})


# ── 라우팅 ─────────────────────────────────────────────────────
if st.session_state.user is None:
    render_login_page()
elif st.session_state.page == "guide":
    render_guide_page()
else:
    render_chat_page()

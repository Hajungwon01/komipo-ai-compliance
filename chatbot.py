"""
챗봇 후속 질의응답 모듈 (RAG 버전)

규칙 엔진의 정형 결과(작은 데이터라 항상 전체 포함)에,
벡터DB(Chroma) 검색으로 찾은 "질문과 관련된 문서 조각 + 관련 법령 조각"만
추가로 붙여서 LLM에 넘긴다. 문서 전체를 매번 프롬프트에 넣지 않으므로
수백 페이지 서류로 확장해도 컨텍스트가 폭발하지 않는 구조.

핵심 원칙: 챗봇이 검색되지 않은 법적 수치를 스스로 지어내지 않도록,
시스템 프롬프트에서 "검색된 컨텍스트 밖의 내용은 모른다고 답하라"를 명시한다.
"""

import os
from typing import Optional

from openai import OpenAI

import vector_store

MODEL_NAME = "gpt-4o-mini"

SYSTEM_PROMPT = """당신은 'AI 컴플라이언스 시뮬레이터'의 대화형 도우미입니다.
발전소 인허가 서류를 검토한 결과를 바탕으로, 사용자의 후속 질문에 친절하고 간결하게 답합니다.

규칙:
1. [규칙 기반 대조 결과]와 [검색된 관련 조각] 안의 정보만 근거로 답하세요.
2. 검색된 조각 밖의 법령 수치나 조항을 절대 새로 지어내지 마세요. 근거가 없으면 "검색된 내용에는 없어 확인이 필요하다"고 답하세요.
3. 이 시스템의 판정은 참고용 사전 검토이며 법적 효력이 없다는 점을 필요할 때 상기시키세요.
4. 답변은 2~5문장 정도로 간결하게, 필요하면 항목을 나열하세요.
"""


def _format_rule_results(rule_results: list) -> str:
    lines = []
    for r in rule_results:
        lines.append(
            f"- {r.item_name}: 상태={r.status}, 추출값={r.extracted_value} {r.unit}, "
            f"기준={r.comparison} {r.limit_value} {r.unit}, 비고={r.note}"
        )
    return "\n".join(lines)


def answer_followup(
    question: str,
    rule_results: list,
    ai_results: list,
    doc_collection=None,
    reg_collection=None,
    chat_history: Optional[list] = None,
    api_key: Optional[str] = None,
    top_k: int = 4,
) -> str:
    """
    question: 사용자의 후속 질문
    rule_results / ai_results: 이번 세션에서 이미 계산된 검토 결과 (항상 포함, 작은 데이터)
    doc_collection / reg_collection: vector_store로 만든 Chroma 컬렉션 (RAG 검색 대상)
    chat_history: [{"role": "user"|"assistant", "content": "..."}] 형태의 이전 대화 (선택)
    """
    client = OpenAI(api_key=api_key or os.environ.get("OPENAI_API_KEY"))

    # 1) RAG 검색: 질문과 관련된 문서 조각 / 법령 조각만 추출
    doc_chunks = vector_store.retrieve(doc_collection, question, api_key, k=top_k)
    reg_chunks = vector_store.retrieve(reg_collection, question, api_key, k=top_k)

    context_parts = ["[규칙 기반 대조 결과 (전체)]", _format_rule_results(rule_results)]

    if ai_results:
        context_parts.append("\n[AI 필수항목 누락 점검 결과 (전체)]")
        for item in ai_results:
            context_parts.append(f"- {item.get('item')}: present={item.get('present')}, reason={item.get('reason')}")

    context_parts.append(f"\n[검색된 관련 서류 조각 (질문 기반 top-{top_k})]")
    context_parts.append("\n---\n".join(doc_chunks) if doc_chunks else "(관련 조각을 찾지 못했습니다)")

    context_parts.append(f"\n[검색된 관련 법령 조각 (질문 기반 top-{top_k})]")
    context_parts.append("\n---\n".join(reg_chunks) if reg_chunks else "(관련 조각을 찾지 못했습니다)")

    context_block = "\n".join(context_parts)

    messages = [{"role": "system", "content": SYSTEM_PROMPT + "\n\n" + context_block}]

    if chat_history:
        messages.extend(chat_history[-6:])  # 컨텍스트 과다 방지를 위해 최근 6턴만 포함

    messages.append({"role": "user", "content": question})

    response = client.chat.completions.create(
        model=MODEL_NAME,
        messages=messages,
        temperature=0.3,
    )

    return response.choices[0].message.content.strip()

"""
LLM 기반 누락 항목 체커 (MVP)

규칙 엔진은 "숫자 기준"을 정확히 대조하는 역할이고,
이 모듈은 "서류에 반드시 들어가야 할 항목이 문맥상 빠져있는지"를
OpenAI API로 보조 판단한다.

MVP 단계에서는 법령 조항 수가 적으므로 별도 벡터DB(RAG) 없이
필수 항목 목록을 프롬프트에 직접 넣는 방식으로 단순화했다.
법령이 늘어나면 Chroma/FAISS로 관련 조항만 검색해 넣는 구조로 확장하면 된다.
"""

import json
import os
from typing import Optional
from openai import OpenAI

MODEL_NAME = "gpt-4o-mini"

SYSTEM_PROMPT = """당신은 발전소 환경·안전 인허가 서류를 검토하는 보조 심사관입니다.
아래 [필수 기재 항목 목록]을 기준으로, [서류 내용]에 각 항목이 실제로 포함되어 있는지 판단하세요.

규칙:
- 항목이 명시적으로 언급되지 않았다면 "누락 의심"으로 표시하세요.
- 최종 법적 판단이 아니라 "검토자가 다시 확인해야 할 후보"를 제시하는 것이 목적입니다.
- 반드시 JSON 배열로만 답하세요. 다른 설명 문장을 붙이지 마세요.

출력 형식 (JSON 배열):
[
  {"item": "항목명", "present": true/false, "reason": "판단 근거 한 문장"}
]
"""


def check_missing_items(document_text: str, required_items: list[str], api_key: Optional[str] = None) -> list[dict]:
    """
    document_text: 서류에서 추출한 텍스트
    required_items: 필수 항목명 리스트 (예: DB의 is_required=1 항목들)
    api_key: 미지정 시 환경변수 OPENAI_API_KEY 사용
    """
    if not required_items:
        return []

    client = OpenAI(api_key=api_key or os.environ.get("OPENAI_API_KEY"))

    user_prompt = f"""[필수 기재 항목 목록]
{json.dumps(required_items, ensure_ascii=False)}

[서류 내용]
{document_text[:8000]}
"""
    # 문서가 매우 길 경우를 대비해 앞부분 8000자만 사용 (MVP 한계, 추후 청크 분할 필요)

    response = client.chat.completions.create(
        model=MODEL_NAME,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0,
    )

    content = response.choices[0].message.content.strip()

    # 모델이 코드블록(```json ... ```)으로 감싸는 경우 대비
    if content.startswith("```"):
        content = content.strip("`")
        content = content.split("\n", 1)[1] if "\n" in content else content
        if content.endswith("json"):
            content = content[:-4]

    try:
        parsed = json.loads(content)
        if isinstance(parsed, dict) and "items" in parsed:
            parsed = parsed["items"]
        return parsed
    except json.JSONDecodeError:
        # 파싱 실패 시 원문을 그대로 보여줘서 디버깅 가능하게 함
        return [{"item": "(파싱 실패)", "present": None, "reason": content}]

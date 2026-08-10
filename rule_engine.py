"""
규칙 기반 수치 대조 엔진 (MVP)

동작 방식:
1. 서류 텍스트에서 "키워드 ... 숫자 단위" 패턴을 정규식으로 탐색
   (예: "먼지 배출농도: 18.5 mg/Sm3")
2. DB(regulations 테이블)에 저장된 기준값과 비교해 초과 여부 판단
3. 키워드 자체가 문서에 아예 없으면 "미기재(누락 의심)"로 표시

주의: 정규식 기반 추출은 문서 서식에 따라 인식률이 달라질 수 있음.
실제 서비스로 발전시킬 때는 표(table) 파싱, 문서 구조 인식 등을 추가해야 함.
"""

import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class RuleCheckResult:
    item_name: str
    keyword: str
    found: bool
    extracted_value: Optional[float]
    unit: str
    limit_value: float
    comparison: str
    status: str          # "위반 위험" / "기준 이내" / "미기재(확인 필요)"
    note: str = ""


def _build_pattern(keyword: str) -> re.Pattern:
    """
    "먼지 ... 18.5 mg/Sm3" 같은 문장에서 키워드 뒤에 나오는
    첫 번째 숫자(소수 포함)를 잡아내는 패턴.
    키워드와 숫자 사이에 콜론, 공백, '농도', '배출' 등의 텍스트가 있어도 허용.
    """
    escaped = re.escape(keyword)
    return re.compile(
        rf"{escaped}[^\d\n]{{0,20}}?([0-9]+(?:\.[0-9]+)?)\s*(mg/Sm3|mg/Nm3|mg/L|ppm)?",
        re.IGNORECASE,
    )


def check_document(text: str, regulations: list[dict]) -> list[RuleCheckResult]:
    """
    text: 서류에서 추출한 전체 텍스트
    regulations: DB에서 읽어온 규정 목록
        [{item_name, keyword, limit_value, unit, comparison, is_required, note}, ...]
    """
    results: list[RuleCheckResult] = []

    for reg in regulations:
        pattern = _build_pattern(reg["keyword"])
        match = pattern.search(text)

        if not match:
            results.append(
                RuleCheckResult(
                    item_name=reg["item_name"],
                    keyword=reg["keyword"],
                    found=False,
                    extracted_value=None,
                    unit=reg["unit"],
                    limit_value=float(reg["limit_value"]),
                    comparison=reg["comparison"],
                    status="미기재(확인 필요)",
                    note="문서에서 해당 항목을 찾지 못했습니다. 실제로 누락됐는지 직접 확인하세요.",
                )
            )
            continue

        value = float(match.group(1))
        limit = float(reg["limit_value"])
        comparison = reg["comparison"]

        if comparison == "<=":
            ok = value <= limit
        else:
            ok = value >= limit

        status = "기준 이내" if ok else "위반 위험"
        note = reg.get("note", "")

        results.append(
            RuleCheckResult(
                item_name=reg["item_name"],
                keyword=reg["keyword"],
                found=True,
                extracted_value=value,
                unit=reg["unit"],
                limit_value=limit,
                comparison=comparison,
                status=status,
                note=note,
            )
        )

    return results

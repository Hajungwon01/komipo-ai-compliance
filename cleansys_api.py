"""
한국환경공단 "굴뚝자동측정기기(TMS) 측정결과" 공식 공공데이터 API 연동 모듈

komipo_api.py가 KOMIPO 자체 API(대기오염물질배출 조회서비스, 3개 항목: 먼지/SOx/NOx, 월별)라면,
이 모듈은 환경부 산하 한국환경공단이 운영하는 전국 TMS(굴뚝자동측정기기) 부착 사업장 실시간
공개시스템 "CleanSYS"의 오픈API를 사용한다. 두 API는 서로 다른 기관이 독립적으로 운영하므로,
같은 발전소의 기준치를 두 소스로 교차 검증할 수 있다는 장점이 있다.

- API명: 한국환경공단_굴뚝자동측정기기 측정결과
- 상세페이지: https://www.data.go.kr/data/15074658/openapi.do
- 엔드포인트: https://apis.data.go.kr/B552584/cleansys/rltmMesureResult
- 관리기관: 한국환경공단 (사업장대기처 사업장대기통계부)

이 API는 "사업장명"으로 LIKE 검색이 가능해서, org_no 같은 코드 없이 "보령", "신서천" 같은
발전소명 일부만으로 KOMIPO 여러 발전소를 각각 조회할 수 있다. 대기오염물질 7개 항목(먼지,
황산화물, 질소산화물, 염화수소, 불화수소, 암모니아, 일산화탄소)의 "허용기준"과 "실시간 측정값"을
동시에 제공한다 — 즉 komipo_api.py보다 더 많은 오염물질을, 더 많은 KOMIPO 발전소에 대해 받아올 수
있다 (단, TMS가 부착된 대형 사업장만 대상이라 소규모 설비는 조회되지 않을 수 있음).

사용 전 준비물:
1. https://www.data.go.kr 회원가입 (KOMIPO API와 같은 계정 사용 가능)
2. 위 API 상세페이지에서 "활용신청" (개발계정은 즉시 자동승인, 개발계정 트래픽 5,000건/일)
3. 마이페이지 > 데이터활용 > Open API > 활용신청 현황에서 발급된 "일반 인증키(Decoding)"를
   .env의 CLEANSYS_API_KEY에 입력 (komipo_api.py와 마찬가지로 Encoding 버전을 넣으면 403 오류)
   (KOMIPO_API_KEY와는 별개의 신청이 필요 — data.go.kr에서 API마다 개별 활용신청)

주의: 계정 생성·API 신청은 사용자 본인이 직접 해야 합니다 (Claude가 대신 가입하지 않음).

✅ 2026-08-06에 data.go.kr 상세페이지(요청/응답 변수표)를 직접 다시 확인해, 아래 요청 파라미터명
(serviceKey/areaNm/factManageNm/stackCode/type)과 응답 필드명(fact_manage_nm, tsp_exhst_perm_stdr_value 등)이
이 모듈의 구현과 정확히 일치함을 검증했습니다. 참고로 이 API는 먼지·황산화물·질소산화물·염화수소 외에도
불화수소(hf)·암모니아(nh3)·일산화탄소(co) 측정값도 제공하지만, 이 프로젝트가 다루는 3개 법령(대기환경보전법·
물환경보전법·중대재해처벌법)의 핵심 항목이 아니라서 이 모듈에는 포함하지 않았습니다.
"""

import os
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://apis.data.go.kr/B552584/cleansys/rltmMesureResult"

# API 응답 필드가 실제로 어떤 단위를 쓰는지 기술문서에 명시돼 있지 않아, 대기환경보전법에서
# 일반적으로 쓰는 단위로 가정했다. 실사용 전 실제 응답값으로 재검증 필요.
UNITS = {
    "먼지": "mg/Sm3",
    "황산화물": "ppm",
    "질소산화물": "ppm",
    "염화수소": "ppm",
}


def fetch_measurements_raw(
    service_key: str,
    fact_manage_nm: Optional[str] = None,
    area_nm: Optional[str] = None,
    stack_code: Optional[str] = None,
    timeout: int = 10,
) -> dict:
    """
    fact_manage_nm: 사업장명 (LIKE 검색, 예: '보령', '신서천') — 비우면 전체 사업장 조회
    area_nm: 지역명 (LIKE 검색, 예: '충남')
    stack_code: 배출구 번호
    반환값: JSON 파싱된 dict (type=json으로 요청)
    """
    params = {"serviceKey": service_key, "type": "json"}
    if fact_manage_nm:
        params["factManageNm"] = fact_manage_nm
    if area_nm:
        params["areaNm"] = area_nm
    if stack_code:
        params["stackCode"] = stack_code

    resp = requests.get(BASE_URL, params=params, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def parse_measurements(response_json: dict) -> list[dict]:
    """
    CleanSYS API 응답을 레코드 리스트로 변환한다. 공공데이터포털 표준 스펙이
    {"response": {"body": {"items": [...]}}} 형태이거나, 이 API처럼 최상위에 바로
    items가 오는 경우도 있어 최대한 관대하게 파싱한다.
    """
    # 여러 가능한 래핑 구조를 순서대로 시도
    items = None
    for path in (
        lambda d: d["response"]["body"]["items"],
        lambda d: d["body"]["items"],
        lambda d: d["items"],
    ):
        try:
            items = path(response_json)
            break
        except (KeyError, TypeError):
            continue
    if items is None:
        items = []
    if isinstance(items, dict):  # 결과가 1건이면 dict로 오는 경우 대비
        items = [items]

    records = []
    for item in items:
        def get(key, default=None):
            return item.get(key, default)

        records.append(
            {
                "사업장명": get("fact_manage_nm") or get("factManageNm"),
                "지역명": get("area_nm") or get("areaNm"),
                "배출구": get("stack_code") or get("stackCode"),
                "측정시간": get("mesure_dt") or get("mesureDt"),
                "먼지_허용기준": get("tsp_exhst_perm_stdr_value"),
                "먼지_측정값": get("tsp_mesure_value"),
                "황산화물_허용기준": get("sox_exhst_perm_stdr_value"),
                "황산화물_측정값": get("sox_mesure_value"),
                "질소산화물_허용기준": get("nox_exhst_perm_stdr_value"),
                "질소산화물_측정값": get("nox_mesure_value"),
                "염화수소_허용기준": get("hcl_exhst_perm_stdr_value"),
                "염화수소_측정값": get("hcl_mesure_value"),
            }
        )
    return records


def _latest_per_plant_stack(records: list[dict]) -> list[dict]:
    """(사업장명, 배출구)별로 가장 최근 측정시간 1건만 남긴다 (komipo_api._latest_per_unit과 같은 이유)."""
    latest: dict[tuple, dict] = {}
    for r in records:
        key = (r.get("사업장명"), r.get("배출구"))
        dt = r.get("측정시간") or ""
        if key not in latest or dt > (latest[key].get("측정시간") or ""):
            latest[key] = r
    return list(latest.values())


def to_regulations(records: list[dict], dedupe: bool = True) -> list[dict]:
    """CleanSYS 레코드를 rule_engine.check_document()가 기대하는 regulations 형식으로 변환."""
    if dedupe:
        records = _latest_per_plant_stack(records)

    regulations = []
    for r in records:
        # CleanSYS 사업장명은 "한국중부발전㈜보령발전본부"처럼 상호명이 앞에 붙는데, 사이드바
        # 드롭다운을 KOMIPO API 쪽("보령화력" 등)과 나란히 보여줄 때 너무 길고 중복돼 보여서
        # 상호명 접두어만 뗀다. (완전히 같은 이름으로 합치진 않는다 — "발전본부"(CleanSYS,
        # 행정 단위)와 "화력"(KOMIPO API, 설비 단위)은 지칭 범위가 달라 임의로 통일하면 오히려
        # 서로 다른 데이터를 같은 발전소인 것처럼 잘못 합치는 위험이 있다.)
        raw_plant = r.get("사업장명", "")
        plant = raw_plant.replace("한국중부발전㈜", "").strip()
        stack = r.get("배출구", "")
        label = f"{plant} {stack}배출구".strip() if stack else plant

        for item_name, limit_key, measured_key in (
            ("먼지", "먼지_허용기준", "먼지_측정값"),
            ("황산화물", "황산화물_허용기준", "황산화물_측정값"),
            ("질소산화물", "질소산화물_허용기준", "질소산화물_측정값"),
            ("염화수소", "염화수소_허용기준", "염화수소_측정값"),
        ):
            limit_val = r.get(limit_key)
            if limit_val in (None, "", 0):
                continue
            try:
                limit_float = float(limit_val)
            except (TypeError, ValueError):
                # 설비가 가동중지 상태면 허용기준/측정값 필드에 숫자 대신 "측정자료확인중(가동중지)"
                # 같은 상태 문자열이 그대로 들어온다(실제 응답에서 확인됨) — 숫자로 못 바꾸는
                # 값은 규칙엔진에 넣을 수 없으니 건너뛴다.
                continue
            measured = r.get(measured_key)
            regulations.append(
                {
                    "law_name": "한국환경공단 CleanSYS (data.go.kr)",
                    "article": f"{label} TMS 실시간 허용기준",
                    "item_name": item_name,
                    "keyword": item_name,
                    "limit_value": limit_float,
                    "unit": UNITS.get(item_name, ""),
                    "comparison": "<=",
                    "is_required": 1,
                    "plant_name": plant,
                    "note": (
                        f"✅ 한국환경공단 CleanSYS 실시간 API ({label}, {r.get('측정시간','')}"
                        f"{f', 당시 실측값 {measured}' if measured not in (None, '') else ''})"
                    ),
                }
            )
    return regulations


def fetch_regulations_from_cleansys(
    fact_manage_nm: Optional[str] = None,
    area_nm: Optional[str] = None,
    api_key: Optional[str] = None,
) -> list[dict]:
    """전체 흐름 한 번에: API 호출 → JSON 파싱 → 규칙엔진용 regulations 리스트 반환."""
    key = api_key or os.environ.get("CLEANSYS_API_KEY")
    if not key:
        raise ValueError("CLEANSYS_API_KEY가 설정되지 않았습니다. .env 파일을 확인하세요.")
    response_json = fetch_measurements_raw(key, fact_manage_nm=fact_manage_nm, area_nm=area_nm)
    records = parse_measurements(response_json)
    return to_regulations(records)


KOMIPO_LEGAL_NAME = "한국중부발전"


def fetch_regulations_for_komipo_plants(api_key: Optional[str] = None) -> list[dict]:
    """
    ⚠️ 예전엔 "보령"·"인천"·"제주"·"세종" 같은 지역 키워드로 여러 번 LIKE 검색했는데,
    실제로 돌려보니 SK인천석유화학, 현대제철 인천공장, 한국남부발전(KOSPO — 한국중부발전과
    다른 회사)의 남제주/신인천 사업장, 아세아제지 세종공장 등 KOMIPO와 무관한 사업장이 지역명이
    겹친다는 이유만으로 대량으로 섞여 들어왔다. 사업장명 LIKE 검색이 지역 기반이 아니라
    "이름에 그 글자가 들어가는지"만 보기 때문이다.

    그래서 KOMIPO의 정식 상호명 "한국중부발전"(사업장명이 전부 "한국중부발전㈜OO발전본부" 형태로
    등록돼 있음)으로 한 번만 검색하고, 혹시 모를 오탐을 막기 위해 응답에서도 한 번 더
    "한국중부발전"이 이름에 포함된 것만 남긴다.
    """
    key = api_key or os.environ.get("CLEANSYS_API_KEY")
    if not key:
        raise ValueError("CLEANSYS_API_KEY가 설정되지 않았습니다. .env 파일을 확인하세요.")

    response_json = fetch_measurements_raw(key, fact_manage_nm=KOMIPO_LEGAL_NAME)
    records = parse_measurements(response_json)
    records = [r for r in records if KOMIPO_LEGAL_NAME in (r.get("사업장명") or "")]
    return to_regulations(records)

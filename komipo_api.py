"""
한국중부발전(KOMIPO) 공식 공공데이터 API 연동 모듈

data.go.kr에 등록된 KOMIPO의 실제 오픈API를 사용한다.
- API명: 한국중부발전(주)_대기오염물질배출 조회서비스
- 상세페이지: https://www.data.go.kr/data/15084758/openapi.do
- 엔드포인트: https://apis.data.go.kr/B552521/airDischarge/getData

이 API는 발전소별·호기별 "실제 허가 기준치"와 "실측 평균값"을 월 단위로 제공한다.
즉 대기환경보전법 시행규칙 별표8 같은 일반 법령 수치가 아니라,
KOMIPO 각 설비에 실제로 적용되는 site-specific 기준치를 그대로 받아올 수 있다.
(참고: 법령상 일반 기준과 실제 허가 기준이 다른 이유는 설비 설치시기·연료종류·용량에 따라
 개별 인허가 시 적용되는 기준이 달라지기 때문 — 바로 이 지점이 본 아이디어가 "법령 원문 대조"만으로는
 부족하고, 발전사 자체 데이터 연동이 필요한 이유를 뒷받침하는 근거이기도 하다.)

사용 전 준비물:
1. https://www.data.go.kr 회원가입
2. 위 API 상세페이지에서 "활용신청" (개발계정은 보통 즉시/자동승인)
3. 마이페이지에서 발급된 서비스키(인증키)를 .env의 KOMIPO_API_KEY에 입력

주의: 계정 생성·API 신청은 사용자 본인이 직접 해야 합니다 (Claude가 대신 가입하지 않음).
"""

import datetime
import os
import xml.etree.ElementTree as ET
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://apis.data.go.kr/B552521/airDischarge/getData"

# ✅ KOMIPO가 제공하는 공식 "활용가이드(GW)_대기오염물질배출조회서비스V1.1_중부.docx" 문서의
#   "라. 코드표"를 그대로 옮긴 것. org_no를 생략하면 "전체 조회"라고 API 문서엔 적혀 있지만,
#   실제로는 202401~202412 기간에 org_no 생략 호출 시 보령화력(8590)만 반환됐다 — 그래서
#   전체 발전소를 확실히 받으려면 아래 코드로 각 발전소를 명시적으로 순회해서 조회해야 한다.
#   (이 API 자체가 서울·인천·제주·서천·보령 5개 사업소만 지원하며, 신보령·신서천·세종은
#   이 API의 대상이 아니다 — 코드표에 없음.)
KOMIPO_ORG_CODES: dict[str, str] = {
    "8410": "서울화력",
    "8420": "인천화력",
    "8560": "제주화력",
    "8570": "서천화력",
    "8571": "서천화력",  # 코드표에 서천화력이 8570/8571 두 코드로 중복 등록되어 있음(문서 원문 그대로)
    "8590": "보령화력",
}


def fetch_emissions_raw(
    service_key: str,
    date_start: str,
    date_end: str,
    org_no: Optional[str] = None,
    hoki: Optional[str] = None,
    timeout: int = 10,
) -> str:
    """
    date_start / date_end: 'YYYYMM' 형식 (예: '202401')
    org_no: 발전소 코드 (선택, 예: '8570') — 비우면 전체 발전소 조회
    hoki: 호기 코드 (선택)
    반환값: XML 원문 문자열
    """
    params = {"ServiceKey": service_key, "strDateS": date_start, "strDateE": date_end}
    if org_no:
        params["strOrgNo"] = org_no
    if hoki:
        params["strHoki"] = hoki

    resp = requests.get(BASE_URL, params=params, timeout=timeout)
    resp.raise_for_status()
    return resp.text


def parse_emissions_xml(xml_text: str) -> list[dict]:
    """
    API 응답 XML을 파싱해 레코드 리스트로 변환한다.
    각 레코드: {발전소명, 년월, 호기, 황산화물_기준, 황산화물_평균,
                질소산화물_기준, 질소산화물_평균, 먼지_기준, 먼지_평균}

    참고 응답 필드명(공식 문서 기준):
    orgnm, ym, hokinm, airsox, avgair01value, airnox, avgair02value, airdst, avgair03value
    실제 API가 XML 네임스페이스나 대소문자를 다르게 줄 수 있어 최대한 관대하게 파싱한다.
    """
    root = ET.fromstring(xml_text)
    records = []

    # <items><item>...</item></items> 구조를 가정 (공공데이터포털 표준 스펙)
    for item in root.iter("item"):
        def get(tag, default=None):
            el = item.find(tag)
            return el.text if el is not None and el.text is not None else default

        records.append(
            {
                "발전소명": get("orgnm"),
                "년월": get("ym"),
                "호기": get("hokinm"),
                "황산화물_기준_ppm": get("airsox"),
                "황산화물_평균_ppm": get("avgair01value"),
                "질소산화물_기준_ppm": get("airnox"),
                "질소산화물_평균_ppm": get("avgair02value"),
                "먼지_기준_mgSm3": get("airdst"),
                "먼지_평균_mgSm3": get("avgair03value"),
            }
        )
    return records


def _latest_per_unit(records: list[dict]) -> list[dict]:
    """
    (발전소, 호기) 조합별로 년월(ym)이 가장 최신인 레코드 1건만 남긴다.
    API를 여러 달치로 조회하면 같은 설비의 과거 월별 기록이 전부 딸려오는데,
    "기준치"는 사실상 같은 값이 매달 반복될 뿐이므로 규칙엔진용 규정으로는
    최신 1건만 있으면 충분하다. (그대로 쓰면 12개월×여러 호기 = 수십~수백 건이
    중복 등록되어 챗봇 응답이 같은 항목을 계속 반복해서 보여주는 문제가 생긴다.)
    """
    latest: dict[tuple, dict] = {}
    for r in records:
        key = (r.get("발전소명"), r.get("호기"))
        ym = r.get("년월") or ""
        if key not in latest or ym > (latest[key].get("년월") or ""):
            latest[key] = r
    return list(latest.values())


def _note_for(label: str, ym: str) -> str:
    """
    발전소별로 API에 마지막으로 보고된 시점(ym)이 크게 다르다 — 예: 보령화력은 2024년까지
    갱신되지만, 서울·인천·제주·서천화력은 2014~2018년이 마지막이고 그 후로 갱신이 끊겼다
    (설비 폐쇄·교체 가능성). "최대한 많은 발전소 데이터를 쓰되, 오래된 값은 오래됐다고
    분명히 표시한다"는 원칙에 따라 3년 이상 지난 값은 ⚠️로 구분해서 표시한다.
    """
    year = None
    if ym and len(ym) >= 4:
        try:
            year = int(ym[:4])
        except ValueError:
            year = None

    current_year = datetime.date.today().year
    if year is not None and year <= current_year - 3:
        return (
            f"⚠️ KOMIPO 공식 API 과거 실측값 ({label}, {ym} 기준 — 이 API에 이후 갱신 데이터 없음). "
            "설비 폐쇄·교체 가능성이 있어 현재도 유효한 기준인지 별도 확인 필요"
        )
    return f"✅ KOMIPO 공식 API 실제값 ({label}, {ym})"


def to_regulations(records: list[dict], dedupe: bool = True) -> list[dict]:
    """
    API 레코드를 rule_engine.check_document()가 기대하는 regulations 형식으로 변환.

    dedupe=True(기본값)면 (발전소, 호기)별 최신 월 데이터만 사용해 중복 규정 생성을 막는다.
    과거 이력을 전부 남기고 싶다면 dedupe=False로 호출.

    발전소마다 "최신 월"의 실제 연도가 다르므로(보령화력=2024, 나머지=2014~2018),
    각 발전소·호기가 가진 데이터 중 가장 최근 것을 그대로 사용한다 — 즉 발전소별로
    실제 유효기간이 다른 걸 그대로 반영하며, 오래된 값은 note에 ⚠️로 표시한다.
    """
    if dedupe:
        records = _latest_per_unit(records)

    regulations = []
    for r in records:
        plant = r.get("발전소명", "")
        unit = r.get("호기", "")
        ym = r.get("년월", "")
        label = f"{plant} {unit}".strip()
        note = _note_for(label, ym)

        if r.get("황산화물_기준_ppm"):
            regulations.append(
                {
                    "law_name": "KOMIPO 공공데이터 API (data.go.kr)",
                    "article": f"{label} 실제 허가기준",
                    "item_name": "황산화물",
                    "keyword": "황산화물",
                    "limit_value": float(r["황산화물_기준_ppm"]),
                    "unit": "ppm",
                    "comparison": "<=",
                    "is_required": 1,
                    "plant_name": plant,
                    "note": note,
                }
            )
        if r.get("질소산화물_기준_ppm"):
            regulations.append(
                {
                    "law_name": "KOMIPO 공공데이터 API (data.go.kr)",
                    "article": f"{label} 실제 허가기준",
                    "item_name": "질소산화물",
                    "keyword": "질소산화물",
                    "limit_value": float(r["질소산화물_기준_ppm"]),
                    "unit": "ppm",
                    "comparison": "<=",
                    "is_required": 1,
                    "plant_name": plant,
                    "note": note,
                }
            )
        if r.get("먼지_기준_mgSm3"):
            regulations.append(
                {
                    "law_name": "KOMIPO 공공데이터 API (data.go.kr)",
                    "article": f"{label} 실제 허가기준",
                    "item_name": "먼지",
                    "keyword": "먼지",
                    "limit_value": float(r["먼지_기준_mgSm3"]),
                    "unit": "mg/Sm3",
                    "comparison": "<=",
                    "is_required": 1,
                    "plant_name": plant,
                    "note": note,
                }
            )
    return regulations


def fetch_regulations_from_api(
    date_start: str,
    date_end: str,
    org_no: Optional[str] = None,
    hoki: Optional[str] = None,
    api_key: Optional[str] = None,
) -> list[dict]:
    """전체 흐름 한 번에: API 호출 → XML 파싱 → 규칙엔진용 regulations 리스트 반환."""
    key = api_key or os.environ.get("KOMIPO_API_KEY")
    if not key:
        raise ValueError("KOMIPO_API_KEY가 설정되지 않았습니다. .env 파일을 확인하세요.")
    xml_text = fetch_emissions_raw(key, date_start, date_end, org_no, hoki)
    records = parse_emissions_xml(xml_text)
    return to_regulations(records)


def fetch_regulations_for_all_plants(
    date_start: str,
    date_end: str,
    api_key: Optional[str] = None,
) -> list[dict]:
    """
    코드표에 있는 모든 KOMIPO 사업소(서울·인천·제주·서천×2코드·보령)를 org_no로 명시해
    하나씩 순회 조회한 뒤 합쳐서 반환한다. org_no를 생략한 "전체 조회"가 문서 설명과 달리
    실제로는 일부만 반환하는 것으로 확인됐기 때문에, 전체 발전소 데이터가 필요하면 이 함수를 쓴다.
    org_no 하나가 실패(네트워크 오류 등)해도 나머지는 계속 조회한다.
    """
    key = api_key or os.environ.get("KOMIPO_API_KEY")
    if not key:
        raise ValueError("KOMIPO_API_KEY가 설정되지 않았습니다. .env 파일을 확인하세요.")

    all_records: list[dict] = []
    for org_no in KOMIPO_ORG_CODES:
        try:
            xml_text = fetch_emissions_raw(key, date_start, date_end, org_no=org_no)
            all_records.extend(parse_emissions_xml(xml_text))
        except Exception as e:
            print(f"⚠️ org_no={org_no}({KOMIPO_ORG_CODES[org_no]}) 조회 실패, 건너뜀: {e}")

    return to_regulations(all_records)

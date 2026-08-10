"""
KOMIPO API에서 최신 규정만 다시 받아서 DB의 regulations 테이블을 정리하는 스크립트.

문제 상황: komipo_api.to_regulations()가 원래 월별 기록을 중복 없이 거르지 않아서,
여러 달치·여러 호기를 조회해 그대로 DB에 넣으면 같은 항목(황산화물/질소산화물/먼지)이
수십~수백 건씩 쌓여 챗봇이 같은 결과를 반복해서 보여주는 문제가 있었습니다.
지금은 komipo_api.py가 (발전소, 호기)별 최신 1건만 남기도록 고쳐졌으니,
이 스크립트로 기존에 쌓인 중복 데이터를 지우고 깨끗하게 다시 넣습니다.

사용법:
    python3 refresh_komipo_data.py [조회시작YYYYMM] [조회종료YYYYMM] [발전소코드(선택)]

인자를 생략하면 201401(이 API의 서비스 시작 시점)부터 이번 달까지 전체 기간을 조회합니다.
발전소마다 API에 마지막으로 보고된 시점이 다르므로(보령화력=2024년까지, 나머지=2014~2018년),
기간을 넓게 잡아야 각 발전소가 가진 데이터 중 "그 발전소의 진짜 최신값"을 다 건질 수 있습니다
(komipo_api.py의 (발전소,호기)별 최신 1건만 남기는 로직이 발전소마다 다른 최신 시점을 알아서 골라줍니다).
오래돼서 갱신이 끊긴 값은 note에 ⚠️로 표시됩니다.

예시:
    python3 refresh_komipo_data.py                  # 201401 ~ 이번달, 전체 5개 사업소
    python3 refresh_komipo_data.py 202401 202412     # 특정 기간만 좁혀서 조회
    python3 refresh_komipo_data.py 201401 202412 8590  # 보령화력만 전체 기간 조회

발전소코드를 생략하면 코드표에 있는 모든 사업소(서울·인천·제주·서천·보령)를 하나씩 순회 조회합니다
(org_no 생략 "전체 조회"가 실제로는 일부만 반환하는 것으로 확인되어, 명시적으로 순회하도록 바꿨습니다).
"""

import datetime
import sys

import db
import komipo_api


def main():
    default_end = datetime.date.today().strftime("%Y%m")
    date_start = sys.argv[1] if len(sys.argv) > 1 else "201401"
    date_end = sys.argv[2] if len(sys.argv) > 2 else default_end
    org_no = sys.argv[3] if len(sys.argv) > 3 else None

    if org_no:
        print(f"KOMIPO API 조회 중... ({date_start} ~ {date_end}, 발전소코드={org_no})")
        regulations = komipo_api.fetch_regulations_from_api(date_start, date_end, org_no=org_no)
    else:
        print(f"KOMIPO API 조회 중... ({date_start} ~ {date_end}, 전체 사업소 순회: {list(komipo_api.KOMIPO_ORG_CODES.values())})")
        regulations = komipo_api.fetch_regulations_for_all_plants(date_start, date_end)
    print(f"조회 완료: (발전소, 호기)별 최신 데이터로 {len(regulations)}건 생성됨")

    conn = db.get_connection()
    try:
        cursor = conn.cursor()

        # KOMIPO API에서 가져온 행만 지우고, 수동으로 넣은 다른 항목(BOD 등)은 그대로 둔다
        cursor.execute(
            "DELETE FROM regulations WHERE law_name = %s",
            ("KOMIPO 공공데이터 API (data.go.kr)",),
        )
        deleted = cursor.rowcount
        print(f"기존 API 데이터 {deleted}건 삭제")

        insert_sql = (
            "INSERT INTO regulations (law_name, article, item_name, keyword, limit_value, "
            "unit, comparison, is_required, plant_name, note) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
        )
        for r in regulations:
            cursor.execute(
                insert_sql,
                (
                    r["law_name"],
                    r["article"],
                    r["item_name"],
                    r["keyword"],
                    r["limit_value"],
                    r["unit"],
                    r["comparison"],
                    r["is_required"],
                    r.get("plant_name"),
                    r["note"],
                ),
            )

        conn.commit()
        print(f"새 데이터 {len(regulations)}건 삽입 완료. regulations 테이블이 정리됐습니다.")
    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    main()

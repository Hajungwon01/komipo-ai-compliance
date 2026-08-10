"""
한국환경공단 CleanSYS(굴뚝자동측정기기 실시간 공개시스템)에서 KOMIPO 발전소들의 최신 기준을
받아와 DB의 regulations 테이블을 정리하는 스크립트. refresh_komipo_data.py와 같은 패턴이며,
법령명(law_name)이 다르므로 서로의 데이터를 건드리지 않고 각자 갱신할 수 있습니다.

사용법:
    python3 refresh_cleansys_data.py

KOMIPO의 정식 상호명 "한국중부발전"으로 검색해 결과를 저장합니다.

(예전엔 "보령"·"인천"·"제주" 같은 지역 키워드로 여러 번 검색했는데, 실제로 돌려보니 SK인천석유화학,
한국남부발전=KOSPO의 남제주/신인천 사업장 등 KOMIPO와 무관한 회사들이 지역명이 겹친다는 이유만으로
대량으로 섞여 들어왔습니다. "한국중부발전"으로 검색하고 응답에서도 그 이름이 포함된 것만 한 번 더
걸러내도록 cleansys_api.fetch_regulations_for_komipo_plants()를 고쳤습니다.)
"""

import cleansys_api
import db


def main():
    print("CleanSYS API 조회 중 (KOMIPO 발전본부명으로 검색)...")
    regulations = cleansys_api.fetch_regulations_for_komipo_plants()
    plants = sorted({r["plant_name"] for r in regulations})
    print(f"조회 완료: {len(regulations)}건 ({len(plants)}개 사업장: {', '.join(plants) if plants else '없음'})")

    if not regulations:
        print("조회된 사업장이 없습니다. CleanSYS에 등록된 정확한 사업장명을 다시 확인해보세요.")
        return

    conn = db.get_connection()
    try:
        cursor = conn.cursor()

        cursor.execute(
            "DELETE FROM regulations WHERE law_name = %s",
            ("한국환경공단 CleanSYS (data.go.kr)",),
        )
        deleted = cursor.rowcount
        print(f"기존 CleanSYS 데이터 {deleted}건 삭제")

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

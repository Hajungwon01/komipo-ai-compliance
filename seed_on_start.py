"""
배포 환경(Render/Railway)에서 서버가 뜨기 직전에 한 번 실행되는 시딩 스크립트.

Volume을 붙인 배포 환경은 SQLite 파일이 재시작해도 유지되지만, "Pre-Deploy Command"는
별도 컨테이너에서 실행되고 Volume이 마운트되지 않기 때문에(Railway 공식 문서 확인) 그 단계에서는
DB 파일에 아무것도 쓸 수 없습니다. 대신 이 스크립트를 실제 Volume이 붙는 Start Command 맨 앞에
끼워 넣어서, 서버가 뜨기 직전에 DB를 확인하고 필요하면 채웁니다.

동작:
1. regulations 테이블이 비어있으면(최초 실행) sample_data.sql(고정 법령 기준 7건)을 로드합니다.
   이미 데이터가 있으면 건너뜁니다(재시작마다 중복 삽입 방지).
2. KOMIPO_API_KEY가 설정돼 있으면 refresh_komipo_data.py를 실행해 발전소별 실제 허가기준을 채웁니다.
3. CLEANSYS_API_KEY가 설정돼 있으면 refresh_cleansys_data.py를 실행합니다.
   두 스크립트 모두 자기 law_name 행만 지우고 다시 넣는 방식이라, 재시작마다 다시 실행돼도
   중복되지 않습니다(멱등).
4. API 키가 없으면 해당 단계는 건너뛰고 안내만 출력합니다(서버 기동 자체는 막지 않음).

Render/Railway의 Start Command를 아래처럼 바꿔서 사용합니다:
    sh -c "python seed_on_start.py; uvicorn server:app --host 0.0.0.0 --port $PORT"
"""
import os
import sqlite3
import subprocess
import sys

import db


def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))

    conn = db.get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM regulations")
        count = cur.fetchone()[0]
    finally:
        conn.close()

    if count == 0:
        sample_path = os.path.join(base_dir, "sample_data.sql")
        print(f"[seed] regulations 테이블이 비어있어 {sample_path}을(를) 로드합니다.")
        with open(sample_path, "r", encoding="utf-8") as f:
            sql_text = f.read()
        raw = sqlite3.connect(db.DB_PATH)
        try:
            raw.executescript(sql_text)
            raw.commit()
        finally:
            raw.close()
        print("[seed] sample_data.sql 로드 완료.")
    else:
        print(f"[seed] regulations 테이블에 이미 {count}건 있어 sample_data.sql은 건너뜁니다.")

    if os.environ.get("KOMIPO_API_KEY"):
        print("[seed] KOMIPO API 데이터 갱신 중...")
        subprocess.run(
            [sys.executable, os.path.join(base_dir, "refresh_komipo_data.py")],
            cwd=base_dir,
            check=False,
        )
    else:
        print("[seed] KOMIPO_API_KEY가 없어 KOMIPO 데이터 갱신을 건너뜁니다.")

    if os.environ.get("CLEANSYS_API_KEY"):
        print("[seed] CleanSYS API 데이터 갱신 중...")
        subprocess.run(
            [sys.executable, os.path.join(base_dir, "refresh_cleansys_data.py")],
            cwd=base_dir,
            check=False,
        )
    else:
        print("[seed] CLEANSYS_API_KEY가 없어 CleanSYS 데이터 갱신을 건너뜁니다.")


if __name__ == "__main__":
    main()

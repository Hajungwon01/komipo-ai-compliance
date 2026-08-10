"""
SQLite 연결 및 조회 헬퍼

원래 MySQL을 썼는데, 배포할 때(Render 등) 외부 DB를 따로 가입해야 하는 번거로움이 있어서
파일 하나로 끝나는 SQLite로 전환했습니다. 로컬에서도 서버 설치·계정 생성 없이 바로 동작합니다.

mysql.connector와 거의 같은 인터페이스(get_connection(), cursor(dictionary=True), %s 플레이스홀더)를
유지하도록 감싸서, auth.py·refresh_komipo_data.py·refresh_cleansys_data.py 등 다른 파일은
전혀 수정하지 않아도 그대로 동작합니다.

DB 파일 위치는 환경변수 SQLITE_DB_PATH로 바꿀 수 있고, 기본값은 이 파일과 같은 폴더의
ai_compliance.db 입니다. 테이블은 연결할 때마다 없으면 자동으로 만들어집니다(수동으로
schema를 실행할 필요 없음 — db_schema.sql은 참고용으로만 남겨둠).
"""

import os
import sqlite3
from dotenv import load_dotenv

load_dotenv()

DB_PATH = os.environ.get(
    "SQLITE_DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "ai_compliance.db")
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS regulations (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  law_name      TEXT NOT NULL,
  article       TEXT NOT NULL,
  item_name     TEXT NOT NULL,
  keyword       TEXT NOT NULL,
  limit_value   REAL NOT NULL,
  unit          TEXT NOT NULL,
  comparison    TEXT NOT NULL DEFAULT '<=' CHECK (comparison IN ('<=', '>=')),
  is_required   INTEGER DEFAULT 0,
  plant_name    TEXT DEFAULT NULL,
  note          TEXT,
  updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS check_history (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  filename          TEXT,
  checked_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
  rule_result_json  TEXT,
  ai_result_json    TEXT
);

CREATE TABLE IF NOT EXISTS users (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  username      TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  salt          TEXT NOT NULL,
  plant_name    TEXT NOT NULL,
  created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


class _DictCursor:
    """mysql.connector의 cursor(dictionary=True)와 비슷하게 동작하도록 감싼 sqlite3 커서.
    %s 플레이스홀더를 sqlite가 쓰는 ?로 자동 변환한다."""

    def __init__(self, raw_cursor, as_dict: bool):
        self._cursor = raw_cursor
        self._as_dict = as_dict

    def execute(self, sql: str, params=()):
        sql = sql.replace("%s", "?")
        return self._cursor.execute(sql, params)

    def fetchall(self):
        rows = self._cursor.fetchall()
        return [dict(r) for r in rows] if self._as_dict else rows

    def fetchone(self):
        row = self._cursor.fetchone()
        if row is None:
            return None
        return dict(row) if self._as_dict else row

    @property
    def rowcount(self):
        return self._cursor.rowcount

    def close(self):
        self._cursor.close()


class _ConnWrapper:
    def __init__(self, raw_conn):
        self._conn = raw_conn

    def cursor(self, dictionary: bool = False):
        return _DictCursor(self._conn.cursor(), as_dict=dictionary)

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()


def get_connection():
    raw_conn = sqlite3.connect(DB_PATH)
    raw_conn.row_factory = sqlite3.Row
    raw_conn.executescript(_SCHEMA)  # 테이블이 이미 있으면 아무 일도 안 함 (IF NOT EXISTS)
    return _ConnWrapper(raw_conn)


def fetch_regulations() -> list[dict]:
    conn = get_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT law_name, article, item_name, keyword, limit_value, unit, "
            "comparison, is_required, plant_name, note FROM regulations"
        )
        rows = cursor.fetchall()
        cursor.close()
        return rows
    finally:
        conn.close()


def save_check_history(filename: str, rule_result_json: str, ai_result_json: str) -> None:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO check_history (filename, rule_result_json, ai_result_json) "
            "VALUES (%s, %s, %s)",
            (filename, rule_result_json, ai_result_json),
        )
        conn.commit()
        cursor.close()
    finally:
        conn.close()

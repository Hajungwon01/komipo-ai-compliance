-- AI 컴플라이언스 시뮬레이터 - MVP DB 스키마 (SQLite)
--
-- 참고용입니다 — db.py가 앱 실행 시 이 내용과 동일한 스키마를 자동으로 만들기 때문에
-- 이 파일을 수동으로 실행할 필요는 없습니다. DB 구조를 눈으로 확인하고 싶을 때,
-- 또는 sqlite3 CLI로 직접 DB 파일을 만들고 싶을 때 참고하세요:
--   sqlite3 ai_compliance.db < db_schema.sql

-- 법령 기준치 테이블 (규칙 기반 엔진이 참조)
CREATE TABLE IF NOT EXISTS regulations (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  law_name      TEXT NOT NULL,               -- 예: 대기환경보전법 시행규칙
  article       TEXT NOT NULL,               -- 예: 별표8
  item_name     TEXT NOT NULL,               -- 예: 먼지
  keyword       TEXT NOT NULL,               -- 문서 텍스트에서 탐색할 키워드
  limit_value   REAL NOT NULL,               -- 기준값
  unit          TEXT NOT NULL,               -- 단위 (mg/Sm3 등)
  comparison    TEXT NOT NULL DEFAULT '<=' CHECK (comparison IN ('<=', '>=')),  -- 기준값과의 비교 방향
  is_required   INTEGER DEFAULT 0,           -- 서류에 반드시 명시돼야 하는 항목인지 (0/1)
  plant_name    TEXT DEFAULT NULL,           -- KOMIPO API로 받은 발전소별 기준일 때만 값 존재 (예: 보령화력).
                                              -- NULL이면 특정 발전소에 국한되지 않는 일반 법령 기준(BOD 등).
  note          TEXT,                        -- 비고 (예: 시설 종류별 예외 등)
  updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 검증 이력 테이블
CREATE TABLE IF NOT EXISTS check_history (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  filename          TEXT,
  checked_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
  rule_result_json  TEXT,   -- 규칙 기반 엔진 결과 (JSON)
  ai_result_json    TEXT    -- AI 누락항목 체크 결과 (JSON)
);

-- 발전소별 관리자 계정 (로그인/회원가입용)
-- ⚠️ 프로토타입 데모 수준의 인증입니다 (salt+SHA-256 해시). 실제 서비스로 발전시킬 때는
--   bcrypt/argon2, 세션 토큰, HTTPS 등 정식 인증 체계로 교체가 필요합니다.
CREATE TABLE IF NOT EXISTS users (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  username      TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,   -- SHA-256(salt + password) 16진수 문자열
  salt          TEXT NOT NULL,
  plant_name    TEXT NOT NULL,   -- 회원가입 시 선택한 소속 발전소 (regulations.plant_name과 매칭)
  created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

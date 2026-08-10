"""
발전소별 관리자 계정 — 회원가입/로그인 (프로토타입 데모용)

⚠️ 보안 수준에 대한 안내:
salt + SHA-256 해시만 사용하는 간단한 인증입니다. 데모/프로토타입 목적에는 충분하지만,
실제 서비스라면 bcrypt/argon2 같은 전용 비밀번호 해시 함수, HTTPS, 세션 토큰 만료 등
정식 보안 체계로 교체해야 합니다. (이 프로젝트 범위는 "회원가입 시 발전소를 선택해서
DB에 저장하고, 로그인하면 그 발전소로 스코프가 고정된다"는 UI 흐름을 보여주는 데 있습니다.)
"""

import hashlib
import secrets
from typing import Optional

import db


def _hash_password(password: str, salt: str) -> str:
    return hashlib.sha256((salt + password).encode("utf-8")).hexdigest()


def create_user(username: str, password: str, plant_name: str) -> tuple[bool, str]:
    """
    회원가입. 성공하면 (True, ""), 실패하면 (False, "사용자에게 보여줄 에러 메시지").
    """
    username = (username or "").strip()
    password = password or ""
    plant_name = (plant_name or "").strip()

    if not username or not password or not plant_name:
        return False, "아이디·비밀번호·소속 발전소를 모두 입력해주세요."
    if len(password) < 4:
        return False, "비밀번호는 4자 이상으로 설정해주세요."

    conn = db.get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM users WHERE username = %s", (username,))
        if cursor.fetchone():
            return False, "이미 사용 중인 아이디입니다."

        salt = secrets.token_hex(16)
        password_hash = _hash_password(password, salt)
        cursor.execute(
            "INSERT INTO users (username, password_hash, salt, plant_name) VALUES (%s, %s, %s, %s)",
            (username, password_hash, salt, plant_name),
        )
        conn.commit()
        return True, ""
    finally:
        cursor.close()
        conn.close()


def authenticate_user(username: str, password: str) -> Optional[dict]:
    """
    로그인. 성공하면 {"username":..., "plant_name":...} 반환, 실패하면 None.
    """
    username = (username or "").strip()
    password = password or ""
    if not username or not password:
        return None

    conn = db.get_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT username, password_hash, salt, plant_name FROM users WHERE username = %s",
            (username,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        if _hash_password(password, row["salt"]) != row["password_hash"]:
            return None
        return {"username": row["username"], "plant_name": row["plant_name"]}
    finally:
        cursor.close()
        conn.close()

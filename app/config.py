"""환경 변수 기반 설정. 값은 호출 시점에 읽으므로 테스트에서 바꿀 수 있습니다."""
import hashlib
import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# 데이터 저장 위치 (SQLite 파일). Render에서는 영구 디스크 경로(/var/data/app.db)를 지정하세요.
DB_PATH = os.getenv("DB_PATH", str(BASE_DIR / "data" / "app.db"))

# 로그인 계정
# - AUTH_USERS="id1:pw1,id2:pw2" 형식으로 여러 명 지정 가능
# - 또는 APP_USER / APP_PASSWORD 한 쌍
# - 둘 다 비어 있으면 인증 없음(로컬 개발용). 운영 시에는 반드시 설정하세요.
APP_USER = os.getenv("APP_USER", "weplatform")
APP_PASSWORD = os.getenv("APP_PASSWORD", "")


def _env_loose(name: str) -> str:
    """대시보드 입력 실수(키 앞뒤 공백, 소문자)를 허용해 환경변수를 찾습니다."""
    if os.getenv(name):
        return os.environ[name]
    for k, v in os.environ.items():
        if k.strip().upper() == name:
            return v
    return ""


AUTH_USERS = _env_loose("AUTH_USERS")

# 로그인 쿠키 서명 키. 지정하지 않으면 계정 정보에서 파생합니다(비밀번호를 바꾸면 기존 로그인 해제).
if os.getenv("SECRET_KEY"):
    SECRET_KEY = os.environ["SECRET_KEY"]
elif AUTH_USERS or APP_PASSWORD:
    SECRET_KEY = hashlib.sha256(f"seogeo:{AUTH_USERS}:{APP_USER}:{APP_PASSWORD}".encode()).hexdigest()
else:
    SECRET_KEY = secrets.token_hex(32)
SESSION_DAYS = int(os.getenv("SESSION_DAYS", "14"))

# 공개 서버(Render 등)에서 계정이 없으면 화면을 잠급니다. Render는 RENDER=true를 자동 설정합니다.
REQUIRE_AUTH = os.getenv("REQUIRE_AUTH", "1" if os.getenv("RENDER") else "0") == "1"

# 브랜드
BRAND_NAME = os.getenv("BRAND_NAME", "위플랫폼")
APP_NAME = "SEO·GEO 진단 센터"

# 크롤러 설정
USER_AGENT_TOKEN = "WeplatformSEOBot"
USER_AGENT = f"{USER_AGENT_TOKEN}/1.0 (+SEO/GEO audit by weplatform)"
CRAWL_DELAY = float(os.getenv("CRAWL_DELAY", "0.5"))        # 요청 간 대기(초)
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "15"))  # 요청 타임아웃(초)
DEFAULT_MAX_PAGES = int(os.getenv("DEFAULT_MAX_PAGES", "30"))
MAX_PAGES_LIMIT = int(os.getenv("MAX_PAGES_LIMIT", "100"))
MAX_CRAWL_DEPTH = int(os.getenv("MAX_CRAWL_DEPTH", "3"))
MAX_BODY_BYTES = 2_000_000

# 동시에 실행할 진단 개수 (무료 서버 자원 보호)
MAX_CONCURRENT_AUDITS = int(os.getenv("MAX_CONCURRENT_AUDITS", "2"))

# 내부망/로컬 주소 크롤링 허용 여부 (SSRF 방지를 위해 기본 차단, 테스트에서만 1)
ALLOW_PRIVATE_HOSTS = os.getenv("ALLOW_PRIVATE_HOSTS", "0") == "1"

# 업종 목록 (광고주 분류)
INDUSTRIES = ["뷰티", "패션", "식품", "건강기능식품", "생활·리빙", "가전·디지털", "유아·키즈",
              "반려동물", "스포츠·레저", "여행·숙박", "교육", "의료·병원", "금융·보험", "부동산",
              "B2B·IT", "프랜차이즈·외식", "기타"]

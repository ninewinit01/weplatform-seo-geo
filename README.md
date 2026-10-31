# 위플랫폼 SEO·GEO 진단 센터

광고주별 사이트를 등록하고 **SEO / GEO(AI 검색 대응) / 네이버** 3개 엔진으로 진단해 리포트를 만드는 내부 웹앱입니다.
리포트는 로그인 없이 볼 수 있는 **외부 공유 링크**로 광고주에게 전달할 수 있습니다.

## 화면
| 메뉴 | 내용 |
|---|---|
| 대시보드 | 광고주·사이트·최근 30일 진단 수, 엔진별 평균 점수, 점수 분포, 우선 관리 대상, 최근 진단 |
| 광고주·사이트 | 광고주 등록·수정(업종/상태/담당자/연락처), 검색·필터, 사이트 등록·수정·삭제, 사이트별/전체 진단 실행, 점수 추이 |
| 진단 리포트 | 종합 점수·등급, SEO/GEO/네이버 엔진 점수와 이전 대비 변화, 분류별 점수, 먼저 손볼 것, 엔진별 필터, 수동 확인 체크리스트, 페이지별 현황, 점수 추이, CSV, PDF 인쇄, 외부 공유 링크 |
| 진단 기준 | 엔진·분류별 가중치와 페이지 단위 점검 항목 |
| 설정·백업 | 전체 데이터 JSON 백업·복원 |

## 실행

### 로컬 (Python 3.11+)
```bash
pip install -r requirements.txt
uvicorn app.main:app --reload        # http://127.0.0.1:8000 (계정 미설정 시 로그인 없이 열림)
```

### 테스트
```bash
pip install -r requirements-dev.txt
pytest -q
```
로컬 샘플 사이트를 띄워 크롤링·3개 엔진·웹 화면·공유 링크·CSV·로그인·백업/복원까지 검증하며 외부 네트워크가 필요 없습니다.

### Render 배포
1. Render 대시보드 → **New → Blueprint** → 이 저장소 선택 (`render.yaml` 사용)
2. `AUTH_USERS`에 `아이디:비밀번호` 입력 (쉼표로 여러 명). `SECRET_KEY`는 자동 생성됩니다.
3. 배포 후 `https://<서비스명>.onrender.com` 접속

- 계정이 없으면 Render에서는 화면이 잠깁니다(`RENDER` 환경변수 감지).
- **무료 플랜**: 15분 무요청 시 잠듦 → `.github/workflows/keepalive.yml`이 10분마다 `/healthz`를 호출합니다.
  URL이 다르면 저장소 Settings → Variables에 `APP_URL`을 등록하세요.
- **무료 플랜은 재시작·재배포 시 데이터가 초기화될 수 있습니다.** 설정·백업 메뉴에서 백업을 받아 두고, 초기화되면 복원하세요.
  영구 보존이 필요하면 Starter 플랜으로 바꾸고 `render.yaml`의 `disk` 주석을 해제하세요.

### Docker (자체 서버)
```bash
cp .env.example .env     # AUTH_USERS 반드시 변경
docker compose up -d --build
```

## 구조
```
app/
  main.py              라우트, 로그인(서명 쿠키), 공유 링크, 백업/복원
  db.py                SQLite (광고주/사이트/진단/결과/페이지) + 경량 마이그레이션
  config.py            환경변수 설정
  audit/
    crawler.py         robots/sitemap/llms.txt 수집, http→https 확인, BFS 크롤링, 페이지 분석
    engines/
      base.py          공통 타입, 엔진·분류 정의, 집계
      seo.py           기술 SEO · 콘텐츠 · 구조화 데이터
      geo.py           AI 검색 대응 (AI 봇 허용, nosnippet, 두괄식, 최신성, 엔티티 등)
      naver.py         Yeti 접근/메타, 소유확인, 프레임 포워딩, RSS, OG
    checks.py          엔진 실행 진입점
    scoring.py         분류·엔진·종합 점수
    runner.py          크롤링→검사→저장 (동시 실행 제한)
  templates/, static/  화면
tests/                 pytest
```

## 알아둘 점
- **정적 HTML 기준 진단**입니다(JS 실행 안 함). 홈 본문이 거의 없으면 'JS 렌더링 의존' 경고를 띄웁니다.
- **GEO 기준은 업계 표준이 없습니다.** `app/audit/engines/*.py`의 `weight`를 팀 경험에 맞춰 조정하세요.
- 크롤러는 robots.txt를 준수하고 요청 간 0.5초 대기하며, 내부망 주소는 차단합니다. https 연결이 안 되면 http로 재시도합니다.

## 다음 단계
- Search Console / PageSpeed / 네이버 검색광고·데이터랩 API 연동, 키워드 순위 추적
- AI 엔진(ChatGPT·Gemini·Perplexity·Claude) 인용 모니터링
- Claude API 기반 개선안 생성(메타·FAQ·초안)

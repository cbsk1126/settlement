# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# FastAPI (Python 3.11) 멀티스테이지 Dockerfile
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 조건:
#   - non-root 유저로 실행
#   - 최종 이미지 200MB 이하
#   - HEALTHCHECK 포함
#
# 구조 가정: requirements.txt + src/settlement 패키지
#           (uvicorn settlement.main:app 형태로 기동)
#           실제 프로젝트 구조가 다르면 COPY/WORKDIR 경로만 맞춰주세요.
#
# 용량 절감 포인트 (200MB 목표):
#   - python:3.11-slim 베이스
#   - 멀티스테이지로 gcc 등 빌드 도구를 최종 이미지에서 제외
#   - pip install --no-cache-dir --no-compile → 다운로드 캐시 및
#     설치 시점 .pyc 바이트코드 생성을 막아 용량 절감
#   - apt 캐시(/var/lib/apt/lists) 삭제
#   - curl 대신 python 표준 라이브러리(urllib)로 헬스체크 (패키지 추가 설치 없음)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


# ── Stage 1: Builder ────────────────────────────────────────────
# 의존성 빌드(컴파일)만 여기서 수행. gcc 등 빌드 도구는
# 이 스테이지에만 존재하며 최종 이미지에는 포함되지 않는다.
FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# 의존성 파일만 먼저 복사 → 소스 코드 변경 시에도 레이어 캐시 재사용
COPY requirements.txt .

# --user: /root/.local 에 설치해서 최종 스테이지로 통째로 복사
# --no-cache-dir: pip 다운로드 캐시 미저장
# --no-compile: 설치 시점에 .pyc를 만들지 않음 (용량 절감의 핵심)
RUN pip install --no-cache-dir --no-compile --user -r requirements.txt

# 설치된 패키지의 불필요한 부산물(테스트 디렉토리, 캐시) 제거
RUN find /root/.local -type d -name "__pycache__" -exec rm -rf {} + \
    && find /root/.local -type d -name "tests" -exec rm -rf {} + \
    && find /root/.local -type d -name "test" -exec rm -rf {} + \
    && find /root/.local -type f -name "*.pyc" -delete


# ── Stage 2: Runtime ────────────────────────────────────────────
# 컴파일러나 빌드 도구가 없는 slim 이미지로 최종 실행 환경 구성.
FROM python:3.11-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/home/appuser/.local/bin:${PATH}"

# tini: PID 1로 사용해 SIGTERM 등 시그널을 uvicorn에 올바르게 전달하고
# 좀비 프로세스를 방지한다. curl은 설치하지 않음(용량 절감,
# 헬스체크는 python 표준 라이브러리로 대체).
RUN apt-get update \
    && apt-get install -y --no-install-recommends tini \
    && rm -rf /var/lib/apt/lists/*

# ── non-root 유저 생성 ───────────────────────────────────────────
# -r: 시스템 계정, -m: 홈 디렉토리 생성 (pip --user 설치 경로용)
RUN useradd -r -m -u 1000 -s /usr/sbin/nologin appuser

WORKDIR /app

# builder 스테이지에서 설치된 패키지만 복사 (빌드 도구는 제외됨)
COPY --from=builder /root/.local /home/appuser/.local

# 애플리케이션 소스 복사
COPY src/ /app/src/

# 소유권을 appuser로 변경 (실행 시점에 파일 접근 문제 방지)
RUN chown -R appuser:appuser /app /home/appuser/.local

# ── non-root 유저로 전환 ─────────────────────────────────────────
USER appuser

ENV PYTHONPATH=/app/src

EXPOSE 8000

# ── HEALTHCHECK ──────────────────────────────────────────────────
# curl 대신 python 표준 라이브러리(urllib)로 헬스체크를 수행해
# 별도 패키지 설치 없이 이미지 용량을 절감한다.
# --start-period: 앱 기동(lifespan 시드 데이터 생성 등) 시간을 고려한 유예 시간
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request, sys; \
        sys.exit(0) if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else sys.exit(1)" \
        || exit 1

# tini를 PID 1로 사용해 시그널 처리(SIGTERM)와 좀비 프로세스 정리를 보장
ENTRYPOINT ["tini", "--"]

CMD ["uvicorn", "settlement.main:app", "--host", "0.0.0.0", "--port", "8000"]

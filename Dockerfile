# 1단계: 웹 화면 빌드
FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# 2단계: FastAPI 서버 (빌드된 화면을 함께 서빙)
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PEERLENS_CACHE_DIR=/app/data/cache PEERLENS_WEB_DIST=/app/web/dist
COPY pyproject.toml README.md ./
COPY src/ src/
RUN pip install --no-cache-dir .
COPY --from=web /web/dist web/dist
EXPOSE 8000
# SEC_USER_AGENT는 실행 시 환경변수로 주입 (이미지에 넣지 않음)
CMD ["uvicorn", "peerlens.api:app", "--host", "0.0.0.0", "--port", "8000"]

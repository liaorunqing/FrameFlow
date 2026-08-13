FROM node:22-alpine AS web
WORKDIR /build/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend/ ./backend/
COPY --from=web /build/frontend/dist ./frontend/dist
RUN mkdir -p /app/backend/data/uploads \
    /app/backend/data/outputs \
    /app/backend/data/workflow-artifacts \
    /app/backend/data/benchmark-artifacts \
    /app/backend/data/benchmark-reports \
    /app/backend/data/benchmark-datasets \
    /app/backend/data/benchmarks \
    /app/backend/data/production \
    /app/backend/data/workflows
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM node:22-alpine AS frontend
WORKDIR /src/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ ./
COPY data/ /data/
COPY --from=frontend /src/frontend/dist /frontend/dist
RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app /data /frontend
USER appuser
EXPOSE 8000

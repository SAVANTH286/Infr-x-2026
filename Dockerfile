FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    nodejs \
    npm \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY frontend/package.json frontend/package-lock.json ./frontend/
RUN cd frontend && npm install

COPY frontend ./frontend
RUN cd frontend && npm run build

COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend ./backend

WORKDIR /app/backend
ENV PORT=8000
EXPOSE 8000

CMD uvicorn main:app --host 0.0.0.0 --port ${PORT}

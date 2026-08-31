# Infr-x-2026 (PageVerdict)

Upload a loan / PDF package, extract evidence, and ask questions with page citations.

Stack: **FastAPI** backend + **React / Vite** frontend. Optional Groq / Gemini / OpenAI keys for QA.

## Local run

Terminal 1 — backend:

```bash
cd backend
pip install -r requirements.txt
python main.py
```

Backend: http://127.0.0.1:8000

Terminal 2 — frontend:

```bash
cd frontend
npm install
npm run dev
```

Frontend: http://localhost:5173  
Vite proxies `/api` to the backend, so you do **not** hardcode `localhost:8000`.

Optional: put keys in `backend/.env`

```
GROQ_API_KEY=...
GEMINI_API_KEY=...
OPENAI_API_KEY=...
```

Or paste them in the UI (stored in the browser).

## Deploy on Render (one service)

This is the simplest public deploy.

1. Push this repo to GitHub (already at [SAVANTH286/Infr-x-2026](https://github.com/SAVANTH286/Infr-x-2026)).
2. Open [https://dashboard.render.com](https://dashboard.render.com) and sign in with GitHub.
3. **New → Web Service** → select `Infr-x-2026`.
4. Settings:

   | Field | Value |
   |---|---|
   | Runtime | Python 3 |
   | Build command | `cd frontend && npm install && npm run build && cd ../backend && pip install -r requirements.txt` |
   | Start command | `cd backend && uvicorn main:app --host 0.0.0.0 --port $PORT` |
   | Instance | Free

5. Environment variables (optional but needed for Q&A):

   - `GROQ_API_KEY`
   - `GEMINI_API_KEY` (optional)
   - `OPENAI_API_KEY` (optional)

6. Click **Create Web Service**.

Render gives a URL like:

`https://infr-x-2026.onrender.com`

The backend also serves the built frontend from `frontend/dist`, so that one URL is the full app.

## Deploy frontend and backend separately

- Backend on Render with start command: `cd backend && uvicorn main:app --host 0.0.0.0 --port $PORT`
- Frontend on Vercel/Netlify from the `frontend` folder
- Set `VITE_API_URL` to the backend URL, for example `https://infr-x-2026.onrender.com`

Do not leave API URLs as `http://localhost:8000` in production.

## Health check

`GET /api/health` → `{ "status": "ok" }`

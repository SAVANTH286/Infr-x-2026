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
GEMINI_API_KEY=...
GROQ_API_KEY=...
OPENAI_API_KEY=...
```

`GEMINI_API_KEY` is the free default. Get it at [https://aistudio.google.com/apikey](https://aistudio.google.com/apikey). Groq is optional. The app can still extract PDF text with no key.

Or paste them in the UI (stored in the browser).

## Deploy (one click)

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/SAVANTH286/Infr-x-2026)

1. Open that button (or [this deploy link](https://render.com/deploy?repo=https://github.com/SAVANTH286/Infr-x-2026)).
2. Sign in with GitHub.
3. Add a free `GEMINI_API_KEY` from [Google AI Studio](https://aistudio.google.com/apikey) if you want LLM Q&A. Groq is no longer required.
4. Click **Apply**.

Render builds the Docker image and gives a public URL, usually:

`https://infr-x-2026.onrender.com`

The first free-tier boot can take 1–2 minutes.

## Deploy on Render (manual)

1. Open [https://dashboard.render.com](https://dashboard.render.com) and sign in with GitHub.
2. **New → Web Service** → select `Infr-x-2026`.
3. Use the Docker runtime (this repo has a `Dockerfile`).
4. Environment variables (optional but needed for Q&A):

   - `GEMINI_API_KEY` (free — recommended)
   - `GROQ_API_KEY` (optional)
   - `OPENAI_API_KEY` (optional)

5. Click **Create Web Service**.

The backend also serves the built frontend from `frontend/dist`, so that one URL is the full app.

## Deploy frontend and backend separately

- Backend on Render with start command: `cd backend && uvicorn main:app --host 0.0.0.0 --port $PORT`
- Frontend on Vercel/Netlify from the `frontend` folder
- Set `VITE_API_URL` to the backend URL, for example `https://infr-x-2026.onrender.com`

Do not leave API URLs as `http://localhost:8000` in production.

## Health check

`GET /api/health` → `{ "status": "ok" }`

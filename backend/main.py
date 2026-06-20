import os
import shutil
import sys
import threading
from typing import Any, Dict, Optional

from fastapi import FastAPI, File, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from dotenv import load_dotenv
load_dotenv()

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from analyzer import GenericPDFAnalyzer

app = FastAPI(title="PageVerdict Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = os.path.abspath("./uploads")
CACHE_DIR = os.path.join(UPLOAD_DIR, "cache")
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(CACHE_DIR, exist_ok=True)

render_lock = threading.Lock()
active_analyzer: Optional[GenericPDFAnalyzer] = None
active_pdf_path: Optional[str] = None


class QueryRequest(BaseModel):
    question: str


def _apply_keys(
    analyzer: GenericPDFAnalyzer,
    groq_key: Optional[str],
    gemini_key: Optional[str],
    openai_key: Optional[str],
):
    analyzer.update_keys(
        groq_key=groq_key,
        gemini_key=gemini_key,
        openai_key=openai_key,
    )


@app.get("/api/health")
def health_check():
    return {"status": "ok", "service": "PageVerdict Backend"}


@app.post("/api/upload")
async def upload_pdf(
    file: UploadFile = File(...),
    x_api_key: Optional[str] = Header(None),
    x_gemini_key: Optional[str] = Header(None),
    x_openai_key: Optional[str] = Header(None),
):
    global active_analyzer, active_pdf_path

    if not file.filename or not file.filename.lower().endswith(".pdf") or os.path.basename(file.filename).startswith("._"):
        raise HTTPException(status_code=400, detail="Only valid PDF files are supported")

    file_path = os.path.abspath(os.path.join(UPLOAD_DIR, file.filename))
    try:
        file_bytes = await file.read()
        with open(file_path, "wb") as buffer:
            buffer.write(file_bytes)

        active_pdf_path = file_path
        pdf_name = os.path.splitext(file.filename)[0]
        file_cache_dir = os.path.join(CACHE_DIR, pdf_name)
        if os.path.exists(file_cache_dir):
            shutil.rmtree(file_cache_dir)
        os.makedirs(file_cache_dir, exist_ok=True)

        active_analyzer = GenericPDFAnalyzer(
            file_path,
            api_key=os.environ.get("GROQ_API_KEY") or x_api_key,
            gemini_key=os.environ.get("GEMINI_API_KEY") or x_gemini_key,
            openai_key=os.environ.get("OPENAI_API_KEY") or x_openai_key,
        )
        return active_analyzer.get_summary()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")


@app.get("/api/summary")
def get_summary(
    x_api_key: Optional[str] = Header(None),
    x_gemini_key: Optional[str] = Header(None),
    x_openai_key: Optional[str] = Header(None),
):
    global active_analyzer
    if not active_analyzer:
        raise HTTPException(status_code=400, detail="No active document uploaded")

    _apply_keys(active_analyzer, x_api_key, x_gemini_key, x_openai_key)
    return active_analyzer.get_summary()


@app.get("/api/evidence")
def get_evidence(page: Optional[int] = None):
    global active_analyzer
    if not active_analyzer:
        raise HTTPException(status_code=400, detail="No active document uploaded")
    return {"evidence": active_analyzer.get_evidence(page)}


@app.post("/api/query")
def query_document(
    request: QueryRequest,
    x_api_key: Optional[str] = Header(None),
    x_gemini_key: Optional[str] = Header(None),
    x_openai_key: Optional[str] = Header(None),
):
    global active_analyzer
    if not active_analyzer:
        raise HTTPException(status_code=400, detail="No active document uploaded")

    try:
        groq_key = os.environ.get("GROQ_API_KEY") or x_api_key
        gemini_key = os.environ.get("GEMINI_API_KEY") or x_gemini_key
        openai_key = os.environ.get("OPENAI_API_KEY") or x_openai_key
        _apply_keys(active_analyzer, groq_key, gemini_key, openai_key)
        return active_analyzer.answer_question(request.question)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/page/{page_index}/render")
def render_pdf_page(page_index: int):
    global active_pdf_path

    if not active_pdf_path or not os.path.exists(active_pdf_path):
        raise HTTPException(status_code=400, detail="No active document uploaded")

    pdf_name = os.path.splitext(os.path.basename(active_pdf_path))[0]
    file_cache_dir = os.path.join(CACHE_DIR, pdf_name)
    os.makedirs(file_cache_dir, exist_ok=True)
    cache_file_path = os.path.join(file_cache_dir, f"page_{page_index}.png")

    if os.path.exists(cache_file_path):
        return FileResponse(cache_file_path, media_type="image/png")

    with render_lock:
        if os.path.exists(cache_file_path):
            return FileResponse(cache_file_path, media_type="image/png")

        try:
            from extractors import page_to_png_bytes

            png = page_to_png_bytes(active_pdf_path, page_index, dpi=100)
            if not png:
                raise HTTPException(status_code=400, detail="Invalid page index")
            with open(cache_file_path, "wb") as f:
                f.write(png)
            return FileResponse(cache_file_path, media_type="image/png")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Rendering failed: {str(e)}")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)

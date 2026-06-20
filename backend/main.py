import os
import sys
import base64
import io
import shutil
import threading
from fastapi import FastAPI, HTTPException, UploadFile, File, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, FileResponse
from pydantic import BaseModel
from typing import List, Dict, Any, Optional

# Add current path to system path for imports
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

# Lock to serialize C-level PDFium page loading and avoid concurrent memory access violations
render_lock = threading.Lock()

# Cache active analyzer in memory
active_analyzer: Optional[GenericPDFAnalyzer] = None
active_pdf_path: Optional[str] = None

class QueryRequest(BaseModel):
    question: str

@app.post("/api/upload")
async def upload_pdf(file: UploadFile = File(...), x_api_key: Optional[str] = Header(None)):
    """Upload any arbitrary PDF file and process it dynamically"""
    global active_analyzer, active_pdf_path
    
    if not file.filename.endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")
        
    file_path = os.path.abspath(os.path.join(UPLOAD_DIR, file.filename))
    try:
        # Save file to disk
        file_bytes = await file.read()
        with open(file_path, "wb") as buffer:
            buffer.write(file_bytes)
            
        active_pdf_path = file_path
        
        # Clear cache for this file if it already existed
        pdf_name = os.path.splitext(file.filename)[0]
        file_cache_dir = os.path.join(CACHE_DIR, pdf_name)
        if os.path.exists(file_cache_dir):
            shutil.rmtree(file_cache_dir)
        os.makedirs(file_cache_dir, exist_ok=True)
        
        # Instantiate analyzer
        active_analyzer = GenericPDFAnalyzer(file_path, api_key=x_api_key)
        return active_analyzer.get_summary()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")

@app.get("/api/summary")
def get_summary(x_api_key: Optional[str] = Header(None)):
    """Returns classification layout and truth matrix for active file"""
    global active_analyzer
    if not active_analyzer:
        raise HTTPException(status_code=400, detail="No active document uploaded")
        
    try:
        if x_api_key:
            active_analyzer.api_key = x_api_key
            active_analyzer._analyze_package()
        return active_analyzer.get_summary()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/query")
def query_document(request: QueryRequest, x_api_key: Optional[str] = Header(None)):
    """Answers question against the uploaded PDF using Grok and Agents"""
    global active_analyzer
    if not active_analyzer:
        raise HTTPException(status_code=400, detail="No active document uploaded")
        
    try:
        if x_api_key:
            active_analyzer.api_key = x_api_key
        return active_analyzer.answer_question(request.question)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/page/{page_index}/render")
def render_pdf_page(page_index: int):
    """Renders page of the PDF, caching results on disk and serializing thread access"""
    global active_pdf_path
    
    if not active_pdf_path or not os.path.exists(active_pdf_path):
        raise HTTPException(status_code=400, detail="No active document uploaded")
        
    pdf_name = os.path.splitext(os.path.basename(active_pdf_path))[0]
    file_cache_dir = os.path.join(CACHE_DIR, pdf_name)
    os.makedirs(file_cache_dir, exist_ok=True)
    
    cache_file_path = os.path.join(file_cache_dir, f"page_{page_index}.png")
    
    # Return from cache instantly if page already rendered (no lock needed)
    if os.path.exists(cache_file_path):
        return FileResponse(cache_file_path, media_type="image/png")
        
    # Serialize file rendering to prevent concurrent PDFium thread conflicts
    with render_lock:
        # Double-check cache inside lock to prevent redundant rendering
        if os.path.exists(cache_file_path):
            return FileResponse(cache_file_path, media_type="image/png")
            
        try:
            import pdfplumber
            with pdfplumber.open(active_pdf_path) as pdf:
                if page_index < 0 or page_index >= len(pdf.pages):
                    raise HTTPException(status_code=400, detail="Invalid page index")
                
                page = pdf.pages[page_index]
                im = page.to_image(resolution=100)
                im.save(cache_file_path, format='PNG')
                
            return FileResponse(cache_file_path, media_type="image/png")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Rendering failed: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)

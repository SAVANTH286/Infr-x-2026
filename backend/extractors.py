"""PDF extraction layers: text, tables, images, checkboxes."""

import base64
import io
import re
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np


def extract_text_and_tables(pdf_path: str) -> Tuple[int, Dict[int, Dict[str, Any]]]:
    """Extract text, word positions, and tables from every page via pdfplumber."""
    import pdfplumber

    hub: Dict[int, Dict[str, Any]] = {}
    with pdfplumber.open(pdf_path) as pdf:
        total = len(pdf.pages)
        for idx, page in enumerate(pdf.pages):
            text = page.extract_text() or ""
            words = page.extract_words() or []
            tables_raw = page.extract_tables() or []
            tables = []
            for t_idx, table in enumerate(tables_raw):
                if not table:
                    continue
                headers = [str(c or "").strip() for c in table[0]]
                rows = []
                for row in table[1:]:
                    rows.append([str(c or "").strip() for c in row])
                tables.append({"index": t_idx, "headers": headers, "rows": rows})
            hub[idx] = {
                "text": text,
                "words": words,
                "tables": tables,
                "width": float(page.width or 612),
                "height": float(page.height or 792),
            }
    return total, hub


def page_to_png_bytes(pdf_path: str, page_index: int, dpi: int = 150) -> Optional[bytes]:
    """Render a PDF page to PNG bytes using PyMuPDF."""
    try:
        import fitz

        doc = fitz.open(pdf_path)
        if page_index < 0 or page_index >= len(doc):
            doc.close()
            return None
        page = doc[page_index]
        mat = fitz.Matrix(dpi / 72, dpi / 72)
        pix = page.get_pixmap(matrix=mat)
        png_bytes = pix.tobytes("png")
        doc.close()
        return png_bytes
    except Exception as e:
        print(f"PyMuPDF render failed page {page_index}: {e}")
        return None


def page_to_base64(pdf_path: str, page_index: int, dpi: int = 150) -> Optional[str]:
    png = page_to_png_bytes(pdf_path, page_index, dpi)
    if not png:
        return None
    b64 = base64.b64encode(png).decode("utf-8")
    return f"data:image/png;base64,{b64}"


def extract_embedded_images(pdf_path: str) -> List[Dict[str, Any]]:
    """Extract embedded images with bounding boxes using PyMuPDF."""
    images: List[Dict[str, Any]] = []
    try:
        import fitz

        doc = fitz.open(pdf_path)
        for page_idx in range(len(doc)):
            page = doc[page_idx]
            for img_info in page.get_images(full=True):
                xref = img_info[0]
                try:
                    rects = page.get_image_rects(xref)
                    for rect in rects:
                        images.append(
                            {
                                "page": page_idx,
                                "bbox": [rect.x0, rect.y0, rect.x1, rect.y1],
                                "xref": xref,
                            }
                        )
                except Exception:
                    pass
        doc.close()
    except Exception as e:
        print(f"Image extraction failed: {e}")
    return images


def detect_checkboxes(pdf_path: str, page_index: int) -> List[Dict[str, Any]]:
    """Detect checkbox squares and infer checked state via fill ratio."""
    png = page_to_png_bytes(pdf_path, page_index, dpi=200)
    if not png:
        return []

    arr = np.frombuffer(png, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
    if img is None:
        return []

    h, w = img.shape
    scale_x = 612.0 / w
    scale_y = 792.0 / h

    _, binary = cv2.threshold(img, 200, 255, cv2.THRESH_BINARY_INV)
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    checkboxes: List[Dict[str, Any]] = []
    for cnt in contours:
        x, y, bw, bh = cv2.boundingRect(cnt)
        aspect = bw / max(bh, 1)
        area = bw * bh
        if not (0.7 <= aspect <= 1.4 and 8 <= bw <= 40 and 8 <= bh <= 40):
            continue

        roi = img[y : y + bh, x : x + bw]
        fill_ratio = 1.0 - (np.mean(roi) / 255.0)
        checked = fill_ratio > 0.25

        px_bbox = [x * scale_x, y * scale_y, (x + bw) * scale_x, (y + bh) * scale_y]
        checkboxes.append({"bbox": px_bbox, "checked": checked, "fill_ratio": round(fill_ratio, 3)})

    return checkboxes


def infer_checkbox_labels(page_text: str, checkboxes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Attach nearby text labels to detected checkboxes using line proximity."""
    lines = [ln.strip() for ln in page_text.split("\n") if ln.strip()]
    checkbox_chars = {"☑", "☐", "☒", "□", "■", "[x]", "[X]", "[ ]"}

    labeled = []
    for i, cb in enumerate(checkboxes):
        label = f"checkbox_{i + 1}"
        for ln in lines:
            if any(ch in ln for ch in checkbox_chars) or re.search(r"\[[xX ]?\]", ln):
                clean = re.sub(r"[☑☐☒□■\[\]xX ]", "", ln).strip()
                if clean:
                    label = clean[:80]
                    break
        labeled.append({**cb, "label": label})
    return labeled


def page_needs_vision(page_data: Dict[str, Any], embedded_images: List[Dict[str, Any]], page_idx: int) -> bool:
    """Heuristic: page likely contains charts, signatures, or scanned content."""
    text = page_data.get("text", "")
    word_count = len(text.split())
    has_images = any(img["page"] == page_idx for img in embedded_images)
    visual_keywords = [
        "signature", "signed", "chart", "graph", "figure", "plot",
        "notary", "seal", "stamp", "photo", "image",
    ]
    has_keywords = any(kw in text.lower() for kw in visual_keywords)
    sparse_text = word_count < 30
    return has_images or has_keywords or sparse_text

"""LLM clients for text QA (Groq) and vision analysis (Gemini / Groq vision)."""

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional


def _post_json(url: str, headers: Dict[str, str], payload: Dict[str, Any], timeout: int = 5) -> Dict[str, Any]:
    """HTTP POST utility with a small timeout (5s) to prevent cascading freezes"""
    # Force a User-Agent header to pass Cloudflare integrity checks
    updated_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        **headers
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=updated_headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _clean_json(text: str) -> str:
    return re.sub(r"```json\s*|\s*```", "", text or "").strip()


class LLMClient:
    def __init__(
        self,
        groq_key: Optional[str] = None,
        gemini_key: Optional[str] = None,
        openai_key: Optional[str] = None,
    ):
        self.groq_key = groq_key or os.environ.get("GROQ_API_KEY", "")
        self.gemini_key = gemini_key or os.environ.get("GEMINI_API_KEY", "")
        self.openai_key = openai_key or os.environ.get("OPENAI_API_KEY", "")

    def groq_text(self, system: str, user: str, model: str = "llama-3.3-70b-versatile") -> str:
        if not self.groq_key:
            return ""
        payload = {
            "model": "llama-3.1-8b-instant", # Fallback to llama-3.1-8b-instant for speed and limits compatibility
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.1,
        }
        try:
            data = _post_json(
                "https://api.groq.com/openai/v1/chat/completions",
                {
                    "Authorization": f"Bearer {self.groq_key}",
                    "Content-Type": "application/json",
                },
                payload,
            )
            return data["choices"][0]["message"]["content"]
        except Exception as e:
            print(f"Groq text call failed: {e}")
            return ""

    def groq_vision(self, prompt: str, images: List[str], model: str = "llama-3.2-90b-vision-preview") -> str:
        if not self.groq_key or not images:
            return ""
        content: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
        for img in images[:1]: # Limit to 1 image for performance
            content.append({"type": "image_url", "image_url": {"url": img}})
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": content}],
            "temperature": 0.1,
        }
        try:
            data = _post_json(
                "https://api.groq.com/openai/v1/chat/completions",
                {
                    "Authorization": f"Bearer {self.groq_key}",
                    "Content-Type": "application/json",
                },
                payload,
                timeout=5,
            )
            return data["choices"][0]["message"]["content"]
        except Exception as e:
            print(f"Groq vision call failed: {e}")
            if self.gemini_key:
                return self._gemini_vision(prompt, images)
            return ""

    def _gemini_vision(self, prompt: str, images: List[str]) -> str:
        if not self.gemini_key:
            return ""
        try:
            parts: List[Dict[str, Any]] = [{"text": prompt}]
            for img in images[:1]:
                if img.startswith("data:"):
                    mime, b64 = img.split(",", 1)
                    mime_type = mime.replace("data:", "").replace(";base64", "")
                else:
                    mime_type, b64 = "image/png", img
                parts.append({"inline_data": {"mime_type": mime_type, "data": b64}})

            payload = {"contents": [{"parts": parts}]}
            url = (
                "https://generativelanguage.googleapis.com/v1beta/models/"
                f"gemini-2.0-flash:generateContent?key={self.gemini_key}"
            )
            data = _post_json(url, {"Content-Type": "application/json"}, payload, timeout=5)
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except Exception as e:
            print(f"Gemini vision call failed: {e}")
            if self.openai_key:
                return self._openai_vision(prompt, images)
            return ""

    def _openai_vision(self, prompt: str, images: List[str]) -> str:
        if not self.openai_key:
            return ""
        content: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
        for img in images[:1]:
            content.append({"type": "image_url", "image_url": {"url": img}})
        payload = {
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": content}],
            "temperature": 0.1,
        }
        try:
            data = _post_json(
                "https://api.openai.com/v1/chat/completions",
                {
                    "Authorization": f"Bearer {self.openai_key}",
                    "Content-Type": "application/json",
                },
                payload,
                timeout=5,
            )
            return data["choices"][0]["message"]["content"]
        except Exception as e:
            print(f"OpenAI vision call failed: {e}")
            return ""

    def analyze_page_visually(self, page_index: int, image_b64: str) -> List[Dict[str, Any]]:
        """Extract charts, signatures, stamps, images from a page image."""
        prompt = f"""Analyze this PDF page (page index {page_index}). Return ONLY valid JSON array.
Each item must have: type (one of: chart, signature, stamp, image, handwriting, table_visual),
content (description or extracted data), bbox ([x0,y0,x1,y1] in PDF points, page is ~612x792),
confidence (0-1), and optional data field for charts (title, axes, values).

If nothing visual found, return []."""

        raw = self.groq_vision(prompt, [image_b64])
        if not raw.strip():
            return []
        try:
            parsed = json.loads(_clean_json(raw))
            if isinstance(parsed, dict):
                parsed = parsed.get("items", parsed.get("evidence", [parsed]))
            if not isinstance(parsed, list):
                return []
            out = []
            for item in parsed:
                if not isinstance(item, dict):
                    continue
                out.append(
                    {
                        "type": item.get("type", "image"),
                        "content": item.get("content", ""),
                        "bbox": item.get("bbox", [50, 100, 560, 200]),
                        "confidence": float(item.get("confidence", 0.7)),
                        "data": item.get("data"),
                        "page": page_index,
                    }
                )
            return out
        except Exception as e:
            print(f"Vision parse failed page {page_index}: {e}")
            return []

    def classify_pages_batch(self, page_samples: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Classify multiple pages in one Groq call."""
        if not page_samples or not self.groq_key:
            # Fallback early if key is missing
            return [
                {
                    "page": s["page"],
                    "document_type": "general",
                    "document_label": "Document Page",
                    "extracted_metadata": {},
                }
                for s in page_samples
            ]
        lines = []
        for s in page_samples:
            lines.append(f"PAGE {s['page']}:\n{s['text'][:1200]}\n")
        system = (
            "You classify PDF pages. Return ONLY a JSON array. Each element: "
            '{"page": N, "document_type": "snake_case", "document_label": "Human Label", '
            '"extracted_metadata": {"key": "value"}}. '
            "Use generic types: invoice, paystub, bank_statement, tax_form, contract, "
            "disclosure, credit_report, correspondence, id_document, general."
        )
        user = "Classify each page:\n\n" + "\n---\n".join(lines)
        raw = self.groq_text(system, user)
        try:
            parsed = json.loads(_clean_json(raw))
            if isinstance(parsed, list):
                return parsed
        except Exception:
            pass
        return [
            {
                "page": s["page"],
                "document_type": "general",
                "document_label": "Document Page",
                "extracted_metadata": {},
            }
            for s in page_samples
        ]

    def answer_from_evidence(self, question: str, evidence_context: str, images: Optional[List[str]] = None) -> Dict[str, Any]:
        """Answer question strictly from evidence. Returns answer + cited evidence ids."""
        system = """You are a precise PDF QA engine. Rules:
1. Answer ONLY from the EVIDENCE provided.
2. If the user asks to summarize, describe, or list information that is present in the provided evidence block, synthesize a clean response based on that evidence.
3. If the evidence does not contain information to support the answer at all, respond with answer exactly: "unavailable".
4. Never hallucinate or guess.
5. Cite the evidence IDs used in the cited_evidence_ids array.
6. Return ONLY valid JSON: {"answer": "...", "cited_evidence_ids": ["EV_1"], "reasoning": "..."}"""

        user = f"EVIDENCE:\n{evidence_context}\n\nQUESTION: {question}"

        if images:
            raw = self.groq_vision(
                system + "\n\n" + user + "\n\nReturn JSON only.",
                images,
            )
        else:
            raw = self.groq_text(system, user)

        try:
            parsed = json.loads(_clean_json(raw))
            answer = str(parsed.get("answer", "")).strip()
            if not answer:
                answer = "unavailable"
            lower = answer.lower()
            if any(p in lower for p in ["not found", "not available", "cannot determine", "no evidence"]):
                answer = "unavailable"
            return {
                "answer": answer,
                "cited_evidence_ids": parsed.get("cited_evidence_ids", []),
                "reasoning": parsed.get("reasoning", ""),
            }
        except Exception:
            text = (raw or "").strip()
            if not text:
                return {"answer": "unavailable", "cited_evidence_ids": [], "reasoning": ""}
            lower = text.lower()
            if any(p in lower for p in ["unavailable", "not found", "not in"]):
                return {"answer": "unavailable", "cited_evidence_ids": [], "reasoning": text}
            return {"answer": text, "cited_evidence_ids": [], "reasoning": ""}

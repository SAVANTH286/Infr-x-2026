import os
import re
from typing import Any, Dict, List, Optional

from extractors import (
    detect_checkboxes,
    extract_embedded_images,
    extract_text_and_tables,
    infer_checkbox_labels,
    page_needs_vision,
    page_to_base64,
)
from llm_client import LLMClient


class GenericPDFAnalyzer:
    def __init__(
        self,
        pdf_path: str,
        api_key: Optional[str] = None,
        gemini_key: Optional[str] = None,
        openai_key: Optional[str] = None,
    ):
        self.pdf_path = pdf_path
        self.package_id = os.path.splitext(os.path.basename(pdf_path))[0]
        self.api_key = api_key
        self.gemini_key = gemini_key
        self.openai_key = openai_key

        self.evidence_hub: Dict[int, Dict[str, Any]] = {}
        self.evidence_store: List[Dict[str, Any]] = []
        self.doc_instances: List[Dict[str, Any]] = []
        self.truth_matrix: Dict[str, Dict[str, Any]] = {}
        self.health_score = 100
        self.health_breakdown: Dict[str, int] = {}
        self.total_pages = 0
        self._ev_counter = 0

        self.llm = LLMClient(
            groq_key=api_key,
            gemini_key=gemini_key,
            openai_key=openai_key,
        )

        self._build_evidence_store()
        self._analyze_package()

    def _next_ev_id(self) -> str:
        self._ev_counter += 1
        return f"EV_{self._ev_counter:04d}"

    def _add_evidence(
        self,
        ev_type: str,
        page: int,
        content: str,
        bbox: Optional[List[float]] = None,
        confidence: float = 0.85,
        extra: Optional[Dict[str, Any]] = None,
    ) -> str:
        ev_id = self._next_ev_id()
        item = {
            "id": ev_id,
            "type": ev_type,
            "page": page,
            "bbox": bbox or [50, 100, 560, 200],
            "content": content,
            "confidence": confidence,
        }
        if extra:
            item.update(extra)
        self.evidence_store.append(item)
        return ev_id

    def _build_evidence_store(self):
        """Full extraction pipeline: text, tables, checkboxes, images, vision."""
        self.total_pages, self.evidence_hub = extract_text_and_tables(self.pdf_path)
        embedded = extract_embedded_images(self.pdf_path)

        for page_idx in range(self.total_pages):
            hub = self.evidence_hub.get(page_idx, {})
            text = hub.get("text", "")

            if text.strip():
                words = hub.get("words", [])
                if words:
                    bbox = [
                        min(w["x0"] for w in words),
                        min(w["top"] for w in words),
                        max(w["x1"] for w in words),
                        max(w["bottom"] for w in words),
                    ]
                else:
                    bbox = [50, 50, 560, 740]
                self._add_evidence("text", page_idx, text[:3000], bbox)

            for t_idx, table in enumerate(hub.get("tables", [])):
                headers = table.get("headers", [])
                rows = table.get("rows", [])
                table_text = " | ".join(headers) + "\n"
                for row in rows[:30]:
                    table_text += " | ".join(row) + "\n"
                self._add_evidence(
                    "table",
                    page_idx,
                    table_text.strip(),
                    [50, 100 + t_idx * 20, 560, 300 + t_idx * 20],
                    extra={"headers": headers, "rows": rows, "table_index": t_idx},
                )

            cbs = detect_checkboxes(self.pdf_path, page_idx)
            if cbs:
                labeled = infer_checkbox_labels(text, cbs)
                for cb in labeled[:20]:
                    state = "checked" if cb["checked"] else "unchecked"
                    self._add_evidence(
                        "checkbox",
                        page_idx,
                        f"{cb['label']}={state}",
                        cb["bbox"],
                        confidence=0.75,
                        extra={"checked": cb["checked"], "label": cb["label"]},
                    )

        for img in embedded[:50]:
            self._add_evidence(
                "image",
                img["page"],
                "Embedded image region detected",
                img["bbox"],
                confidence=0.8,
            )

        vision_pages = []
        if self.llm.groq_key or self.llm.gemini_key or self.llm.openai_key:
            vision_pages = [
                i
                for i in range(self.total_pages)
                if page_needs_vision(self.evidence_hub.get(i, {}), embedded, i)
            ]
            if not vision_pages:
                vision_pages = list(range(min(self.total_pages, 2)))

        for page_idx in vision_pages[:2]:
            img_b64 = page_to_base64(self.pdf_path, page_idx)
            if not img_b64:
                continue
            visual_items = self.llm.analyze_page_visually(page_idx, img_b64)
            for v in visual_items:
                self._add_evidence(
                    v.get("type", "image"),
                    v.get("page", page_idx),
                    v.get("content", ""),
                    v.get("bbox"),
                    confidence=float(v.get("confidence", 0.7)),
                    extra={"data": v.get("data")},
                )

    def _analyze_package(self):
        self._classify_and_split_instances()
        self._build_truth_matrix()
        self._compute_health_score()

    def _classify_and_split_instances(self):
        page_samples = []
        step = max(1, self.total_pages // 20)
        for idx in range(0, self.total_pages, step):
            text = self.evidence_hub.get(idx, {}).get("text", "")
            if text.strip():
                page_samples.append({"page": idx, "text": text})

        if not page_samples:
            self.doc_instances = [
                {
                    "id": "document_package#1",
                    "type": "pdf_document",
                    "label": "Uploaded PDF",
                    "start_page": 0,
                    "end_page": max(0, self.total_pages - 1),
                    "page_count": self.total_pages,
                    "metadata": {},
                }
            ]
            return

        classified = self.llm.classify_pages_batch(page_samples[:20])
        sampled_page_map = {c.get("page", 0): c for c in classified}
        
        if not sampled_page_map:
            sampled_page_map = {
                s["page"]: {
                    "page": s["page"],
                    "document_type": "general",
                    "document_label": "Document Page",
                    "extracted_metadata": {}
                } for s in page_samples
            }

        sampled_keys = sorted(sampled_page_map.keys())

        def get_page_info(p: int):
            closest_key = min(sampled_keys, key=lambda k: abs(k - p))
            return sampled_page_map[closest_key]

        current = None
        counter: Dict[str, int] = {}
        self.doc_instances = []

        for p in range(self.total_pages):
            info = get_page_info(p)
            dtype = info.get("document_type", "general")
            label = info.get("document_label", "Document Page")
            meta = info.get("extracted_metadata", {})

            if current and current["type"] == dtype:
                current["end_page"] = p
                current["page_count"] = current["end_page"] - current["start_page"] + 1
                current["metadata"].update(meta)
            else:
                if current:
                    self.doc_instances.append(current)
                counter[dtype] = counter.get(dtype, 0) + 1
                current = {
                    "id": f"{dtype}#{counter[dtype]}",
                    "type": dtype,
                    "label": f"{label} #{counter[dtype]}",
                    "start_page": p,
                    "end_page": p,
                    "page_count": 1,
                    "metadata": dict(meta),
                }
        if current:
            self.doc_instances.append(current)

    def _build_truth_matrix(self):
        all_keys: Dict[str, int] = {}
        for inst in self.doc_instances:
            for k, v in inst.get("metadata", {}).items():
                if v:
                    all_keys[k] = all_keys.get(k, 0) + 1
        self.truth_matrix = {}
        for k in all_keys:
            self.truth_matrix[k] = {}
            for inst in self.doc_instances:
                if k in inst.get("metadata", {}):
                    self.truth_matrix[k][inst["id"]] = inst["metadata"][k]

    def _compute_health_score(self):
        conflicts = 0
        total_checks = 0
        for _, mapping in self.truth_matrix.items():
            vals = list(mapping.values())
            if len(vals) > 1:
                total_checks += 1
                if len(set(str(v).lower().strip() for v in vals)) > 1:
                    conflicts += 1
        conflict_score = max(0, 100 - conflicts * 25)
        self.health_breakdown = {
            "conflicts": conflict_score,
            "completeness": 100 if self.doc_instances else 0,
            "integrity": min(100, 50 + len(self.evidence_store) // 5),
        }
        self.health_score = int(sum(self.health_breakdown.values()) / len(self.health_breakdown))

    def get_summary(self) -> Dict[str, Any]:
        return {
            "package_id": self.package_id,
            "total_pages": self.total_pages,
            "health_score": self.health_score,
            "health_breakdown": self.health_breakdown,
            "doc_instances": self.doc_instances,
            "truth_matrix": self.truth_matrix,
            "evidence_count": len(self.evidence_store),
            "evidence_types": self._evidence_type_counts(),
        }

    def _evidence_type_counts(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for ev in self.evidence_store:
            t = ev.get("type", "unknown")
            counts[t] = counts.get(t, 0) + 1
        return counts

    def get_evidence(self, page: Optional[int] = None) -> List[Dict[str, Any]]:
        if page is None:
            return self.evidence_store
        return [e for e in self.evidence_store if e["page"] == page]

    def _search_relevant_evidence(self, question: str, limit: int = 25) -> List[Dict[str, Any]]:
        q = question.lower()

        # A. Check for specific page numbers (e.g. "page 2", "page number 2", "page 25")
        page_matches = re.findall(r"\bpage\s*(?:number)?\s*(\d+)\b", q)
        if page_matches:
            target_pages = [int(m) - 1 for m in page_matches]
            page_evidence = [ev for ev in self.evidence_store if ev["page"] in target_pages]
            if page_evidence:
                return page_evidence[:limit]

        # B. Check for summary / overview queries
        summary_keywords = ["summarize", "summary", "overview", "what is this", "about", "what does this pdf contain", "what document", "what doc"]
        if any(kw in q for kw in summary_keywords):
            summary_ev = []
            seen_pages = set()
            # Add first page of each classified document instance
            for inst in self.doc_instances:
                p = inst["start_page"]
                for ev in self.evidence_store:
                    if ev["page"] == p and ev["type"] in ("text", "table"):
                        if p not in seen_pages:
                            summary_ev.append(ev)
                            seen_pages.add(p)
            # Fill in with the first few pages of the PDF
            for p in range(min(self.total_pages, 8)):
                if p not in seen_pages:
                    for ev in self.evidence_store:
                        if ev["page"] == p and ev["type"] in ("text", "table"):
                            summary_ev.append(ev)
                            seen_pages.add(p)
                            break
            if summary_ev:
                return summary_ev[:limit]

        tokens = [t for t in re.split(r"\W+", q) if len(t) > 2]
        vision_keywords = {
            "signature", "sign", "signed", "graph", "chart", "diagram", "image",
            "visual", "figure", "plot", "stamp", "seal", "photo", "checkbox",
            "checked", "marital", "married", "single",
        }
        type_boost = {
            "signature": ["signature", "sign", "signed"],
            "chart": ["chart", "graph", "plot", "trend", "income"],
            "checkbox": ["checkbox", "checked", "married", "single", "selected"],
            "stamp": ["stamp", "seal", "notary"],
            "table": ["table", "amount", "balance", "income", "wage", "total"],
            "image": ["image", "photo", "property", "house"],
        }

        scored: List[tuple] = []
        for ev in self.evidence_store:
            content = ev.get("content", "").lower()
            ev_type = ev.get("type", "")
            score = 0.0
            for t in tokens:
                if t in content:
                    score += 2.0
                if t in ev_type:
                    score += 1.0
            for kw in vision_keywords:
                if kw in q and kw in content:
                    score += 3.0
            for t, kws in type_boost.items():
                if ev_type == t and any(k in q for k in kws):
                    score += 4.0
            if score > 0:
                scored.append((score, ev))

        scored.sort(key=lambda x: x[0], reverse=True)
        if scored:
            return [ev for _, ev in scored[:limit]]

        page_scores: Dict[int, float] = {}
        for idx in range(self.total_pages):
            text = self.evidence_hub.get(idx, {}).get("text", "").lower()
            page_scores[idx] = sum(1 for t in tokens if t in text)

        top_pages = sorted(page_scores.items(), key=lambda x: x[1], reverse=True)[:5]
        fallback = []
        for p, sc in top_pages:
            if sc <= 0 and not tokens:
                continue
            for ev in self.evidence_store:
                if ev["page"] == p:
                    fallback.append(ev)
        return fallback[:limit] if fallback else self.evidence_store[:min(10, len(self.evidence_store))]

    def _format_evidence_context(self, evidence: List[Dict[str, Any]]) -> str:
        lines = []
        for ev in evidence:
            lines.append(
                f"[{ev['id']}] type={ev['type']} page={ev['page']+1} "
                f"bbox={ev.get('bbox')} confidence={ev.get('confidence')}\n"
                f"content: {ev.get('content', '')[:1000]}"
            )
            if ev.get("data"):
                lines.append(f"structured_data: {ev['data']}")
            if ev.get("headers"):
                lines.append(f"table_headers: {ev['headers']}")
        return "\n\n".join(lines)

    def _evidence_to_citations(self, evidence: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        citations = []
        seen_pages = set()
        for ev in evidence:
            p = ev["page"]
            if p in seen_pages:
                continue
            seen_pages.add(p)
            citations.append(
                {
                    "evidence_id": ev["id"],
                    "page_index": p,
                    "bbox": ev.get("bbox", [50, 100, 560, 200]),
                    "doc_type": ev.get("type", "document"),
                    "content_preview": str(ev.get("content", ""))[:120],
                }
            )
        return citations[:5]

    def answer_question(self, question: str) -> Dict[str, Any]:
        q_clean = question.lower().strip()
        vision_keywords = [
            "signature", "sign", "graph", "chart", "diagram", "image", "visual",
            "figure", "plot", "stamp", "seal", "photo", "checkbox", "checked",
        ]
        requires_vision = any(kw in q_clean for kw in vision_keywords)

        relevant = self._search_relevant_evidence(question, limit=8)
        context = self._format_evidence_context(relevant)

        images: List[str] = []
        if requires_vision:
            pages = sorted(set(ev["page"] for ev in relevant))[:4]
            for p in pages:
                img = page_to_base64(self.pdf_path, p)
                if img:
                    images.append(img)

        result = self.llm.answer_from_evidence(question, context, images if images else None)
        answer = result.get("answer", "unavailable")
        cited_ids = set(result.get("cited_evidence_ids", []))

        cited_evidence = [ev for ev in relevant if ev["id"] in cited_ids]
        if not cited_evidence and answer != "unavailable":
            cited_evidence = relevant[:3]
        if not cited_evidence:
            cited_evidence = relevant[:1]

        citations = self._evidence_to_citations(cited_evidence)
        pages_analyzed = sorted(set(ev["page"] for ev in relevant))

        trace = [
            {"agent": "Planner Agent", "action": f"Parsed question: '{question}'"},
            {
                "agent": "Answerability Agent",
                "action": f"Found {len(relevant)} evidence items across {len(pages_analyzed)} page(s)",
            },
            {
                "agent": "Lookup Agent",
                "action": f"Retrieved evidence types: {', '.join(sorted(set(e['type'] for e in relevant))) or 'text'}",
            },
        ]
        if requires_vision:
            trace.append(
                {"agent": "Vision Agent", "action": f"Analyzed {len(images)} page image(s) for visual content"}
            )
        trace.extend(
            [
                {
                    "agent": "Reasoning Agent",
                    "action": result.get("reasoning", "Synthesized answer from evidence")[:200],
                },
                {
                    "agent": "Verification Agent",
                    "action": f"Answer status: {'Available' if answer != 'unavailable' else 'Evidence Not Found'}",
                },
            ]
        )

        return {
            "question": question,
            "answerable": answer != "unavailable",
            "answer": answer if answer != "unavailable" else "Evidence Not Found — unavailable in this PDF",
            "confidence": 92 if answer != "unavailable" else 100,
            "evidence": citations,
            "trace": trace,
            "requires_vision": requires_vision,
            "pages_analyzed": len(pages_analyzed),
            "evidence_items_used": len(relevant),
        }

    def update_keys(
        self,
        groq_key: Optional[str] = None,
        gemini_key: Optional[str] = None,
        openai_key: Optional[str] = None,
    ):
        if groq_key:
            self.api_key = groq_key
        if gemini_key:
            self.gemini_key = gemini_key
        if openai_key:
            self.openai_key = openai_key
        self.llm = LLMClient(
            groq_key=self.api_key,
            gemini_key=self.gemini_key,
            openai_key=self.openai_key,
        )

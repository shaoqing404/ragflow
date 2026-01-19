import base64
import inspect
import io
import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import asyncio
from PIL import Image

from common.constants import LLMType
from api.db.services.llm_service import LLMBundle
from json_repair import repair_json
from rag.prompts import three_u_mel_vlm_enhance_prompt


def _env_get(*keys: str, default: Optional[str] = None) -> Optional[str]:
    """
    Return the first non-None environment value from the provided keys.
    """
    for key in keys:
        if key is None:
            continue
        value = os.getenv(key)
        if value is not None:
            return value
    return default


@dataclass
class EnhancedChunk:
    """Container for VLM-enhanced chunk content and auxiliary metadata."""

    content: str
    metadata: Optional[Dict[str, Any]] = None


class MelVLMEnhancer:
    """
    Wrapper around VLM enhancement logic for MEL documents.

    Responsibilities:
    - Build page-aware prompts that include OCR fallback content.
    - Call configured image2text model with throttled concurrency.
    - Post-process structured JSON output and map it back to chunk strings.
    """

    def __init__(
        self,
        tenant_id: Optional[str],
        lang: str = "Chinese",
        callback=None,
        logger: Optional[logging.Logger] = None,
        enabled: Optional[bool] = None,
        max_concurrency: Optional[int] = None,
        primary_allowed: Optional[bool] = None,
    ) -> None:
        self._tenant_id = tenant_id
        self._lang = lang
        self._callback = callback or (lambda prog, msg: None)
        self._logger = logger or logging.getLogger(__name__)
        self._configured = enabled if enabled is not None else self._default_enabled()
        self._max_concurrency = max_concurrency or int(
            _env_get("FTMS_VLM_MAX_CONCURRENCY", "THREE_U_MEL_VLM_MAX_CONCURRENCY", default="2")
        )
        self._max_image_pages = max(
            1, self._safe_int_env(3, "FTMS_VLM_MAX_PAGE_IMAGES", "THREE_U_MEL_VLM_MAX_PAGE_IMAGES")
        )
        self._stitch_gap_px = max(
            0, self._safe_int_env(24, "FTMS_VLM_PAGE_GAP", "THREE_U_MEL_VLM_PAGE_GAP")
        )
        self._max_stitch_width = max(
            0, self._safe_int_env(2048, "FTMS_VLM_MAX_WIDTH", "THREE_U_MEL_VLM_MAX_WIDTH")
        )
        default_primary = bool(self._tenant_id)
        self._primary_allowed = default_primary if primary_allowed is None else bool(primary_allowed)
        self._primary_allowed = self._primary_allowed and default_primary and self._configured
        self._vision_model: Optional[LLMBundle] = None
        self._model_name: Optional[str] = None
        self._primary_ready = False
        if self._configured:
            self._fallback_config, self._fallback_reason = self._resolve_fallback_config()
        else:
            self._fallback_config = None
            self._fallback_reason = "FTMS_VLM 已禁用"
        self._fallback_client = None
        self._to_thread_supports_cancellable = self._detect_to_thread_support()

    @property
    def enabled(self) -> bool:
        if not self._configured:
            return False
        return self._primary_allowed or self.fallback_available

    @property
    def configured_enabled(self) -> bool:
        """Raw configuration state before tenant gating."""
        return self._configured

    @property
    def model_name(self) -> Optional[str]:
        return self._model_name

    @property
    def fallback_available(self) -> bool:
        return bool(self._fallback_config)

    @property
    def fallback_descriptor(self) -> Optional[str]:
        if not self._fallback_config:
            return None
        provider = self._fallback_config.get("provider") or "openai"
        model = self._fallback_config.get("model")
        if model:
            return f"{provider}:{model}"
        return provider

    @property
    def fallback_reason(self) -> Optional[str]:
        return self._fallback_reason

    def _detect_to_thread_support(self) -> bool:
        # asyncio.to_thread is available in Python 3.9+
        return hasattr(asyncio, 'to_thread')

    def enhance_chunks(
        self,
        chunks: Sequence[str],
        parser,
        context: Dict[str, Any],
    ) -> List[EnhancedChunk]:
        """
        Enhance merged chunks using the configured VLM.

        Args:
            chunks: List of chunk strings (with DeepDoc position tags).
            parser: DeepDoc Pdf parser instance used to recover tags/positions.
            context: Additional metadata (ata_code, page_range, etc.).
        """
        if not self.enabled or not chunks:
            return [EnhancedChunk(content=ck) for ck in chunks]

        if self._primary_allowed and self._vision_model is None:
            try:
                self._ensure_model()
            except Exception as exc:
                self._primary_allowed = False
                self._logger.warning("⚠️ 3U_MEL VLM主通道初始化失败: %s", exc)
        if not self._primary_ready and self._primary_allowed:
            self._logger.warning("⚠️ 3U_MEL VLM主通道不可用，准备尝试备用通道。")

        try:
            return asyncio.run(self._enhance_async(chunks, parser, context))
        except Exception as exc:  # pragma: no cover - defensive
            self._logger.exception("🔴 3U_MEL VLM并发增强流程异常: %s", exc)
            return [EnhancedChunk(content=ck) for ck in chunks]

    async def _enhance_async(
        self,
        chunks: Sequence[str],
        parser,
        context: Dict[str, Any],
    ) -> List[EnhancedChunk]:
        semaphore = asyncio.Semaphore(max(1, self._max_concurrency))
        results: List[Optional[EnhancedChunk]] = [None] * len(chunks)

        async def worker(index: int, chunk: str) -> None:
            async with semaphore:
                enhanced = await asyncio.to_thread(
                    self._enhance_single,
                    chunk,
                    parser,
                    context,
                    index,
                )
                results[index] = enhanced

        tasks = [worker(idx, chunk) for idx, chunk in enumerate(chunks)]
        await asyncio.gather(*tasks)

        finalized: List[EnhancedChunk] = []
        for idx, chunk in enumerate(chunks):
            enhanced = results[idx] or EnhancedChunk(content=chunk)
            finalized.append(enhanced)
        return finalized

    def _enhance_single(
        self,
        chunk: str,
        parser,
        context: Dict[str, Any],
        index: int,
    ) -> EnhancedChunk:
        try:
            clean_text = parser.remove_tag(chunk)
            if not clean_text.strip():
                return EnhancedChunk(content=chunk)
        except Exception:  # pragma: no cover - defensive
            return EnhancedChunk(content=chunk)

        tags = self._extract_tags(chunk)
        positions = self._safe_extract_positions(parser, chunk)
        page_from_cache = getattr(parser, "page_from", 0)
        relative_pages = sorted({pn for pos in positions for pn in pos[0]})
        if not relative_pages:
            relative_pages = [0]
        pages_abs = [page_from_cache + pn for pn in relative_pages]
        primary_page = pages_abs[0]
        page_images_attr = getattr(parser, "page_images", None)
        page_images_count = len(page_images_attr) if page_images_attr else 0
        cache_lower = page_from_cache
        cache_upper = page_from_cache + page_images_count
        clipped_pages = [p for p in pages_abs if cache_lower <= p < cache_upper]
        if pages_abs and not clipped_pages and page_images_count:
            self._logger.info(
                "🟡 3U_MEL VLM 请求页超出缓存范围 请求=%s 缓存区间=[%s,%s)",
                [p + 1 for p in pages_abs],
                cache_lower + 1,
                cache_upper + 1,
            )
            clipped_pages = [cache_lower]
        pages_for_preview = clipped_pages or pages_abs

        image_payload, preview_pages, available_pages, missing_pages, truncated_pages = self._prepare_image_payload(
            parser,
            pages_for_preview,
        )

        prompt_text = self._filter_ocr_text(clean_text.strip()) or clean_text.strip()

        if self._looks_like_catalog(prompt_text):
            self._logger.info(
                "🟡 3U_MEL VLM 跳过块=%s 原因=疑似目录/页眉内容", index
            )
            return EnhancedChunk(content=chunk)

        doc_span = context.get("page_range")
        chunk_page_numbers = [p + 1 for p in pages_for_preview]
        preview_page_numbers = [p + 1 for p in preview_pages]
        available_page_numbers = [p + 1 for p in available_pages]
        missing_page_numbers = [p + 1 for p in missing_pages]
        truncated_page_numbers = [p + 1 for p in truncated_pages]

        prompt = three_u_mel_vlm_enhance_prompt(
            page=(available_pages[0] if available_pages else pages_for_preview[0]) + 1,
            ata_code=context.get("ata_code", ""),
            nav_title=context.get("navigation_title", ""),
            ocr_text=prompt_text,
            cross_page=len(pages_for_preview) > 1,
            target_name=context.get("target_name", ""),
            doc_span=doc_span,
            chunk_pages=chunk_page_numbers,
        )

        if image_payload is None:
            self._logger.info(
                "🟡 3U_MEL VLM 跳过块=%s 原因=缺少页面截图 页码=%s",
                index,
                chunk_page_numbers,
            )
            return EnhancedChunk(content=chunk)

        if missing_pages:
            self._logger.info(
                "🟡 3U_MEL VLM 预览不完整 块=%s 可用页=%s 缺失页=%s",
                index,
                available_page_numbers,
                missing_page_numbers,
            )

        response_text: Optional[str] = None
        response_origin = None
        provider_meta: Dict[str, Any] = {}
        primary_exc: Optional[Exception] = None

        if self._vision_model is not None:
            try:
                response_text = self._vision_model.describe_with_prompt(image_payload, prompt)
                response_origin = "primary"
            except Exception as exc:  # pragma: no cover - defensive
                primary_exc = exc
                self._logger.warning("🔴 3U_MEL VLM 主通道调用失败 块=%s 原因=%s", index, exc)

        if response_text is None:
            response_text, provider_meta = self._invoke_fallback(image_payload, prompt, index, primary_exc)
            if response_text:
                response_origin = "fallback"

        if response_text is None:
            self._logger.error(
                "🔴 3U_MEL VLM无法提供增强结果 chunk=%s，已回退至OCR文本。",
                index,
            )
            return EnhancedChunk(content=chunk)

        payload = self._parse_response(response_text)
        payload = self._post_process_payload(payload, context)
        if payload:
            payload.setdefault("preview_pages", available_page_numbers)
            if truncated_page_numbers:
                payload.setdefault(
                    "warnings", []
                ).append(
                    f"视觉预览仅覆盖页码 {', '.join(map(str, preview_page_numbers))}，其余页面退回OCR文本"
                )
            if missing_page_numbers:
                payload.setdefault(
                    "warnings", []
                ).append(
                    f"缺失页面截图：{', '.join(map(str, missing_page_numbers))}"
                )
            warnings_list = payload.get("warnings")
            if isinstance(warnings_list, list):
                payload["warnings"] = list(dict.fromkeys(warnings_list))
        if not payload:
            self._logger.info("🟠 3U_MEL VLM 返回空结构 块=%s", index)
            return EnhancedChunk(content=self._compose_chunk(clean_text, tags))

        enhanced_chunks: List[EnhancedChunk] = []
        for sub in payload.get("chunks", []):
            text = (sub.get("text_markdown") or sub.get("text") or "").strip()
            if not text:
                continue
            chunk_text = self._compose_chunk(text, tags)
            metadata = {
                "source": "vlm",
                "model_name": self._model_name,
                "page": payload.get("page"),
                "ata_hint": payload.get("ata_hint"),
                "chunk_role": sub.get("role"),
                "ident": sub.get("ident"),
                "ata_outer": sub.get("ata_outer"),
                "inner_codes": sub.get("inner_codes"),
                "criteria": sub.get("criteria"),
                "applicability": sub.get("applicability"),
                "notes": sub.get("notes"),
                "raw_vlm_chunk": sub,
                "preview_pages": available_page_numbers,
                "preview_missing_pages": missing_page_numbers,
                "preview_truncated_pages": truncated_page_numbers,
                "preview_image_mode": "stitched" if len(available_pages) > 1 else "single",
                "vlm_origin": response_origin or provider_meta.get("vlm_origin", "primary"),
            }
            for key, value in provider_meta.items():
                if value is None:
                    continue
                metadata[key] = value
            enhanced_chunks.append(EnhancedChunk(content=chunk_text, metadata=metadata))

        if enhanced_chunks:
            # If VLM split the chunk, return the first item and keep the rest appended.
            self._logger.info(
                "🟢 3U_MEL VLM 增强成功 块=%s 子块数=%s 预览页=%s",
                index,
                len(enhanced_chunks),
                available_page_numbers,
            )
            return self._flatten_enhanced_chunks(enhanced_chunks)

        self._logger.info("🟠 3U_MEL VLM 未生成可用文本 块=%s", index)
        return EnhancedChunk(content=self._compose_chunk(clean_text, tags))

    def _post_process_payload(self, payload: Optional[Dict[str, Any]], context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not isinstance(payload, dict):
            return None

        chunks = payload.get("chunks")
        if not isinstance(chunks, list):
            payload["chunks"] = []
            chunks = payload["chunks"]

        current_ata_outer: Optional[str] = None
        current_ident: Optional[str] = None
        inferred_warnings: List[str] = []

        previous_struct: Optional[Dict[str, Any]] = None

        for sub in chunks:
            if not isinstance(sub, dict):
                continue

            role = (sub.get("role") or "").strip().lower()
            if role not in {"outer_ata", "ident", "inner_ata", "note", "table", "other"}:
                role = "other"
                sub["role"] = "other"

            # normalize notes field
            notes = sub.get("notes")
            if notes is None:
                sub["notes"] = []
            elif isinstance(notes, str):
                sub["notes"] = [notes]
            elif not isinstance(notes, list):
                sub["notes"] = [str(notes)]

            # normalize text_markdown to string
            if sub.get("text_markdown") is None:
                sub["text_markdown"] = ""

            text_md = sub.get("text_markdown", "")

            if role == "outer_ata":
                if not sub.get("ata_outer"):
                    extracted = self._extract_outer_ata(text_md) or context.get("ata_code")
                    if extracted:
                        sub["ata_outer"] = extracted
                current_ata_outer = sub.get("ata_outer") or current_ata_outer
                current_ident = None  # reset when new outer ATA begins
            else:
                if not sub.get("ata_outer") and current_ata_outer:
                    sub["ata_outer"] = current_ata_outer

            if role == "ident":
                if not sub.get("ident"):
                    extracted_ident = self._extract_ident(text_md)
                    if extracted_ident:
                        sub["ident"] = extracted_ident
                ident_value = sub.get("ident")
                if self._looks_like_ident(ident_value or ""):
                    current_ident = ident_value
                else:
                    # Misclassified ident, downgrade based on heuristics
                    if self._looks_like_catalog(text_md):
                        sub["role"] = "other"
                        role = "other"
                        sub.setdefault("notes", []).append("Converted catalog/header content to other role")
                    else:
                        inferred_codes = self._extract_inner_codes(text_md)
                        if inferred_codes:
                            sub["role"] = "inner_ata"
                            sub["inner_codes"] = inferred_codes
                            role = "inner_ata"
                        else:
                            sub["role"] = "other"
                            role = "other"
                            sub.setdefault("notes", []).append("Role normalized to other due to missing ident pattern")
                    current_ident = None

            if role in {"inner_ata", "note", "table"} and current_ident and not sub.get("ident"):
                sub["ident"] = current_ident

            if role == "inner_ata":
                inner_codes = sub.get("inner_codes")
                if not inner_codes:
                    inferred = self._extract_inner_codes(text_md)
                    if inferred:
                        sub["inner_codes"] = inferred
                    else:
                        sub["inner_codes"] = []
                        inferred_warnings.append("inner_ata chunk missing inner_codes")
                        sub.setdefault("notes", []).append("inner_codes missing; please verify")

                if not sub.get("ata_outer") and current_ata_outer:
                    sub["ata_outer"] = current_ata_outer
                if not sub.get("ident") and current_ident:
                    sub["ident"] = current_ident

            if role == "other" and self._looks_like_catalog(text_md):
                sub.setdefault("notes", []).append("Catalog or header/footer content")

            if role != "outer_ata" and not sub.get("ata_outer") and current_ata_outer:
                sub["ata_outer"] = current_ata_outer

            if role != "outer_ata" and not sub.get("ata_outer"):
                inferred_warnings.append("Chunk missing ata_outer after normalization")

            if role in {"inner_ata", "note", "table"} and not sub.get("ident"):
                inferred_warnings.append(f"{role} chunk missing ident reference")

            if "有意留空" in text_md:
                sub.setdefault("notes", []).append("页面标注“有意留空”，上一页内容延续")
                if role not in {"note", "other"}:
                    sub["role"] = "note"
                    role = "note"
                if not sub.get("ident") and previous_struct and previous_struct.get("ident"):
                    sub["ident"] = previous_struct["ident"]
                if not sub.get("ata_outer") and previous_struct and previous_struct.get("ata_outer"):
                    sub["ata_outer"] = previous_struct["ata_outer"]

            if role != "other" or self._contains_ident_hint(text_md):
                previous_struct = sub

        warnings = payload.get("warnings")
        if warnings is None:
            payload["warnings"] = []
        elif isinstance(warnings, str):
            payload["warnings"] = [warnings]
        elif not isinstance(warnings, list):
            payload["warnings"] = [str(warnings)]

        if inferred_warnings:
            payload.setdefault("warnings", []).extend(inferred_warnings)

        warnings_list = payload.get("warnings", [])
        if isinstance(warnings_list, list):
            payload["warnings"] = list(dict.fromkeys(warnings_list))

        return payload

    def _filter_ocr_text(self, text: str) -> str:
        if not text:
            return text
        filtered_lines: List[str] = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                filtered_lines.append(line)
                continue
            lower = line.lower()
            if re.search(r"\btable of contents\b", lower):
                continue
            if "目录" in line or "序页" in line:
                continue
            if re.search(r"mi-[0-9\-]+\s+p\s*\d+/\d+", lower):
                continue
            if "fleet" in lower and "mi" in lower and "p" in lower:
                continue
            if re.search(r"\bmelt?\b.*\d{2}\s?(?:may|jun|jul|aug|sep|oct|nov|dec)\b", lower):
                continue
            if line.upper().startswith("MEL") and ("→" in line or "←" in line):
                continue
            if re.fullmatch(r"[-─━=]{3,}", line):
                continue
            if "有意留空" in line:
                filtered_lines.append("【有意留空，上一页内容延续】")
                continue
            if re.search(r"\bCSC\b", line) and "FLEET" in line:
                continue
            if re.search(r"\bA319/A320/A321\b", line):
                continue
            filtered_lines.append(raw_line)
        return "\n".join(filtered_lines)

    def _extract_outer_ata(self, text: str) -> Optional[str]:
        if not text:
            return None
        match = re.search(r"\b\d{2}-\d{2}-\d{2}\b", text)
        return match.group(0) if match else None

    def _extract_ident(self, text: str) -> Optional[str]:
        if not text:
            return None
        match = re.search(r"MI-\d{2}-\d{2}-\d{8}\.\d{7}", text)
        return match.group(0) if match else None

    def _extract_inner_codes(self, text: str) -> List[str]:
        if not text:
            return []
        codes = re.findall(r"\b\d{2}-\d{2}-\d{2}[A-Z]\b", text)
        deduped: List[str] = []
        for code in codes:
            if code not in deduped:
                deduped.append(code)
        return deduped

    def _looks_like_ident(self, ident: str) -> bool:
        if not ident:
            return False
        return bool(re.fullmatch(r"MI-\d{2}-\d{2}-\d{8}\.\d{7}", ident.strip()))

    def _looks_like_catalog(self, text: str) -> bool:
        if not text:
            return False
        lowered = text.lower()
        if self._contains_ident_hint(text):
            return False
        if "目录" in text or "序页" in text:
            return True
        if "table of contents" in lowered:
            return True
        if re.search(r"\bpage\s+\d+", lowered) and "melt" in lowered:
            return True
        if re.search(r"\bP\s*\d+/\d+", text):
            return True
        if re.search(r"CSC\s+[A-Z0-9/\-]+\s+FLEET", text):
            return True
        if "接上页" in text or "接下页" in text:
            return True
        if re.search(r"\.{5,}", text):
            return True
        return False

    def _contains_ident_hint(self, text: str) -> bool:
        if not text:
            return False
        if "识别" in text or "标准" in text or "适用于" in text:
            return True
        return bool(re.search(r"MI-\d{2}-\d{2}-\d{8}\.\d{7}", text))

    def _flatten_enhanced_chunks(self, chunks: List[EnhancedChunk]) -> EnhancedChunk:
        if len(chunks) == 1:
            return chunks[0]
        combined = "\n\n".join(chunk.content for chunk in chunks)
        merged_meta: Dict[str, Any] = {
            "source": "vlm",
            "model_name": self._model_name,
            "sub_chunks": [chunk.metadata for chunk in chunks if chunk.metadata],
        }
        base_meta = next((chunk.metadata for chunk in chunks if chunk.metadata), None)
        if isinstance(base_meta, dict):
            for key in ("preview_pages", "preview_missing_pages", "preview_truncated_pages", "preview_image_mode"):
                value = base_meta.get(key)
                if value:
                    merged_meta[key] = value
        return EnhancedChunk(content=combined, metadata=merged_meta)

    def _compose_chunk(self, text: str, tags: str) -> str:
        if not tags:
            return text
        normalized = text.rstrip()
        if normalized and not normalized.endswith("\n"):
            normalized += "\n"
        return normalized + tags

    def _ensure_model(self) -> None:
        if self._vision_model is not None or not self._primary_allowed:
            return
        self._primary_ready = False
        llm_name = _env_get("FTMS_VLM_MODEL_NAME", "THREE_U_MEL_VLM_MODEL_NAME")
        kwargs: Dict[str, Any] = {
            "tenant_id": self._tenant_id,
            "llm_type": LLMType.IMAGE2TEXT,
            "lang": self._lang,
        }
        if llm_name:
            kwargs["llm_name"] = llm_name
        override_source = "env_override" if llm_name else "tenant_default"
        self._vision_model = LLMBundle(**kwargs)
        resolved_model_name = llm_name or getattr(self._vision_model, "llm_name", None)
        if not resolved_model_name:
            resolved_model_name = getattr(getattr(self._vision_model, "mdl", None), "model_name", None)
        self._model_name = resolved_model_name
        resolved_factory = getattr(self._vision_model, "llm_factory", None)
        if not resolved_factory:
            resolved_factory = getattr(getattr(self._vision_model, "mdl", None), "_FACTORY_NAME", None)
        client = getattr(getattr(self._vision_model, "mdl", None), "client", None)
        api_base = getattr(client, "base_url", None) if client else None
        safe_factory = resolved_factory or "<unknown>"
        safe_model = resolved_model_name or "<unknown>"
        safe_base = api_base or "<default>"
        self._logger.info(
            "🧠 3U_MEL: 视觉模型初始化成功 source=%s, model=%s, factory=%s, api_base=%s",
            override_source,
            safe_model,
            safe_factory,
            safe_base,
        )
        self._primary_ready = True

    def _extract_tags(self, chunk: str) -> str:
        tags = re.findall(r"@@[0-9\-\t.]+?##", chunk)
        return "".join(tags)

    def _safe_extract_positions(self, parser, chunk: str) -> List[Tuple[List[int], float, float, float, float]]:
        try:
            return parser.extract_positions(chunk)
        except Exception:
            return []

    def _prepare_image_payload(
        self,
        parser,
        pages: Sequence[int],
    ) -> Tuple[Optional[bytes], List[int], List[int], List[int], List[int]]:
        """
        Prepare stitched image bytes for the given set of absolute (0-based) page indices.

        Returns:
            payload: JPEG bytes or None if unavailable
            preview_pages: pages attempted for preview (subset of pages)
            available_pages: pages that yielded an image
            missing_pages: preview pages with no available image
            truncated_pages: remaining pages outside preview (due to limit)
        """
        if not pages:
            return None, [], [], [], []

        preview_pages, truncated_pages = self._select_preview_pages(pages)
        if not preview_pages:
            return None, [], [], truncated_pages, []

        collected = self._collect_page_images(parser, preview_pages)
        available_pages = [page for page, _ in collected]
        missing_pages = [page for page in preview_pages if page not in available_pages]

        if not collected:
            return None, preview_pages, available_pages, missing_pages, truncated_pages

        if len(collected) == 1:
            payload_bytes = self._image_to_bytes(collected[0][1])
        else:
            stitched = self._stitch_page_images([img for _, img in collected])
            payload_bytes = self._image_to_bytes(stitched)

        return payload_bytes, preview_pages, available_pages, missing_pages, truncated_pages

    def _select_preview_pages(self, pages: Sequence[int]) -> Tuple[List[int], List[int]]:
        unique_pages: List[int] = []
        for page in pages:
            if page not in unique_pages:
                unique_pages.append(page)
        if not unique_pages:
            return [], []
        preview = unique_pages[: self._max_image_pages]
        truncated = unique_pages[len(preview) :] if len(unique_pages) > len(preview) else []
        return preview, truncated

    def _collect_page_images(self, parser, pages: Sequence[int]) -> List[Tuple[int, Image.Image]]:
        images: List[Tuple[int, Image.Image]] = []
        for page_idx in pages:
            pil_image = self._page_to_image(parser, page_idx)
            if pil_image is not None:
                images.append((page_idx, pil_image))
            else:
                page_images_attr = getattr(parser, "page_images", None)
                page_images_count = len(page_images_attr) if page_images_attr else 0
                self._logger.debug(
                    "🟡 3U_MEL VLM 页面截图缺失 page=%s parser.page_from=%s page_images=%s",
                    page_idx + 1,
                    getattr(parser, "page_from", None),
                    page_images_count,
                )
        return images

    def _page_to_image(self, parser, page_idx: int) -> Optional[Image.Image]:
        images = getattr(parser, "page_images", None)
        page_from = getattr(parser, "page_from", 0)
        if not images:
            return None
        rel_idx = page_idx - page_from
        if not (0 <= rel_idx < len(images)):
            if 0 <= page_idx < len(images):
                rel_idx = page_idx
            else:
                return None
        image = images[rel_idx]
        if isinstance(image, Image.Image):
            pil_image = image
        else:
            pil_image = Image.fromarray(image) if hasattr(image, "__array__") else None
        if pil_image is None:
            return None
        if pil_image.mode != "RGB":
            pil_image = pil_image.convert("RGB")
        return pil_image.copy()

    def _stitch_page_images(self, images: Sequence[Image.Image]) -> Image.Image:
        if not images:
            raise ValueError("No images provided for stitching")
        if len(images) == 1:
            return images[0].copy()

        prepared: List[Image.Image] = []
        target_width = max(img.width for img in images)
        if self._max_stitch_width and target_width > self._max_stitch_width:
            target_width = self._max_stitch_width

        for img in images:
            working = img.copy() if img.mode == "RGB" else img.convert("RGB")
            if target_width and working.width > target_width:
                ratio = target_width / float(working.width)
                new_height = max(1, int(working.height * ratio))
                working = working.resize((target_width, new_height), Image.LANCZOS)
            elif target_width and working.width < target_width:
                padded = Image.new("RGB", (target_width, working.height), color="white")
                offset_x = (target_width - working.width) // 2
                padded.paste(working, (offset_x, 0))
                working = padded
            prepared.append(working)

        gap = self._stitch_gap_px if len(prepared) > 1 else 0
        total_height = sum(img.height for img in prepared) + gap * (len(prepared) - 1)
        canvas = Image.new("RGB", (prepared[0].width, total_height), color="white")
        offset_y = 0
        for img in prepared:
            canvas.paste(img, (0, offset_y))
            offset_y += img.height + gap

        return canvas

    def _image_to_bytes(self, image: Image.Image) -> bytes:
        with io.BytesIO() as buffer:
            image.convert("RGB").save(buffer, format="JPEG", quality=90)
            return buffer.getvalue()

    def _page_to_bytes(self, parser, page_idx: int) -> Optional[bytes]:
        image = self._page_to_image(parser, page_idx)
        if image is None:
            return None
        return self._image_to_bytes(image)

    def _parse_response(self, response: str) -> Optional[Dict[str, Any]]:
        if not response:
            return None
        cleaned = response.strip()
        cleaned = re.sub(r"^```json", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"```$", "", cleaned)
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError:
            try:
                parsed = json.loads(repair_json(cleaned))
            except Exception:
                self._logger.debug("3U_MEL: 无法解析VLM返回JSON，截断内容=%s", response[:200])
                return None
        return self._normalize_payload(parsed)

    def _normalize_payload(self, payload: Any) -> Optional[Dict[str, Any]]:
        if isinstance(payload, dict):
            return payload
        if isinstance(payload, list):
            dict_items = [item for item in payload if isinstance(item, dict)]
            if not dict_items:
                self._logger.debug("3U_MEL: VLM 返回列表但不包含结构化字典，截断内容=%r", payload[:1])
                return None
            merged: Dict[str, Any] = dict(dict_items[0])
            if len(dict_items) > 1:
                extra_chunks: List[Dict[str, Any]] = []
                for extra in dict_items[1:]:
                    chunks = extra.get("chunks")
                    if isinstance(chunks, list):
                        extra_chunks.extend(chunks)
                if extra_chunks:
                    base_chunks = merged.get("chunks")
                    if isinstance(base_chunks, list):
                        merged["chunks"] = base_chunks + extra_chunks
                    elif base_chunks is None:
                        merged["chunks"] = extra_chunks
                    else:
                        merged["chunks"] = [base_chunks] + extra_chunks
                    merged.setdefault("warnings", []).append("VLM 返回多条记录，已合并 chunk 列表。")
            return merged
        self._logger.debug("3U_MEL: VLM 返回非结构化类型 %s", type(payload).__name__)
        return None

    def _default_enabled(self) -> bool:
        raw = _env_get("FTMS_VLM_ENABLED", "THREE_U_MEL_VLM_ENABLED", default="true")
        return str(raw).lower() in {"1", "true", "yes", "on"}

    def _resolve_fallback_config(self) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        raw_flag = _env_get("FTMS_VLM_FALLBACK_ENABLED", "THREE_U_MEL_VLM_FALLBACK_ENABLED", default="true")
        flag = str(raw_flag).lower() in {"1", "true", "yes", "on"}
        if not flag:
            return None, "备用通道已禁用"
        api_key = _env_get(
            "FTMS_VLM_FALLBACK_API_KEY",
            "THREE_U_MEL_VLM_FALLBACK_API_KEY",
            "FTMS_QWEN_MODEL_API_KEY",
        )
        model = _env_get(
            "FTMS_VLM_FALLBACK_MODEL",
            "THREE_U_MEL_VLM_FALLBACK_MODEL",
            "FTMS_QWEN_VL_MODEL_NAME",
            "FTMS_QWEN_MODEL_NAME",
        )
        if not api_key or not model:
            return None, "缺少备用通道 API key 或模型名"
        base_url = _env_get(
            "FTMS_VLM_FALLBACK_BASE_URL",
            "THREE_U_MEL_VLM_FALLBACK_BASE_URL",
            "FTMS_QWEN_MODEL_URL",
        )
        provider = _env_get(
            "FTMS_VLM_FALLBACK_PROVIDER",
            "THREE_U_MEL_VLM_FALLBACK_PROVIDER",
            default="openai",
        )
        max_tokens = self._safe_int_env(
            8000,
            "FTMS_VLM_FALLBACK_MAX_TOKENS",
            "THREE_U_MEL_VLM_FALLBACK_MAX_TOKENS",
            "FTMS_VLM_MAX_TOKENS",
            "THREE_U_MEL_VLM_MAX_TOKENS",
        )
        reasoning_effort = _env_get(
            "FTMS_VLM_FALLBACK_REASONING",
            "THREE_U_MEL_VLM_FALLBACK_REASONING",
        )
        try:  # validate dependency availability early
            from openai import OpenAI  # noqa: F401
        except ImportError as exc:  # pragma: no cover - dependency guard
            return None, f"openai 包未安装: {exc}"
        return (
            {
                "key": api_key,
                "base_url": base_url,
                "model": model,
                "provider": provider,
                "max_tokens": max_tokens,
                "reasoning_effort": reasoning_effort,
            },
            None,
        )

    def _safe_int_env(self, default: int, *keys: str) -> int:
        for key in keys:
            if key is None:
                continue
            raw = os.getenv(key)
            if raw is None:
                continue
            try:
                return int(raw)
            except ValueError:
                self._logger.warning("⚠️ 3U_MEL 配置项 %s=%r 非法，使用默认值 %s", key, raw, default)
                return default
        return default

    def _ensure_fallback_client(self):
        if not self._fallback_config:
            return None
        if self._fallback_client is not None:
            return self._fallback_client
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - dependency guard
            self._fallback_reason = f"openai 包未安装: {exc}"
            self._fallback_config = None
            return None
        kwargs = {"api_key": self._fallback_config["key"]}
        if self._fallback_config.get("base_url"):
            kwargs["base_url"] = self._fallback_config["base_url"]
        try:
            self._fallback_client = OpenAI(**kwargs)
            return self._fallback_client
        except Exception as exc:  # pragma: no cover - defensive
            self._fallback_reason = f"备用通道初始化失败: {exc}"
            self._fallback_config = None
            return None

    def _invoke_fallback(
        self,
        image_payload: bytes,
        prompt: str,
        index: int,
        primary_exc: Optional[Exception],
    ) -> Tuple[Optional[str], Dict[str, Any]]:
        if not self._fallback_config:
            if primary_exc:
                self._logger.warning(
                    "🛑 3U_MEL VLM 备用通道不可用 块=%s 原因=%s",
                    index,
                    self._fallback_reason or "未配置",
                )
            return None, {}

        client = self._ensure_fallback_client()
        if client is None:
            return None, {}

        if primary_exc:
            self._logger.info(
                "🟠 3U_MEL VLM 主通道失败 块=%s 原因=%s，尝试备用提供方=%s",
                index,
                primary_exc,
                self._fallback_config.get("provider"),
            )

        content = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": self._bytes_to_data_url(image_payload)}},
        ]
        request: Dict[str, Any] = {
            "model": self._fallback_config["model"],
            "messages": [{"role": "user", "content": content}],
            "temperature": 0.0,
            "max_tokens": self._fallback_config["max_tokens"],
        }
        if self._fallback_config.get("reasoning_effort"):
            request["reasoning_effort"] = self._fallback_config["reasoning_effort"]

        try:
            response = client.chat.completions.create(**request)
            message = response.choices[0].message
            rendered = self._render_fallback_message(message)
            usage = getattr(response, "usage", None)
            prompt_tokens = None
            completion_tokens = None
            if usage is not None:
                prompt_tokens = getattr(usage, "prompt_tokens", None) or getattr(usage, "input_tokens", None)
                completion_tokens = getattr(usage, "completion_tokens", None) or getattr(usage, "output_tokens", None)
            self._model_name = self._fallback_config["model"]
            meta: Dict[str, Any] = {
                "vlm_origin": "fallback",
                "vlm_provider": self._fallback_config.get("provider"),
                "model_name": self._fallback_config["model"],
            }
            if prompt_tokens is not None:
                meta["fallback_prompt_tokens"] = prompt_tokens
            if completion_tokens is not None:
                meta["fallback_completion_tokens"] = completion_tokens
            self._logger.info(
                "🟢 3U_MEL VLM 备用通道成功 块=%s 提供方=%s 模型=%s",
                index,
                self._fallback_config.get("provider"),
                self._fallback_config["model"],
            )
            return rendered, meta
        except Exception as exc:  # pragma: no cover - defensive
            self._logger.error("🔴 3U_MEL VLM 备用通道失败 块=%s 原因=%s", index, exc)
            return None, {}

    def _bytes_to_data_url(self, payload: bytes) -> str:
        encoded = base64.b64encode(payload).decode("ascii")
        return f"data:image/jpeg;base64,{encoded}"

    def _render_fallback_message(self, message) -> str:
        content = getattr(message, "content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            segments: List[str] = []
            for item in content:
                if isinstance(item, str):
                    segments.append(item)
                elif isinstance(item, dict):
                    text = item.get("text") or item.get("content") or ""
                    if text:
                        segments.append(str(text))
                else:
                    text = getattr(item, "text", "") or getattr(item, "content", "")
                    if text:
                        segments.append(str(text))
            return "".join(segments).strip()
        return str(content)

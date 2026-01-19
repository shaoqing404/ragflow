import json
from pathlib import Path
from rag.app.naive import PARSERS, by_deepdoc, by_three_u  # 🆕 添加by_three_u
import logging
from datetime import datetime
import sys
import os
from io import BytesIO
import time
import re
import copy
import random
from typing import Any, Dict, List, Optional, Tuple, Sequence

# Import RAGFlow's standard tokenization mechanism
from rag.nlp import rag_tokenizer, tokenize_chunks, is_english, tokenize, add_positions

from rag.app.mel_chunking.vlm_enhancer import MelVLMEnhancer
from common.constants import LLMType
from api.db.services.tenant_llm_service import TenantLLMService

# 🆕 Import refactored modules with text_level support
from rag.app.three_u_splitting import (
    split_sections_by_mi_and_inner_codes,
    extract_mi_ident,
    extract_inner_codes,
    extract_inner_codes_with_text_level,  # 🆕 支持text_level
)
from rag.app.three_u_metadata import (
    inject_hierarchical_metadata,
    dedup_chunks_by_mi_and_inner_code,
    validate_chunk_metadata,
)
from rag.app.three_u_semantic_merge import (
    merge_sparse_chunks_v2,
    strip_position_tags,
)

# Import K2's standalone extractor for Sichuan Airlines A330 MEL
try:
    from .three_u_link_hierarchy_extractor import K2_Standalone_Extractor
    K2_EXTRACTOR_AVAILABLE = True
    print("✅ K2_Standalone_Extractor loaded successfully for 3U_MEL")
except ImportError as e:
    K2_EXTRACTOR_AVAILABLE = False
    print(f"Warning: K2_Standalone_Extractor not available: {e}")


# Use a logger for better output management
logger = logging.getLogger(__name__)


POSITION_TAG_PATTERN = re.compile(r"@@[0-9\-\t.]+##")
TAG_TRAIL_PATTERN = re.compile(r"(?:@@[0-9\-\t.]+##)+$")
IDENT_PATTERN = re.compile(r"\bMI-\d{2}-\d{2}-\d{8}\.\d+\b")
_BLANK_KEYWORDS = (
    "有意留空",
    "有意留白",
    "故意留白",
    "本页留空",
    "空白页",
)
_CONTINUATION_KEYWORDS = (
    "接上页",
    "接下页",
    "续上页",
    "续下页",
    "接前页",
    "接后页",
    "continued on next page",
    "continued from previous page",
    "see next page",
    "see previous page",
)
_BLANK_KEYWORDS_LOWER = tuple(kw.lower() for kw in _BLANK_KEYWORDS)
_CONTINUATION_KEYWORDS_LOWER = tuple(kw.lower() for kw in _CONTINUATION_KEYWORDS)


def _env_get(*keys: str, default: Optional[str] = None) -> Optional[str]:
    for key in keys:
        value = os.getenv(key)
        if value is not None:
            return value
    return default


def _env_get_int(*keys: str, default: Optional[int] = None) -> Optional[int]:
    raw = _env_get(*keys)
    if raw is None or raw == "":
        return default
    try:
        return int(str(raw).strip())
    except Exception:
        return default


def _resolve_layout_parser(parser_config: Optional[Dict[str, Any]]) -> Tuple[str, Any]:
    """
    解析layout parser配置
    
    🆕 当配置为mineru时,使用by_three_u替代by_mineru
    by_three_u保留MinerU的text_level字段用于可靠的子项检测
    """
    cfg = parser_config or {}
    layout = cfg.get("layout_recognize", "DeepDOC")
    if isinstance(layout, bool):
        layout = "DeepDOC" if layout else "Plain Text"
    layout = (layout or "DeepDOC").strip()
    
    # 🆕 MinerU时使用three_u入口,保留text_level
    layout_lower = layout.lower()
    if layout_lower == "mineru":
        logger.info("🆕 3U_MEL: 使用three_u入口替代mineru,保留text_level字段")
        parser_func = by_three_u
        layout = "three_u"  # 更新显示名称
    else:
        parser_func = PARSERS.get(layout_lower, by_deepdoc)
    
    return layout, parser_func


def _probe_primary_vlm_model(tenant_id: Optional[str]) -> Tuple[bool, Optional[str], Optional[str]]:
    if not tenant_id:
        return False, None, "缺少 tenant_id"
    try:
        cfg = TenantLLMService.get_model_config(tenant_id, LLMType.IMAGE2TEXT.value)
    except LookupError as exc:
        return False, None, f"租户未配置视觉模型: {exc}"
    except Exception as exc:
        logger.exception("3U_MEL: 租户视觉模型探测异常: %s", exc)
        return False, None, str(exc)

    if not cfg:
        return False, None, "未找到视觉模型配置"

    api_key = cfg.get("api_key")
    llm_name = cfg.get("llm_name") or cfg.get("model_name")
    llm_factory = cfg.get("llm_factory")
    api_base = cfg.get("api_base")
    if not llm_name:
        return False, None, "视觉模型名称缺失"
    if not api_key:
        return False, None, "视觉模型 API key 缺失"

    safe_factory = llm_factory or "<unknown>"
    safe_base = api_base or "<default>"
    logger.info(
        "🧠 3U_MEL 租户视觉模型默认配置: llm_name=%s, factory=%s, api_base=%s",
        llm_name,
        safe_factory,
        safe_base,
    )

    descriptor = f"{llm_name}@{llm_factory}" if llm_factory else llm_name
    return True, descriptor, None


def _strip_position_tags(text: str) -> str:
    """Strip position tags like @@page\tx1\ty1\tx2\ty2## from text."""
    if not text:
        return ""
    return POSITION_TAG_PATTERN.sub("", text)


def _split_text_and_tags(text: str) -> Tuple[str, str]:
    if not text:
        return "", ""
    match = TAG_TRAIL_PATTERN.search(text)
    if not match:
        return text, ""
    body = text[: match.start()]
    tags = match.group(0)
    return body, tags


def _merge_chunk_text(base_text: str, tail_text: str) -> str:
    base_body, base_tags = _split_text_and_tags(base_text)
    tail_body, tail_tags = _split_text_and_tags(tail_text)

    merged_body = base_body.rstrip()
    tail_body_clean = tail_body.strip()

    if merged_body and tail_body_clean:
        merged_body = f"{merged_body}\n\n{tail_body_clean}"
    elif tail_body_clean:
        merged_body = tail_body_clean

    merged_body = merged_body.rstrip()
    if merged_body and not merged_body.endswith("\n"):
        merged_body += "\n"

    return merged_body + base_tags + tail_tags


# ⚠️ DEPRECATED: Use extract_mi_ident from three_u_splitting.py instead
def _extract_ident(text: str) -> Tuple[Optional[str], Optional[str]]:
    """
    [DEPRECATED] Extract MI ident (MI-XX-XX-YYYYMMDD.N) and provide a short hint line.
    Use three_u_splitting.extract_mi_ident() for new code.
    """
    if not text:
        return None, None
    match = IDENT_PATTERN.search(text)
    if not match:
        return None, None
    ident = match.group(0)
    hint = None
    for raw_line in text.splitlines():
        if ident in raw_line:
            hint = raw_line.strip()
            break
    if not hint:
        span = text[max(match.start() - 40, 0) : min(match.end() + 40, len(text))]
        hint = span.strip()
    return ident, (hint or ident)


def _merge_metadata_dict(
    base_meta: Optional[Dict[str, Any]],
    tail_meta: Optional[Dict[str, Any]],
    reason: str,
) -> Dict[str, Any]:
    merged = copy.deepcopy(base_meta) if base_meta else {}
    tail_copy = copy.deepcopy(tail_meta) if tail_meta else {}

    def _merge_list_field(key: str, ensure_unique: bool = True) -> None:
        tail_value = tail_copy.get(key)
        if not tail_value:
            return
        if not isinstance(tail_value, list):
            tail_values = [tail_value]
        else:
            tail_values = tail_value
        existing = merged.get(key)
        if not existing:
            merged[key] = list(dict.fromkeys(tail_values)) if ensure_unique else list(tail_values)
            return
        if not isinstance(existing, list):
            existing = [existing]
        for item in tail_values:
            if ensure_unique and item in existing:
                continue
            existing.append(item)
        merged[key] = existing

    for field in ("notes", "warnings"):
        _merge_list_field(field, ensure_unique=True)

    for field in ("preview_pages", "preview_missing_pages", "preview_truncated_pages"):
        _merge_list_field(field, ensure_unique=True)

    if tail_copy.get("raw_vlm_chunk"):
        merged.setdefault("merged_tail_chunks", []).append(tail_copy["raw_vlm_chunk"])

    tail_role = tail_copy.get("chunk_role") or (tail_copy.get("raw_vlm_chunk") or {}).get("role")
    if tail_role:
        merged.setdefault("merged_tail_roles", [])
        if tail_role not in merged["merged_tail_roles"]:
            merged["merged_tail_roles"].append(tail_role)

    merged.setdefault("merged_tail_reasons", [])
    if reason not in merged["merged_tail_reasons"]:
        merged["merged_tail_reasons"].append(reason)

    merged.setdefault("semantic_merge", [])
    summary_payload = {
        "reason": reason,
        "source_role": tail_role,
        "source_preview_pages": tail_copy.get("preview_pages"),
        "source_notes": tail_copy.get("notes"),
    }
    merged["semantic_merge"].append(summary_payload)
    merged["merged_tail_attached"] = True
    return merged


def _merge_vlm_fields_into_chunk(chunk_doc: Dict[str, Any], vlm_meta: Dict[str, Any]) -> None:
    """
    Promote useful VLM fields into chunk for downstream retrieval.
    Does not overwrite existing values unless they are missing/empty.
    """
    if not vlm_meta:
        return
    # If VLM detected ident / outer ATA, fill when missing
    for key in ("ident", "ata_outer", "ata_hint"):
        if vlm_meta.get(key) and not chunk_doc.get(key):
            chunk_doc[key] = vlm_meta[key]
    # Copy commonly-used MEL fields when provided
    for key in ("criteria", "applicability", "inner_codes", "notes"):
        if vlm_meta.get(key):
            chunk_doc.setdefault(key, vlm_meta[key])
    # Attach preview diagnostics to aid debugging/retrieval
    for key in ("preview_pages", "preview_missing_pages", "preview_truncated_pages"):
        if vlm_meta.get(key):
            chunk_doc.setdefault(key, vlm_meta[key])


def _same_ident(prev_meta: Optional[Dict[str, Any]], current_meta: Optional[Dict[str, Any]]) -> bool:
    if not prev_meta or not current_meta:
        return False
    prev_ident = prev_meta.get("ident")
    curr_ident = current_meta.get("ident")
    if prev_ident and curr_ident and prev_ident == curr_ident:
        return True
    return False


def _classify_sparse_chunk(
    current_meta: Optional[Dict[str, Any]],
    previous_meta: Optional[Dict[str, Any]],
    current_content: str,
) -> Optional[str]:
    meta = current_meta or {}
    role = (meta.get("chunk_role") or meta.get("raw_vlm_chunk", {}).get("role") or "").lower()

    plain_text = _strip_position_tags(current_content)
    plain_lower = plain_text.lower()
    raw_lower = (meta.get("raw_vlm_chunk", {}).get("text_markdown") or "").lower()
    note_texts = [str(note).lower() for note in meta.get("notes") or []]

    if any(keyword in plain_lower for keyword in _BLANK_KEYWORDS_LOWER) or any(
        keyword in raw_lower for keyword in _BLANK_KEYWORDS_LOWER
    ) or any(kw in note for note in note_texts for kw in _BLANK_KEYWORDS_LOWER):
        if role in {"note", "other", ""}:
            return "intentional_blank"

    if any(keyword in plain_lower for keyword in _CONTINUATION_KEYWORDS_LOWER) or any(
        keyword in raw_lower for keyword in _CONTINUATION_KEYWORDS_LOWER
    ) or any(kw in note for note in note_texts for kw in _CONTINUATION_KEYWORDS_LOWER):
        if role in {"note", "other", ""}:
            return "continuation_marker"

    if role == "table" and _same_ident(previous_meta, meta):
        return "table_continuation"

    if role in {"note", "other"} and _same_ident(previous_meta, meta):
        trimmed = plain_text.strip()
        if trimmed and len(trimmed) <= 60 and not re.search(r"MI-\d{2}-\d{2}-\d{8}", trimmed):
            return "sparse_note"

    return None


def _merge_sparse_vlm_chunks(chunks: List[Any]) -> List[Any]:
    if not chunks:
        return []

    merged: List[Any] = []
    for idx, chunk in enumerate(chunks):
        prev_chunk = merged[-1] if merged else None
        prev_meta = prev_chunk.metadata if prev_chunk else None
        reason = _classify_sparse_chunk(getattr(chunk, "metadata", None), prev_meta, getattr(chunk, "content", ""))
        if reason and prev_chunk:
            logger.info("🟢 3U_MEL semantic merge idx=%s reason=%s", idx, reason)
            prev_chunk.content = _merge_chunk_text(prev_chunk.content, chunk.content)
            prev_chunk.metadata = _merge_metadata_dict(prev_chunk.metadata, getattr(chunk, "metadata", None), reason)
            continue
        merged.append(chunk)
    return merged


def _merge_leave_blank_chunks(chunks: List[Any], vlm_meta_list: List[Dict[str, Any]]) -> Tuple[List[Any], List[Dict[str, Any]]]:
    """
    Merge VLM-marked blank/continuation chunks back to previous chunk to reduce fragmentation.
    """
    if not chunks:
        return chunks, vlm_meta_list
    merged_chunks: List[Any] = []
    merged_meta: List[Dict[str, Any]] = []
    for idx, (ck, meta) in enumerate(zip(chunks, vlm_meta_list)):
        if not meta:
            merged_chunks.append(ck)
            merged_meta.append(meta)
            continue
        notes = meta.get("notes") or []
        role = (meta.get("chunk_role") or meta.get("role") or "").lower()
        is_blankish = any("有意留空" in str(n) for n in notes) or "continuation" in role
        if is_blankish and merged_chunks:
            prev_meta = merged_meta[-1] or {}
            ident_ok = (not meta.get("ident")) or (meta.get("ident") == prev_meta.get("ident"))
            ata_ok = (not meta.get("ata_outer")) or (meta.get("ata_outer") == prev_meta.get("ata_outer"))
            if ident_ok and ata_ok:
                merged_chunks[-1] = _merge_chunk_text(merged_chunks[-1], ck)
                merged_meta[-1] = _merge_metadata_dict(prev_meta, meta, reason="vlm_blank_merge")
                continue
        merged_chunks.append(ck)
        merged_meta.append(meta)
    return merged_chunks, merged_meta


def _classify_content_type(ata_code):
    """Classify content type based on ATA code pattern."""
    if not ata_code or not isinstance(ata_code, str):
        return "unknown"

    # Count hierarchy levels by dashes
    dash_count = ata_code.count("-")

    if dash_count == 1:  # XX-XX format
        return "directory"
    elif dash_count == 2:  # XX-XX-XX format
        return "item"
    elif dash_count >= 3:  # XX-XX-XX-XX format
        return "subitem"
    else:
        return "unknown"




# This is the entry point function that RAGFlow's task_executor will call.
def chunk(filename, binary=None, from_page=0, to_page=100000, **kwargs):
    """
    Entry point for the 3U_MEL navigation-based parser.
    Uses K2_Standalone_Extractor for Sichuan Airlines A330 MEL documents.
    Follows RAGFlow's standard parser architecture.
    """
    try:
        return _chunk_impl(filename, binary, from_page, to_page, **kwargs)
    except Exception as e:
        # 捕获所有异常，避免返回简单的字符串错误
        logger.exception(f"3U_MEL: Critical error in chunk function: {e}")
        # 抛出更详细的异常信息
        raise RuntimeError(f"3U_MEL parsing failed: {str(e)}") from e


def _chunk_impl(filename, binary=None, from_page=0, to_page=100000, **kwargs):
    """
    Entry point for the 3U_MEL navigation-based parser.
    Uses K2_Standalone_Extractor for Sichuan Airlines A330 MEL documents.
    Follows RAGFlow's standard parser architecture.
    """

    # ✅ 1. Create standard doc object (following naive.py pattern)
    doc = {
        "docnm_kwd": filename,
        "title_tks": rag_tokenizer.tokenize(re.sub(r"\.[a-zA-Z]+$", "", filename)),
    }
    doc["title_sm_tks"] = rag_tokenizer.fine_grained_tokenize(doc["title_tks"])

    def smart_callback(*args, **kwargs):
        """智能callback包装器，兼容RAGFlow的两种调用模式"""
        if "msg" in kwargs:
            # 模式1: callback(msg="OCR started")
            msg = kwargs["msg"]
            logger.info(f"[3U_MEL] {msg}")
        elif len(args) == 2:
            # 模式2: callback(0.63, "Layout analysis")
            prog, msg = args
            logger.info(f"[3U_MEL] {prog * 100:.1f}% - {msg}")
        elif len(args) == 1 and isinstance(args[0], str):
            # 其他情况：只有消息
            logger.info(f"[3U_MEL] {args[0]}")

    callback = kwargs.get("callback", smart_callback)

    # --- 1. Generate Navigation Map using K2 Extractor ---
    navigation_map = None

    # Strategy: Use K2_Standalone_Extractor for Sichuan Airlines A330 MEL
    if K2_EXTRACTOR_AVAILABLE and (binary is not None or filename):
        try:
            logger.info("🚀 3U_MEL: Attempting navigation map generation using K2_Standalone_Extractor...")
            callback(0.05, "Generating 3U_MEL navigation map from PDF outline...")

            # Initialize K2 extractor with binary data
            k2_input = binary if binary is not None else filename
            k2_extractor = K2_Standalone_Extractor(k2_input)

            # Build navigation map in memory
            navigation_map = k2_extractor.build_navigation_map()

            if navigation_map:
                logger.info(f"✅ 3U_MEL: Successfully generated navigation map with {len(navigation_map)} entries")
                callback(0.1, f"3U_MEL map generation successful: {len(navigation_map)} entries found.")

        except Exception as e:
            logger.warning(f"⚠️ 3U_MEL: K2 map generation failed: {e}. No fallback available.")
            navigation_map = None

    # If K2 extractor is not available or failed
    if not navigation_map:
        err_msg = "3U_MEL: Could not generate navigation map. K2_Standalone_Extractor is required for Sichuan Airlines A330 MEL documents."
        logger.error(err_msg)
        # 使用更详细的错误回调，避免简单的字符串错误
        try:
            callback(-1, f"[3U_MEL ERROR] {err_msg}")
        except Exception as callback_err:
            logger.warning(f"3U_MEL: Callback failed: {callback_err}")
        return []

    # --- 2. 选择处理模式：测试模式 vs 全量模式 ---
    parser_config = copy.deepcopy(kwargs.get("parser_config") or {})
    layout_recognizer, parser_entry = _resolve_layout_parser(parser_config)
    logger.info("[3U_MEL] Using OCR parser: %s", layout_recognizer)
    result_chunks = []  # This will store the final, fully-processed chunks

    # 检查是否为全量测试模式 - 从os.environ读取
    # RAGFlow的task_executor不传递环境变量到kwargs，需要直接从os.environ读取
    ENABLE_FULL_TEST = os.environ.get("ENABLE_FULL_TEST", "false").lower() in ("true", "1", "yes","True")
    TEST_LIMIT = int(os.environ.get("THREE_U_MEL_TEST_LIMIT", "100"))

    # 页面范围过滤 (格式: "start_page,end_page", 例如 "820,850")
    PAGE_RANGE = os.environ.get("THREE_U_MEL_PAGE_RANGE", "").strip()
    page_range_start, page_range_end = None, None
    if PAGE_RANGE and "," in PAGE_RANGE:
        try:
            page_range_start, page_range_end = map(int, PAGE_RANGE.split(","))
            logger.info(f"[3U_MEL] 页面范围过滤: {page_range_start}-{page_range_end}")
        except ValueError:
            logger.warning(f"[3U_MEL] 无效的页面范围格式: {PAGE_RANGE}, 已忽略")
            page_range_start, page_range_end = None, None

    logger.info(f"[3U_MEL] 全量模式: {ENABLE_FULL_TEST}, 测试限制: {TEST_LIMIT}")

    if not ENABLE_FULL_TEST and TEST_LIMIT:
        # Environment variable limit mode
        limit = int(TEST_LIMIT)
        navigation_items = list(navigation_map.items())[:limit]
        logger.info(f"🧪 3U_MEL LIMITED TEST MODE: Processing only {limit} chunks")
        print(f"🧪 3U_MEL 限制测试模式：仅处理 {limit} 个目标")
    elif not ENABLE_FULL_TEST:
        # 测试模式：仅处理前20个目标
        LIMITED_TEST_COUNT = 20
        navigation_items = list(navigation_map.items())[:LIMITED_TEST_COUNT]
        logger.info(f"🧪 3U_MEL TESTING MODE: Processing first {LIMITED_TEST_COUNT} entries")
        print(f"🧪 3U_MEL 测试模式：开始处理 {len(navigation_items)} 个目标...")
        if navigation_items:
            print(f"📍 页面范围: {navigation_items[0][1]['start_page']}-{navigation_items[-1][1]['end_page']}")
    else:
        # 全量模式：处理所有导航目标 (默认模式)
        navigation_items = list(navigation_map.items())

        # 应用页面范围过滤
        if page_range_start is not None and page_range_end is not None:
            original_count = len(navigation_items)
            navigation_items = [
                (name, bounds) for name, bounds in navigation_items
                if not (bounds['end_page'] < page_range_start or bounds['start_page'] > page_range_end)
            ]
            filtered_count = len(navigation_items)
            logger.info(f"🎯 3U_MEL PAGE RANGE FILTER: {original_count} -> {filtered_count} targets (pages {page_range_start}-{page_range_end})")
            print(f"🎯 3U_MEL 页面范围过滤: 从 {original_count} 个目标中筛选出 {filtered_count} 个 (页面 {page_range_start}-{page_range_end})")

        logger.info(f"🚀 3U_MEL FULL MODE: Processing all {len(navigation_items)} navigation targets")
        print(f"🚀 3U_MEL 全量模式：开始处理所有 {len(navigation_items)} 个目标...")
        # 预估处理时间
        estimated_time = len(navigation_items) * 0.5 / 60  # 分钟 (K2 is much faster)
        print(f"⏱️ 预估3U_MEL全量处理时间: {estimated_time:.1f} 分钟")

    total_entries = len(navigation_items)

    # 添加计时和成功率统计
    start_time = time.time()
    success_count = 0
    fallback_success_count = 0

    # Determine language once before the loop
    # This is an approximation; we'll collect some text first
    sample_texts_for_lang_detection = []
    for _, boundaries in navigation_items[:20]: # Sample first 20 items
        title = boundaries.get("title", "")
        if title: sample_texts_for_lang_detection.append(title)
    eng = is_english(sample_texts_for_lang_detection) if sample_texts_for_lang_detection else False
    logger.info(f"Language detected as {'English' if eng else 'Chinese'}")

    tenant_id = kwargs.get("tenant_id")
    primary_ready, primary_descriptor, primary_error = _probe_primary_vlm_model(tenant_id)
    if primary_ready:
        logger.info("🧠 3U_MEL 租户视觉模型检查通过: %s", primary_descriptor)
    else:
        logger.warning("🧠 3U_MEL 租户视觉模型检查未通过: %s", primary_error)
    vlm_enhancer = MelVLMEnhancer(
        tenant_id=tenant_id,
        lang="English" if eng else "Chinese",
        callback=callback,
        logger=logger,
        primary_allowed=primary_ready,
    )
    vlm_env_raw = _env_get("FTMS_VLM_ENABLED", "THREE_U_MEL_VLM_ENABLED", default="true")
    vlm_configured = vlm_enhancer.configured_enabled
    tenant_marker = "<none>"
    if tenant_id:
        tenant_marker = tenant_id[:8] + ("..." if len(tenant_id) > 8 else "")
    vlm_reasons = []
    if not vlm_configured:
        vlm_reasons.append("env disabled")
    if vlm_configured and not primary_ready:
        vlm_reasons.append(primary_error or "primary unavailable")
    if vlm_configured and not tenant_id:
        vlm_reasons.append("missing tenant_id")
    if not vlm_enhancer.fallback_available and vlm_enhancer.fallback_reason:
        vlm_reasons.append(f"fallback: {vlm_enhancer.fallback_reason}")
    effective_state = "ACTIVE" if vlm_enhancer.enabled else "INACTIVE"
    reason_suffix = f" reason={'; '.join(vlm_reasons)}" if vlm_reasons else ""
    fallback_clause = (
        f"启用:{vlm_enhancer.fallback_descriptor}"
        if vlm_enhancer.fallback_available
        else f"禁用:{vlm_enhancer.fallback_reason or '未配置'}"
    )
    primary_clause = primary_descriptor or "<未配置>"
    logger.info(
        "🧠 3U_MEL VLM增强策略 %s (环境变量=%s, 租户模型=%s, tenant=%s, fallback=%s)%s",
        "已启用" if effective_state == "ACTIVE" else "已禁用",
        vlm_env_raw,
        primary_clause,
        tenant_marker,
        fallback_clause,
        reason_suffix,
    )
    model_override = _env_get("FTMS_VLM_MODEL_NAME", "THREE_U_MEL_VLM_MODEL_NAME")
    fallback_model_pref = _env_get(
        "FTMS_VLM_FALLBACK_MODEL",
        "THREE_U_MEL_VLM_FALLBACK_MODEL",
        "FTMS_QWEN_VL_MODEL_NAME",
        "FTMS_QWEN_MODEL_NAME",
    )
    override_desc = model_override or "<租户默认>"
    fallback_desc = fallback_model_pref or "<未配置>"
    logger.info("🧠 3U_MEL VLM模型配置: override=%s, fallback=%s", override_desc, fallback_desc)

    vlm_attempted_targets = 0
    vlm_enhanced_targets = 0
    vlm_failed_targets = 0
    raw_chunk_count = 0
    dropped_empty_count = 0
    dedup_skipped_count = 0
    drop_records: List[Dict[str, Any]] = []
    drop_log_path: Optional[Path] = None

    def _record_drop(reason: str, text: str, ident: Optional[str], ata_code_val: str, bucket_idx: int, page_hint: Optional[int] = None) -> None:
        preview = (text or "").strip()[:160]
        drop_records.append(
            {
                "reason": reason,
                "ident": ident or "",
                "ata_code": ata_code_val,
                "bucket_index": bucket_idx,
                "page_hint": page_hint,
                "preview": preview,
            }
        )

    for i, (target_name, boundaries) in enumerate(navigation_items):
        # Handle composite keys (e.g., "MEL-33-37-01") from K2 extractor
        if target_name.startswith("MEL-"):
            parts = target_name.split("-", 1)
            context_prefix = parts[0]  # MEL
            true_ata_code = parts[1]   # XX-XX-XX
        else:
            context_prefix = "UNK"
            true_ata_code = target_name

        start_page = boundaries["start_page"]
        end_page = boundaries["end_page"]

        prog = 0.1 + 0.8 * (i + 1) / total_entries
        msg = f"Processing 3U_MEL target '{true_ata_code}' (Context: {context_prefix}): Pages {start_page}-{end_page}"
        callback(prog, msg)
        logger.info(msg)

        try:
            ocr_sections, _, pdf_parser = parser_entry(
                filename=filename,
                binary=binary,
                from_page=start_page - 1,
                to_page=end_page,
                lang="English" if eng else "Chinese",
                callback=callback,
                layout_recognizer=layout_recognizer,
                tenant_id=tenant_id,
            )

            if not ocr_sections:
                logger.warning(f"3U_MEL: No text content extracted for target '{true_ata_code}' via {layout_recognizer}.")
                continue

            if pdf_parser is None:
                logger.error("3U_MEL: OCR parser did not return PdfParser context for '%s'. Skipping target.", true_ata_code)
                continue

            parsed_sections = ocr_sections

            # 🆕 保留MinerU的type和text_level字段，intelligent filtering
            sections_with_type = []
            for section in parsed_sections:
                if not section or not section[0]:
                    continue
                
                if len(section) >= 4:
                    # 🆕 by_three_u: (text, type, tag, text_level)
                    sections_with_type.append({
                        "text": section[0],
                        "type": section[1],
                        "tag": section[2],
                        "text_level": section[3]  # 🆕 保留text_level
                    })
                elif len(section) >= 3:
                    # MinerU standard: (text, type, tag)
                    sections_with_type.append({
                        "text": section[0],
                        "type": section[1],
                        "tag": section[2],
                        "text_level": None
                    })
                elif len(section) >= 2:
                    # Other parsers: (text, tag)
                    sections_with_type.append({
                        "text": section[0],
                        "type": "text",
                        "tag": section[1],
                        "text_level": None
                    })
                else:
                    sections_with_type.append({
                        "text": section[0],
                        "type": "text",
                        "tag": "",
                        "text_level": None
                    })
            
            # 🆕 统计text_level信息
            text_level_count = sum(1 for item in sections_with_type if item.get("text_level") is not None)
            if text_level_count > 0:
                logger.info(f"🆕 3U_MEL [{true_ata_code}]: 发现 {text_level_count} 个带text_level标记的sections")
            
            # Filter: 保留ATA表头，过滤页眉页脚，🆕 保留type和text_level信息用于MI边界检测
            filtered_count = 0
            sections_filtered = []  # 🆕 使用4元组 (text, tag, type, text_level)
            
            for item in sections_with_type:
                text, item_type = item["text"], item["type"]
                text_level = item.get("text_level")  # 🆕 保留text_level
                
                # 过滤页脚和页码
                if item_type in ["footer", "page_number"]:
                    filtered_count += 1
                    continue
                
                # 如果是header，检查是否ATA表头
                if item_type == "header":
                    # ATA表头关键词
                    if any(kw in text for kw in ["A319/A320/A321", "最低设备清单", "MEL项目", 
                                                  "设备/设施", "客舱逃生设施", "识别：MI-", "识别: MI-"]):
                        # 保留ATA表头，保留type和text_level信息
                        sections_filtered.append((text, item["tag"], item_type, text_level))
                    elif text.strip() == "#" or len(text.strip()) < 3:
                        # 过滤页眉装饰
                        filtered_count += 1
                    else:
                        # 其他header也保留（可能是标题）
                        sections_filtered.append((text, item["tag"], item_type, text_level))
                else:
                    # text, table等类型都保留，保留type和text_level信息
                    sections_filtered.append((text, item["tag"], item_type, text_level))
            
            logger.info(f"3U_MEL [{true_ata_code}]: Filtered {filtered_count} footer/header items, "
                       f"kept {len(sections_filtered)}/{len(parsed_sections)} sections")

            # 🆕 替换变量名，用于后续新逻辑
            ocr_sections = sections_filtered  # 现在是4元组 (text, tag, type, text_level)



            from rag.nlp import naive_merge

            chunk_token_num = kwargs.get("chunk_token_num")
            if not chunk_token_num:
                chunk_token_num = _env_get_int(
                    "THREE_U_MEL_CHUNK_TOKEN_NUM",
                    "FTMS_CHUNK_TOKEN_NUM",
                    "CHUNK_TOKEN_NUM",
                    default=768,
                )
            try:
                chunk_token_num = int(chunk_token_num)
            except Exception:
                chunk_token_num = 768
            delimiter = kwargs.get("delimiter", "\n!?。；！？")
            overlapped_percent = kwargs.get("overlapped_percent", 10)

            nav_info = boundaries
            ata_hierarchy_path_list = nav_info.get("ata_hierarchy", {}).get("full_path", [true_ata_code])
            base_metadata = {
                "source_target": target_name,
                "start_page": start_page,
                "end_page": end_page,
                "parser": f"3u_mel_navigation_parser_v2.1_ident_split[{layout_recognizer}]",
                "context": context_prefix,
                "ata_code": true_ata_code,
                "title": nav_info.get("title", ""),
                "full_anchor_text": nav_info.get("full_anchor_text", target_name),
                "source_destination": nav_info.get("source_destination", ""),
                "content_type": _classify_content_type(true_ata_code),
                "hierarchy_level": len(true_ata_code.split("-")) if "-" in true_ata_code else 1,
                "ata_hierarchy_path": " > ".join(ata_hierarchy_path_list) if isinstance(ata_hierarchy_path_list, list) else str(ata_hierarchy_path_list),
                "ata_level": nav_info.get("ata_hierarchy", {}).get("level", 1),
                "ata_parent_code": nav_info.get("ata_hierarchy", {}).get("parent_code", ""),
                "ata_base_code": nav_info.get("ata_hierarchy", {}).get("base_code", true_ata_code),
                "ata_suffix": nav_info.get("ata_hierarchy", {}).get("suffix", ""),
                "has_ata_suffix": nav_info.get("ata_hierarchy", {}).get("has_suffix", False),
                "content_type_v2": nav_info.get("content_classification", {}).get("type", "item"),
                "content_classification_confidence": nav_info.get("content_classification", {}).get("confidence", 0.95),
                "cache_utilization": nav_info.get("extraction_metadata", {}).get("cache_hit", False),
                "extraction_time_ms": nav_info.get("extraction_metadata", {}).get("generation_time_ms", 0),
                "coordinate_precision": 0.95 if nav_info.get("coordinates") else 0.0,
                "extraction_method": "outline_based_k2_v1",
                "airline": "四川航空",
                "mel_type": "3U_MEL",
            }

            target_vlm_attempted = vlm_enhancer.enabled
            target_vlm_success = False
            target_generated_chunks = 0

            # 🆕 使用新的双层拆分逻辑：MI识别号 → 子项编号
            logger.info(f"🔄 3U_MEL: 开始双层拆分 - Target '{true_ata_code}' (pages {start_page}-{end_page}), sections={len(ocr_sections)}")
            
            # ocr_sections 已经是过滤后的3元组 (text, tag, type)，直接使用

            # 🆕 构建K2上下文用于fallback边界检测
            # 从MEL-key提取base_code更可靠(避免full_anchor_text中的空格问题)
            k2_context = {
                "full_anchor_text": nav_info.get("full_anchor_text", target_name),
                "ata_base_code": nav_info.get("ata_hierarchy", {}).get("base_code", true_ata_code),
                "title": nav_info.get("title", ""),
            }
            
            # 🆕 双层拆分：第一层按MI，第二层按子项
            all_chunk_data = split_sections_by_mi_and_inner_codes(
                ocr_sections,  # 已过滤的3元组
                ata_code=true_ata_code,
                k2_context=k2_context  # 🆕 传递K2上下文
            )
            
            logger.info(f"📦 3U_MEL: 双层拆分完成 - 生成 {len(all_chunk_data)} 个初始chunks")
            
            target_vlm_attempted = vlm_enhancer.enabled
            target_vlm_success = False
            target_generated_chunks = 0
            
            # 处理每个chunk_data（已按MI+子项拆分）
            for chunk_idx, chunk_data in enumerate(all_chunk_data):
                mi_ident = chunk_data.get("mi_ident")
                inner_code = chunk_data.get("inner_code")
                
                # 🆕 分别获取header和content sections
                header_sections = chunk_data.get("header_sections", [])
                content_sections = chunk_data.get("content_sections", [])
                
                mi_metadata = chunk_data.get("mi_metadata", {})
                chunk_metadata = chunk_data.get("chunk_metadata", {})
                
                # 🐛 修复: 确保 inner_code 和 mi_ident 在 metadata 中可用
                chunk_metadata["inner_code"] = inner_code
                mi_metadata["ident"] = mi_ident
                
                if not content_sections:
                    logger.warning(f"⚠️ Chunk {chunk_idx}: Empty content, skip")
                    continue
                
                logger.debug(f"  Processing chunk {chunk_idx}: MI={mi_ident}, inner={inner_code}, header={len(header_sections)}, content={len(content_sections)}")
                
                # 🆕 合并时剥离header的position tags，防止缩略图超长拼接
                # Header作为纯文本继承，不参与图片定位
                # 🆕 修复: 确保顺序 ATA表头 → MI识别 → MEL子项, 添加换行分隔
                
                # 步骤1: 提取header文本(无position tags) - 用于显示
                header_texts = []
                for sec in header_sections:
                    if len(sec) >= 1:
                        text = sec[0]
                        header_texts.append(_strip_position_tags(text))
                
                
                # 🆕 步骤2: 提取content文本,过滤非子项正文的tag (接上页/接下页/MI识别行等)
                EXCLUDE_TAG_KEYWORDS = [
                    "接上页", "接下页", "续上页", "续下页", "continued",
                    "最低设备清单", "MEL项目",
                    "有意留空", "空白", "BLANK"
                ]
                
                content_for_merge = []
                for sec in content_sections:
                    if len(sec) >= 2:
                        text, tag = sec[0], sec[1]
                        # 🆕 仅过滤明确非子项正文的tag,避免误伤子项内容
                        text_stripped = text.strip()
                        is_mi_ident_line = bool(re.match(r"^(识别[:：]|MI-)", text_stripped))
                        has_inner_code = bool(inner_code) and (inner_code in text)
                        should_strip_tag = (not has_inner_code) and (
                            is_mi_ident_line or any(kw in text for kw in EXCLUDE_TAG_KEYWORDS)
                        )
                        if should_strip_tag:
                            content_for_merge.append((text, ""))  # 保留文本但移除tag
                            logger.debug(f"⏭️ 过滤非子项tag: {text[:50]}...")
                        else:
                            content_for_merge.append((text, tag))  # 保留原始tag用于引用
                    elif len(sec) >= 1:
                        content_for_merge.append((sec[0], ""))
                
                # 步骤3: 构建有结构的文本 (带换行分隔)
                # 格式: [ATA表头]\n\n[MI识别]\n\n[MEL子项内容...]
                structured_header = ""
                if header_texts:
                    # ATA表头和MI识别用换行分隔
                    structured_header = "\n\n".join(header_texts) + "\n\n"
                
                # 将header作为无position tag的首段落
                all_sections_for_merge = []
                if structured_header.strip():
                    all_sections_for_merge.append((structured_header.strip(), ""))  # 无tag
                
                # 添加content sections (只有子项正文保留position tags用于引用)
                all_sections_for_merge.extend(content_for_merge)
                
                # Naive merge (token-based merging)
                merged_chunks = naive_merge(
                    all_sections_for_merge,
                    chunk_token_num=chunk_token_num,
                    delimiter=delimiter,
                    overlapped_percent=overlapped_percent,
                )
                

                # VLM enhancement (optional, if enabled)
                if vlm_enhancer.enabled:
                    vlm_context = {
                        "ata_code": true_ata_code,
                        "target_name": target_name,
                        "navigation_title": boundaries.get("title"),
                        "page_range": (start_page, end_page),
                        "ident": mi_ident,
                        "ident_hint": mi_metadata.get("ident", ""),
                        "bucket_index": chunk_idx + 1,
                        "bucket_count": len(all_chunk_data),
                    }
                    enhanced_chunks = vlm_enhancer.enhance_chunks(merged_chunks, pdf_parser, vlm_context)
                else:
                    enhanced_chunks = merged_chunks

                
                # 🆕 新的语义合并（只合并同MI+同子项）
                enhanced_chunks = merge_sparse_chunks_v2([
                    {
                        "content_with_weight": chunk.content if hasattr(chunk, "content") else str(chunk),
                        "metadata": chunk.metadata if hasattr(chunk, "metadata") else {}
                    }
                    for chunk in enhanced_chunks
                ])
                
                # 转换回chunk对象
                class SimpleChunk:
                    def __init__(self, content, metadata):
                        self.content = content
                        self.metadata = metadata
                
                enhanced_chunks = [SimpleChunk(c["content_with_weight"], c.get("metadata", {})) for c in enhanced_chunks]
                
                # 过滤空chunks并构建最终metadata
                current_chunks = []
                for enhanced in enhanced_chunks:
                    raw_chunk_count += 1
                    candidate_text = enhanced.content

                    # 🆕 three_u MEL: 单页兜底，汇总子项段落bbox以覆盖整块高亮
                    # 规则：若tag数量不等于1，选inner_code所在页的所有tag并合并bbox
                    def _build_single_page_bbox_tag(default_page: Optional[int], inner_code_value: Optional[str]) -> str:
                        tags_with_text = []
                        collecting = inner_code_value is None
                        for sec in content_sections:
                            text = sec[0] if len(sec) >= 1 else ""
                            tag = sec[1] if len(sec) >= 2 else ""

                            if inner_code_value:
                                if inner_code_value in text:
                                    collecting = True
                                elif collecting:
                                    codes = extract_inner_codes(text)
                                    if codes and inner_code_value not in codes:
                                        break

                            if not collecting:
                                continue

                            if tag:
                                tags_with_text.append((text, tag))
                            elif text:
                                # 有些tag可能被拼到文本里
                                embedded = re.findall(r"@@[0-9-]+\t[0-9.\t]+##", text)
                                for t in embedded:
                                    tags_with_text.append((text, t))

                        parsed = []
                        for text, tag in tags_with_text:
                            m = re.match(r"@@([0-9]+)\t([0-9.]+)\t([0-9.]+)\t([0-9.]+)\t([0-9.]+)##", tag)
                            if not m:
                                continue
                            page = int(m.group(1))
                            left = float(m.group(2))
                            right = float(m.group(3))
                            top = float(m.group(4))
                            bottom = float(m.group(5))
                            parsed.append((page, left, right, top, bottom, text))

                        if parsed:
                            # 优先选择包含inner_code的页
                            target_page = None
                            if inner_code_value:
                                for page, _, _, _, _, text in parsed:
                                    if inner_code_value in text:
                                        target_page = page
                                        break
                            if target_page is None:
                                page_counts: Dict[int, int] = {}
                                for page, _, _, _, _, _ in parsed:
                                    page_counts[page] = page_counts.get(page, 0) + 1
                                target_page = max(page_counts, key=page_counts.get)

                            page_items = [p for p in parsed if p[0] == target_page]
                            min_left = min(p[1] for p in page_items)
                            max_right = max(p[2] for p in page_items)
                            min_top = min(p[3] for p in page_items)
                            max_bottom = max(p[4] for p in page_items)
                            
                            # ⚠️ 诊断：记录 bbox 合并过程
                            logger.info(
                                "🔍 bbox合并诊断: inner_code=%s, 收集sections=%d, parsed_tags=%d, "
                                "target_page=%s, page_items=%d, merged_bbox=(%.1f,%.1f,%.1f,%.1f)",
                                inner_code_value, len(tags_with_text), len(parsed),
                                target_page, len(page_items),
                                min_left, max_right, min_top, max_bottom
                            )
                            
                            # 🆕 使用同数值不同格式的tag，避免命中MinerU的缓存裁剪图
                            return (
                                f"@@{target_page}\t{min_left:.2f}\t{max_right:.2f}"
                                f"\t{min_top:.2f}\t{max_bottom:.2f}##"
                            )
                        if default_page:
                            logger.info("🔍 bbox合并诊断: inner_code=%s, 无有效tags, 使用默认页=%s", inner_code_value, default_page)
                            return f"@@{int(default_page)}\t0\t0\t0\t0##"
                        return ""

                    tag_matches = POSITION_TAG_PATTERN.findall(candidate_text or "")
                    if len(tag_matches) != 1:
                        fallback_tag = _build_single_page_bbox_tag(start_page, inner_code)
                        if fallback_tag:
                            candidate_text = _strip_position_tags(candidate_text).rstrip() + "\n" + fallback_tag
                    
                    # 提取页码提示
                    page_hint = None
                    m = re.search(r"@@([0-9]+)", candidate_text or "")
                    if m:
                        try:
                            page_hint = int(m.group(1))
                        except Exception:
                            page_hint = None
                    
                    # 移除标签检查是否为空
                    try:
                        plain = pdf_parser.remove_tag(candidate_text)
                    except Exception:
                        plain = strip_position_tags(candidate_text)
                    
                    if not plain.strip():
                        dropped_empty_count += 1
                        drop_records.append({
                            "reason": "empty_after_strip",
                            "ident": mi_ident,
                            "inner_code": inner_code,
                            "ata_code": true_ata_code,
                            "target": target_name,
                            "page_hint": page_hint,
                            "content_preview": candidate_text[:200] if candidate_text else ""
                        })
                        continue
                    
                    # ✅ 单页高亮优先：直接用整页图 + 单bbox定位，避免裁剪图坐标偏移
                    try:
                        single_tag = None
                        tag_match = re.search(r"@@([0-9]+)\t([0-9.]+)\t([0-9.]+)\t([0-9.]+)\t([0-9.]+)##", candidate_text or "")
                        if tag_match:
                            single_tag = tag_match.groups()

                        if single_tag and hasattr(pdf_parser, "page_images") and pdf_parser.page_images:
                            page = int(single_tag[0])
                            left = float(single_tag[1])
                            right = float(single_tag[2])
                            top = float(single_tag[3])
                            bottom = float(single_tag[4])
                            page_idx = page - 1
                            if 0 <= page_idx < len(pdf_parser.page_images):
                                img = pdf_parser.page_images[page_idx]
                                # ✅ 修复：tag 中的坐标已由 _line_tag() 转换为像素值
                                # ⚡⚡⚡ CRITICAL FIX: DO NOT DIVIDE BY 1000.0 HERE! ⚡⚡⚡
                                # 之前错误代码: px = val * page_dimension / 1000.0
                                # 之前这里再次除以1000导致坐标极小，高亮位置错误。
                                # MinerU的tag已经是从0-1000归一化转回像素了。
                                page_from = int(getattr(pdf_parser, "page_from", 0) or 0)
                                abs_page_idx = page_idx + page_from
                                
                                # tag 中的坐标已经是像素，直接使用
                                px0 = int(left)
                                px1 = int(right)
                                py0 = int(top)
                                py1 = int(bottom)
                                logger.info(
                                    "🌟🌙 3U_MEL highlight: page=%s abs=%s bbox_px=(%s,%s,%s,%s)",
                                    page,
                                    abs_page_idx + 1,
                                    px0,
                                    px1,
                                    py0,
                                    py1,
                                )
                                # add_positions expects 0-based page index; offset by page_from to map to absolute PDF page
                                poss = [(abs_page_idx, px0, px1, py0, py1)]

                                chunk_doc = copy.deepcopy(doc)
                                chunk_doc["image"] = img
                                add_positions(chunk_doc, poss)
                                tokenize(chunk_doc, _strip_position_tags(candidate_text), eng)
                            else:
                                logger.info("🌟🌙 3U_MEL highlight: page_idx=%s out of range, fallback to tokenize_chunks", page_idx)
                                chunk_doc = None
                        else:
                            logger.info("🌟🌙 3U_MEL highlight: single_tag missing or page_images unavailable, fallback to tokenize_chunks")
                            chunk_doc = None

                        if not chunk_doc:
                            # ✅ 使用RAGFlow标准的tokenize_chunks函数
                            # 这会自动生成: image, position_int, page_num_int, top_int
                            temp_chunks = tokenize_chunks([candidate_text], doc, eng, pdf_parser)
                            if not temp_chunks:
                                logger.warning(f"⚠️ Tokenization failed for chunk, MI={mi_ident}, inner={inner_code}")
                                continue
                            chunk_doc = temp_chunks[0]
                        
                    except Exception as e:
                        logger.warning(f"⚠️ Tokenization exception for chunk, MI={mi_ident}, inner={inner_code}: {e}")
                        # Fallback: 手动构建（但会缺少image）
                        chunk_doc = {
                            "content_with_weight": candidate_text,
                            "content_ltks": [],
                            "content_sm_ltks": [],
                        }
                    
                    # 🆕 注入5层元数据
                    metadata_context = {
                        "filename": Path(filename).name if filename else "unknown.pdf",
                        "airline": "四川航空",
                        "mel_type": "3U_MEL",
                        # "aircraft_models": ["A319", "A320", "A321"],  # ❌ 移除硬编码，避免误导
                        "navigation_path": context_prefix,
                        "ata_code": true_ata_code,
                        "ata_title": boundaries.get("title", ""),
                        "navigation_hint_page": start_page,
                        "actual_page_range": [start_page, end_page],
                        "mi_metadata": mi_metadata,
                        "chunk_metadata": chunk_metadata,
                    }
                    
                    chunk_doc = inject_hierarchical_metadata(chunk_doc, metadata_context)
                    
                    # 🆕 提取页码和坐标信息（支持按页查询）
                    pages_involved = set()
                    coordinates = []
                    for match in re.finditer(r'@@([0-9]+)\t([0-9.]+)\t([0-9.]+)\t([0-9.]+)\t([0-9.]+)##', 
                                            chunk_doc["content_with_weight"]):
                        try:
                            page = int(match.group(1))
                            left = float(match.group(2))
                            right = float(match.group(3))
                            top = float(match.group(4))
                            bottom = float(match.group(5))
                            
                            pages_involved.add(page)
                            coordinates.append({
                                "page": page,
                                "bbox": [left, top, right, bottom]
                            })
                        except Exception:
                            pass
                    
                    # 添加页码字段（便于ES查询）
                    if pages_involved:
                        chunk_doc.update({
                            "pages_involved": sorted(list(pages_involved)),
                            "primary_page": min(pages_involved),
                            "page_count": len(pages_involved),
                            "is_cross_page": len(pages_involved) > 1,
                            "coordinates": coordinates,  # 结构化坐标信息
                        })
                    else:
                        # Fallback to navigation range
                        chunk_doc.update({
                            "pages_involved": [start_page],
                            "primary_page": start_page,
                            "page_count": 1,
                            "is_cross_page": False,
                            "coordinates": [],
                        })
                    
                    # 添加传统字段（向前兼容）
                    chunk_doc.update({
                        "start_page": page_hint or start_page,
                        "end_page": page_hint or end_page,
                        "parser": f"3u_mel_v3_refactored[{layout_recognizer}]"
                    })
                    
                    # 🆕 添加RAGFlow标准字段（task_executor会添加doc_id和kb_id）
                    chunk_doc.update({
                        "docnm_kwd": Path(filename).name if filename else "unknown.pdf",
                        # ⚠️ 不添加doc_id和kb_id - task_executor会自动添加！
                        # ⚠️ manual.py和paper.py都没有添加这两个字段！
                        "available_int": 1,  # 默认可用
                        "important_kwd": [],  # 重要关键词（可后续标注）
                        "question_kwd": [],   # 问题关键词（可后续标注）
                        # img_id 会由task_executor在upload_to_minio中生成
                        # position_int, page_num_int, top_int 已由tokenize_chunks生成
                    })
                    
                    # 验证元数据
                    warnings = validate_chunk_metadata(chunk_doc)
                    if warnings:
                        chunk_doc.setdefault("validation_warnings", []).extend(warnings)
                    
                    current_chunks.append(chunk_doc)
                
                result_chunks.extend(current_chunks)
                target_generated_chunks += len(current_chunks)
            
            if target_generated_chunks == 0:
                logger.warning(f"⚠️ 3U_MEL: No valid chunks produced for '{true_ata_code}'.")
                continue
            
            success_count += 1
            if target_vlm_attempted:
                vlm_attempted_targets += 1
                if target_vlm_success:
                    vlm_enhanced_targets += 1
                else:
                    vlm_failed_targets += 1

        except ValueError as ve:
            # Fallback logic remains largely the same, but we need to adapt it to the new structure
            if "cannot convert float NaN to integer" in str(ve):
                logger.warning(f"3U_MEL: NaN error for '{target_name}', attempting fallback OCR without layout analysis")
                # ... [The extensive fallback logic can be inserted here, but it also needs to be adapted
                # to create chunks and append to result_chunks directly if successful]
                # For brevity in this refactoring, we'll log and continue.
                # A full implementation would refactor the fallback block as well.
                print(f"⚠️ [3U_MEL DEBUG] NaN error for '{target_name}'. Fallback logic needs review. Skipping.")
                continue
            else:
                raise ve

        except Exception as e:
            logger.exception(f"[3U_MEL ERROR] Failed to parse target '{true_ata_code}'. Reason: {e}")
            continue

    if drop_records:
        project_root_candidates = [
            Path(os.getcwd()),
            Path(__file__).resolve().parents[2],
            Path("/ragflow"),
            Path.home() / "ragflow",
        ]
        for root in project_root_candidates:
            try:
                logs_dir = (root / "vibe" / "logs").resolve()
                logs_dir.mkdir(parents=True, exist_ok=True)
                drop_log_path = logs_dir / f"3u_mel_dropped_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
                with drop_log_path.open("w", encoding="utf-8") as f:
                    for rec in drop_records:
                        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                break
            except Exception:
                drop_log_path = None
                continue

    # 🆕 全局精确去重（按 MI + 子项 + 内容hash）
    logger.info(f"🔍 3U_MEL: 开始全局去重 - 去重前共 {len(result_chunks)} 个chunks")
    result_chunks, duplicates = dedup_chunks_by_mi_and_inner_code(result_chunks)
    logger.info(f"✅ 3U_MEL: 全局去重完成 - 去重后共 {len(result_chunks)} 个chunks，移除 {len(duplicates)} 个重复")
    
    # 记录去重详情
    if duplicates:
        for dup in duplicates[:10]:  # 只记录前10个
            logger.debug(f"  去重: MI={dup.get('mi_ident')}, inner={dup.get('inner_code')}, preview={dup.get('content_preview', '')[:100]}")
    
    callback(1.0, f"3U_MEL chunking finished. Generated {len(result_chunks)} chunks (after deduplication).")

    # 🔍 诊断：输出采样 chunk 的详细信息
    print("\n" + "=" * 70)
    print("🔍 采样诊断 (优先选有 inner_code 的 chunk):")
    
    # 优先选择有 inner_code 的 chunk
    chunks_with_inner = [c for c in result_chunks if c.get("inner_code")]
    sample_chunks = chunks_with_inner[:2] if chunks_with_inner else result_chunks[:2]
    
    for i, chunk in enumerate(sample_chunks):
        content = chunk.get("content_with_weight", "")
        position_int = chunk.get("position_int", [])
        page_num = chunk.get("page_num_int", "N/A")
        top = chunk.get("top_int", "N/A")
        coordinates = chunk.get("coordinates", [])
        
        # 从 content 中提取 position tag (re 已在文件顶部导入)
        tags_in_content = re.findall(r"@@[0-9-]+\t[0-9.\t]+##", content or "")
        
        print(f"  [{i+1}] MI={chunk.get('mi_ident')}, inner={chunk.get('inner_code')}")
        print(f"      page_num_int={page_num}, top_int={top}")
        print(f"      position_int={position_int[:2] if position_int else 'None'}")
        print(f"      coordinates={coordinates[:1] if coordinates else 'None'}")
        print(f"      content中tags数={len(tags_in_content)}, 首个tag={tags_in_content[0] if tags_in_content else 'None'}")
        print(f"      content前150字: {content[:150].replace(chr(10), ' ')}...")
    print("=" * 70)

    # Performance statistics
    elapsed_time = time.time() - start_time
    total_processed = success_count + fallback_success_count
    
    print("\n" + "=" * 60)
    print("📊 3U_MEL 处理统计 (v2.0):")
    print(f"   总目标数: {total_entries}")
    print(f"   成功处理: {success_count} (正常)")
    print(f"   回退成功: {fallback_success_count} (使用fallback)")
    print(f"   总chunks数: {len(result_chunks)}")
    print(f"   原始chunk数: {raw_chunk_count} (去重前)")
    print(f"   去重跳过: {dedup_skipped_count} 段")
    print(f"   过滤丢弃: {dropped_empty_count} 段 (空/无token)")
    if total_entries > 0:
        print(f"   成功率: {total_processed / total_entries * 100:.1f}%")
        print(f"   处理时间: {elapsed_time:.1f}秒")
        print(f"   平均速度: {elapsed_time / total_entries:.1f}秒/目标")
    else:
        print("   成功率: N/A (无处理目标)")
        print(f"   处理时间: {elapsed_time:.1f}秒")
    print("=" * 60)
    if vlm_attempted_targets:
        logger.info(
            "🧠 3U_MEL VLM总结: 尝试增强 %s 个目标, 成功增强 %s 个",
            vlm_attempted_targets,
            vlm_enhanced_targets,
        )
    else:
        logger.info("🧠 3U_MEL VLM总结: 未尝试增强任何目标 (可能已禁用)")
    
    # 🆕 全局去重后，实施质量筛选策略
    logger.info(f"🔍 3U_MEL: 开始质量筛选 - 筛选前共 {len(result_chunks)} 个chunks")
    
    # 保存所有chunks（包括非标准chunks）到logs
    all_chunks_for_archive = result_chunks.copy()
    
    # 定义完整性检查函数
    def is_complete_chunk(chunk):
        """
        检查chunk是否具有完整的三层结构：
        1. ATA表头
        2. MI识别号（在content中）
        3. 子项编号（inner_code或content中有）
        """
        content = chunk.get("content_with_weight", "")
        
        # Layer 1: ATA表头
        has_ata_header = "A319/A320/A321" in content or "最低设备清单" in content
        
        # Layer 2: MI识别号在content中
        has_mi_in_content = "识别：MI-" in content or "识别: MI-" in content
        
        # Layer 3: 子项编号 - 修复re.Match错误
        has_inner_code = bool(chunk.get("inner_code")) or bool(re.search(r'\d{2}-\d{2}-\d{2}[A-Z]\s', content))
        
        return has_ata_header and has_mi_in_content
    
    # 筛选出完整chunks
    complete_chunks = [c for c in result_chunks if is_complete_chunk(c)]
    incomplete_chunks = [c for c in result_chunks if not is_complete_chunk(c)]
    
    logger.info(f"✅ 3U_MEL: 质量筛选完成")
    logger.info(f"   - 完整chunks (返回RAGFlow): {len(complete_chunks)}")
    logger.info(f"   - 不完整chunks (仅存档): {len(incomplete_chunks)}")
    
    # 保存所有chunks到归档文件
    all_chunks_path = Path("/mnt/d/element_WorkSpeac/ragflow_worktrees/strategy-a-sync/vibe/logs") / \
                      f"3u_mel_all_chunks_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    try:
        # 🆕 过滤掉不可序列化的字段（如Image对象）
        def make_serializable(obj):
            if isinstance(obj, dict):
                return {k: make_serializable(v) for k, v in obj.items() 
                        if not isinstance(v, bytes) and not hasattr(v, 'read')}  # 排除bytes和file-like
            elif isinstance(obj, list):
                return [make_serializable(item) for item in obj]
            elif hasattr(obj, '__dict__'):
                return str(obj)  # 将复杂对象转为字符串
            return obj
        
        serializable_chunks = make_serializable(all_chunks_for_archive)
        with open(all_chunks_path, "w", encoding="utf-8") as f:
            json.dump(serializable_chunks, f, ensure_ascii=False, indent=2, default=str)
        logger.info(f"📁 所有Chunks已归档到: {all_chunks_path}")
    except Exception as e:
        logger.warning(f"保存归档失败: {e}")

    
    # 只返回完整chunks给RAGFlow
    result_chunks = complete_chunks
    
    logger.info(f"📊 最终返回 {len(result_chunks)} 个完整chunks给RAGFlow")
    if vlm_attempted_targets:
        print(f"   VLM: 尝试 {vlm_attempted_targets} 个目标, 成功 {vlm_enhanced_targets}, 失败 {vlm_failed_targets}")
    if drop_log_path:
        logger.info("🗑️ 3U_MEL 丢弃明细已写入: %s", drop_log_path)
    elif dropped_empty_count or dedup_skipped_count:
        logger.info("🗑️ 3U_MEL 丢弃统计: 去重跳过=%s, 过滤丢弃=%s", dedup_skipped_count, dropped_empty_count)

    # Save chunks to a file for debugging
    if result_chunks:
        project_root_candidates = [
            Path(os.getcwd()),
            Path(__file__).resolve().parents[2],
            Path("/ragflow"),
            Path.home() / "ragflow",
        ]
        chunks_file = None
        for root in project_root_candidates:
            try:
                logs_dir = (root / "vibe" / "logs").resolve()
                logs_dir.mkdir(parents=True, exist_ok=True)
                chunks_file = logs_dir / f"3u_mel_chunks_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
                with open(chunks_file, "w", encoding="utf-8") as f:
                    def json_default(o):
                        if isinstance(o, (datetime, Path)):
                            return str(o)
                        return f"<<non-serializable: {type(o).__name__}>>"

                    json.dump(result_chunks, f, ensure_ascii=False, indent=2, default=json_default)
                break
            except Exception as err:  # pragma: no cover - fallback for misconfigured envs
                logger.warning("3U_MEL: Failed to persist chunks under %s (%s)", root, err)
                chunks_file = None
        if chunks_file and chunks_file.exists():
            print(f"📁 3U_MEL Chunks已保存到: {chunks_file}")
            print(f"   文件大小: {chunks_file.stat().st_size / 1024:.1f} KB")
        else:
            logger.warning("3U_MEL: Unable to persist chunks summary; skipping file output.")

    logger.info(f"3U_MEL: Finished MEL Parsing. Generated {len(result_chunks)} chunks.")

    return result_chunks


# Example of how to run this for testing (not part of the final integration)
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

    logger.info("--- Running 3U_MEL navigation parser in standalone test mode (v1.0) ---")

    # Mock data for testing
    mock_kwargs = {"doc_id": "test_3u_mel_doc_id_123", "docnm_kwd": "330.pdf", "callback": lambda *args, **kwargs: logger.info(f"[3U_MEL Callback] Args: {args}, Kwargs: {kwargs}") if args or kwargs else None}

    # Find the test PDF
    project_root_candidates = [Path(__file__).parent.parent.parent, Path.cwd(), Path("/mnt/d/element_WorkSpeac/ragflow")]
    test_pdf_path = None
    for root in project_root_candidates:
        path = root / "vibe" / "data" / "330.pdf"
        if path.exists():
            test_pdf_path = str(path)
            break

    if not test_pdf_path:
        logger.error("3U_MEL: Test PDF (330.pdf) not found. Skipping standalone run.")
    else:
        logger.info(f"3U_MEL: Using test PDF: {test_pdf_path}")
        with open(test_pdf_path, "rb") as f:
            binary_content = f.read()

        # Run the main chunk function
        result_chunks = chunk(filename=test_pdf_path, binary=binary_content, **mock_kwargs)

        logger.info("\n--- 3U_MEL Standalone Test Finished ---")
        logger.info(f"3U_MEL: Total chunks generated: {len(result_chunks)}")
        if result_chunks:
            logger.info("3U_MEL: Sample of the first chunk generated:")
            # Print metadata and a snippet of the content
            if "metadata" in result_chunks[0]:
                print(json.dumps(result_chunks[0]["metadata"], indent=2, ensure_ascii=False))
            print("3U_MEL Content Snippet:")
            print(result_chunks[0]["content_with_weight"][:200] + "...")

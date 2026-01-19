"""
three_u_semantic_merge.py - 改进的语义合并逻辑

关键改进：
1. 只合并同MI + 同子项的续页内容
2. 检测子项边界，防止过度合并
3. 支持人工标记的强制分割点
"""

import re
import logging
from typing import Any, Dict, List, Optional

from rag.app.three_u_splitting import extract_inner_codes

logger = logging.getLogger(__name__)

# 标记关键词
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


def strip_position_tags(text: str) -> str:
    """移除位置标签"""
    if not text:
        return ""
    return re.sub(r'@@[0-9\-\t.]+?##', '', text)


def classify_sparse_chunk_v2(
    current_chunk: Dict[str, Any],
    previous_chunk: Optional[Dict[str, Any]]
) -> Optional[str]:
    """
    改进的稀疏块分类逻辑
    
    关键改进：
    - 只有同MI + 同子项才合并
    - 检测子项边界，防止A和B被误合并
    
    Returns:
        合并原因（None表示不合并）
    """
    if not previous_chunk:
        return None
    
    curr_content = current_chunk.get("content_with_weight", "")
    curr_meta = current_chunk.get("metadata", {}) or {}
    prev_meta = previous_chunk.get("metadata", {}) or {}
    
    plain_text = strip_position_tags(curr_content)
    plain_lower = plain_text.lower()
    
    # 检测"有意留空"标记
    has_blank_marker = any(kw in plain_lower for kw in _BLANK_KEYWORDS_LOWER)
    if has_blank_marker:
        # 同MI + 同ATA才合并
        if _same_mi_and_ata(curr_meta, prev_meta):
            return "intentional_blank"
    
    # 检测"接下页"/"接上页"标记
    has_continuation = any(kw in plain_lower for kw in _CONTINUATION_KEYWORDS_LOWER)
    if has_continuation:
        # 🔥 关键：只有同MI + 同子项才合并
        if _same_mi_and_inner_code(curr_meta, prev_meta):
            return "continuation_within_same_item"
        else:
            # 不同子项，即使有"接下页"也不合并
            logger.debug(
                f"跳过跨子项合并: prev_inner={prev_meta.get('inner_code')} -> curr_inner={curr_meta.get('inner_code')}"
            )
            return None
    
    # 检测表格延续（同MI + 同子项）
    curr_role = curr_meta.get("chunk_role", "").lower()
    if curr_role == "table" and _same_mi_and_inner_code(curr_meta, prev_meta):
        return "table_continuation"
    
    # 检测稀疏注释（同MI + 同子项 + 内容短）
    if curr_role in {"note", "other"} and _same_mi_and_inner_code(curr_meta, prev_meta):
        trimmed = plain_text.strip()
        if trimmed and len(trimmed) <= 60:
            return "sparse_note"
    
    return None


def _same_mi_and_inner_code(meta1: Dict[str, Any], meta2: Dict[str, Any]) -> bool:
    """检查是否是同一MI识别号 + 同一子项编号"""
    # 检查MI识别号
    mi1 = meta1.get("ident")
    mi2 = meta2.get("ident")
    if not mi1 or not mi2 or mi1 != mi2:
        return False
    
    # 检查子项编号
    inner1 = meta1.get("inner_code")
    inner2 = meta2.get("inner_code")
    
    # 如果都没有子项编号，也认为是同一子项（向前兼容）
    if not inner1 and not inner2:
        return True
    
    # 必须完全匹配
    return inner1 == inner2


def _same_mi_and_ata(meta1: Dict[str, Any], meta2: Dict[str, Any]) -> bool:
    """检查是否是同一MI识别号 + 同一ATA代码"""
    mi1 = meta1.get("ident")
    mi2 = meta2.get("ident")
    if not mi1 or not mi2 or mi1 != mi2:
        return False
    
    ata1 = meta1.get("ata_code")
    ata2 = meta2.get("ata_code")
    if not ata1 or not ata2 or ata1 != ata2:
        return False
    
    return True


def merge_sparse_chunks_v2(chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    改进的语义合并逻辑
    
    Args:
        chunks: 带metadata的chunk列表
    
    Returns:
        合并后的chunks
    """
    if not chunks:
        return []
    
    merged: List[Dict[str, Any]] = []
    
    for idx, chunk in enumerate(chunks):
        prev_chunk = merged[-1] if merged else None
        
        # 分类当前chunk
        merge_reason = classify_sparse_chunk_v2(chunk, prev_chunk)
        
        if merge_reason and prev_chunk:
            # 合并到前一个chunk
            logger.info(f"🟢 3U_MEL语义合并: idx={idx} reason={merge_reason}")
            
            # 合并内容
            prev_chunk["content_with_weight"] = _merge_chunk_text(
                prev_chunk["content_with_weight"],
                chunk["content_with_weight"]
            )
            
            # 合并元数据
            prev_chunk["metadata"] = _merge_metadata_for_continuation(
                prev_chunk["metadata"],
                chunk["metadata"],
                merge_reason
            )
            
            continue
        
        # 不合并，作为独立chunk
        merged.append(chunk)
    
    return merged


def _merge_chunk_text(base_text: str, tail_text: str) -> str:
    """
    合并两个chunk的文本内容
    
    保留位置标签在末尾
    """
    # 分离正文和标签
    base_body, base_tags = _split_text_and_tags(base_text)
    tail_body, tail_tags = _split_text_and_tags(tail_text)
    
    # 合并正文
    merged_body = base_body.rstrip()
    tail_body_clean = tail_body.strip()
    
    if merged_body and tail_body_clean:
        merged_body = f"{merged_body}\n\n{tail_body_clean}"
    elif tail_body_clean:
        merged_body = tail_body_clean
    
    # 确保正文以换行结尾
    merged_body = merged_body.rstrip()
    if merged_body and not merged_body.endswith("\n"):
        merged_body += "\n"
    
    # 拼接所有标签
    return merged_body + base_tags + tail_tags


def _split_text_and_tags(text: str) -> tuple[str, str]:
    """分离正文和末尾的位置标签"""
    if not text:
        return "", ""
    
    # 匹配末尾的标签串
    tag_pattern = re.compile(r'((?:@@[0-9\-\t.]+?##)+)$')
    match = tag_pattern.search(text)
    
    if not match:
        return text, ""
    
    body = text[:match.start()]
    tags = match.group(1)
    return body, tags


def _merge_metadata_for_continuation(
    base_meta: Dict[str, Any],
    tail_meta: Dict[str, Any],
    reason: str
) -> Dict[str, Any]:
    """
    合并跨页延续chunks的元数据
    """
    import copy
    merged = copy.deepcopy(base_meta)
    
    # 合并警告列表
    base_warnings = merged.get("validation_warnings", [])
    tail_warnings = tail_meta.get("validation_warnings", [])
    for warning in tail_warnings:
        if warning not in base_warnings:
            base_warnings.append(warning)
    if base_warnings:
        merged["validation_warnings"] = base_warnings
    
    # 记录合并历史
    merged.setdefault("merge_history", []).append({
        "reason": reason,
        "merged_from_inner_code": tail_meta.get("inner_code"),
        "merged_from_ident": tail_meta.get("ident"),
    })
    
    # 更新跨页标记
    merged["cross_page"] = True
    if tail_meta.get("continuation_to_next"):
        merged["continuation_to_next"] = True
    
    return merged

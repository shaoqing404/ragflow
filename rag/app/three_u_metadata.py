"""
three_u_metadata.py - 元数据注入与去重模块

职责：
1. 5层元数据注入（文件→章节→ATA→MI→子项）
2. 精确去重（MI + 子项 + 内容hash）
3. 元数据合并与传播
"""

import re
import hashlib
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ========== 5层元数据结构 ==========


def inject_hierarchical_metadata(
    chunk_doc: Dict[str, Any],
    context: Dict[str, Any]
) -> Dict[str, Any]:
    """
    注入5层元数据到chunk
    
    Args:
        chunk_doc: 待注入的chunk文档
        context: 上下文信息，包含所有层级的元数据
    
    Returns:
        注入完整元数据的chunk
    """
    # Layer 1: 文件级元数据
    chunk_doc.update({
        "filename": context.get("filename", ""),
        "airline": context.get("airline", "四川航空"),
        "mel_type": context.get("mel_type", "3U_MEL"),
        # "aircraft_models": context.get("aircraft_models", ["A319", "A320", "A321"]), # ❌ 移除
    })
    
    # Layer 2: 章节级元数据（从导航路径提取）
    nav_path = context.get("navigation_path", "")
    chapter_info = parse_navigation_path(nav_path)
    chunk_doc.update({
        "chapter_code": chapter_info.get("chapter_code"),
        "chapter_title": chapter_info.get("chapter_title"),
        "section_code": chapter_info.get("section_code"),
        "section_title": chapter_info.get("section_title"),
        "navigation_path": nav_path,
    })
    
    # Layer 3: ATA条目级元数据
    chunk_doc.update({
        "ata_code": context.get("ata_code"),
        "ata_title": context.get("ata_title"),
        "navigation_hint_page": context.get("navigation_hint_page"),
        "actual_page_range": context.get("actual_page_range"),
    })
    
    # Layer 4: MI识别号级元数据
    mi_metadata = context.get("mi_metadata", {})
    chunk_doc.update({
        "ident": mi_metadata.get("ident"),
        "ident_date": mi_metadata.get("ident_date"),
        "standard": mi_metadata.get("standard"),
        "applicability": mi_metadata.get("applicability"),
    })
    
    # 如果MI识别失败，添加验证警告
    if mi_metadata.get("validation_warning"):
        chunk_doc.setdefault("validation_warnings", []).append(mi_metadata["validation_warning"])
    
    # Layer 5: 子项级元数据
    chunk_metadata = context.get("chunk_metadata", {})
    chunk_doc.update({
        "inner_code": chunk_metadata.get("inner_code"),
        "role": chunk_metadata.get("role", "inner_ata"),
        "repair_interval": chunk_metadata.get("repair_interval"),
        "installed_qty": chunk_metadata.get("installed_qty"),
        "required_qty": chunk_metadata.get("required_qty"),
        "placard": chunk_metadata.get("placard"),
        "cross_page": chunk_metadata.get("cross_page", False),
        "continuation_from_previous": chunk_metadata.get("continuation_from_previous", False),
        "continuation_to_next": chunk_metadata.get("continuation_to_next", False),
    })
    
    # 如果子项识别失败，添加验证警告
    if chunk_metadata.get("validation_warning"):
        chunk_doc.setdefault("validation_warnings", []).append(chunk_metadata["validation_warning"])
    
    # 构建可搜索字段（用于ES检索优化）
    chunk_doc["searchable_ata_codes"] = build_ata_hierarchy(context.get("ata_code", ""))
    chunk_doc["searchable_mi_ident"] = mi_metadata.get("ident", "")
    chunk_doc["searchable_inner_code"] = chunk_metadata.get("inner_code", "")
    
    return chunk_doc


def parse_navigation_path(nav_path: str) -> Dict[str, Optional[str]]:
    """
    从导航路径提取章节信息
    
    Example:
        Input: "MEL项目 25 - 设备/设施 > 25-62 - 客舱逃生设施"
        Output: {
            "chapter_code": "25",
            "chapter_title": "设备/设施",
            "section_code": "25-62",
            "section_title": "客舱逃生设施"
        }
    """
    result: Dict[str, Optional[str]] = {
        "chapter_code": None,
        "chapter_title": None,
        "section_code": None,
        "section_title": None,
    }
    
    if not nav_path:
        return result
    
    # 按 " > " 分割路径
    parts = [p.strip() for p in nav_path.split(">")]
    
    for part in parts:
        # 匹配格式："XX - 标题" 或 "XX-XX - 标题"
        match = re.match(r'^(\d{2}(?:-\d{2})?)\s*-\s*(.+)$', part)
        if match:
            code, title = match.groups()
            dash_count = code.count("-")
            
            if dash_count == 0:
                # 单个数字，视为章节
                result["chapter_code"] = code
                result["chapter_title"] = title.strip()
            elif dash_count == 1:
                # XX-XX 格式，视为section
                result["section_code"] = code
                result["section_title"] = title.strip()
    
    return result


def build_ata_hierarchy(ata_code: str) -> List[str]:
    """
    构建ATA层级列表，用于多层级检索
    
    Example:
        Input: "25-62-03"
        Output: ["25", "25-62", "25-62-03"]
    """
    if not ata_code:
        return []
    
    parts = ata_code.split("-")
    hierarchy = []
    
    for i in range(1, len(parts) + 1):
        hierarchy.append("-".join(parts[:i]))
    
    return hierarchy


# ========== 精确去重逻辑 ==========


def dedup_chunks_by_mi_and_inner_code(chunks: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    按 MI识别号 + 子项编号 + 内容hash 精确去重
    
    Args:
        chunks: 待去重的chunk列表
    
    Returns:
        (去重后的chunks, 重复chunks记录)
    """
    seen = set()
    deduped = []
    duplicates = []
    
    for chunk in chunks:
        # 构建去重key
        dedup_key = build_dedup_key(chunk)
        
        if dedup_key in seen:
            # 记录重复chunk
            duplicates.append({
                "mi_ident": chunk.get("ident", ""),
                "inner_code": chunk.get("inner_code", ""),
                "ata_code": chunk.get("ata_code", ""),
                "content_preview": chunk.get("content_with_weight", "")[:200],
                "reason": "exact_duplicate",
                "dedup_key": dedup_key
            })
            continue
        
        seen.add(dedup_key)
        deduped.append(chunk)
    
    if duplicates:
        logger.info(f"去重: 移除 {len(duplicates)} 个完全重复的chunks")
        # 记录详细信息到日志
        for dup in duplicates[:5]:  # 只记录前5个
            logger.debug(f"  重复chunk: MI={dup['mi_ident']}, inner={dup['inner_code']}, preview={dup['content_preview'][:100]}...")
    
    return deduped, duplicates


def build_dedup_key(chunk: Dict[str, Any]) -> str:
    """
    🆕 构建去重key: 基于结构化业务数据,不受OCR标点差异影响
    
    优先使用业务字段: MI + inner_code + 修理间隔 + 页面
    Fallback: 使用净化后的内容hash
    
    返回格式: "MI-25-62-00008557.0001001::25-62-03A::A::598"
    """
    mi_ident = chunk.get("ident", "") or ""
    inner_code = chunk.get("inner_code", "") or ""
    
    # 🆕 优先使用结构化业务字段
    repair_interval = chunk.get("repair_interval", "") or ""
    primary_page = str(chunk.get("primary_page", "") or "")
    installed_qty = str(chunk.get("installed_qty", "") or "")
    required_qty = str(chunk.get("required_qty", "") or "")
    
    # 如果有足够的业务字段,使用它们作为key
    if mi_ident and (inner_code or repair_interval):
        key_parts = [mi_ident, inner_code, repair_interval, installed_qty, required_qty, primary_page]
        return "::".join(key_parts)
    
    # Fallback: 计算净化后的内容hash
    content = chunk.get("content_with_weight", "")
    content_hash = compute_content_hash(content)
    
    return f"{mi_ident}::{inner_code}::{content_hash}"


def compute_content_hash(content: str) -> str:
    """
    🆕 计算内容hash（去除标点差异影响）
    
    策略: 只保留字母、数字、汉字,移除所有标点和空白
    这样半角逗号和全角逗号的差异不会影响hash
    
    Returns:
        16位hex字符串
    """
    # 移除位置标签
    clean_content = re.sub(r'@@[0-9\-\t.]+?##', '', content)
    
    # 🆕 只保留字母、数字、汉字 (移除所有标点和空白)
    clean_content = re.sub(r'[^\w\u4e00-\u9fff]', '', clean_content)
    
    # 计算SHA256并取前16位
    hash_obj = hashlib.sha256(clean_content.encode('utf-8'))
    return hash_obj.hexdigest()[:16]


def is_substantive_content(chunk: Dict[str, Any]) -> bool:
    """
    判断是否是实质性内容（不应被误去重的内容）
    
    白名单规则：
    - 包含子项编号（25-62-03A）
    - 包含表格数据
    - 包含MI识别号详情
    - 内容长度 > 100字符
    """
    content = chunk.get("content_with_weight", "")
    
    # 包含子项编号
    if chunk.get("inner_code"):
        return True
    
    # 包含表格关键字
    if any(kw in content for kw in ["修理间隔", "安装数量", "需要数量", "标牌"]):
        return True
    
    # 包含MI识别详情
    if "识别:" in content or "MI-" in content:
        return True
    
    # 内容足够长
    clean_content = re.sub(r'@@[0-9\-\t.]+?##', '', content)
    if len(clean_content.strip()) > 100:
        return True
    
    return False


# ========== 元数据合并逻辑 ==========


def merge_metadata_for_continuation(
    base_meta: Dict[str, Any],
    tail_meta: Dict[str, Any],
    reason: str
) -> Dict[str, Any]:
    """
    合并跨页延续chunks的元数据
    
    Args:
        base_meta: 基础chunk的元数据
        tail_meta: 待合并chunk的元数据
        reason: 合并原因
    
    Returns:
        合并后的元数据
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
    
    # 合并页面范围
    base_range = merged.get("actual_page_range", [])
    tail_range = tail_meta.get("actual_page_range", [])
    if base_range and tail_range:
        merged["actual_page_range"] = [
            min(base_range[0], tail_range[0]),
            max(base_range[1], tail_range[1])
        ]
    
    # 记录合并信息
    merged.setdefault("merge_history", []).append({
        "reason": reason,
        "merged_inner_code": tail_meta.get("inner_code"),
        "merged_ident": tail_meta.get("ident"),
    })
    
    # 标记为跨页chunk
    merged["cross_page"] = True
    
    return merged


# ========== 工具函数 ==========


def validate_chunk_metadata(chunk: Dict[str, Any]) -> List[str]:
    """
    验证chunk元数据完整性
    
    Returns:
        警告列表（空列表表示验证通过）
    """
    warnings = []
    
    # 必需字段
    required_fields = ["ident", "ata_code", "inner_code"]
    for field in required_fields:
        if not chunk.get(field):
            warnings.append(f"缺少必需字段: {field}")
    
    # MI识别号格式验证
    ident = chunk.get("ident")
    if ident and not re.match(r'MI-\d{2}-\d{2}-\d{8}\.\d+', ident):
        warnings.append(f"MI识别号格式异常: {ident}")
    
    # ATA代码格式验证
    ata_code = chunk.get("ata_code")
    if ata_code and not re.match(r'\d{2}-\d{2}(-\d{2})?', ata_code):
        warnings.append(f"ATA代码格式异常: {ata_code}")
    
    # 子项编号格式验证
    inner_code = chunk.get("inner_code")
    if inner_code and not re.match(r'\d{2}-\d{2}-\d{2}[A-Z]', inner_code):
        warnings.append(f"子项编号格式异常: {inner_code}")
    
    return warnings

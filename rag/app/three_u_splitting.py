"""
three_u_splitting.py - 双层拆分逻辑模块

职责：
1. 按MI识别号拆分（第一层）
2. 按子项编号（A/B/C）拆分（第二层）
3. MI元数据提取

2025年12月22日23:54:23 初次提交
"""

import re
import logging
from typing import Any, Dict, List, Optional, Tuple, Sequence

logger = logging.getLogger(__name__)

# 🆕 识别号模式：MI/MO-XX-XX(-XX)?-YYYYMMDD.NNNNNNN (支持3或4段ATA)
IDENT_PATTERN = re.compile(r'\b(?:MI|MO)-\d{2}-\d{2}(?:-\d{2})?-\d{8}\.\d+\b')

# 🆕 修复: 子项编号模式 - 支持3或4段ATA (如25-62-01A 或 21-01-01-01A)
# 原pattern只支持3段, 新pattern支持3或4段
INNER_CODE_PATTERN = re.compile(
    r'(?:^|[^\d-])(\d{2}-\d{2}-\d{2}(?:-\d{2})?[A-Z])(?=[^\d-]|$)',
    re.MULTILINE
)

# 直接匹配pattern (用于text_level=1的情况) - 支持3或4段
DIRECT_INNER_CODE_PATTERN = re.compile(r'^(\d{2}-\d{2}-\d{2}(?:-\d{2})?[A-Z])\b')


def extract_mi_ident(text: str) -> Tuple[Optional[str], Optional[str]]:
    """
    提取MI识别号及其完整行作为hint
    
    Returns:
        (ident, hint) - 识别号和包含该识别号的完整行
    """
    if not text:
        return None, None
    
    match = IDENT_PATTERN.search(text)
    if not match:
        return None, None
    
    ident = match.group(0)
    
    # 提取包含ident的完整行作为hint
    hint = None
    for line in text.splitlines():
        if ident in line:
            hint = line.strip()
            break
    
    if not hint:
        # 如果找不到完整行，提取ident周围的文本
        start = max(0, match.start() - 40)
        end = min(len(text), match.end() + 40)
        hint = text[start:end].strip()
    
    return ident, hint or ident


def extract_inner_codes(text: str) -> List[str]:
    """
    提取子项编号列表（25-62-03A、25-62-03B等）
    
    Returns:
        去重后的子项编号列表
    """
    if not text:
        return []
    
    codes = INNER_CODE_PATTERN.findall(text)
    
    # 去重并保持顺序
    seen = set()
    unique_codes = []
    for code in codes:
        if code not in seen:
            seen.add(code)
            unique_codes.append(code)
    
    # 🆕 Debug logging
    if unique_codes:
        logger.debug(f"✅ Extracted inner codes: {unique_codes} from text: {text[:100]}")
    
    return unique_codes


def extract_inner_codes_with_text_level(text: str, text_level: Optional[int] = None) -> List[str]:
    """
    🆕 结合text_level和regex提取子项编号
    
    MinerU的text_level字段:
    - text_level: 1 = 一级标题 (对应MEL子项如"25-62-01A 滑梯不工作")
    - 不存在 = 普通正文
    
    Args:
        text: 待检测的文本
        text_level: MinerU标记的标题级别 (1=一级标题)
        
    Returns:
        去重后的子项编号列表
    """
    if not text:
        return []
    
    codes = []
    
    # 🆕 方法1: 利用text_level标记 (优先,更可靠)
    if text_level == 1:
        # text_level==1 的text通常以子项编号开头
        # 例如: "25-62-01A 滑梯不工作"
        text_stripped = text.strip()
        match = DIRECT_INNER_CODE_PATTERN.match(text_stripped)
        if match:
            codes.append(match.group(1))
            logger.info(f"✅ [text_level=1] 检测到子项: {match.group(1)} <- '{text_stripped[:50]}'")
            return codes  # text_level=1只会有一个子项编号
    
    # 🆕 方法2: Fallback - 使用修复后的regex
    if not codes:
        codes = extract_inner_codes(text)
        if codes and text_level is not None:
            logger.debug(f"📝 [regex fallback] 检测到子项: {codes} (text_level={text_level})")
    
    return codes


def extract_mi_metadata(header_text: str) -> Dict[str, Any]:
    """
    从MI头部文本提取元数据
    
    预期格式：
        25-62-03 SLIDE ARMED 灯
        识别: MI-25-62-00008557.0001001 / 30 MAY 25
        标准: A319, 321-200XX
        适用于: MSN 02313-02539, 03114-04419...
    
    Returns:
        {
            "ident": "MI-25-62-00008557.0001001",
            "ident_date": "30 MAY 25",
            "standard": "A319, 321-200XX",
            "applicability": "MSN 02313-02539, 03114-04419...",
            "raw_header": header_text
        }
    """
    metadata = {"raw_header": header_text}
    
    # 预处理：将 HTML 换行符转换为实际换行符，以便 regex 更好匹配
    # 处理 <td>AAA</td><td>BBB</td> 的情况
    text_processed = re.sub(r'<\/?(br|p|tr|div)[^>]*>', '\n', header_text, flags=re.IGNORECASE)
    text_processed = re.sub(r'<\/td>\s*<td[^>]*>', ' ', text_processed, flags=re.IGNORECASE) # td之间加空格
    text_processed = re.sub(r'<[^>]+>', '', text_processed) # 去除剩余标签
    
    # 提取MI识别号
    ident, _ = extract_mi_ident(text_processed)
    if ident:
        metadata["ident"] = ident
        
        # 尝试提取日期（在识别号后面）
        ident_line_match = re.search(rf'{re.escape(ident)}[\s/]+(\d{{1,2}}\s+[A-Z]{{3}}\s+\d{{2}})', text_processed)
        if ident_line_match:
            metadata["ident_date"] = ident_line_match.group(1)
    
    # 提取标准
    # 匹配直到行尾，或遇到下一个关键词 "适用于"
    standard_match = re.search(r'标准\s*[:：]\s*(.*?)(?=\s+适用于|\n|$)', text_processed, re.IGNORECASE)
    if standard_match:
        metadata["standard"] = standard_match.group(1).strip()
    
    # 提取适用于
    # 匹配直到行尾，或遇到下一个关键词 (可能是表格开始或其他未预期的内容)
    applicability_match = re.search(r'适用于\s*[:：]\s*(.*?)(?=\s+识别|\s+标准|\n|$)', text_processed, re.IGNORECASE)
    if applicability_match:
        val = applicability_match.group(1).strip()
        # 清理可能残留的标点
        val = val.rstrip(".,;，。；")
        metadata["applicability"] = val
    
    return metadata


def _is_k2_boundary_match(text: str, k2_context: Optional[Dict[str, Any]]) -> bool:
    """
    🆕 Fallback边界检测: 当table+text模式失败时使用K2锚点检测边界
    
    检测条件 (需同时满足):
    1. 文本包含k2_context.ata_base_code (如21-01-01-02)
    2. 文本不包含修理间隔等数据表标志 (排除数据行)
    
    适用场景:
    - OCR将ATA边界表格识别为text (如"21-01-01-02" "PACK按钮电门FAULT灯"两个孤立text)
    - OCR将ATA表头与边界表格融合成一个table
    
    Args:
        text: OCR识别的文本内容
        k2_context: K2上下文 {"ata_base_code": "21-01-01-02", ...}
    
    Returns:
        True 如果匹配到K2边界
    """
    if not k2_context or not text:
        return False
    
    base_code = k2_context.get("ata_base_code", "")
    if not base_code:
        return False
    
    # 排除数据表行
    if any(kw in text for kw in ["修理间隔", "安装数量", "需要数量", "标牌"]):
        return False
    
    # 排除全局ATA表头(最低设备清单)
    if "最低设备清单" in text:
        return False
    
    # 检查是否包含base_code
    # 支持完整匹配或被table分隔的情况
    if base_code in text:
        return True
    
    # 🆕 支持被OCR拆分的情况: 只匹配开头的ATA编码
    # 如text="21-01-01-02" 或 text以base_code开头
    text_stripped = text.strip()
    if text_stripped.startswith(base_code):
        return True
    
    # 🆕 处理融合table: 从table_body中提取ATA编码
    # 如 <table><tr><td>21-01-01-02</td><td>PACK按钮电门</td></tr></table>
    ata_in_table = re.search(r'<td>(' + re.escape(base_code) + r')</td>', text)
    if ata_in_table:
        return True
    
    return False


def split_by_mi_ident(
    sections: Sequence[Tuple],
    k2_context: Optional[Dict[str, Any]] = None  # 🆕 K2锚点上下文
) -> List[Dict[str, Any]]:
    """
    第一层拆分：按MI/MO识别号分组
    
    🆕 使用MinerU的table+text模式检测MI边界：
       - type=table 且包含ATA编码（XX-XX-XX或XX-XX-XX-XX格式）
       - 紧接着 type=text 且以"识别：MI-"或"识别：MO-"开头
       → 这两条记录标志一个新MI块的开始
    
    🆕 当table+text模式失败时,使用k2_context进行fallback边界检测:
       - 从k2_context.ata_base_code提取目标ATA编码
       - 在任意type中查找包含该编码+MI/MO识别号的段落
    
    🆕 全局ATA表头(包含"最低设备清单")会被收集并添加到所有bucket的header_sections开头
    
    Args:
        sections: [(text, tag, type, text_level), ...] OCR提取的段落列表
        k2_context: K2上下文 {"full_anchor_text", "ata_base_code", "title"}
    
    Returns:
        [
            {
                "ident": "MI-25-62-00008557.0001001",
                "ident_hint": "识别: MI-25-62-00008557.0001001 / 30 MAY 25",
                "header_sections": [(text, tag, type, text_level), ...],  # 全局ATA + MI边界
                "content_sections": [(text, tag, type, text_level), ...],
                "metadata": {...}
            },
            ...
        ]
    """
    mi_buckets: List[Dict[str, Any]] = []
    
    # 🆕 ATA表格模式：支持3或4段XX-XX-XX(-XX)?格式
    ATA_TABLE_PATTERN = re.compile(r'\d{2}-\d{2}-\d{2}(?:-\d{2})?')
    
    # 🆕 全局ATA表头收集 (包含"最低设备清单"的表格)
    global_ata_header: Optional[Tuple] = None
    ata_table_count = 0  # 🔍 诊断：统计 ATA 表格出现次数
    for section in sections:
        text = section[0] if section else ""
        sec_type = section[2] if len(section) >= 3 else "text"
        if sec_type == "table" and "最低设备清单" in text:
            ata_table_count += 1
            if global_ata_header is None:
                global_ata_header = section
                logger.info(f"🌐 发现全局ATA表头: {text[:100]}...")
            else:
                # 🔍 诊断：检测重复
                logger.warning(f"🔍 诊断: 发现重复ATA表头 #{ata_table_count}! 前100字符: {text[:100]}...")
    
    if ata_table_count > 1:
        logger.warning(f"🔍 诊断: MinerU返回了 {ata_table_count} 个ATA表头表格，可能是OCR重复识别!")
    
    current_ident: Optional[str] = None
    current_hint: Optional[str] = None
    current_header_sections: List[Tuple[str, str, str]] = []
    current_content_sections: List[Tuple[str, str, str]] = []
    pending_ata_table: Optional[Tuple[str, str, str]] = None  # 缓存等待确认的ATA表
    
    def flush_bucket() -> None:
        nonlocal current_ident, current_hint, current_header_sections, current_content_sections
        
        if not current_content_sections and not current_header_sections:
            return
        
        # 🆕 在header_sections开头添加全局ATA表头
        final_header_sections = []
        if global_ata_header:
            final_header_sections.append(global_ata_header)
        final_header_sections.extend(current_header_sections)
        
        # 🆕 提取元数据: 合并header和content前5行来提取standard/applicability
        # 因为"标准"和"适用于"通常在content的开头几行
        header_text = "\n".join([sec[0] for sec in final_header_sections])
        content_first_5 = "\n".join([sec[0] for sec in current_content_sections[:5]])
        combined_for_metadata = header_text + "\n" + content_first_5
        
        mi_metadata = extract_mi_metadata(combined_for_metadata)
        
        # 确保ident字段存在
        if not mi_metadata.get("ident") and current_ident:
            mi_metadata["ident"] = current_ident
        
        if not current_ident:
            logger.warning(f"创建未识别MI的bucket，包含 {len(current_content_sections)} 个段落")
            mi_metadata["validation_warning"] = "MI识别失败，需要人工审核"
        
        mi_buckets.append({
            "ident": current_ident,
            "ident_hint": current_hint or (current_ident or "未识别"),
            "header_sections": list(final_header_sections),  # 🆕 包含全局ATA
            "content_sections": list(current_content_sections),
            "metadata": mi_metadata
        })
        
        logger.info(f"=" * 60)
        logger.info(f"📦 MI Block: {current_ident or '未识别'}")
        logger.info(f"   Header sections: {len(final_header_sections)} (含全局ATA: {global_ata_header is not None})")
        logger.info(f"   Content sections: {len(current_content_sections)}")
        logger.info(f"=" * 60)
        
        current_header_sections = []
        current_content_sections = []
    
    for idx, section in enumerate(sections):
        # 🆕 处理2/3/4元组兼容性
        if len(section) >= 4:
            text, tag, sec_type, text_level = section[0], section[1], section[2], section[3]
        elif len(section) == 3:
            text, tag, sec_type = section
            text_level = None
        elif len(section) == 2:
            text, tag = section
            sec_type = "text"
            text_level = None
        else:
            text = section[0] if section else ""
            tag, sec_type, text_level = "", "text", None
        
        # 🆕 检测 table(ATA) + text(MI识别) 边界模式
        is_ata_table = (sec_type == "table" and ATA_TABLE_PATTERN.search(text) and 
                        "修理间隔" not in text and "安装数量" not in text)  # 排除数据表
        
        # 检查是否是MI识别号
        ident, hint = extract_mi_ident(text)
        
        if is_ata_table:
            # 缓存ATA表格，等待下一条MI识别确认 (🆕 保留text_level)
            pending_ata_table = (text, tag, sec_type, text_level)
            continue
        
        if ident and pending_ata_table:
            # 🎯 发现 table(ATA) + text(MI) 边界！
            if current_ident is not None:
                # 保存前一个bucket
                flush_bucket()
            
            # 开始新的MI bucket (🆕 保留text_level)
            current_ident = ident
            current_hint = hint
            current_header_sections = [pending_ata_table, (text, tag, sec_type, text_level)]
            pending_ata_table = None
            continue
        
        if ident and not pending_ata_table:
            # 只有MI识别号，没有前置ATA表格（可能是页面开头）
            if ident != current_ident and current_ident is not None:
                flush_bucket()
            
            if ident != current_ident:
                current_ident = ident
                current_hint = hint
                current_header_sections = [(text, tag, sec_type, text_level)]  # 🆕 保留text_level
            continue
        
        # 🆕 Fallback边界检测: 当table+text模式失败时,使用K2锚点检测
        # 场景: OCR将边界表格识别为text,或将ATA表头与边界表格融合
        if k2_context and _is_k2_boundary_match(text, k2_context):
            # 检查下一条是否是MI识别号
            next_has_ident = False
            if idx + 1 < len(sections):
                next_text = sections[idx + 1][0] if sections[idx + 1] else ""
                next_ident, _ = extract_mi_ident(next_text)
                next_has_ident = next_ident is not None
            
            if next_has_ident:
                # 🎯 Fallback边界发现! 当前段落是边界table(被识别为text)
                logger.info(f"🔄 Fallback边界检测: 发现K2锚点匹配 '{text[:50]}...'")
                pending_ata_table = (text, tag, sec_type, text_level)
                continue
            else:
                # 如果当前文本同时包含MI识别号,可能是融合的table
                if ident:
                    logger.info(f"🔄 Fallback: 融合table检测 - 包含ATA+MI '{text[:80]}...'")
                    if current_ident is not None:
                        flush_bucket()
                    current_ident = ident
                    current_hint = hint
                    current_header_sections = [(text, tag, sec_type, text_level)]
                    continue
        
        # 如果有pending的ATA表格但没有被MI确认，加入content
        if pending_ata_table:
            # 🆕 跳过全局ATA表头(避免重复)
            if global_ata_header and pending_ata_table[0] == global_ata_header[0]:
                logger.debug(f"⏭️ 跳过重复的全局ATA表头")
            else:
                current_content_sections.append(pending_ata_table)
            pending_ata_table = None
        
        # 🆕 跳过全局ATA表头在content中出现(避免重复)
        # ⚠️ 修改：移除 sec_type == "table" 限制，因为 MinerU 偶尔会将表头识别为 text
        if global_ata_header and "最低设备清单" in text and sec_type in {"table","text"}:
            logger.debug(f"⏭️ 跳过content中的全局ATA表头 (type={sec_type})")
            continue
        
        # 普通内容段落 (🆕 保留text_level)
        current_content_sections.append((text, tag, sec_type, text_level))
    
    # 处理最后的pending ATA表格
    if pending_ata_table:
        current_content_sections.append(pending_ata_table)
    
    # 保存最后一个bucket
    flush_bucket()
    
    # 如果没有找到任何MI识别号，创建一个默认bucket
    if not mi_buckets and sections:
        logger.warning("整个文档未找到MI识别号，创建默认bucket")
        mi_buckets.append({
            "ident": None,
            "ident_hint": "未识别",
            "header_sections": [],
            "content_sections": list(sections),
            "metadata": {"validation_warning": "整个文档MI识别失败，需要人工审核"}
        })
    
    return mi_buckets





def split_by_inner_codes(
    sections: Sequence[Tuple],  # 🆕 支持2/3/4元组
    mi_ident: Optional[str],
    mi_metadata: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """
    第二层拆分：按子项编号（A/B/C）分组
    
    Args:
        sections: 已按MI分组的段落列表
        mi_ident: 当前MI识别号
        mi_metadata: MI层元数据
    
    Returns:
        [
            {
                "mi_ident": "MI-25-62-00008557.0001001",
                "inner_code": "25-62-03A",
                "sections": [(text, tag), ...],
                "mi_metadata": {...},
                "chunk_metadata": {
                    "repair_interval": "C",
                    "cross_page": False,
                    ...
                }
            },
            ...
        ]
    
     
    Split sections by inner_code and inherit header sections to each chunk.
    
    Note: Headers without inner codes will be prepended to all sub-items.
    """
    logger.info(f"split_by_inner_code: Processing MI={mi_ident}, sections={len(sections)}")
    if sections:
        logger.debug(f"First 3 sections: {[s[0][:80] for s in sections[:3]]}")
    
    inner_code_chunks: List[Dict[str, Any]] = []
    
    # 🆕 从mi_metadata获取header sections（由split_by_mi_ident传递）
    # 包含ATA表格和MI识别号
    inherited_header_sections: List[Tuple] = mi_metadata.get("_header_sections", [])
    
    # 转换为2元组供后续处理,同时提取text_level信息
    header_sections: List[Tuple[str, str]] = []
    section_text_levels: Dict[int, Optional[int]] = {}  # 🆕 记录每个section的text_level
    
    for sec in inherited_header_sections:
        if len(sec) >= 2:
            header_sections.append((sec[0], sec[1] if len(sec) > 1 else ""))

    
    current_inner_code: Optional[str] = None
    current_sections: List[Tuple[str, str]] = []
    
    def flush_chunk() -> None:
        nonlocal current_inner_code, current_sections
        
        if not current_sections:
            return
        
        # 提取chunk级元数据
        chunk_metadata = _extract_chunk_metadata(current_sections, current_inner_code)
        
        inner_code_chunks.append({
            "mi_ident": mi_ident,
            "inner_code": current_inner_code,
            "header_sections": list(header_sections),  # 🆕 保存header引用
            "content_sections": list(current_sections),  # 🆕 重命名为content_sections
            "mi_metadata": mi_metadata,
            "chunk_metadata": chunk_metadata
        })
        
        current_sections = []
    
    # 🆕 处理sections - 支持4元组(text, tag, type, text_level)
    for section in sections:
        # 🆕 解包section,支持2/3/4元组
        if len(section) >= 4:
            text, tag, sec_type, text_level = section[0], section[1], section[2], section[3]
        elif len(section) >= 3:
            text, tag, sec_type = section[0], section[1], section[2]
            text_level = None
        else:
            text, tag = section[0], section[1] if len(section) > 1 else ""
            sec_type, text_level = "text", None
        
        # 🆕 使用双重检测: text_level优先 + regex fallback
        inner_codes = extract_inner_codes_with_text_level(text, text_level)
        
        if inner_codes:
            # 找到子项编号
            detected_code = inner_codes[0]
            
            if detected_code != current_inner_code:
                # 新的子项，保存前一个chunk
                if current_sections:
                    flush_chunk()
                
                current_inner_code = detected_code
            
            # 这是子项内容，添加到current_sections
            current_sections.append((text, tag))
        else:
            # 🆕 没有子项编号 - 这是header部分
            if current_inner_code is None:
                # 还没开始任何子项，这是全局header
                header_sections.append((text, tag))
            else:
                # 已经有子项了，但这段没有编号 - 可能是子项的延续内容
                current_sections.append((text, tag))
    
    # 保存最后一个chunk
    flush_chunk()
    
    # 如果没有找到任何子项编号，创建一个默认chunk（包含所有sections）
    if not inner_code_chunks and sections:
        logger.warning(f"MI {mi_ident}: No inner codes detected! Creating default chunk. "
                      f"First section: {sections[0][0][:200] if sections else 'N/A'}")
        inner_code_chunks.append({
            "mi_ident": mi_ident,
            "inner_code": None,
            "header_sections": [],
            "content_sections": list(sections),
            "mi_metadata": mi_metadata,
            "chunk_metadata": {"validation_warning": "未识别子项编号"}
        })
    
    return inner_code_chunks


def _extract_chunk_metadata(sections: Sequence[Tuple], inner_code: Optional[str]) -> Dict[str, Any]:  # 🆕 支持4元组
    """
    Extract metadata from chunk content like repair interval, cross-page markers, etc.
    
    Returns:
        {
            "repair_interval": "C",
            "installed_qty": 5,
            "required_qty": 0,
            "placard": "有",
            "cross_page": False,
            "continuation_from_previous": False,
            "continuation_to_next": False
        }
    """
    metadata = {}
    
    # 合并所有section文本 (🆕 兼容4元组)
    combined_text = "\n".join(sec[0] for sec in sections)  # sec[0] = text
    
    # 提取修理间隔
    repair_match = re.search(r'修理间隔\s*[:：]?\s*([A-D])', combined_text)
    if repair_match:
        metadata["repair_interval"] = repair_match.group(1)
    
    # 提取表格数据（安装数量、需要数量、标牌）
    table_match = re.search(
        r'安装数量\s*[:：]?\s*(\d+).*?需要数量\s*[:：]?\s*(\d+).*?标牌\s*[:：]?\s*([是否有无])',
        combined_text,
        re.DOTALL
    )
    if table_match:
        metadata["installed_qty"] = int(table_match.group(1))
        metadata["required_qty"] = int(table_match.group(2))
        metadata["placard"] = table_match.group(3)
    
    # 检测跨页标记
    has_next_page = any(kw in combined_text for kw in ["接下页", "continued on next page"])
    has_prev_page = any(kw in combined_text for kw in ["接上页", "continued from previous page"])
    
    metadata["cross_page"] = has_next_page or has_prev_page
    metadata["continuation_from_previous"] = has_prev_page
    metadata["continuation_to_next"] = has_next_page
    
    return metadata


def split_sections_by_mi_and_inner_codes(
    sections: Sequence[Tuple],  # 🆕 支持3或4元组
    ata_code: str,
    k2_context: Optional[Dict[str, Any]] = None  # 🆕 K2锚点上下文用于fallback边界检测
) -> List[Dict[str, Any]]:
    """
    双层拆分主入口
    
    Args:
        sections: List of (text, tag, type) tuples from OCR (含MinerU type信息)
        ata_code: Current ATA code from navigation
        k2_context: 🆕 K2上下文 {"full_anchor_text", "ata_base_code", "title"}
                    用于table+text模式失败时的fallback边界检测
    
    Returns:
        List of chunked data, each chunk contains complete MI and sub-item info
    """

    # 第一层：按MI识别号拆分
    mi_buckets = split_by_mi_ident(sections, k2_context=k2_context)
    
    logger.info(f"ATA {ata_code}: 拆分出 {len(mi_buckets)} 个MI buckets")
    
    # 第二层：每个MI bucket按子项编号拆分
    all_chunks = []
    for bucket_idx, mi_bucket in enumerate(mi_buckets):
        mi_ident = mi_bucket["ident"]
        mi_metadata = mi_bucket["metadata"]
        header_sections = mi_bucket["header_sections"]
        content_sections = mi_bucket["content_sections"]
        
        # 🆕 将header_sections信息添加到metadata，用于后续继承
        mi_metadata["_header_sections"] = header_sections
        
        if not content_sections and not header_sections:
            logger.warning(f"MI bucket {bucket_idx} (ident={mi_ident}) 没有内容段落")
            continue
        
        # 🆕 直接传递content_sections给split_by_inner_codes(已支持4元组)
        inner_code_chunks = split_by_inner_codes(content_sections, mi_ident, mi_metadata)
        
        logger.info(f"  MI {mi_ident or '未识别'}: 拆分出 {len(inner_code_chunks)} 个子项chunks")
        
        all_chunks.extend(inner_code_chunks)
    
    return all_chunks


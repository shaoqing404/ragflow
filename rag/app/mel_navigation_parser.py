# 2025-09-16由少卿组织写入，负责国航MEL文档解析的策略，支持RAGFlow耦合和FTMS服务
import json
from pathlib import Path
from rag.app.naive import Pdf  # Use the correct high-level API
import logging
from datetime import datetime
import sys
import os
from io import BytesIO
import re
import copy
import random

# Import RAGFlow's standard tokenization mechanism
from rag.nlp import rag_tokenizer, tokenize_chunks, is_english

# Add vibe path for imports
# sys.path.append(os.path.join(os.path.dirname(__file__), "..", "..", "vibe"))

# Import Gemini's MelHierarchyExtractor for dynamic navigation map generation
try:
    from .link_hierarchy_extractor import MelHierarchyExtractorV2_2 as MelHierarchyExtractor

    HIERARCHY_EXTRACTOR_AVAILABLE = True
    print("✅ MelHierarchyExtractor V2.2 loaded successfully")
except ImportError as e:
    HIERARCHY_EXTRACTOR_AVAILABLE = False
    print(f"Warning: MelHierarchyExtractor not available: {e}")

# Use a logger for better output management
logger = logging.getLogger(__name__)


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


def _extract_equipment_name(text_content):
    """Extract equipment name from text content."""
    if not text_content:
        return ""

    import re

    # Look for patterns like "设备名称" or equipment identifiers
    lines = text_content.split("\n")
    for line in lines[:10]:  # Check first 10 lines
        # Skip empty lines
        if not line.strip():
            continue
        # Look for equipment patterns
        # Often the equipment name appears after the ATA code
        match = re.match(r"^\d{2}-\d{2}(?:-\d{2})*\s+(.+)", line)
        if match:
            return match.group(1).strip()

    # If no pattern found, use first non-empty line as fallback
    for line in lines:
        if line.strip() and not line.strip().isdigit():
            return line.strip()[:100]  # Limit to 100 chars

    return ""


def find_navigation_map():
    """Finds the absolute path to navigation_map.json using multiple strategies."""
    possible_paths = [
        Path(__file__).parent.parent.parent / "vibe" / "gemini" / "navigation_map.json",
        Path.cwd() / "vibe" / "gemini" / "navigation_map.json",
        Path("/mnt/d/element_WorkSpeac/ragflow/vibe/gemini/navigation_map.json"),
    ]
    for path in possible_paths:
        if path.exists():
            logger.info(f"Found navigation_map.json at: {path}")
            return str(path)
    return None


def store_navigation_map_to_ftms(navigation_map, doc_id, doc_name):
    """Store navigation map in FTMS document-level metadata"""
    try:
        from FTMS.ftms_manager import FTMSDatabase

        ftms_db = FTMSDatabase()

        # Prepare document metadata with navigation map
        doc_metadata = {
            "doc_id": doc_id,
            "doc_name": doc_name,
            "parser_type": "mel_navigation_dynamic",
            "total_targets": len(navigation_map),
            "navigation_map": navigation_map,  # Store entire map as JSON field
            "status": "processing",
        }

        success = ftms_db.save_document_metadata(doc_metadata)

        if success:
            logger.info(f"✅ Successfully stored navigation map with {len(navigation_map)} entries to FTMS")
            return True
        else:
            logger.warning("⚠️ Failed to store navigation map to FTMS")
            return False

    except Exception as e:
        logger.warning(f"⚠️ FTMS storage failed: {e}. Navigation map will remain in memory only.")
        return False


# This is the entry point function that RAGFlow's task_executor will call.
def chunk(filename, binary=None, from_page=0, to_page=100000, **kwargs):
    """
    Entry point for the navigation-based MEL parser.
    Uses the high-level `Pdf` class from naive.py to parse sections efficiently.
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
            logger.info(f"[DeepDoc] {msg}")
        elif len(args) == 2:
            # 模式2: callback(0.63, "Layout analysis")
            prog, msg = args
            logger.info(f"[DeepDoc] {prog * 100:.1f}% - {msg}")
        elif len(args) == 1 and isinstance(args[0], str):
            # 其他情况：只有消息
            logger.info(f"[DeepDoc] {args[0]}")

    callback = kwargs.get("callback", smart_callback)

    # --- 1. Generate or Load Navigation Map ---
    navigation_map = None

    # Strategy 1: Try to generate navigation map dynamically using MelHierarchyExtractor
    if HIERARCHY_EXTRACTOR_AVAILABLE and binary:
        try:
            logger.info("🚀 Attempting dynamic navigation map generation using MelHierarchyExtractor...")
            callback(0.05, "Generating navigation map dynamically from PDF...")

            # Initialize extractor with binary data
            extractor = MelHierarchyExtractor(binary)

            # Build navigation map in memory
            navigation_map = extractor.build_map()

            if navigation_map:
                logger.info(f"✅ Successfully generated navigation map with {len(navigation_map)} entries")
                callback(0.1, f"Dynamic map generation successful: {len(navigation_map)} entries found.")

                # Store to FTMS if available
                doc_id = kwargs.get("doc_id", "unknown")
                doc_name = kwargs.get("docnm_kwd", filename if filename else "MEL_Document")
                store_navigation_map_to_ftms(navigation_map, doc_id, doc_name)

        except Exception as e:
            logger.warning(f"⚠️ Dynamic map generation failed: {e}. Falling back to static map.")
            navigation_map = None

    # Strategy 2: Fall back to static navigation_map.json file
    if not navigation_map:
        map_path = find_navigation_map()
        if not map_path:
            err_msg = "Could not find or generate navigation map. Aborting MEL parsing."
            logger.error(err_msg)
            callback(-1, err_msg)
            return []

        logger.info("📁 Using static navigation map from file...")
        with open(map_path, "r", encoding="utf-8") as f:
            navigation_map = json.load(f)
        callback(0.1, f"Loaded static navigation map with {len(navigation_map)} entries.")

    # --- 2. 选择处理模式：测试模式 vs 全量模式 ---
    parser = Pdf()  # Instantiate the high-level wrapper class
    sections = []  # ✅ 改为sections格式，用于tokenize_chunks

    # 检查是否为全量测试模式
    ENABLE_FULL_TEST = kwargs.get("enable_full_test", False)

    # Check for test limit from environment
    TEST_LIMIT = os.environ.get("MEL_TEST_LIMIT")

    if ENABLE_FULL_TEST:
        # 全量模式：处理所有575个目标
        navigation_items = list(navigation_map.items())
        logger.info(f"🚀 FULL TEST MODE: Processing all {len(navigation_items)} navigation targets")
        print(f"🚀 全量模式：开始处理所有 {len(navigation_items)} 个目标...")
        # 预估处理时间
        estimated_time = len(navigation_items) * 14.6 / 60  # 分钟
        print(f"⏱️ 预估全量处理时间: {estimated_time:.1f} 分钟 ({estimated_time / 60:.1f} 小时)")
    elif TEST_LIMIT:
        # Environment variable limit mode
        limit = int(TEST_LIMIT)
        navigation_items = list(navigation_map.items())[:limit]
        logger.info(f"🧪 LIMITED TEST MODE: Processing only {limit} chunks")
        print(f"🧪 限制测试模式：仅处理 {limit} 个目标")
    else:
        # 测试模式：仅处理ATA44区间的20个目标
        ata44_targets = [(k, v) for k, v in navigation_map.items() if 553 <= v["start_page"] <= 600]
        LIMITED_TEST_COUNT = 20
        navigation_items = ata44_targets[:LIMITED_TEST_COUNT]
        logger.info(f"🧪 TESTING MODE: Processing ATA44 range - first {LIMITED_TEST_COUNT} entries out of {len(ata44_targets)} ATA44 targets")
        print(f"🧪 测试模式：开始处理 {len(navigation_items)} 个目标...")
        print(f"📍 页面范围: {navigation_items[0][1]['start_page']}-{navigation_items[-1][1]['end_page']}")

    total_entries = len(navigation_items)

    # 添加计时和成功率统计
    import time

    start_time = time.time()
    success_count = 0
    nan_error_count = 0
    fallback_success_count = 0

    for i, (target_name, boundaries) in enumerate(navigation_items):
        # -----[GEMINI-MODIFICATION-START]-----
        # Handle composite keys (e.g., "MEL-33-37-01") from V2.2 extractor
        if target_name.startswith(("MEL-", "CDL-", "UNK-")):
            parts = target_name.split("-", 1)
            context_prefix = parts[0]
            true_ata_code = parts[1]
        else:
            context_prefix = "UNK"
            true_ata_code = target_name
        # -----[GEMINI-MODIFICATION-END]-------

        start_page = boundaries["start_page"]
        end_page = boundaries["end_page"]

        prog = 0.1 + 0.8 * (i + 1) / total_entries
        msg = f"Processing target '{true_ata_code}' (Context: {context_prefix}): Pages {start_page}-{end_page}"
        callback(prog, msg)
        logger.info(msg)

        try:
            # The Pdf class __call__ method correctly supports from_page and to_page
            # and only runs OCR on the specified page range.
            pdf_sections, _ = parser(
                binary if binary else filename,
                from_page=start_page - 1,  # The API is 0-indexed
                to_page=end_page,
                callback=callback,  # Pass the callback to avoid NoneType error
            )

            # pdf_sections is a list of (text, tag) tuples. We only need the text.
            text_content = "\n".join([sec[0] for sec in pdf_sections if sec and sec[0]])

            print(f"🔍 [DEBUG] Target '{true_ata_code}': extracted {len(text_content)} characters")
            print(f"🔍 [DEBUG] PDF sections count: {len(pdf_sections)}")
            if text_content.strip():
                print(f"🔍 [DEBUG] Sample content: {text_content[:100]}...")
            else:
                logger.warning(f"No text content extracted for target '{true_ata_code}'.")
                print(f"⚠️ [DEBUG] Empty content for '{true_ata_code}' - skipping")
                continue

            # Create a standardized RAGFlow chunk with enriched metadata
            # Extract additional information from navigation map
            nav_info = boundaries

            # Build enriched metadata using Gemini's navigation map data
            enriched_metadata = {
                "source_target": target_name,  # Keep original composite key for compatibility/reference
                "start_page": start_page,
                "end_page": end_page,
                "parser": "mel_navigation_parser_v3.1",  # Version bump to signify change
                # -----[GEMINI-MODIFICATION-START]-----
                "context": context_prefix,
                "ata_code": true_ata_code,
                "title": nav_info.get("title", ""),
                "full_anchor_text": nav_info.get("full_anchor_text", target_name),
                "source_destination": nav_info.get("source_destination", ""),
                "content_type": _classify_content_type(true_ata_code),
                "equipment_name": _extract_equipment_name(text_content),
                "hierarchy_level": len(true_ata_code.split("-")) if "-" in true_ata_code else 1,
                "ata_hierarchy_path": nav_info.get("ata_hierarchy", {}).get("full_path", [true_ata_code]),
                # -----[GEMINI-MODIFICATION-END]-------
                "ata_level": nav_info.get("ata_hierarchy", {}).get("level", 1),
                "ata_parent_code": nav_info.get("ata_hierarchy", {}).get("parent_code"),
                "ata_base_code": nav_info.get("ata_hierarchy", {}).get("base_code", true_ata_code),
                "ata_suffix": nav_info.get("ata_hierarchy", {}).get("suffix"),
                "has_ata_suffix": nav_info.get("ata_hierarchy", {}).get("has_suffix", False),
                "content_type_v2": nav_info.get("content_classification", {}).get("type", "item"),
                "content_classification_confidence": nav_info.get("content_classification", {}).get("confidence", 0.95),
                "cache_utilization": nav_info.get("extraction_metadata", {}).get("cache_hit", False),
                "extraction_time_ms": nav_info.get("extraction_metadata", {}).get("generation_time_ms", 0),
                "coordinate_precision": 0.95 if nav_info.get("coordinates") else 0.0,
                "extraction_method": "coordinate_based_v2" if nav_info.get("coordinates") else "page_based_v1",
            }

            # ✅ 生成section格式 (text_content, metadata)，用于tokenize_chunks
            section_data = (text_content, enriched_metadata)
            sections.append(section_data)
            print(f"✅ [DEBUG] Successfully created section for '{true_ata_code}', total sections: {len(sections)}")
            success_count += 1

        except ValueError as ve:
            if "cannot convert float NaN to integer" in str(ve):
                logger.warning(f"NaN error for '{target_name}', attempting fallback OCR without layout analysis")
                print(f"⚠️ [DEBUG] NaN error for '{target_name}', trying simple text extraction")

                # 特殊调试F415
                if target_name == "F415":
                    print("🔍 [F415 SPECIAL DEBUG] F415页面内容应该是44-11-01客舱乘务员面板")
                    print(f"🔍 [F415 SPECIAL DEBUG] 页面范围: {start_page}-{end_page} (555页)")

                    # 使用pdfplumber直接读取验证
                    try:
                        import pdfplumber

                        with pdfplumber.open(filename if not binary else BytesIO(binary)) as pdf:
                            page = pdf.pages[start_page - 1]  # 0-indexed
                            direct_text = page.extract_text()
                            print(f"🔍 [F415 SPECIAL DEBUG] pdfplumber直接提取: {len(direct_text) if direct_text else 0}字符")
                            if direct_text:
                                print(f"🔍 [F415 SPECIAL DEBUG] 内容预览: {direct_text[:200]}...")
                                # 如果pdfplumber能读到，就直接使用这个文本
                                nav_info = boundaries
                                enriched_metadata = {
                                    "source_target": target_name,
                                    "start_page": start_page,
                                    "end_page": end_page,
                                    "parser": "mel_navigation_parser_v3_pdfplumber_direct",
                                    "ata_code": true_ata_code,
                                    "title": nav_info.get("title", ""),
                                    "full_anchor_text": nav_info.get("full_anchor_text", target_name),
                                    "source_destination": nav_info.get("source_destination", ""),
                                    "content_type": _classify_content_type(true_ata_code),
                                    "equipment_name": _extract_equipment_name(direct_text),
                                    "hierarchy_level": len(true_ata_code.split("-")) if "-" in true_ata_code else 1,
                                    # V2.2 metadata fields
                                    "ata_hierarchy_path": nav_info.get("ata_hierarchy", {}).get("full_path", [true_ata_code]),
                                    "ata_level": nav_info.get("ata_hierarchy", {}).get("level", 1),
                                    "ata_parent_code": nav_info.get("ata_hierarchy", {}).get("parent_code"),
                                    "ata_base_code": nav_info.get("ata_hierarchy", {}).get("base_code", true_ata_code),
                                    "ata_suffix": nav_info.get("ata_hierarchy", {}).get("suffix"),
                                    "has_ata_suffix": nav_info.get("ata_hierarchy", {}).get("has_suffix", False),
                                    "content_type_v2": nav_info.get("content_classification", {}).get("type", "item"),
                                    "content_classification_confidence": nav_info.get("content_classification", {}).get("confidence", 0.95),
                                    "cache_utilization": nav_info.get("extraction_metadata", {}).get("cache_hit", False),
                                    "extraction_time_ms": nav_info.get("extraction_metadata", {}).get("generation_time_ms", 0),
                                    "coordinate_precision": 0.95 if nav_info.get("coordinates") else 0.0,
                                    "extraction_method": "pdfplumber_direct_v2",
                                }
                                # ✅ 生成section格式 (text_content, metadata)，用于tokenize_chunks
                                section_data = (direct_text, enriched_metadata)
                                sections.append(section_data)
                                print("✅ [F415 SPECIAL DEBUG] 使用pdfplumber直接提取成功创建section")
                                fallback_success_count += 1
                                continue
                    except Exception as direct_error:
                        print(f"❌ [F415 SPECIAL DEBUG] pdfplumber直接提取也失败: {direct_error}")

                # 尝试简单的文本提取，跳过复杂的版面分析
                try:
                    # 使用基础OCR，不进行版面分析
                    from deepdoc.parser import PdfParser

                    basic_parser = PdfParser()
                    print(f"🔧 [DEBUG] 使用fallback PdfParser处理 '{true_ata_code}'")
                    print(f"🔧 [DEBUG] 参数: page_from={start_page - 1}, page_to={end_page}")

                    # 只执行OCR，使用正确的参数名
                    basic_parser.__images__(
                        filename if not binary else binary,
                        zoomin=3,
                        page_from=start_page - 1,  # 正确的参数名是page_from
                        page_to=end_page,  # 正确的参数名是page_to
                        callback=callback,
                    )

                    print("🔧 [DEBUG] OCR完成，检查boxes属性...")
                    # 直接获取OCR结果，跳过版面分析
                    if hasattr(basic_parser, "boxes") and basic_parser.boxes:
                        print(f"📦 [DEBUG] Found {len(basic_parser.boxes)} boxes for '{true_ata_code}'")
                        # 简单拼接所有文本
                        text_parts = []
                        for i, box in enumerate(basic_parser.boxes):
                            if isinstance(box, list):
                                # boxes可能是嵌套列表
                                for j, inner_box in enumerate(box):
                                    if isinstance(inner_box, dict) and inner_box.get("text"):
                                        text_parts.append(inner_box["text"].strip())
                                        if target_name == "F415":
                                            print(f"🔍 [F415] box[{i}][{j}]: {repr(inner_box.get('text', '')[:50])}")
                            elif isinstance(box, dict) and box.get("text"):
                                text_parts.append(box["text"].strip())
                                if target_name == "F415":
                                    print(f"🔍 [F415] box[{i}]: {repr(box.get('text', '')[:50])}")

                        print(f"📝 [DEBUG] Extracted {len(text_parts)} text parts from boxes")
                        text_content = "\n".join(text_parts)

                        if text_content.strip():
                            print(f"✅ [DEBUG] Fallback OCR extracted {len(text_content)} characters for '{true_ata_code}'")
                            print(f"✅ [DEBUG] Fallback sample: {text_content[:100]}...")

                            # 创建chunk
                            nav_info = boundaries
                            enriched_metadata = {
                                "source_target": target_name,
                                "start_page": start_page,
                                "end_page": end_page,
                                "parser": "mel_navigation_parser_v3_fallback",
                                "ata_code": true_ata_code,
                                "title": nav_info.get("title", ""),
                                "full_anchor_text": nav_info.get("full_anchor_text", target_name),
                                "source_destination": nav_info.get("source_destination", ""),
                                "content_type": _classify_content_type(true_ata_code),
                                "equipment_name": _extract_equipment_name(text_content),
                                "hierarchy_level": len(true_ata_code.split("-")) if "-" in true_ata_code else 1,
                                # V2.2 metadata fields
                                "ata_hierarchy_path": nav_info.get("ata_hierarchy", {}).get("full_path", [true_ata_code]),
                                "ata_level": nav_info.get("ata_hierarchy", {}).get("level", 1),
                                "ata_parent_code": nav_info.get("ata_hierarchy", {}).get("parent_code"),
                                "ata_base_code": nav_info.get("ata_hierarchy", {}).get("base_code", true_ata_code),
                                "ata_suffix": nav_info.get("ata_hierarchy", {}).get("suffix"),
                                "has_ata_suffix": nav_info.get("ata_hierarchy", {}).get("has_suffix", False),
                                "content_type_v2": nav_info.get("content_classification", {}).get("type", "item"),
                                "content_classification_confidence": nav_info.get("content_classification", {}).get("confidence", 0.95),
                                "cache_utilization": nav_info.get("extraction_metadata", {}).get("cache_hit", False),
                                "extraction_time_ms": nav_info.get("extraction_metadata", {}).get("generation_time_ms", 0),
                                "coordinate_precision": 0.95 if nav_info.get("coordinates") else 0.0,
                                "extraction_method": "fallback_ocr_v2",
                            }
                            # ✅ 生成section格式 (text_content, metadata)，用于tokenize_chunks
                            section_data = (text_content, enriched_metadata)
                            sections.append(section_data)
                            print(f"✅ [DEBUG] Fallback section created for '{true_ata_code}', total sections: {len(sections)}")
                            fallback_success_count += 1
                            continue
                        else:
                            print(f"⚠️ [DEBUG] Text content is empty after extraction for '{true_ata_code}'")
                    else:
                        boxes_type = type(basic_parser.boxes) if hasattr(basic_parser, "boxes") else "None"
                        boxes_len = len(basic_parser.boxes) if hasattr(basic_parser, "boxes") and basic_parser.boxes else 0
                        print(f"⚠️ [DEBUG] No boxes found in fallback OCR for '{true_ata_code}'")
                        print(f"⚠️ [DEBUG] basic_parser.boxes type: {boxes_type}, length: {boxes_len}")
                        if target_name == "F415":
                            print(f"🔍 [F415 DEBUG] basic_parser attributes: {[attr for attr in dir(basic_parser) if not attr.startswith('_')]}")

                except Exception as fallback_error:
                    print(f"❌ [DEBUG] Fallback also failed for '{target_name}': {fallback_error}")
                    print(f"❌ [DEBUG] Fallback error type: {type(fallback_error)}")
                    if target_name == "F415":
                        import traceback

                        print("🔍 [F415 DEBUG] Full traceback:")
                        traceback.print_exc()

                logger.warning(f"Skipping target '{target_name}' completely due to persistent errors")
                nan_error_count += 1
                continue
            else:
                raise ve  # Re-raise other ValueError

        except Exception as e:
            logger.exception(f"[ERROR] Failed to parse target '{true_ata_code}'. Reason: {e}")
            # Continue to the next item in the map
            continue

    callback(1.0, f"Chunking finished. Generated {len(final_chunks)} chunks.")

    # Performance statistics
    elapsed_time = time.time() - start_time

    print("\n" + "=" * 60)
    print("📊 处理统计:")
    print(f"   总目标数: {total_entries}")
    print(f"   成功处理: {success_count} (正常)")
    print(f"   回退成功: {fallback_success_count} (使用fallback)")
    print(f"   跳过处理: {nan_error_count} (NaN错误)")
    print(f"   总sections: {len(sections)}")
    print(f"   成功率: {len(sections) / total_entries * 100:.1f}%")
    print(f"   处理时间: {elapsed_time:.1f}秒")
    print(f"   平均速度: {elapsed_time / total_entries:.1f}秒/目标")
    print("=" * 60)

    # ✅ 使用RAGFlow标准tokenize_chunks机制
    if sections:
        # 检测是否为英文
        sample_texts = [section[0] for section in sections if section[0]][:20]
        eng = is_english(random.choices(sample_texts, k=min(len(sample_texts), 20))) if sample_texts else False

        # 使用tokenize_chunks处理
        result_chunks = tokenize_chunks([section[0] for section in sections], doc, eng, parser)

        # 添加MEL特有的metadata到每个chunk
        for i, chunk in enumerate(result_chunks):
            if i < len(sections):
                section_text, section_metadata = sections[i]
                chunk.update(section_metadata)  # 添加MEL特有的metadata
    else:
        result_chunks = []

    # 保存chunks到文件 - 使用健壮的多候选路径机制
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
                logs_dir.mkdir(parents=True, exist_ok=True)  # ✅ 添加parents=True
                chunks_file = logs_dir / f"chunks_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
                with open(chunks_file, "w", encoding="utf-8") as f:
                    json.dump(result_chunks, f, ensure_ascii=False, indent=2, default=str)
                break
            except Exception as err:
                logger.warning("Failed to persist chunks under %s (%s)", root, err)
                chunks_file = None
        if chunks_file and chunks_file.exists():
            print(f"📁 Chunks已保存到: {chunks_file}")
            print(f"   文件大小: {chunks_file.stat().st_size / 1024:.1f} KB")
        else:
            logger.warning("Unable to persist chunks summary; skipping file output.")

    logger.info(f"Finished MEL Parsing. Generated {len(result_chunks)} chunks.")

    return result_chunks


# Example of how to run this for testing (not part of the final integration)
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

    logger.info("--- Running mel_navigation_parser.py in standalone test mode (v3) ---")

    # Mock data for testing
    mock_kwargs = {"doc_id": "test_doc_id_123", "docnm_kwd": "B787-9MEL.pdf", "callback": lambda *args, **kwargs: logger.info(f"[Callback] Args: {args}, Kwargs: {kwargs}") if args or kwargs else None}

    # Find the test PDF
    project_root_candidates = [Path(__file__).parent.parent.parent, Path.cwd(), Path("/mnt/d/element_WorkSpeac/ragflow")]
    test_pdf_path = None
    for root in project_root_candidates:
        path = root / "vibe" / "data" / "B787-9MEL.pdf"
        if path.exists():
            test_pdf_path = str(path)
            break

    if not test_pdf_path:
        logger.error("Test PDF not found. Skipping standalone run.")
    else:
        logger.info(f"Using test PDF: {test_pdf_path}")
        with open(test_pdf_path, "rb") as f:
            binary_content = f.read()

        # Run the main chunk function
        result_chunks = chunk(filename=test_pdf_path, binary=binary_content, **mock_kwargs)

        logger.info("\n--- Standalone Test Finished ---")
        logger.info(f"Total chunks generated: {len(result_chunks)}")
        if result_chunks:
            logger.info("Sample of the first chunk generated:")
            # Print metadata and a snippet of the content
            if "metadata" in result_chunks[0]:
                print(json.dumps(result_chunks[0]["metadata"], indent=2, ensure_ascii=False))
            print("Content Snippet:")
            print(result_chunks[0]["content_with_weight"][:200] + "...")
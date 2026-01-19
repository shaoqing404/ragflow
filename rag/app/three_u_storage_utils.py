"""
3U MEL 日志存储工具
2025年12月23日13:37:08
将日志存储逻辑从主parser分离，降低代码复杂度
"""

import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 默认日志目录
DEFAULT_LOG_DIR = Path("/mnt/d/element_WorkSpeac/ragflow_worktrees/strategy-a-sync/vibe/logs")


def make_serializable(obj: Any) -> Any:
    """
    递归过滤不可JSON序列化的对象（如Image、bytes、file-like）
    """
    if isinstance(obj, dict):
        return {k: make_serializable(v) for k, v in obj.items() 
                if not isinstance(v, bytes) and not hasattr(v, 'read')}
    elif isinstance(obj, list):
        return [make_serializable(item) for item in obj]
    elif hasattr(obj, '__dict__') and not isinstance(obj, (str, int, float, bool, type(None))):
        return str(obj)  # 将复杂对象转为字符串
    return obj


def save_chunks_archive(
    chunks: List[Dict[str, Any]], 
    log_dir: Optional[Path] = None,
    prefix: str = "3u_mel_all_chunks"
) -> Optional[Path]:
    """
    保存所有chunks到归档文件（包括未通过筛选的）
    
    Args:
        chunks: 要保存的chunks列表
        log_dir: 日志目录路径
        prefix: 文件名前缀
        
    Returns:
        保存的文件路径，失败返回None
    """
    if not chunks:
        logger.warning("No chunks to archive")
        return None
        
    log_dir = log_dir or DEFAULT_LOG_DIR
    log_dir.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filepath = log_dir / f"{prefix}_{timestamp}.json"
    
    try:
        serializable_chunks = make_serializable(chunks)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(serializable_chunks, f, ensure_ascii=False, indent=2, default=str)
        logger.info(f"📁 Chunks archived to: {filepath}")
        return filepath
    except Exception as e:
        logger.warning(f"Failed to save archive: {e}")
        return None


def save_final_chunks(
    chunks: List[Dict[str, Any]], 
    log_dir: Optional[Path] = None,
    prefix: str = "3u_mel_chunks"
) -> Optional[Path]:
    """
    保存最终返回给RAGFlow的chunks（通过筛选的）
    
    Args:
        chunks: 要保存的chunks列表
        log_dir: 日志目录路径
        prefix: 文件名前缀
        
    Returns:
        保存的文件路径，失败返回None
    """
    if not chunks:
        logger.warning("No chunks to save")
        return None
        
    log_dir = log_dir or DEFAULT_LOG_DIR
    log_dir.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filepath = log_dir / f"{prefix}_{timestamp}.json"
    
    try:
        serializable_chunks = make_serializable(chunks)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(serializable_chunks, f, ensure_ascii=False, indent=2, default=str)
        
        # 打印统计信息
        file_size_kb = filepath.stat().st_size / 1024
        print(f"📁 3U_MEL Chunks已保存到: {filepath}")
        print(f"   文件大小: {file_size_kb:.1f} KB")
        
        return filepath
    except Exception as e:
        logger.warning(f"Failed to save chunks: {e}")
        return None


def save_drop_records(
    records: List[Dict[str, Any]], 
    log_dir: Optional[Path] = None,
    prefix: str = "3u_mel_dropped"
) -> Optional[Path]:
    """
    保存丢弃记录（JSONL格式）
    
    Args:
        records: 丢弃记录列表
        log_dir: 日志目录路径
        prefix: 文件名前缀
        
    Returns:
        保存的文件路径，失败返回None
    """
    if not records:
        return None
        
    log_dir = log_dir or DEFAULT_LOG_DIR
    log_dir.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filepath = log_dir / f"{prefix}_{timestamp}.jsonl"
    
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            for record in records:
                json.dump(record, f, ensure_ascii=False)
                f.write("\n")
        logger.info(f"🗑️ Drop records saved to: {filepath}")
        return filepath
    except Exception as e:
        logger.warning(f"Failed to save drop records: {e}")
        return None


def log_processing_stats(
    total_targets: int,
    success_count: int,
    fallback_count: int,
    total_chunks: int,
    raw_chunk_count: int,
    dedup_skipped: int,
    dropped_empty: int,
    processing_time: float,
    vlm_stats: Optional[Dict[str, int]] = None
) -> None:
    """
    打印处理统计信息
    """
    success_rate = (success_count / total_targets * 100) if total_targets > 0 else 0
    avg_speed = processing_time / total_targets if total_targets > 0 else 0
    
    print("\n" + "=" * 60)
    print("📊 3U_MEL 处理统计 (v2.0):")
    print(f"   总目标数: {total_targets}")
    print(f"   成功处理: {success_count} (正常)")
    print(f"   回退成功: {fallback_count} (使用fallback)")
    print(f"   总chunks数: {total_chunks}")
    print(f"   原始chunk数: {raw_chunk_count} (去重前)")
    print(f"   去重跳过: {dedup_skipped} 段")
    print(f"   过滤丢弃: {dropped_empty} 段 (空/无token)")
    print(f"   成功率: {success_rate:.1f}%")
    print(f"   处理时间: {processing_time:.1f}秒")
    print(f"   平均速度: {avg_speed:.1f}秒/目标")
    print("=" * 60)
    
    if vlm_stats:
        print(f"   VLM: 尝试 {vlm_stats.get('attempted', 0)} 个目标, "
              f"成功 {vlm_stats.get('success', 0)}, "
              f"失败 {vlm_stats.get('failed', 0)}")


def log_quality_filter_stats(
    before_count: int,
    complete_count: int,
    incomplete_count: int
) -> None:
    """
    打印质量筛选统计
    """
    logger.info(f"🔍 3U_MEL: 开始质量筛选 - 筛选前共 {before_count} 个chunks")
    logger.info(f"✅ 3U_MEL: 质量筛选完成")
    logger.info(f"   - 完整chunks (返回RAGFlow): {complete_count}")
    logger.info(f"   - 不完整chunks (仅存档): {incomplete_count}")


def log_dedup_stats(before_count: int, after_count: int, removed_count: int) -> None:
    """
    打印去重统计
    """
    logger.info(f"🔍 3U_MEL: 开始全局去重 - 去重前共 {before_count} 个chunks")
    if removed_count > 0:
        logger.info(f"去重: 移除 {removed_count} 个完全重复的chunks")
    logger.info(f"✅ 3U_MEL: 全局去重完成 - 去重后共 {after_count} 个chunks，移除 {removed_count} 个重复")

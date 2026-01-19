# 2025-09-16由少卿组织写入，负责国航MEL文档解析的策略，支持RAGFlow耦合和FTMS服务
#!/usr/bin/env python3
"""
K2独立提取器 - 完全基于V2.2逻辑，不依赖任何外部模块

参考：
1. gemini/extract_link_hierarchy_v2.2.py - 核心算法
2. rag/app/mel_navigation_parser.py - 集成逻辑
"""

import pypdf
import json
import os
import re
import io
import time
import logging
from collections import deque
from datetime import datetime


class K2_Standalone_Extractor:
    """
    K2独立提取器 - 基于V2.2逻辑的完整实现
    """

    def __init__(self, pdf_path_or_stream):
        """初始化提取器"""
        if isinstance(pdf_path_or_stream, str):
            if not os.path.exists(pdf_path_or_stream):
                raise FileNotFoundError(f"PDF文件未找到: {pdf_path_or_stream}")
            print(f"信息: 成功加载PDF (来自路径): {os.path.basename(pdf_path_or_stream)}")
            self.reader = pypdf.PdfReader(pdf_path_or_stream)
        elif isinstance(pdf_path_or_stream, bytes):
            print("信息: 成功加载PDF (来自二进制流).")
            self.reader = pypdf.PdfReader(io.BytesIO(pdf_path_or_stream))
        else:
            raise TypeError("输入必须是文件路径 (str) 或二进制流 (bytes).")

        self.dest_to_name_map = {id(v): k for k, v in self.reader.named_destinations.items()}
        self.page_text_cache = {}
        self.logger = self._setup_logger()

    def _setup_logger(self):
        """设置日志"""
        logger = logging.getLogger(__name__)
        if not logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
            handler.setFormatter(formatter)
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)
        return logger

    def _get_text_in_rect(self, page_num, rect):
        """提取矩形区域内的文本（带缓存）"""
        start_time = time.time()
        if page_num in self.page_text_cache:
            page_parts = self.page_text_cache[page_num]
            cache_hit = True
        else:
            try:
                visitor_parts = []

                def visitor_body(text, cm, tm, fontDict, fontSize):
                    visitor_parts.append({"text": text, "x": tm[4], "y": tm[5]})

                self.reader.pages[page_num - 1].extract_text(visitor_text=visitor_body)
                self.page_text_cache[page_num] = sorted(visitor_parts, key=lambda p: (p["y"], p["x"]))
                page_parts = self.page_text_cache[page_num]
            except Exception:
                self.page_text_cache[page_num] = []
                page_parts = []
            cache_hit = False

        extraction_time_ms = (time.time() - start_time) * 1000
        metadata = {"cache_hit": cache_hit, "generation_time_ms": int(extraction_time_ms), "extraction_method": "cached" if cache_hit else "full"}

        if not page_parts:
            return "", metadata

        x0, y0, x1, y1 = rect
        y_tolerance = 2.0
        line_parts = [part for part in page_parts if (y0 - y_tolerance) < part["y"] < (y1 + y_tolerance) and x0 <= part["x"] <= x1]
        line_parts.sort(key=lambda p: p["x"])
        return "".join([part["text"] for part in line_parts]).strip(), metadata

    def _get_all_links_by_page(self):
        """提取所有页面的链接和目录结构"""
        page_map = {}
        self.logger.info("信息: 开始提取所有页面的链接...")

        for i, page in enumerate(self.reader.pages):
            page_num = i + 1
            if (i + 1) % 200 == 0:
                self.logger.info(f"  - 正在处理页面 {page_num}/{len(self.reader.pages)}...")

            page_links = []

            # 方法1：尝试提取超链接
            if "/Annots" in page:
                for annot in page["/Annots"]:
                    annot_obj = annot.get_object()
                    if annot_obj.get("/Subtype") == "/Link":
                        try:
                            dest = annot_obj.get("/Dest")
                            if dest is None and "/A" in annot_obj:
                                action = annot_obj["/A"].get_object()
                                if action.get("/S") == "/GoTo":
                                    dest = action.get("/D")
                            if dest is None:
                                continue

                            dest_name = None
                            if isinstance(dest, pypdf.generic.ArrayObject):
                                dest_name = self.dest_to_name_map.get(id(dest[0].get_object()))
                            elif isinstance(dest, pypdf.generic.TextStringObject):
                                dest_name = str(dest)

                            if dest_name:
                                anchor_text, metadata = self._get_text_in_rect(page_num, annot_obj["/Rect"])
                                page_links.append({"anchor_text": anchor_text, "destination_name": dest_name, "rect": annot_obj["/Rect"], "extraction_metadata": metadata, "link_type": "hyperlink"})
                        except Exception:
                            pass

            # 方法2：如果没有链接，尝试从文本中提取目录项
            if not page_links:
                page_links = self._extract_text_based_items(page_num)

            if page_links:
                page_map[page_num] = page_links

        total_links = sum(len(links) for links in page_map.values())
        self.logger.info(f"信息: 页面链接提取完成。总共找到 {total_links} 个链接/目录项。")
        return page_map

    def _extract_from_outline(self):
        """从PDF大纲提取导航信息（新增方法）"""
        self.logger.info("信息: 开始从PDF大纲提取导航信息...")
        
        if not hasattr(self.reader, 'outline') or not self.reader.outline:
            self.logger.warning("警告: PDF没有大纲结构")
            return {}
            
        # 递归遍历大纲树，提取所有末节点
        leaves = self._collect_outline_leaves(self.reader.outline, path=[])
        
        if not leaves:
            self.logger.warning("警告: 未从大纲中提取到任何节点")
            return {}
            
        self.logger.info(f"信息: 从大纲中提取到 {len(leaves)} 个节点")
        return self._build_navigation_map_from_leaves(leaves)
        
    def _collect_outline_leaves(self, outline_items, path, parent_context="MEL"):
        """收集大纲中的末节点（新增方法）"""
        leaves = []
        
        # 确定当前上下文类型
        path_string = ' '.join(path)
        if 'CONFIGURATION DEVIATION LIST' in path_string:
            context_prefix = 'CDL'
        elif 'MINIMUM EQUIPMENT LIST' in path_string:
            context_prefix = 'MEL'
        else:
            context_prefix = parent_context
            
        for item in outline_items:
            if isinstance(item, list):
                # 递归处理子项
                sub_leaves = self._collect_outline_leaves(item, path, context_prefix)
                leaves.extend(sub_leaves)
            elif isinstance(item, pypdf.generic.Destination):
                # 检查是否为末节点（包含ATA代码格式）
                is_leaf = bool(re.search(r"\d{2}-\d{2}-\d{2}", item.title)) or \
                         bool(re.search(r"\d{2}-\d{2}", item.title))
                
                page_num = self.reader.get_destination_page_number(item)
                
                if is_leaf and page_num is not None:
                    # 叶子节点：包含页面和位置信息
                    leaves.append({
                        'title': item.title,
                        'page_num': page_num + 1,  # 转为1基索引
                        'left': getattr(item, 'left', 0),
                        'top': getattr(item, 'top', 0),
                        'context': context_prefix,
                        'path': path + [item.title]
                    })
                elif page_num is not None:
                    # 非叶子节点，递归处理其子项（如果有）
                    # 这里我们简化处理，不深入遍历非叶子节点
                    pass
                    
        return leaves

    def _extract_text_based_items(self, page_num):
        """基于文本提取目录项（针对没有超链接的PDF）"""
        page_items = []

        try:
            page = self.reader.pages[page_num - 1]
            text_content = page.extract_text()

            if not text_content:
                return page_items

            lines = text_content.split("\n")

            # 查找可能的目录项模式
            for line in lines:
                line = line.strip()
                if not line:
                    continue

                # 模式1：数字-数字-数字格式 (如 24-10-01)
                match1 = re.match(r"^(\d{1,2}-\d{1,2}-\d{1,2})(?:\s*([A-Z]?))?\s+(.+)", line)
                if match1:
                    ata_code = match1.group(1)
                    if match1.group(2):
                        ata_code += match1.group(2)
                    title = match1.group(3)

                    # 模拟一个位置（使用页面中点）
                    page_rect = page.mediabox
                    simulated_rect = [
                        float(page_rect[0] + 50),  # x0
                        float(page_rect[3] - 20),  # y0
                        float(page_rect[0] + 200),  # x1
                        float(page_rect[3] - 5),  # y1
                    ]

                    page_items.append(
                        {
                            "anchor_text": f"{ata_code} {title}",
                            "destination_name": f"page_{page_num}",
                            "rect": simulated_rect,
                            "extraction_metadata": {"cache_hit": False, "generation_time_ms": 10, "extraction_method": "text_based"},
                            "link_type": "text_based",
                            "page_number": page_num,
                        }
                    )

                # 模式2：纯数字格式 (如 2401 或 24-01)
                match2 = re.match(r"^(\d{2,4})(?:\s*([A-Z]?))?\s+(.+)", line)
                if match2 and not match1:  # 避免重复匹配
                    ata_code = match2.group(1)
                    if match2.group(2):
                        ata_code += match2.group(2)
                    title = match2.group(3)

                    # 模拟一个位置
                    page_rect = page.mediabox
                    simulated_rect = [float(page_rect[0] + 50), float(page_rect[3] - 20), float(page_rect[0] + 200), float(page_rect[3] - 5)]

                    page_items.append(
                        {
                            "anchor_text": f"{ata_code} {title}",
                            "destination_name": f"page_{page_num}",
                            "rect": simulated_rect,
                            "extraction_metadata": {"cache_hit": False, "generation_time_ms": 10, "extraction_method": "text_based"},
                            "link_type": "text_based",
                            "page_number": page_num,
                        }
                    )

        except Exception as e:
            self.logger.debug(f"页面 {page_num} 文本提取失败: {e}")

        return page_items

    def _parse_ata_code(self, text_string):
        """解析ATA代码 - 基于V2.2逻辑"""
        if not text_string:
            return None, None
        match = re.match(r'^\s*(\d{2}-\d{2}-\d{2})([A-Z]?)\s*.*', text_string)
        if match:
            base_code = match.group(1)
            suffix = match.group(2) if match.group(2) else None
            return base_code, suffix
        return None, None
        
    def _calculate_content_boundaries(self, current_item, next_item):
        """根据位置信息计算内容边界（新增方法）"""
        # 使用当前项的位置作为起始
        start_page = current_item['page_num']
        start_y = current_item['top']
        
        # 使用下一项的位置作为结束（如果在同一页面）
        if next_item and next_item['page_num'] == start_page:
            end_y = next_item['top']
        else:
            end_y = 0  # 页面底部
        
        # 🆕 扩展边界+1页，避免共享页面的内容丢失
        # 重叠部分由后续去重处理
        if next_item:
            end_page = next_item['page_num'] + 1  # 包含下一项的起始页
        else:
            end_page = len(self.reader.pages)
            
        return {
            'start_page': start_page,
            'end_page': end_page,

            'coordinates': {
                'title_rect': {
                    'x0': float(current_item['left']),
                    'y0': float(current_item['top']),
                    'x1': float(current_item['left']) + 200,  # 假设标题宽度
                    'y1': float(current_item['top']) + 20    # 假设标题高度
                },
                'content_start_rect': {
                    'x0': float(current_item['left']),
                    'y0': float(current_item['top']) - 20,
                    'x1': 520.0,
                    'y1': float(current_item['top'])
                }
            }
        }
        
    def _build_navigation_map_from_leaves(self, leaves):
        """构建符合V2.2规范的导航地图（新增方法）"""
        if not leaves:
            return {}
            
        # 按页面排序
        sorted_leaves = sorted(leaves, key=lambda x: x['page_num'])
        
        final_map = {}
        for i, leaf in enumerate(sorted_leaves):
            # 解析ATA代码
            match = re.match(r'^\s*([\d\-]+[A-Z]?)\s*(.*)', leaf['title'])
            if match:
                ata_code = match.group(1).strip()
                title_text = match.group(2).strip()
                
                # 生成完整的键（包含上下文前缀）
                final_key = f"{leaf['context']}-{ata_code}"
                
                # 解析基础代码和后缀
                base_code, suffix = self._parse_ata_code(ata_code)
                
                # 构建路径代码
                path_codes = [self._parse_ata_code(p)[0] for p in leaf['path'] if self._parse_ata_code(p)[0]]
                parent_code = path_codes[-2] if len(path_codes) > 1 else None
                
                # 计算边界
                next_leaf = sorted_leaves[i + 1] if i + 1 < len(sorted_leaves) else None
                boundaries = self._calculate_content_boundaries(leaf, next_leaf)
                
                final_map[final_key] = {
                    "title": title_text,
                    "start_page": boundaries['start_page'],
                    "end_page": boundaries['end_page'],
                    "source_destination": f"page_{leaf['page_num']}",  # 模拟目标名称
                    "full_anchor_text": leaf['title'],
                    "coordinates": boundaries['coordinates'],
                    "ata_hierarchy": {
                        "full_path": path_codes,
                        "level": len(path_codes),
                        "parent_code": parent_code,
                        "base_code": base_code,
                        "suffix": suffix,
                        "has_suffix": bool(suffix),
                    },
                    "extraction_metadata": {
                        "cache_hit": False,
                        "generation_time_ms": 100,  # 模拟时间
                        "extraction_method": "outline_based"
                    },
                    "content_classification": {
                        "type": "item", 
                        "confidence": 0.95
                    }
                }
        
        return final_map

    def _traverse_and_collect_leaves(self, page_to_links_map):
        """遍历链接并收集叶子节点"""
        leaves = []
        visited_destinations = set()

        # 方法1：如果有超链接，使用原有逻辑
        has_hyperlinks = any(link.get("link_type") == "hyperlink" for page_links in page_to_links_map.values() for link in page_links)

        if has_hyperlinks:
            queue = deque()
            # 从目录开始
            if hasattr(self.reader, "outline") and self.reader.outline:
                for item in self.reader.outline:
                    if isinstance(item, pypdf.generic.Destination):
                        page_num = self.reader.get_destination_page_number(item)
                        if page_num is not None and page_num not in visited_destinations:
                            queue.append((page_num + 1, [item.title]))
                            visited_destinations.add(page_num)

            # 如果没有目录，从第一页开始
            if not queue:
                queue.append((1, ["目录"]))

            while queue:
                page_num, path = queue.popleft()
                links_on_page = page_to_links_map.get(page_num, [])

                for link in links_on_page:
                    dest_name = link["destination_name"]
                    if dest_name in visited_destinations:
                        continue
                    visited_destinations.add(dest_name)

                    # 判断是否为叶子节点（包含具体维修信息）
                    is_leaf = bool(re.search(r"\d{2}-\d{2}-\d{2}", link["anchor_text"]))

                    if is_leaf:
                        leaves.append({"link": link, "path": path})
                    else:
                        dest = self.reader.named_destinations.get(dest_name)
                        if dest:
                            target_page_num = self.reader.get_destination_page_number(dest)
                            if target_page_num is not None:
                                new_path = path + [link["anchor_text"]]
                                queue.append((target_page_num + 1, new_path))
        else:
            # 方法2：处理文本基础的目录项
            self.logger.info("使用文本基础模式处理目录项...")

            # 按页码排序处理
            sorted_pages = sorted(page_to_links_map.keys())

            for page_num in sorted_pages:
                links_on_page = page_to_links_map.get(page_num, [])

                for link in links_on_page:
                    # 对于文本基础的项目，直接作为叶子节点处理
                    if link.get("link_type") == "text_based" and re.search(r"\d{1,2}-\d{1,2}-\d{1,2}", link["anchor_text"]):
                        # 为文本基础的项目设置目标页面
                        if "page_number" in link:
                            target_page = link["page_number"]
                        else:
                            target_page = page_num

                        link["target_page"] = target_page
                        leaves.append({"link": link, "path": ["目录", link["anchor_text"]]})

        self.logger.info(f"遍历完成，找到 {len(leaves)} 个叶子节点")
        return leaves

    def _finalize_map(self, leaves):
        """生成最终的导航地图"""
        # 诊断无效目标
        invalid_target_leaves = [leaf for leaf in leaves if self.reader.named_destinations.get(leaf["link"]["destination_name"]) is None]
        if invalid_target_leaves:
            self.logger.info(f"诊断: 发现 {len(invalid_target_leaves)} 个无效目标节点 (将被丢弃)")

        for leaf_obj in leaves:
            link = leaf_obj["link"]
            dest = self.reader.named_destinations.get(link["destination_name"])
            leaf_obj["target_page"] = self.reader.get_destination_page_number(dest) + 1 if dest else -1

        valid_leaves = [leaf for leaf in leaves if leaf["target_page"] != -1]
        self.logger.info(f"诊断: 过滤无效目标后，剩余 {len(valid_leaves)} 个有效节点。")

        sorted_leaves = sorted(valid_leaves, key=lambda x: x["target_page"])

        final_map = {}

        # 遍历有效节点
        for i, leaf_obj in enumerate(sorted_leaves):
            link = leaf_obj["link"]
            start_page = leaf_obj["target_page"]
            end_page = len(self.reader.pages)

            if i + 1 < len(sorted_leaves):
                next_leaf = sorted_leaves[i + 1]
                end_page = next_leaf["target_page"] - 1 if next_leaf["target_page"] > start_page else start_page

            full_anchor_text = link["anchor_text"]
            match = re.match(r"^\s*([\d\-]+[A-Z]?)\s*(.*)", full_anchor_text)
            ata_code, title_text = (match.group(1).strip(), match.group(2).strip()) if match else (full_anchor_text, "")

            if not ata_code:
                continue

            if ata_code in final_map:
                self.logger.info(f"  - 警告: 重复编码, 文本: '{full_anchor_text}', 编码: '{ata_code}' (将覆盖前一节点)")

            base_code, suffix = self._parse_ata_code(ata_code)
            path_titles = leaf_obj["path"] + [full_anchor_text]
            path_codes = [self._parse_ata_code(p)[0] for p in path_titles if self._parse_ata_code(p)[0]]
            parent_code = path_codes[-2] if len(path_codes) > 1 else None

            rect = link["rect"]
            coords = {
                "title_rect": {"x0": float(rect[0]), "y0": float(rect[1]), "x1": float(rect[2]), "y1": float(rect[3])},
                "content_start_rect": {"x0": float(rect[0]), "y0": float(rect[3]), "x1": 520.0, "y1": float(rect[3]) + 20},
            }

            # K2特定信息
            k2_specific = {
                "aircraft_type": "A330",
                "airline": "四川航空",
                "extraction_date": datetime(2025, 1, 9).isoformat(),
                "maintenance_category": self._get_maintenance_category(base_code),
                "priority_level": self._get_priority_level(title_text),
                "sichuan_airlines_config": {"airline_id": "SC", "airline_name": "四川航空", "base_airport": "成都双流", "maintenance_center": "四川航空维修基地"},
            }

            final_map[ata_code] = {
                "title": title_text,
                "start_page": start_page,
                "end_page": end_page,
                "source_destination": link["destination_name"],
                "full_anchor_text": full_anchor_text,
                "coordinates": coords,
                "ata_hierarchy": {
                    "full_path": path_codes,
                    "level": len(path_codes),
                    "parent_code": parent_code,
                    "base_code": base_code,
                    "suffix": suffix,
                    "has_suffix": bool(suffix),
                },
                "extraction_metadata": link["extraction_metadata"],
                "content_classification": {"type": "item", "confidence": 0.95},
                "k2_specific": k2_specific,
            }

        self.logger.info(f"--- 诊断: _finalize_map 阶段结束，生成 {len(final_map)} 个条目 ---")
        return final_map

    def _get_maintenance_category(self, base_code):
        """获取维修类别"""
        if base_code:
            code_num = base_code.replace("-", "")[:2]
            category_map = {
                "24": "电力系统",
                "25": "设备/装饰",
                "26": "防火系统",
                "27": "飞控系统",
                "28": "燃油系统",
                "29": "液压系统",
                "30": "防冰/除雨",
                "31": "指示/记录系统",
                "32": "起落架系统",
                "33": "灯光系统",
                "34": "导航系统",
                "35": "氧气系统",
                "36": "气动系统",
                "38": "水/废水系统",
                "49": "APU系统",
                "52": "舱门系统",
                "53": "机身系统",
                "54": "短舱/吊舱",
                "55": "安定面",
                "56": "窗户",
                "57": "机翼",
            }
            return category_map.get(code_num, "其他系统")
        return "未知"

    def _get_priority_level(self, title_text):
        """获取优先级"""
        if any(keyword in title_text.lower() for keyword in ["emergency", "紧急", "warning", "警告", "caution", "注意"]):
            return 5
        elif any(keyword in title_text.lower() for keyword in ["critical", "关键", "重要", "essential"]):
            return 4
        elif any(keyword in title_text.lower() for keyword in ["procedure", "程序", "check", "检查", "test", "测试"]):
            return 3
        else:
            return 2

    def build_navigation_map(self):
        """构建导航地图"""
        start_time = time.time()
        self.logger.info("--- K2独立提取器: 开始构建导航地图 ---")

        # 优先尝试从大纲提取导航信息（针对川航330.pdf）
        self.logger.info("信息: 尝试从PDF大纲提取导航信息...")
        outline_map = self._extract_from_outline()
        
        if outline_map:
            self.logger.info(f"✅ 成功从大纲提取到 {len(outline_map)} 个导航项")
            end_time = time.time()
            self.logger.info(f"--- 导航地图构建完毕，耗时 {end_time - start_time:.2f} 秒 ---")
            return outline_map

        # 如果大纲提取失败，使用原有的基于链接的方法
        self.logger.info("信息: 大纲提取未成功，使用原有基于链接的方法...")
        self.logger.info(f"信息: 步骤 1: 构建页面链接地图 (共 {len(self.reader.pages)} 页)...")
        page_to_links_map = self._get_all_links_by_page()
        self.logger.info(f"信息: 步骤 1 完成. 在 {len(page_to_links_map)} 个页面上找到了链接。")

        self.logger.info("信息: 步骤 2: 遍历链接查找叶子节点 (带路径追踪)...")
        leaves = self._traverse_and_collect_leaves(page_to_links_map)
        self.logger.info(f"信息: 步骤 2 完成. 找到了 {len(leaves)} 个叶子节点。")

        self.logger.info("信息: 步骤 3: 生成最终导航地图 (K2独立版)...")
        final_map = self._finalize_map(leaves)
        self.logger.info(f"信息: 步骤 3 完成. 最终地图包含 {len(final_map)} 个条目。")

        end_time = time.time()
        self.logger.info(f"--- 导航地图构建完毕，耗时 {end_time - start_time:.2f} 秒 ---")
        return final_map

    def generate_k2_chunks(self, navigation_map, test_mode=False):
        """生成K2格式的chunks"""
        self.logger.info("🔄 生成K2格式的chunks...")

        chunks = []

        # 限制处理数量用于测试
        items_to_process = list(navigation_map.items())
        if test_mode and len(items_to_process) > 20:
            items_to_process = items_to_process[:20]

        for ata_code, nav_item in items_to_process:
            # 生成chunk内容
            content_text = f"""
{ata_code} - {nav_item.get("title", "")}

维修信息:
- 页码范围: {nav_item.get("start_page", 0)}-{nav_item.get("end_page", 0)}
- 维修类别: {nav_item.get("k2_specific", {}).get("maintenance_category", "未知")}
- 优先级: {nav_item.get("k2_specific", {}).get("priority_level", 1)}
- 航空公司: {nav_item.get("k2_specific", {}).get("airline", "四川航空")}
- 飞机类型: {nav_item.get("k2_specific", {}).get("aircraft_type", "A330")}

处理日期: {nav_item.get("k2_specific", {}).get("extraction_date", "2025-01-09")}
            """

            chunk = {
                "content_with_weight": content_text,
                "metadata": {
                    "ata_code": ata_code,
                    "title": nav_item.get("title", ""),
                    "page_range": [nav_item.get("start_page", 0), nav_item.get("end_page", 0)],
                    "coordinates": nav_item.get("coordinates", {}),
                    "ata_hierarchy": nav_item.get("ata_hierarchy", {}),
                    "extraction_metadata": nav_item.get("extraction_metadata", {}),
                    "content_classification": nav_item.get("content_classification", {}),
                    "k2_specific": nav_item.get("k2_specific", {}),
                    "doc_id": "k2_330_test",
                    "docnm_kwd": "330.pdf",
                    "aircraft_type": "A330",
                    "airline": "四川航空",
                },
            }
            chunks.append(chunk)

        self.logger.info(f"✅ 生成了 {len(chunks)} 个K2 chunks")
        return chunks

    def save_to_json(self, data, output_path):
        """保存数据到JSON文件"""
        try:
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            print(f"信息: 成功保存到: {output_path}")
        except Exception as e:
            print(f"错误: 保存JSON文件失败: {e}")


def main():
    """主函数 - 独立测试"""
    print("=== K2独立提取器测试 ===")

    # 查找330.pdf
    pdf_paths = ["../data/330.pdf", "D:/element_WorkSpeac/ragflow_worktrees/strategy-a-sync/vibe/tools/test_fix_data/320-2025.pdf", "/mnt/d/element_WorkSpeac/ragflow/vibe/data/330.pdf"]

    pdf_path = None
    for path in pdf_paths:
        if os.path.exists(path):
            pdf_path = path
            break

    if not pdf_path:
        print("错误: 找不到330.pdf文件")
        return

    print(f"使用PDF文件: {pdf_path}")

    try:
        # 创建提取器
        extractor = K2_Standalone_Extractor(pdf_path)

        # 构建导航地图
        navigation_map = extractor.build_navigation_map()

        # 保存导航地图
        nav_output = "k2_navigation_map.json"
        extractor.save_to_json(navigation_map, nav_output)

        # 生成chunks
        chunks = extractor.generate_k2_chunks(navigation_map, test_mode=True)

        # 保存chunks
        chunks_output = "k2_chunks.json"
        extractor.save_to_json(chunks, chunks_output)

        print("✅ 处理完成:")
        print(f"   导航条目: {len(navigation_map)}")
        print(f"   Chunks: {len(chunks)}")
        print(f"   导航地图: {nav_output}")
        print(f"   Chunks文件: {chunks_output}")

    except Exception as e:
        print(f"❌ 处理失败: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    main()

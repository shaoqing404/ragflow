# 2025-09-16由少卿组织写入，负责国航MEL文档解析的策略，支持RAGFlow耦合和FTMS服务
# 文件名: extract_link_hierarchy_v2.2.py
# 版本: 2.2 (Claude Spec Compliant)
# 更新时间: 2025-09-12
# 目的: MEL PDF链接层级结构解析器 (适配Claude管理单元)
# 功能:
#   1. 性能优化：采用页级别文本缓存。
#   2. 坐标提取：输出标题坐标。
#   3. 结构化上下文：输出完整的层级、父级、后缀关系 (移除children_codes)。
#   4. 新增 extraction_metadata 和 content_classification 字段。
#   5. 日志汉化

import pypdf
import json
import os
import re
import io
import time
from collections import deque


class MelHierarchyExtractorV2_2:
    """
    Adapts to Claude's proposed standardized management unit output.
    """

    def __init__(self, pdf_path_or_stream):
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

    def _get_text_in_rect(self, page_num, rect):
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
        page_map = {}
        print("信息: 开始提取所有页面的链接...")
        for i, page in enumerate(self.reader.pages):
            page_num = i + 1
            if (i + 1) % 200 == 0:
                print(f"  - 正在处理页面 {page_num}/{len(self.reader.pages)}...")
            if "/Annots" not in page:
                continue

            page_links = []
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
                            page_links.append({"anchor_text": anchor_text, "destination_name": dest_name, "rect": annot_obj["/Rect"], "extraction_metadata": metadata})
                    except Exception:
                        pass
            if page_links:
                page_map[page_num] = page_links
        print("信息: 页面链接提取完成。")
        return page_map

    def _parse_ata_code(self, ata_string):
        if not ata_string:
            return None, None
        match = re.match(r"'''^\s*(\d{2}-\d{2}-\d{2})([A-Z]?)\s*.*'''", ata_string)
        if match:
            base_code = match.group(1)
            suffix = match.group(2) if match.group(2) else None
            return base_code, suffix
        return None, None

    def _traverse_and_collect_leaves(self, page_to_links_map):
        leaves = []
        queue = deque()
        visited_destinations = set()

        for item in self.reader.outline:
            if isinstance(item, pypdf.generic.Destination):
                page_num = self.reader.get_destination_page_number(item)
                if page_num is not None and page_num not in visited_destinations:
                    queue.append((page_num + 1, [item.title]))
                    visited_destinations.add(page_num)

        while queue:
            page_num, path = queue.popleft()
            links_on_page = page_to_links_map.get(page_num, [])

            for link in links_on_page:
                dest_name = link["destination_name"]
                if dest_name in visited_destinations:
                    continue
                visited_destinations.add(dest_name)

                is_leaf = bool(re.search(r"\d{2}-\d{2}-\d{2}-\d{2}", link["anchor_text"])) or bool(re.search(r"\d{2}-\d{2}-\d{2}", link["anchor_text"]))

                if is_leaf:
                    leaves.append({"link": link, "path": path})
                else:
                    dest = self.reader.named_destinations.get(dest_name)
                    if dest:
                        target_page_num = self.reader.get_destination_page_number(dest)
                        if target_page_num is not None:
                            new_path = path + [link["anchor_text"]]
                            queue.append((target_page_num + 1, new_path))
        return leaves

    def _finalize_map(self, leaves):
        for leaf_obj in leaves:
            link = leaf_obj["link"]
            dest = self.reader.named_destinations.get(link["destination_name"])
            leaf_obj["target_page"] = self.reader.get_destination_page_number(dest) + 1 if dest else -1

        valid_leaves = [leaf for leaf in leaves if leaf["target_page"] != -1]
        sorted_leaves = sorted(valid_leaves, key=lambda x: x["target_page"])

        final_map = {}
        for i, leaf_obj in enumerate(sorted_leaves):
            link = leaf_obj["link"]
            start_page = leaf_obj["target_page"]
            end_page = len(self.reader.pages)
            if i + 1 < len(sorted_leaves):
                next_leaf = sorted_leaves[i + 1]
                end_page = next_leaf["target_page"] - 1 if next_leaf["target_page"] > start_page else start_page

            full_anchor_text = link["anchor_text"]
            match = re.match(r"'''^\s*([\d\-]+[A-Z]?)\s*(.*)'''", full_anchor_text)
            ata_code, title_text = (match.group(1).strip(), match.group(2).strip()) if match else (full_anchor_text, "")

            if not ata_code:
                continue

            # 根据PDF大纲路径确定上下文前缀 (MEL/CDL) - V2 (更健壮)
            context_prefix = "UNK"
            path_list = leaf_obj.get("path", [])
            for item in path_list:
                if "CDL" in item:
                    context_prefix = "CDL"
                    break
                elif "MEL" in item:
                    context_prefix = "MEL"
                    break

            if context_prefix == "UNK":
                # This block should now ideally not be hit.
                # -----[GEMINI-DIAGNOSTIC-START]-----
                print(f"[诊断] 上下文检测失败，路径: {path_list}, ATA: {ata_code}")
                # -----[GEMINI-DIAGNOSTIC-END]-------

            final_key = f"{context_prefix}-{ata_code}"

            base_code, suffix = self._parse_ata_code(ata_code)
            path_titles = leaf_obj["path"] + [full_anchor_text]
            path_codes = [self._parse_ata_code(p)[0] for p in path_titles if self._parse_ata_code(p)[0]]
            parent_code = path_codes[-2] if len(path_codes) > 1 else None

            rect = link["rect"]
            coords = {
                "title_rect": {"x0": float(rect[0]), "y0": float(rect[1]), "x1": float(rect[2]), "y1": float(rect[3])},
                "content_start_rect": {"x0": float(rect[0]), "y0": float(rect[3]), "x1": 520.0, "y1": float(rect[3]) + 20},
            }

            final_map[final_key] = {
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
            }
        return final_map

    def save_map_to_json(self, navigation_map, output_path):
        print(f"信息: 正在保存地图到: {output_path}")
        try:
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(navigation_map, f, indent=2, ensure_ascii=False)
            print("信息: 保存成功！")
        except Exception as e:
            print(f"错误: 保存JSON文件失败: {e}")


def main():
    print("--- 运行 MEL 层级提取 V2.2 (Claude规约版) ---")
    try:
        script_dir = os.path.dirname(__file__)
        pdf_path = os.path.abspath(os.path.join(script_dir, "../data/B787-9MEL.pdf"))
        output_path = os.path.abspath(os.path.join(script_dir, "navigation_map_v2.2.json"))

        if not os.path.exists(pdf_path):
            print(f"致命错误: 未找到PDF文件于 {pdf_path}")
            return

        extractor = MelHierarchyExtractorV2_2(pdf_path)
        final_map = extractor.build_map()

        if final_map:
            extractor.save_map_to_json(final_map, output_path)
            print(f"\n成功: 最终地图已保存到 {output_path}")
        else:
            print("\n失败: 最终地图为空。")

    except Exception as e:
        import traceback

        print(f"\n致命错误: 发生未知异常: {e}")
        traceback.print_exc()


if __name__ == "__main__":
    main()

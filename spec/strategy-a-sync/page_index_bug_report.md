# PDF Parser 页码索引越界问题分析

## 问题描述

在将 3U MEL 代码从 `feature/strategy-a-sync` 分支迁移到 RAGFlow v0.23.1 后，出现了 `IndexError: index 318 is out of bounds for axis 0 with size 3/4` 错误，导致所有 chunk 块无法被正常处理（去重环节收到 0 个 chunks）。

## 错误堆栈

```
2026-01-27 12:02:28,064 ERROR    41022 [3U_MEL ERROR] Failed to parse target '21-01-04-03'. Reason: index 318 is out of bounds for axis 0 with size 4
Traceback (most recent call last):
  File "/mnt/e/element_workspace/ragflow/rag/app/three_u_mel_navigation_parser.py", line 663, in _chunk_impl
    ocr_sections, _, pdf_parser = parser_entry(...)
  File "/mnt/e/element_workspace/ragflow/rag/app/naive.py", line 61, in by_deepdoc
    sections, tables = pdf_parser(filename if not binary else binary, from_page=from_page, to_page=to_page, callback=callback)
  File "/mnt/e/element_workspace/ragflow/rag/app/naive.py", line 636, in __call__
    tbls = self._extract_table_figure(True, zoomin, True, True)
  File "/mnt/e/element_workspace/ragflow/deepdoc/parser/pdf_parser.py", line 1164, in _extract_table_figure
    res.append((cropout(bxs, "table", poss), self.tbl_det.construct_table(bxs, html=return_html, is_english=self.is_english)))
  File "/mnt/e/element_workspace/ragflow/deepdoc/parser/pdf_parser.py", line 1130, in cropout
    imgs = [cropout(arr, ltype, poss) for p, arr in pn]
  File "/mnt/e/element_workspace/ragflow/deepdoc/parser/pdf_parser.py", line 1109, in cropout
    ht = self.page_cum_height[pn]
         ~~~~~~~~~~~~~~~~~~~~^^^^
IndexError: index 318 is out of bounds for axis 0 with size 4
```

## 问题根因

在 `deepdoc/parser/pdf_parser.py` 的 `_extract_table_figure` 方法中，嵌套函数 `cropout` 存在页码索引计算错误：

### 问题代码（L1106 和 L1125）

```python
# L1106 - 单页表格裁剪
def cropout(bxs, ltype, poss):
    nonlocal ZM
    pn = set([b["page_number"] - 1 for b in bxs])  # ❌ 使用绝对页码
    if len(pn) < 2:
        pn = list(pn)[0]
        ht = self.page_cum_height[pn]  # ❌ 数组越界！
        ...

# L1125 - 跨页表格裁剪
pn = {}
for b in bxs:
    p = b["page_number"] - 1  # ❌ 使用绝对页码
    if p not in pn:
        pn[p] = []
    pn[p].append(b)
```

### 问题分析

1. **上下文**：3U MEL 分段处理 PDF 文档，每次只处理部分页面（例如 page_from=315, page_to=319，共 4 页）
2. **page_cum_height 数组**：只包含当前处理页面范围的累积高度，大小为 `len(page_images) + 1`（例如 4+1=5）
3. **page_number 字段**：box 对象中的 `page_number` 是**文档的绝对页码**（例如 318）
4. **错误原因**：代码直接使用 `page_number - 1` (317) 作为索引，但 `page_cum_height` 数组只有 5 个元素（索引 0-4）

## 修复方案

需要将绝对页码转换为相对于 `page_from` 的索引：

```python
# L1106 修复 ✅
pn = set([b["page_number"] - 1 - self.page_from for b in bxs])

# L1125 修复 ✅
p = b["page_number"] - 1 - self.page_from
```

## 需要确认的问题

**请帮我检查 `feature/strategy-a-sync` 分支的 `deepdoc/parser/pdf_parser.py` 文件：**

1. **是否存在相同的 bug**？
   - 在 `_extract_table_figure` 方法的 `cropout` 函数中（大约 L1106 和 L1125 附近）
   - 是否有 `- self.page_from` 的偏移量计算？

2. **如果源分支没有这个 bug，是什么时候修复的**？
   - 是否有相关的 commit 记录？
   - 修复的具体代码是什么？

3. **是否与 Python 版本升级有关**？
   - 源分支使用 Python 3.10
   - 目标环境使用 Python 3.12
   - 是否有 numpy 或其他依赖库的行为变化导致这个问题暴露？

4. **3U MEL 的分段处理逻辑**：
   - 源分支如何处理 `page_from` 和 `page_to` 参数？
   - 是否有特殊的页码转换逻辑？

## 环境信息

- **源分支**: `feature/strategy-a-sync` (Python 3.10, trio)
- **目标环境**: RAGFlow v0.23.1 (Python 3.12, asyncio)
- **受影响文件**: `deepdoc/parser/pdf_parser.py`
- **受影响方法**: `_extract_table_figure` -> `cropout` (嵌套函数)

## 补充说明

这个问题导致 3U MEL 解析流程中的所有 chunk 都无法生成（去重前 0 个 chunks），因为表格提取阶段就已经抛出异常。修复后应该能够正常生成 chunks。

# Task: Fix DeepDoc Compatibility Issue with 3U MEL Segmented Processing

## Error Type
`IndexError: index -312 is out of bounds for axis 0 with size 3`

## Current Analysis

### Error Stack Trace
```
File "rag/app/naive.py", line 636, in __call__
    tbls = self._extract_table_figure(True, zoomin, True, True)
File "deepdoc/parser/pdf_parser.py", line 1164, in _extract_table_figure
    res.append((cropout(bxs, "figure", poss), [txt]))
File "deepdoc/parser/pdf_parser.py", line 1109, in cropout
    ht = self.page_cum_height[pn]
         ~~~~~~~~~~~~~~~~~~~~^^^^
IndexError: index -312 is out of bounds for axis 0 with size 3
```

### Root Cause Hypothesis

The `cropout` function in `pdf_parser.py` uses **absolute page numbers** from boxes to index into `page_cum_height` array, which only contains entries for the **currently processed page range**.

**Context**:
- 3U MEL parser processes PDF in segments (e.g., page_from=630, to_page=633, only 3-4 pages at a time)
- `page_cum_height` array size = number of pages in current segment (e.g., 4 elements)
- Boxes may contain `page_number` from different segments or absolute page numbers
- Calculation: `pn = b["page_number"] - 1 - self.page_from` 
- When `page_number` is far outside the current range, `pn` becomes negative (e.g., -312)

### Temporary Fix Applied

We added bounds checking to filter out-of-range boxes:

```python
# Filter boxes to only include those within the current page range
valid_bxs = []
for b in bxs:
    page_idx = b["page_number"] - 1 - self.page_from
    if 0 <= page_idx < len(self.page_cum_height):
        valid_bxs.append(b)
    else:
        logging.warning(f"Skipping box with out-of-range page_number: {b['page_number']}")

if not valid_bxs:
    # Return placeholder image
    return Image.new("RGB", (100, 100), (245, 245, 245))
```

This prevents the crash but **may skip valid content**.

## Information You Need to Gather

1. **Understand `page_number` semantics**:
   - Is `b["page_number"]` an absolute PDF page number (1-indexed)?
   - Or is it relative to some base offset?
   - Check where boxes are generated and what `page_number` represents

2. **Understand cross-page table/figure merging logic**:
   - Why do boxes from page 318 appear when processing pages 630-633?
   - Is this intentional (cross-segment references) or a bug?
   - Check the table detection code that populates `bxs`

3. **Check if this is specific to segmented processing**:
   - Does the error occur when `page_from > 0`?
   - Does it work correctly when processing the full PDF (page_from=0)?

4. **Verify the original implementation**:
   - Check the `feature/strategy-a-sync` branch to see if this bug exists there
   - If not, what's different in that implementation?

## Key Files to Investigate

1. **`deepdoc/parser/pdf_parser.py`**:
   - Lines 1104-1156: `cropout` function and caller `_extract_table_figure`
   - Lines 1106, 1125, 1144: Page index calculations
   - Check `self.page_from`, `self.page_cum_height`, `self.page_images` initialization

2. **Where boxes are created**:
   - Search for assignments to `b["page_number"]` 
   - Likely in `Recognizer` or table/figure detection code
   - Check if `page_number` should be adjusted for segmented processing

3. **Caller context**:
   - `rag/app/naive.py`: Line 636, how `pdf_parser` is instantiated
   - Check if `page_from` and `to_page` parameters are correctly passed

## Expected Outcome

After your investigation, please:

1. **Determine if the temporary fix is correct** or if we need a different approach
2. **Identify the proper fix**:
   - Should boxes be filtered by page range earlier in the pipeline?
   - Should `page_number` be normalized at creation time?
   - Should we adjust the merging logic to handle segmented processing?
3. **Test the fix** with both segmented (page_from > 0) and full PDF processing

## 已对齐官方实现的内部修复（简要记录）

为避免分段处理时 DeepDoc 的 `page_number` 语义混乱，已按官方 `origin/main` 的 `cropout` 逻辑（使用相对页 `page_number - 1`）修复 DeepDoc 内部页码一致性：

1. **`deepdoc/vision/layout_recognizer.py`**
   - 改动：layout box 的 `page_number` 从 0‑based 改为 1‑based（`pn` → `pn + 1`；`len(layouts_all_pages)` → `len(layouts_all_pages) + 1`）。
   - 原因：与 OCR 产出的 box（1‑based）一致，避免后续以 `page_number - 1` 索引时越界。

2. **`deepdoc/parser/pdf_parser.py`**
   - 改动：`_extract_table_figure.cropout` 恢复使用 `page_number - 1`（不减 `page_from`），与官方实现一致。
   - 原因：DeepDoc 的内部页码约定为“分段内 1‑based”，`page_from` 只用于对外位置映射。

3. **`deepdoc/parser/pdf_parser.py`**
   - 改动：`_ocr_rotated_tables` 新增 box 时 `page_number` 改为 `page + 1`（不加 `page_from`）。
   - 原因：该处原本混入绝对页码，导致 `page_cum_height` 索引越界。

## Additional Context

- This error only surfaces when `by_three_u` fails to find MinerU and falls back to DeepDoc (`by_deepdoc`)
- The 3U MEL parser intentionally segments PDFs to avoid memory issues
- The error suggests boxes from previous segments are being retained in memory or referenced incorrectly

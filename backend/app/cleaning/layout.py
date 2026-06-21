"""
版面感知清洗器 (LayoutAwareCleaner) - 处理复杂 PDF 版面结构

预留接口，集成 unstructured 或 docling 进行版面分析。
主要功能：
1. 识别多栏布局，按正确阅读顺序排列文本
2. 识别页眉页脚并移除
3. 识别表格、图片、页号等非正文元素

依赖: pip install "unstructured[pdf]" 或 docling
"""

from typing import Any, Dict

from app.cleaning.base import BaseCleaningStrategy, CleaningResult


class LayoutAwareCleaner(BaseCleaningStrategy):
    """
    版面感知清洗：识别多栏/页眉页脚/表格等版面元素。

    目前提供两种后端：
    - unstructured: 使用 unstructured.io 的 partition 函数
    - docling: 使用 IBM Docling 引擎（需要额外安装）
    """

    def process(self, text: str) -> CleaningResult:
        changes: list[Dict[str, Any]] = []
        backend = self.config.get("layout_backend", "unstructured")

        # 目前版面分析需要输入文件路径而非纯文本
        file_path = self.config.get("file_path")
        if not file_path:
            return CleaningResult(
                text=text,
                cleaned=False,
                changes=[{"op": "skip", "detail": "未提供 file_path，版面分析无法执行"}],
                metadata={"backend": backend, "status": "skipped"},
            )

        if backend == "unstructured":
            result, layout_changes = self._clean_with_unstructured(text, file_path)
            changes.extend(layout_changes)
        elif backend == "docling":
            result, layout_changes = self._clean_with_docling(text, file_path)
            changes.extend(layout_changes)
        else:
            return CleaningResult(
                text=text,
                cleaned=False,
                changes=[{"op": "error", "detail": f"不支持的版面分析后端: {backend}"}],
            )

        return CleaningResult(
            text=result,
            cleaned=any(c.get("op") not in ("skip",) for c in changes),
            changes=changes,
            metadata={"backend": backend, "layout_elements_removed": len(changes)},
        )

    def _clean_with_unstructured(self, text: str, file_path: str) -> tuple[str, list]:
        """使用 unstructured 进行版面分析。"""
        changes = []
        try:
            from unstructured.partition.auto import partition

            elements = partition(filename=str(file_path))
            # 过滤掉页眉、页脚、页号等非正文元素
            filtered = []
            skip_types = {"Header", "Footer", "PageNumber", "Image"}
            for el in elements:
                el_type = type(el).__name__
                # 部分版本使用 category 属性
                category = getattr(el, "category", el_type)
                if category not in skip_types:
                    filtered.append(str(el))
                else:
                    changes.append({
                        "op": "remove_layout_element",
                        "detail": f"移除了 {category} 元素",
                    })

            result = "\n\n".join(filtered)
            if result != text:
                changes.append({
                    "op": "layout_reorder",
                    "detail": "按阅读顺序重排了版面内容",
                })
            return result, changes
        except ImportError:
            changes.append({
                "op": "skip",
                "detail": "unstructured 未安装，跳过版面分析。如需启用请: pip install unstructured",
            })
            return text, changes
        except Exception as e:
            changes.append({
                "op": "error",
                "detail": f"unstructured 版面分析异常: {e}",
            })
            return text, changes

    def _clean_with_docling(self, text: str, file_path: str) -> tuple[str, list]:
        """使用 IBM Docling 进行版面分析（预留接口）。"""
        changes = []
        try:
            from docling.document_converter import DocumentConverter

            converter = DocumentConverter()
            doc = converter.convert(str(file_path))
            result = doc.document.export_to_text()
            changes.append({
                "op": "docling_parse",
                "detail": "使用 Docling 完成了版面解析",
            })
            return result, changes
        except ImportError:
            changes.append({
                "op": "skip",
                "detail": "docling 未安装，跳过版面分析。如需启用请: pip install docling",
            })
            return text, changes
        except Exception as e:
            changes.append({
                "op": "error",
                "detail": f"Docling 版面分析异常: {e}",
            })
            return text, changes

"""
PDF 表格版面分块策略 - docling / unstructured

底层原理：
传统字符切分会将表格行切断，破坏表格结构。本策略通过版面分析解决此问题：
1. 使用 unstructured 库对 PDF 进行版面元素检测（标题、段落、表格、图片）
2. 或使用 docling 进行高精度文档解析，还原表格为 Markdown/HTML 格式
3. 将每个版面元素（尤其是完整表格）作为独立分块
4. 对非表格段落使用 RecursiveCharacterTextSplitter 二次切分

注意：unstructured 内部使用 httpx 等异步 HTTP 库，与 FastAPI 事件循环
存在冲突。需在独立线程中执行分区调用，避免 "Event loop is closed" 错误。

适用场景：财务报表、学术论文、含大量表格的 PDF 文档。
"""

import logging
import os
import concurrent.futures
from contextlib import redirect_stderr
from pathlib import Path
from typing import Any, List

from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.services.log_service import emit_log_sync, LogLevel
from app.strategies.base import BaseChunker, ChunkResult

logger = logging.getLogger(__name__)


class PDFTableLayoutChunker(BaseChunker):
    """基于版面分析的 PDF 表格感知分块器。"""

    DEFAULT_CHUNK_SIZE = 800
    DEFAULT_CHUNK_OVERLAP = 50

    def chunk(self, text: str, **kwargs: Any) -> List[ChunkResult]:
        file_path = kwargs.get("file_path")
        chunk_size = int(self.params.get("chunk_size", self.DEFAULT_CHUNK_SIZE))
        chunk_overlap = int(self.params.get("chunk_overlap", self.DEFAULT_CHUNK_OVERLAP))
        use_docling = bool(self.params.get("use_docling", False))
        languages = str(self.params.get("languages", "zh,en"))

        # 解析语言列表供 unstructured 使用
        lang_list = [lang.strip() for lang in languages.split(",") if lang.strip()]

        emit_log_sync(
            f"PDF 表格版面分块: chunk_size={chunk_size}, overlap={chunk_overlap}, use_docling={use_docling}",
            level=LogLevel.INFO,
            source="pdf_table_layout",
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            use_docling=use_docling,
            has_pdf_path=file_path is not None,
        )

        # 若提供 PDF 文件路径，尝试版面分析
        if file_path and Path(file_path).suffix.lower() == ".pdf":
            try:
                emit_log_sync(
                    f"检测到 PDF 文件，执行版面分析: {Path(file_path).name}",
                    level=LogLevel.INFO,
                    source="pdf_table_layout",
                )
                result = self._chunk_pdf(text, file_path, chunk_size, chunk_overlap, use_docling, lang_list)
                emit_log_sync(
                    f"PDF 版面分块完成: {len(result)} 个块",
                    level=LogLevel.DEBUG,
                    source="pdf_table_layout",
                    total_chunks=len(result),
                )
                # PDF 分块器的 char_start/char_end 基于 PDF 提取的文本，
                # 不一定与传入的 text 参数对齐。用 text.find() 重新校准位置。
                for r in result:
                    pos = text.find(r.text)
                    if pos != -1:
                        r.char_start = pos
                        r.char_end = pos + len(r.text)
                    # 未找到则保持原始偏移（由 PDF 元素累积计算）
                return result
            except Exception as e:
                logger.warning("PDF 版面分析失败，回退到文本切分: %s", e)
                emit_log_sync(
                    f"PDF 版面分析失败，回退到文本切分: {e}",
                    level=LogLevel.WARN,
                    source="pdf_table_layout",
                    error=str(e),
                )

        # 回退：对纯文本使用递归字符切分
        emit_log_sync(
            "使用递归字符切分（非 PDF 或回退模式）",
            level=LogLevel.DEBUG,
            source="pdf_table_layout",
        )
        return self._chunk_text(text, chunk_size, chunk_overlap)

    def _chunk_pdf(
        self, text: str, file_path: str, chunk_size: int, chunk_overlap: int, use_docling: bool, lang_list: List[str] | None = None
    ) -> List[ChunkResult]:
        """对 PDF 执行版面感知分块。"""
        elements: List[tuple[str, dict]] = []

        if use_docling:
            elements = self._extract_with_docling(file_path)
        else:
            elements = self._extract_with_unstructured(file_path, lang_list)

        # 版面分析未返回任何元素时，回退到文本切分
        if not elements:
            emit_log_sync(
                "版面分析未返回元素，回退到文本切分",
                level=LogLevel.WARN,
                source="pdf_table_layout",
            )
            return self._chunk_text(text, chunk_size, chunk_overlap)

        results: List[ChunkResult] = []
        char_offset = 0

        for elem_text, elem_meta in elements:
            elem_type = elem_meta.get("type", "text")

            if elem_type == "table":
                # 表格作为独立分块，避免切断
                results.append(
                    ChunkResult(
                        text=elem_text,
                        metadata={"element_type": "table", **elem_meta},
                        char_start=char_offset,
                        char_end=char_offset + len(elem_text),
                    )
                )
                char_offset += len(elem_text) + 1
            else:
                # 非表格元素二次切分
                sub_chunks = self._chunk_text(elem_text, chunk_size, chunk_overlap)
                for sub in sub_chunks:
                    sub.metadata.update({"element_type": elem_type, **elem_meta})
                    sub.char_start = char_offset + sub.char_start
                    sub.char_end = char_offset + sub.char_end
                    results.append(sub)
                char_offset += len(elem_text) + 1

        return results

    def _extract_with_unstructured(self, file_path: str, lang_list: List[str] | None = None) -> List[tuple[str, dict]]:
        """
        使用 unstructured 库提取 PDF 版面元素。

        在独立线程中运行，避免 unstructured 内部 httpx 异步连接
        与 FastAPI 事件循环冲突导致 "Event loop is closed"。

        如果 unstructured 缺少 PDF 推理依赖，自动回退到 pdfplumber 提取。

        Args:
            file_path: PDF 文件路径。
            lang_list: 语言代码列表，如 ["zh-cn", "en"]，用于 langdetect 语言检测，
                      避免 "No languages specified, defaulting to English" 警告。
        """
        try:
            from unstructured.partition.auto import partition

            # 在线程池中执行，完全隔离 FastAPI 的事件循环
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                # langdetect 使用 print() 输出到 stderr，无法通过 logging.setLevel 抑制
                def _run_partition():
                    with open(os.devnull, "w", encoding="utf-8") as devnull:
                        with redirect_stderr(devnull):
                            return partition(filename=file_path, languages=lang_list)

                future = pool.submit(_run_partition)
                try:
                    elements = future.result(timeout=120)
                except concurrent.futures.TimeoutError:
                    emit_log_sync(
                        "unstructured 版面分析超时（120s），回退到 pdfplumber",
                        level=LogLevel.WARN,
                        source="pdf_table_layout",
                    )
                    return self._extract_with_pdfplumber(file_path)

            result = []
            for elem in elements:
                elem_type = type(elem).__name__.lower()
                meta = {"type": "table" if "table" in elem_type else "text"}
                if hasattr(elem, "metadata") and elem.metadata:
                    meta.update(
                        {
                            "page_number": getattr(elem.metadata, "page_number", None),
                        }
                    )
                result.append((str(elem), meta))
            return result

        except ImportError:
            # unstructured 的 PDF 依赖（如 unstructured-inference）未安装
            emit_log_sync(
                "unstructured PDF 依赖不全，回退到 pdfplumber 提取文本",
                level=LogLevel.INFO,
                source="pdf_table_layout",
            )
            return self._extract_with_pdfplumber(file_path)

    def _extract_with_pdfplumber(self, file_path: str) -> List[tuple[str, dict]]:
        """
        使用 pdfplumber 提取 PDF 文本（轻量备选方案）。

        不依赖 unstructured-inference / PyTorch，仅需 pdfplumber 即可。
        每页作为一个元素，保留页面号元数据。
        """
        import pdfplumber

        result = []
        with pdfplumber.open(file_path) as pdf:
            for page_num, page in enumerate(pdf.pages, start=1):
                page_text = page.extract_text() or ""
                if page_text.strip():
                    result.append((
                        page_text,
                        {"type": "text", "page_number": page_num, "parser": "pdfplumber"},
                    ))
        return result

    def _get_page_count(self, file_path: str) -> int:
        """获取 PDF 总页数（使用轻量 pypdf 库）。"""
        try:
            from pypdf import PdfReader
            with open(file_path, "rb") as f:
                reader = PdfReader(f)
                return len(reader.pages)
        except Exception as e:
            logger.warning("获取 PDF 页数失败: %s", e)
            # 回退：返回一个较大的默认值
            return 9999

    def _extract_with_docling(self, file_path: str) -> List[tuple[str, dict]]:
        """
        使用 docling 库进行高精度 PDF 解析。

        修复了以下问题：
        - 使用 PdfFormatOption + PyPdfiumDocumentBackend 替代无效的 pdf_backend kwarg
        - 按页分批处理（page_range）绕过 Docling 内部模型的输入长度限制（"400 input length too long"）
        - 禁用 OCR 以减少模型处理负载，仅保留版面布局和表格结构识别
        - 隔离在独立线程中运行，避免与 FastAPI 事件循环冲突

        策略参数:
            page_batch_size (int): 每批处理的页数，默认 5。PDF 页数多时设小值，少时设大值。
        """
        emit_log_sync(
            "开始 Docling 解析: %s" % Path(file_path).name,
            level=LogLevel.INFO,
            source="pdf_table_layout",
        )

        from docling.document_converter import DocumentConverter, PdfFormatOption
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.datamodel.base_models import InputFormat
        from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend

        # 获取每批页数（策略参数，默认 5 页/批）
        page_batch_size = int(self.params.get("page_batch_size", 5))

        # 配置 PipelineOptions：
        # - do_ocr=False：禁用 OCR，减少模型输入数据量
        # - do_table_structure=True：保留表格结构识别（核心功能）
        pipeline_opts = PdfPipelineOptions(
            do_ocr=False,
            do_table_structure=True,
        )

        # 使用 PdfFormatOption 正确指定 pypdfium2 后端（避免 glyph 路径 bug）
        format_opts = PdfFormatOption(
            pipeline_options=pipeline_opts,
            backend=PyPdfiumDocumentBackend,
        )

        # 获取 PDF 总页数
        total_pages = self._get_page_count(file_path)
        emit_log_sync(
            "Docling 解析: PDF 共 %d 页，每批 %d 页" % (total_pages, page_batch_size),
            level=LogLevel.INFO,
            source="pdf_table_layout",
        )

        # 在线程池隔离执行，避免事件循环冲突
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:

            def _run_docling_pipeline(fp: str) -> List[tuple[str, dict]]:
                """在单个线程中创建 converter 并逐批处理所有页面。"""
                converter = DocumentConverter(
                    format_options={InputFormat.PDF: format_opts},
                )
                results: List[tuple[str, dict]] = []

                for start_page in range(1, total_pages + 1, page_batch_size):
                    end_page = min(start_page + page_batch_size - 1, total_pages)
                    emit_log_sync(
                        "Docling 处理批次: 第 %d-%d 页" % (start_page, end_page),
                        level=LogLevel.DEBUG,
                        source="pdf_table_layout",
                    )

                    try:
                        conv_result = converter.convert(
                            fp,
                            page_range=(start_page, end_page),
                            raises_on_error=False,
                        )
                        doc = getattr(conv_result, "document", None)
                        if doc is not None:
                            md_text = doc.export_to_markdown().strip()
                            if md_text:
                                results.append((
                                    md_text,
                                    {
                                        "type": "text",
                                        "parser": "docling",
                                        "page_range": "%d-%d" % (start_page, end_page),
                                    },
                                ))
                        else:
                            # 批次解析失败，记录日志但继续下一批
                            errors = getattr(conv_result, "errors", [])
                            error_msgs = [e.error_message for e in errors] if errors else ["未知错误"]
                            emit_log_sync(
                                "Docling 第 %d-%d 页解析结果为空: %s" % (start_page, end_page, "; ".join(error_msgs[:2])),
                                level=LogLevel.WARN,
                                source="pdf_table_layout",
                            )
                    except Exception as batch_err:
                        emit_log_sync(
                            "Docling 第 %d-%d 页解析异常: %s" % (start_page, end_page, str(batch_err)[:100]),
                            level=LogLevel.WARN,
                            source="pdf_table_layout",
                        )
                        # 继续处理下一页批

                return results

            future = pool.submit(_run_docling_pipeline, file_path)
            try:
                elements = future.result(timeout=600)  # 大 PDF 总超时放宽到 600s
            except concurrent.futures.TimeoutError:
                emit_log_sync(
                    "Docling 解析超时(600s)，回退到 pdfplumber",
                    level=LogLevel.WARN,
                    source="pdf_table_layout",
                )
                return self._extract_with_pdfplumber(file_path)
            except Exception as e:
                emit_log_sync(
                    "Docling 解析失败: %s，回退到 pdfplumber" % str(e)[:100],
                    level=LogLevel.WARN,
                    source="pdf_table_layout",
                )
                return self._extract_with_pdfplumber(file_path)

        if not elements:
            emit_log_sync(
                "Docling 解析未返回任何元素，回退到 pdfplumber",
                level=LogLevel.WARN,
                source="pdf_table_layout",
            )
            return self._extract_with_pdfplumber(file_path)

        emit_log_sync(
            "Docling 解析完成: %d 个元素" % len(elements),
            level=LogLevel.INFO,
            source="pdf_table_layout",
        )
        return elements

    def _chunk_text(self, text: str, chunk_size: int, chunk_overlap: int) -> List[ChunkResult]:
        """对纯文本执行递归字符切分。"""
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )
        chunks = splitter.split_text(text)
        results: List[ChunkResult] = []
        search_pos = 0

        for chunk_text in chunks:
            start, end = self._find_char_positions(text, chunk_text, search_pos)
            results.append(
                ChunkResult(
                    text=chunk_text,
                    metadata={"chunk_size": chunk_size},
                    char_start=start,
                    char_end=end,
                )
            )
            search_pos = max(0, end - chunk_overlap)

        return results

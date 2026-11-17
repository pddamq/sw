from __future__ import annotations

import asyncio
import io
import json
import logging
import math
import re
from collections import Counter
from html.parser import HTMLParser

from openai import AsyncOpenAI
from .model_endpoint import api_root
from pypdf import PdfReader

from .config import SettingsStore
from .storage import Database

try:
    from docx import Document as DocxDocument
except ImportError:  # Optional during minimal backend tests.
    DocxDocument = None
try:
    from openpyxl import load_workbook
except ImportError:
    load_workbook = None
try:
    from pptx import Presentation
except ImportError:
    Presentation = None
try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None
try:
    from fastembed import TextEmbedding
except ImportError:
    TextEmbedding = None


def document_path(value: str) -> str:
    """Keep directory provenance as a logical name, never as a filesystem target."""
    path = value.replace('\\', '/')
    parts = path.split('/')
    if (not path or len(path) > 4096 or len(parts) > 64
            or any(part in ('', '.', '..') or len(part) > 255 for part in parts)
            or ':' in path or any(ord(char) < 32 or ord(char) == 127 for char in path)):
        raise ValueError('文件相对路径无效：请保留文件夹层级，避免绝对路径或上级目录。')
    return path


def terms(text: str) -> list[str]:
    latin = re.findall(r'[a-z_][a-z_0-9]*|\d+(?:\.\d+)?', text.lower())
    # Character bigrams allow Chinese retrieval without a heavyweight tokenizer.
    for group in re.findall(r'[\u3400-\u9fff]+', text):
        latin.extend(group[i:i + 2] for i in range(max(1, len(group) - 1)))
    return latin


def split_text(text: str, size: int = 1000, overlap: int = 150) -> list[str]:
    """Backward-compatible character splitter used by older callers."""
    text = text.replace('\x00', '').strip()
    return [text[i:i + size] for i in range(0, len(text), size - overlap) if text[i:i + size].strip()]


def _clean(value: str) -> str:
    value = value.replace('\u00a0', ' ')
    value = re.sub(r'[ \t]+', ' ', value)
    value = re.sub(r'\n{3,}', '\n\n', value)
    return value.strip()


def split_structured(blocks: list[dict], size: int = 1200, overlap: int = 180) -> tuple[list[str], list[dict]]:
    """Split by document structure first, then use bounded overlap inside long sections."""
    chunks: list[str] = []
    metadata: list[dict] = []
    for block in blocks:
        text = _clean(str(block.get('text', '')))
        if not text:
            continue
        section = str(block.get('section') or '')
        page = block.get('page')
        paragraphs = [p.strip() for p in re.split(r'\n\s*\n|(?<=[。！？.!?])\s+(?=[A-Z\u3400-\u9fff])', text) if p.strip()]
        if not paragraphs:
            paragraphs = [text]
        current = ''
        for paragraph in paragraphs:
            candidate = f'{current}\n{paragraph}'.strip() if current else paragraph
            if current and len(candidate) > size:
                chunks.append(current)
                metadata.append({'page': page, 'section': section})
                tail = current[-overlap:].lstrip()
                current = f'{tail}\n{paragraph}'.strip() if tail else paragraph
            else:
                current = candidate
        if current:
            chunks.append(current)
            metadata.append({'page': page, 'section': section})
    return chunks, metadata


class HtmlText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.skip += 1
        elif tag in ('p', 'div', 'br', 'li', 'h1', 'h2', 'h3'):
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in ('script', 'style') and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def extract_blocks(name: str, content: bytes) -> list[dict]:
    suffix = name.rsplit('.', 1)[-1].lower()
    if suffix == 'pdf':
        reader = PdfReader(io.BytesIO(content))
        if reader.is_encrypted:
            raise ValueError('请先移除 PDF 密码保护。')
        if len(reader.pages) > 500:
            raise ValueError('单个 PDF 最多导入 500 页。')
        return [{'text': page.extract_text() or '', 'page': index + 1, 'section': ''}
                for index, page in enumerate(reader.pages)]
    if suffix in ('docx',):
        if DocxDocument is None:
            raise ValueError('DOCX 解析依赖未安装，请重新安装知识库版本依赖。')
        document = DocxDocument(io.BytesIO(content))
        blocks: list[dict] = []
        section = ''
        for paragraph in document.paragraphs:
            text = paragraph.text.strip()
            if not text:
                continue
            style = paragraph.style.name.lower() if paragraph.style else ''
            if 'heading' in style or re.match(r'^(第[一二三四五六七八九十0-9]+[章节部分]|\d+(?:\.\d+)*[ .])', text):
                section = text
            blocks.append({'text': text, 'section': section})
        for table_index, table in enumerate(document.tables, 1):
            rows = [' | '.join(cell.text.strip() for cell in row.cells) for row in table.rows]
            blocks.append({'text': f'表格 {table_index}\n' + '\n'.join(rows), 'section': section})
        return blocks
    if suffix in ('xlsx', 'xlsm'):
        if load_workbook is None:
            raise ValueError('XLSX 解析依赖未安装，请重新安装知识库版本依赖。')
        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        blocks = []
        for sheet in workbook.worksheets:
            rows = []
            for row in sheet.iter_rows(values_only=True):
                values = [str(value).strip() for value in row if value is not None and str(value).strip()]
                if values:
                    rows.append(' | '.join(values))
            if rows:
                blocks.append({'text': '\n'.join(rows), 'section': f'工作表：{sheet.title}'})
        return blocks
    if suffix in ('pptx',):
        if Presentation is None:
            raise ValueError('PPTX 解析依赖未安装，请重新安装知识库版本依赖。')
        presentation = Presentation(io.BytesIO(content))
        blocks = []
        for index, slide in enumerate(presentation.slides, 1):
            text = '\n'.join(shape.text.strip() for shape in slide.shapes
                             if hasattr(shape, 'text') and shape.text.strip())
            if text:
                blocks.append({'text': text, 'page': index, 'section': f'幻灯片 {index}'})
        return blocks
    if suffix not in ('txt', 'md', 'html', 'htm', 'csv', 'json', 'log', 'xml', 'yaml', 'yml'):
        raise ValueError('支持 TXT、Markdown、HTML、CSV、JSON、XML、YAML、DOCX、XLSX、PPTX 和文本型 PDF。')
    try:
        text = content.decode('utf-8-sig')
    except UnicodeDecodeError:
        text = content.decode('gb18030')
    if suffix in ('html', 'htm'):
        if BeautifulSoup is not None:
            text = BeautifulSoup(text, 'lxml').get_text('\n')
        else:
            parser = HtmlText()
            parser.feed(text)
            text = '\n'.join(parser.parts)
    blocks = []
    section = ''
    for paragraph in re.split(r'\n\s*\n', text):
        stripped = _clean(paragraph)
        if not stripped:
            continue
        heading = re.match(r'^(#{1,6})\s+(.+?)(?:\n|$)', stripped)
        if heading:
            section = heading.group(2).strip()
        blocks.append({'text': stripped, 'section': section})
    return blocks


def extract(name: str, content: bytes) -> str:
    return '\n\n'.join(str(block.get('text', '')) for block in extract_blocks(name, content))


class KnowledgeBase:
    DEFAULT_LOCAL_MODEL = 'BAAI/bge-small-zh-v1.5'

    def __init__(self, db: Database, config: SettingsStore):
        self.db, self.config = db, config
        self._local_model = None

    def _model_name(self) -> str:
        configured = self.config.value.embedding_model.strip()
        return configured.removeprefix('local:') if configured.startswith('local:') else ''

    def embedding_identity(self) -> str:
        s = self.config.value
        if s.embedding_model.startswith('local:'):
            return f'local:{self._model_name()}'
        return f'{s.embedding_base_url or api_root(s)}|{s.embedding_model}' if s.embedding_model else ''

    async def embed(self, texts: list[str]) -> list[list[float]]:
        settings = self.config.value
        if settings.embedding_model.startswith('local:'):
            if TextEmbedding is None:
                raise ValueError('本地向量依赖未安装，请重新安装知识库版本依赖。')
            model_name = self._model_name() or self.DEFAULT_LOCAL_MODEL
            if self._local_model is None:
                self._local_model = await asyncio.to_thread(TextEmbedding, model_name=model_name)
            vectors = await asyncio.to_thread(lambda: list(self._local_model.embed(texts, batch_size=32)))
            return [list(map(float, vector)) for vector in vectors]
        async with AsyncOpenAI(api_key=self.config.key(), base_url=settings.embedding_base_url or api_root(settings),
                               timeout=60, max_retries=1) as client:
            vectors = []
            for i in range(0, len(texts), 32):
                result = await client.embeddings.create(model=settings.embedding_model, input=texts[i:i + 32])
                vectors.extend(item.embedding for item in sorted(result.data, key=lambda item: item.index))
            return vectors

    @staticmethod
    def _validate_vectors(vectors: list[list[float]], expected: int) -> None:
        if len(vectors) != expected or not vectors or not vectors[0]:
            raise ValueError('Embedding 向量数量或维度无效。')
        dimension = len(vectors[0])
        if any(len(vector) != dimension or not all(math.isfinite(x) for x in vector) for vector in vectors):
            raise ValueError('Embedding 向量包含无效数值或维度不一致。')

    async def ingest(self, name: str, content: bytes) -> dict:
        blocks = await asyncio.to_thread(extract_blocks, name, content)
        chunks, metadata = split_structured(blocks)
        if not chunks:
            raise ValueError('文档没有可提取的文字；扫描 PDF 需先进行 OCR。')
        if len(chunks) > 3000:
            raise ValueError('文档文字过多，请拆分后导入。')
        vectors = None
        if self.config.value.embedding_model:
            if not self.config.value.embedding_model.startswith('local:') and not self.config.key():
                raise ValueError('使用远程向量索引前请先配置 API Key；或选择本地向量模型。')
            vectors = await self.embed(chunks)
            self._validate_vectors(vectors, len(chunks))
        return self.db.add_document(name, chunks, vectors, self.embedding_identity(), metadata)

    @staticmethod
    def _query_terms(query: str) -> set[str]:
        return set(terms(query))

    def _lexical_scores(self, query: str, rows: list[dict]) -> list[tuple[int, float]]:
        tokenized = [Counter(terms(row['text'])) for row in rows]
        query_terms = self._query_terms(query)
        if not query_terms:
            return []
        avg_len = sum(sum(tokens.values()) for tokens in tokenized) / max(len(rows), 1)
        frequency = Counter(token for document in tokenized for token in document)
        scored = []
        for index, document in enumerate(tokenized):
            length = sum(document.values())
            score = 0.0
            for token in query_terms:
                tf = document.get(token, 0)
                if not tf:
                    continue
                inverse = math.log(1 + (len(rows) - frequency[token] + 0.5) / (frequency[token] + 0.5))
                score += inverse * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * length / max(avg_len, 1)))
            # Exact phrase and title/section matches are strong precision signals.
            lowered = document and rows[index]['text'].lower()
            q = query.strip().lower()
            if q and q in lowered:
                score += 2.5
            if q and q in str(rows[index].get('name', '')).lower():
                score += 1.5
            if score > 0:
                scored.append((index, score))
        return sorted(scored, key=lambda item: item[1], reverse=True)

    async def search(self, query: str, limit: int = 5, report: dict | None = None) -> list[dict]:
        report = report if report is not None else {}
        report.update(mode='bm25', warning=None, candidates=0)
        if not query.strip():
            return []
        limit = min(max(limit, 1), 20)
        rows = self.db.chunks()
        if not rows:
            return []
        by_id = {row['id']: index for index, row in enumerate(rows)}
        lexical = self._lexical_scores(query, rows)
        rank_scores: dict[int, float] = {}
        for rank, (index, score) in enumerate(lexical[:80], 1):
            rank_scores[index] = rank_scores.get(index, 0.0) + 1 / (60 + rank) + min(score, 8) / 100
        # FTS5 supplements the Chinese bigram tokenizer with phrase-aware Latin search.
        fts_query = ' OR '.join('"' + token.replace('"', '') + '"' for token in query.split() if token.strip())
        if fts_query:
            try:
                for rank, candidate in enumerate(self.db.fts_candidates(fts_query, 80), 1):
                    index = by_id.get(candidate['id'])
                    if index is not None:
                        rank_scores[index] = rank_scores.get(index, 0.0) + 1 / (60 + rank)
            except Exception as exc:
                logging.getLogger(__name__).debug('FTS candidate fallback: %s', type(exc).__name__)
        identity = self.embedding_identity()
        vector_rows = [(index, row) for index, row in enumerate(rows)
                       if identity and row['embedding_model'] == identity and row['embedding']]
        if vector_rows:
            try:
                embedded = await self.embed([query])
                self._validate_vectors(embedded, 1)
                vector = embedded[0]
                norm = math.sqrt(sum(x * x for x in vector)) or 1
                semantic = []
                indexed = {row['id']: (index, row) for index, row in vector_rows}
                candidates = self.db.vector_candidates(vector, 80)
                if candidates:
                    for candidate in candidates:
                        item = indexed.get(candidate['id'])
                        if item:
                            index, _ = item
                            similarity = 1.0 / (1.0 + max(candidate['distance'], 0.0))
                            semantic.append((index, similarity))
                    report['vector_index'] = 'sqlite-vec'
                else:
                    # JSON fallback keeps old databases and remote dimensions portable.
                    for index, row in vector_rows:
                        try:
                            stored = json.loads(row['embedding'])
                            if len(stored) != len(vector) or not all(math.isfinite(x) for x in stored):
                                continue
                            denom = norm * (math.sqrt(sum(x * x for x in stored)) or 1)
                            similarity = sum(x * y for x, y in zip(vector, stored)) / denom
                            semantic.append((index, similarity))
                        except (ValueError, TypeError, json.JSONDecodeError):
                            continue
                semantic.sort(key=lambda item: item[1], reverse=True)
                for rank, (index, similarity) in enumerate(semantic[:80], 1):
                    if similarity > 0.15:
                        rank_scores[index] = rank_scores.get(index, 0.0) + 1 / (60 + rank) + max(similarity, 0) * 0.45
                if semantic:
                    report['mode'] = 'hybrid'
            except Exception as exc:
                logging.getLogger(__name__).warning('Embedding search fallback (%s)', type(exc).__name__)
                report['warning'] = '本地向量检索暂不可用，本次已保留关键词检索结果。'
        report['candidates'] = len(rank_scores)
        ordered = sorted(rank_scores.items(), key=lambda pair: pair[1], reverse=True)[:limit]
        return [self._result(rows[index], score) for index, score in ordered]

    @staticmethod
    def _result(row: dict, score: float) -> dict:
        result = {'name': row['name'], 'text': row['text'], 'score': round(score, 5),
                  'document_id': row['document_id'], 'chunk_id': row['id']}
        if row.get('page') is not None:
            result['page'] = row['page']
        if row.get('section'):
            result['section'] = row['section']
        return result

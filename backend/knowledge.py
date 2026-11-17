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
    text = text.replace('\x00', '').strip()
    return [text[i:i + size] for i in range(0, len(text), size - overlap) if text[i:i + size].strip()]


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


def extract(name: str, content: bytes) -> str:
    suffix = name.rsplit('.', 1)[-1].lower()
    if suffix == 'pdf':
        reader = PdfReader(io.BytesIO(content))
        if reader.is_encrypted:
            raise ValueError('请先移除 PDF 密码保护。')
        if len(reader.pages) > 500:
            raise ValueError('单个 PDF 最多导入 500 页。')
        return '\n'.join(page.extract_text() or '' for page in reader.pages)
    if suffix not in ('txt', 'md', 'html', 'htm'):
        raise ValueError('支持 TXT、Markdown、HTML 和文本型 PDF。')
    try:
        text = content.decode('utf-8-sig')
    except UnicodeDecodeError:
        text = content.decode('gb18030')
    if suffix in ('html', 'htm'):
        parser = HtmlText()
        parser.feed(text)
        return '\n'.join(parser.parts)
    return text


class KnowledgeBase:
    def __init__(self, db: Database, config: SettingsStore):
        self.db, self.config = db, config

    def embedding_identity(self) -> str:
        s = self.config.value
        return f'{s.embedding_base_url or api_root(s)}|{s.embedding_model}' if s.embedding_model else ''

    async def embed(self, texts: list[str]) -> list[list[float]]:
        settings = self.config.value
        async with AsyncOpenAI(api_key=self.config.key(), base_url=settings.embedding_base_url or api_root(settings),
                               timeout=60, max_retries=1) as client:
            vectors = []
            for i in range(0, len(texts), 32):
                result = await client.embeddings.create(model=settings.embedding_model, input=texts[i:i + 32])
                vectors.extend(item.embedding for item in sorted(result.data, key=lambda item: item.index))
            return vectors

    async def ingest(self, name: str, content: bytes) -> dict:
        text = await asyncio.to_thread(extract, name, content)
        chunks = split_text(text)
        if not chunks:
            raise ValueError('文档没有可提取的文字；扫描 PDF 需先进行 OCR。')
        if len(chunks) > 3000:
            raise ValueError('文档文字过多，请拆分后导入。')
        vectors = None
        if self.config.value.embedding_model:
            if not self.config.key():
                raise ValueError('使用向量索引前请先配置 API Key。')
            vectors = await self.embed(chunks)
            if len(vectors) != len(chunks):
                raise ValueError('Embedding API 返回的向量数量与分块数量不一致。')
        if vectors is not None:
            if len(vectors) != len(chunks) or not vectors or not vectors[0]:
                raise ValueError('Embedding 向量数量或维度无效。')
            dimension = len(vectors[0])
            if any(len(v) != dimension or not all(math.isfinite(x) for x in v) for v in vectors):
                raise ValueError('Embedding 向量包含无效数值或维度不一致。')
        return self.db.add_document(name, chunks, vectors, self.embedding_identity())

    async def search(self, query: str, limit: int = 5, report: dict | None = None) -> list[dict]:
        report = report if report is not None else {}
        report.update(mode='bm25', warning=None)
        if not query.strip():
            return []
        limit = min(max(limit, 1), 20)
        rows = self.db.chunks()
        if not rows:
            return []
        tokenized = [Counter(terms(r['text'])) for r in rows]
        query_terms = set(terms(query))
        avg_len = sum(sum(t.values()) for t in tokenized) / max(len(rows), 1)
        frequency = Counter(t for document in tokenized for t in document)
        lexical = []
        for i, document in enumerate(tokenized):
            length = sum(document.values())
            score = 0.0
            for token in query_terms:
                tf = document.get(token, 0)
                inverse = math.log(1 + (len(rows) - frequency[token] + 0.5) / (frequency[token] + 0.5))
                score += inverse * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * length / max(avg_len, 1)))
            if score > 0:
                lexical.append((i, score))
        lexical.sort(key=lambda item: item[1], reverse=True)
        ranks = {index: 1 / (60 + rank) for rank, (index, _) in enumerate(lexical[:30], 1)}
        identity = self.embedding_identity()
        vector_rows = [(i, r) for i, r in enumerate(rows) if identity and r['embedding_model'] == identity and r['embedding']]
        if vector_rows:
            try:
                embedded = await self.embed([query])
                if len(embedded) != 1 or not embedded[0] or not all(math.isfinite(x) for x in embedded[0]):
                    raise ValueError('Invalid query vector')
                vector = embedded[0]
                norm = math.sqrt(sum(x * x for x in vector)) or 1
                semantic = []
                usable = 0
                for index, row in vector_rows:
                    try:
                        stored = json.loads(row['embedding'])
                        if len(stored) != len(vector) or not all(math.isfinite(x) for x in stored):
                            continue
                        usable += 1
                        denom = norm * (math.sqrt(sum(x * x for x in stored)) or 1)
                        similarity = sum(x * y for x, y in zip(vector, stored)) / denom
                        if similarity > 0.2:
                            semantic.append((index, similarity))
                    except (ValueError, TypeError):
                        continue
                if not usable:
                    raise ValueError('No compatible document vectors')
                semantic.sort(key=lambda item: item[1], reverse=True)
                for rank, (index, _) in enumerate(semantic[:30], 1):
                    ranks[index] = ranks.get(index, 0) + 1 / (60 + rank)
                report['mode'] = 'hybrid'
            except Exception as exc:
                logging.getLogger(__name__).warning('Embedding search fallback (%s)', type(exc).__name__)
                report['warning'] = '向量检索暂不可用，本次已保留 BM25 关键词检索结果。'
        return [{'name': rows[i]['name'], 'text': rows[i]['text'], 'score': round(score, 5),
                 'document_id': rows[i]['document_id'], 'chunk_id': rows[i]['id']} for i, score in sorted(ranks.items(), key=lambda p: p[1], reverse=True)[:limit]]

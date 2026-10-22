"""Local retrieval primitives; no external dependency for EPUB/TXT/MD/BM25."""
import hashlib
import ipaddress
import json
import math
import os
import re
import sqlite3
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from collections import Counter
from urllib.parse import urlparse

SCHEMA_VERSION = 2
DEFAULT_OLLAMA = 'http://127.0.0.1:11434'
# 允许用环境变量指向自建的局域网 Ollama（例如另一台机器上的 bge-m3）。
OLLAMA = os.environ.get('AI_READING_OLLAMA') or DEFAULT_OLLAMA
# Tailscale/CGNAT 网段被 Python 视为公网，但在本场景属于自有网络。
CGNAT = ipaddress.ip_network('100.64.0.0/10')


def ollama_base(explicit=None):
    """决定向量服务端点：显式参数 > 环境变量 > 本机默认。

    只接受 http(s) 且落在本机、局域网或 Tailscale 网段的地址，
    避免把书籍内容发到公网服务。
    """
    raw = ((explicit or '').strip() or (OLLAMA or DEFAULT_OLLAMA)).strip().rstrip('/')
    parts = urlparse(raw)
    if parts.scheme not in ('http', 'https') or not parts.hostname:
        raise ValueError(f'向量服务地址无效：{raw}；应形如 http://127.0.0.1:11434')
    try:
        ip = ipaddress.ip_address(parts.hostname)
    except ValueError:
        return raw  # 主机名交由系统解析，不做 DNS 探测
    if not (ip.is_loopback or ip.is_private or ip.is_link_local or ip in CGNAT):
        raise ValueError(f'只支持本机或局域网自建的 Ollama：{raw}；不向公网发送材料')
    return raw


def fingerprint(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


@contextmanager
def connect_read(path):
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        raise ValueError(f'索引不存在：{p}')
    conn = sqlite3.connect(p.as_uri() + '?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def load_book(path):
    with connect_read(path) as c:
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'chunks' not in tables:
            raise ValueError('不支持的索引；请用 index_book.py 从原文件重建')
        cols = {r[1] for r in c.execute('PRAGMA table_info(chunks)')}
        rows = [dict(r) for r in c.execute('SELECT * FROM chunks ORDER BY chunk_id')]
        meta = dict(c.execute('SELECT key,value FROM metadata')) if 'metadata' in tables else {}
        for r in rows:
            r.setdefault('chapter_id', r.get('chapter', 'legacy'))
            r.setdefault('locator', f"段落 {r['para_id']}")
        if not meta:
            sidecar = Path(path).with_name(Path(path).stem + '_meta.json')
            if sidecar.exists():
                meta = json.loads(sidecar.read_text(encoding='utf-8'))
            meta['legacy'] = True
        return rows, meta


def check_source(meta):
    source = meta.get('source_path')
    if not source:
        return 'legacy_index'
    if not Path(source).is_file():
        return 'source_unavailable'
    if fingerprint(source) != meta.get('source_sha256'):
        raise ValueError('原文件已改变，当前索引过期；请用 --replace 重建并创建新的学习记录')
    return 'verified'


def tokens(text):
    result = re.findall(r'[a-z0-9]+', text.lower())
    for run in re.findall(r'[\u3400-\u9fff]+', text):
        result.extend(run[i:i+2] for i in range(len(run)-1))
        result.extend(run)  # supports single-character Chinese queries too
    return result


def bm25(query, rows):
    counts = [Counter(tokens(r['content'])) for r in rows]
    lengths = [sum(c.values()) for c in counts]
    avg = sum(lengths) / len(rows) if rows else 0
    if not avg:
        return []
    df = Counter()
    for count in counts:
        df.update(count.keys())
    scores = []
    for row, count, length in zip(rows, counts, lengths):
        score = 0.0
        for term in set(tokens(query)):
            if not count[term]:
                continue
            idf = math.log(1 + (len(rows)-df[term]+.5)/(df[term]+.5))
            score += idf * count[term]*2.5/(count[term]+1.5*(.25+.75*length/avg))
        if score > 0:
            scores.append((row['chunk_id'], score))
    return sorted(scores, key=lambda x: (-x[1], x[0]))


def embed(texts, model='bge-m3', timeout=30, base=None):
    endpoint = ollama_base(base)
    req = urllib.request.Request(endpoint + '/api/embed', data=json.dumps(
        {'model': model, 'input': texts, 'truncate': False}).encode(),
        headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        data = json.load(response)
    vectors = data.get('embeddings', [])
    if len(vectors) != len(texts):
        raise ValueError('向量服务返回数量不符')
    dim = len(vectors[0]) if vectors else 0
    for v in vectors:
        if not dim or len(v) != dim or any(not isinstance(x, (int,float)) or not math.isfinite(x) for x in v):
            raise ValueError('向量无效')
        norm = math.sqrt(sum(x*x for x in v))
        if not norm:
            raise ValueError('零向量无效')
    return [[x / math.sqrt(sum(y*y for y in v)) for x in v] for v in vectors]


def cosine(a, b):
    if len(a) != len(b):
        raise ValueError('向量维度不一致，请重建向量')
    return sum(x*y for x,y in zip(a,b)) / (math.sqrt(sum(x*x for x in a))*math.sqrt(sum(x*x for x in b)))


def emit(data):
    print(json.dumps(data, ensure_ascii=False, indent=2))

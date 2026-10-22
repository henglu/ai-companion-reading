"""Local retrieval primitives; no external dependency for EPUB/TXT/MD/BM25."""
import hashlib
import ipaddress
import json
import math
import os
import re
import sqlite3
import http.client
import socket
import ssl
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


def allowed_address(value):
    ip = ipaddress.ip_address(value.split('%')[0])
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    networks = ('127.0.0.0/8', '10.0.0.0/8', '172.16.0.0/12',
                '192.168.0.0/16', '169.254.0.0/16', '100.64.0.0/10',
                '::1/128', 'fc00::/7', 'fe80::/10')
    return any(ip in ipaddress.ip_network(n) for n in networks)


def resolve_endpoint(raw):
    parts = urlparse(raw)
    if (parts.scheme not in ('http', 'https') or not parts.hostname
            or parts.username is not None or parts.password is not None
            or parts.query or parts.fragment or parts.path not in ('', '/')):
        raise ValueError('向量地址应为 http(s)://主机:端口，不含账号、路径或查询参数')
    try:
        port = parts.port or (443 if parts.scheme == 'https' else 80)
        addresses = socket.getaddrinfo(parts.hostname, port, type=socket.SOCK_STREAM)
    except (OSError, ValueError) as exc:
        raise ValueError('无法解析向量服务地址；请检查主机名、端口或使用内网 IP。BM25 仍可使用') from exc
    if not addresses or any(not allowed_address(item[4][0]) for item in addresses):
        raise ValueError('向量服务必须全部解析到本机、内网或 Tailscale 地址；已阻止发送材料')
    return parts, addresses


def ollama_base(explicit=None):
    raw = ((explicit or '').strip() or (OLLAMA or DEFAULT_OLLAMA)).strip().rstrip('/')
    resolve_endpoint(raw)
    return raw


def embedding_response(endpoint, payload, timeout):
    # Resolve once, validate every answer, and connect to that exact sockaddr.
    # No proxies, redirects or a second DNS lookup can change the destination.
    parts, addresses = resolve_endpoint(endpoint)
    conn = http.client.HTTPConnection(parts.hostname, parts.port or
                                      (443 if parts.scheme == 'https' else 80), timeout=timeout)
    family, kind, proto, _, sockaddr = addresses[0]
    sock = socket.socket(family, kind, proto)
    try:
        sock.settimeout(timeout)
        sock.connect(sockaddr)
        if parts.scheme == 'https':
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=parts.hostname)
        conn.sock = sock
        conn.request('POST', '/api/embed', body=payload,
                     headers={'Content-Type': 'application/json'})
        response = conn.getresponse()
        if 300 <= response.status < 400:
            raise ValueError('向量服务返回重定向，已阻止跟随；请填写服务的直接内网地址')
        if response.status == 404:
            raise ValueError('未找到向量模型或 /api/embed 接口；请检查模型名称与 Ollama 版本')
        if response.status != 200:
            raise ValueError(f'向量服务返回 HTTP {response.status}；请检查服务状态，BM25 仍可使用')
        raw = response.read(16 * 1024 * 1024 + 1)
        if len(raw) > 16 * 1024 * 1024:
            raise ValueError('向量响应过大；请减小批次或检查服务')
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeError) as exc:
            raise ValueError('向量服务未返回有效 JSON；请确认地址指向 Ollama') from exc
        if not isinstance(data, dict):
            raise ValueError('向量响应格式错误：应为 JSON 对象')
        return data
    except (TimeoutError, socket.timeout) as exc:
        raise ValueError('向量服务响应超时；请检查服务负载或连接，BM25 仍可使用') from exc
    except (OSError, http.client.HTTPException) as exc:
        raise ValueError('无法连接向量服务；请确认 Ollama 已启动、端口可达，BM25 仍可使用') from exc
    finally:
        conn.close()
        sock.close()


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
    data = embedding_response(endpoint, json.dumps({'model': model, 'input': texts, 'truncate': False}).encode(), timeout)
    vectors = data.get('embeddings', [])
    if not isinstance(vectors, list) or len(vectors) != len(texts):
        raise ValueError('向量服务返回数量不符')
    if any(not isinstance(v, list) for v in vectors):
        raise ValueError('向量响应格式错误：每个向量必须为数组')
    dim = len(vectors[0]) if vectors else 0
    for v in vectors:
        if not dim or len(v) != dim or any(type(x) not in (int,float) or not math.isfinite(x) for x in v):
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

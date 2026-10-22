#!/usr/bin/env python3
"""Opt-in embeddings through a self-hosted Ollama; never downloads models or uses cloud endpoints."""
import argparse
import json
import sqlite3
import sys
from contextlib import closing
from core import load_book, check_source, embed, emit, ollama_base


def build(db, model='bge-m3', base=None):
    endpoint=ollama_base(base)
    rows,meta=load_book(db)
    if meta.get('schema_version')!='2': raise ValueError('请先从原材料重建 2.0 索引')
    check_source(meta)
    vectors=[]; dim=None
    for offset in range(0,len(rows),16):
        batch=embed([r['content'] for r in rows[offset:offset+16]],model,timeout=120,base=endpoint)
        for v in batch:
            if dim is None: dim=len(v)
            if len(v)!=dim: raise ValueError('批次向量维度不一致')
        vectors.extend(batch)
    # Source/index may have changed while the remote-local inference was running.
    fresh_rows,fresh_meta=load_book(db)
    if fresh_rows!=rows or fresh_meta.get('source_sha256')!=meta['source_sha256']:
        raise ValueError('建向量期间索引发生变化，请重试')
    check_source(meta)
    with closing(sqlite3.connect(db)) as c, c:
        c.execute('BEGIN IMMEDIATE')
        current=dict(c.execute('SELECT key,value FROM metadata'))
        if current.get('source_sha256')!=meta['source_sha256']:
            raise ValueError('保存向量前索引已变化，请重试')
        c.execute('CREATE TABLE IF NOT EXISTS vectors(chunk_id INTEGER PRIMARY KEY,embedding TEXT NOT NULL)')
        c.execute('DELETE FROM vectors')
        c.executemany('INSERT INTO vectors VALUES(?,?)',[(r['chunk_id'],json.dumps(v)) for r,v in zip(rows,vectors)])
        c.executemany('INSERT OR REPLACE INTO metadata VALUES(?,?)',[
            ('vector_model',model),('vector_dimension',str(dim)),
            ('vector_endpoint',endpoint),('vector_source_sha256',meta['source_sha256'])])
    return {'status':'vectors_built','count':len(vectors),'dimension':dim,'model':model,'endpoint':endpoint}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('db');p.add_argument('--model',default='bge-m3')
    p.add_argument('--ollama',help='自建 Ollama 地址；默认取环境变量 AI_READING_OLLAMA，否则 http://127.0.0.1:11434')
    a=p.parse_args()
    try: emit(build(a.db,a.model,a.ollama));return 0
    except Exception as e:
        print(f'向量构建失败，BM25 索引保留：{e}',file=sys.stderr);return 1
if __name__=='__main__':sys.exit(main())

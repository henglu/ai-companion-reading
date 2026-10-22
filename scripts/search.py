#!/usr/bin/env python3
"""Search one explicit book, report the actual retrieval mode and verifiable source locations."""
import argparse
import json
import sqlite3
import sys
from core import load_book, check_source, bm25, embed, cosine, connect_read, emit, ollama_base


def search(query, db, top_k=5, mode='auto', chapter=None, context=0, base=None):
    if not query.strip(): raise ValueError('查询不能为空')
    if not 1<=top_k<=100: raise ValueError('top-k 必须在 1—100')
    rows,meta=load_book(db); source_status=check_source(meta)
    if chapter:
        rows=[r for r in rows if r['chapter_id']==chapter]
        if not rows: raise ValueError('章节 ID 不存在，请先用 read_book.py --chapters 查看')
    lexical=bm25(query,rows); ranked=lexical; actual='bm25'; warning=None
    if mode in ('auto','hybrid'):
        try:
            if not meta.get('vector_model'): raise ValueError('尚未构建向量')
            if meta.get('vector_source_sha256')!=meta.get('source_sha256'): raise ValueError('向量与原文版本不一致')
            with connect_read(db) as c:
                stored={r[0]:json.loads(r[1]) for r in c.execute('SELECT chunk_id,embedding FROM vectors')}
            if any(r['chunk_id'] not in stored for r in rows): raise ValueError('向量不完整')
            qv=embed([query],meta['vector_model'],timeout=5,base=base)[0]
            semantic=sorted(((r['chunk_id'],cosine(qv,stored[r['chunk_id']])) for r in rows),key=lambda x:(-x[1],x[0]))
            # RRF ranks are fused, not interpreted as a calibrated accuracy/confidence score.
            fused={}
            for ranking in (lexical[:max(10,top_k)],semantic[:max(10,top_k)]):
                for rank,(cid,_) in enumerate(ranking,1):fused[cid]=fused.get(cid,0)+1/(60+rank)
            ranked=sorted(fused.items(),key=lambda x:(-x[1],x[0]));actual='hybrid'
        except Exception as exc:
            try: endpoint=ollama_base(base)
            except Exception: endpoint='未配置'
            warning=f'已降级 BM25：{type(exc).__name__}: {exc}（向量端点 {endpoint}）'
    by_id={r['chunk_id']:r for r in rows}; results=[]
    for cid,score in ranked[:top_k]:
        r=by_id[cid]; snippet=r['content']
        if context>0:
            pos=snippet.find(query);pos=max(pos,0);snippet=snippet[max(0,pos-context):pos+len(query)+context]
        results.append({'rank':len(results)+1,'score':round(score,6),**r,'context':snippet,
                        'book_id':meta.get('book_id'),'source_sha256':meta.get('source_sha256')})
    return {'query':query,'title':meta.get('title'),'requested_mode':mode,'actual_mode':actual,
            'vector_model':meta.get('vector_model'),'source_status':source_status,
            'warning':warning,'results':results}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('query');p.add_argument('db_path')
    p.add_argument('--top-k',type=int,default=5);p.add_argument('--mode',choices=['auto','bm25','hybrid'],default='auto')
    p.add_argument('--chapter');p.add_argument('--context',type=int,default=0);p.add_argument('--json',action='store_true')
    p.add_argument('--ollama',help='自建 Ollama 地址；默认取环境变量 AI_READING_OLLAMA，否则 http://127.0.0.1:11434')
    a=p.parse_args()
    try:
        data=search(a.query,a.db_path,a.top_k,a.mode,a.chapter,a.context,a.ollama)
        if a.json:emit(data)
        else:
            print(f"{data['title']} | 实际检索：{data['actual_mode']}")
            if data['warning']:print(data['warning'])
            for r in data['results']:print(f"\n[{r['chunk_id']}] {r['chapter']} · {r['locator']}\n{r['context']}")
            if not data['results']:print('未命中原文；请调整查询或按章节阅读，不要编造引用。')
        return 0
    except (ValueError,OSError,sqlite3.Error) as exc:
        print(f'错误：{exc}',file=sys.stderr);return 1
if __name__=='__main__':sys.exit(main())

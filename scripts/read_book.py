#!/usr/bin/env python3
"""Read chapters or exact evidence chunks; PDF page groups are not claimed to be book chapters."""
import argparse
import sqlite3
import sys
from core import load_book,check_source,emit


def read(db,chapter=None,chunk=None,offset=0,limit=20):
    if offset<0 or not 1<=limit<=100:raise ValueError('offset>=0，limit 在 1—100')
    rows,meta=load_book(db);status=check_source(meta)
    if chapter is None and chunk is None:
        chapters={}
        for r in rows:
            chapters.setdefault(r['chapter_id'],{'id':r['chapter_id'],'title':r.get('chapter'),'chunks':0})['chunks']+=1
        return {'book':meta,'source_status':status,'chapters':list(chapters.values())}
    if chapter is not None:rows=[r for r in rows if r['chapter_id']==chapter]
    if chunk is not None:rows=[r for r in rows if r['chunk_id']==chunk]
    if not rows:raise ValueError('没有对应章节/段落')
    return {'book':meta,'source_status':status,'total_chunks':len(rows),'offset':offset,
            'next_offset':offset+limit if offset+limit<len(rows) else None,'chunks':rows[offset:offset+limit]}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('db');p.add_argument('--chapters',action='store_true')
    p.add_argument('--chapter');p.add_argument('--chunk',type=int);p.add_argument('--offset',type=int,default=0);p.add_argument('--limit',type=int,default=20)
    a=p.parse_args()
    try:emit(read(a.db,a.chapter,a.chunk,a.offset,a.limit));return 0
    except (ValueError,OSError,sqlite3.Error) as e:print(f'错误：{e}',file=sys.stderr);return 1
if __name__=='__main__':sys.exit(main())

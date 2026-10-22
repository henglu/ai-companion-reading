#!/usr/bin/env python3
"""Index a local EPUB, Markdown, TXT or text PDF. Existing output is not silently removed."""
import argparse
import json
import os
import posixpath
import re
import sqlite3
import sys
import tempfile
import zipfile
from contextlib import closing
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote
from xml.etree import ElementTree as ET
from core import fingerprint, load_book, emit


class Blocks(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks = []; self.buf = []; self.kind = 'p'; self.skip = 0
    def flush(self):
        value = ''.join(self.buf).strip()
        if value: self.blocks.append((self.kind, value))
        self.buf = []
    def handle_starttag(self, tag, attrs):
        if tag in ('script','style'): self.skip += 1
        if self.skip: return
        if tag in ('p','h1','h2','h3','h4','li','blockquote','tr'):
            self.flush(); self.kind = tag
        if tag == 'br': self.buf.append('\n')
    def handle_endtag(self, tag):
        if tag in ('script','style'):
            self.skip = max(0,self.skip-1); return
        if not self.skip and tag in ('p','h1','h2','h3','h4','li','blockquote','tr','div'):
            self.flush(); self.kind = 'p'
    def handle_data(self, data):
        if not self.skip: self.buf.append(data)


def epub_sections(path):
    with zipfile.ZipFile(path) as z:
        try:
            container = ET.fromstring(z.read('META-INF/container.xml'))
            opf = next(e.attrib['full-path'] for e in container.iter() if e.tag.split('}')[-1]=='rootfile')
            package = ET.fromstring(z.read(opf))
            manifest = {e.attrib['id']: e.attrib for e in package.iter() if e.tag.split('}')[-1]=='item'}
            order = []
            for e in package.iter():
                if e.tag.split('}')[-1]!='itemref' or e.attrib.get('linear')=='no': continue
                item = manifest[e.attrib['idref']]
                if 'nav' in item.get('properties','').split(): continue
                order.append(posixpath.normpath(posixpath.join(posixpath.dirname(opf),unquote(item['href'].split('#')[0]))))
        except (KeyError, StopIteration, ET.ParseError) as exc:
            raise ValueError('EPUB 缺少有效的 OPF/spine，无法可靠确认章节顺序') from exc
        if not order: raise ValueError('EPUB 没有可读取的 spine 章节')
        sections=[]
        for member in order:
            parser=Blocks(); parser.feed(z.read(member).decode('utf-8-sig')); parser.close(); parser.flush()
            title=next((v for k,v in parser.blocks if k.startswith('h')),Path(member).stem)
            body='\n\n'.join(v for k,v in parser.blocks)
            if body.strip(): sections.append((title,member,body))
        return sections


def text_sections(path):
    text=path.read_text(encoding='utf-8-sig')
    sections=[]; title='正文'; lines=[]; start=1
    for n,line in enumerate(text.splitlines(),1):
        match=re.match(r'^#{1,3}\s+(.+?)\s*#*\s*$',line) if path.suffix.lower()=='.md' else re.match(r'^(第[一二三四五六七八九十百零\d]+[章节].*)$',line)
        if match:
            if '\n'.join(lines).strip(): sections.append((title,f'行 {start}—{n-1}','\n'.join(lines)))
            title=match[1]; lines=[line]; start=n
        else: lines.append(line)
    if '\n'.join(lines).strip(): sections.append((title,f'行 {start}—{len(text.splitlines())}','\n'.join(lines)))
    return sections


def pdf_sections(path):
    try: from pypdf import PdfReader
    except Exception as exc:
        from check_environment import pdf_help
        raise ValueError(pdf_help()) from exc
    try:
        reader=PdfReader(str(path))
        if reader.is_encrypted and not reader.decrypt(''):
            raise ValueError('PDF 已加密；请先使用有权限的工具解密，再重新导入')
        pages=list(reader.pages)
    except Exception as exc:
        raise ValueError('PDF 无法读取或已加密；请确认阅读器能打开，并导出未加密的文字层 PDF') from exc
    sections=[]
    for n,page in enumerate(pages,1):
        try: text=page.extract_text() or ''
        except Exception as exc:
            raise ValueError(f'PDF 第 {n} 页无法解析；请重新导出文件或先做 OCR') from exc
        if text.strip(): sections.append((f'PDF 第 {n} 页',f'PDF 物理页 {n}',text))
    if not sections: raise ValueError('PDF 未提取到文字；可能为扫描件，请先 OCR')
    if len(sections)<len(pages):
        print('提示：部分 PDF 页没有文字，未纳入索引；不能据此声称覆盖整本材料。',file=sys.stderr)
    return sections


def chunks_of(text, limit=700):
    # Split within a source section; never merge across chapter boundaries.
    pieces=[]; buf=''
    for para in re.split(r'\n\s*\n',text):
        para=para.strip()
        if not para: continue
        if buf and len(buf)+len(para)+2>limit: pieces.append(buf);buf=''
        while len(para)>limit:
            if buf: pieces.append(buf);buf=''
            pieces.append(para[:limit]);para=para[limit:]
        buf=(buf+'\n\n'+para).strip()
    if buf: pieces.append(buf)
    return pieces


def build(source, output, title=None, author='', replace=False):
    source=Path(source).expanduser().resolve(); output=Path(output).expanduser().resolve()
    if not source.is_file(): raise ValueError('材料文件不存在')
    if output==source: raise ValueError('索引输出不能覆盖源材料')
    sha=fingerprint(source)
    if output.exists() and not replace:
        _,meta=load_book(output)
        if meta.get('source_sha256')==sha:
            return {'status':'reused','db':str(output),'book_id':meta['book_id']}
        raise ValueError('目标索引已存在；如需重建，请显式使用 --replace（将保留 .bak）')
    ext=source.suffix.lower()
    if ext=='.epub': sections=epub_sections(source)
    elif ext in ('.txt','.md'): sections=text_sections(source)
    elif ext=='.pdf': sections=pdf_sections(source)
    else: raise ValueError('支持 EPUB / 带文字层 PDF / UTF-8 TXT / Markdown')
    rows=[]
    for n,(heading,loc,text) in enumerate(sections,1):
        for k,part in enumerate(chunks_of(text),1):
            rows.append((len(rows),len(rows),f'c{n:04}',heading,f'{loc} · 段 {k}',part))
    if not rows: raise ValueError('没有可索引的正文')
    meta={'schema_version':'2','title':title or source.stem,'author':author or '',
          'source_path':str(source),'source_sha256':sha,'book_id':sha[:20],
          'format':ext,'total_chunks':str(len(rows)),'total_chapters':str(len(sections))}
    output.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix='.reading-',suffix='.db',dir=output.parent);os.close(fd)
    try:
        with closing(sqlite3.connect(tmp)) as c, c:
            c.execute('CREATE TABLE chunks(chunk_id INTEGER PRIMARY KEY,para_id INTEGER,chapter_id TEXT,chapter TEXT,locator TEXT,content TEXT NOT NULL)')
            c.execute('CREATE INDEX chapter_idx ON chunks(chapter_id)')
            c.execute('CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
            c.executemany('INSERT INTO chunks VALUES(?,?,?,?,?,?)',rows)
            c.executemany('INSERT INTO metadata VALUES(?,?)',meta.items())
        if output.exists():
            backup=Path(str(output)+'.bak')
            with closing(sqlite3.connect(output)) as src,closing(sqlite3.connect(backup)) as dst:
                src.backup(dst)
        os.replace(tmp,output)
    finally:
        if Path(tmp).exists(): Path(tmp).unlink()
    return {'status':'indexed','db':str(output),**meta,'retrieval':'bm25','vectors':'not_built'}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('source');p.add_argument('output_db')
    p.add_argument('--title');p.add_argument('--author',default='');p.add_argument('--replace',action='store_true')
    a=p.parse_args()
    try: emit(build(a.source,a.output_db,a.title,a.author,a.replace))
    except zipfile.BadZipFile:
        print('错误：EPUB 文件损坏或格式不正确；请用阅读器确认能否打开，或重新导出 EPUB/TXT/Markdown 后重试。',file=sys.stderr);return 1
    except UnicodeError:
        print('错误：文本编码不是有效的 UTF-8；请另存为 UTF-8 后重试，原文件未修改。',file=sys.stderr);return 1
    except (ValueError,OSError,sqlite3.Error,KeyError) as exc:
        print(f'错误：{exc}',file=sys.stderr);return 1
    return 0
if __name__=='__main__':sys.exit(main())

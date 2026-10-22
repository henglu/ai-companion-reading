#!/usr/bin/env python3
"""Local learning records. The host AI writes questions/assessments; this tool validates, stores and scores objective answers."""
import argparse
import json
import math
import sqlite3
import sys
import uuid
from datetime import datetime,timezone
from pathlib import Path
from core import load_book,check_source,emit

MODES=('reading','exam')
VERDICTS=('understood','partial','needs_review','discussion')
DIMENSIONS=('concept','evidence','reasoning','application')


def now():return datetime.now(timezone.utc).isoformat()
def uid():return uuid.uuid4().hex[:16]
def dump(obj):return json.dumps(obj,ensure_ascii=False)
def require(condition,message):
    if not condition:raise ValueError(message)


def database(root):
    root=Path(root).expanduser();root.mkdir(parents=True,exist_ok=True)
    c=sqlite3.connect(root/'learning.sqlite3');c.row_factory=sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON');c.execute('PRAGMA busy_timeout=5000')
    c.executescript('''
    CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,mode TEXT NOT NULL,db TEXT NOT NULL,book_id TEXT NOT NULL,sha TEXT NOT NULL,goal TEXT,config TEXT,created TEXT);
    CREATE TABLE IF NOT EXISTS progress(session TEXT REFERENCES sessions(id),chapter TEXT,status TEXT,note TEXT,updated TEXT,PRIMARY KEY(session,chapter));
    CREATE TABLE IF NOT EXISTS quizzes(id TEXT PRIMARY KEY,session TEXT REFERENCES sessions(id),kind TEXT,payload TEXT,created TEXT);
    CREATE TABLE IF NOT EXISTS attempts(id INTEGER PRIMARY KEY AUTOINCREMENT,session TEXT REFERENCES sessions(id),quiz TEXT REFERENCES quizzes(id),question TEXT,answer TEXT,result TEXT,created TEXT);
    CREATE TABLE IF NOT EXISTS resolutions(session TEXT REFERENCES sessions(id),question TEXT,via TEXT,created TEXT,PRIMARY KEY(session,question));
    ''')
    return c


def session(c,sid):
    row=c.execute('SELECT * FROM sessions WHERE id=?',(sid,)).fetchone()
    require(row is not None,'学习记录不存在')
    s=dict(row);rows,meta=load_book(s['db']);check_source(meta)
    require(meta.get('source_sha256')==s['sha'],'材料版本已变化，请新建学习记录；旧记录保留')
    return s,rows,meta


def new(c,db,mode,goal='',config=None):
    require(mode in MODES,'模式必须是 reading 或 exam')
    rows,meta=load_book(db);check_source(meta)
    require(meta.get('schema_version')=='2','学习记录需要 2.0 索引，请先从原文件重建')
    config=config or {};require(isinstance(config,dict),'config 应为 JSON 对象')
    sid=uid()
    with c:c.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?,?,?)',(sid,mode,str(Path(db).expanduser().resolve()),meta['book_id'],meta['source_sha256'],goal,dump(config),now()))
    return {'session':sid,'mode':mode,'book':meta['title'],'goal':goal,'config':config}


def assessment(value,mode,max_points):
    require(isinstance(value,dict),'开放题需要 AI 提供 assessment；脚本不会自动判断语义')
    require(value.get('verdict') in VERDICTS,'assessment.verdict 无效')
    require(isinstance(value.get('feedback'),str) and value['feedback'].strip(),'需要具体反馈')
    dims=value.get('dimensions',{})
    require(isinstance(dims,dict) and set(dims)==set(DIMENSIONS),'需要 concept/evidence/reasoning/application 四项评价')
    require(all(v in ('clear','partial','unclear','not_applicable') for v in dims.values()),'评价维度值无效')
    score=value.get('score')
    if mode=='reading':require(score is None,'阅读模式使用描述性反馈，不录入考试分数')
    elif score is not None:require(type(score) in (int,float) and math.isfinite(score) and 0<=score<=max_points,'评分超出题目分值')
    return value


def add_quiz(c,sid,payload):
    s,rows,meta=session(c,sid);by_id={r['chunk_id']:r for r in rows}
    require(isinstance(payload,dict),'练习文件必须为对象')
    kind=payload.get('kind','chapter');require(kind in ('chapter','mock','review'),'练习类型无效')
    require(s['mode']=='exam' or kind!='mock','阅读模式不创建模拟考试')
    questions=payload.get('questions');require(isinstance(questions,list) and 1<=len(questions)<=100,'每组需要 1—100 道题')
    quiz_id=uid();seen=set();canonical=[]
    for q in questions:
        require(isinstance(q,dict),'题目必须为对象');q=dict(q)
        local=q.get('id',str(len(canonical)+1));require(isinstance(local,str) and local and local not in seen,'题目 ID 重复或为空');seen.add(local)
        q['id']=quiz_id+':'+local
        require(q.get('type') in ('judgment','single','multiple','open'),'题型无效')
        require(s['mode']=='exam' or q['type'] in ('judgment','open'),'阅读模式使用判断题和开放题')
        require(isinstance(q.get('prompt'),str) and q['prompt'].strip(),'题干不能为空')
        require(isinstance(q.get('concept'),str) and q['concept'].strip(),'需要标记概念/考点')
        origin=q.get('origin','generated');require(origin in ('generated','user_provided'),'origin 只能为 generated 或 user_provided')
        if origin=='user_provided':require(bool(q.get('source_label')),'用户提供的题目需要 source_label，不能伪称官方认证')
        q['origin']=origin
        evidence=q.get('evidence');require(isinstance(evidence,list) and evidence,'题目必须有教材依据')
        for e in evidence:
            require(isinstance(e,dict) and type(e.get('chunk_id')) is int and e['chunk_id'] in by_id,'引用段落不存在')
            quote=e.get('quote');require(isinstance(quote,str) and quote.strip() and quote in by_id[e['chunk_id']]['content'],'引用不在对应原文中')
            if q.get('chapter_id'):require(by_id[e['chunk_id']]['chapter_id']==q['chapter_id'],'引用超出题目指定章节')
        max_points=q.get('max_points',1)
        require(type(max_points) in (int,float) and math.isfinite(max_points) and max_points>0,'分值必须为正数')
        q['max_points']=max_points
        if q['type']=='open':
            require(isinstance(q.get('rubric'),str) and q['rubric'].strip(),'开放题需要评价依据 rubric')
            q.pop('correct',None)
        else:
            require(isinstance(q.get('explanation'),str) and q['explanation'].strip(),'客观题需要教材解析')
            if q['type']=='judgment':require(type(q.get('correct')) is bool,'判断题答案必须为 JSON true/false')
            else:
                opts=q.get('options');require(isinstance(opts,dict) and len(opts)>=2 and all(isinstance(k,str) and isinstance(v,str) and v.strip() for k,v in opts.items()),'选择题需要选项字典')
                correct=q.get('correct')
                if q['type']=='single':require(isinstance(correct,str) and correct in opts,'单选答案不在选项内')
                else:require(isinstance(correct,list) and correct and all(isinstance(x,str) and x in opts for x in correct) and len(set(correct))==len(correct),'多选答案无效')
        canonical.append(q)
    stored={**payload,'questions':canonical,'mode':s['mode'],'book_id':meta['book_id'],'source_sha256':meta['source_sha256']}
    with c:c.execute('INSERT INTO quizzes VALUES(?,?,?,?,?)',(quiz_id,sid,kind,dump(stored),now()))
    return {'quiz':quiz_id,'mode':s['mode'],'questions':[public_question(q,s['mode']) for q in canonical]}


def public_question(q,mode='exam'):
    # No answer, explanation, evidence excerpt or private grading rubric before the attempt.
    return {k:q[k] for k in ('id','type','prompt','options','origin','source_label','max_points') if k in q and (mode=='exam' or k!='max_points')}


def get_quiz(c,sid,qid,reveal=False):
    session(c,sid)
    row=c.execute('SELECT payload FROM quizzes WHERE id=? AND session=?',(qid,sid)).fetchone()
    require(row is not None,'练习不属于此学习记录')
    p=json.loads(row[0])
    if not reveal:p['questions']=[public_question(q,p['mode']) for q in p['questions']]
    return p


def answer(c,sid,qid,responses):
    s,_,_=session(c,sid);quiz=get_quiz(c,sid,qid,True)
    qs={q['id']:q for q in quiz['questions']}
    require(isinstance(responses,list) and responses,'responses 不能为空');seen=set();pending=[]
    for item in responses:
        require(isinstance(item,dict),'回答必须为对象');qid_item=item.get('question_id')
        require(qid_item in qs and qid_item not in seen,'问题 ID 不属于本次练习或重复');seen.add(qid_item)
        q=qs[qid_item];a=item.get('answer');reason=item.get('reason','')
        require(isinstance(reason,str),'reason 应为文本')
        if q['type']=='open':
            require(isinstance(a,str) and a.strip(),'开放题回答不能为空')
            ev=assessment(item.get('assessment'),s['mode'],q['max_points'])
            result={'verdict':ev['verdict'],'score':ev.get('score'),'assessment':ev,'correct':None}
        else:
            if q['type']=='judgment':require(type(a) is bool,'判断题回答必须是 true/false')
            elif q['type']=='single':require(isinstance(a,str) and a in q['options'],'回答不在选项内')
            else:require(isinstance(a,list) and a and all(isinstance(x,str) and x in q['options'] for x in a) and len(set(a))==len(a),'多选回答无效')
            correct=set(a)==set(q['correct']) if q['type']=='multiple' else a==q['correct']
            verdict='understood' if correct else 'needs_review'
            ev=item.get('assessment')
            if ev is not None:
                ev=assessment(ev,s['mode'],q['max_points'])
                if correct and ev['verdict'] in ('partial','needs_review'):verdict=ev['verdict']
            elif s['mode']=='reading':verdict='partial' if correct else 'needs_review'
            result={'correct':correct,'verdict':verdict,'score':(q['max_points'] if correct else 0) if s['mode']=='exam' else None,
                    'expected':q['correct'],'explanation':q['explanation'],'assessment':ev}
            if s['mode']=='reading' and not reason.strip():
                result['verdict']='partial' if correct else 'needs_review';result['follow_up']='请说明判断理由，再评价概念是否理解。'
        result.update(concept=q['concept'],evidence=q['evidence'],mode=s['mode'],max_points=q['max_points'])
        pending.append((sid,qid,qid_item,dump({'answer':a,'reason':reason}),dump(result),now()))
    # Validation completes before writing any answers; partial submissions remain explicitly partial.
    with c:c.executemany('INSERT INTO attempts(session,quiz,question,answer,result,created) VALUES(?,?,?,?,?,?)',pending)
    return {'recorded':len(pending),'results':[{'question_id':r[2],**json.loads(r[4])} for r in pending],**quiz_summary(c,sid,qid)}


def latest(c,sid,quiz=None):
    sql='SELECT * FROM attempts WHERE session=?';params=[sid]
    if quiz:sql+=' AND quiz=?';params.append(quiz)
    out={}
    for r in c.execute(sql+' ORDER BY id',params):out[r['question']]={**dict(r),'answer':json.loads(r['answer']),'result':json.loads(r['result'])}
    return out


def quiz_summary(c,sid,qid):
    s,_,_=session(c,sid);q=get_quiz(c,sid,qid,True);a=latest(c,sid,qid);total=len(q['questions'])
    result={'answered':len(a),'total':total,'completed':len(a)==total}
    if s['mode']=='exam':
        graded=[x for x in a.values() if x['result']['score'] is not None]
        result.update(graded=len(graded),score=sum(x['result']['score'] for x in graded),
                      max_score=sum(x['max_points'] for x in q['questions']),
                      score_is_final=len(graded)==total)
    return result


def review(c,sid):
    s,_,_=session(c,sid);answers=latest(c,sid);resolved=set()
    for r in c.execute('SELECT question,via FROM resolutions WHERE session=?',(sid,)):
        old=answers.get(r['question']);new=answers.get(r['via'])
        if old and new and new['id']>old['id'] and new['result']['verdict']=='understood':resolved.add(r['question'])
    items=[]
    for qid,a in answers.items():
        if a['result']['verdict'] in ('partial','needs_review') and qid not in resolved:
            quiz=get_quiz(c,sid,a['quiz'],True);q=next(q for q in quiz['questions'] if q['id']==qid)
            items.append({'question_id':qid,'prompt':q['prompt'],'concept':q['concept'],'last_answer':a['answer'],**a['result']})
    return {'mode':s['mode'],'label':'待澄清概念' if s['mode']=='reading' else '错题与薄弱考点','items':items}


def resolve(c,sid,question,via):
    session(c,sid);a=latest(c,sid)
    require(question in a and via in a and question!=via,'需要本记录内原题与一道不同的复习题')
    require(a[via]['result']['verdict']=='understood','复习题尚未达到理解要求')
    require(a[via]['id']>a[question]['id'],'复习题必须在原题最近一次作答之后完成')
    require(a[question]['result']['concept']==a[via]['result']['concept'],'复习题必须检验同一个考点')
    with c:c.execute('INSERT OR REPLACE INTO resolutions VALUES(?,?,?,?)',(sid,question,via,now()))
    return {'resolved':question,'via':via,'history_preserved':True}


def progress(c,sid,chapter,status,note=''):
    _,rows,_=session(c,sid);require(chapter in {r['chapter_id'] for r in rows},'章节 ID 不存在')
    require(status in ('reading','read','review'),'status 无效')
    with c:c.execute('INSERT OR REPLACE INTO progress VALUES(?,?,?,?,?)',(sid,chapter,status,note,now()))
    return {'chapter':chapter,'status':status,'note':note}


def status(c,sid):
    s,_,meta=session(c,sid);s['config']=json.loads(s['config'])
    return {'session':s,'book':meta['title'],'progress':[dict(r) for r in c.execute('SELECT * FROM progress WHERE session=? ORDER BY chapter',(sid,))],
            'quizzes':[{'id':r['id'],'kind':r['kind'],**quiz_summary(c,sid,r['id'])} for r in c.execute('SELECT id,kind FROM quizzes WHERE session=? ORDER BY created',(sid,))],
            'review':review(c,sid)}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',default=str(Path.home()/'ai_reading'/'learning'))
    sub=p.add_subparsers(dest='cmd',required=True)
    q=sub.add_parser('new');q.add_argument('--db',required=True);q.add_argument('--mode',choices=MODES,required=True);q.add_argument('--goal',default='');q.add_argument('--config')
    q=sub.add_parser('list')
    for name in ('status','review'):
        q=sub.add_parser(name);q.add_argument('--session',required=True)
    q=sub.add_parser('progress');q.add_argument('--session',required=True);q.add_argument('--chapter',required=True);q.add_argument('--status',choices=['reading','read','review'],required=True);q.add_argument('--note',default='')
    q=sub.add_parser('add-quiz');q.add_argument('--session',required=True);q.add_argument('--file',required=True)
    q=sub.add_parser('quiz');q.add_argument('--session',required=True);q.add_argument('--quiz',required=True);q.add_argument('--reveal',action='store_true')
    q=sub.add_parser('answer');q.add_argument('--session',required=True);q.add_argument('--quiz',required=True);q.add_argument('--file',required=True)
    q=sub.add_parser('resolve');q.add_argument('--session',required=True);q.add_argument('--question',required=True);q.add_argument('--via',required=True)
    a=p.parse_args()
    c=database(a.root)
    try:
        with c:
            if a.cmd=='new':r=new(c,a.db,a.mode,a.goal,json.loads(Path(a.config).read_text()) if a.config else None)
            elif a.cmd=='list':r=[dict(x) for x in c.execute('SELECT id,mode,goal,book_id,created FROM sessions ORDER BY created')]
            elif a.cmd=='status':r=status(c,a.session)
            elif a.cmd=='review':r=review(c,a.session)
            elif a.cmd=='progress':r=progress(c,a.session,a.chapter,a.status,a.note)
            elif a.cmd=='add-quiz':r=add_quiz(c,a.session,json.loads(Path(a.file).read_text(encoding='utf-8')))
            elif a.cmd=='quiz':r=get_quiz(c,a.session,a.quiz,a.reveal)
            elif a.cmd=='answer':r=answer(c,a.session,a.quiz,json.loads(Path(a.file).read_text(encoding='utf-8'))['responses'])
            else:r=resolve(c,a.session,a.question,a.via)
            emit(r)
        return 0
    except (ValueError,KeyError,TypeError,OSError,sqlite3.Error) as exc:
        print(f'错误：{exc}',file=sys.stderr);return 1
    finally:
        c.close()
if __name__=='__main__':sys.exit(main())

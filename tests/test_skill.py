import copy
import importlib
import json
import sqlite3
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import core,index_book,search,read_book,study,build_vectors

TEXT='''# 第一章 有效反馈
反馈应描述可观察的行为，并指出行为与目标之间的差距。只赞美一个人聪明，并没有告诉他如何改善。反馈不是评价一个人的固定能力。

有效反馈需要具体、及时，并与可调整的行动联系起来。反馈的目的是帮助改进行动，而不是要求接受者无条件服从。

# 第二章 检验理解
回忆是暂时不看材料，用自己的话说出已经学习的内容。重复阅读可以增加熟悉感，但熟悉并不等于理解。

迁移是把理解运用到新的情境，必须检查原有条件在新情境中是否仍然成立。改变情境之后，原结论不一定适用。
'''

def assessment(verdict='understood',score=None):
    a={'verdict':verdict,'feedback':'指出概念与条件，并给出原文依据。','dimensions':dict(concept='clear',evidence='clear',reasoning='clear',application='not_applicable')}
    if score is not None:a['score']=score
    return a

class Base(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.source=self.root/'反馈教材.md';self.source.write_text(TEXT)
        self.db=self.root/'book.db';index_book.build(self.source,self.db,title='反馈教材')
        self.c=study.database(self.root/'state')
        self.reading=study.new(self.c,self.db,'reading')['session'];self.exam=study.new(self.c,self.db,'exam')['session']
    def tearDown(self):self.c.close();self.temp.cleanup()
    def question(self,typ='judgment'):
        q={'id':'q1','type':typ,'prompt':'有效反馈要求接受者无条件服从。请判断并解释。','concept':'反馈的目的','chapter_id':'c0001',
           'correct':False,'explanation':'反馈是改进行动，不是无条件服从。','evidence':[{'chunk_id':0,'quote':'反馈的目的是帮助改进行动，而不是要求接受者无条件服从。'}]}
        if typ=='open':q.update(prompt='解释有效反馈，并提出你对作者观点的异议。',rubric='准确理解观点，异议有依据即可。');q.pop('correct')
        if typ=='single':q.update(options={'A':'服从','B':'改进'},correct='B')
        if typ=='multiple':q.update(options={'A':'具体','B':'固定能力','C':'及时'},correct=['A','C'])
        return q
    def quiz(self,sid=None,typ='judgment'):
        return study.add_quiz(self.c,sid or self.reading,{'questions':[self.question(typ)]})
    def respond(self,quiz,sid=None,value=False,ev=None,reason='原文说反馈帮助改进行动。'):
        item={'question_id':quiz['questions'][0]['id'],'answer':value,'reason':reason}
        if ev is not None:item['assessment']=ev
        return study.answer(self.c,sid or self.reading,quiz['quiz'],[item])

class RetrievalTests(Base):
    def test_chapter_order_and_read(self):
        data=read_book.read(self.db);self.assertEqual([c['title'] for c in data['chapters']],['第一章 有效反馈','第二章 检验理解'])
        self.assertIn('反馈',read_book.read(self.db,'c0001')['chunks'][0]['content'])
    def test_bm25_evidence(self):
        result=search.search('可观察的行为',self.db,mode='bm25')
        self.assertEqual(result['actual_mode'],'bm25');self.assertEqual(result['results'][0]['chapter_id'],'c0001')
        self.assertTrue(result['results'][0]['locator']);self.assertEqual(result['source_status'],'verified')
    def test_no_match(self):self.assertEqual(search.search('zyxnonexistent',self.db,mode='bm25')['results'],[])
    def test_single_chinese_character(self):self.assertTrue(search.search('行',self.db,mode='bm25')['results'])
    def test_chapter_filter(self):
        result=search.search('反馈',self.db,mode='bm25',chapter='c0002');self.assertEqual(result['results'],[])
    def test_missing_chapter_error(self):
        with self.assertRaises(ValueError):search.search('反馈',self.db,chapter='missing')
    def test_fallback_disclosed(self):
        result=search.search('反馈',self.db,mode='hybrid');self.assertEqual(result['actual_mode'],'bm25');self.assertIn('未构建',result['warning'])
    def test_stale_source_rejected(self):
        self.source.write_text(TEXT+'新内容')
        with self.assertRaises(ValueError):search.search('反馈',self.db)
        with self.assertRaises(ValueError):study.status(self.c,self.reading)
    def test_rebuild_and_old_session_rejected(self):
        self.source.write_text(TEXT+'新内容')
        with self.assertRaises(ValueError):index_book.build(self.source,self.db)
        index_book.build(self.source,self.db,replace=True);self.assertTrue(Path(str(self.db)+'.bak').exists())
        with self.assertRaises(ValueError):study.status(self.c,self.reading)
    def test_reuse_without_rebuild(self):self.assertEqual(index_book.build(self.source,self.db)['status'],'reused')
    def test_missing_source_snapshot(self):
        self.source.unlink();self.assertEqual(search.search('反馈',self.db)['source_status'],'source_unavailable')
    def test_source_not_overwritten(self):
        with self.assertRaises(ValueError):index_book.build(self.source,self.source,replace=True)
        self.assertEqual(self.source.read_text(),TEXT)
    def test_empty_input_preserves_index(self):
        self.source.write_text('')
        before=self.db.read_bytes()
        with self.assertRaises(ValueError):index_book.build(self.source,self.db,replace=True)
        self.assertEqual(before,self.db.read_bytes())
    def test_txt_format(self):
        f=self.root/'中文 空格.txt';f.write_text('第一章 测试\n中文内容。\n第二章 行动\n行动需要具体目标。')
        result=index_book.build(f,self.root/'text.db');self.assertEqual(result['total_chapters'],'2')
    def test_epub_spine_and_inline(self):
        f=self.root/'book.epub'
        with zipfile.ZipFile(f,'w') as z:
            z.writestr('META-INF/container.xml','<container><rootfiles><rootfile full-path="OPS/book.opf"/></rootfiles></container>')
            z.writestr('OPS/book.opf','<package><manifest><item id="a" href="z.xhtml"/><item id="b" href="a.xhtml"/></manifest><spine><itemref idref="a"/><itemref idref="b"/></spine></package>')
            z.writestr('OPS/z.xhtml','<html><body><h1>第一章</h1><p>必须保留<strong>嵌套重点</strong>和后面的文字。</p><script>BAD_SCRIPT</script></body></html>')
            z.writestr('OPS/a.xhtml','<html><body><h1>第二章</h1><p>第二段文字。</p></body></html>')
        db=self.root/'epub.db';index_book.build(f,db);rows,_=core.load_book(db)
        self.assertEqual(rows[0]['chapter'],'第一章');self.assertIn('嵌套重点和后面',rows[0]['content']);self.assertNotIn('BAD_SCRIPT',str(rows))
    def test_pagination(self):
        self.source.write_text('# A\n'+'甲乙丙丁'*800);index_book.build(self.source,self.db,replace=True)
        r=read_book.read(self.db,'c0001',limit=2);self.assertEqual(r['next_offset'],2)
        self.assertNotEqual(r['chunks'][0]['chunk_id'],read_book.read(self.db,'c0001',offset=2,limit=2)['chunks'][0]['chunk_id'])
    def test_hybrid_fusion_and_service_failure(self):
        # Controlled vectors exercise storage/fusion; not a real BGE-M3 semantic benchmark.
        with patch('build_vectors.embed',return_value=[[1.,0.],[0.,1.]]):build_vectors.build(self.db)
        with patch('search.embed',return_value=[[0.,1.]]):
            r=search.search('zyxnonexistent',self.db,mode='hybrid');self.assertEqual(r['actual_mode'],'hybrid');self.assertEqual(r['results'][0]['chapter_id'],'c0002')
        with patch('search.embed',side_effect=TimeoutError('offline')):
            r=search.search('反馈',self.db,mode='hybrid');self.assertEqual(r['actual_mode'],'bm25');self.assertIn('offline',r['warning'])
    def test_failed_vectors_preserve_bm25(self):
        with patch('build_vectors.embed',side_effect=TimeoutError()):
            with self.assertRaises(TimeoutError):build_vectors.build(self.db)
        self.assertTrue(search.search('反馈',self.db,mode='bm25')['results'])
    def test_legacy_search(self):
        db=self.root/'old.db'
        with sqlite3.connect(db) as c:
            c.execute('CREATE TABLE chunks(chunk_id INTEGER,para_id INTEGER,chapter TEXT,content TEXT)');c.execute('INSERT INTO chunks VALUES(0,0,"chapter1","反馈需要具体。")')
        self.assertEqual(search.search('反馈',db)['source_status'],'legacy_index')
        with self.assertRaises(ValueError):study.new(self.c,db,'reading')

class LearningTests(Base):
    def test_hidden_answer(self):
        q=self.quiz();visible=study.get_quiz(self.c,self.reading,q['quiz'])['questions'][0]
        for field in ('correct','explanation','evidence','rubric'):self.assertNotIn(field,visible)
        self.assertIn('correct',study.get_quiz(self.c,self.reading,q['quiz'],True)['questions'][0])
    def test_fake_quote_rejected(self):
        q=self.question();q['evidence'][0]['quote']='原文没有的句子'
        with self.assertRaises(ValueError):study.add_quiz(self.c,self.reading,{'questions':[q]})
    def test_cross_chapter_rejected(self):
        q=self.question();q['chapter_id']='c0002'
        with self.assertRaises(ValueError):study.add_quiz(self.c,self.reading,{'questions':[q]})
    def test_mode_isolation(self):
        q=self.quiz();self.respond(q,value=True)
        self.assertEqual(study.review(self.c,self.exam)['items'],[])
        with self.assertRaises(ValueError):study.get_quiz(self.c,self.exam,q['quiz'])
    def test_reading_no_mock(self):
        with self.assertRaises(ValueError):study.add_quiz(self.c,self.reading,{'kind':'mock','questions':[self.question()]})
        with self.assertRaises(ValueError):self.quiz(typ='single')
    def test_correct_without_reason_not_mastery(self):
        r=self.respond(self.quiz(),reason='');self.assertEqual(r['results'][0]['verdict'],'partial');self.assertNotIn('score', {k:v for k,v in r.items() if v is not None})
    def test_reading_no_score(self):
        q=self.quiz(typ='open')
        with self.assertRaises(ValueError):self.respond(q,value='我的看法',ev=assessment(score=1))
    def test_reason_assessed_and_disagreement_allowed(self):
        r=self.respond(self.quiz(typ='open'),value='我理解作者的目的，但我认为反例可以改进原有框架。',ev=assessment())
        self.assertEqual(r['results'][0]['verdict'],'understood');self.assertIsNone(r['results'][0]['score'])
    def test_open_no_automatic_grading(self):
        q=self.quiz(typ='open')
        with self.assertRaises(ValueError):self.respond(q,value='我的答案')
    def test_reading_wrong_reason_not_mastery(self):
        r=self.respond(self.quiz(),ev=assessment('needs_review'));self.assertTrue(r['results'][0]['correct']);self.assertEqual(r['results'][0]['verdict'],'needs_review')
    def test_objective_score_and_partial(self):
        a=self.question('single');b=self.question('multiple');b['id']='q2'
        q=study.add_quiz(self.c,self.exam,{'questions':[a,b]})
        r=self.respond(q,self.exam,'B');self.assertEqual(r['score'],1);self.assertFalse(r['score_is_final']);self.assertEqual(r['answered'],1)
        r=study.answer(self.c,self.exam,q['quiz'],[{'question_id':q['questions'][1]['id'],'answer':['C','A']}])
        self.assertTrue(r['score_is_final']);self.assertEqual(r['score'],2)
    def test_no_multiple_partial_credit(self):
        r=self.respond(self.quiz(self.exam,'multiple'),self.exam,['A']);self.assertEqual(r['score'],0)
    def test_exam_open_pending(self):
        r=self.respond(self.quiz(self.exam,'open'),self.exam,'解释',assessment());self.assertTrue(r['completed']);self.assertFalse(r['score_is_final'])
    def test_score_range(self):
        q=self.quiz(self.exam,'open')
        for n in (2,-1,float('nan'),True):
            with self.assertRaises(ValueError):self.respond(q,self.exam,'解释',assessment(score=n))
    def test_atomic_answers(self):
        q=self.quiz()
        with self.assertRaises(ValueError):study.answer(self.c,self.reading,q['quiz'],[{'question_id':q['questions'][0]['id'],'answer':False},{'question_id':'invalid','answer':False}])
        self.assertEqual(study.quiz_summary(self.c,self.reading,q['quiz'])['answered'],0)
    def test_resume_progress(self):
        study.progress(self.c,self.reading,'c0001','read','需要澄清')
        self.c.close();self.c=study.database(self.root/'state');self.assertEqual(study.status(self.c,self.reading)['progress'][0]['note'],'需要澄清')
    def test_retest_resolution_and_reopened(self):
        q=self.quiz();self.respond(q,value=True)
        q2=self.quiz();self.respond(q2,ev=assessment())
        study.resolve(self.c,self.reading,q['questions'][0]['id'],q2['questions'][0]['id'])
        self.assertFalse(study.review(self.c,self.reading)['items'])
        self.respond(q,value=True);self.assertEqual(len(study.review(self.c,self.reading)['items']),1)
        self.assertEqual(self.c.execute('SELECT count(*) FROM attempts').fetchone()[0],3)
    def test_other_concept_cannot_resolve(self):
        q=self.quiz();self.respond(q,value=True)
        other=self.question();other['concept']='其他考点';q2=study.add_quiz(self.c,self.reading,{'questions':[other]});self.respond(q2,ev=assessment())
        with self.assertRaises(ValueError):study.resolve(self.c,self.reading,q['questions'][0]['id'],q2['questions'][0]['id'])

if __name__=='__main__':unittest.main(verbosity=2)

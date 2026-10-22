"""Run both modes through the actual CLI; fixtures are authored test material, not real learner data."""
import tempfile
import json
import subprocess
import sys
from pathlib import Path
from test_skill import ROOT,TEXT,assessment

temporary=tempfile.TemporaryDirectory(prefix='reading-rehearsal-');out=Path(temporary.name)
source=out/'测试教材.md';source.write_text(TEXT);db=out/'book.db';record=out/'local-state';log=[]

def call(script,*args):
    p=subprocess.run([sys.executable,str(ROOT/'scripts'/script),*map(str,args)],capture_output=True,text=True,check=True)
    result=json.loads(p.stdout);log.append({'script':script,'arguments':list(map(str,args)),'result':result});return result

def study(*args):return call('study.py','--root',record,*args)
def save(name,obj):
    f=out/name;f.write_text(json.dumps(obj,ensure_ascii=False,indent=2));return f

call('index_book.py',source,db,'--title','有效反馈与检验理解·自编测试教材')
chapters=call('read_book.py',db,'--chapters')
rows=call('read_book.py',db,'--chapter','c0001')['chunks'];quote='反馈的目的是帮助改进行动，而不是要求接受者无条件服从。'
evidence=[{'chunk_id':rows[0]['chunk_id'],'quote':quote}]
reading=study('new','--db',db,'--mode','reading','--goal','精读，不参加考试')['session']
questions=[
 {'id':'1','type':'judgment','prompt':'有效反馈的目的，是让接受者无条件服从。请判断并说明理由。','concept':'反馈目的','correct':False,'explanation':'原文把目的界定为改进行动。','evidence':evidence},
 {'id':'2','type':'judgment','prompt':'只说“你很聪明”，未必构成有效反馈。请说明理由。','concept':'具体反馈','correct':True,'explanation':'只赞美聪明没有指出改善方式。','evidence':[{'chunk_id':0,'quote':'只赞美一个人聪明，并没有告诉他如何改善。'}]},
 {'id':'3','type':'open','prompt':'用自己的话说明有效反馈与评价固定能力的区别。','concept':'反馈目的','rubric':'准确区分行为改善与人格能力评价，允许不同表述。','evidence':evidence},
 {'id':'4','type':'open','prompt':'你是否认同“反馈需要及时”？举一个支持或反对的场景，说明条件。','concept':'反馈时机','rubric':'能理解作者观点，举例与条件成立即可，合理异议不判错。','evidence':[{'chunk_id':0,'quote':'有效反馈需要具体、及时，并与可调整的行动联系起来。'}]}
]
quiz=study('add-quiz','--session',reading,'--file',save('reading-quiz.json',{'questions':questions}))
assert 'correct' not in json.dumps(quiz['questions'])
responses=[
 {'question_id':quiz['questions'][0]['id'],'answer':False,'reason':'改进行动才是目的，服从不一定有助于改进。','assessment':assessment()},
 {'question_id':quiz['questions'][1]['id'],'answer':True,'reason':''},
 {'question_id':quiz['questions'][2]['id'],'answer':'反馈告诉人如何调整具体行为，能力评价只给人一个标签。','assessment':assessment()},
 {'question_id':quiz['questions'][3]['id'],'answer':'我认同及时通常更有帮助，但当双方情绪激动时，稍后沟通可能更有效；关键是仍然有具体可执行的信息。','assessment':assessment()}
]
r=study('answer','--session',reading,'--quiz',quiz['quiz'],'--file',save('reading-answers.json',{'responses':responses}))
assert r['completed'] and r['results'][1]['verdict']=='partial' and r['results'][3]['verdict']=='understood'
study('progress','--session',reading,'--chapter','c0001','--status','read','--note','复习具体反馈：判断正确但尚未给理由')
study('status','--session',reading)
exam=study('new','--db',db,'--mode','exam','--goal','测试用认证，不声称官方题型')['session']
q={**questions[0],'type':'single','prompt':'哪项最符合有效反馈？','options':{'A':'你就是不擅长这个','B':'指出可观察行为及改善方式'},'correct':'B'}
eq=study('add-quiz','--session',exam,'--file',save('exam-quiz.json',{'questions':[q]}))
r=study('answer','--session',exam,'--quiz',eq['quiz'],'--file',save('exam-answers.json',{'responses':[{'question_id':eq['questions'][0]['id'],'answer':'A'}]}))
assert r['score']==0 and len(study('review','--session',exam)['items'])==1
newq={**q,'prompt':'同事交付延迟时，哪种反馈更符合本章？','options':{'A':'你总是这么差','B':'这次比约定晚了一天，请先确认风险并及时通知'},'correct':'B'}
retry=study('add-quiz','--session',exam,'--file',save('exam-retest.json',{'kind':'review','questions':[newq]}))
study('answer','--session',exam,'--quiz',retry['quiz'],'--file',save('exam-retest-answer.json',{'responses':[{'question_id':retry['questions'][0]['id'],'answer':'B','reason':'描述具体行为，并提出调整行动。'}]}))
study('resolve','--session',exam,'--question',eq['questions'][0]['id'],'--via',retry['questions'][0]['id'])
assert not study('review','--session',exam)['items']
assert len(study('review','--session',reading)['items'])==1
(out/'cli-rehearsal.json').write_text(json.dumps(log,ensure_ascii=False,indent=2))
print(json.dumps({'status':'pass','cli_calls':len(log),'reading_session':reading,'exam_session':exam,'operations':log},ensure_ascii=False))

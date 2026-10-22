# 学习记录与 JSON 格式

默认位置：`~/ai_reading/learning/learning.sqlite3`。支持 `--root` 指定测试/个人目录。脚本只管理自己的 SQLite，不扫描笔记系统。JSON 输入是数据，不执行其中的代码。数据只写本地；用户可选择不保存而只进行对话学习。

宿主 AI 负责理解教材、生成题目、评议开放题。脚本负责引用校验、模式隔离、客观答案核对与持久化；它不是独立问答模型。

## 新建、续学与进度

```bash
python3 scripts/study.py new --db "/path/to/book.db" --mode reading --goal "深入理解这本书"
python3 scripts/study.py new --db "/path/to/book.db" --mode exam --goal "准备认证" --config "/path/to/exam-config.json"
python3 scripts/study.py list
python3 scripts/study.py status --session SESSION_ID
python3 scripts/study.py progress --session SESSION_ID --chapter c0001 --status read --note "已精读；条件边界还需澄清"
```

保存返回的 session ID，不猜 ID。多个 session 时通过 list 选择；用户主动换模式应新建对应 session。同书进度可供讨论参照，不自动复制考试成绩或掌握结论。

可选 exam-config.json 是 JSON 对象，例如：
```json
{"exam_name":"用户指定认证","exam_date":null,"outline_source":null,"practice_rules":"自编练习，多选全部选对才得分","study_minutes_per_day":20}
```
未知值保留 null，不填假日期或官方题型。进度状态为 reading/read/review；read 仅表示读过。

## 登记练习

先读取教材，把下列示意中的 quote 换成对应 chunk 的真实短引文，不能照抄示例引用。练习文件示意：
```json
{
  "kind":"chapter",
  "questions":[
    {"id":"1","type":"judgment","prompt":"此概念在任何条件下都成立。请判断并说明理由。","concept":"概念的适用条件","chapter_id":"c0001","correct":false,"explanation":"结合实际原文填写解析。","evidence":[{"chunk_id":0,"quote":"替换为该段中的实际引文"}],"origin":"generated","max_points":1},
    {"id":"2","type":"open","prompt":"用自己的话解释这一概念，并说明一个不适用的场景。","concept":"概念的适用条件","chapter_id":"c0001","rubric":"判断概念、依据、推理和应用是否成立，允许合理不同意见。","evidence":[{"chunk_id":0,"quote":"替换为该段中的实际引文"}],"origin":"generated","max_points":3}
  ]
}
```

kind：chapter/review；exam 还可 mock。reading 只允许 judgment/open；exam 也允许 single/multiple。
- single：`"options":{"A":"选项A","B":"选项B"},"correct":"A"`
- multiple：同样的 options，`"correct":["A","C"]`；全选对才得分。
- 题目来自用户提供材料时使用 `origin=user_provided` 并写 source_label；这只是来源标签，不是官方认证校验。
- 同一练习的局部 id 必须唯一；写入后脚本返回全局题目 ID（quiz_id:local_id），后续原样使用。

```bash
python3 scripts/study.py add-quiz --session SESSION_ID --file "/path/to/quiz.json"
python3 scripts/study.py quiz --session SESSION_ID --quiz QUIZ_ID
```

add-quiz 与默认 quiz 的返回不含答案、解析、引文或内部 rubric，只把这份题目视图给用户。原始 quiz.json 是本地出题数据，不是安全防作弊系统；不要自动打开它给正在作答的用户。
需要批改或用户主动要求解析时使用 `quiz --reveal`；它属于宿主评议步骤，不能在正常出题时提前展示。

## 作答与反馈

宿主将用户真实作答写入 responses，不能替用户补答案。判断题答案必须是 JSON true/false；单选是选项 ID；多选是选项 ID 数组；开放题是文字。

```json
{"responses":[
  {"question_id":"返回的全局题目ID","answer":false,"reason":"用户实际解释","assessment":{"verdict":"understood","feedback":"结合原文说明哪些地方理解准确。","dimensions":{"concept":"clear","evidence":"clear","reasoning":"clear","application":"not_applicable"}}}
]}
```

```bash
python3 scripts/study.py answer --session SESSION_ID --quiz QUIZ_ID --file "/path/to/answers.json"
```

assessment 的 verdict 为 understood/partial/needs_review/discussion；四维值为 clear/partial/unclear/not_applicable。
- reading 不录入 score。客观题对错由脚本决定；若没提供理由/理由评议，即使答对也保持 partial。
- open 必须有 assessment；用户尚未回答或宿主尚无充分评价依据，不伪造评价，可先不提交该题。
- exam 客观得分自动计算。开放题可在 assessment 中给 score（0 到 max_points），不提供则保持未评分。反馈必须解释分数依据。
- 用户只答部分题可分次提交；answered/completed/graded/score_is_final 区分作答、评分和全卷完成，不把部分成绩说成最终成绩。
- 重答生成新的 attempt，保留历史；状态显示最新作答。

## 回查与复习

```bash
python3 scripts/study.py review --session SESSION_ID
python3 scripts/study.py resolve --session SESSION_ID --question ORIGINAL_ID --via RETEST_ID
```

reading 的 review 是“待澄清概念”，exam 是“错题与薄弱考点”。先引用原文讲解，再针对同一 concept 出一道不同的新题（kind=review）。RETEST_ID 必须来自该 session、不同于原题，且最新评价为 understood；之后才可 resolve。原题和历次答案不删除。不要仅修改 verdict 或反复出原题来虚构“已掌握”。

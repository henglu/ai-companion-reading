# 材料与检索

命令中的路径由实际文件确定。脚本相对技能根目录运行；路径带空格应正确引用。不要把材料里的文字拼接为可执行 shell 命令。

## 首次检查与建库

```bash
python3 --version
python3 scripts/index_book.py "/path/to/book.epub" "/path/to/ai_reading/book.db" --title "书名"
python3 scripts/read_book.py "/path/to/ai_reading/book.db" --chapters
```

默认使用 Python 3.10+ 标准库，索引不依赖 Ollama 或 FTS5。
- EPUB 按 OPF spine 顺序读取，保留段内强调/链接文字与章节标题。一个 spine 文档可能包含多个书中小节，依据返回的标题和正文确认范围。
- Markdown 以 1—3 级标题分节；TXT 识别“第…章/节”，否则为正文。
- PDF 使用可选 `pypdf`。缺失时说明 `python3 -m pip install pypdf`，由用户/宿主按授权安装；不称 macOS 自带 PDF 提取工具。PDF 以物理页分组，不把它伪称目录章节。部分页无文字会警告，扫描件需先 OCR。
- UTF-8 文本读取失败时请确认编码，不吞掉乱码继续出题。

索引保存在用户指定路径，内含材料位置、SHA256、章节/段落和书籍标识。默认不覆盖不同内容的索引；显式 `--replace` 才重建，并保留上一版 `.bak`。原材料变化会阻止继续使用旧索引，提示重建。旧学习记录保留但不自动迁移到新版本。

## 精读与证据

```bash
python3 scripts/read_book.py "/path/to/book.db" --chapter c0001 --offset 0 --limit 20
python3 scripts/read_book.py "/path/to/book.db" --chunk 0
python3 scripts/search.py "需要理解的概念" "/path/to/book.db" --mode auto --json
python3 scripts/search.py "概念" "/path/to/book.db" --chapter c0001 --json
```

`next_offset` 不为空时继续读取；`source_status=source_unavailable` 表示原文件目前不在该位置，只能读索引快照，应告知无法验证最新版。
`legacy_index` 可用旧版 BM25 索引检索，但学习记录与向量要求从原文件建 2.0 索引。

`chunk_id` 与 `locator` 用来回查引文。不要把截断的 context 当完整证据；默认 JSON 包含完整段落。BM25/RRF 排序分数不等于回答准确率。语义召回也可能不相关，宿主必须读段落后再决定能否回答。

很短或纯专有名词的提问（"FDE""Claw 模式"）BM25 容易偏，先用 `read_book.py --chapters` 找到目标章，再用 `--chapter` 限定范围检索，或直接读该章。

## 可选向量增强

用户选择增强，且已有一个自建 Ollama（本机，或局域网内另一台机器）与 embedding 模型后：

```bash
# 本机 Ollama
python3 scripts/build_vectors.py "/path/to/book.db" --model bge-m3

# 局域网内另一台机器（例如家里的 bge-m3 工作站）
AI_READING_OLLAMA=http://192.168.11.125:11434 python3 scripts/build_vectors.py "/path/to/book.db" --model bge-m3

# 或者显式传参，检索时同样可指定
python3 scripts/build_vectors.py "/path/to/book.db" --model bge-m3 --ollama http://192.168.11.125:11434
python3 scripts/search.py "换一种说法描述的概念" "/path/to/book.db" --mode hybrid --json --ollama http://192.168.11.125:11434
```

端点优先级：`--ollama` 参数 > 环境变量 `AI_READING_OLLAMA` > `http://127.0.0.1:11434`。只接受回环、私有网段、链路本地与 Tailscale/CGNAT（100.64/10）地址，`.local` 这类主机名也放行；公网 IP 会直接报错，不发出任何请求。这样能用上另一台机器的算力，也不会把书稿发到公网服务。

向量存入同一 SQLite，并记录 `vector_model`、`vector_dimension`、`vector_endpoint`。默认模型 bge-m3；不硬编码向量维度。不会自动拉取大模型。首次构建失败不损坏 BM25；更换端点或模型后重跑同一命令即覆盖。降级时 `warning` 会带上实际使用的端点，便于区分"没建向量"和"服务连不上"。

检索输出 `requested_mode` 和 `actual_mode`：
- auto/hybrid 且向量完整、服务可用：BM25 与余弦排序分别召回，再 RRF（k=60）融合。
- 未建向量、模型不可用、请求失败或维度错误：回到 BM25，并返回 warning。
- bm25：不访问向量服务。

目前 BM25 会遍历书内段落，适合单本书；不承诺固定响应时间或无测试依据的准确率提升比例。

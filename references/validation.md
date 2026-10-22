# 故障恢复与验证

## 常见故障

| 情况 | 处理 |
| --- | --- |
| EPUB 损坏或结构异常 | 用阅读器验证，重新导出 EPUB 或转换为 TXT/Markdown；保留原文件和已有索引 |
| TXT/Markdown 编码错误 | 另存为 UTF-8 后导入，不吞掉乱码继续出题 |
| PDF 无文字或部分页无文字 | 先做 OCR，确认文字覆盖范围后再出题 |
| PDF 加密或解析失败 | 使用有权限的工具解密或重新导出文字层 PDF |
| 原文件改变 | 显式 --replace 重建；保留 .bak，旧学习记录不自动迁移 |
| Ollama 未启动或超时 | 检查服务、模型与端口；先继续用 BM25 |
| 地址被拒绝 | 改用本机/自有内网服务直接地址；域名所有解析结果须在允许网段；不绕过限制 |
| 模型/API 未找到 | 确认已安装所选 embedding 模型及支持 /api/embed 的 Ollama；不自动安装 |

## 可复现测试

在技能根目录执行：

```bash
python3 -m pip install -r tests/requirements.txt
python3 -m unittest discover -s tests -v
python3 tests/demo_workflows.py
```

测试材料为 tests/test_skill.py 中自编的两章教材，不含用户学习记录。测试在临时目录运行，不修改用户书籍。测试依赖仅用于验证；日常 EPUB/TXT/Markdown、BM25 和学习记录不需要这些依赖。

测试覆盖：原文定位、章节分页、阅读与备考隔离、开放题不自动评分、错题复测、索引过期、文件异常、地址校验、重定向阻止、向量失败后降级。HTTP 合同测试使用本地模拟向量服务，不证明真实 bge-m3 的语义质量。

## 真实模型验证（可选）

用户已有自建 Ollama/bge-m3 后，用自编教材运行 build_vectors.py，再用 search.py --mode hybrid --json 检查 actual_mode=hybrid、模型与来源位置。人工检查改写查询的结果是否相关。停止服务后重查，应 actual_mode=bm25 且有明确 warning。记录运行环境、模型、查询、返回片段与结果，不将混合检索成功等同于准确率保证。

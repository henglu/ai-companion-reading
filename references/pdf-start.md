# PDF 第一次使用

## 先检查，再继续原任务

使用接下来实际运行索引脚本的同一 Python，技能根目录执行：

```bash
python3 scripts/check_environment.py --pdf
```

检查不联网、不安装软件、不改文件。

- `status=ready` 且 `pdf_ready=true`：直接用同一解释器运行原来的 PDF 建库命令。
- `pdf_dependency_unavailable`：先告诉用户：“还差一个 PDF 读取组件。装好后我会继续处理这份文件，阅读目标和进度不用重填。你也可以先提供 EPUB、TXT 或 Markdown。”
- `python_upgrade_needed`：切换到 Python 3.10+，重新检查。

## 安装与验证

检查结果提供 `install_command` 和 `verify_command`，路径针对当前解释器生成，含空格时也会正确引用。不要换到另一套 Python 安装。

在用户已授权安装且宿主允许的环境，执行返回的安装命令；否则先说明需要安装 `pypdf` 及用途，取得安装授权。用户此前已明确授权时不重复询问。执行后运行返回的验证命令；只有验证成功才重试原来的导入命令。脚本本身不会自动安装依赖。

若提示 `externally-managed-environment` 或权限不足，不使用 sudo 或 `--break-system-packages`。在用户选择的可写工作目录创建独立环境：

```bash
python3 -m venv .reading-venv
# macOS / Linux
.reading-venv/bin/python -m pip install 'pypdf>=5,<7'
.reading-venv/bin/python /技能目录/scripts/check_environment.py --pdf
# Windows 对应解释器：.reading-venv\Scripts\python.exe
```

把示例路径换成实际技能路径。后续建库与学习脚本也使用这个新解释器，无需激活全局环境。若 pip/venv 不可用，选择已有的可用 Python 环境，或让用户按其软件管理方式安装；不要循环尝试相同失败命令。

安装下载失败：保留原文件和当前任务，提示检查网络/软件源；不擅自更换未知镜像。修复后重新验证。暂时不能安装时，可以继续使用文本格式材料。

## 装好仍然不能读？

- 扫描图片 PDF：组件安装不能代替 OCR。先获得文字层，再导入。
- 加密 PDF：使用有权限的工具解密或导出可读取版本。
- 文件损坏：先用普通阅读器打开验证，重新下载或导出。
- 部分页没有文字：先明确哪些页可读，不能宣称已覆盖整章或全书。

用户可见的结果应是：“组件已就绪，已继续读取你刚才提供的文件”，并附真实返回的页数或章节范围；没有完成建库时不要提前说“可以开始本章测试”。

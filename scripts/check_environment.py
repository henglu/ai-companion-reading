#!/usr/bin/env python3
"""Read-only dependency check; never installs packages or contacts a service."""
import argparse
import importlib
import json
import os
import shlex
import subprocess
import sys


def command(args):
    return subprocess.list2cmdline(args) if os.name == 'nt' else shlex.join(args)


def inspect(pdf=False):
    result = {'python': sys.executable, 'python_version': '.'.join(map(str,sys.version_info[:3])),
              'status': 'ready', 'basic_ready': sys.version_info >= (3,10),
              'pdf_requested': pdf, 'network_used': False, 'installed_anything': False}
    if not result['basic_ready']:
        result.update(status='python_upgrade_needed', message='需要 Python 3.10 或更新版本；请切换解释器后重新检查。')
        return result
    if not pdf:
        result['message']='可以开始阅读 EPUB、UTF-8 TXT 或 Markdown，无需安装 Ollama。'
        return result
    install = command([sys.executable,'-m','pip','install','pypdf>=5,<7'])
    retry = command([sys.executable,os.path.abspath(__file__),'--pdf'])
    try:
        module=importlib.import_module('pypdf')
        if not callable(getattr(module,'PdfReader',None)):
            raise ImportError('PdfReader unavailable')
        result.update(pdf_ready=True, pdf_version=getattr(module,'__version__','unknown'),
                      message='PDF 读取组件已就绪，可以继续导入原来的 PDF。扫描图片仍需先做 OCR。')
    except Exception as exc:
        result.update(status='pdf_dependency_unavailable',pdf_ready=False,
                      message='当前环境还不能读取 PDF。请安装或修复 PDF 读取组件；EPUB、TXT、Markdown 仍可使用。',
                      install_command=install, verify_command=retry,
                      diagnostic=type(exc).__name__,
                      next_step='在允许安装依赖的环境执行 install_command，成功后运行 verify_command，再重试原来的 PDF 导入。')
    return result


def pdf_help():
    data=inspect(pdf=True)
    return data['message']+' 安装命令：'+data.get('install_command',command([sys.executable,'-m','pip','install','pypdf>=5,<7']))+'；安装后重新运行原来的导入命令。'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pdf',action='store_true',help='检查 PDF 可选依赖')
    args=parser.parse_args();data=inspect(args.pdf)
    print(json.dumps(data,ensure_ascii=False,indent=2))
    return 0 if data['status']=='ready' else 2

if __name__=='__main__':sys.exit(main())

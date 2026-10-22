import json
import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from test_skill import Base, ROOT
import check_environment as env

class EnvironmentTests(Base):
    def test_basic_needs_no_pdf(self):
        with patch.object(env.importlib,'import_module') as load:
            self.assertEqual(env.inspect()['status'],'ready');load.assert_not_called()
    def test_pdf_present(self):
        with patch.object(env.importlib,'import_module',return_value=SimpleNamespace(PdfReader=lambda:None,__version__='6.0')):
            d=env.inspect(True);self.assertTrue(d['pdf_ready']);self.assertNotIn('install_command',d)
    def test_missing_and_broken_give_next_step(self):
        for error in (ImportError(),RuntimeError()):
            with patch.object(env.importlib,'import_module',side_effect=error):
                d=env.inspect(True);self.assertFalse(d['pdf_ready']);self.assertIn('pip',d['install_command']);self.assertIn(sys.executable,d['install_command']);self.assertIn('--pdf',d['verify_command'])
    def test_incomplete_package(self):
        with patch.object(env.importlib,'import_module',return_value=SimpleNamespace()):
            self.assertEqual(env.inspect(True)['status'],'pdf_dependency_unavailable')
    def test_check_readonly(self):
        with patch.object(env.subprocess,'run') as run:
            env.inspect(True);run.assert_not_called()
    def test_unsupported_python(self):
        with patch.object(env.sys,'version_info',(3,9,0)):
            self.assertEqual(env.inspect(True)['status'],'python_upgrade_needed')
    def test_no_site_cli_and_pdf_import(self):
        result=subprocess.run([sys.executable,'-S',str(ROOT/'scripts/check_environment.py'),'--pdf'],capture_output=True,text=True)
        self.assertEqual(result.returncode,2);d=json.loads(result.stdout);self.assertFalse(d['pdf_ready']);self.assertFalse(d['installed_anything'])
        source=self.root/'test.pdf';source.write_bytes(b'%PDF')
        result=subprocess.run([sys.executable,'-S',str(ROOT/'scripts/index_book.py'),str(source),str(self.root/'pdf.db')],capture_output=True,text=True)
        self.assertEqual(result.returncode,1);self.assertIn('安装命令',result.stderr);self.assertNotIn('Traceback',result.stderr);self.assertFalse((self.root/'pdf.db').exists())
    def test_space_path_command(self):
        with patch.object(env.sys,'executable','/tmp/Python With Space/bin/python'),patch.object(env.importlib,'import_module',side_effect=ImportError()):
            d=env.inspect(True)
            if env.os.name!='nt':
                self.assertEqual(env.shlex.split(d['install_command'])[0],'/tmp/Python With Space/bin/python')

if __name__=='__main__':unittest.main(verbosity=2)

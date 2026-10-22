import json
import socket
import subprocess
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch
from test_skill import Base, ROOT
import core, index_book, build_vectors, search

class ReliabilityTests(Base):
    def test_private_ranges(self):
        for ip in ['127.0.0.1','10.0.0.1','172.16.0.1','192.168.1.1','100.64.0.1','::1','fd00::1','::ffff:192.168.1.1']:
            self.assertTrue(core.allowed_address(ip), ip)
        for ip in ['8.8.8.8','0.0.0.0','192.0.2.1','224.0.0.1','::','2001:4860:4860::8888','::ffff:8.8.8.8']:
            self.assertFalse(core.allowed_address(ip),ip)

    def test_dns_public_and_mixed_rejected_before_socket(self):
        def addr(ip):return (socket.AF_INET,socket.SOCK_STREAM,6,'',(ip,11434))
        for records in [[addr('8.8.8.8')],[addr('127.0.0.1'),addr('8.8.8.8')],[]]:
            with patch('core.socket.getaddrinfo',return_value=records), patch('core.socket.socket') as connect:
                with self.assertRaises(ValueError):core.embed(['private book'],base='http://test.invalid:11434')
                connect.assert_not_called()

    def test_reject_credentials_paths_and_bad_port(self):
        for url in ['http://a:b@127.0.0.1','http://127.0.0.1/path','http://127.0.0.1?q=x','http://127.0.0.1#x','ftp://127.0.0.1','http://127.0.0.1:bad']:
            with self.assertRaises(ValueError):core.ollama_base(url)

    def test_dns_failure(self):
        with patch('core.socket.getaddrinfo',side_effect=socket.gaierror()):
            with self.assertRaisesRegex(ValueError,'解析'):core.ollama_base('http://missing.invalid')

    def serve(self,status=200,body=None,redirect=None):
        seen=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*a):pass
            def do_POST(self):
                seen.append(self.rfile.read(int(self.headers['Content-Length'])))
                self.send_response(status)
                if redirect:self.send_header('Location',redirect)
                self.end_headers();self.wfile.write(json.dumps(body).encode())
        server=HTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(lambda:(server.shutdown(),server.server_close(),thread.join()))
        return server,seen

    def test_redirect_never_followed(self):
        target,seen=self.serve(body={'embeddings':[[1,0]]})
        redirect,_=self.serve(status=307,redirect=f'http://127.0.0.1:{target.server_port}')
        with self.assertRaisesRegex(ValueError,'重定向'):
            core.embed(['secret'],base=f'http://127.0.0.1:{redirect.server_port}')
        self.assertEqual(seen,[])

    def test_dns_pinned_and_proxy_ignored(self):
        server,seen=self.serve(body={'embeddings':[[1,0]]})
        record=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',server.server_port))]
        with patch('core.socket.getaddrinfo',return_value=record) as dns, patch.dict('os.environ',{'http_proxy':'http://8.8.8.8:99'}):
            result=core.embedding_response('http://book.test:11434',b'{}',2)
            self.assertEqual(result['embeddings'],[[1,0]])
            self.assertEqual(dns.call_count,1)
        self.assertEqual(len(seen),1)

    def test_http_error_preserves_bm25(self):
        server,_=self.serve(status=404)
        with self.assertRaisesRegex(ValueError,'模型'):
            build_vectors.build(self.db,base=f'http://127.0.0.1:{server.server_port}')
        self.assertTrue(search.search('反馈',self.db,mode='bm25')['results'])

    def test_invalid_vector_types(self):
        for value in [None,{},[None],[[True]],[[float('nan')]]]:
            with patch('core.embedding_response',return_value={'embeddings':value}):
                with self.assertRaises(ValueError):core.embed(['text'])

    def test_rebinding_revalidated_before_send(self):
        local=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',11434))]
        public=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('8.8.8.8',11434))]
        with patch('core.socket.getaddrinfo',side_effect=[local,public]), patch('core.socket.socket') as connect:
            with self.assertRaises(ValueError):core.embed(['private text'],base='http://changing.test:11434')
            connect.assert_not_called()

    def test_https_checks_original_hostname(self):
        records=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',443))]
        with patch('core.socket.getaddrinfo',return_value=records), patch('core.socket.socket') as sock, patch('core.ssl.create_default_context') as tls, patch('core.http.client.HTTPConnection') as conn:
            response=conn.return_value.getresponse.return_value
            response.status=200;response.read.return_value=b'{"embeddings":[[1,0]]}'
            core.embedding_response('https://internal.test',b'{}',2)
            sock.return_value.connect.assert_called_once_with(('127.0.0.1',443))
            tls.return_value.wrap_socket.assert_called_once_with(sock.return_value,server_hostname='internal.test')

    def test_encrypted_pdf_actionable(self):
        from pypdf import PdfWriter
        pdf=self.root/'locked.pdf';writer=PdfWriter();writer.add_blank_page(width=100,height=100);writer.encrypt('secret')
        with pdf.open('wb') as f:writer.write(f)
        with self.assertRaisesRegex(ValueError,'加密'):index_book.build(pdf,self.root/'locked.db')
        self.assertFalse((self.root/'locked.db').exists())

    def test_timeout_actionable(self):
        with patch('core.socket.socket') as cls:
            cls.return_value.connect.side_effect=TimeoutError()
            with self.assertRaisesRegex(ValueError,'超时'):core.embed(['text'])

    def test_corrupt_epub_cli(self):
        p=self.root/'bad.epub';p.write_bytes(b'broken')
        result=subprocess.run([sys.executable,str(ROOT/'scripts/index_book.py'),str(p),str(self.root/'new.db')],capture_output=True,text=True)
        self.assertEqual(result.returncode,1);self.assertIn('重新导出',result.stderr)
        self.assertNotIn('Traceback',result.stderr);self.assertFalse((self.root/'new.db').exists())

    def test_bad_encoding_cli(self):
        p=self.root/'bad.txt';p.write_bytes(b'\xff\xfe\xff')
        result=subprocess.run([sys.executable,str(ROOT/'scripts/index_book.py'),str(p),str(self.root/'new.db')],capture_output=True,text=True)
        self.assertEqual(result.returncode,1);self.assertIn('UTF-8',result.stderr);self.assertNotIn('Traceback',result.stderr)

    def test_corrupt_pdf_cli(self):
        p=self.root/'bad.pdf';p.write_bytes(b'%PDF broken')
        result=subprocess.run([sys.executable,str(ROOT/'scripts/index_book.py'),str(p),str(self.root/'new.db')],capture_output=True,text=True)
        self.assertEqual(result.returncode,1);self.assertNotIn('Traceback',result.stderr)

if __name__=='__main__':unittest.main(verbosity=2)

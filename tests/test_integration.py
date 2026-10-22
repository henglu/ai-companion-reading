import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler,HTTPServer
from pathlib import Path
from unittest.mock import patch
from test_skill import Base,ROOT
import core,index_book,build_vectors,search

class IntegrationTests(Base):
    def test_real_pdf_text_pages(self):
        from reportlab.pdfgen import canvas
        pdf=self.root/'book.pdf';c=canvas.Canvas(str(pdf));c.drawString(72,720,'Feedback describes observable behavior.');c.showPage();c.drawString(72,720,'Transfer requires checking the new conditions.');c.save()
        db=self.root/'pdf.db';r=index_book.build(pdf,db);self.assertEqual(r['total_chapters'],'2')
        hit=search.search('Transfer',db,mode='bm25')['results'][0]
        self.assertEqual(hit['chapter'],'PDF 第 2 页');self.assertIn('物理页 2',hit['locator'])
    def test_empty_pdf_rejected(self):
        from reportlab.pdfgen import canvas
        pdf=self.root/'empty.pdf';c=canvas.Canvas(str(pdf));c.showPage();c.save()
        with self.assertRaises(ValueError):index_book.build(pdf,self.root/'empty.db')
    def test_ollama_http_contract(self):
        requests=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*a):pass
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length'])));requests.append((self.path,body))
                vs=[[1.,0.,0.] if '反馈' in text else [0.,1.,0.] for text in body['input']]
                raw=json.dumps({'model':body['model'],'embeddings':vs}).encode()
                self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        server=HTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with patch('core.OLLAMA',f'http://127.0.0.1:{server.server_port}'):
                built=build_vectors.build(self.db);self.assertEqual(built['dimension'],3)
                result=search.search('反馈',self.db,mode='hybrid');self.assertEqual(result['actual_mode'],'hybrid')
                self.assertTrue(all(path=='/api/embed' and body['truncate'] is False for path,body in requests))
                self.assertEqual(result['results'][0]['chapter_id'],'c0001')
        finally:server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main(verbosity=2)

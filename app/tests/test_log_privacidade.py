import io
import logging
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from flask import Flask, request, abort
from log_privacidade import sanitizar, configurar_logs, proteger_logger

TOKEN = 'TOKEN-SINTETICO-NAO-REAL-12345'

class PrivacidadeTest(unittest.TestCase):
    def test_rotas_derivadas(self):
        for suffix in ('','/modulo/1','/modulo/1/heartbeat','/iniciar','/modulo/1/avancar','/conclusao','/quiz','/feedback'):
            with self.subTest(suffix=suffix):
                self.assertEqual(sanitizar('/aprender/t/'+TOKEN+suffix), '/aprender/t/[REDACTED]'+suffix)
    def test_query_e_referer(self):
        text='GET /aprender/t/'+TOKEN+'/quiz?q='+TOKEN+' Referer: https://host/aprender/t/'+TOKEN+'?c='+TOKEN
        self.assertNotIn(TOKEN,sanitizar(text))
        self.assertNotIn(TOKEN,sanitizar('/outra?credencial='+TOKEN))
    def test_query_com_email_nao_perde_delimitador(self):
        bearer='00000000-0000-4000-8000-000000000123'
        email='marcador@synthetic.invalid';key='API-SINTETICA-NAO-REAL';query='QUERY-SINTETICA-NAO-REAL'
        for qs in (f'key={key}&email={email}&q={query}',f'email={email}&q={query}&key={key}',f'q={query}&key={key}&email={email}'):
            for prefix in ('/simulacao/educacao/'+bearer,'/aprender/t/'+TOKEN,'https://one.test/status'):
                with self.subTest(prefix=prefix,query_order=qs):
                    cleaned=sanitizar('GET '+prefix+'?'+qs+' HTTP/1.1')
                    for marker in (bearer,TOKEN,email,key,query):self.assertNotIn(marker,cleaned)
                    self.assertIn('?[REDACTED]',cleaned)

    def test_encoded(self):
        self.assertEqual(sanitizar('%2Faprender%2Ft%2F'+TOKEN+'%2Fquiz'),'/aprender/t/[REDACTED]%2Fquiz')
        self.assertNotIn(TOKEN,sanitizar('/aprender/t/%54OKEN-SINTETICO-NAO-REAL-12345'))
    def test_prefixo_encoded(self):
        self.assertNotIn(TOKEN,sanitizar('/%61prender/%74/'+TOKEN))
        self.assertNotIn(TOKEN,sanitizar('/%61prender/t/'+TOKEN+'%ZZ'))
    def test_idempotente(self):
        x=sanitizar('/aprender/t/'+TOKEN+'/feedback?q=x')
        self.assertEqual(sanitizar(x),x)
    def test_outras_rotas(self):
        self.assertEqual(sanitizar('GET /convites/ HTTP/1.1 200'), 'GET /convites/ HTTP/1.1 200')
    def test_flask_intacto_e_erro(self):
        app=Flask(__name__); app.config.update(TESTING=False,PROPAGATE_EXCEPTIONS=False)
        stream=io.StringIO(); handler=logging.StreamHandler(stream); app.logger.addHandler(handler)
        configurar_logs(app)
        seen=[]
        @app.get('/aprender/t/<token>/<int:status>')
        def route(token,status):
            seen.append((token,request.path,request.query_string))
            app.logger.warning('url %s path %s headers %s',request.url,request.path,dict(request.headers))
            if status==500:
                raise RuntimeError('Credencial '+token+' url '+request.url)
            if status!=200: abort(status)
            return 'ok'
        for status in (200,403,404,500):
            r=app.test_client().get('/aprender/t/'+TOKEN+'/'+str(status)+'?q='+TOKEN,headers={'Referer':'https://host/aprender/t/'+TOKEN,'X-Token':TOKEN})
            self.assertEqual(r.status_code,status)
        self.assertTrue(all(t==TOKEN and TOKEN in p and TOKEN.encode() in q for t,p,q in seen))
        self.assertNotIn(TOKEN,stream.getvalue())
        self.assertIn('RuntimeError',stream.getvalue()); self.assertIn('[REDACTED]',stream.getvalue())
        app.logger.removeHandler(handler)
    def test_gunicorn_erro_fora_contexto(self):
        logger=logging.getLogger('privacy-test-gunicorn'); stream=io.StringIO(); handler=logging.StreamHandler(stream)
        logger.addHandler(handler); proteger_logger(logger)
        try: raise RuntimeError(TOKEN)
        except RuntimeError: logger.error('Error handling request %s','/aprender/t/'+TOKEN,exc_info=True)
        self.assertNotIn(TOKEN,stream.getvalue());self.assertIn('RuntimeError',stream.getvalue())
        logger.removeHandler(handler)
    def test_pipe_e_rotacao(self):
        script=Path(__file__).resolve().parents[1]/'log_privacidade.py'
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'access.log'
            result=subprocess.run([sys.executable,'-I',str(script),'--arquivo',str(path)],input='GET /aprender/t/'+TOKEN+'/quiz?token='+TOKEN+' HTTP/1.1 404\nGET /convites/ HTTP/1.1 200\n',text=True,capture_output=True)
            self.assertEqual(result.returncode,0)
            self.assertNotIn(TOKEN,path.read_text());self.assertIn('/convites/',path.read_text())
            self.assertEqual(result.stderr,'')
    def test_pipe_falha_fechada(self):
        script=Path(__file__).resolve().parents[1]/'log_privacidade.py'
        result=subprocess.run([sys.executable,'-I',str(script),'--arquivo','/inexistente/log'],input=TOKEN,text=True,capture_output=True)
        self.assertEqual(result.returncode,1);self.assertNotIn(TOKEN,result.stdout+result.stderr)

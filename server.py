import argparse, hashlib, hmac, json, os, secrets, sqlite3
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from domain import connect, initialize, change, BusinessError, TRANSITIONS, MESSAGES

ROOT=Path(__file__).parent
READINGS=json.loads((ROOT/'static/name-readings.json').read_text())
SESSIONS={}
DB_PATH=os.environ.get('INQUIRY_DB',str(ROOT/'inquiries.sqlite3'))
ACCOUNTS={}

def seed():
 with connect(DB_PATH) as db:
  db.executemany('INSERT OR IGNORE INTO users(id,name,role,is_active) VALUES(?,?,?,?)',[(1,'佐藤 花子','ADMIN',1),(2,'鈴木 一郎','MEMBER',1),(3,'高橋 美咲','MEMBER',1),(4,'田中 健太','MEMBER',0)])
  db.executemany('INSERT OR IGNORE INTO inquiries(id,title,body,requester_name,status,assignee_id,priority) VALUES(?,?,?,?,?,?,?)',[(1,'プリンタが印刷できません','3階の複合機で印刷しようとすると、エラーメッセージが表示されます。\n昨日までは正常に印刷できていました。確認をお願いします。','山田 太郎','NEW',None,'MIDDLE'),(2,'PCが起動しません','電源ボタンを押しても画面が表示されません。','伊藤 愛','IN_PROGRESS',2,'HIGH'),(3,'ソフトウェアのインストールについて','利用するソフトウェアの情報を確認中です。','加藤 翔','PENDING',3,'LOW')])

class Handler(BaseHTTPRequestHandler):
 def response(self,status,data,headers=None):
  raw=json.dumps(data,ensure_ascii=False).encode()
  self.send_response(status)
  self.send_header('Content-Type','application/json; charset=utf-8')
  self.send_header('Cache-Control','no-store')
  self.send_header('X-Content-Type-Options','nosniff')
  for k,v in (headers or {}).items(): self.send_header(k,v)
  self.end_headers(); self.wfile.write(raw)
 def session(self):
  cookie=SimpleCookie()
  try: cookie.load(self.headers.get('Cookie',''))
  except Exception: return None
  token=cookie.get('inquiry_session')
  return SESSIONS.get(token.value) if token else None
 def do_GET(self):
  path=urlparse(self.path)
  if path.path.startswith('/api/'):
   session=self.session()
   if not session: return self.response(401,{'message':'ログインしてください。'})
   with connect(DB_PATH) as db:
    actor=db.execute('SELECT * FROM users WHERE id=? AND is_active=1',(session['id'],)).fetchone()
    if not actor: return self.response(403,{'message':MESSAGES['permission']})
    if path.path=='/api/me': return self.response(200,{'user':dict(actor),'csrf':session['csrf']})
    if path.path=='/api/inquiries': return self.response(200,[dict(r) for r in db.execute('SELECT i.*,u.name assignee_name FROM inquiries i LEFT JOIN users u ON u.id=i.assignee_id ORDER BY i.id')])
    if path.path=='/api/detail':
     raw=parse_qs(path.query).get('id',['1'])[0]
     row=db.execute('SELECT i.*,u.name assignee_name FROM inquiries i LEFT JOIN users u ON u.id=i.assignee_id WHERE i.id=?',(raw,)).fetchone()
     if not row: return self.response(404,{'message':MESSAGES['missing']})
     histories=[dict(r) for r in db.execute('SELECT h.*,u.name actor_name,o.name old_name,n.name new_name FROM inquiry_histories h JOIN users u ON u.id=h.changed_by LEFT JOIN users o ON h.field_name=\'assignee\' AND o.id=h.old_value LEFT JOIN users n ON h.field_name=\'assignee\' AND n.id=h.new_value WHERE inquiry_id=? ORDER BY h.changed_at DESC,h.id DESC LIMIT 20',(raw,))]
     return self.response(200,{'inquiry':dict(row),'users':[dict(r,reading=READINGS.get(r['name'],r['name'])) for r in db.execute('SELECT id,name FROM users WHERE is_active=1')],'histories':histories,'transitions':TRANSITIONS[row['status']]})
   return self.response(404,{'message':'ページが見つかりません。'})
  filename={'/':'index.html','/app.js':'app.js','/style.css':'style.css','/favicon.svg':'favicon.svg'}.get(path.path)
  if not filename: return self.response(404,{'message':'ページが見つかりません。'})
  data=(ROOT/'static'/filename).read_bytes()
  self.send_response(200)
  self.send_header('Content-Type',{'html':'text/html; charset=utf-8','js':'text/javascript; charset=utf-8','css':'text/css; charset=utf-8','svg':'image/svg+xml'}[filename.split('.')[-1]])
  self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'")
  self.end_headers();self.wfile.write(data)
 def do_POST(self):
  try:
   length=int(self.headers.get('Content-Length','0'))
   if length>8192: return self.response(413,{'message':'送信内容が大きすぎます。'})
   data=json.loads(self.rfile.read(length))
   if not isinstance(data,dict): raise ValueError()
  except (ValueError,TypeError): return self.response(400,{'message':'入力内容を確認してください。'})
  if self.path=='/api/login':
   key=data.get('key','')
   actor=next((uid for stored,uid in ACCOUNTS.items() if isinstance(key,str) and hmac.compare_digest(stored,key)),None)
   if not actor: return self.response(401,{'message':'ログインキーが正しくありません。'})
   token=secrets.token_urlsafe(32); SESSIONS[token]={'id':actor,'csrf':secrets.token_urlsafe(32)}
   return self.response(200,{'ok':True},{'Set-Cookie':f'inquiry_session={token}; HttpOnly; SameSite=Strict; Path=/'+('; Secure' if os.environ.get('COOKIE_SECURE')=='1' else '')})
  session=self.session()
  if not session: return self.response(401,{'message':'ログインしてください。'})
  if not hmac.compare_digest(self.headers.get('X-CSRF-Token',''),session['csrf']): return self.response(403,{'message':MESSAGES['permission']})
  if self.path=='/api/change':
   try:
    with connect(DB_PATH) as db: message=change(db,data.get('id'),session['id'],data)
    return self.response(200,{'message':message})
   except BusinessError as e: return self.response(403 if e.key=='permission' else 409 if e.key=='conflict' else 404 if e.key=='missing' else 400,{'message':str(e)})
   except sqlite3.Error: return self.response(500,{'message':'変更を保存できませんでした。時間をおいて再度お試しください。'})
  return self.response(404,{'message':'ページが見つかりません。'})

if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('--demo',action='store_true');parser.add_argument('--port',type=int,default=8080);args=parser.parse_args()
 initialize(DB_PATH)
 if args.demo:
  seed();ACCOUNTS={'admin-demo':1,'member-demo':2}
 else:
  ACCOUNTS=json.loads(os.environ.get('INQUIRY_LOGIN_KEYS','{}'))
  if not ACCOUNTS: raise SystemExit('INQUIRY_LOGIN_KEYS にログインキーと users.id の対応を設定してください。')
 print(f'http://127.0.0.1:{args.port}',flush=True)
 ThreadingHTTPServer(('127.0.0.1',args.port),Handler).serve_forever()

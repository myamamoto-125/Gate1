import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

LABELS = {'NEW':'未対応','IN_PROGRESS':'対応中','PENDING':'保留','DONE':'完了'}
TRANSITIONS = {'NEW':['IN_PROGRESS'],'IN_PROGRESS':['PENDING','DONE'],'PENDING':['IN_PROGRESS'],'DONE':['IN_PROGRESS']}
MESSAGES = {
 'transition':'このステータスへは変更できません。画面を再読み込みしてください。',
 'permission':'この操作を行う権限がありません。',
 'done':'完了済みの問い合わせは担当者を変更できません。',
 'user':'指定されたユーザーは選択できません。',
 'comment':'コメントは 200 文字以内で入力してください。',
 'missing':'指定された問い合わせは存在しません。',
 'conflict':'他のユーザーが更新しました。画面を再読み込みしてください。'}
class BusinessError(Exception):
 def __init__(self, key):
  self.key=key
  super().__init__(MESSAGES[key])
def connect(path):
 db=sqlite3.connect(path,timeout=10)
 db.row_factory=sqlite3.Row
 db.execute('PRAGMA foreign_keys=ON')
 return db
def initialize(path):
 with connect(path) as db: db.executescript(Path(__file__).with_name('schema.sql').read_text())
def change(db, inquiry_id, actor_id, payload):
 try:
  db.execute('BEGIN IMMEDIATE')
  actor=db.execute('SELECT * FROM users WHERE id=? AND is_active=1',(actor_id,)).fetchone()
  if not actor: raise BusinessError('permission')
  row=db.execute('SELECT * FROM inquiries WHERE id=?',(inquiry_id,)).fetchone()
  if not row: raise BusinessError('missing')
  if payload.get('updated_at')!=row['updated_at']: raise BusinessError('conflict')
  now=datetime.now(timezone.utc)
  previous=datetime.fromisoformat(row['updated_at'].replace('Z','+00:00'))
  if previous.tzinfo is None: previous=previous.replace(tzinfo=timezone.utc)
  now=max(now,previous+timedelta(microseconds=1)).isoformat(timespec='microseconds')
  def history(field,old,new):
   db.execute('INSERT INTO inquiry_histories(inquiry_id,changed_by,field_name,old_value,new_value,changed_at) VALUES(?,?,?,?,?,?)',(inquiry_id,actor_id,field,None if old is None else str(old),None if new is None else str(new),now))
  if payload.get('field')=='status':
   target=payload.get('value')
   if target not in TRANSITIONS[row['status']]: raise BusinessError('transition')
   comment=payload.get('comment','')
   if not isinstance(comment,str) or len(comment)>200: raise BusinessError('comment')
   assignee=row['assignee_id']
   if row['status']=='NEW' and assignee is None:
    assignee=actor_id
    history('assignee',None,actor_id)
   closed=now if target=='DONE' else None if row['status']=='DONE' else row['closed_at']
   db.execute('UPDATE inquiries SET status=?,assignee_id=?,closed_at=?,updated_at=? WHERE id=?',(target,assignee,closed,now,inquiry_id))
   history('status',row['status'],target)
   message=f'ステータスを「{LABELS[target]}」に変更しました。'
  elif payload.get('field')=='assignee':
   target=payload.get('value')
   if row['status']=='DONE': raise BusinessError('done')
   if actor['role']=='MEMBER' and (row['assignee_id'] is not None or target!=actor_id): raise BusinessError('permission')
   if target is None:
    if actor['role']!='ADMIN' or row['status']!='NEW': raise BusinessError('permission')
    name=None
   else:
    if type(target) is not int: raise BusinessError('user')
    user=db.execute('SELECT * FROM users WHERE id=? AND is_active=1',(target,)).fetchone()
    if not user: raise BusinessError('user')
    name=user['name']
   if target==row['assignee_id']:
    raise BusinessError('permission')
   db.execute('UPDATE inquiries SET assignee_id=?,updated_at=? WHERE id=?',(target,now,inquiry_id))
   history('assignee',row['assignee_id'],target)
   message=f'担当者を「{name}」に変更しました。' if name else '担当者を未割り当てに戻しました。'
  else: raise BusinessError('permission')
  db.commit()
  return message
 except Exception:
  db.rollback()
  raise

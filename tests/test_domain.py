import sqlite3,tempfile,unittest
from pathlib import Path
from domain import initialize,connect,change,BusinessError,TRANSITIONS

class DomainTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.path=str(Path(self.tmp.name)/'test.db');initialize(self.path);self.db=connect(self.path)
  self.db.executemany('INSERT INTO users(id,name,role,is_active) VALUES(?,?,?,?)',[(1,'管理者','ADMIN',1),(2,'担当者','MEMBER',1),(3,'別担当者','MEMBER',1),(4,'無効','MEMBER',0)])
  self.db.execute("INSERT INTO inquiries(id,title,body,requester_name) VALUES(1,'件名','本文','依頼者')");self.db.commit()
 def tearDown(self): self.db.close();self.tmp.cleanup()
 def row(self):return self.db.execute('SELECT * FROM inquiries WHERE id=1').fetchone()
 def act(self,field,value,actor=1,**extra):return change(self.db,1,actor,dict(field=field,value=value,updated_at=self.row()['updated_at'],**extra))
 def state(self,s):self.db.execute('UPDATE inquiries SET status=?,assignee_id=NULL,closed_at=NULL WHERE id=1',(s,));self.db.commit()
 def test_transition_matrix(self):
  for old in TRANSITIONS:
   for new in TRANSITIONS:
    with self.subTest(old=old,new=new):
     self.state(old)
     if new in TRANSITIONS[old]:self.act('status',new);self.assertEqual(self.row()['status'],new)
     else:
      with self.assertRaises(BusinessError):self.act('status',new)
 def test_auto_assignment_two_histories(self):
  self.act('status','IN_PROGRESS',2)
  self.assertEqual(self.row()['assignee_id'],2)
  self.assertEqual(self.db.execute('SELECT count(*) FROM inquiry_histories').fetchone()[0],2)
 def test_closed_and_reopen(self):
  self.act('status','IN_PROGRESS');self.act('status','DONE');self.assertIsNotNone(self.row()['closed_at'])
  self.act('status','IN_PROGRESS');self.assertIsNone(self.row()['closed_at'])
 def test_member_claim_only(self):
  with self.assertRaisesRegex(BusinessError,'権限'):self.act('assignee',3,2)
  self.act('assignee',2,2)
  with self.assertRaisesRegex(BusinessError,'権限'):self.act('assignee',2,2)
 def test_admin_clear_only_new(self):
  self.act('assignee',2);self.act('assignee',None);self.assertIsNone(self.row()['assignee_id'])
  self.act('status','IN_PROGRESS')
  with self.assertRaisesRegex(BusinessError,'権限'):self.act('assignee',None)
 def test_inactive_and_done(self):
  with self.assertRaisesRegex(BusinessError,'選択'):self.act('assignee',4)
  self.state('DONE')
  with self.assertRaisesRegex(BusinessError,'完了済み'):self.act('assignee',2)
 def test_comment_boundary(self):
  with self.assertRaisesRegex(BusinessError,'200'):self.act('status','IN_PROGRESS',comment='😀'*201)
  self.act('status','IN_PROGRESS',comment='😀'*200)
 def test_conflict(self):
  stamp=self.row()['updated_at'];self.act('assignee',2)
  with self.assertRaisesRegex(BusinessError,'他のユーザー'):change(self.db,1,1,{'updated_at':stamp,'field':'status','value':'IN_PROGRESS'})
 def test_history_failure_rolls_back(self):
  before=dict(self.row());self.db.execute("CREATE TRIGGER fail_history BEFORE INSERT ON inquiry_histories BEGIN SELECT RAISE(ABORT,'test failure'); END;");self.db.commit()
  with self.assertRaises(sqlite3.IntegrityError):self.act('status','IN_PROGRESS')
  self.assertEqual(before,dict(self.row()))
  with self.assertRaises(sqlite3.IntegrityError):self.act('assignee',2)
  self.assertEqual(before,dict(self.row()))
 def test_missing_and_inactive_actor(self):
  with self.assertRaisesRegex(BusinessError,'存在'):change(self.db,999,1,{})
  with self.assertRaisesRegex(BusinessError,'権限'):self.act('status','IN_PROGRESS',4)
 def test_assigned_user_preserved(self):
  self.act('assignee',3);self.act('status','IN_PROGRESS',2);self.assertEqual(self.row()['assignee_id'],3)
 def test_history_assignee_values(self):
  self.act('assignee',2);self.act('assignee',3)
  h=self.db.execute('SELECT * FROM inquiry_histories ORDER BY id DESC').fetchone()
  self.assertEqual((h['old_value'],h['new_value']),('2','3'))

if __name__=='__main__':unittest.main()

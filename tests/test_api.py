import http.cookiejar
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from http.server import ThreadingHTTPServer

import server
from domain import connect, initialize


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous = server.DB_PATH, server.ACCOUNTS, server.SESSIONS
        server.DB_PATH = str(Path(self.temp.name) / 'api.sqlite3')
        server.ACCOUNTS = {'admin-test': 1, 'member-test': 2}
        server.SESSIONS = {}
        initialize(server.DB_PATH)
        server.seed()
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.base = 'http://127.0.0.1:' + str(self.http.server_port)
        self.client = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.csrf = ''
        self.login('admin-test')

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join()
        server.DB_PATH, server.ACCOUNTS, server.SESSIONS = self.previous
        self.temp.cleanup()

    def request(self, path, payload=None):
        data = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(self.base + path, data=data, headers={
            'Content-Type': 'application/json', 'X-CSRF-Token': self.csrf})
        try:
            response = self.client.open(request)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, json.load(response)

    def login(self, key):
        self.assertEqual(self.request('/api/login', {'key': key})[0], 200)
        self.csrf = self.request('/api/me')[1]['csrf']

    def snapshot(self):
        with connect(server.DB_PATH) as db:
            return ([tuple(r) for r in db.execute('SELECT * FROM inquiries ORDER BY id')],
                    [tuple(r) for r in db.execute('SELECT * FROM inquiry_histories ORDER BY id')])

    def change(self, field, value, inquiry=1, **extra):
        detail = self.request('/api/detail?id=' + str(inquiry))[1]
        payload = dict(id=inquiry, field=field, value=value,
                       updated_at=detail['inquiry']['updated_at'])
        payload.update(extra)
        return self.request('/api/change', payload)

    def rejected(self, field, value, status, message, **extra):
        before = self.snapshot()
        self.assertEqual(self.change(field, value, **extra), (status, {'message': message}))
        self.assertEqual(self.snapshot(), before)

    def test_new_done_direct_request(self):
        self.assertEqual(self.request('/api/detail?id=1')[1]['transitions'], ['IN_PROGRESS'])
        self.rejected('status', 'DONE', 400,
                      'このステータスへは変更できません。画面を再読み込みしてください。')

    def test_admin_assign_and_clear(self):
        self.assertEqual(self.change('assignee', 1),
                         (200, {'message': '担当者を「佐藤 花子」に変更しました。'}))
        self.assertEqual(self.change('assignee', None),
                         (200, {'message': '担当者を未割り当てに戻しました。'}))
        detail = self.request('/api/detail?id=1')[1]
        self.assertIsNone(detail['inquiry']['assignee_id'])
        self.assertEqual(detail['inquiry']['status'], 'NEW')
        self.assertEqual(len(detail['histories']), 2)
        self.assertEqual(detail['histories'][0]['old_value'], '1')
        self.assertIsNone(detail['histories'][0]['new_value'])

    def test_member_cannot_forge_admin_or_clear(self):
        self.change('assignee', 1)
        self.login('member-test')
        self.rejected('assignee', None, 403, 'この操作を行う権限がありません。',
                      role='ADMIN', actor_id=1, current_user={'id': 1, 'role': 'ADMIN'})
        self.rejected('assignee', 2, 403, 'この操作を行う権限がありません。')

    def test_member_unassigned_can_only_claim_self(self):
        self.login('member-test')
        self.rejected('assignee', 3, 403, 'この操作を行う権限がありません。')
        self.assertEqual(self.change('assignee', 2),
                         (200, {'message': '担当者を「鈴木 一郎」に変更しました。'}))

    def test_clear_forbidden_outside_new(self):
        for inquiry in (2, 3):
            self.rejected('assignee', None, 403, 'この操作を行う権限がありません。', inquiry=inquiry)

    def test_comment_exact_and_status_success(self):
        self.rejected('status', 'IN_PROGRESS', 400,
                      'コメントは 200 文字以内で入力してください。', comment='あ' * 201)
        self.assertEqual(self.change('status', 'IN_PROGRESS', comment='あ' * 200),
                         (200, {'message': 'ステータスを「対応中」に変更しました。'}))
        self.assertEqual(self.change('status', 'PENDING'),
                         (200, {'message': 'ステータスを「保留」に変更しました。'}))
        self.change('status', 'IN_PROGRESS')
        self.assertEqual(self.change('status', 'DONE'),
                         (200, {'message': 'ステータスを「完了」に変更しました。'}))
        self.rejected('assignee', 2, 400, '完了済みの問い合わせは担当者を変更できません。')

    def test_inactive_user_and_conflict(self):
        self.rejected('assignee', 4, 400, '指定されたユーザーは選択できません。')
        self.rejected('status', 'IN_PROGRESS', 409,
                      '他のユーザーが更新しました。画面を再読み込みしてください。', updated_at='stale')

    def test_missing_inquiry(self):
        before = self.snapshot()
        self.assertEqual(self.request('/api/change', {'id': 999, 'field': 'status', 'value': 'DONE'}),
                         (404, {'message': '指定された問い合わせは存在しません。'}))
        self.assertEqual(self.snapshot(), before)

    def test_history_failure_atomic_for_both_operations(self):
        with connect(server.DB_PATH) as db:
            db.execute("CREATE TRIGGER fail BEFORE INSERT ON inquiry_histories BEGIN SELECT RAISE(ABORT,'test'); END")
        for field, value in [('status', 'IN_PROGRESS'), ('assignee', 2)]:
            self.rejected(field, value, 500, '変更を保存できませんでした。時間をおいて再度お試しください。')


if __name__ == '__main__':
    unittest.main()

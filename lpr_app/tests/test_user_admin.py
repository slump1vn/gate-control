from django.contrib.auth import get_user_model

from lpr_app.models import GateConfigChange
from lpr_app.tests.test_gate_api import ApiTestBase

User = get_user_model()


class UserApiTest(ApiTestBase):
    def _create(self, **overrides):
        body = {'username': 'newop', 'email': 'newop@example.com', 'role': 'gate_operator', 'password': 'new-pass-123456'}
        body.update(overrides)
        return self.send('post', '/api/v1/users/', body)

    def test_create_sets_role_and_password(self):
        self.as_admin()
        response = self._create()
        self.assertEqual(response.status_code, 201, response.content)
        data = response.json()
        self.assertEqual(data['role'], 'gate_operator')
        self.assertTrue(data['is_active'])
        self.assertNotIn('password', data)
        user = User.objects.get(username='newop')
        self.assertTrue(user.check_password('new-pass-123456'))
        self.assertEqual(
            GateConfigChange.objects.filter(object_type='user', action='create').count(), 1,
        )

    def test_create_requires_password(self):
        self.as_admin()
        response = self._create(password='')
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn('password', response.json()['errors'])

    def test_create_username_must_be_unique(self):
        self.as_admin()
        self._create()
        response = self._create()
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn('username', response.json()['errors'])

    def test_list_and_detail_never_contain_password(self):
        self.as_admin()
        user_id = self._create().json()['id']
        for url in ('/api/v1/users/', f'/api/v1/users/{user_id}/'):
            body = self.client.get(url).content.decode()
            self.assertNotIn('new-pass-123456', body)
            self.assertNotIn('password', body)

    def test_operator_forbidden(self):
        self.as_operator()
        self.assertEqual(self._create().status_code, 403)
        self.assertEqual(self.client.get('/api/v1/users/').status_code, 403)

    def test_anonymous_forbidden(self):
        self.assertEqual(self.client.get('/api/v1/users/').status_code, 403)

    def test_patch_updates_role_and_keeps_password(self):
        self.as_admin()
        user_id = self._create().json()['id']
        old_hash = User.objects.get(pk=user_id).password
        response = self.send('patch', f'/api/v1/users/{user_id}/', {'role': 'gate_admin'})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['role'], 'gate_admin')
        user = User.objects.get(pk=user_id)
        self.assertEqual(user.password, old_hash)
        self.assertTrue(user.groups.filter(name='gate_admin').exists())
        self.assertFalse(user.groups.filter(name='gate_operator').exists())

    def test_patch_resets_password(self):
        self.as_admin()
        user_id = self._create().json()['id']
        response = self.send('patch', f'/api/v1/users/{user_id}/', {'password': 'brand-new-pass-1'})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(User.objects.get(pk=user_id).check_password('brand-new-pass-1'))

    def test_patch_no_changes_records_no_audit_entry(self):
        self.as_admin()
        user_id = self._create().json()['id']
        GateConfigChange.objects.all().delete()
        response = self.send('patch', f'/api/v1/users/{user_id}/', {})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(GateConfigChange.objects.filter(object_type='user').count(), 0)

    def test_delete_deactivates_rather_than_removes(self):
        self.as_admin()
        user_id = self._create().json()['id']
        response = self.client.delete(f'/api/v1/users/{user_id}/')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(response.json()['is_active'])
        self.assertTrue(User.objects.filter(pk=user_id).exists())
        self.assertFalse(User.objects.get(pk=user_id).is_active)

    def test_admin_cannot_deactivate_self(self):
        self.as_admin()
        response = self.client.delete(f'/api/v1/users/{self.admin.id}/')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['error_code'], 'SELF_LOCKOUT')
        self.assertTrue(User.objects.get(pk=self.admin.id).is_active)

    def test_admin_cannot_demote_self(self):
        self.as_admin()
        response = self.send('patch', f'/api/v1/users/{self.admin.id}/', {'role': 'gate_operator'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['error_code'], 'SELF_LOCKOUT')
        self.assertTrue(self.admin.groups.filter(name='gate_admin').exists())

    def test_admin_can_edit_own_email(self):
        self.as_admin()
        response = self.send('patch', f'/api/v1/users/{self.admin.id}/', {'email': 'me@example.com'})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['email'], 'me@example.com')

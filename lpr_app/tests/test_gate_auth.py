import json
from io import StringIO

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Group
from django.core.cache import cache
from django.core.management import call_command
from django.http import JsonResponse
from django.test import Client, RequestFactory, TestCase, override_settings

from lpr_app.models import GateDevice
from lpr_app.utils.auth import (
    GATE_ADMIN, GATE_OPERATOR, device_token_matches, require_agent_token,
    require_gate_admin, require_gate_operator, user_roles,
)

KEY = Fernet.generate_key().decode()
User = get_user_model()


def _ok_view(request):
    return JsonResponse({'ok': True})


admin_view = require_gate_admin(_ok_view)
operator_view = require_gate_operator(_ok_view)
agent_view = require_agent_token(_ok_view)


def _user(username, *groups, **kw):
    user = User.objects.create_user(username, password='pw-123456', **kw)
    for g in groups:
        user.groups.add(Group.objects.get(name=g))
    return user


class RoleGroupsMigrationTest(TestCase):
    def test_groups_created_with_permissions(self):
        admin_perms = set(Group.objects.get(name=GATE_ADMIN).permissions.values_list('codename', flat=True))
        operator_perms = set(Group.objects.get(name=GATE_OPERATOR).permissions.values_list('codename', flat=True))
        self.assertIn('change_camera', admin_perms)
        self.assertIn('view_gateconfigchange', admin_perms)
        self.assertIn('change_vehicle', operator_perms)
        self.assertIn('view_accessevent', operator_perms)
        self.assertNotIn('change_camera', operator_perms)
        self.assertNotIn('view_camera', operator_perms)
        self.assertNotIn('delete_vehicle', operator_perms)


class UserRolesTest(TestCase):
    def test_roles(self):
        self.assertEqual(user_roles(AnonymousUser()), [])
        self.assertEqual(user_roles(_user('nobody')), [])
        self.assertEqual(user_roles(_user('op', GATE_OPERATOR)), [GATE_OPERATOR])
        self.assertEqual(user_roles(_user('adm', GATE_ADMIN)), [GATE_ADMIN, GATE_OPERATOR])
        root = User.objects.create_superuser('root', 'r@x.com', 'pw')
        self.assertEqual(user_roles(root), [GATE_ADMIN, GATE_OPERATOR])

    def test_inactive_user_has_no_roles(self):
        self.assertEqual(user_roles(_user('gone', GATE_ADMIN, is_active=False)), [])


class RoleDecoratorTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _call(self, view, user):
        request = self.factory.get('/x')
        request.user = user
        return view(request).status_code

    def test_anonymous_denied(self):
        self.assertEqual(self._call(admin_view, AnonymousUser()), 403)
        self.assertEqual(self._call(operator_view, AnonymousUser()), 403)

    def test_operator_scope(self):
        op = _user('op', GATE_OPERATOR)
        self.assertEqual(self._call(operator_view, op), 200)
        self.assertEqual(self._call(admin_view, op), 403)

    def test_admin_scope(self):
        adm = _user('adm', GATE_ADMIN)
        self.assertEqual(self._call(operator_view, adm), 200)
        self.assertEqual(self._call(admin_view, adm), 200)


@override_settings(GATE_AGENT_TOKEN='agent-secret')
class AgentTokenTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _call(self, header=None, user=None):
        kwargs = {'HTTP_AUTHORIZATION': header} if header else {}
        request = self.factory.post('/x', **kwargs)
        request.user = user or AnonymousUser()
        return agent_view(request).status_code

    def test_valid_token(self):
        self.assertEqual(self._call('Bearer agent-secret'), 200)

    def test_wrong_or_missing_token(self):
        self.assertEqual(self._call('Bearer nope'), 403)
        self.assertEqual(self._call('agent-secret'), 403)
        self.assertEqual(self._call(), 403)

    def test_user_session_is_not_an_agent(self):
        root = User.objects.create_superuser('root', 'r@x.com', 'pw')
        self.assertEqual(self._call(user=root), 403)

    @override_settings(GATE_AGENT_TOKEN='')
    def test_unconfigured_token_rejects_everything(self):
        self.assertEqual(self._call('Bearer '), 403)

    def test_agent_view_is_csrf_exempt(self):
        self.assertTrue(getattr(agent_view, 'csrf_exempt', False))


@override_settings(GATE_CONFIG_ENCRYPTION_KEY=KEY)
class DeviceTokenTest(TestCase):
    def test_device_token(self):
        gate = GateDevice(name='Main')
        gate.set_controller_token('dev-tok')
        gate.save()
        factory = RequestFactory()
        self.assertTrue(device_token_matches(factory.post('/x', HTTP_AUTHORIZATION='Bearer dev-tok'), gate))
        self.assertFalse(device_token_matches(factory.post('/x', HTTP_AUTHORIZATION='Bearer other'), gate))
        with override_settings(GATE_CONFIG_ENCRYPTION_KEY=Fernet.generate_key().decode()):
            self.assertFalse(device_token_matches(factory.post('/x', HTTP_AUTHORIZATION='Bearer dev-tok'), gate))

    def test_gate_without_token_never_matches(self):
        gate = GateDevice.objects.create(name='Main')
        self.assertFalse(device_token_matches(RequestFactory().post('/x', HTTP_AUTHORIZATION='Bearer '), gate))


class LoginFlowTest(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client(enforce_csrf_checks=True)
        _user('op', GATE_OPERATOR)

    def _csrf(self):
        response = self.client.get('/api/v1/auth/me/')
        self.assertEqual(response.status_code, 200)
        return response.json()['csrf_token']

    def _login(self, password='pw-123456', csrf=None):
        return self.client.post(
            '/api/v1/auth/login/',
            data=json.dumps({'username': 'op', 'password': password}),
            content_type='application/json',
            HTTP_X_CSRFTOKEN=csrf if csrf is not None else self._csrf(),
        )

    def test_me_anonymous(self):
        data = self.client.get('/api/v1/auth/me/').json()
        self.assertFalse(data['authenticated'])
        self.assertEqual(data['roles'], [])
        self.assertTrue(data['csrf_token'])

    def test_login_success_sets_session(self):
        response = self._login()
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['authenticated'])
        self.assertEqual(data['username'], 'op')
        self.assertEqual(data['roles'], [GATE_OPERATOR])
        cookie = response.cookies['sessionid']
        self.assertTrue(cookie['httponly'])
        self.assertEqual(cookie['samesite'], 'Lax')
        self.assertTrue(self.client.get('/api/v1/auth/me/').json()['authenticated'])

    def test_login_without_csrf_rejected(self):
        self.assertEqual(self._login(csrf='').status_code, 403)

    def test_wrong_password(self):
        response = self._login(password='bad')
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()['error'], 'Invalid username or password')

    def test_unknown_user_same_error(self):
        csrf = self._csrf()
        response = self.client.post(
            '/api/v1/auth/login/', data=json.dumps({'username': 'ghost', 'password': 'x'}),
            content_type='application/json', HTTP_X_CSRFTOKEN=csrf,
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()['error'], 'Invalid username or password')

    def test_lockout_after_repeated_failures(self):
        for _ in range(5):
            self.assertEqual(self._login(password='bad').status_code, 401)
        locked = self._login()
        self.assertEqual(locked.status_code, 429)
        self.assertEqual(locked.json()['error_code'], 'LOGIN_LOCKED')

    def test_form_encoded_login(self):
        csrf = self._csrf()
        response = self.client.post('/api/v1/auth/login/', {'username': 'op', 'password': 'pw-123456'},
                                    HTTP_X_CSRFTOKEN=csrf)
        self.assertEqual(response.status_code, 200)

    def test_logout(self):
        csrf = self._login().json()['csrf_token']
        response = self.client.post('/api/v1/auth/logout/', HTTP_X_CSRFTOKEN=csrf)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['authenticated'])
        self.assertFalse(self.client.get('/api/v1/auth/me/').json()['authenticated'])

    def test_login_get_not_allowed(self):
        self.assertEqual(self.client.get('/api/v1/auth/login/').status_code, 405)


@override_settings(CORS_ALLOWED_ORIGINS=['http://localhost:3000'])
class CrossOriginSessionTest(TestCase):
    def test_credentials_allowed_for_listed_origin(self):
        response = self.client.get('/api/v1/auth/me/', HTTP_ORIGIN='http://localhost:3000')
        self.assertEqual(response['Access-Control-Allow-Origin'], 'http://localhost:3000')
        self.assertEqual(response['Access-Control-Allow-Credentials'], 'true')

    def test_unlisted_origin_gets_no_cors(self):
        response = self.client.get('/api/v1/auth/me/', HTTP_ORIGIN='http://evil.example')
        self.assertNotIn('Access-Control-Allow-Origin', response)


class PublicEndpointsUnchangedTest(TestCase):
    def test_public_endpoints_still_anonymous(self):
        for url in ('/api/v1/images/', '/api/v1/config/', '/api/v1/health-light/'):
            self.assertEqual(self.client.get(url).status_code, 200, url)


class GenerateSecretsCommandTest(TestCase):
    def test_outputs_valid_values(self):
        out, err = StringIO(), StringIO()
        call_command('generate_gate_secrets', stdout=out, stderr=err)
        lines = dict(line.split('=', 1) for line in out.getvalue().strip().splitlines())
        Fernet(lines['GATE_CONFIG_ENCRYPTION_KEY'].encode())
        self.assertGreaterEqual(len(lines['GATE_AGENT_TOKEN']), 32)
        self.assertIn('Back up', err.getvalue())

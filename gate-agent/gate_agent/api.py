"""Client for the LPR service's gate agent endpoints."""

import requests


class ApiError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class LprApi:
    def __init__(self, base_url, token, timeout=10.0, session=None):
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers['Authorization'] = f'Bearer {token}'

    def _url(self, path):
        return f'{self.base_url}{path}'

    def _check(self, response):
        if response.status_code >= 400:
            try:
                message = response.json().get('error', response.text)
            except ValueError:
                message = response.text
            raise ApiError(f'HTTP {response.status_code}: {message}', response.status_code)
        return response

    def agent_config(self, version=None):
        """Return the new config, or None when it is unchanged since `version`."""
        params = {'version': version} if version else None
        response = self.session.get(self._url('/api/v1/gate/agent-config/'), params=params, timeout=self.timeout)
        if response.status_code == 304:
            return None
        return self._check(response).json()

    def approach(self, gate_id, camera_id):
        """A vehicle is coming toward this camera: does the gate open for it right now?"""
        response = self.session.post(
            self._url('/api/v1/gate/approach/'), json={'gate_id': gate_id, 'camera_id': camera_id},
            timeout=self.timeout,
        )
        return self._check(response).json()

    def decide(self, gate_id, jpeg_frames, timeout, camera_id=None, approach_event_id=None):
        files = [('frames', (f'frame{i}.jpg', data, 'image/jpeg')) for i, data in enumerate(jpeg_frames)]
        data = {'gate_id': gate_id}
        if camera_id is not None:
            # Tells the service which way the vehicle was going
            data['camera_id'] = camera_id
        if approach_event_id is not None:
            # The gate already opened on approach: the read only fills in that event
            data['approach_event_id'] = approach_event_id
        response = self.session.post(
            self._url('/api/v1/gate/decide/'), data=data, files=files, timeout=timeout,
        )
        return self._check(response).json()

    def command_result(self, event_id, sent, result):
        response = self.session.post(
            self._url(f'/api/v1/gate/events/{event_id}/command-result/'),
            json={'sent': bool(sent), 'result': str(result)[:100]}, timeout=self.timeout,
        )
        self._check(response)

    def agent_commands(self):
        response = self.session.get(self._url('/api/v1/gate/agent-commands/'), timeout=self.timeout)
        return self._check(response).json().get('commands', [])

    def agent_jobs(self):
        """Installer jobs (remote capture) to carry out on controllers."""
        response = self.session.get(self._url('/api/v1/gate/agent-jobs/'), timeout=self.timeout)
        return self._check(response).json().get('jobs', [])

    def job_result(self, job_id, sent, result, detail=None):
        body = {'sent': bool(sent), 'result': str(result)[:100]}
        if detail:
            body['detail'] = detail
        response = self.session.post(
            self._url(f'/api/v1/gate/controller-jobs/{job_id}/result/'), json=body, timeout=self.timeout,
        )
        self._check(response)

    def agent_status(self, cameras):
        response = self.session.post(
            self._url('/api/v1/gate/agent-status/'), json={'cameras': cameras}, timeout=self.timeout,
        )
        self._check(response)

"""
Client for the gate controller protocol (design.md §6): the ESP32 relay board,
or the simulated barrier served by the LPR service. Both speak the same
contract: POST {base}/open|close|stop with a bearer token and {nonce, ts}.

The local rate limit is a second line of defence behind the firmware's own.
"""

import threading
import time

import requests

MOTION_COMMANDS = ('open', 'close')


class ControllerError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status
        self.message = message


class ControllerClient:
    def __init__(self, base_url, token, timeout=3.0, min_interval=3.0, session=None,
                 clock=time.monotonic, wall_clock=time.time):
        self.base_url = base_url if base_url.endswith('/') else base_url + '/'
        self.token = token
        self.timeout = timeout
        self.min_interval = min_interval
        self.session = session or requests.Session()
        self.clock = clock
        self.wall_clock = wall_clock
        self._last_nonce = 0
        self._last_motion_at = None
        self._lock = threading.Lock()

    def _nonce(self):
        self._last_nonce = max(self._last_nonce + 1, int(self.wall_clock() * 1000))
        return self._last_nonce

    def _post(self, command):
        return self.session.post(
            self.base_url + command,
            json={'nonce': self._nonce(), 'ts': self.wall_clock()},
            headers={'Authorization': f'Bearer {self.token}'},
            timeout=self.timeout,
        )

    def send(self, command):
        """Send a command; returns the controller's JSON body. Raises ControllerError."""
        if command not in ('open', 'close', 'stop'):
            raise ControllerError(f'Unknown command {command!r}')
        with self._lock:
            now = self.clock()
            if command in MOTION_COMMANDS and self._last_motion_at is not None \
                    and now - self._last_motion_at < self.min_interval:
                raise ControllerError('Local rate limit: motion command too soon', 429)
            response = None
            for attempt in range(2):
                try:
                    response = self._post(command)
                    break
                except (requests.ConnectionError, requests.Timeout) as exc:
                    if attempt == 1:
                        raise ControllerError(f'Controller unreachable: {exc.__class__.__name__}')
            if command in MOTION_COMMANDS and response.status_code < 400:
                self._last_motion_at = now
        if response.status_code >= 400:
            try:
                message = response.json().get('error', response.text)
            except ValueError:
                message = response.text
            raise ControllerError(f'Controller refused {command}: HTTP {response.status_code} {message}',
                                  response.status_code)
        try:
            return response.json()
        except ValueError:
            return {'ok': True}

    def status(self):
        try:
            response = self.session.get(self.base_url + 'status', headers={'Authorization': f'Bearer {self.token}'},
                                        timeout=self.timeout)
        except requests.RequestException as exc:
            raise ControllerError(f'Controller unreachable: {exc.__class__.__name__}')
        if response.status_code >= 400:
            raise ControllerError(f'Controller status HTTP {response.status_code}', response.status_code)
        return response.json()

"""Llama-swap HTTP adapter. A model is active only after a successful real request."""
import json
import urllib.request
from urllib.parse import urlsplit
from .base import BaseEngine
from ..config import config

class LlamaSwapEngine(BaseEngine):
    def __init__(self, base_url=None):
        self.base_url = (base_url or config.get('llama_swap_url')).rstrip('/')
        self._active_model = None
        self.last_error = None
    def request(self, path, payload=None, timeout=5, method=None):
        data = json.dumps(payload).encode('utf-8') if payload is not None else None
        req = urllib.request.Request(self.base_url + path, data=data,
            headers={'Content-Type': 'application/json'}, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read()
            return json.loads(raw) if raw else {}
    def is_online(self):
        try:
            return isinstance(self.request('/v1/models', timeout=2).get('data'), list)
        except Exception as exc:
            self.last_error = str(exc)
            self._active_model = None
            return False
    def list_models(self):
        try:
            return [m['id'] for m in self.request('/v1/models', timeout=3).get('data', [])]
        except Exception as exc:
            self.last_error = str(exc)
            return []
    def get_active_model(self):
        try:
            running = self.request('/running', timeout=2).get('running', [])
            ids = [row.get('model') for row in running if row.get('state') == 'ready']
            return self._active_model if self._active_model in ids else None
        except Exception:
            return None
    def switch_model(self, model_id, timeout=240):
        try:
            response = self.request('/v1/chat/completions', {'model': model_id,
                'messages': [{'role': 'user', 'content': 'Reply OK.'}], 'max_tokens': 8,
                'temperature': 0, 'chat_template_kwargs': {'enable_thinking': False}}, timeout=timeout)
            if not response.get('choices') or not response['choices'][0].get('message', {}).get('content'):
                raise ValueError('Backend returned no text answer')
            self._active_model = model_id
            self.last_error = None
            return True
        except Exception as exc:
            self.last_error = str(exc)
            return False
    def unload_models(self):
        try:
            self.request('/api/models/unload', payload={}, timeout=10, method='POST')
            self._active_model = None
            return True
        except Exception as exc:
            self.last_error = str(exc)
            return False
    def get_status(self):
        online = self.is_online()
        return {'name': 'llama-swap', 'online': online, 'url': self.base_url,
            'active_model': self._active_model if online else None, 'error': self.last_error}

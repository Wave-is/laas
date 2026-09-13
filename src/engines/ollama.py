"""Ollama adapter that distinguishes installed models from loaded models."""
import requests
from .base import BaseEngine
from ..config import config

class OllamaEngine(BaseEngine):
    def __init__(self):
        self.base_url=config.get('ollama_url','http://127.0.0.1:11434').rstrip('/')
        self._active_model=None;self.last_error=None
    def call(self,path,payload=None,timeout=3):
        response=requests.get(self.base_url+path,timeout=timeout) if payload is None else requests.post(self.base_url+path,json=payload,timeout=timeout)
        response.raise_for_status();return response.json()
    def is_online(self):
        try:return bool(self.call('/api/version').get('version'))
        except Exception as exc:self.last_error=str(exc);return False
    def list_models(self):
        try:return [row['name'] for row in self.call('/api/tags').get('models',[])]
        except Exception as exc:self.last_error=str(exc);return []
    def get_active_model(self):
        try:
            loaded=[m['name'] for m in self.call('/api/ps').get('models',[])]
            return self._active_model if self._active_model in loaded else None
        except Exception:return None
    def switch_model(self,model_id):
        try:
            result=self.call('/api/generate',{'model':model_id,'prompt':'Reply OK.','stream':False,'options':{'num_predict':16}},timeout=240)
            if not result.get('response'):return False
            self._active_model=model_id;return True
        except Exception as exc:self.last_error=str(exc);return False
    def unload_models(self):
        model=self.get_active_model()
        if not model:return True
        try:
            self.call('/api/generate',{'model':model,'keep_alive':0,'stream':False},timeout=30)
            self._active_model=None;return True
        except Exception as exc:self.last_error=str(exc);return False
    def get_status(self):
        return {'name':'Ollama','online':self.is_online(),'active_model':self.get_active_model(),'error':self.last_error}

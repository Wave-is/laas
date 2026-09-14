"""Configurable shared services using tracked process ownership."""
import time
from pathlib import Path
from urllib.parse import urlsplit
from .config import config
from .supervisor import supervisor
from .engines.llama_swap import LlamaSwapEngine

class ProcessManager:
    def is_llama_swap_running(self):
        return LlamaSwapEngine().is_online()
    def start_llama_swap(self, config_path=None):
        if self.is_llama_swap_running():
            return True
        exe = config.get('llama_swap_executable')
        path = config_path or config.get('llama_swap_config')
        if not exe or not Path(exe).is_file() or not path or not Path(path).is_file():
            return False
        url = urlsplit(config.get('llama_swap_url'))
        if url.hostname not in ('127.0.0.1', 'localhost', '::1') or url.scheme != 'http':
            raise ValueError('Managed llama-swap must bind to loopback HTTP')
        supervisor.start('service:llama-swap', [exe, '-config', str(path), '-listen', f'0.0.0.0:{url.port or 9292}'],
            cwd=str(Path(exe).parent))
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if not supervisor.status('service:llama-swap')['running']:
                return False
            if self.is_llama_swap_running():
                return True
            time.sleep(.25)
        return False
    def stop_llama_swap(self):
        if self.is_llama_swap_running() and not supervisor.status('service:llama-swap')['owned']:
            return False
        return supervisor.stop('service:llama-swap')['success']
    def free_gpu(self):
        # No kill-by-name fallback. Backend API manages its own models.
        if self.is_llama_swap_running() and not supervisor.status('service:llama-swap')['owned']:
            return False
        if not self.is_llama_swap_running():
            return True
        engine = LlamaSwapEngine()
        if not engine.unload_models():
            # Some Windows llama-swap builds cannot signal a CREATE_NO_WINDOW child.
            # Only the independently identified Station-owned process tree may be stopped.
            return self.stop_llama_swap()
        try:
            if not engine.request('/running', timeout=5).get('running', []):
                return True
            return self.stop_llama_swap()
        except Exception:
            return False
    def start_all(self):
        return {'llama_swap': self.start_llama_swap()}
    def stop_all(self):
        success = True
        for key in list(supervisor.records):
            if key.startswith('service:'):
                success = supervisor.stop(key)['success'] and success
        return success

pm = ProcessManager()

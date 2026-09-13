"""One control center per data directory, with a loopback SHOW-only channel."""
import hashlib
import socket
import threading
from .paths import data_dir

class StationInstance:
    def __init__(self, directory=None):
        key=str(directory or data_dir()).casefold().encode('utf-8')
        self.port=47000+int(hashlib.sha256(key).hexdigest()[:8],16)%1500
        self.server=None;self.stop=threading.Event()
    def acquire(self):
        server=socket.socket();server.settimeout(.5)
        try:server.bind(('127.0.0.1',self.port));server.listen(3)
        except OSError:
            server.close()
            try:
                with socket.create_connection(('127.0.0.1',self.port),timeout=3) as peer:
                    peer.sendall(b'SHOW\n')
                    if peer.recv(64)==b'LOCAL_AGENT_AI_STATION\n':return False
            except OSError:pass
            raise RuntimeError('Station instance channel is occupied. Close the previous instance and try again.')
        self.server=server;return True
    def listen(self,show):
        def run():
            while not self.stop.is_set():
                try:
                    client,_=self.server.accept()
                    with client:
                        client.settimeout(1)
                        if client.recv(64)==b'SHOW\n':
                            show();client.sendall(b'LOCAL_AGENT_AI_STATION\n')
                except (OSError,AttributeError):pass
        threading.Thread(target=run,daemon=True).start()
    def close(self):
        self.stop.set()
        if self.server:self.server.close()

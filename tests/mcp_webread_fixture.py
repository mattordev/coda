"""Test-only public-peer socket mapping to an owned HTTP fixture, never an operator option."""

import argparse
import importlib.util
import os
from pathlib import Path
import socket
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from utils import public_http


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--port', type=int, required=True)
    args = parser.parse_args()
    args.config.with_suffix('.pid').write_text(str(os.getpid()), encoding='ascii')
    original_socket = socket.socket
    public = '93.184.216.34'

    class MappedSocket:
        def __init__(self, _family):
            self.inner = original_socket(socket.AF_INET, socket.SOCK_STREAM)

        def __getattr__(self, name):
            return getattr(self.inner, name)

        def connect(self, address):
            if address != (public, args.port):
                raise OSError('Unexpected test destination.')
            self.inner.connect(('127.0.0.1', args.port))

        def getpeername(self):
            return public, args.port

    def answers(host, port, **_):
        if host != 'public.example' or port != args.port:
            raise OSError('Unexpected test hostname.')
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (public, port))]

    public_http.socket.getaddrinfo = answers
    public_http._open_socket = MappedSocket
    spec = importlib.util.spec_from_file_location('controlled_webread', ROOT / 'integrations/coda-webread/server.py')
    reader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reader)
    reader.CONFIG_PATH = args.config
    reader.server.run(transport='stdio')


if __name__ == '__main__':
    main()

"""Real browser MCP against an owned HTTP fixture via a test-only public dialer."""

import argparse
import importlib.util
import os
from pathlib import Path
import socket
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from utils import public_http


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--port',type=int,required=True)
    args=parser.parse_args()
    with args.config.with_suffix('.pids').open('a') as file:
        file.write(str(os.getpid())+'\n')
    original=socket.socket
    class MappedSocket:
        def __init__(self,_family):
            self.inner=original(socket.AF_INET,socket.SOCK_STREAM)
        def __getattr__(self,name):
            return getattr(self.inner,name)
        def connect(self,address):
            if address != ('93.184.216.34',args.port):
                raise OSError('Unexpected test destination.')
            self.inner.connect(('127.0.0.1',args.port))
        def getpeername(self):
            return '93.184.216.34',args.port
    def resolve(_self,host,port,guard):
        guard.check()
        if host != 'public.example' or port != args.port:
            raise OSError('Unexpected test hostname.')
        return [(socket.AF_INET,socket.SOCK_STREAM,6,'',('93.184.216.34',port))]
    public_http.Resolver.lookup=resolve
    public_http._open_socket=MappedSocket
    spec=importlib.util.spec_from_file_location('controlled_browser',ROOT/'integrations/coda-browser/server.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    module.CONFIG_PATH=args.config
    module.server.run(transport='stdio')


if __name__=='__main__':
    main()

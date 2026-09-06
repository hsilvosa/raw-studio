"""Serialized JSON-RPC client. Only darktable writes developed pixels."""
import json
import queue
import subprocess
import threading
import time
from .settings import DARKTABLE, STATE


class Darktable:
    def __init__(self):
        self.lock = threading.RLock()
        self.process = None
        self.seq = 0
        self.log = None

    def _start(self):
        if self.process and self.process.poll() is None:
            return
        if not DARKTABLE.is_file():
            raise RuntimeError('No se encuentra darktable-mcp. Configura DARKTABLE_MCP.')
        for name in ('config', 'cache'):
            (STATE / name).mkdir(parents=True, exist_ok=True)
        self.messages = queue.Queue()
        self.log = (STATE / 'darktable.log').open('a', encoding='utf-8')
        self.process = subprocess.Popen([
            str(DARKTABLE), '--core', '--configdir', (STATE / 'config').as_posix(),
            '--cachedir', (STATE / 'cache').as_posix(), '--library', ':memory:',
            '--conf', 'write_sidecar_files=never', '--disable-opencl',
        ], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log,
            text=True, encoding='utf-8', errors='replace',
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        process, messages = self.process, self.messages
        def read():
            for line in process.stdout:
                try:
                    messages.put(json.loads(line))
                except json.JSONDecodeError:
                    pass
            messages.put({'eof': True})
        threading.Thread(target=read, daemon=True).start()
        self._rpc('initialize', {'protocolVersion': '2024-11-05', 'capabilities': {}, 'clientInfo': {'name': 'revelado-local', 'version': '0.1'}})
        self._send({'jsonrpc': '2.0', 'method': 'notifications/initialized'})

    def _send(self, value):
        self.process.stdin.write(json.dumps(value) + '\n')
        self.process.stdin.flush()

    def _rpc(self, method, params):
        self.seq += 1
        identifier = self.seq
        self._send({'jsonrpc': '2.0', 'id': identifier, 'method': method, 'params': params})
        deadline = time.monotonic() + 180
        while True:
            try:
                message = self.messages.get(timeout=max(.01, deadline - time.monotonic()))
            except queue.Empty:
                self.close()
                raise RuntimeError('darktable no respondió en 180 segundos.')
            if message.get('eof'):
                raise RuntimeError('darktable terminó. Consulta .studio/darktable.log.')
            if message.get('id') == identifier:
                if 'error' in message:
                    raise RuntimeError(str(message['error']))
                return message['result']

    def call(self, name, arguments):
        if name not in {'export', 'module_schema', 'image_stats'}:
            raise ValueError('Operación MCP no permitida')
        with self.lock:
            self._start()
            result = self._rpc('tools/call', {'name': name, 'arguments': arguments})
            if result.get('isError'):
                raise RuntimeError(str(result.get('content')))
            texts = [x['text'] for x in result.get('content', []) if x['type'] == 'text']
            payload = json.loads(texts[0]) if texts else result
            if isinstance(payload, dict) and (payload.get('ok') is False or 'error' in payload):
                raise RuntimeError(str(payload))
            return payload

    def export(self, source, stack, output, width=1600):
        output.parent.mkdir(parents=True, exist_ok=True)
        self.call('export', {'input': {'path': source.as_posix()}, 'stack': stack,
                            'out_path': output.as_posix(), 'width': width, 'height': width})
        if not output.is_file() or output.stat().st_size == 0:
            raise RuntimeError('darktable no escribió la imagen.')

    def close(self):
        with self.lock:
            if self.process:
                if self.process.poll() is None:
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                        self.process.wait()
                self.process = None
            if self.log:
                self.log.close()
                self.log = None

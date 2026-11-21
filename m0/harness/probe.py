import json
import platform
from http.server import BaseHTTPRequestHandler, HTTPServer
from importlib.metadata import version
from pathlib import Path
from threading import Thread

from deepseek_harness import DeepSeekHarness

IMAGE = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNQ6rgLAAJXAYihuo7TAAAAAElFTkSuQmCC'
TOOLS = ['read_image', 'skill', 'working_directory']


class FixtureProvider(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers['Content-Length']))
        if self.path != '/v1/messages':
            self.send_error(404)
            return
        request = json.loads(body)
        tools = sorted(tool['name'] for tool in request['tools'])
        if tools != TOOLS:
            raise ValueError(f'Unexpected tools: {tools}')
        blocks = [block for message in request['messages'] for block in message['content']]
        if not any(block['type'] == 'image' and block['source'].get('data') == IMAGE for block in blocks):
            raise ValueError('Missing SDK image content')
        if not any('M0 read-only skill discovery probe.' in block.get('text', '') for block in blocks):
            raise ValueError('Missing skill catalog entry')
        results = [block for block in blocks if block['type'] == 'tool_result']
        if results:
            result = results[-1]
            if result['tool_use_id'] != 'probe1' or result.get('is_error') or 'M0_SKILL_BODY_71C9' not in str(result['content']):
                raise ValueError(f'Skill was not loaded: {result}')
            block = {'type': 'text', 'text': 'probe passed'}
            reason = 'end_turn'
        else:
            block = {'type': 'tool_use', 'id': 'probe1', 'name': 'skill', 'input': {'name': 'm0-check'}}
            reason = 'tool_use'
        events = [
            {'type': 'message_start', 'message': {'id': 'm0', 'model': request['model'],
                                                   'usage': {'input_tokens': 1, 'output_tokens': 0}}},
            {'type': 'content_block_start', 'index': 0, 'content_block': block},
            {'type': 'content_block_stop', 'index': 0},
            {'type': 'message_delta', 'delta': {'stop_reason': reason}, 'usage': {'output_tokens': 1}},
            {'type': 'message_stop'},
        ]
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.end_headers()
        for event in events:
            self.wfile.write(f'data: {json.dumps(event)}\n\n'.encode())


workspace = Path('/tmp/work')
workspace.mkdir()
with HTTPServer(('127.0.0.1', 0), FixtureProvider) as server:
    Thread(target=server.serve_forever, daemon=True).start()
    with DeepSeekHarness(provider='deepseek-official', model='deepseek-flash',
                         api_key='local-probe', base_url=f'http://127.0.0.1:{server.server_port}',
                         profile='sdk-minimal', patches=('/opt/probe/profile.patch.yml',),
                         cwd=str(workspace), dsh_home='/tmp/dsh', request_timeout_seconds=30) as harness:
        result = harness.run([{'type': 'text', 'text': 'Run the environment probe.'},
                              {'type': 'image', 'data': IMAGE, 'mimeType': 'image/png'}], session_id='m0')
        if result.finish_reason != 'completed' or result.final_response != 'probe passed':
            raise RuntimeError(f'{result.finish_reason}: {result.final_response}; {result.events[-2:]}')
print(json.dumps({'provider': 'loopback fixture', 'sdk': version('deepseek-harness-sdk'),
                  'runtime': version('deepseek-harness-runtime-bin'), 'python': platform.python_version(),
                  'tools': TOOLS, 'loaded_skill': 'm0-check', 'image_transport': 'inline PNG'}))

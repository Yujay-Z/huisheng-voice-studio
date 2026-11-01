import argparse
import json
from pathlib import Path
import re
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse
import webbrowser

from voiceclone import service as svc
from voiceclone import audio, separation

TOKEN = secrets.token_urlsafe(32)
STATIC = svc.ROOT / 'web'
STATIC_TYPES = {'index.html': 'text/html', 'style.css': 'text/css', 'app.js': 'text/javascript', 'audio-editor.js': 'text/javascript', 'results.js': 'text/javascript'}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def send(self, value, status=200, mime='application/json; charset=utf-8', download=None):
        raw = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; media-src 'self' blob:; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
        if download:
            self.send_header('Content-Disposition', f'attachment; filename="{download}"')
        self.end_headers()
        self.wfile.write(raw)

    def local_request(self):
        host = self.headers.get('Host', '')
        expected = f'127.0.0.1:{self.server.server_port}'
        if host != expected:
            raise ValueError('只允许通过本机地址访问。')
        origin = self.headers.get('Origin')
        if origin and origin != 'http://' + expected:
            raise ValueError('不允许来自其他网站的请求。')

    def do_GET(self):
        try:
            self.local_request()
            path = unquote(urlparse(self.path).path)
            if path == '/api/state':
                return self.send({'api_version': 3, 'build': 'editor-variants-v1', 'supports_shutdown': True, 'token': TOKEN, 'engines': svc.readiness(),
                                  'model': svc.model_status(), 'audio_processing': separation.readiness(svc.ROOT),
                                  'jobs': [svc.public(j) for j in reversed(list(svc.JOBS.values()))]})
            if path == '/api/model':
                return self.send(svc.model_status())
            if path.startswith('/api/audio/task/'):
                return self.send(separation.status(separation.TASKS[path.rsplit('/', 1)[1]]))
            if path.startswith('/api/job/'):
                return self.send(svc.public(svc.JOBS[path.rsplit('/', 1)[1]]))
            if path in ['/', '/style.css', '/app.js', '/audio-editor.js', '/results.js']:
                file = STATIC / ('index.html' if path == '/' else path[1:])
                return self.send(file.read_bytes(), mime=STATIC_TYPES[file.name] + '; charset=utf-8')
            m = re.fullmatch(r'/audio/reference/([a-f0-9]{32})', path)
            if m:
                download_name = {'download=1': 'audio.wav', 'download=vocals': 'vocals.wav',
                                 'download=background': 'background.wav'}.get(urlparse(self.path).query)
                return self.send(svc.upload_path(m[1]).read_bytes(), mime='audio/wav',
                                 download=download_name)
            m = re.fullmatch(r'/(audio/variant|download/variant)/([a-f0-9]{32})/([0-2])', path)
            if m:
                from voiceclone.batch import audio_file
                file = audio_file(svc.JOBS[m[2]], int(m[3]))
                return self.send(file.read_bytes(), mime='audio/wav',
                                 download=f'voice-{int(m[3]) + 1}.wav' if m[1] == 'download/variant' else None)
            m = re.fullmatch(r'/audio/segment/([a-f0-9]{32})/(\d+)', path)
            if m:
                job = svc.JOBS[m[1]]
                if int(m[2]) >= len(job['segments']):
                    raise KeyError()
                return self.send((svc.DATA / 'jobs' / m[1] / (m[2] + '.wav')).read_bytes(), mime='audio/wav')
            m = re.fullmatch(r'/(audio/final|download|report|log)/([a-f0-9]{32})', path)
            if m:
                job = svc.JOBS[m[2]]
                directory = svc.DATA / 'jobs' / m[2]
                if m[1] == 'report':
                    return self.send((directory / 'report.json').read_bytes(), download='task.json')
                if m[1] == 'log':
                    if job.get('variants'):
                        logs = []
                        for variant in job['variants']:
                            file = svc.DATA / 'jobs' / variant['id'] / 'engine.log'
                            logs.append(f'\n=== 第 {variant["number"]} 次 ===\n'.encode('utf-8') + (file.read_bytes() if file.exists() else b''))
                        return self.send(b''.join(logs), mime='text/plain; charset=utf-8')
                    file = directory / 'engine.log'
                    return self.send(file.read_bytes() if file.exists() else b'', mime='text/plain; charset=utf-8')
                if job['state'] != 'ready' or not job.get('generated'):
                    raise ValueError('音频尚未生成完成。')
                file = directory / 'candidate.wav'
                import hashlib
                if hashlib.sha256(file.read_bytes()).hexdigest() != job['sha256']:
                    raise ValueError('音频校验摘要不一致。')
                return self.send(file.read_bytes(), mime='audio/wav', download='voice.wav' if m[1] == 'download' else None)
            self.send({'error': '未找到'}, 404)
        except (KeyError, FileNotFoundError):
            self.send({'error': '未找到'}, 404)
        except ValueError as exc:
            self.send({'error': str(exc)}, 403)
        except Exception as exc:
            self.send({'error': str(exc)}, 500)

    def do_POST(self):
        try:
            self.local_request()
            if self.headers.get('X-Local-Token') != TOKEN:
                return self.send({'error': '会话已失效，请刷新页面。'}, 403)
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 64 * 1024 * 1024:
                raise ValueError('文件过大或请求为空，最大支持 64 MB。')
            path = urlparse(self.path).path
            if path == '/api/upload':
                name = unquote(self.headers.get('X-Filename', 'audio.wav'))
                suffix = Path(name).suffix.lower()
                if suffix not in ['.wav', '.mp3', '.flac', '.m4a', '.ogg']:
                    raise ValueError('不支持这种音频格式。')
                # Separate source filename even for wav uploads.
                return self.send(svc.prepare_upload(self.rfile.read(size), '.source' + suffix))
            if size > 100000:
                raise ValueError('请求内容过大。')
            data = json.loads(self.rfile.read(size))
            if path == '/api/shutdown':
                self.send({'ok': True})
                self.server.shutdown()
                return
            if path == '/api/jobs':
                return self.send(svc.create_job(data))
            if path == '/api/audio/crop':
                return self.send(audio.crop_upload(svc.upload_path(data.get('reference')), svc.DATA / 'uploads', data.get('start'), data.get('end')))
            if path == '/api/audio/separate':
                return self.send(separation.start(svc.ROOT, svc.DATA, svc.upload_path(data.get('reference')), data))
            if path == '/api/audio/cancel':
                separation.TASKS[data['id']]['_cancel'].set()
                return self.send({'ok': True})
            if path == '/api/transcribe':
                # No background download: installation must have finished first.
                if svc.ACTIVE.locked():
                    raise ValueError('请等待当前生成任务结束后识别参考音频。')
                actual = svc.transcribe(svc.upload_path(data.get('reference')), data.get('language', 'zh'), offline=True)
                return self.send({'text': actual})
            if path in ['/api/cancel', '/api/approve']:
                job = svc.JOBS[data['id']]
                if path == '/api/cancel':
                    job['_cancel'].set()
                    return self.send({'ok': True})
                return self.send(svc.approve(job))
            self.send({'error': '未找到'}, 404)
        except (ValueError, KeyError) as exc:
            self.send({'error': str(exc)}, 400)
        except Exception as exc:
            self.send({'error': str(exc)}, 500)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    server = ThreadingHTTPServer(('127.0.0.1', svc.CONFIG['port']), Handler)
    server.daemon_threads = True
    url = f'http://127.0.0.1:{server.server_port}'
    print('音色工作室已启动：' + url, flush=True)
    svc.start_preload()
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for job in svc.JOBS.values():
            job['_cancel'].set()
        svc.WORKER.close()
        separation.shutdown()
        server.server_close()


if __name__ == '__main__':
    main()

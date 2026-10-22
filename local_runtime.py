"""Run n8n and Elf on Windows without Docker. Logs/state stay in LocalAppData.

Usage: python local_runtime.py init | start | status
No key is ever displayed. init imports credentials only on the first run.
"""
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import time
from urllib import request, error

from elf import ROOT, load_env

load_env()
RUNTIME = Path(os.getenv('ELF_HOME',str(Path(os.environ.get('LOCALAPPDATA', str(ROOT))) / 'LineWorkElf'))) / 'runtime'
STATE = RUNTIME.parent / 'state'
N8N = RUNTIME / 'node_modules' / 'n8n' / 'bin' / 'n8n'
NODE = str(RUNTIME.parent/'node.exe') if (RUNTIME.parent/'node.exe').exists() else (shutil.which('node') or r'C:\Program Files\nodejs\node.exe')
STATE.mkdir(parents=True, exist_ok=True)
env = dict(os.environ)
env.update(N8N_USER_FOLDER=str(STATE), N8N_ENCRYPTION_KEY=os.environ.get('N8N_ENCRYPTION_KEY', ''),
           GENERIC_TIMEZONE='Asia/Taipei', TZ='Asia/Taipei',
           N8N_LISTEN_ADDRESS='127.0.0.1', N8N_PORT='5678',
           WEBHOOK_URL=os.getenv('WEBHOOK_URL', 'http://localhost:5678/'),
           N8N_DIAGNOSTICS_ENABLED='false', N8N_PERSONALIZATION_ENABLED='false',
           EXECUTIONS_DATA_SAVE_ON_SUCCESS='none', EXECUTIONS_DATA_SAVE_ON_ERROR='none',
           EXECUTIONS_DATA_SAVE_MANUAL_EXECUTIONS='false', N8N_BLOCK_ENV_ACCESS_IN_NODE='true')
for name in ('OPENROUTER_API_KEY', 'LINE_CHANNEL_SECRET', 'LINE_CHANNEL_ACCESS_TOKEN', 'LINE_OWNER_USER_ID', 'INTERNAL_TOKEN'):
    env.pop(name, None)


def command(*args):
    subprocess.run([NODE, str(N8N), *args], env=env, cwd=ROOT, check=True)


def healthy(url):
    try:
        with request.urlopen(url, timeout=3) as response:
            return response.status == 200
    except (error.URLError, TimeoutError):
        return False


def starting(pid, expected):
    if os.name != 'nt' or not isinstance(pid, int):
        return False
    result = subprocess.run(['powershell', '-NoProfile', '-Command',
        f'(Get-CimInstance Win32_Process -Filter "ProcessId={pid}").CommandLine'],
        capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
    return str(expected).replace('/', '\\').lower() in result.stdout.replace('/', '\\').lower()


def initialize():
    if not N8N.exists():
        raise SystemExit('n8n is not installed. Run install-local.ps1 first.')
    marker = STATE / 'elf-imported.json'
    if marker.exists():
        print('Existing import preserved. Start the services to continue.')
        return
    if healthy('http://127.0.0.1:5678/healthz'):
        raise SystemExit('Stop n8n before importing workflows.')
    token = os.getenv('INTERNAL_TOKEN', '')
    if not token or not env['N8N_ENCRYPTION_KEY']:
        raise SystemExit('Run setup.py first.')
    credentials = [
        {'id': 'elf-internal-auth', 'name': 'Elf internal auth', 'type': 'httpHeaderAuth',
         'data': {'name': 'Authorization', 'value': 'Bearer ' + token}},
        {'id': 'elf-free-gateway', 'name': 'Elf OpenRouter FREE gateway', 'type': 'openAiApi',
         'data': {'apiKey': token, 'url': 'http://127.0.0.1:8787/v1'}}]
    credential_file = STATE / 'elf-credentials.json'
    try:
        credential_file.write_text(json.dumps(credentials), encoding='utf-8')
        subprocess.run([sys.executable, str(ROOT / 'build_workflow.py'), '--local'], check=True)
        command('import:credentials', '--input=' + str(credential_file))
        command('import:workflow', '--input=' + str(ROOT / 'workflow.local.json'))
        marker.write_text(json.dumps({'imported': True}), encoding='utf-8')
    finally:
        credential_file.unlink(missing_ok=True)


def start(only=None):
    if not (STATE / 'elf-imported.json').exists():
        raise SystemExit('Run python local_runtime.py init first.')
    process_file = STATE / 'processes.json'
    processes = json.loads(process_file.read_text(encoding='utf-8-sig')) if process_file.exists() else {}
    for name, url, args in [
        ('elf', 'http://127.0.0.1:8787/health', [sys.executable, str(ROOT / 'elf.py')]),
        ('n8n', 'http://127.0.0.1:5678/healthz', [NODE, str(N8N), 'start']),
        ('calendar', 'http://127.0.0.1:8791/health', [sys.executable, str(ROOT / 'calendar_oauth.py')]),
        ('relay', 'http://127.0.0.1:8790/health', [sys.executable, str(ROOT / 'webhook_relay.py')])]:
        if only is not None and name not in only:
            continue
        if healthy(url):
            print(name + ' is already running.')
            continue
        if starting(processes.get(name), args[1]):
            print(name + ' process is running; health endpoint may still be starting.')
            continue
        child_env = {**env,'WEBHOOK_URL':os.getenv('WEBHOOK_URL',env['WEBHOOK_URL'])} if name == 'n8n' else {**os.environ, 'ELF_DB': str(STATE / 'elf.sqlite3'), 'ELF_BIND': '127.0.0.1'}
        with (STATE / (name + '.log')).open('ab') as log:
            proc = subprocess.Popen(args, cwd=ROOT, env=child_env, stdout=log, stderr=log,
                                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        processes[name] = proc.pid
        print(name + ' started.')
    process_file.write_text(json.dumps(processes), encoding='utf-8')


if __name__ == '__main__':
    action = sys.argv[1] if len(sys.argv) > 1 else 'status'
    if action == 'init':
        initialize()
    elif action == 'start':
        start()
    elif action == 'status':
        print(json.dumps({'elf': healthy('http://127.0.0.1:8787/health'),
                          'n8n': healthy('http://127.0.0.1:5678/healthz'),
                          'relay': healthy('http://127.0.0.1:8790/health'),
                          'calendar': healthy('http://127.0.0.1:8791/health'),
                          'runtime_installed': N8N.exists()}))
    else:
        raise SystemExit('Usage: python local_runtime.py init | start | status')

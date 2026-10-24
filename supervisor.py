"""Local Windows service supervisor. No prompts, credentials or chat bodies in logs."""
import ctypes
import json
import os
from pathlib import Path
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib import request

import local_runtime as runtime
from elf import load_env
from line_api import api, save_settings

STATE = runtime.STATE
STATUS = STATE / 'supervisor-status.json'
TZ = timezone(timedelta(hours=8))
HIDDEN = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0


def write_json(path, data):
    tmp = path.with_name(path.name + '.' + str(os.getpid()) + '.pending')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)


def log(event, **meta):
    path = STATE / 'supervisor.log'
    if path.exists() and path.stat().st_size > 2_000_000:
        path.replace(path.with_suffix('.previous.log'))
    with path.open('a', encoding='utf-8') as f:
        f.write(json.dumps({'time': datetime.now(TZ).isoformat(), 'event': event, **meta}) + '\n')


def process_map():
    try:
        return json.loads((STATE / 'processes.json').read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return {}


def keep_one():
    import msvcrt
    handle = (STATE / 'supervisor.lock').open('a+b')
    handle.seek(0)
    if not handle.read(1):
        handle.write(b'0'); handle.flush()
    handle.seek(0)
    try:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        handle.close()
        return None
    return handle


def public_url(text):
    matches = re.findall(r'https://[a-z0-9]+(?:-[a-z0-9]+)*\.trycloudflare\.com', text)
    return matches[-1] if matches else None


def get_json(url, timeout=8):
    with request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


def backup_daily():
    base = STATE / 'backups'
    base.mkdir(exist_ok=True)
    today = datetime.now(TZ).strftime('%Y-%m-%d')
    target = base / today
    if (target / 'complete.json').exists():
        return
    target.mkdir(exist_ok=True)
    databases=[('elf.sqlite3', STATE/'elf.sqlite3'),('n8n.sqlite3', STATE/'.n8n/database.sqlite')]
    for path in (STATE/'members').glob('*.sqlite3'):
        if not path.is_symlink() and re.fullmatch(r'[0-9a-f]{64}\.sqlite3',path.name):
            databases.append(('members/'+path.name,path))
    for name, path in databases:
        if path.exists():
            (target/name).parent.mkdir(parents=True,exist_ok=True)
            src = sqlite3.connect(path, timeout=10)
            dst = sqlite3.connect(target/name)
            try:
                src.backup(dst)
            finally:
                dst.close(); src.close()
    for name, path in [('settings.env', runtime.ROOT/'.env'),
                       ('google-client.json', STATE/'google-client.json'),
                       ('google-token.json', STATE/'google-token.json')]:
        if path.exists():
            shutil.copy2(path, target/name)
    write_json(target/'complete.json', {'created':datetime.now(TZ).isoformat()})
    # Remove only this supervisor's dated directories inside its backup root.
    cutoff = today_to_date(today) - timedelta(days=6)
    for candidate in base.iterdir():
        if (candidate.is_dir() and not candidate.is_symlink()
                and re.fullmatch(r'\d{4}-\d{2}-\d{2}', candidate.name)
                and candidate.resolve().parent == base.resolve()
                and today_to_date(candidate.name) < cutoff):
            shutil.rmtree(candidate.resolve())
    log('backup_complete')


def today_to_date(text):
    return datetime.strptime(text, '%Y-%m-%d').date()


def request_missed_digest(now, checked):
    cutoff = now.replace(hour=21, minute=0, second=0, microsecond=0)
    if now < cutoff + timedelta(minutes=5):
        cutoff -= timedelta(days=1)
    day = cutoff.date().isoformat()
    if checked == day:
        return checked
    db = sqlite3.connect(STATE/'elf.sqlite3', timeout=10)
    try:
        pending = db.execute('''SELECT 1 FROM messages m JOIN sources s ON s.id=m.source
            WHERE m.seen=0 AND s.enabled=1 AND m.timestamp<=? AND m.timestamp>=?
            AND NOT EXISTS(SELECT 1 FROM blocked_sources b WHERE b.id=m.source) LIMIT 1''',
            (int(cutoff.timestamp()*1000),int((now-timedelta(days=7)).timestamp()*1000))).fetchone()
        if pending:
            db.execute("INSERT OR REPLACE INTO controls VALUES('digest_requested',1)")
            db.commit()
            log('missed_digest_requested', scheduled_date=day)
        return day
    finally:
        db.close()


class Supervisor:
    def __init__(self):
        self.bad = {}
        self.last_restart = {}
        self.tunnel_started = 0
        self.webhook_ok = False
        self.endpoint = ''
        self.last_network_check = 0
        self.last_backup_check = 0
        self.last_daily_check = ''
        self.health = {}
        self.tunnel_failures = 0

    def local_services(self):
        specs = [('elf',8787,runtime.ROOT/'elf.py'),
                 ('n8n',5678,runtime.N8N),
                 ('calendar',8791,runtime.ROOT/'calendar_oauth.py'),
                 ('relay',8790,runtime.ROOT/'webhook_relay.py')]
        processes = process_map()
        now = time.time()
        for name, port, path in specs:
            url = f'http://127.0.0.1:{port}/' + ('healthz' if name=='n8n' else 'health')
            ok = runtime.healthy(url)
            self.health[name] = ok
            self.last_restart.setdefault(name,now)
            if ok:
                self.last_restart[name]=now
            self.bad[name] = 0 if ok else self.bad.get(name,0)+1
            pid = processes.get(name)
            # PID ownership must be checked again before terminating anything.
            if (not ok and self.bad[name]>=6 and
                    now-self.last_restart.get(name,0)>240 and runtime.starting(pid,str(path))):
                if runtime.starting(pid,str(path)):
                    os.kill(pid,signal.SIGTERM)
                    self.last_restart[name] = now
                    self.bad[name] = 0
                    log('unresponsive_service_restarted',service=name)
        if not all(self.health.values()):
            self.webhook_ok=False
            installing=STATE.parent.name=='LineWorkElfService' and not (STATE.parent/'installed.txt').exists()
            runtime.start(only=('elf','calendar','relay') if installing else None)

    def tunnel(self):
        binary = STATE.parent/'bin/cloudflared.exe'
        processes = process_map()
        pid = processes.get('cloudflared')
        if not runtime.starting(pid,str(binary)):
            if time.time()-self.tunnel_started < 60:
                return
            with (STATE/'tunnel.log').open('wb') as err, (STATE/'tunnel.stdout.log').open('wb') as out:
                child = subprocess.Popen([str(binary),'tunnel','--no-autoupdate',
                    '--metrics','127.0.0.1:8792','--url','http://127.0.0.1:8790'],
                    stdout=out,stderr=err,cwd=runtime.ROOT,creationflags=HIDDEN)
            processes['cloudflared'] = child.pid
            write_json(STATE/'processes.json', processes)
            self.tunnel_started = time.time()
            self.webhook_ok = False
            self.last_network_check = 0
            log('tunnel_started')
        text = (STATE/'tunnel.log').read_text(encoding='utf-8',errors='replace')[-64000:]
        url = public_url(text)
        if not url or not all(self.health.values()):
            return
        if time.time()-self.last_network_check < (300 if self.webhook_ok else 30):
            return
        self.last_network_check = time.time()
        public_ok=False
        try:
            if not get_json(url+'/health').get('ok'):
                raise ValueError('public relay unhealthy')
            public_ok=True
            self.tunnel_failures=0
            load_env()
            endpoint = url+'/webhook/line-work-elf'
            existing = api('GET','/v2/bot/channel/webhook/endpoint')
            if existing.get('endpoint') != endpoint:
                api('PUT','/v2/bot/channel/webhook/endpoint',{'endpoint':endpoint})
                save_settings({'WEBHOOK_URL':url})
                log('line_endpoint_updated')
            result = api('POST','/v2/bot/channel/webhook/test',{})
            self.webhook_ok = bool(result.get('success')) and existing.get('active',True)
            self.endpoint = endpoint
            log('webhook_check',ok=self.webhook_ok,status=result.get('statusCode'))
        except Exception as exc:
            self.webhook_ok = False
            log('webhook_check_failed',error_type=type(exc).__name__)
            if not public_ok:
                self.tunnel_failures+=1
                if self.tunnel_failures>=6:
                    try:
                        # Avoid churning tunnel URLs when the whole network is down.
                        load_env();api('GET','/v2/bot/info')
                        if runtime.starting(process_map().get('cloudflared'),str(binary)):
                            os.kill(process_map()['cloudflared'],signal.SIGTERM)
                            self.tunnel_failures=0
                            log('unreachable_tunnel_restarted')
                    except Exception:
                        pass

    def tick(self):
        self.local_services()
        self.tunnel()
        if self.health.get('elf'):
            self.last_daily_check = request_missed_digest(datetime.now(TZ),self.last_daily_check)
        if time.time()-self.last_backup_check > 3600:
            backup_daily()
            self.last_backup_check = time.time()
        write_json(STATUS,{'checked_at':datetime.now(TZ).isoformat(),
            'pid':os.getpid(),'services':self.health,'webhook_verified':self.webhook_ok,
            'endpoint':self.endpoint,'last_daily_check':self.last_daily_check})


def main():
    lock = keep_one()
    if lock is None:
        return
    if os.name=='nt':
        # Prevent automatic system sleep while serving; never keep the display on.
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
    log('supervisor_started',state=str(STATE),marker_exists=(STATE/'elf-imported.json').exists(),
        username=os.getenv('USERNAME'),
        n8n_present=runtime.N8N.exists(),env_present=(runtime.ROOT/'.env').exists())
    manager = Supervisor()
    while True:
        try:
            manager.tick()
        except Exception as exc:
            log('supervisor_tick_failed',error_type=type(exc).__name__)
        time.sleep(20)


if __name__=='__main__':
    try:
        main()
    except BaseException as exc:
        log('supervisor_exit',error_type=type(exc).__name__,
            frames=__import__('traceback').format_tb(exc.__traceback__))
        raise

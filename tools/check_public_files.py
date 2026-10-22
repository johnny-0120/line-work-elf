"""Inspect tracked/indexed files before publication. Prints paths, never secrets."""
from pathlib import Path
import re
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
TOKEN_PATTERNS=[
    re.compile(r'sk-(?:or-v1-|proj-)[A-Za-z0-9_-]{20,}'),
    re.compile(r'(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})'),
    re.compile(r'AIza[A-Za-z0-9_-]{30,}'),
    re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    re.compile(r'\b[UC][0-9a-f]{32}\b'),
]


def violations(paths):
    problems=[]
    for relative in paths:
        path=Path(relative)
        name=path.name.lower()
        if ((name.startswith('.env') and name!='.env.example')
            or name.endswith(('.sqlite','.sqlite3','.db','.log','.pending','.zip','.pptx','.pdf','.env','.pem','.key'))
            or (name.endswith('.json') and relative.replace('\\','/')!='workflow.json')
            or any(part in ('node_modules','data','state','backups','.secrets','runtime') for part in path.parts)):
            problems.append((relative,'private/runtime file'))
            continue
        file=ROOT/path
        if file.is_symlink():
            problems.append((relative,'symlink'))
            continue
        if not file.is_file() or file.suffix.lower()=='.png':
            continue
        try:
            text=file.read_text(encoding='utf-8-sig')
        except UnicodeError:
            problems.append((relative,'unexpected binary file'))
            continue
        if any(pattern.search(text) for pattern in TOKEN_PATTERNS):
            problems.append((relative,'possible secret or private LINE identifier'))
    return problems


def main():
    result=subprocess.run(['git','ls-files','-z'],cwd=ROOT,capture_output=True,check=True)
    paths=result.stdout.decode('utf-8').rstrip('\0').split('\0')
    problems=violations([p for p in paths if p])
    for path,reason in problems:
        print(f'{path}: {reason}')
    if not problems:
        print(f'Public-file scan passed ({len(paths)} tracked files).')
    return bool(problems)


if __name__=='__main__':
    sys.exit(main())

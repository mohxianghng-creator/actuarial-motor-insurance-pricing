"""Check the recorded environment; never install or download packages."""
import importlib.metadata
from pathlib import Path
import sys

def check():
    failures=[]
    for raw in Path(__file__).with_name("requirements.lock.txt").read_text().splitlines():
        if not raw.strip() or raw.startswith('#'):continue
        name,wanted=raw.split('==')
        try:actual=importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:actual='MISSING'
        if actual!=wanted:failures.append(f'{name}: expected {wanted}, installed {actual}')
    print('Python:',sys.version)
    if failures:
        print('\n'.join(failures));return 1
    print('All locked package versions match. No network access was used.');return 0

if __name__=='__main__':raise SystemExit(check())

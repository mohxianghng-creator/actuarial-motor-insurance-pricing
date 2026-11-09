"""Offline, single-command research build. No network access is used."""
import os
for k in ["OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"]:
    os.environ[k]="1"
from pathlib import Path
import argparse
from research import build

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--config",default=str(Path(__file__).with_name("config.json")))
    p.add_argument("--data")
    p.add_argument("--output",default=str(Path(__file__).with_name("results")))
    p.add_argument("--cache",default=str(Path(__file__).with_name(".cache")))
    p.add_argument("--force",action="store_true",help="Ignore reusable fold caches")
    args=p.parse_args()
    build(args.config,args.data,args.output,args.cache,args.force)

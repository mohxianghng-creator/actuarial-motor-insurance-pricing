"""Portable scratch paths; never infer a writable directory from extraction depth."""
import os
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
WORK=Path(os.environ.get('MOTOR_FORMAT_WORK',str(Path(__file__).resolve().parent/'.work'))).resolve()
WORK.mkdir(parents=True,exist_ok=True)

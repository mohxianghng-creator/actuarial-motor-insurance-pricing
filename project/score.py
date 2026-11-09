"""Score new risks with a trusted v2 research model, without network access."""
import os
for k in ["OMP_NUM_THREADS","OPENBLAS_NUM_THREADS","MKL_NUM_THREADS"]:os.environ[k]="1"
from pathlib import Path
import argparse
import joblib
import pandas as pd

if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("input_csv")
    p.add_argument("--engine",default=str(Path(__file__).with_name("results")/"pricing_engine.joblib"))
    p.add_argument("--output",default="new_quotes.csv")
    a=p.parse_args()
    joblib.load(a.engine).quote(pd.read_csv(a.input_csv)).to_csv(a.output,index=False)

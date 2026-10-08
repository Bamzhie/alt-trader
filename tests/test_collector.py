"""Collector tests: dedupe, vol+amount preserved."""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from proto import collector as col
FAILURES=[]
def check(n,c,d=""):
    print(f"  {'PASS' if c else 'FAIL'}  {n}"+("" if c else f"  {d}"))
    if not c: FAILURES.append(n)
tmp=tempfile.mkdtemp()
bars=[{"ts":100+i,"o":1.0,"h":1.1,"l":0.9,"c":1.0+i*0.01,"vol":10.0,"amount":10.0+i*0.1} for i in range(5)]
n1=col.append_bars(tmp,"TEST",bars)
n2=col.append_bars(tmp,"TEST",bars)
check("first write 5", n1==5, str(n1))
check("rewrite dedupes 0 new", n2==0, str(n2))
rows=col.read_bars(tmp,"TEST")
check("read back 5", len(rows)==5, str(len(rows)))
check("vol+amount present", all("vol" in r and "amount" in r for r in rows))
print("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILED")
sys.exit(1 if FAILURES else 0)

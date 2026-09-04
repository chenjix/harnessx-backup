import json, glob, re, os
d = "/fsx/home/jixuan.chen/harnessx-backup/.benchmarks/tmax/tmax-coev-rep12-i4-r7-traj/"
for f in sorted(glob.glob(d + "task_*.result.json")):
    dd = json.load(open(f))
    if dd.get("reward") != 0:
        continue
    tail = dd.get("final_pytest", {}).get("output_tail", "") or ""
    ag = dd.get("agent", {})
    tid = os.path.basename(f)[:24]
    miss = ("does not exist" in tail) or ("No such file" in tail) or ("FileNotFoundError" in tail)
    fails = [l.strip() for l in tail.splitlines() if l.startswith("FAILED") or ("assert" in l and l.strip().startswith("E"))]
    tag = "MISSING-FILE" if miss else ""
    print(f"=== {tid} exit={ag.get('exit_reason')} steps={ag.get('steps')} fin={ag.get('finished')} {tag}")
    for l in fails[:2]:
        print("    ", l[:150])

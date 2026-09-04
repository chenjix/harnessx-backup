import json, glob, os
d = "/fsx/home/jixuan.chen/harnessx-backup/.benchmarks/tmax/tmax-coev-rep25-i1-r0-traj"
summ = json.load(open(os.path.join(d, "summary.json")))
res = {r["task_id"]: r["reward"] for r in summ["results"]}
rows = []
for f in glob.glob(os.path.join(d, "*.messages.json")):
    tid = os.path.basename(f).replace(".messages.json", "")
    m = json.load(open(f))
    tot = heredoc = editwarn = dup = 0
    prev_args = None
    for x in m:
        for tc in (x.get("tool_calls") or []):
            args = str(tc.get("function", {}).get("arguments", ""))
            tot += 1
            if "<<" in args:
                heredoc += 1
            if args == prev_args:
                dup += 1
            prev_args = args
        c = x.get("content")
        if isinstance(c, list):
            c = " ".join(str(y.get("text", "")) for y in c if isinstance(y, dict))
        if "EditDetection" in str(c):
            editwarn += 1
    rows.append((res.get(tid, "?"), tid, tot, heredoc, dup, editwarn))
rows.sort()
print(f"{'rew':>3} {'task':28} {'calls':>5} {'heredoc':>7} {'dupcmd':>6} {'editwarn':>8}")
for r in rows:
    print(f"{r[0]:>3} {r[1]:28} {r[2]:>5} {r[3]:>7} {r[4]:>6} {r[5]:>8}")
# aggregates
fails = [r for r in rows if r[0] == 0]
passes = [r for r in rows if r[0] == 1]
def agg(rs, k):
    return sum(r[k] for r in rs)
print()
print("FAIL n=%d heredoc=%d dup=%d editwarn=%d" % (len(fails), agg(fails,3), agg(fails,4), agg(fails,5)))
print("PASS n=%d heredoc=%d dup=%d editwarn=%d" % (len(passes), agg(passes,3), agg(passes,4), agg(passes,5)))

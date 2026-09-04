import json, glob, os
os.chdir("/fsx/home/jixuan.chen/harnessx-backup/.benchmarks/tmax/tmax-coev-rep21-i3-r0-traj")
summ = json.load(open("summary.json"))
rew = {r["task_id"]: r["reward"] for r in summ["results"]}
rows = []
for f in glob.glob("*.messages.json"):
    tid = f.replace(".messages.json", "")
    msgs = json.load(open(f))
    cmds = []
    for m in msgs:
        if m.get("role") == "assistant":
            for tc in (m.get("tool_calls") or []):
                cmds.append(tc.get("function", {}).get("arguments", ""))
    best = cur = 0
    prev = None
    for c in cmds:
        if c == prev:
            cur += 1
        else:
            cur = 1
        prev = c
        if cur > best:
            best = cur
    rows.append((tid, rew.get(tid, "?"), len(cmds), best))
rows.sort(key=lambda x: -x[3])
print("task / reward / ncalls / max_consecutive_identical")
for tid, r, n, b in rows:
    print(tid, r, n, b)
thr = 4
loops = [r for r in rows if r[3] >= thr]
passed = sum(1 for r in loops if r[1] == 1)
print("")
print("tasks with consecutive-identical at least", thr, ":", len(loops), "of", len(rows))
print("of those, passed:", passed)

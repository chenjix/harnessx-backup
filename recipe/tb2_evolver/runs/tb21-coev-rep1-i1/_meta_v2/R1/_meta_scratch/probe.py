import json, sys, glob, os

base = "/fsx/home/jixuan.chen/harnessx-backup/.benchmarks/tb2/tb21-coev-rep1-i1-r0-traj"

def load(d):
    fs = [f for f in glob.glob(os.path.join(base, d, "agent", "**", "*.jsonl"), recursive=True)
          if not f.endswith("_trace.jsonl")]
    if not fs:
        return None
    return [json.loads(l) for l in open(fs[0])]

def tail(d, n=8):
    lines = load(d)
    if not lines:
        print("  NO LOG")
        return
    print("== %s == (%d events)" % (d, len(lines)))
    for l in lines[-n:]:
        t = l.get("type")
        msg = l.get("message")
        content = ""
        if isinstance(msg, dict):
            c = msg.get("content")
            content = str(c)[:350]
        extra = {k: l[k] for k in ("exit_reason", "error", "tool", "tool_name", "step", "name") if k in l}
        print("  [%s] %s" % (t, extra))
        if content:
            print("     " + content.replace(chr(10), " ")[:350])

for d in sys.argv[1:]:
    tail(d)
    print()

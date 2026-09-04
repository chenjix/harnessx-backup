import json, glob, os, sys
base = "/fsx/home/jixuan.chen/harnessx-backup/.benchmarks/tb2/tb21-coev-rep1-i1-r0-traj"
def load(d):
    fs=[f for f in glob.glob(os.path.join(base,d,"agent","**","*.jsonl"),recursive=True) if not f.endswith("_trace.jsonl")]
    return [json.loads(l) for l in open(fs[0])] if fs else []
for d in sys.argv[1:]:
    lines=load(d)
    sizes=[]
    for l in lines:
        msg=l.get("message")
        if isinstance(msg,dict):
            sizes.append((len(str(msg.get("content"))), l.get("type"), l.get("step")))
    sizes.sort(reverse=True)
    print("==",d,"total events",len(lines))
    for s in sizes[:6]:
        print("   chars=%-7d type=%-14s step=%s" % s)

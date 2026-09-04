import json,glob,re,collections
d="/fsx/home/jixuan.chen/harnessx-backup/.benchmarks/tmax/tmax-coev-rep25-i1-r0-traj"

def norm(s): return re.sub(r"\s+"," ",str(s).strip())

# HARD error: an explicit nonzero exit with a real error, or a Traceback, or a compiler/parse error.
# Exclude benign: "no output captured", "warning", "command not found"(one-off probe), self-verify probe.
HARD = re.compile(r"traceback \(most recent call last\)|error:|parse error|exception:|segmentation fault|assertionerror|[a-z]*error: ", re.I)
EXCL = re.compile(r"no output captured|^warning|tb.self.verify|self_verify|command not found", re.I)

def hard_sig(text):
    lines=[l.strip() for l in text.splitlines() if l.strip()]
    for l in reversed(lines):
        if EXCL.search(l): 
            continue
        if HARD.search(l):
            s=l.lower()
            s=re.sub(r"0x[0-9a-f]+"," ",s); s=re.sub(r"[0-9]+"," ",s)
            s=re.sub(r"/[^ ]+"," ",s); s=re.sub(r"[^a-z ]"," ",s)
            s=re.sub(r"\s+"," ",s).strip()
            return s[:70]
    return None

for f in sorted(glob.glob(d+"/*.result.json")):
    j=json.load(open(f)); tid=j["task_id"]; rew=j["reward"]
    mf=f.replace(".result.json",".messages.json")
    try: msgs=json.load(open(mf))
    except: continue
    sigs=[]
    for m in msgs:
        if m.get("role")!="tool": continue
        c=m.get("content")
        if isinstance(c,list): c=" ".join(str(x.get("text","")) if isinstance(x,dict) else str(x) for x in c)
        s=hard_sig(str(c))
        if s and len(s)>=8: sigs.append(s)
    cnt=collections.Counter(sigs); top=cnt.most_common(1)
    topn=top[0][1] if top else 0; tops=top[0][0] if top else ""
    if topn>=3 or rew==0:
        print(f"rew={rew} hard_recur={topn:2d} tot_hard={len(sigs):2d} {tid} :: {tops[:48]}")

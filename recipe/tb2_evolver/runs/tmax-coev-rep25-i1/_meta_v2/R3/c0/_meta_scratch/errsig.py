import json,glob,re,collections
d="/fsx/home/jixuan.chen/harnessx-backup/.benchmarks/tmax/tmax-coev-rep25-i1-r0-traj"

def norm(s): return re.sub(r"\s+"," ",str(s).strip())

# error signature extraction: look for classic error markers, take a normalized fingerprint
ERR_PAT = re.compile(r"(traceback|error|exception|exit 1|exit [2-9]|not found|no such file|failed|refused|cannot|invalid|undefined|unresolved|denied)", re.I)

def err_sig(text):
    t=text.lower()
    if not ERR_PAT.search(t): return None
    # pick the last line containing 'error' or a known marker, strip digits/paths
    lines=[l for l in text.splitlines() if l.strip()]
    cand=None
    for l in reversed(lines):
        if re.search(r"error|exception|failed|refused|not found|cannot|invalid|denied", l, re.I):
            cand=l; break
    if cand is None: cand=lines[-1] if lines else text
    s=cand.lower()
    s=re.sub(r"0x[0-9a-f]+"," ",s)
    s=re.sub(r"[0-9]+"," ",s)
    s=re.sub(r"/[^ ]+"," ",s)   # strip paths
    s=re.sub(r"[^a-z ]"," ",s)
    s=re.sub(r"\s+"," ",s).strip()
    return s[:80]

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
        s=err_sig(str(c))
        if s and len(s)>=8: sigs.append(s)
    cnt=collections.Counter(sigs)
    top=cnt.most_common(1)
    topn=top[0][1] if top else 0
    tops=top[0][0] if top else ""
    print(f"rew={rew} top_err_recur={topn:2d} tot_err={len(sigs):2d} {tid}  :: {tops[:50]}")

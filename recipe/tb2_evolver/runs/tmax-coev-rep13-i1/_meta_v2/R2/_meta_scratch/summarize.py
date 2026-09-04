import sys, json, collections, os

traj="/fsx/home/jixuan.chen/harnessx-backup/.benchmarks/tmax/tmax-coev-rep13-i1-r1-traj"

def bash_cmd(tc):
    try:
        args=tc.get('function',{}).get('arguments')
        if isinstance(args,str): args=json.loads(args)
        return (args or {}).get('command','')
    except Exception:
        return ''

for t in sys.argv[1:]:
    p=os.path.join(traj,t+".messages.json")
    if not os.path.exists(p):
        print(t,"MISSING"); continue
    msgs=json.load(open(p))
    ncalls=0; cmds=[]
    for m in msgs:
        for tc in (m.get('tool_calls') or []):
            ncalls+=1
            c=bash_cmd(tc)
            cmds.append(' '.join(c.split())[:80])
    cnt=collections.Counter(cmds)
    top=cnt.most_common(3)
    # last assistant text
    last=""
    for m in reversed(msgs):
        if m.get('role')=='assistant' and m.get('content'):
            last=m['content'] if isinstance(m['content'],str) else str(m['content'])
            break
    print("="*70)
    print(f"{t}  nmsgs={len(msgs)} ncalls={ncalls}")
    print("  top cmds:", top)
    print("  last assistant:", (last[:400].replace(chr(10),' / ')))

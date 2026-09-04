import json, collections, sys
tasks = ["task_000010_644ab1c2","task_000578_cebe85a5","task_001321_658ce4a8","task_001818_b251e5ea","task_001701_95e3bbcb"]
base="/fsx/home/jixuan.chen/harnessx-backup/.benchmarks/tmax/tmax-coev-rep19-i1-r0-traj/"
for t in tasks:
    d=json.load(open(base+t+".messages.json"))
    cmds=[]
    bg=0; compaction=0; cutoff=0
    for m in d:
        for tc in (m.get('tool_calls') or []):
            try:
                a=json.loads(tc['function']['arguments'])
                cmd=a.get('command','')
                cmds.append(cmd)
                if '&' in cmd and ('nohup' in cmd or 'python' in cmd or '.sh' in cmd): bg+=1
            except: pass
        c=m.get('content') or ''
        if isinstance(c,str):
            if 'PostCompaction' in c: compaction+=1
            if 'cut off by the token limit' in c: cutoff+=1
    dup=collections.Counter(cmds)
    top=dup.most_common(3)
    print(f"\n=== {t} : {len(cmds)} tool calls, {len(set(cmds))} unique; bg-launch={bg} compaction={compaction} tokencutoff={cutoff}")
    for cmd,n in top:
        if n>1: print(f"   x{n}: {cmd[:90]}")

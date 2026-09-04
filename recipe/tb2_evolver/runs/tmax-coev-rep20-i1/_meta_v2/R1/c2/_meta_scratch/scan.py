import json, glob, os
base='/fsx/home/jixuan.chen/harnessx-backup/.benchmarks/tmax/tmax-coev-rep20-i1-r0-traj'
for f in sorted(glob.glob(base+'/*.messages.json')):
    tid=os.path.basename(f).replace('.messages.json','')
    d=json.load(open(f))
    # count max run of identical consecutive Bash commands
    cmds=[]
    for m in d:
        if m.get('role')=='assistant' and m.get('tool_calls'):
            for t in m['tool_calls']:
                fn=t.get('function',{})
                cmds.append(fn.get('name','')+'::'+str(fn.get('arguments','')))
    maxrun=1; cur=1
    for i in range(1,len(cmds)):
        if cmds[i]==cmds[i-1]:
            cur+=1; maxrun=max(maxrun,cur)
        else:
            cur=1
    # also total dup fraction
    rj=f.replace('.messages.json','.result.json')
    reward='?'
    try:
        reward=json.load(open(rj)).get('reward')
    except: pass
    print("%s reward=%s calls=%d max_identical_run=%d"%(tid,reward,len(cmds),maxrun))

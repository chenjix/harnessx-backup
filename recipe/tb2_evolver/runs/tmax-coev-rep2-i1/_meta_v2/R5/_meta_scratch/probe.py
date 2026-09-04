import json, sys
base="/fsx/home/jixuan.chen/harnessx-backup/.benchmarks/tmax/tmax-coev-rep2-i1-r4-traj/"
for t in ["task_001032_1adaccb9","task_001321_658ce4a8","task_000958_4bb2b05d","task_000015_89886d8d","task_000010_644ab1c2","task_001090_c61c71f2"]:
    m=json.load(open(base+t+".messages.json"))
    cmds=[]
    for msg in m:
        for tc in (msg.get("tool_calls") or []):
            fn=tc.get("function",{})
            if fn.get("name")=="Bash":
                try: a=json.loads(fn.get("arguments","{}"))
                except: a={}
                cmds.append((a.get("command","") or "")[:80])
    # count consecutive identical
    maxrun=1; run=1
    for i in range(1,len(cmds)):
        if cmds[i]==cmds[i-1]: run+=1; maxrun=max(maxrun,run)
        else: run=1
    uniq=len(set(cmds))
    print("===",t,"bash_calls=",len(cmds),"unique=",uniq,"max_consec_identical=",maxrun)
    for c in cmds[-5:]: print("   >",c)

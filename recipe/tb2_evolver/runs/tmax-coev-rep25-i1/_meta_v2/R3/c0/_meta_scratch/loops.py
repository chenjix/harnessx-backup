import json,glob
d="/fsx/home/jixuan.chen/harnessx-backup/.benchmarks/tmax/tmax-coev-rep25-i1-r0-traj"
tasks=["task_001818_b251e5ea","task_001031_a8f0eb37","task_001321_658ce4a8","task_001673_86224c91","task_000329_a3ac56b0","task_001701_95e3bbcb","task_000015_89886d8d","task_000028_7fe033ac"]
import re
def norm(s): return re.sub(r"\s+"," ",s.strip())
for t in tasks:
    msgs=json.load(open(f"{d}/{t}.messages.json"))
    # collect assistant contents and bash commands
    asst=[]; cmds=[]; toolouts=[]
    for m in msgs:
        if m.get("role")=="assistant":
            c=m.get("content")
            if isinstance(c,list): c=" ".join(str(x.get("text","")) if isinstance(x,dict) else str(x) for x in c)
            asst.append(norm(str(c))[:120])
            for tc in (m.get("tool_calls") or []):
                cmds.append(norm(str(tc.get("function",{}).get("arguments","")))[:120])
        elif m.get("role")=="tool":
            c=m.get("content")
            if isinstance(c,list): c=" ".join(str(x.get("text","")) if isinstance(x,dict) else str(x) for x in c)
            toolouts.append(norm(str(c)))
    # longest identical consecutive run for asst and cmds
    def maxrun(lst):
        best=1;cur=1
        for i in range(1,len(lst)):
            if lst[i]==lst[i-1] and lst[i]: cur+=1; best=max(best,cur)
            else: cur=1
        return best
    # count error/nonzero markers
    nzero=sum(1 for o in toolouts if "exit 1" in o or "Traceback" in o or "error" in o.lower())
    print(t, "msgs",len(msgs),"asst_maxrun",maxrun(asst),"cmd_maxrun",maxrun(cmds),"toolouts_with_err",nzero,"/",len(toolouts))

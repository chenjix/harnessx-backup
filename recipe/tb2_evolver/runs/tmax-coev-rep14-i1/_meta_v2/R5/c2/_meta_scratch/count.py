import json
m=json.load(open('/fsx/home/jixuan.chen/harnessx-backup/.benchmarks/tmax/tmax-coev-rep14-i1-r4-traj/task_000028_7fe033ac.messages.json'))
prev=None; run=1; maxrun=1; seq=[]
for msg in m:
    if msg.get('role')=='assistant' and msg.get('tool_calls'):
        for t in msg['tool_calls']:
            args=t.get('function',{}).get('arguments','')
            seq.append(args)
# compute consecutive identical runs
for a in seq:
    if a==prev:
        run+=1
    else:
        run=1
    maxrun=max(maxrun,run)
    prev=a
print('total tool calls:', len(seq))
print('max consecutive identical run:', maxrun)
# show the dominant command
from collections import Counter
c=Counter(seq)
top=c.most_common(3)
for cmd,n in top:
    print('n=%d  %s' % (n, cmd[:120]))

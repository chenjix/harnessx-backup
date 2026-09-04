import json, sys
base='/fsx/home/jixuan.chen/harnessx-backup/.benchmarks/tmax/tmax-ev9b50-20260814-203834-r2-traj/'
t=sys.argv[1]
d=json.load(open(base+t+'.messages.json'))
for i,m in enumerate(d):
    r=m.get('role')
    fr=m.get('finish_reason') or m.get('stop_reason')
    tc=m.get('tool_calls')
    ntc=len(tc) if tc else 0
    keys=[k for k in m.keys() if k not in ('role','content','tool_calls')]
    print(i, r, 'ntc=',ntc, 'finish=',fr, 'keys=',keys)

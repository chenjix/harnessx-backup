import json, sys
base='/fsx/home/jixuan.chen/harnessx-backup/.benchmarks/tmax/tmax-ev9b50-20260814-203834-r2-traj/'
t=sys.argv[1]
d=json.load(open(base+t+'.messages.json'))
cmds=[]
for i,m in enumerate(d):
    if m.get('role')=='assistant' and m.get('tool_calls'):
        for call in m['tool_calls']:
            args=call['function']['arguments']
            try:
                a=json.loads(args); cmd=a.get('command','')
            except Exception:
                cmd='PARSEFAIL:'+args[:80]
            cmds.append((i,len(cmd),cmd))
for i,l,c in cmds:
    print(i,'len=',l,repr(c[:70]))
# group identical full commands
from collections import Counter
full=Counter(c for _,_,c in cmds)
print('--- repeated full commands (count>1) ---')
for c,n in full.items():
    if n>1:
        print('  count=%d len=%d: %r'%(n,len(c),c[:80]))

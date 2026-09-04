import json, sys
base='/fsx/home/jixuan.chen/harnessx-backup/.benchmarks/tmax/tmax-ev9b50-20260814-203834-r2-traj/'
t=sys.argv[1]
d=json.load(open(base+t+'.messages.json'))
def txt(m):
    c=m.get('content','')
    if isinstance(c,list): c=' '.join(str(x) for x in c)
    return ' '.join(str(c).split())
print('=== TASK (first user) ===')
print(txt(d[0])[:700])
print('=== #msgs=%d ==='%len(d))
# count tool calls
ntc=sum(1 for m in d if m.get('role')=='assistant' and m.get('tool_calls'))
print('assistant tool-call turns:', ntc)
print('=== LAST 6 assistant turns ===')
asst=[m for m in d if m.get('role')=='assistant']
for m in asst[-6:]:
    tc=m.get('tool_calls')
    if tc:
        for call in tc:
            try: a=json.loads(call['function']['arguments']); cmd=a.get('command','')
            except: cmd=call['function']['arguments'][:100]
            print('CMD:', ' '.join(cmd.split())[:160])
    else:
        print('TEXT:', txt(m)[:200])

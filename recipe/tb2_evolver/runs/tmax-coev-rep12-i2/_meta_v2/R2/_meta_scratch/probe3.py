import json
base='/fsx/home/jixuan.chen/harnessx-backup/.benchmarks/tmax/tmax-coev-rep12-i2-r1-traj/'
t='task_000998_4d9c7852'
msgs=json.load(open(base+f'{t}.messages.json'))
print('TASK PROMPT:')
c=msgs[0].get('content','')
if isinstance(c,list): c=' '.join(str(x.get('text','')) for x in c if isinstance(x,dict))
print(str(c)[:1400])
print('\n===== all bash commands issued =====')
for m in msgs:
    if m.get('role')=='assistant':
        tc=m.get('tool_calls') or []
        for call in tc:
            fn=call.get('function',{})
            args=fn.get('arguments','')
            if isinstance(args,str):
                try: args=json.loads(args)
                except: args={}
            cmd=args.get('command','') if isinstance(args,dict) else ''
            print('$', str(cmd)[:160].replace('\n',' '))

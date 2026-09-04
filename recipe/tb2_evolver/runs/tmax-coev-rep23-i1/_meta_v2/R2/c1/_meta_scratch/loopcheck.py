import json
m=json.load(open('/fsx/home/jixuan.chen/harnessx-backup/.benchmarks/tmax/tmax-coev-rep23-i1-r0-fe-c4-traj/task_000015_89886d8d.messages.json'))
# Count identical consecutive (command,result) and look for breaker nudges
prev=None; run=1; runs=[]
cmds=[]
for i,e in enumerate(m):
    if e.get('role')=='assistant':
        for tc in (e.get('tool_calls') or []):
            args=tc.get('function',{}).get('arguments','')
            try: cmd=json.loads(args).get('command','')
            except: cmd=str(args)
            cmds.append((i,cmd))
# consecutive identical commands
for idx,(i,c) in enumerate(cmds):
    if idx>0 and c==cmds[idx-1][1]:
        run+=1
    else:
        if run>=3: runs.append((cmds[idx-1][0],run,cmds[idx-1][1][:60]))
        run=1
if run>=3: runs.append((cmds[-1][0],run,cmds[-1][1][:60]))
print('consecutive-identical-command runs >=3:')
for r in runs: print(r)
print()
# search for breaker nudge text in user/tool messages
NUDGE=['stuck in a loop','identical','RepeatCommand','same command','loop']
for i,e in enumerate(m):
    if e.get('role') in ('user','tool'):
        c=str(e.get('content',''))
        if 'you are' in c.lower() and 'loop' in c.lower():
            print('NUDGE at',i,e.get('role'),':',c[:200])

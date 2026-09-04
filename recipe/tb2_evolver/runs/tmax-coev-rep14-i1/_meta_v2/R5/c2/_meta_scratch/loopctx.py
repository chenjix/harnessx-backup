import json
for tid in ['task_000396_e56917e2','task_001706_24462a09']:
    m=json.load(open('/fsx/home/jixuan.chen/harnessx-backup/.benchmarks/tmax/tmax-coev-rep14-i1-r4-traj/%s.messages.json'%tid))
    print('==== %s (n=%d) ===='%(tid,len(m)))
    # find where the identical run starts
    prev=None; runstart=0
    calls=[]
    for i,msg in enumerate(m):
        if msg.get('role')=='assistant' and msg.get('tool_calls'):
            a=msg['tool_calls'][0].get('function',{}).get('arguments','')
            calls.append((i,a))
    # find longest run
    best=(0,0,0); run=1; s=0
    for k in range(1,len(calls)):
        if calls[k][1]==calls[k-1][1]:
            run+=1
        else:
            if run>best[0]: best=(run,calls[s][0],calls[k-1][0])
            run=1; s=k
    if run>best[0]: best=(run,calls[s][0],calls[-1][0])
    print('longest run len=%d from msg %d to %d'%best)
    # print the repeated command
    startmsg=best[1]
    for i,msg in enumerate(m):
        if i==startmsg:
            a=msg['tool_calls'][0].get('function',{}).get('arguments','')
            print('repeated cmd:', a[:200])
    # print the msg right before run start
    if startmsg>=2:
        pm=m[startmsg-2]
        c=pm.get('content')
        if isinstance(c,list): c=str(c)
        print('context before loop (msg %d [%s]):'%(startmsg-2,pm.get('role')), str(c)[:250].replace(chr(10),' '))
    print()

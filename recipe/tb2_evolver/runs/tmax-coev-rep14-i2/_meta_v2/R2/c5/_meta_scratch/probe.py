import json, sys
tid=sys.argv[1]
base='/fsx/home/jixuan.chen/harnessx-backup/.benchmarks/tmax/tmax-coev-rep14-i2-r1-traj/'
d=json.load(open(base+tid+'/messages.json'))
# task desc
for m in d:
    if m.get('role')=='user':
        c=m.get('content')
        if isinstance(c,list):
            c=' '.join(x.get('text','') for x in c if isinstance(x,dict) and x.get('type')=='text')
        print('=== DESC ===')
        print(str(c)[:1200])
        break
# count background launches and pkill/leftover patterns
bg=0; pkill=0
for m in d:
    tc=m.get('tool_calls')
    if tc:
        for t in tc:
            cmd=str(t.get('function',{}).get('arguments',''))
            if ' &\\n' in cmd or cmd.rstrip().endswith('&"}') or 'nohup' in cmd:
                bg+=1
            if 'pkill' in cmd or 'kill ' in cmd:
                pkill+=1
print('=== bg_launches~', bg, 'kill_cmds~', pkill, 'total_msgs', len(d))

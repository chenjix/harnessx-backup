import json, glob, os
base='/fsx/home/jixuan.chen/harnessx-backup/.benchmarks/tmax/tmax-coev-rep14-i1-r4-traj'
rows=[]
for mf in sorted(glob.glob(base+'/*.messages.json')):
    tid=os.path.basename(mf).replace('.messages.json','')
    rf=mf.replace('.messages.json','.result.json')
    try:
        res=json.load(open(rf))
    except Exception:
        continue
    reward=res.get('reward'); exitr=res.get('agent',{}).get('exit_reason')
    try:
        m=json.load(open(mf))
    except Exception:
        continue
    seq=[]
    for msg in m:
        if msg.get('role')=='assistant' and msg.get('tool_calls'):
            for t in msg['tool_calls']:
                seq.append(t.get('function',{}).get('arguments',''))
    prev=None;run=1;maxrun=1
    for a in seq:
        run=run+1 if a==prev else 1
        maxrun=max(maxrun,run); prev=a
    if maxrun>=5:
        rows.append((tid,reward,exitr,maxrun,len(seq)))
rows.sort(key=lambda r:-r[3])
print('tasks with >=5 consecutive identical tool calls:')
for r in rows:
    print('  reward=%s exit=%-16s maxrun=%2d ncalls=%3d  %s' % (r[1],r[2],r[3],r[4],r[0]))
print('total flagged:', len(rows))

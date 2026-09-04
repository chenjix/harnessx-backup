import json, glob, os
base='/fsx/home/jixuan.chen/harnessx-backup/.benchmarks/tmax/tmax-coev-rep14-i1-r4-traj'
passing=[]
for mf in sorted(glob.glob(base+'/*.messages.json')):
    tid=os.path.basename(mf).replace('.messages.json','')
    rf=mf.replace('.messages.json','.result.json')
    try:
        res=json.load(open(rf))
    except Exception:
        continue
    if res.get('reward')!=1:
        continue
    m=json.load(open(mf))
    seq=[]
    for msg in m:
        if msg.get('role')=='assistant' and msg.get('tool_calls'):
            for t in msg['tool_calls']:
                seq.append(t.get('function',{}).get('arguments',''))
    prev=None;run=1;maxrun=1
    for a in seq:
        run=run+1 if a==prev else 1
        maxrun=max(maxrun,run); prev=a
    passing.append((tid,maxrun,len(seq)))
passing.sort(key=lambda r:-r[1])
print('PASSING tasks, sorted by max consecutive identical run:')
for r in passing[:15]:
    print('  maxrun=%2d ncalls=%3d  %s' % (r[1],r[2],r[0]))
print('num passing:', len(passing))
print('passing tasks with maxrun>=5:', sum(1 for r in passing if r[1]>=5))
print('passing tasks with maxrun>=4:', sum(1 for r in passing if r[1]>=4))
print('passing tasks with maxrun>=3:', sum(1 for r in passing if r[1]>=3))

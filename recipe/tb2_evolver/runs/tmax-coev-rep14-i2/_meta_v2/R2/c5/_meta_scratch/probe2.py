import json, sys
tid=sys.argv[1]
base='/fsx/home/jixuan.chen/harnessx-backup/.benchmarks/tmax/tmax-coev-rep14-i2-r1-traj/'
d=json.load(open(base+tid+'/messages.json'))
# Find tool results mentioning multiple leftover processes / defunct / ps aux python
for i,m in enumerate(d):
    if m.get('role')=='tool':
        c=m.get('content')
        if isinstance(c,list):
            c=' '.join(x.get('text','') for x in c if isinstance(x,dict))
        c=str(c)
        low=c.lower()
        if ('defunct' in low) or (low.count('python3')>=2) or ('already in use' in low) or ('address already' in low):
            print(f'--- tool result [{i}] ---')
            print(c[:500])

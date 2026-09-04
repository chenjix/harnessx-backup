import json, os
base='/fsx/home/jixuan.chen/harnessx-backup/.benchmarks/tmax/tmax-coev-rep12-i2-r1-traj/'
for t in ['task_000716_206dc0f6','task_001028_5bc8bc70','task_000910_16cc0daf','task_001877_bd2513aa']:
    msgs=json.load(open(base+f'{t}.messages.json'))
    asst=[m for m in msgs if m.get('role')=='assistant']
    print(f'=== {t}: {len(msgs)} msgs, {len(asst)} asst turns ===')
    for m in asst[-3:]:
        c=m.get('content','')
        if isinstance(c,list):
            c=' '.join(str(x.get('text','')) for x in c if isinstance(x,dict))
        print('  >', str(c)[:220].replace('\n',' '))

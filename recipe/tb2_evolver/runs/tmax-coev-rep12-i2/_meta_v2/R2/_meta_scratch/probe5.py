import json, hashlib
base='/fsx/home/jixuan.chen/harnessx-backup/.benchmarks/tmax/tmax-coev-rep12-i2-r1-traj/'
def norm(m):
    c=m.get('content','')
    if isinstance(c,list): c=' '.join(str(x.get('text','')) for x in c if isinstance(x,dict))
    return str(c).strip()
for t in ['task_001028_5bc8bc70','task_000716_206dc0f6','task_001877_bd2513aa','task_000910_16cc0daf']:
    msgs=json.load(open(base+f'{t}.messages.json'))
    asst=[norm(m) for m in msgs if m.get('role')=='assistant']
    # find max run of identical consecutive
    maxrun=1; cur=1; runtext=''
    for i in range(1,len(asst)):
        if asst[i]==asst[i-1] and asst[i]:
            cur+=1
            if cur>maxrun: maxrun=cur; runtext=asst[i]
        else: cur=1
    # also count near-identical by first 80 chars
    print(f'=== {t}: {len(asst)} asst turns, max identical consecutive run = {maxrun} ===')
    if maxrun>=2: print('   REPEATED TEXT:', runtext[:150].replace(chr(10),' '))
    # prefix-based repetition
    from collections import Counter
    prefixes=Counter(a[:60] for a in asst if a)
    top=prefixes.most_common(3)
    print('   top repeated 60-char prefixes:', [(n,p[:50]) for p,n in top])

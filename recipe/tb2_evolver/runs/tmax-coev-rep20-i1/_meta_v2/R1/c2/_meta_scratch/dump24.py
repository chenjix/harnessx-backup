import json
d=json.load(open('/fsx/home/jixuan.chen/harnessx-backup/.benchmarks/tmax/tmax-coev-rep20-i1-r0-traj/task_000024_a0664029.messages.json'))
for i,m in enumerate(d):
    r=m.get('role')
    c=m.get('content')
    tc=m.get('tool_calls')
    txt=''
    if isinstance(c,str): txt=c
    elif isinstance(c,list): txt=' '.join(str(x.get('text','')) if isinstance(x,dict) else str(x) for x in c)
    line="[%d] %s"%(i,r)
    if tc:
        for t in tc:
            fn=t.get('function',{})
            line+=" TOOL:"+fn.get('name','')+" "+str(fn.get('arguments',''))[:260]
    else:
        line+=" "+txt[:260].replace(chr(10),' ')
    print(line)

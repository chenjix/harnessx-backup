import json, sys
p=sys.argv[1]
msgs=json.load(open(p))
print('num msgs', len(msgs))
for i,m in enumerate(msgs):
    role=m.get('role')
    c=m.get('content')
    if isinstance(c,list):
        c=' '.join(str(x.get('text',x)) if isinstance(x,dict) else str(x) for x in c)
    c=str(c)
    tc=m.get('tool_calls')
    tcs=''
    if tc:
        for t in tc:
            fn=t.get('function',{})
            tcs+='\n  TOOLCALL '+str(fn.get('name'))+' '+str(fn.get('arguments'))[:600]
    print('==== ['+str(i)+'] '+str(role)+' (len '+str(len(c))+')')
    print(c[:1000])
    if tcs: print(tcs)

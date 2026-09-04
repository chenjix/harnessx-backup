import json,sys
p=sys.argv[1]
msgs=json.load(open(p))
for i,m in enumerate(msgs):
    role=m.get('role')
    c=m.get('content')
    if isinstance(c,list):
        parts=[]
        for x in c:
            if isinstance(x,dict):
                parts.append(str(x.get('text',x.get('content',''))))
            else:
                parts.append(str(x))
        c=' '.join(parts)
    c=str(c)
    tc=m.get('tool_calls')
    extra=''
    if tc:
        for t in tc:
            fn=t.get('function',{})
            extra+=' CALL:'+str(fn.get('arguments',''))[:400]
    print(i,role,repr(c[:300])+extra[:400])

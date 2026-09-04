import json, sys
for p in sys.argv[1:]:
    try:
        msgs=json.load(open(p))
    except Exception as e:
        print(p, 'ERR', e); continue
    # last assistant text
    last_txt=''
    for m in msgs:
        if m.get('role')=='assistant':
            c=m.get('content')
            if isinstance(c,list):
                c=' '.join(str(x.get('text','')) for x in c if isinstance(x,dict))
            if c: last_txt=str(c)
    print('#####', p.split('/')[-2])
    print('LAST ASSISTANT (tail):', last_txt[-700:].replace(chr(10),' '))
    print()

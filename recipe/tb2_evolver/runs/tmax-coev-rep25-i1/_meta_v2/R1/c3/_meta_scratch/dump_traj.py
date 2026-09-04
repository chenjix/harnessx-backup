import json, sys
path = sys.argv[1]
msgs = json.load(open(path))
print('n messages', len(msgs))
for i, m in enumerate(msgs):
    role = m.get('role')
    c = m.get('content')
    if isinstance(c, list):
        c = ' '.join(str(x.get('text', x)) for x in c if isinstance(x, dict))
    c = str(c)
    tc = m.get('tool_calls')
    print(f'--- [{i}] {role} ---')
    print(c[:600])
    if tc:
        for t in tc:
            fn = t.get('function', {})
            print('  TOOLCALL:', str(fn.get('arguments'))[:500])

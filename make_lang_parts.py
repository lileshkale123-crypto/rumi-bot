import ast, glob, json, os
files = sorted(glob.glob("ax_*.py")) + ["main.py"]
seen = set()
order = []
SQL = ("SELECT", "INSERT", "UPDATE", "CREATE", "DELETE", "ALTER", "DROP")
for f in files:
    try:
        t = ast.parse(open(f, encoding="utf-8").read())
    except Exception:
        continue
    skip = set()
    for n in ast.walk(t):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant):
                skip.add(id(n.body[0].value))
        if isinstance(n, ast.JoinedStr):
            for v in n.values:
                skip.add(id(v))
    for n in ast.walk(t):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in skip:
            s = n.value
            if len(s) < 25 or " " not in s:
                continue
            if s.lstrip().upper().startswith(SQL):
                continue
            if s not in seen:
                seen.add(s)
                order.append(s)
os.makedirs("lang_parts", exist_ok=True)
k = 5
size = -(-len(order) // k)
for i in range(k):
    chunk = order[i * size:(i + 1) * size]
    d = {s: "" for s in chunk}
    with open("lang_parts/part%d.json" % (i + 1), "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False, indent=0)
    print("part", i + 1, len(chunk))
print("UNIQUE", len(order))

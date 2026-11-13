import ast, glob
tot = 0
for f in sorted(glob.glob("ax_*.py")) + ["main.py"]:
    try:
        t = ast.parse(open(f, encoding="utf-8").read())
    except Exception:
        continue
    n = sum(1 for x in ast.walk(t) if isinstance(x, ast.Constant) and isinstance(x.value, str) and len(x.value) >= 25 and " " in x.value)
    tot += n
    if n >= 15:
        print(f, n)
print("TOTAL", tot)

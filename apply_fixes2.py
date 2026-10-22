p = "quote_fb.py"
s = open(p, encoding="utf-8").read()
old = '''            if ch in ZW:
                continue
            g = pick(f, ch)
'''
new = '''            if ch in ZW:
                continue
            if out and unicodedata.category(ch) in ("Mn", "Mc", "Me"):
                out[-1][0] += ch  # marks (matras, virama) stay with their letter
                continue
            g = pick(f, ch)
'''
if s.count(old) != 1 or "import unicodedata" in s:
    print("SKIP: already patched or pattern not found")
else:
    s = s.replace(old, new).replace("import os\n", "import os\nimport unicodedata\n", 1)
    open(p, "w", encoding="utf-8").write(s)
    print("patched:", p)

import json, re, sys
n = sys.argv[1]
orig = json.load(open("lang_parts/part%s.json" % n, encoding="utf-8"))
raw = open("lang_parts/hi%s.raw" % n, "rb").read().decode("utf-8", errors="replace")
out = {k: "" for k in orig}
for l in raw.split("\n"):
    l = l.strip().rstrip(",")
    if not l.startswith('"'):
        continue
    try:
        d = json.loads("{" + l + "}", strict=False)
    except Exception:
        continue
    for k, v in d.items():
        if k in out and isinstance(v, str) and "\ufffd" not in v:
            out[k] = v
def sig(s):
    return (s.count("%s"), s.count("%d"), s.count("%%"), s.count("\n"),
            sorted(re.findall(r"</?[a-z]+", s)), sorted(re.findall(r"\{[^}]*\}", s)))
bad = 0
for k in list(out):
    if out[k] and not sig(k) == sig(out[k]):
        out[k] = ""
        bad += 1
json.dump(out, open("lang_parts/hi%s.json" % n, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
miss = {k: "" for k, v in out.items() if not v}
json.dump(miss, open("lang_parts/missing%s.json" % n, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
print("PART", n, "FILLED", len(out) - len(miss), "EMPTY", len(miss), "DROPPED", bad)

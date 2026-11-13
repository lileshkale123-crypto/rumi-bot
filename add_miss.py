import json, re
miss = json.load(open("lang_parts/missing_all.json", encoding="utf-8"))
hi = json.load(open("lang_hi.json", encoding="utf-8"))
raw = open("lang_parts/himiss.raw", "rb").read().decode("utf-8", errors="replace")
def sig(s):
    return (s.count("%s"), s.count("%d"), s.count("%%"), s.count("\n"),
            sorted(re.findall(r"</?[a-z]+", s)), sorted(re.findall(r"\{[^}]*\}", s)))
added, bad = 0, 0
for l in raw.split("\n"):
    l = l.strip().rstrip(",")
    if not l.startswith('"'):
        continue
    try:
        d = json.loads("{" + l + "}", strict=False)
    except Exception:
        bad += 1
        continue
    for k, v in d.items():
        if k in miss and isinstance(v, str) and v and "\ufffd" not in v and sig(k) == sig(v):
            hi[k] = v
            added += 1
        else:
            bad += 1
json.dump(hi, open("lang_hi.json", "w", encoding="utf-8"), ensure_ascii=False, indent=0)
print("ADDED", added, "SKIPPED", bad, "TOTAL", len(hi))

import json
allv, miss = {}, {}
for n in range(1, 6):
    d = json.load(open("lang_parts/hi%d.json" % n, encoding="utf-8"))
    for k, v in d.items():
        if v:
            allv[k] = v
        else:
            miss[k] = ""
json.dump(allv, open("lang_hi.json", "w", encoding="utf-8"), ensure_ascii=False, indent=0)
json.dump(miss, open("lang_parts/missing_all.json", "w", encoding="utf-8"), ensure_ascii=False, indent=0)
print("FILLED", len(allv), "MISSING", len(miss))

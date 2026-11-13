raw = open("lang_parts/hi1.json", "rb").read()
txt = raw.decode("utf-8", errors="replace")
lines = txt.split("\n")
print("LINES", len(lines), "BYTES", len(raw))
bad = [i + 1 for i, l in enumerate(lines) if "\ufffd" in l]
print("BAD LINES", bad)
for i in bad[:5]:
    print(i, lines[i - 1][:160])

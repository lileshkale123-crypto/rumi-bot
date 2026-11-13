import os, zipfile
SKIP = {"venv", ".git", "__pycache__", "emoji_cache_g", "node_modules", "assets"}
OK_EXT = (".py", ".sql", ".txt", ".md", ".json", ".toml", ".yml", ".yaml")
files = []
for root, dirs, fs in os.walk("."):
    dirs[:] = [d for d in dirs if d not in SKIP]
    for f in fs:
        if not f.endswith(OK_EXT) or ".bak" in f or f.startswith("patch_") or f.startswith("make_"):
            continue
        p = os.path.join(root, f)
        files.append((os.path.getsize(p), p))
files.sort(reverse=True)
parts = [[] for _ in range(5)]
sizes = [0] * 5
for sz, p in files:
    i = sizes.index(min(sizes))
    parts[i].append(p)
    sizes[i] += sz
for i, ps in enumerate(parts, 1):
    out = os.path.expanduser("~/rumi_part%d.zip" % i)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in ps:
            z.write(p)
    print("part", i, "files:", len(ps), "KB:", round(os.path.getsize(out) / 1000))

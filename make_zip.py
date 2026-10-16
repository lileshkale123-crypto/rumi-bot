import os, zipfile, fnmatch, datetime

SKIP_DIRS = {"venv", ".git", "__pycache__", "emoji_cache"}
SKIP_PATTERNS = [".env", ".env.*", "*.bak*", "patch_*.py", "test_q*.png", "*.pyc", "make_zip.py"]

stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M")
out = os.path.expanduser(f"~/rumi-bot_{stamp}.zip")
count = 0

with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for root, dirs, files in os.walk("."):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if any(fnmatch.fnmatch(f, p) for p in SKIP_PATTERNS):
                continue
            path = os.path.join(root, f)
            z.write(path, os.path.join("rumi-bot", path[2:]))
            count += 1

print("Files zipped:", count)
print("Saved to:", out)
print("Size KB:", os.path.getsize(out) // 1024)

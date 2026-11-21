#!/usr/bin/env python3
"""Downloads the assets /q needs: the full Apple emoji set and extra fonts (no boxes for rare text).

Run once from the project folder (works in Termux, standard library only):

    python3 fetch_quote_assets.py            # emojis + fonts
    python3 fetch_quote_assets.py emoji      # only emojis
    python3 fetch_quote_assets.py fonts      # only fonts

Then commit emoji_cache_apple/ and assets/fonts/ so the server has them. Safe to run again:
files that already exist are skipped, and a failed download is only reported, never fatal.
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

EMOJI_DIR = "emoji_cache_apple"
FONT_DIR = "assets/fonts"
CDN_VERSION = "16.0.0"
CDN_BASE = "https://cdn.jsdelivr.net/npm/emoji-datasource-apple@" + CDN_VERSION + "/img/apple/64/"
LIST_URLS = [
    "https://data.jsdelivr.com/v1/packages/npm/emoji-datasource-apple@" + CDN_VERSION + "?structure=flat",
    "https://data.jsdelivr.com/v1/package/npm/emoji-datasource-apple@" + CDN_VERSION + "/flat",
]
EMOJI_TEST_URLS = [
    "https://unicode.org/Public/emoji/latest/emoji-test.txt",
    "https://unicode.org/Public/emoji/16.0/emoji-test.txt",
]

NOTO_FAMILIES = [
    "NotoSans", "NotoSansArabic", "NotoSansHebrew", "NotoSansBengali", "NotoSansGurmukhi",
    "NotoSansGujarati", "NotoSansTamil", "NotoSansTelugu", "NotoSansKannada", "NotoSansMalayalam",
    "NotoSansSinhala", "NotoSansThai", "NotoSansLao", "NotoSansKhmer", "NotoSansMyanmar",
    "NotoSansGeorgian", "NotoSansArmenian", "NotoSansEthiopic", "NotoSansCherokee",
    "NotoSansCanadianAboriginal", "NotoSansTaiViet", "NotoSansTaiLe", "NotoSansTaiTham",
    "NotoSansJavanese", "NotoSansBalinese", "NotoSansRunic", "NotoSansOgham", "NotoSansThaana",
    "NotoSansSyriac", "NotoSansMongolian", "NotoSansTifinagh", "NotoSansNKo",
]
NOTO_URLS = [
    "https://raw.githubusercontent.com/notofonts/notofonts.github.io/main/fonts/{F}/hinted/ttf/{F}-Regular.ttf",
    "https://raw.githubusercontent.com/notofonts/notofonts.github.io/main/fonts/{F}/unhinted/ttf/{F}-Regular.ttf",
    "https://raw.githubusercontent.com/googlefonts/noto-fonts/main/hinted/ttf/{F}/{F}-Regular.ttf",
    "https://raw.githubusercontent.com/googlefonts/noto-fonts/main/unhinted/ttf/{F}/{F}-Regular.ttf",
    "https://raw.githubusercontent.com/googlefonts/noto-fonts/main/unhinted/{F}-Regular.ttf",
]
UNIFONT_INDEXES = ["https://ftp.gnu.org/gnu/unifont/", "https://mirrors.kernel.org/gnu/unifont/"]

UA = {"User-Agent": "Mozilla/5.0 (quote-assets)"}
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
FONT_MAGIC = (b"\x00\x01\x00\x00", b"OTTO", b"true", b"ttcf")


def http_get(url, timeout=25, tries=3):
    """Returns (status, bytes). status 0 = network problem."""
    last = 0
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return 404, b""
            last = e.code
        except Exception:
            last = 0
        time.sleep(0.6 * (i + 1))
    return last, b""


def write_atomic(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".part"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)


# ---------------------------------------------------------------- emojis

def emoji_names_from_cdn_list():
    for url in LIST_URLS:
        st, body = http_get(url)
        if st != 200:
            continue
        try:
            files = json.loads(body.decode("utf-8")).get("files", [])
        except Exception:
            continue
        names = sorted(
            f["name"].rsplit("/", 1)[1][:-4]
            for f in files
            if f.get("name", "").startswith("/img/apple/64/") and f["name"].endswith(".png")
        )
        if names:
            return names
    return []


def emoji_names_from_unicode():
    for url in EMOJI_TEST_URLS:
        st, body = http_get(url, timeout=40)
        if st != 200:
            continue
        names = set()
        for line in body.decode("utf-8", "replace").splitlines():
            if not line or line.startswith("#") or ";" not in line:
                continue
            codes, rest = line.split(";", 1)
            status = rest.strip().split("#", 1)[0].strip()
            if status in ("fully-qualified", "component"):
                names.add("-".join(c.lower() for c in codes.split()))
        if names:
            return sorted(names)
    return []


def fetch_emojis():
    print("== Apple emojis ==")
    os.makedirs(EMOJI_DIR, exist_ok=True)
    names = emoji_names_from_cdn_list()
    source = "jsdelivr file list"
    if not names:
        names = emoji_names_from_unicode()
        source = "unicode.org emoji list"
    if not names:
        print("Could not get the emoji list (no internet?). Nothing downloaded.")
        return False
    print("%d emojis listed (%s)" % (len(names), source))
    have = {f[:-4] for f in os.listdir(EMOJI_DIR) if f.endswith(".png")}
    todo = [n for n in names if n not in have]
    print("%d already here, %d to download" % (len(have), len(todo)))
    stats = {"ok": 0, "absent": 0, "failed": 0}

    def one(name):
        st, body = http_get(CDN_BASE + name + ".png")
        if st == 200 and body[:8] == PNG_MAGIC:
            write_atomic(os.path.join(EMOJI_DIR, name + ".png"), body)
            stats["ok"] += 1
        elif st == 404:
            stats["absent"] += 1
        else:
            stats["failed"] += 1

    done = 0
    with ThreadPoolExecutor(max_workers=12) as ex:
        for _ in ex.map(one, todo):
            done += 1
            if done % 300 == 0:
                print("  %d / %d" % (done, len(todo)))
    print("downloaded %d, not in the Apple set %d, failed %d" % (stats["ok"], stats["absent"], stats["failed"]))
    mark = os.path.join(EMOJI_DIR, ".complete")
    if stats["failed"] == 0:
        write_atomic(mark, b"ok\n")
        print("Emoji set is complete. The bot will not try to download emojis at runtime.")
        return True
    if os.path.exists(mark):
        os.unlink(mark)
    print("Some downloads failed: run this script again to finish.")
    return False


# ---------------------------------------------------------------- fonts

def looks_like_font(data):
    return len(data) > 2000 and data[:4] in FONT_MAGIC


def fetch_noto(family):
    fname = family + "-Regular.ttf"
    path = os.path.join(FONT_DIR, fname)
    if os.path.isfile(path):
        return "have"
    for tpl in NOTO_URLS:
        st, body = http_get(tpl.replace("{F}", family), timeout=40, tries=2)
        if st == 200 and looks_like_font(body):
            write_atomic(path, body)
            return "ok"
    return "missing"


def latest_unifont_versions():
    for idx in UNIFONT_INDEXES:
        st, body = http_get(idx)
        if st == 200:
            vers = set(re.findall(r"unifont-(\d+\.\d+\.\d+)/", body.decode("utf-8", "replace")))
            if vers:
                return idx, sorted(vers, key=lambda v: tuple(int(x) for x in v.split(".")), reverse=True)
    return None, []


def fetch_unifont():
    """Unifont draws almost every Unicode character: the last-resort font, so nothing is a box."""
    wanted = [("unifont.otf", "unifont-{v}.otf"), ("unifont_upper.otf", "unifont_upper-{v}.otf")]
    missing = [w for w in wanted if not os.path.isfile(os.path.join(FONT_DIR, w[0]))]
    if not missing:
        print("  Unifont: have")
        return
    idx, versions = latest_unifont_versions()
    if not versions:
        print("  Unifont: could not read the version list, skipped")
        return
    for local, tpl in missing:
        got = False
        for v in versions[:4]:
            st, body = http_get("%sunifont-%s/%s" % (idx, v, tpl.format(v=v)), timeout=90, tries=2)
            if st == 200 and looks_like_font(body):
                write_atomic(os.path.join(FONT_DIR, local), body)
                print("  Unifont: %s ok (version %s, %d KB)" % (local, v, len(body) // 1024))
                got = True
                break
        if not got:
            print("  Unifont: %s could not be downloaded" % local)


def fetch_fonts():
    print("== Fonts ==")
    os.makedirs(FONT_DIR, exist_ok=True)
    results = {"ok": [], "have": [], "missing": []}
    with ThreadPoolExecutor(max_workers=6) as ex:
        for fam, res in zip(NOTO_FAMILIES, ex.map(fetch_noto, NOTO_FAMILIES)):
            results[res].append(fam)
    print("  Noto: %d new, %d already here, %d not found" % (len(results["ok"]), len(results["have"]), len(results["missing"])))
    if results["missing"]:
        print("  not found (fine, Unifont covers most of them): " + ", ".join(results["missing"]))
    fetch_unifont()
    return True


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "all"
    if what in ("all", "emoji"):
        fetch_emojis()
    if what in ("all", "fonts"):
        fetch_fonts()
    print("Done. Commit emoji_cache_apple/ and assets/fonts/ so the server has them.")

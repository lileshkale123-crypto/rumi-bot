import ax_i18n as t
t._load_file()
print("EXACT", len(t.EXACT), "PATS", len(t.PATS))
a = "✅ <b>Language saved</b>\n<blockquote>%s, Rumi will use English with you ✨</blockquote>" % "Aarav"
b = t.tr(a)
print("CHANGED", b != a, "HAS_NAME", "Aarav" in b)
print(b[:120])
c = "this text is not in the file at all, so it stays english"
print("UNKNOWN_SAME", t.tr(c) == c)

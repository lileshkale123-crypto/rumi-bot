def sub(path, old, new):
    s = open(path, encoding="utf-8").read()
    if s.count(old) != 1:
        print("SKIP (pattern not found exactly once):", path, "->", old[:50].replace("\n", " "))
        return
    open(path, "w", encoding="utf-8").write(s.replace(old, new))
    print("patched:", path)

sub("quote_fb.py",
    'FB = [\n',
    'FB = [\n    "assets/fonts/NotoSansDevanagari.ttf",\n')

sub("quote_pil.py",
'''    words = [w for w in clean_name.upper().split() if w]
    if not words:
        return "?"
    first = next((ch for ch in words[0] if not ch.isspace()), "?")
    if len(words) > 1:
        last = next((ch for ch in words[-1] if not ch.isspace()), "")
        return first + last
    return first''',
'''    words = [w for w in clean_name.upper().split() if any(c.isalnum() for c in w)]
    if not words:
        return "?"
    first = next(ch for ch in words[0] if ch.isalnum())
    if len(words) > 1:
        last = next(ch for ch in words[-1] if ch.isalnum())
        return first + last
    return first''')
sub("quote_pil.py",
    '    f = L.style_font(f_size, bold=True)\n    dr.text((size / 2.0, size / 2.0), letters,',
    '    f = qf.pick(L.style_font(f_size, bold=True), letters[0])\n    dr.text((size / 2.0, size / 2.0), letters,')

sub("ax_kang.py",
'''        import ax_quote as aq
        import quote_remote as qr
        tm = [r]
        roles = await aq._roles(context.bot, msg.chat_id, tm)
        img = await qr.remote(context.bot.token, tm, roles, color=None, reply=False)
        if img is None:
            img = await aq._card(context.bot, r)
''',
'''        import asyncio
        import ax_quote as aq
        import quote_pil as qp
        roles = await aq._roles(context.bot, msg.chat_id, [r])
        dicts = await aq._build_msg_dicts(context.bot, [r], roles, include_reply=False)
        img = await asyncio.to_thread(qp.render_messages, dicts, None)
''')

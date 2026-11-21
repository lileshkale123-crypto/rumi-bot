import html
def hub_menu(d=None): return "🏴 <b>CLAN HUB</b>\n<blockquote>1. /clan create Name\n2. /clan invite @user\n3. /clan kick @user\n4. /clan info &amp; /clan deposit\n5. /clan top</blockquote>"
def info(d): return hub_menu(d)
def top(r): return "🏴 <b>TOP CLANS</b>"
def e(x): return html.escape(str(x))

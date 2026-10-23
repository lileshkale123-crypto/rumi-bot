"""Compatibility wrapper: redirects any legacy render() calls to quote_pil.render_messages."""
import quote_pil as qp
def render(name, text, avatar, uid):
    return qp.render_messages([{"name": name, "user_id": uid, "text": text, "entities": [], "avatar": avatar, "tag": None, "reply": None}])

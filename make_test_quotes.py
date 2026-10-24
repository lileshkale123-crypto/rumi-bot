"""Generate Quotly visual test PNGs into test_quotes/ for comparison."""
import io
import os
from PIL import Image, ImageDraw
import quote_pil as qp

OUT_DIR = "test_quotes"


def _make_sample_avatar() -> bytes:
    im = Image.new("RGBA", (160, 160), (45, 85, 155, 255))
    dr = ImageDraw.Draw(im)
    dr.ellipse([30, 25, 130, 125], fill=(240, 190, 140, 255))
    dr.ellipse([55, 55, 70, 70], fill=(30, 30, 30, 255))
    dr.ellipse([90, 55, 105, 70], fill=(30, 30, 30, 255))
    dr.arc([55, 75, 105, 105], 0, 180, fill=(180, 60, 60, 255), width=4)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    sample_av = _make_sample_avatar()

    tests = {
        "01_single_message.png": (
            [
                {
                    "name": "Nilesh Kale",
                    "user_id": 1002,
                    "text": "Hey there! Testing the pure-Python Quotly renderer.",
                    "entities": [],
                    "avatar": sample_av,
                    "tag": None,
                    "reply": None,
                }
            ],
            None,
        ),
        "02_long_text_truncation.png": (
            [
                {
                    "name": "LORD 么 AIZEN 『D E V I L』ツ",
                    "user_id": 1005,
                    "text": "\n".join(
                        [f"Line {i}: The quick brown fox jumps over the lazy dog in a very long message." for i in range(1, 22)]
                    ),
                    "entities": [],
                    "avatar": None,
                    "tag": ("owner", "Founder"),
                    "reply": None,
                }
            ],
            None,
        ),
        "03_inline_emoji_and_fancy_name.png": (
            [
                {
                    "name": "✨ Rumi Bot 🌸",
                    "user_id": 1006,
                    "text": "Inline colour emojis 🚀🔥✨ align smoothly with the text baseline!",
                    "entities": [],
                    "avatar": None,
                    "tag": ("admin", "bot admin"),
                    "reply": None,
                }
            ],
            None,
        ),
        "04_reply_block.png": (
            [
                {
                    "name": "Alice Wonderland",
                    "user_id": 1003,
                    "text": "Totally agree! That makes deployment way simpler.",
                    "entities": [],
                    "avatar": sample_av,
                    "tag": ("admin", None),
                    "reply": {
                        "name": "Bob Builder",
                        "text": "We don't need a separate Node.js container on Railway anymore!",
                    },
                }
            ],
            None,
        ),
        "05_stacked_messages.png": (
            [
                {
                    "name": "Alex",
                    "user_id": 2001,
                    "text": "First message in a consecutive run (shows name, no avatar, flat bottom-left).",
                    "entities": [],
                    "avatar": None,
                    "tag": ("admin", "Mod"),
                    "reply": None,
                },
                {
                    "name": "Alex",
                    "user_id": 2001,
                    "text": "Second message by same user (shows avatar & tail, no name, flat top-left).",
                    "entities": [],
                    "avatar": None,
                    "tag": ("admin", "Mod"),
                    "reply": None,
                },
                {
                    "name": "Sarah",
                    "user_id": 2004,
                    "text": "And a third message from a different user right below!",
                    "entities": [],
                    "avatar": sample_av,
                    "tag": ("owner", None),
                    "reply": None,
                },
            ],
            None,
        ),
        "06_formatted_entities.png": (
            [
                {
                    "name": "Syntax Tester",
                    "user_id": 1004,
                    "text": "Bold Italic Underline Strike code @mention #tag Spoiler\nA quoted block inside text!",
                    "entities": [
                        {"type": "bold", "offset": 0, "length": 4},
                        {"type": "italic", "offset": 5, "length": 6},
                        {"type": "underline", "offset": 12, "length": 9},
                        {"type": "strikethrough", "offset": 22, "length": 6},
                        {"type": "code", "offset": 29, "length": 4},
                        {"type": "mention", "offset": 34, "length": 8},
                        {"type": "hashtag", "offset": 43, "length": 4},
                        {"type": "spoiler", "offset": 48, "length": 7},
                        {"type": "blockquote", "offset": 56, "length": 27},
                    ],
                    "avatar": None,
                    "tag": None,
                    "reply": None,
                }
            ],
            None,
        ),
        "07_custom_background_pink.png": (
            [
                {
                    "name": "Light Theme Test",
                    "user_id": 1000,
                    "text": "Custom light background (#ffc1dc) automatically switches to dark text and high-contrast accents!",
                    "entities": [{"type": "bold", "offset": 24, "length": 9}],
                    "avatar": sample_av,
                    "tag": ("owner", "Queen"),
                    "reply": {
                        "name": "Someone",
                        "text": "Try /q pink!",
                    },
                }
            ],
            "#ffc1dc",
        ),
    }

    for filename, (msgs, bg) in tests.items():
        img = qp.render_messages(msgs, bg=bg)
        out_path = os.path.join(OUT_DIR, filename)
        img.save(out_path, format="PNG")
        print(f"Wrote {out_path} ({img.width}x{img.height})")


if __name__ == "__main__":
    main()



import html as _html

import mafia_config as mc

# role -> (what you do, how you win)
ROLE_INFO = {
    "don": ("You lead the family. Each night your crew picks a victim, and if they split, your choice decides. The Journalist reads you as an ordinary citizen.",
            "Mafia side equals or outnumbers everyone else."),
    "mafia": ("You work for the family. Each night, vote with your crew on who disappears. Stay calm in daylight.",
              "Mafia side equals or outnumbers everyone else."),
    "hooker": ("Each night, pick someone to keep busy. Whoever you visit can't act until morning.",
               "Help the Mafia side take over the city."),
    "lawyer": ("Each night, cover one person with your paperwork. A covered Mafia member looks clean to the Journalist.",
               "Help the Mafia side take over the city."),
    "townie": ("No night powers, just your eyes and your vote. Find the criminals before they find you.",
               "Remove every Mafia member and the Maniac."),
    "doctor": ("Each night, choose someone to patch up. You may treat yourself, but never the same person two nights in a row.",
               "Remove every Mafia member and the Maniac."),
    "journalist": ("Each night, pick two people and learn whether they stand on the same side.",
                   "Remove every Mafia member and the Maniac."),
    "hobo": ("Each night, watch one person's door. If they get killed, you learn who did it.",
             "Remove every Mafia member and the Maniac."),
    "lucky": (f"Fortune follows you: a night attack fails on you {int(mc.LUCKY_SURVIVE_CHANCE * 100)}% of the time.",
              "Remove every Mafia member and the Maniac."),
    "kamikaze": ("If the town lynches you, you choose one person to take down with you.",
                 "Remove every Mafia member and the Maniac."),
    "maniac": ("You answer to no one. Strike every night. No side is yours.",
               "Be the last one standing, or one of the last two."),
    "suicide": ("You want the town to turn on you.",
                "Get lynched by the town. It wins you the game instantly."),
}


def role_card(role: str, group: str = "") -> str:
    emoji, title, _ = mc.ROLES[role]
    info, goal = ROLE_INFO[role]
    where = f"🏛️ <i>Game in: {_html.escape(group)}</i>\n\n" if group else ""
    return (
        f"{where}🎭 <b>You are the {emoji} {title}</b>\n"
        f"<blockquote>{info}</blockquote>\n"
        f"🎯 <i>Goal: {goal}</i>"
    )

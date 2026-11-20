# All tunable Mafia numbers live here. Mafia is NOT connected to the coin economy.

MIN_PLAYERS = 4
MAX_PLAYERS = 15

# Timers (seconds)
LOBBY_SECONDS = 120
DM_CHECK_SECONDS = 30
NIGHT_SECONDS = 60
DISCUSSION_SECONDS = 45
TRIBUNAL_SECONDS = 45
KAMIKAZE_PICK_SECONDS = 20

# Lucky survives a night attack with this chance
LUCKY_SURVIVE_CHANCE = 0.40

# Roles: key -> (emoji, display name, team)
# Teams: "mafia", "town", "maniac", "suicide"
ROLES = {
    "don":        ("🎩", "Don",        "mafia"),
    "mafia":      ("🕴️", "Mafia",      "mafia"),
    "hooker":     ("💋", "Hooker",     "mafia"),
    "lawyer":     ("⚖️", "Lawyer",     "mafia"),
    "townie":     ("🙂", "Townie",     "town"),
    "doctor":     ("💉", "Doctor",     "town"),
    "journalist": ("📰", "Journalist", "town"),
    "hobo":       ("🧥", "Hobo",       "town"),
    "lucky":      ("🍀", "Lucky",      "town"),
    "kamikaze":   ("💣", "Kamikaze",   "town"),
    "maniac":     ("🔪", "Maniac",     "maniac"),
    "suicide":    ("🤡", "Suicide",    "suicide"),
}

# Base roles for the smallest game (4 players)
BASE_ROLES = ["don", "doctor", "townie", "townie"]

# Each extra player adds exactly one role: player count -> role added
ROLE_ADDITIONS = {
    5: "townie",
    6: "mafia",
    7: "journalist",
    8: "maniac",
    9: "hobo",
    10: "hooker",
    11: "lucky",
    12: "lawyer",
    13: "suicide",
    14: "kamikaze",
    15: "mafia",
}

# Night resolution order
NIGHT_ORDER = ["hooker", "lawyer", "mafia_maniac", "doctor", "journalist_hobo"]

# Telegram file_ids for phase images. Fill these using the file_id helper.
MEDIA = {
    "night": "AgACAgUAAxkBAAIB8Wq7NoaquB3MjuKxuu1nH_MS4DeAAAIREGsbKd_ZVQWMJPDfFjXDAQADAgADeQADPQQ",
    "dawn": "AgACAgUAAxkBAAIB82q7NvmSwfc6BLTaR8fR90oiZDUFAAISEGsbKd_ZVdcI639woin6AQADAgADeQADPQQ",
    "lobby": "AgACAgUAAxkBAAIB9Gq7NvleistClM48j1FNjd92782wAAITEGsbKd_ZVaVXy-LLy9MzAQADAgADeQADPQQ",
    "victory": "AgACAgUAAxkBAAIB9Wq7NvlhVPELwLUzlGCtJnnM9Ze-AAIUEGsbKd_ZVZszmE-xN7K5AQADAgADeQADPQQ",
    "defeat": "AgACAgUAAxkBAAIB9mq7Nvkom8IV32kjKOffyC3V7vi1AAIVEGsbKd_ZVWF2FwvYQS6pAQADAgADeQADPQQ",
    "cards": "AgACAgUAAxkBAAIB92q7NvlIoY_DjotuccmTF22vE-9DAAIWEGsbKd_ZVR2Xxg41znw3AQADAgADeQADPQQ",
    "boss": "AgACAgUAAxkBAAIB-Gq7Nvm6741uodfgykDG4sP9eg6tAAIXEGsbKd_ZVeTAhZsfKPbbAQADAgADeQADPQQ",
    "countdown": "AgACAgUAAxkBAAIB-Wq7NvnEGrlOwjaMPTNFHpoEb0-nAAIYEGsbKd_ZVdq5nUuWQj_7AQADAgADeQADPQQ",
}

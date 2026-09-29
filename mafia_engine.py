import random

import mafia_config as mc


class Player:
    def __init__(self, user_id: int, name: str):
        self.user_id = user_id
        self.name = name
        self.role = None
        self.alive = True
        self.dm_open = False

    @property
    def team(self) -> str:
        return mc.ROLES[self.role][2]

    @property
    def label(self) -> str:
        emoji, title, _ = mc.ROLES[self.role]
        return f"{emoji} {title}"


def build_role_list(n: int) -> list[str]:
    assert mc.MIN_PLAYERS <= n <= mc.MAX_PLAYERS
    roles = list(mc.BASE_ROLES)
    for count in range(5, n + 1):
        roles.append(mc.ROLE_ADDITIONS[count])
    return roles


class Game:
    def __init__(self, game_id: str, chat_id: int, host_id: int):
        self.game_id = game_id
        self.chat_id = chat_id
        self.host_id = host_id
        self.players: dict[int, Player] = {}
        self.phase = "lobby"
        self.day = 0
        self.last_save = None   # doctor's target last night
        self.night = {}         # night actions for the current night
        self.votes = {}         # tribunal: voter_id -> target_id (0 = skip)

    def add_player(self, user_id: int, name: str) -> bool:
        if user_id in self.players or len(self.players) >= mc.MAX_PLAYERS:
            return False
        self.players[user_id] = Player(user_id, name)
        return True

    def remove_player(self, user_id: int) -> None:
        self.players.pop(user_id, None)

    def assign_roles(self) -> None:
        roles = build_role_list(len(self.players))
        random.shuffle(roles)
        for p, role in zip(self.players.values(), roles):
            p.role = role

    def alive_players(self) -> list[Player]:
        return [p for p in self.players.values() if p.alive]

    def with_role(self, *roles: str) -> list[Player]:
        return [p for p in self.alive_players() if p.role in roles]

    # ---- win checks -------------------------------------------------
    def check_winner(self):
        """Returns 'maniac', 'mafia', 'town' or None."""
        alive = self.alive_players()
        mafia = [p for p in alive if p.team == "mafia"]
        maniac = [p for p in alive if p.role == "maniac"]

        if maniac and len(alive) <= 2:
            return "maniac"
        if mafia and len(mafia) >= len(alive) - len(mafia):
            return "mafia"
        if not mafia and not maniac:
            return "town"
        return None

    # ---- journalist -------------------------------------------------
    def apparent_team(self, p: Player) -> str:
        if p.role in ("maniac", "suicide"):
            return p.role
        if p.role == "don":
            return "town"
        if p.user_id in self.night.get("protected", set()):
            return "town"
        return p.team

    def same_team(self, a: Player, b: Player) -> bool:
        ta, tb = self.apparent_team(a), self.apparent_team(b)
        if ta in ("maniac", "suicide") or tb in ("maniac", "suicide"):
            return False
        return ta == tb

    # ---- night actions ----------------------------------------------
    def start_night(self) -> None:
        self.phase = "night"
        self.day += 1
        self.night = {"mafia_votes": {}, "protected": set(), "blocked": set()}

    def _valid(self, uid) -> bool:
        return uid in self.players and self.players[uid].alive

    def set_action(self, actor: Player, target: int, target2: int | None = None) -> bool:
        role = actor.role
        if not actor.alive or not self._valid(target):
            return False
        if role != "doctor" and target == actor.user_id:
            return False
        if role in ("don", "mafia"):
            self.night["mafia_votes"][actor.user_id] = target
        elif role == "doctor":
            if target == self.last_save:
                return False
            self.night["doctor"] = target
        elif role == "journalist":
            if target2 is None or target2 == target or target2 == actor.user_id or not self._valid(target2):
                return False
            self.night["journalist"] = (target, target2)
        elif role in ("hooker", "lawyer", "maniac", "hobo"):
            self.night[role] = target
        else:
            return False
        return True

    def mafia_target(self):
        votes = {u: t for u, t in self.night["mafia_votes"].items()
                 if u not in self.night["blocked"]}
        if not votes:
            return None
        tally = {}
        for t in votes.values():
            tally[t] = tally.get(t, 0) + 1
        top = max(tally.values())
        tied = [t for t, c in tally.items() if c == top]
        if len(tied) == 1:
            return tied[0]
        don = self.with_role("don")
        if don and votes.get(don[0].user_id) in tied:
            return votes[don[0].user_id]
        return random.choice(tied)

    # ---- night resolution -------------------------------------------
    def resolve_night(self) -> dict:
        n = self.night
        alive_ids = {p.user_id for p in self.alive_players()}
        res = {"deaths": [], "saved": None, "lucky": [],
               "blocked": [], "journalist": None, "hobo": None}

        # 1. Hooker blocks
        for _ in self.with_role("hooker"):
            t = n.get("hooker")
            if t is not None and t in alive_ids:
                n["blocked"].add(t)

        # 2. Lawyer protects
        for lw in self.with_role("lawyer"):
            t = n.get("lawyer")
            if t is not None and lw.user_id not in n["blocked"]:
                n["protected"].add(t)

        # 3. Mafia and Maniac attacks
        attacks = {}
        mt = self.mafia_target()
        if mt is not None:
            attacks.setdefault(mt, []).append("Mafia")
        for m in self.with_role("maniac"):
            t = n.get("maniac")
            if t is not None and m.user_id not in n["blocked"]:
                attacks.setdefault(t, []).append("Maniac")

        # 4. Doctor saves
        saved = None
        for d in self.with_role("doctor"):
            t = n.get("doctor")
            if t is not None and d.user_id not in n["blocked"]:
                saved = t
        self.last_save = saved
        if saved in attacks:
            res["saved"] = saved
            del attacks[saved]

        # Deaths (Lucky gets a roll)
        for tid, killers in attacks.items():
            p = self.players[tid]
            if p.role == "lucky" and random.random() < mc.LUCKY_SURVIVE_CHANCE:
                res["lucky"].append(p)
                continue
            p.alive = False
            res["deaths"].append((p, killers[0]))

        # 5. Journalist and Hobo
        for j in self.with_role("journalist"):
            pair = n.get("journalist")
            if pair and j.user_id not in n["blocked"]:
                a, b = self.players[pair[0]], self.players[pair[1]]
                res["journalist"] = (j.user_id, a, b, self.same_team(a, b))
        for h in self.with_role("hobo"):
            t = n.get("hobo")
            if t is not None and h.user_id not in n["blocked"]:
                killer = next((k for p, k in res["deaths"] if p.user_id == t), None)
                res["hobo"] = (h.user_id, self.players[t], killer)

        res["blocked"] = [self.players[u] for u in n["blocked"]]
        return res

    # ---- tribunal ---------------------------------------------------
    def start_tribunal(self) -> None:
        self.phase = "tribunal"
        self.votes = {}

    def cast_vote(self, voter_id: int, target_id: int) -> bool:
        v = self.players.get(voter_id)
        if v is None or not v.alive or voter_id in self.votes:
            return False
        if target_id != 0 and (not self._valid(target_id) or target_id == voter_id):
            return False
        self.votes[voter_id] = target_id
        return True

    def resolve_tribunal(self) -> dict:
        res = {"lynched": None, "suicide_win": False, "kamikaze": False, "tally": {}}
        tally, skips = {}, 0
        for t in self.votes.values():
            if t == 0:
                skips += 1
            else:
                tally[t] = tally.get(t, 0) + 1
        res["tally"] = tally
        if not tally:
            return res
        top = max(tally.values())
        tied = [t for t, c in tally.items() if c == top]
        if len(tied) != 1 or top <= skips:
            return res
        p = self.players[tied[0]]
        p.alive = False
        res["lynched"] = p
        if p.role == "suicide":
            res["suicide_win"] = True
        elif p.role == "kamikaze":
            res["kamikaze"] = True
        return res

    def kamikaze_take(self, kami: Player, target_id: int):
        if kami.role != "kamikaze" or not self._valid(target_id):
            return None
        p = self.players[target_id]
        p.alive = False
        return p

    # ---- dawn and game over helpers ---------------------------------
    def dawn_hint(self, k: int = 4) -> str:
        labels = sorted({p.label for p in self.alive_players()})
        random.shuffle(labels)
        return ", ".join(labels[:k])

    def final_roster(self) -> list[tuple[str, str]]:
        return [(p.name, p.label) for p in self.players.values()]

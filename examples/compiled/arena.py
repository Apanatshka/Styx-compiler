import uuid
from styx.common.operator import Operator
from styx.common.stateful_function import StatefulFunction
from styx.common.logging import logging


def send_reply(ctx: StatefulFunction, reply_to: list, result):
    if reply_to:
        reply_info = reply_to.pop()
        ctx.call_remote_async(
            operator_name=reply_info["op_name"],
            function_name=reply_info["fun"],
            key=reply_info["id"],
            params=(reply_info["context"], result, reply_to),
        )
    else:
        return result


def push_continuation(
    ctx: StatefulFunction, reply_to: list, op_name: str, fun: str, step_id: str, context: dict
) -> list:
    context_dict = ctx.get_func_context()
    continuation_id = str(uuid.uuid4())
    context_dict[continuation_id] = context
    ctx.put_func_context(context_dict)
    if reply_to is None:
        reply_to = []
    reply_to.append(
        {
            "op_name": op_name,
            "fun": fun,
            "id": step_id,
            "context": continuation_id,
        }
    )
    return reply_to


def resolve_context(ctx: StatefulFunction, context_data) -> dict:
    if isinstance(context_data, dict):
        return context_data

    ctx_dict = ctx.get_func_context()
    params = ctx_dict.pop(context_data)
    ctx.put_func_context(ctx_dict)
    return params


class EntityIsDead(Exception):
    pass


monster_operator = Operator("monster", n_partitions=4)


@monster_operator.register
async def create(ctx: StatefulFunction, name: str, hp: int, damage: int, gold_drop: int, reply_to: list = None):
    state = {"name": name, "hp": hp, "damage": damage, "gold_drop": gold_drop}
    ctx.put(state)
    ctx.put_func_context({})
    return send_reply(ctx, reply_to, ctx.key)


@monster_operator.register
async def take_damage(ctx: StatefulFunction, amount: int, reply_to: list = None) -> bool:
    state = ctx.get()
    if state["hp"] <= 0:
        raise EntityIsDead("Monster is already dead.")
    state["hp"] -= amount
    ctx.put(state)
    return send_reply(ctx, reply_to, state["hp"] <= 0)


@monster_operator.register
async def get_combat_stats(ctx: StatefulFunction, reply_to: list = None) -> dict:
    state = ctx.get()
    return send_reply(ctx, reply_to, {"damage": state["damage"], "gold_drop": state["gold_drop"]})


player_operator = Operator("player", n_partitions=4)


@player_operator.register
async def create(ctx: StatefulFunction, username: str, reply_to: list = None):
    state = {"username": username, "hp": 100, "gold": 0, "potions": 3}
    ctx.put(state)
    ctx.put_func_context({})
    return send_reply(ctx, reply_to, ctx.key)


@player_operator.register
async def receive_damage(ctx: StatefulFunction, amount: int, reply_to: list = None) -> bool:
    state = ctx.get()
    state["hp"] -= amount
    ctx.put(state)
    return send_reply(ctx, reply_to, state["hp"] <= 0)


@player_operator.register
async def heal_if_needed(ctx: StatefulFunction, reply_to: list = None) -> str:
    state = ctx.get()
    if state["hp"] < 30 and state["potions"] > 0:
        state["hp"] += 50
        state["potions"] -= 1
        ctx.put(state)
        return send_reply(ctx, reply_to, "Healed")
    ctx.put(state)
    return send_reply(ctx, reply_to, "No healing needed")


@player_operator.register
async def add_gold(ctx: StatefulFunction, amount: int, reply_to: list = None) -> int:
    state = ctx.get()
    state["gold"] += amount
    ctx.put(state)
    return send_reply(ctx, reply_to, state["gold"])


arena_operator = Operator("arena", n_partitions=4)


@arena_operator.register
async def create(ctx: StatefulFunction, arena_id: str, reply_to: list = None):
    state = {"arena_id": arena_id, "battles_fought": 0}
    ctx.put(state)
    ctx.put_func_context({})
    return send_reply(ctx, reply_to, ctx.key)


@arena_operator.register
async def run_gauntlet(ctx: StatefulFunction, player: str, monsters: list[str], reply_to: list = None) -> str:
    total_gold_earned = 0
    __loop_index_1 = 0
    ctx.call_remote_async(
        operator_name="arena",
        function_name="run_gauntlet_step_2",
        key=ctx.key,
        params=(
            {
                "__loop_index_1": __loop_index_1,
                "monsters": monsters,
                "player": player,
                "total_gold_earned": total_gold_earned,
            },
            None,
            reply_to,
        ),
    )


@arena_operator.register
async def run_gauntlet_step_2(ctx: StatefulFunction, func_context, placeholder_return=None, reply_to: list = None):
    state = ctx.get()
    params = resolve_context(ctx, func_context)
    (__loop_index_1, monsters, player, total_gold_earned) = (
        params["__loop_index_1"],
        params["monsters"],
        params["player"],
        params["total_gold_earned"],
    )
    if __loop_index_1 >= len(monsters):
        state["battles_fought"] += 1
        ctx.put(state)
        return send_reply(ctx, reply_to, "Gauntlet cleared! Total gold earned: " + str(total_gold_earned))
    else:
        i = __loop_index_1
        __loop_index_1 += 1
        current_monster = monsters[i]
        reply_to = push_continuation(
            ctx,
            reply_to,
            "arena",
            "run_gauntlet_step_3",
            ctx.key,
            {
                "current_monster": current_monster,
                "__loop_index_1": __loop_index_1,
                "total_gold_earned": total_gold_earned,
                "player": player,
                "monsters": monsters,
                "i": i,
            },
        )
        ctx.put(state)
        ctx.call_remote_async(
            operator_name="monster", function_name="get_combat_stats", key=current_monster, params=(reply_to,)
        )


@arena_operator.register
async def run_gauntlet_step_3(ctx: StatefulFunction, func_context, stats=None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, current_monster, i, monsters, player, total_gold_earned) = (
        params["__loop_index_1"],
        params["current_monster"],
        params["i"],
        params["monsters"],
        params["player"],
        params["total_gold_earned"],
    )
    reply_to = push_continuation(
        ctx,
        reply_to,
        "arena",
        "run_gauntlet_step_4",
        ctx.key,
        {
            "current_monster": current_monster,
            "__loop_index_1": __loop_index_1,
            "total_gold_earned": total_gold_earned,
            "player": player,
            "monsters": monsters,
            "stats": stats,
            "i": i,
        },
    )
    ctx.call_remote_async(
        operator_name="monster", function_name="take_damage", key=current_monster, params=(25, reply_to)
    )


@arena_operator.register
async def run_gauntlet_step_4(ctx: StatefulFunction, func_context, is_dead=None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, current_monster, i, monsters, player, stats, total_gold_earned) = (
        params["__loop_index_1"],
        params["current_monster"],
        params["i"],
        params["monsters"],
        params["player"],
        params["stats"],
        params["total_gold_earned"],
    )

    if is_dead:
        gold = stats["gold_drop"]
        reply_to = push_continuation(
            ctx,
            reply_to,
            "arena",
            "run_gauntlet_step_5",
            ctx.key,
            {
                "current_monster": current_monster,
                "is_dead": is_dead,
                "gold": gold,
                "__loop_index_1": __loop_index_1,
                "total_gold_earned": total_gold_earned,
                "player": player,
                "monsters": monsters,
                "stats": stats,
                "i": i,
            },
        )
        ctx.call_remote_async(operator_name="player", function_name="add_gold", key=player, params=(gold, reply_to))
    else:
        dmg = stats["damage"]
        reply_to = push_continuation(
            ctx,
            reply_to,
            "arena",
            "run_gauntlet_step_6",
            ctx.key,
            {
                "current_monster": current_monster,
                "is_dead": is_dead,
                "__loop_index_1": __loop_index_1,
                "total_gold_earned": total_gold_earned,
                "player": player,
                "dmg": dmg,
                "monsters": monsters,
                "stats": stats,
                "i": i,
            },
        )
        ctx.call_remote_async(
            operator_name="player", function_name="receive_damage", key=player, params=(dmg, reply_to)
        )


@arena_operator.register
async def run_gauntlet_step_5(ctx: StatefulFunction, func_context, placeholder_return=None, reply_to: list = None):
    state = ctx.get()
    params = resolve_context(ctx, func_context)
    (__loop_index_1, current_monster, gold, i, is_dead, monsters, player, stats, total_gold_earned) = (
        params["__loop_index_1"],
        params["current_monster"],
        params["gold"],
        params["i"],
        params["is_dead"],
        params["monsters"],
        params["player"],
        params["stats"],
        params["total_gold_earned"],
    )
    total_gold_earned += gold
    ctx.put(state)
    ctx.call_remote_async(
        operator_name="arena",
        function_name="run_gauntlet_step_2",
        key=ctx.key,
        params=(
            {
                "__loop_index_1": __loop_index_1,
                "current_monster": current_monster,
                "gold": gold,
                "i": i,
                "is_dead": is_dead,
                "monsters": monsters,
                "player": player,
                "stats": stats,
                "total_gold_earned": total_gold_earned,
            },
            None,
            reply_to,
        ),
    )


@arena_operator.register
async def run_gauntlet_step_6(ctx: StatefulFunction, func_context, player_died=None, reply_to: list = None):
    params = resolve_context(ctx, func_context)
    (__loop_index_1, current_monster, dmg, i, is_dead, monsters, player, stats, total_gold_earned) = (
        params["__loop_index_1"],
        params["current_monster"],
        params["dmg"],
        params["i"],
        params["is_dead"],
        params["monsters"],
        params["player"],
        params["stats"],
        params["total_gold_earned"],
    )

    if player_died:
        return send_reply(
            ctx, reply_to, "Player defeated at monster " + str(i) + ". Total gold: " + str(total_gold_earned)
        )
    reply_to = push_continuation(
        ctx,
        reply_to,
        "arena",
        "run_gauntlet_step_2",
        ctx.key,
        {
            "player_died": player_died,
            "current_monster": current_monster,
            "is_dead": is_dead,
            "__loop_index_1": __loop_index_1,
            "total_gold_earned": total_gold_earned,
            "player": player,
            "dmg": dmg,
            "monsters": monsters,
            "stats": stats,
            "i": i,
        },
    )
    ctx.call_remote_async(operator_name="player", function_name="heal_if_needed", key=player, params=(reply_to,))

from styx.common.logging import logging
from styx.common.operator import Operator
from styx.common.stateful_function import StatefulFunction

item_operator = Operator("item", n_partitions=4)


class OutOfStock(Exception):
    pass


@item_operator.register
async def create(ctx: StatefulFunction, item_name: str, price: int, reply_to: list = None):
    state = ctx.get()
    state = {"item_name": item_name, "stock": 0, "price": price}
    ctx.put(state)
    if reply_to:
        reply_info = reply_to.pop()
        ctx.call_remote_async(
            operator_name=reply_info["op_name"],
            function_name=reply_info["fun"],
            key=reply_info["id"],
            params=(reply_info["context"], ctx.key, reply_to),
        )
        return None
    return ctx.key


@item_operator.register
async def get_price(ctx: StatefulFunction, reply_to: list = None) -> int:
    state = ctx.get()
    if reply_to:
        reply_info = reply_to.pop()
        ctx.call_remote_async(
            operator_name=reply_info["op_name"],
            function_name=reply_info["fun"],
            key=reply_info["id"],
            params=(reply_info["context"], state["price"], reply_to),
        )
        return None
    return state["price"]


@item_operator.register
async def get_stock(ctx: StatefulFunction, reply_to: list = None) -> int:
    state = ctx.get()
    if reply_to:
        reply_info = reply_to.pop()
        ctx.call_remote_async(
            operator_name=reply_info["op_name"],
            function_name=reply_info["fun"],
            key=reply_info["id"],
            params=(reply_info["context"], state["stock"], reply_to),
        )
        return None
    return state["stock"]


@item_operator.register
async def update_stock(ctx: StatefulFunction, amount: int, reply_to: list = None) -> bool:
    state = ctx.get()
    if (state["stock"] + amount) < 0:
        raise OutOfStock("Not enough stock to update.")
    state["stock"] += amount
    ctx.put(state)
    if reply_to:
        reply_info = reply_to.pop()
        ctx.call_remote_async(
            operator_name=reply_info["op_name"],
            function_name=reply_info["fun"],
            key=reply_info["id"],
            params=(reply_info["context"], True, reply_to),
        )
        return None
    return True


@item_operator.register
async def test_stack(ctx: StatefulFunction, user: str, amount: int, reply_to: list = None) -> bool:
    state = ctx.get()
    if reply_to is None:
        reply_to = []
    reply_to.append(
        {"op_name": "item", "fun": "test_stack_step_2", "id": ctx.key, "context": {"user": user, "amount": amount}}
    )
    ctx.call_remote_async(
        operator_name="user", function_name="buy_item", key=user, params=(amount, state["item_name"], reply_to)
    )


@item_operator.register
async def test_stack_step_2(ctx: StatefulFunction, params, placeholder_return=None, reply_to: list = None):
    state = ctx.get()
    (amount, user) = (params["amount"], params["user"])
    if reply_to:
        reply_info = reply_to.pop()
        ctx.call_remote_async(
            operator_name=reply_info["op_name"],
            function_name=reply_info["fun"],
            key=reply_info["id"],
            params=(reply_info["context"], True, reply_to),
        )
        return None
    return True


user_operator = Operator("user", n_partitions=4)


class NotEnoughBalance(Exception):
    pass


@user_operator.register
async def create(ctx: StatefulFunction, username: str, reply_to: list = None):
    state = ctx.get()
    state = {"username": username, "balance": 0, "myitems": []}
    ctx.put(state)
    if reply_to:
        reply_info = reply_to.pop()
        ctx.call_remote_async(
            operator_name=reply_info["op_name"],
            function_name=reply_info["fun"],
            key=reply_info["id"],
            params=(reply_info["context"], ctx.key, reply_to),
        )
        return None
    return ctx.key


@user_operator.register
async def get_balance(ctx: StatefulFunction, reply_to: list = None) -> int:
    state = ctx.get()
    if reply_to:
        reply_info = reply_to.pop()
        ctx.call_remote_async(
            operator_name=reply_info["op_name"],
            function_name=reply_info["fun"],
            key=reply_info["id"],
            params=(reply_info["context"], state["balance"], reply_to),
        )
        return None
    return state["balance"]


@user_operator.register
async def get_items(ctx: StatefulFunction, reply_to: list = None) -> list[str]:
    state = ctx.get()
    if reply_to:
        reply_info = reply_to.pop()
        ctx.call_remote_async(
            operator_name=reply_info["op_name"],
            function_name=reply_info["fun"],
            key=reply_info["id"],
            params=(reply_info["context"], state["myitems"], reply_to),
        )
        return None
    return state["myitems"]


@user_operator.register
async def add_balance(ctx: StatefulFunction, amount: int, reply_to: list = None) -> bool:
    state = ctx.get()
    state["balance"] += amount
    ctx.put(state)
    if reply_to:
        reply_info = reply_to.pop()
        ctx.call_remote_async(
            operator_name=reply_info["op_name"],
            function_name=reply_info["fun"],
            key=reply_info["id"],
            params=(reply_info["context"], True, reply_to),
        )
        return None
    return True


@user_operator.register
async def buy_item(ctx: StatefulFunction, amount: int, item: str, reply_to: list = None) -> bool:
    state = ctx.get()
    if reply_to is None:
        reply_to = []
    reply_to.append(
        {"op_name": "user", "fun": "buy_item_step_2", "id": ctx.key, "context": {"amount": amount, "item": item}}
    )
    ctx.call_remote_async(operator_name="item", function_name="get_price", key=item, params=(reply_to,))


@user_operator.register
async def buy_item_step_2(ctx: StatefulFunction, params, attr_1=None, reply_to: list = None):
    state = ctx.get()
    (amount, item) = (params["amount"], params["item"])
    total_price = amount * attr_1

    if state["balance"] < total_price:
        raise NotEnoughBalance("Not enough balance to buy the item.")
    if reply_to is None:
        reply_to = []
    reply_to.append(
        {
            "op_name": "user",
            "fun": "buy_item_step_3",
            "id": ctx.key,
            "context": {"total_price": total_price, "amount": amount, "attr_1": attr_1, "item": item},
        }
    )
    ctx.call_remote_async(operator_name="item", function_name="update_stock", key=item, params=(-amount, reply_to))


@user_operator.register
async def buy_item_step_3(ctx: StatefulFunction, params, placeholder_return=None, reply_to: list = None):
    state = ctx.get()
    (amount, attr_1, item, total_price) = (params["amount"], params["attr_1"], params["item"], params["total_price"])
    state["balance"] -= total_price
    state["myitems"].append(item)
    ctx.put(state)
    if reply_to:
        reply_info = reply_to.pop()
        ctx.call_remote_async(
            operator_name=reply_info["op_name"],
            function_name=reply_info["fun"],
            key=reply_info["id"],
            params=(reply_info["context"], True, reply_to),
        )
        return None
    return True


@user_operator.register
async def create_user_item(ctx: StatefulFunction, name: str, price: int, reply_to: list = None) -> str:
    state = ctx.get()
    if reply_to is None:
        reply_to = []
    reply_to.append(
        {"op_name": "user", "fun": "create_user_item_step_2", "id": ctx.key, "context": {"price": price, "name": name}}
    )
    ctx.call_remote_async(
        operator_name="item",
        function_name="create",
        key=state["username"] + "_" + name,
        params=(state["username"] + "_" + name, price, reply_to),
    )


@user_operator.register
async def create_user_item_step_2(ctx: StatefulFunction, params, new_item=None, reply_to: list = None):
    state = ctx.get()
    (name, price) = (params["name"], params["price"])
    if reply_to is None:
        reply_to = []
    reply_to.append(
        {
            "op_name": "user",
            "fun": "create_user_item_step_3",
            "id": ctx.key,
            "context": {"price": price, "name": name, "new_item": new_item},
        }
    )
    ctx.call_remote_async(operator_name="item", function_name="update_stock", key=new_item, params=(1, reply_to))


@user_operator.register
async def create_user_item_step_3(ctx: StatefulFunction, params, placeholder_return=None, reply_to: list = None):
    state = ctx.get()
    (name, new_item, price) = (params["name"], params["new_item"], params["price"])
    if reply_to:
        reply_info = reply_to.pop()
        ctx.call_remote_async(
            operator_name=reply_info["op_name"],
            function_name=reply_info["fun"],
            key=reply_info["id"],
            params=(reply_info["context"], new_item, reply_to),
        )
        return None
    return new_item


@user_operator.register
async def test_loop(ctx: StatefulFunction, amount, item: str, reply_to: list = None) -> bool:
    state = ctx.get()
    state["__loop_index_1"] = 0
    ctx.put(state)
    ctx.call_remote_async(
        operator_name="user",
        function_name="test_loop_step_2",
        key=ctx.key,
        params=({"amount": amount, "item": item}, None, reply_to),
    )
    ctx.put(state)


@user_operator.register
async def test_loop_step_2(ctx: StatefulFunction, params, placeholder_return=None, reply_to: list = None):
    state = ctx.get()
    (amount, item) = (params["amount"], params["item"])
    if state["__loop_index_1"] >= amount:
        if reply_to:
            reply_info = reply_to.pop()
            ctx.call_remote_async(
                operator_name=reply_info["op_name"],
                function_name=reply_info["fun"],
                key=reply_info["id"],
                params=(reply_info["context"], True, reply_to),
            )
            return None
        return True
    i = state["__loop_index_1"]
    state["__loop_index_1"] += 1
    if reply_to is None:
        reply_to = []
    reply_to.append(
        {
            "op_name": "user",
            "fun": "test_loop_step_2",
            "id": ctx.key,
            "context": {"i": i, "amount": amount, "item": item},
        }
    )
    ctx.call_remote_async(operator_name="item", function_name="update_stock", key=item, params=(-1, reply_to))
    ctx.put(state)


@user_operator.register
async def process_inventory(ctx: StatefulFunction, budget: int, items: list[str], reply_to: list = None) -> bool:
    state = ctx.get()
    total_spent = 0
    logging.warn(f"Processing inventory for user {state['username']} with budget {budget}")
    state["__loop_index_1"] = 0
    ctx.put(state)
    ctx.call_remote_async(
        operator_name="user",
        function_name="process_inventory_step_2",
        key=ctx.key,
        params=({"budget": budget, "items": items, "total_spent": total_spent}, None, reply_to),
    )
    ctx.put(state)


@user_operator.register
async def process_inventory_step_2(ctx: StatefulFunction, params, placeholder_return=None, reply_to: list = None):
    state = ctx.get()
    (budget, items, total_spent) = (params["budget"], params["items"], params["total_spent"])
    if state["__loop_index_1"] >= len(items):
        state["balance"] -= total_spent
        ctx.put(state)
        if reply_to:
            reply_info = reply_to.pop()
            ctx.call_remote_async(
                operator_name=reply_info["op_name"],
                function_name=reply_info["fun"],
                key=reply_info["id"],
                params=(reply_info["context"], "New balance: " + str(state["balance"]), reply_to),
            )
            return None
        return "New balance: " + str(state["balance"])
    item = items[state["__loop_index_1"]]
    state["__loop_index_1"] += 1
    logging.warn(f"Evaluating item {item}")
    if reply_to is None:
        reply_to = []
    reply_to.append(
        {
            "op_name": "user",
            "fun": "process_inventory_step_3",
            "id": ctx.key,
            "context": {"budget": budget, "total_spent": total_spent, "items": items, "item": item},
        }
    )
    ctx.call_remote_async(operator_name="item", function_name="get_price", key=item, params=(reply_to,))
    ctx.put(state)


@user_operator.register
async def process_inventory_step_3(ctx: StatefulFunction, params, price=None, reply_to: list = None):
    state = ctx.get()
    (budget, item, items, total_spent) = (params["budget"], params["item"], params["items"], params["total_spent"])
    logging.warn(f"Item price: {price}")
    if reply_to is None:
        reply_to = []
    reply_to.append(
        {
            "op_name": "user",
            "fun": "process_inventory_step_4",
            "id": ctx.key,
            "context": {"budget": budget, "total_spent": total_spent, "price": price, "items": items, "item": item},
        }
    )
    ctx.call_remote_async(operator_name="item", function_name="get_stock", key=item, params=(reply_to,))


@user_operator.register
async def process_inventory_step_4(ctx: StatefulFunction, params, stock=None, reply_to: list = None):
    state = ctx.get()
    (budget, item, items, price, total_spent) = (
        params["budget"],
        params["item"],
        params["items"],
        params["price"],
        params["total_spent"],
    )
    logging.warn(f"Item stock: {stock}")

    if price < budget:
        if stock > 10:
            if reply_to is None:
                reply_to = []
            reply_to.append(
                {
                    "op_name": "user",
                    "fun": "process_inventory_step_5",
                    "id": ctx.key,
                    "context": {
                        "budget": budget,
                        "total_spent": total_spent,
                        "price": price,
                        "stock": stock,
                        "items": items,
                        "item": item,
                    },
                }
            )
            ctx.call_remote_async(operator_name="item", function_name="update_stock", key=item, params=(-5, reply_to))
        elif stock > 0:
            if reply_to is None:
                reply_to = []
            reply_to.append(
                {
                    "op_name": "user",
                    "fun": "process_inventory_step_6",
                    "id": ctx.key,
                    "context": {
                        "budget": budget,
                        "total_spent": total_spent,
                        "price": price,
                        "items": items,
                        "item": item,
                        "stock": stock,
                    },
                }
            )
            ctx.call_remote_async(operator_name="item", function_name="update_stock", key=item, params=(-1, reply_to))
        else:
            total_spent = total_spent + price
            logging.warn(f"Total spent so far: {total_spent}")
            state["myitems"].append(item)
            ctx.put(state)
            ctx.call_remote_async(
                operator_name="user",
                function_name="process_inventory_step_2",
                key=ctx.key,
                params=(
                    {
                        "budget": budget,
                        "item": item,
                        "items": items,
                        "price": price,
                        "stock": stock,
                        "total_spent": total_spent,
                    },
                    None,
                    reply_to,
                ),
            )
    else:
        if reply_to is None:
            reply_to = []
        reply_to.append(
            {
                "op_name": "user",
                "fun": "process_inventory_step_7",
                "id": ctx.key,
                "context": {
                    "budget": budget,
                    "total_spent": total_spent,
                    "price": price,
                    "items": items,
                    "item": item,
                    "stock": stock,
                },
            }
        )
        ctx.call_remote_async(operator_name="item", function_name="update_stock", key=item, params=(1, reply_to))


@user_operator.register
async def process_inventory_step_5(ctx: StatefulFunction, params, placeholder_return=None, reply_to: list = None):
    state = ctx.get()
    (budget, item, items, price, stock, total_spent) = (
        params["budget"],
        params["item"],
        params["items"],
        params["price"],
        params["stock"],
        params["total_spent"],
    )
    total_spent = total_spent + price
    logging.warn(f"Total spent so far: {total_spent}")
    state["myitems"].append(item)
    ctx.put(state)
    ctx.call_remote_async(
        operator_name="user",
        function_name="process_inventory_step_2",
        key=ctx.key,
        params=(
            {
                "budget": budget,
                "item": item,
                "items": items,
                "price": price,
                "stock": stock,
                "total_spent": total_spent,
            },
            None,
            reply_to,
        ),
    )


@user_operator.register
async def process_inventory_step_6(ctx: StatefulFunction, params, placeholder_return=None, reply_to: list = None):
    state = ctx.get()
    (budget, item, items, price, stock, total_spent) = (
        params["budget"],
        params["item"],
        params["items"],
        params["price"],
        params["stock"],
        params["total_spent"],
    )
    total_spent = total_spent + price
    logging.warn(f"Total spent so far: {total_spent}")
    state["myitems"].append(item)
    ctx.put(state)
    ctx.call_remote_async(
        operator_name="user",
        function_name="process_inventory_step_2",
        key=ctx.key,
        params=(
            {
                "budget": budget,
                "item": item,
                "items": items,
                "price": price,
                "stock": stock,
                "total_spent": total_spent,
            },
            None,
            reply_to,
        ),
    )


@user_operator.register
async def process_inventory_step_7(ctx: StatefulFunction, params, placeholder_return=None, reply_to: list = None):
    state = ctx.get()
    (budget, item, items, price, stock, total_spent) = (
        params["budget"],
        params["item"],
        params["items"],
        params["price"],
        params["stock"],
        params["total_spent"],
    )
    total_spent = total_spent + price
    logging.warn(f"Total spent so far: {total_spent}")
    state["myitems"].append(item)
    ctx.put(state)
    ctx.call_remote_async(
        operator_name="user",
        function_name="process_inventory_step_2",
        key=ctx.key,
        params=(
            {
                "budget": budget,
                "item": item,
                "items": items,
                "price": price,
                "stock": stock,
                "total_spent": total_spent,
            },
            None,
            reply_to,
        ),
    )


@user_operator.register
async def nested_loop_test(ctx: StatefulFunction, amount: int, items: list[str], reply_to: list = None) -> bool:
    state = ctx.get()
    total = 0
    state["__loop_index_1"] = 0
    ctx.put(state)
    ctx.call_remote_async(
        operator_name="user",
        function_name="nested_loop_test_step_2",
        key=ctx.key,
        params=({"amount": amount, "items": items, "total": total}, None, reply_to),
    )
    ctx.put(state)


@user_operator.register
async def nested_loop_test_step_2(ctx: StatefulFunction, params, placeholder_return=None, reply_to: list = None):
    state = ctx.get()
    (amount, items, total) = (params["amount"], params["items"], params["total"])
    if state["__loop_index_1"] >= len(items):
        state["balance"] -= total
        ctx.put(state)
        if reply_to:
            reply_info = reply_to.pop()
            ctx.call_remote_async(
                operator_name=reply_info["op_name"],
                function_name=reply_info["fun"],
                key=reply_info["id"],
                params=(reply_info["context"], "New balance: " + str(state["balance"]), reply_to),
            )
            return None
        return "New balance: " + str(state["balance"])
    item = items[state["__loop_index_1"]]
    state["__loop_index_1"] += 1
    if reply_to is None:
        reply_to = []
    reply_to.append(
        {
            "op_name": "user",
            "fun": "nested_loop_test_step_3",
            "id": ctx.key,
            "context": {"total": total, "amount": amount, "items": items, "item": item},
        }
    )
    ctx.call_remote_async(operator_name="item", function_name="get_price", key=item, params=(reply_to,))
    ctx.put(state)


@user_operator.register
async def nested_loop_test_step_3(ctx: StatefulFunction, params, price=None, reply_to: list = None):
    state = ctx.get()
    (amount, item, items, total) = (params["amount"], params["item"], params["items"], params["total"])
    state["__loop_index_2"] = 0
    ctx.put(state)
    ctx.call_remote_async(
        operator_name="user",
        function_name="nested_loop_test_step_4",
        key=ctx.key,
        params=({"amount": amount, "item": item, "items": items, "price": price, "total": total}, None, reply_to),
    )
    ctx.put(state)


@user_operator.register
async def nested_loop_test_step_4(ctx: StatefulFunction, params, placeholder_return=None, reply_to: list = None):
    state = ctx.get()
    (amount, item, items, price, total) = (
        params["amount"],
        params["item"],
        params["items"],
        params["price"],
        params["total"],
    )
    if state["__loop_index_2"] >= amount:
        total = total + price
        ctx.put(state)
        ctx.call_remote_async(
            operator_name="user",
            function_name="nested_loop_test_step_2",
            key=ctx.key,
            params=({"amount": amount, "item": item, "items": items, "price": price, "total": total}, None, reply_to),
        )
    else:
        i = state["__loop_index_2"]
        state["__loop_index_2"] += 1
        if reply_to is None:
            reply_to = []
        reply_to.append(
            {
                "op_name": "user",
                "fun": "nested_loop_test_step_4",
                "id": ctx.key,
                "context": {"price": price, "amount": amount, "items": items, "i": i, "total": total, "item": item},
            }
        )
        ctx.call_remote_async(operator_name="item", function_name="update_stock", key=item, params=(-1, reply_to))
    ctx.put(state)


from styx.common.operator import Operator
from styx.common.stateful_function import StatefulFunction

item_operator = Operator('item', n_partitions=4)


class OutOfStock(Exception):
    pass


@item_operator.register
async def create(ctx: StatefulFunction, item_name: str, price: int, reply_to: list = None):
    state = ctx.get()
    state = {'item_name': item_name, 'stock': 0, 'price': price, 'userlist': []}
    ctx.put(state)
    if reply_to:
        reply_info = reply_to.pop()
        ctx.call_remote_async(operator_name = reply_info["op_name"], function_name = reply_info["fun"], key = reply_info["id"], params = (reply_info["context"], ctx.key, reply_to))
        return
    else:
        return ctx.key


@item_operator.register
async def get_price(ctx: StatefulFunction, reply_to: list = None) -> int:
    state = ctx.get()
    if reply_to:
        reply_info = reply_to.pop()
        ctx.call_remote_async(operator_name = reply_info["op_name"], function_name = reply_info["fun"], key = reply_info["id"], params = (reply_info["context"], state['price'], reply_to))
        return
    else:
        return state['price']


@item_operator.register
async def update_stock(ctx: StatefulFunction, amount: int, reply_to: list = None) -> bool:
    state = ctx.get()
    if (state['stock'] + amount) < 0:  
        raise OutOfStock()
    state['stock'] += amount
    ctx.put(state)
    if reply_to:
        reply_info = reply_to.pop()
        ctx.call_remote_async(operator_name = reply_info["op_name"], function_name = reply_info["fun"], key = reply_info["id"], params = (reply_info["context"], True, reply_to))
        return
    else:
        return True

user_operator = Operator('user', n_partitions=4)


class NotEnoughBalance(Exception):
    pass


@user_operator.register
async def create(ctx: StatefulFunction, username: str, reply_to: list = None):
    state = ctx.get()
    state = {'username': username, 'balance': 0}
    ctx.put(state)
    if reply_to:
        reply_info = reply_to.pop()
        ctx.call_remote_async(operator_name = reply_info["op_name"], function_name = reply_info["fun"], key = reply_info["id"], params = (reply_info["context"], ctx.key, reply_to))
        return
    else:
        return ctx.key


@user_operator.register
async def buy_item(ctx: StatefulFunction, amount: int, item: Item, reply_to: list = None) -> bool:
    state = ctx.get()
    reply_to.append({'op_name': 'user', 'fun': 'buy_item_step_2', 'id': ctx.key, 'context': {'item': item, 'amount': amount}})
    ctx.call_remote_async(operator_name = 'item', function_name = 'get_price', key = item, params = reply_to)

@user_operator.register
async def buy_item_step_3(ctx: StatefulFunction, params, placeholder_return = None, reply_to: list = None):
    state = ctx.get()
    (amount, item, state, total_price) = (params['amount'], params['item'], params['state'], params['total_price'])
    state['balance'] -= total_price
    ctx.put(state)
    if reply_to:
        reply_info = reply_to.pop()
        ctx.call_remote_async(operator_name = reply_info["op_name"], function_name = reply_info["fun"], key = reply_info["id"], params = (reply_info["context"], True, reply_to))
        return
    else:
        return True

@user_operator.register
async def buy_item_step_2(ctx: StatefulFunction, params, attr_1 = None, reply_to: list = None):
    state = ctx.get()
    (amount, item) = (params['amount'], params['item'])
    total_price = amount * attr_1

    if state['balance'] < total_price:
        raise NotEnoughBalance()
    reply_to.append({'op_name': 'user', 'fun': 'buy_item_step_3', 'id': ctx.key, 'context': {'item': item, 'state': state, 'amount': amount, 'total_price': total_price}})
    ctx.call_remote_async(operator_name = 'item', function_name = 'update_stock', key = item, params = (-amount, reply_to))


@user_operator.register
async def temp(ctx: StatefulFunction, item: Item, reply_to: list = None) -> int:
    state = ctx.get()
    ctx.call_remote_async(operator_name = 'item', function_name = 'get_price', key = item, params = reply_to)

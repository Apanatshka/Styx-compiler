
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
    state = {'username': username, 'balance': 0, 'primary_item': None}
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
    reply_to.append({'op_name': 'user', 'fun': 'buy_item_step_2', 'id': ctx.key, 'context': {'amount': amount, 'item': item}})
    ctx.call_remote_async(operator_name = 'item', function_name = 'get_price', key = item, params = None)

@user_operator.register
async def buy_item_step_2(ctx: StatefulFunction, params, attr_1 = None, reply_to: list = None):
    state = ctx.get()
    amount = params['amount']
    item = params['item']
    total_price = amount * attr_1

    if state['balance'] < total_price:
        raise NotEnoughBalance()
    reply_to.append({'op_name': 'user', 'fun': 'buy_item_step_3', 'id': ctx.key, 'context': {'amount': amount, 'attr_1': attr_1, 'item': item, 'state': state, 'total_price': total_price}})
    ctx.call_remote_async(operator_name = 'item', function_name = 'update_stock', key = item, params = -amount)

@user_operator.register
async def buy_item_step_3(ctx: StatefulFunction, params, placeholder_return = None, reply_to: list = None):
    state = ctx.get()
    amount = params['amount']
    attr_1 = params['attr_1']
    item = params['item']
    state = params['state']
    total_price = params['total_price']
    state['balance'] -= total_price
    ctx.put(state)
    if reply_to:
        reply_info = reply_to.pop()
        ctx.call_remote_async(operator_name = reply_info["op_name"], function_name = reply_info["fun"], key = reply_info["id"], params = (reply_info["context"], True, reply_to))
        return
    else:
        return True


@user_operator.register
async def temp(ctx: StatefulFunction, item: Item, reply_to: list = None) -> int:
    state = ctx.get()
    reply_to.append({'op_name': 'user', 'fun': 'None', 'id': ctx.key, 'context': {'item': item}})
    ctx.call_remote_async(operator_name = 'item', function_name = 'get_price', key = item, params = None)


@user_operator.register
async def get_primary_item(ctx: StatefulFunction, reply_to: list = None):
    state = ctx.get()
    reply_to.append({'op_name': 'user', 'fun': 'get_primary_item_step_2', 'id': ctx.key, 'context': {}})
    ctx.call_remote_async(operator_name = 'item', function_name = 'get_price', key = state['primary_item'], params = None)

@user_operator.register
async def get_primary_item_step_2(ctx: StatefulFunction, params, attr_1 = None, reply_to: list = None):
    state = ctx.get()
    if reply_to:
        reply_info = reply_to.pop()
        ctx.call_remote_async(operator_name = reply_info["op_name"], function_name = reply_info["fun"], key = reply_info["id"], params = (reply_info["context"], attr_1, reply_to))
        return
    else:
        return attr_1

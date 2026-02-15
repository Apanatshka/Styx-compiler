from styx.common.operator import Operator
from styx.common.stateful_function import StatefulFunction

from typing import Dict


class NotEnoughStock(Exception):
    pass

class ItemDoesNotExist(Exception):
    pass

class OrderDoesNotExist(Exception):
    pass

class NotEnoughCredit(Exception):
    pass

class UserDoesNotExist(Exception):
    pass
item_operator = Operator('item', n_partitions=4)


@item_operator.register
async def create(ctx: StatefulFunction, price: int, reply_to: list = None):
    state = ctx.get()
    state = {'stock': 0, 'price': price}
    ctx.put(state)
    if reply_to:
        reply_info = reply_to.pop()
        ctx.call_remote_async(operator_name = reply_info["op_name"], function_name = reply_info["fun"], key = reply_info["id"], params = (reply_info["context"], ctx.key, reply_to))
        return
    else:
        return ctx.key


@item_operator.register
async def add_stock(ctx: StatefulFunction, amount: int, reply_to: list = None) -> int:
    state = ctx.get()
    state['stock'] += amount
    ctx.put(state)
    if reply_to:
        reply_info = reply_to.pop()
        ctx.call_remote_async(operator_name = reply_info["op_name"], function_name = reply_info["fun"], key = reply_info["id"], params = (reply_info["context"], state['stock'], reply_to))
        return
    else:
        return state['stock']


@item_operator.register
async def remove_stock(ctx: StatefulFunction, amount: int, reply_to: list = None):
    state = ctx.get()
    if state['stock'] - amount < 0:
        raise NotEnoughStock(f"Item does not have enough stock")
    state['stock'] -= amount
    ctx.put(state)


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
async def find(ctx: StatefulFunction, reply_to: list = None):
    state = ctx.get()
    if reply_to:
        reply_info = reply_to.pop()
        ctx.call_remote_async(operator_name = reply_info["op_name"], function_name = reply_info["fun"], key = reply_info["id"], params = (reply_info["context"], {"stock": state['stock'], "price": state['price']}, reply_to))
        return
    else:
        return {"stock": state['stock'], "price": state['price']}

user_operator = Operator('user', n_partitions=4)


@user_operator.register
async def create(ctx: StatefulFunction, reply_to: list = None):
    state = ctx.get()
    state = {'credit': 0}
    ctx.put(state)
    if reply_to:
        reply_info = reply_to.pop()
        ctx.call_remote_async(operator_name = reply_info["op_name"], function_name = reply_info["fun"], key = reply_info["id"], params = (reply_info["context"], ctx.key, reply_to))
        return
    else:
        return ctx.key


@user_operator.register
async def add_credit(ctx: StatefulFunction, amount: int, reply_to: list = None):
    state = ctx.get()
    state['credit'] += amount
    ctx.put(state)
    if reply_to:
        reply_info = reply_to.pop()
        ctx.call_remote_async(operator_name = reply_info["op_name"], function_name = reply_info["fun"], key = reply_info["id"], params = (reply_info["context"], {"credit": state['credit']}, reply_to))
        return
    else:
        return {"credit": state['credit']}


@user_operator.register
async def remove_credit(ctx: StatefulFunction, amount: int, reply_to: list = None):
    state = ctx.get()
    if state['credit'] - amount < 0:
        attr_1 = state['NotEnoughCredit'](f"User does not have enough credit")
        raise attr_1
    state['credit'] -= amount
    ctx.put(state)


@user_operator.register
async def find(ctx: StatefulFunction, reply_to: list = None):
    state = ctx.get()
    if reply_to:
        reply_info = reply_to.pop()
        ctx.call_remote_async(operator_name = reply_info["op_name"], function_name = reply_info["fun"], key = reply_info["id"], params = (reply_info["context"], {"credit": state['credit']}, reply_to))
        return
    else:
        return {"credit": state['credit']}

order_operator = Operator('order', n_partitions=4)



@order_operator.register
async def create(ctx: StatefulFunction, user: User, reply_to: list = None):
    state = ctx.get()
    state = {'paid': False, 'items': {}, 'user': user, 'total_cost': 0}
    ctx.put(state)
    if reply_to:
        reply_info = reply_to.pop()
        ctx.call_remote_async(operator_name = reply_info["op_name"], function_name = reply_info["fun"], key = reply_info["id"], params = (reply_info["context"], ctx.key, reply_to))
        return
    else:
        return ctx.key


@order_operator.register
async def add_item(ctx: StatefulFunction, item: Item, quantity: int, reply_to: list = None):
    state = ctx.get()
    reply_to.append({'op_name': 'order', 'fun': 'add_item_step_2', 'id': ctx.key, 'context': {'item': item, 'quantity': quantity}})
    ctx.call_remote_async(operator_name = 'item', function_name = 'get_price', key = item, params = reply_to)

@order_operator.register
async def add_item_step_2(ctx: StatefulFunction, params, price = None, reply_to: list = None):
    state = ctx.get()
    (item, quantity) = (params['item'], params['quantity'])

    if item in state['items']:
        state['items'][item] += quantity
    else:
        state['items'][item] = quantity
        
    state['total_cost'] += quantity * price
    ctx.put(state)
    if reply_to:
        reply_info = reply_to.pop()
        ctx.call_remote_async(operator_name = reply_info["op_name"], function_name = reply_info["fun"], key = reply_info["id"], params = (reply_info["context"], self, reply_to))
        return
    else:
        return self


@order_operator.register
async def checkout(ctx: StatefulFunction, reply_to: list = None) -> str:
    state = ctx.get()
    for item, quantity in state['items'].items():
        item.remove_stock(quantity)
    reply_to.append({'op_name': 'order', 'fun': 'checkout_step_2', 'id': ctx.key, 'context': {}})
    ctx.call_remote_async(operator_name = 'user', function_name = 'remove_credit', key = state['user'], params = (state['total_cost'], reply_to))

@order_operator.register
async def checkout_step_2(ctx: StatefulFunction, params, placeholder_return = None, reply_to: list = None):
    state = ctx.get()
    () = ()
    state['paid'] = True
    ctx.put(state)
    if reply_to:
        reply_info = reply_to.pop()
        ctx.call_remote_async(operator_name = reply_info["op_name"], function_name = reply_info["fun"], key = reply_info["id"], params = (reply_info["context"], "Payment successful", reply_to))
        return
    else:
        return "Payment successful"


@order_operator.register
async def find(ctx: StatefulFunction, reply_to: list = None):
    state = ctx.get()
    attr_1 = state['items'].items()
    readable_items = {f"Item(price={k.price})": v for k, v in attr_1}
    if reply_to:
        reply_info = reply_to.pop()
        ctx.call_remote_async(operator_name = reply_info["op_name"], function_name = reply_info["fun"], key = reply_info["id"], params = (reply_info["context"], {
            "paid": state['paid'],
            "items": readable_items,
            "user": state['user'],
            "total_cost": state['total_cost']
        }, reply_to))
        return
    else:
        return {
            "paid": state['paid'],
            "items": readable_items,
            "user": state['user'],
            "total_cost": state['total_cost']
        }

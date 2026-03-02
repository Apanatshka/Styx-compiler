from styx.common.operator import Operator
from styx.common.stateful_function import StatefulFunction
from styx.common.logging import logging

item_operator = Operator('item', n_partitions=4)

class OutOfStock(Exception):
    pass


@item_operator.register
async def create(ctx: StatefulFunction, item_name: str, price: int, reply_to: list = None):
    state = ctx.get()
    state = {'item_name': item_name, 'stock': 0, 'price': price}
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
async def get_stock(ctx: StatefulFunction, reply_to: list = None) -> int:
    state = ctx.get()
    if reply_to:
        reply_info = reply_to.pop()
        ctx.call_remote_async(operator_name = reply_info["op_name"], function_name = reply_info["fun"], key = reply_info["id"], params = (reply_info["context"], state['stock'], reply_to))
        return
    else:
        return state['stock']


@item_operator.register
async def update_stock(ctx: StatefulFunction, amount: int, reply_to: list = None) -> bool:
    state = ctx.get()
    if (state['stock'] + amount) < 0:
        raise OutOfStock("Not enough stock to update.")
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
    state = {'username': username, 'balance': 0, 'myitems': []}
    ctx.put(state)
    if reply_to:
        reply_info = reply_to.pop()
        ctx.call_remote_async(operator_name = reply_info["op_name"], function_name = reply_info["fun"], key = reply_info["id"], params = (reply_info["context"], ctx.key, reply_to))
        return
    else:
        return ctx.key


@user_operator.register
async def get_balance(ctx: StatefulFunction, reply_to: list = None) -> int:
    state = ctx.get()
    if reply_to:
        reply_info = reply_to.pop()
        ctx.call_remote_async(operator_name = reply_info["op_name"], function_name = reply_info["fun"], key = reply_info["id"], params = (reply_info["context"], state['balance'], reply_to))
        return
    else:
        return state['balance']


@user_operator.register
async def get_items(ctx: StatefulFunction, reply_to: list = None) -> list[str]:
    state = ctx.get()
    if reply_to:
        reply_info = reply_to.pop()
        ctx.call_remote_async(operator_name = reply_info["op_name"], function_name = reply_info["fun"], key = reply_info["id"], params = (reply_info["context"], state['myitems'], reply_to))
        return
    else:
        return state['myitems']


@user_operator.register
async def add_balance(ctx: StatefulFunction, amount: int, reply_to: list = None) -> bool:
    state = ctx.get()
    state['balance'] += amount
    ctx.put(state)
    if reply_to:
        reply_info = reply_to.pop()
        ctx.call_remote_async(operator_name = reply_info["op_name"], function_name = reply_info["fun"], key = reply_info["id"], params = (reply_info["context"], True, reply_to))
        return
    else:
        return True


@user_operator.register
async def buy_item(ctx: StatefulFunction, amount: int, item: str, reply_to: list = None) -> bool:
    state = ctx.get()
    if reply_to is None:
        reply_to = []
    reply_to.append({'op_name': 'user', 'fun': 'buy_item_step_2', 'id': ctx.key, 'context': {'amount': amount, 'item': item}})
    ctx.call_remote_async(operator_name = 'item', function_name = 'get_price', key = item, params = (reply_to,))

@user_operator.register
async def buy_item_step_2(ctx: StatefulFunction, params, attr_1 = None, reply_to: list = None):
    state = ctx.get()
    (amount, item) = (params['amount'], params['item'])
    total_price = amount * attr_1

    if state['balance'] < total_price:
        raise NotEnoughBalance("Not enough balance to buy the item.")
    if reply_to is None:
        reply_to = []
    reply_to.append({'op_name': 'user', 'fun': 'buy_item_step_3', 'id': ctx.key, 'context': {'total_price': total_price, 'attr_1': attr_1, 'amount': amount, 'item': item}})
    ctx.call_remote_async(operator_name = 'item', function_name = 'update_stock', key = item, params = (-amount, reply_to))

@user_operator.register
async def buy_item_step_3(ctx: StatefulFunction, params, placeholder_return = None, reply_to: list = None):
    state = ctx.get()
    (amount, attr_1, item, total_price) = (params['amount'], params['attr_1'], params['item'], params['total_price'])
    state['balance'] -= total_price
    state['myitems'].append(item)
    ctx.put(state)
    if reply_to:
        reply_info = reply_to.pop()
        ctx.call_remote_async(operator_name = reply_info["op_name"], function_name = reply_info["fun"], key = reply_info["id"], params = (reply_info["context"], True, reply_to))
        return
    else:
        return True


@user_operator.register
async def bulk_purchase_with_tiers(ctx: StatefulFunction, cart: list[str], quantities: list[int], reply_to: list = None) -> bool:
    state = ctx.get()
    total_cost = 0
    state['__loop_index_1'] = 0
    ctx.put(state)
    ctx.call_remote_async(operator_name = 'user', function_name = 'bulk_purchase_with_tiers_step_2', key = ctx.key, params = ({'cart': cart, 'quantities': quantities, 'total_cost': total_cost}, None, reply_to))
    ctx.put(state)

@user_operator.register
async def bulk_purchase_with_tiers_step_2(ctx: StatefulFunction, params, placeholder_return = None, reply_to: list = None):
    state = ctx.get()
    (cart, quantities, total_cost) = (params['cart'], params['quantities'], params['total_cost'])
    if state['__loop_index_1'] >= len(cart):
        state['balance'] -= total_cost
        ctx.put(state)
        if reply_to:
            reply_info = reply_to.pop()
            ctx.call_remote_async(operator_name = reply_info["op_name"], function_name = reply_info["fun"], key = reply_info["id"], params = (reply_info["context"], "Bulk purchase complete. Remaining balance: " + str(state['balance']), reply_to))
            return
        else:
            return "Bulk purchase complete. Remaining balance: " + str(state['balance'])
    else:
        index = state['__loop_index_1']
        state['__loop_index_1'] += 1
        item = cart[index]
        requested_amount = quantities[index]
        if reply_to is None:
            reply_to = []
        reply_to.append({'op_name': 'user', 'fun': 'bulk_purchase_with_tiers_step_3', 'id': ctx.key, 'context': {'total_cost': total_cost, 'cart': cart, 'item': item, 'quantities': quantities, 'index': index, 'requested_amount': requested_amount}})
        ctx.call_remote_async(operator_name = 'item', function_name = 'get_stock', key = item, params = (reply_to,))
    ctx.put(state)

@user_operator.register
async def bulk_purchase_with_tiers_step_3(ctx: StatefulFunction, params, attr_4 = None, reply_to: list = None):
    state = ctx.get()
    (cart, index, item, quantities, requested_amount, total_cost) = (params['cart'], params['index'], params['item'], params['quantities'], params['requested_amount'], params['total_cost'])

    # Condition 1: Check if item has enough stock
    if attr_4 >= requested_amount:
        current_item_cost = 0
        state['__loop_index_2'] = 1
        ctx.put(state)
        ctx.call_remote_async(operator_name = 'user', function_name = 'bulk_purchase_with_tiers_step_4', key = ctx.key, params = ({'attr_4': attr_4, 'cart': cart, 'current_item_cost': current_item_cost, 'index': index, 'item': item, 'quantities': quantities, 'requested_amount': requested_amount, 'total_cost': total_cost}, None, reply_to))
    else:
        logging.warning(f"Skipping {item} due to low stock.")
        ctx.put(state)
        ctx.call_remote_async(operator_name = 'user', function_name = 'bulk_purchase_with_tiers_step_2', key = ctx.key, params = ({'attr_4': attr_4, 'cart': cart, 'index': index, 'item': item, 'quantities': quantities, 'requested_amount': requested_amount, 'total_cost': total_cost}, None, reply_to))
    ctx.put(state)

@user_operator.register
async def bulk_purchase_with_tiers_step_4(ctx: StatefulFunction, params, placeholder_return = None, reply_to: list = None):
    state = ctx.get()
    (attr_4, cart, current_item_cost, index, item, quantities, requested_amount, total_cost) = (params['attr_4'], params['cart'], params['current_item_cost'], params['index'], params['item'], params['quantities'], params['requested_amount'], params['total_cost'])
    if state['__loop_index_2'] >= requested_amount + 1:

        # Condition 3: Check if adding this specific bulk breaks the bank
        if (total_cost + current_item_cost) > state['balance']:
            raise NotEnoughBalance("Cannot afford the entire cart.")
        if reply_to is None:
            reply_to = []
        reply_to.append({'op_name': 'user', 'fun': 'bulk_purchase_with_tiers_step_5', 'id': ctx.key, 'context': {'total_cost': total_cost, 'cart': cart, 'item': item, 'attr_4': attr_4, 'quantities': quantities, 'index': index, 'current_item_cost': current_item_cost, 'requested_amount': requested_amount}})
        ctx.call_remote_async(operator_name = 'item', function_name = 'update_stock', key = item, params = (-requested_amount, reply_to))
    else:
        unit = state['__loop_index_2']
        state['__loop_index_2'] += 1
        # Nested Conditionals: The more you buy, the cheaper the marginal unit gets
        if unit > 50:
            if reply_to is None:
                reply_to = []
            reply_to.append({'op_name': 'user', 'fun': 'bulk_purchase_with_tiers_step_6', 'id': ctx.key, 'context': {'total_cost': total_cost, 'cart': cart, 'item': item, 'attr_4': attr_4, 'quantities': quantities, 'index': index, 'current_item_cost': current_item_cost, 'unit': unit, 'requested_amount': requested_amount}})
            ctx.call_remote_async(operator_name = 'item', function_name = 'get_price', key = item, params = (reply_to,))
        elif unit > 10:
            if reply_to is None:
                reply_to = []
            reply_to.append({'op_name': 'user', 'fun': 'bulk_purchase_with_tiers_step_7', 'id': ctx.key, 'context': {'total_cost': total_cost, 'cart': cart, 'item': item, 'attr_4': attr_4, 'quantities': quantities, 'index': index, 'current_item_cost': current_item_cost, 'unit': unit, 'requested_amount': requested_amount}})
            ctx.call_remote_async(operator_name = 'item', function_name = 'get_price', key = item, params = (reply_to,))
        else:
            if reply_to is None:
                reply_to = []
            reply_to.append({'op_name': 'user', 'fun': 'bulk_purchase_with_tiers_step_8', 'id': ctx.key, 'context': {'total_cost': total_cost, 'cart': cart, 'item': item, 'attr_4': attr_4, 'quantities': quantities, 'index': index, 'current_item_cost': current_item_cost, 'unit': unit, 'requested_amount': requested_amount}})
            ctx.call_remote_async(operator_name = 'item', function_name = 'get_price', key = item, params = (reply_to,))
    ctx.put(state)

@user_operator.register
async def bulk_purchase_with_tiers_step_5(ctx: StatefulFunction, params, placeholder_return = None, reply_to: list = None):
    state = ctx.get()
    (attr_4, cart, current_item_cost, index, item, quantities, requested_amount, total_cost) = (params['attr_4'], params['cart'], params['current_item_cost'], params['index'], params['item'], params['quantities'], params['requested_amount'], params['total_cost'])
    total_cost = total_cost + current_item_cost

    # Another nested loop to populate user inventory
    for copy in range(requested_amount):
        state['myitems'].append(item)
    ctx.put(state)
    ctx.call_remote_async(operator_name = 'user', function_name = 'bulk_purchase_with_tiers_step_2', key = ctx.key, params = ({'attr_4': attr_4, 'cart': cart, 'current_item_cost': current_item_cost, 'index': index, 'item': item, 'quantities': quantities, 'requested_amount': requested_amount, 'total_cost': total_cost}, None, reply_to))

@user_operator.register
async def bulk_purchase_with_tiers_step_6(ctx: StatefulFunction, params, attr_1 = None, reply_to: list = None):
    state = ctx.get()
    (attr_4, cart, current_item_cost, index, item, quantities, requested_amount, total_cost, unit) = (params['attr_4'], params['cart'], params['current_item_cost'], params['index'], params['item'], params['quantities'], params['requested_amount'], params['total_cost'], params['unit'])
    current_item_cost = current_item_cost + int(attr_1 * 0.8)
    ctx.put(state)
    ctx.call_remote_async(operator_name = 'user', function_name = 'bulk_purchase_with_tiers_step_4', key = ctx.key, params = ({'attr_1': attr_1, 'attr_4': attr_4, 'cart': cart, 'current_item_cost': current_item_cost, 'index': index, 'item': item, 'quantities': quantities, 'requested_amount': requested_amount, 'total_cost': total_cost, 'unit': unit}, None, reply_to))

@user_operator.register
async def bulk_purchase_with_tiers_step_7(ctx: StatefulFunction, params, attr_2 = None, reply_to: list = None):
    state = ctx.get()
    (attr_4, cart, current_item_cost, index, item, quantities, requested_amount, total_cost, unit) = (params['attr_4'], params['cart'], params['current_item_cost'], params['index'], params['item'], params['quantities'], params['requested_amount'], params['total_cost'], params['unit'])
    current_item_cost = current_item_cost + int(attr_2 * 0.9)
    ctx.put(state)
    ctx.call_remote_async(operator_name = 'user', function_name = 'bulk_purchase_with_tiers_step_4', key = ctx.key, params = ({'attr_2': attr_2, 'attr_4': attr_4, 'cart': cart, 'current_item_cost': current_item_cost, 'index': index, 'item': item, 'quantities': quantities, 'requested_amount': requested_amount, 'total_cost': total_cost, 'unit': unit}, None, reply_to))

@user_operator.register
async def bulk_purchase_with_tiers_step_8(ctx: StatefulFunction, params, attr_3 = None, reply_to: list = None):
    state = ctx.get()
    (attr_4, cart, current_item_cost, index, item, quantities, requested_amount, total_cost, unit) = (params['attr_4'], params['cart'], params['current_item_cost'], params['index'], params['item'], params['quantities'], params['requested_amount'], params['total_cost'], params['unit'])
    current_item_cost = current_item_cost + attr_3
    ctx.put(state)
    ctx.call_remote_async(operator_name = 'user', function_name = 'bulk_purchase_with_tiers_step_4', key = ctx.key, params = ({'attr_3': attr_3, 'attr_4': attr_4, 'cart': cart, 'current_item_cost': current_item_cost, 'index': index, 'item': item, 'quantities': quantities, 'requested_amount': requested_amount, 'total_cost': total_cost, 'unit': unit}, None, reply_to))


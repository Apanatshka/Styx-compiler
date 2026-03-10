from styx.common.operator import Operator
from styx.common.stateful_function import StatefulFunction
from styx.common.logging import logging

class NotEnoughBalance(Exception):
    pass

class OutOfStock(Exception):
    pass
    
item_operator = Operator('item', n_partitions=4)


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
    reply_to.append({'op_name': 'user', 'fun': 'buy_item_step_2', 'id': ctx.key, 'context': {'item': item, 'amount': amount}})
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
    reply_to.append({'op_name': 'user', 'fun': 'buy_item_step_3', 'id': ctx.key, 'context': {'item': item, 'amount': amount, 'attr_1': attr_1, 'total_price': total_price}})
    ctx.call_remote_async(operator_name = 'item', function_name = 'update_stock', key = item, params = (-amount, reply_to))

@user_operator.register
async def buy_item_step_3(ctx: StatefulFunction, params, placeholder_return = None, reply_to: list = None):
    state = ctx.get()
    (amount, attr_1, item, total_price) = (params['amount'], params['attr_1'], params['item'], params['total_price'])
    state['balance'] -= total_price
    attr_3 = state['myitems']
    attr_3.append(item)
    ctx.put(state)
    if reply_to:
        reply_info = reply_to.pop()
        ctx.call_remote_async(operator_name = reply_info["op_name"], function_name = reply_info["fun"], key = reply_info["id"], params = (reply_info["context"], True, reply_to))
        return
    else:
        return True


@user_operator.register
async def bulk_purchase_with_tiers(ctx: StatefulFunction, cart: list[str], quantities: list[int], reply_to: list = None) -> str:
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
        reply_to.append({'op_name': 'user', 'fun': 'bulk_purchase_with_tiers_step_3', 'id': ctx.key, 'context': {'quantities': quantities, 'requested_amount': requested_amount, 'item': item, 'total_cost': total_cost, 'index': index, 'cart': cart}})
        ctx.call_remote_async(operator_name = 'item', function_name = 'get_stock', key = item, params = (reply_to,))
    ctx.put(state)

@user_operator.register
async def bulk_purchase_with_tiers_step_3(ctx: StatefulFunction, params, attr_8 = None, reply_to: list = None):
    state = ctx.get()
    (cart, index, item, quantities, requested_amount, total_cost) = (params['cart'], params['index'], params['item'], params['quantities'], params['requested_amount'], params['total_cost'])
    
    if attr_8 >= requested_amount:
        current_item_cost = 0
        state['__loop_index_2'] = 1
        ctx.put(state)
        ctx.call_remote_async(operator_name = 'user', function_name = 'bulk_purchase_with_tiers_step_4', key = ctx.key, params = ({'attr_8': attr_8, 'cart': cart, 'current_item_cost': current_item_cost, 'index': index, 'item': item, 'quantities': quantities, 'requested_amount': requested_amount, 'total_cost': total_cost}, None, reply_to))
    else:
        logging.warn(f"Skipping {item} due to low stock.")
        ctx.put(state)
        ctx.call_remote_async(operator_name = 'user', function_name = 'bulk_purchase_with_tiers_step_2', key = ctx.key, params = ({'attr_8': attr_8, 'cart': cart, 'index': index, 'item': item, 'quantities': quantities, 'requested_amount': requested_amount, 'total_cost': total_cost}, None, reply_to))
    ctx.put(state)

@user_operator.register
async def bulk_purchase_with_tiers_step_4(ctx: StatefulFunction, params, placeholder_return = None, reply_to: list = None):
    state = ctx.get()
    (attr_8, cart, current_item_cost, index, item, quantities, requested_amount, total_cost) = (params['attr_8'], params['cart'], params['current_item_cost'], params['index'], params['item'], params['quantities'], params['requested_amount'], params['total_cost'])
    if state['__loop_index_2'] >= requested_amount + 1:
        
        if (total_cost + current_item_cost) > state['balance']:
            raise NotEnoughBalance("Cannot afford the entire cart.")
            ctx.put(state)
            ctx.call_remote_async(operator_name = 'user', function_name = 'bulk_purchase_with_tiers_step_2', key = ctx.key, params = ({'attr_8': attr_8, 'cart': cart, 'current_item_cost': current_item_cost, 'index': index, 'item': item, 'quantities': quantities, 'requested_amount': requested_amount, 'total_cost': total_cost}, None, reply_to))
        else:
            if reply_to is None:
                reply_to = []
            reply_to.append({'op_name': 'user', 'fun': 'bulk_purchase_with_tiers_step_5', 'id': ctx.key, 'context': {'quantities': quantities, 'requested_amount': requested_amount, 'item': item, 'current_item_cost': current_item_cost, 'total_cost': total_cost, 'index': index, 'attr_8': attr_8, 'cart': cart}})
            ctx.call_remote_async(operator_name = 'item', function_name = 'update_stock', key = item, params = (-requested_amount, reply_to))
    else:
        unit = state['__loop_index_2']
        state['__loop_index_2'] += 1
        
        if unit > 50:
            if reply_to is None:
                reply_to = []
            reply_to.append({'op_name': 'user', 'fun': 'bulk_purchase_with_tiers_step_6', 'id': ctx.key, 'context': {'unit': unit, 'quantities': quantities, 'requested_amount': requested_amount, 'item': item, 'current_item_cost': current_item_cost, 'total_cost': total_cost, 'index': index, 'attr_8': attr_8, 'cart': cart}})
            ctx.call_remote_async(operator_name = 'item', function_name = 'get_price', key = item, params = (reply_to,))
        elif unit > 10:
            if reply_to is None:
                reply_to = []
            reply_to.append({'op_name': 'user', 'fun': 'bulk_purchase_with_tiers_step_7', 'id': ctx.key, 'context': {'unit': unit, 'quantities': quantities, 'requested_amount': requested_amount, 'item': item, 'current_item_cost': current_item_cost, 'total_cost': total_cost, 'index': index, 'attr_8': attr_8, 'cart': cart}})
            ctx.call_remote_async(operator_name = 'item', function_name = 'get_price', key = item, params = (reply_to,))
        else:
            if reply_to is None:
                reply_to = []
            reply_to.append({'op_name': 'user', 'fun': 'bulk_purchase_with_tiers_step_8', 'id': ctx.key, 'context': {'unit': unit, 'quantities': quantities, 'requested_amount': requested_amount, 'item': item, 'current_item_cost': current_item_cost, 'total_cost': total_cost, 'index': index, 'attr_8': attr_8, 'cart': cart}})
            ctx.call_remote_async(operator_name = 'item', function_name = 'get_price', key = item, params = (reply_to,))
    ctx.put(state)

@user_operator.register
async def bulk_purchase_with_tiers_step_5(ctx: StatefulFunction, params, placeholder_return = None, reply_to: list = None):
    state = ctx.get()
    (attr_8, cart, current_item_cost, index, item, quantities, requested_amount, total_cost) = (params['attr_8'], params['cart'], params['current_item_cost'], params['index'], params['item'], params['quantities'], params['requested_amount'], params['total_cost'])
    total_cost = total_cost + current_item_cost
    
    for copy in range(requested_amount):
        attr_5 = state['myitems']
        attr_5.append(item)
    ctx.put(state)
    ctx.call_remote_async(operator_name = 'user', function_name = 'bulk_purchase_with_tiers_step_2', key = ctx.key, params = ({'attr_5': attr_5, 'attr_8': attr_8, 'cart': cart, 'copy': copy, 'current_item_cost': current_item_cost, 'index': index, 'item': item, 'quantities': quantities, 'requested_amount': requested_amount, 'total_cost': total_cost}, None, reply_to))

@user_operator.register
async def bulk_purchase_with_tiers_step_6(ctx: StatefulFunction, params, attr_1 = None, reply_to: list = None):
    state = ctx.get()
    (attr_8, cart, current_item_cost, index, item, quantities, requested_amount, total_cost, unit) = (params['attr_8'], params['cart'], params['current_item_cost'], params['index'], params['item'], params['quantities'], params['requested_amount'], params['total_cost'], params['unit'])
    current_item_cost = current_item_cost + int(attr_1 * 0.8)
    ctx.put(state)
    ctx.call_remote_async(operator_name = 'user', function_name = 'bulk_purchase_with_tiers_step_4', key = ctx.key, params = ({'attr_1': attr_1, 'attr_8': attr_8, 'cart': cart, 'current_item_cost': current_item_cost, 'index': index, 'item': item, 'quantities': quantities, 'requested_amount': requested_amount, 'total_cost': total_cost, 'unit': unit}, None, reply_to))

@user_operator.register
async def bulk_purchase_with_tiers_step_7(ctx: StatefulFunction, params, attr_2 = None, reply_to: list = None):
    state = ctx.get()
    (attr_8, cart, current_item_cost, index, item, quantities, requested_amount, total_cost, unit) = (params['attr_8'], params['cart'], params['current_item_cost'], params['index'], params['item'], params['quantities'], params['requested_amount'], params['total_cost'], params['unit'])
    current_item_cost = current_item_cost + int(attr_2 * 0.9)
    ctx.put(state)
    ctx.call_remote_async(operator_name = 'user', function_name = 'bulk_purchase_with_tiers_step_4', key = ctx.key, params = ({'attr_2': attr_2, 'attr_8': attr_8, 'cart': cart, 'current_item_cost': current_item_cost, 'index': index, 'item': item, 'quantities': quantities, 'requested_amount': requested_amount, 'total_cost': total_cost, 'unit': unit}, None, reply_to))

@user_operator.register
async def bulk_purchase_with_tiers_step_8(ctx: StatefulFunction, params, attr_3 = None, reply_to: list = None):
    state = ctx.get()
    (attr_8, cart, current_item_cost, index, item, quantities, requested_amount, total_cost, unit) = (params['attr_8'], params['cart'], params['current_item_cost'], params['index'], params['item'], params['quantities'], params['requested_amount'], params['total_cost'], params['unit'])
    current_item_cost = current_item_cost + attr_3
    ctx.put(state)
    ctx.call_remote_async(operator_name = 'user', function_name = 'bulk_purchase_with_tiers_step_4', key = ctx.key, params = ({'attr_3': attr_3, 'attr_8': attr_8, 'cart': cart, 'current_item_cost': current_item_cost, 'index': index, 'item': item, 'quantities': quantities, 'requested_amount': requested_amount, 'total_cost': total_cost, 'unit': unit}, None, reply_to))



@user_operator.register
async def get_first_item(ctx: StatefulFunction, reply_to: list = None) -> str:
    state = ctx.get()
    if reply_to:
        reply_info = reply_to.pop()
        ctx.call_remote_async(operator_name = reply_info["op_name"], function_name = reply_info["fun"], key = reply_info["id"], params = (reply_info["context"], state['myitems'][0], reply_to))
        return
    else:
        return state['myitems'][0]



@user_operator.register
async def type_test(ctx: StatefulFunction, hard: list[list[dict[str, int]]], easy: list[list[str]], reply_to: list = None) -> str:
    state = ctx.get()
    temp = easy[0][0]
    if reply_to is None:
        reply_to = []
    reply_to.append({'op_name': 'user', 'fun': 'type_test_step_2', 'id': ctx.key, 'context': {'temp': temp, 'easy': easy, 'hard': hard}})
    ctx.call_remote_async(operator_name = 'item', function_name = 'get_stock', key = temp, params = (reply_to,))

@user_operator.register
async def type_test_step_2(ctx: StatefulFunction, params, placeholder_return = None, reply_to: list = None):
    state = ctx.get()
    (easy, hard, temp) = (params['easy'], params['hard'], params['temp'])
    attr_2 = hard[0][0]
    attr_3 = attr_2.keys()
    attr_4 = list(attr_3)[0]
    if reply_to is None:
        reply_to = []
    reply_to.append({'op_name': 'user', 'fun': 'type_test_step_3', 'id': ctx.key, 'context': {'attr_4': attr_4, 'hard': hard, 'temp': temp, 'attr_3': attr_3, 'attr_2': attr_2, 'easy': easy}})
    ctx.call_remote_async(operator_name = 'item', function_name = 'get_stock', key = attr_4, params = (reply_to,))

@user_operator.register
async def type_test_step_3(ctx: StatefulFunction, params, placeholder_return = None, reply_to: list = None):
    state = ctx.get()
    (attr_2, attr_3, attr_4, easy, hard, temp) = (params['attr_2'], params['attr_3'], params['attr_4'], params['easy'], params['hard'], params['temp'])
    temp3 = easy[0][0]
    if reply_to is None:
        reply_to = []
    reply_to.append({'op_name': 'user', 'fun': 'type_test_step_4', 'id': ctx.key, 'context': {'attr_4': attr_4, 'hard': hard, 'temp': temp, 'temp3': temp3, 'attr_3': attr_3, 'attr_2': attr_2, 'easy': easy}})
    ctx.call_remote_async(operator_name = 'item', function_name = 'get_stock', key = temp3, params = (reply_to,))

@user_operator.register
async def type_test_step_4(ctx: StatefulFunction, params, placeholder_return = None, reply_to: list = None):
    state = ctx.get()
    (attr_2, attr_3, attr_4, easy, hard, temp, temp3) = (params['attr_2'], params['attr_3'], params['attr_4'], params['easy'], params['hard'], params['temp'], params['temp3'])
    if reply_to is None:
        reply_to = []
    reply_to.append({'op_name': 'user', 'fun': 'type_test_step_5', 'id': ctx.key, 'context': {'attr_4': attr_4, 'hard': hard, 'temp': temp, 'temp3': temp3, 'attr_3': attr_3, 'attr_2': attr_2, 'easy': easy}})
    ctx.call_remote_async(operator_name = 'user', function_name = 'get_first_item', key = ctx.key, params = (reply_to,))

@user_operator.register
async def type_test_step_5(ctx: StatefulFunction, params, temp4 = None, reply_to: list = None):
    state = ctx.get()
    (attr_2, attr_3, attr_4, easy, hard, temp, temp3) = (params['attr_2'], params['attr_3'], params['attr_4'], params['easy'], params['hard'], params['temp'], params['temp3'])
    if reply_to is None:
        reply_to = []
    reply_to.append({'op_name': 'user', 'fun': 'type_test_step_6', 'id': ctx.key, 'context': {'attr_4': attr_4, 'hard': hard, 'temp': temp, 'temp3': temp3, 'temp4': temp4, 'attr_3': attr_3, 'attr_2': attr_2, 'easy': easy}})
    ctx.call_remote_async(operator_name = 'item', function_name = 'get_stock', key = temp4, params = (reply_to,))

@user_operator.register
async def type_test_step_6(ctx: StatefulFunction, params, placeholder_return = None, reply_to: list = None):
    state = ctx.get()
    (attr_2, attr_3, attr_4, easy, hard, temp, temp3, temp4) = (params['attr_2'], params['attr_3'], params['attr_4'], params['easy'], params['hard'], params['temp'], params['temp3'], params['temp4'])
    attr_9 = state['myitems'][0]
    if reply_to is None:
        reply_to = []
    reply_to.append({'op_name': 'user', 'fun': 'type_test_step_7', 'id': ctx.key, 'context': {'attr_4': attr_4, 'hard': hard, 'temp': temp, 'temp3': temp3, 'temp4': temp4, 'attr_3': attr_3, 'attr_9': attr_9, 'attr_2': attr_2, 'easy': easy}})
    ctx.call_remote_async(operator_name = 'item', function_name = 'get_stock', key = attr_9, params = (reply_to,))

@user_operator.register
async def type_test_step_7(ctx: StatefulFunction, params, stock_val = None, reply_to: list = None):
    state = ctx.get()
    (attr_2, attr_3, attr_4, attr_9, easy, hard, temp, temp3, temp4) = (params['attr_2'], params['attr_3'], params['attr_4'], params['attr_9'], params['easy'], params['hard'], params['temp'], params['temp3'], params['temp4'])
    something = Something()
    something.remote()
    lst = [state['myitems'][0], state['myitems'][1]]
    attr_12 = lst[0]
    if reply_to is None:
        reply_to = []
    reply_to.append({'op_name': 'user', 'fun': 'type_test_step_8', 'id': ctx.key, 'context': {'attr_4': attr_4, 'hard': hard, 'lst': lst, 'temp': temp, 'temp3': temp3, 'temp4': temp4, 'stock_val': stock_val, 'attr_3': attr_3, 'attr_9': attr_9, 'something': something, 'attr_2': attr_2, 'attr_12': attr_12, 'easy': easy}})
    ctx.call_remote_async(operator_name = 'item', function_name = 'get_stock', key = attr_12, params = (reply_to,))

@user_operator.register
async def type_test_step_8(ctx: StatefulFunction, params, stock = None, reply_to: list = None):
    state = ctx.get()
    (attr_12, attr_2, attr_3, attr_4, attr_9, easy, hard, lst, something, stock_val, temp, temp3, temp4) = (params['attr_12'], params['attr_2'], params['attr_3'], params['attr_4'], params['attr_9'], params['easy'], params['hard'], params['lst'], params['something'], params['stock_val'], params['temp'], params['temp3'], params['temp4'])
    something.remote()
    temp5 = state['myitems'][0]
    if reply_to is None:
        reply_to = []
    reply_to.append({'op_name': 'user', 'fun': 'type_test_step_9', 'id': ctx.key, 'context': {'attr_4': attr_4, 'temp5': temp5, 'hard': hard, 'stock': stock, 'lst': lst, 'temp': temp, 'temp3': temp3, 'temp4': temp4, 'stock_val': stock_val, 'attr_3': attr_3, 'attr_9': attr_9, 'something': something, 'attr_2': attr_2, 'attr_12': attr_12, 'easy': easy}})
    ctx.call_remote_async(operator_name = 'item', function_name = 'get_stock', key = temp5, params = (reply_to,))

@user_operator.register
async def type_test_step_9(ctx: StatefulFunction, params, placeholder_return = None, reply_to: list = None):
    state = ctx.get()
    (attr_12, attr_2, attr_3, attr_4, attr_9, easy, hard, lst, something, stock, stock_val, temp, temp3, temp4, temp5) = (params['attr_12'], params['attr_2'], params['attr_3'], params['attr_4'], params['attr_9'], params['easy'], params['hard'], params['lst'], params['something'], params['stock'], params['stock_val'], params['temp'], params['temp3'], params['temp4'], params['temp5'])
    if reply_to:
        reply_info = reply_to.pop()
        ctx.call_remote_async(operator_name = reply_info["op_name"], function_name = reply_info["fun"], key = reply_info["id"], params = (reply_info["context"], "hello", reply_to))
        return
    else:
        return "hello"



class Something:
    def __init__(self):
        self.value = 10

    def remote(self):
        self.value = 20

    def get_value(self) -> int:
        return self.value


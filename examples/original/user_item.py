class NotEnoughBalance(Exception):
    pass


class OutOfStock(Exception):
    pass


@entity
class Item:
    def __init__(self, item_name: str, price: int):
        self.item_name: str = item_name
        self.stock: int = 0
        self.price: int = price

    def get_price(self) -> int:
        return self.price

    def get_stock(self) -> int:
        return self.stock

    def update_stock(self, amount: int) -> bool:
        if (self.stock + amount) < 0:
            raise OutOfStock("Not enough stock to update.")

        self.stock += amount
        return True

    def __key__(self):
        return self.item_name


@entity
class User:
    def __init__(self, username: str):
        self.username: str = username
        self.balance: int = 0
        self.myitems: list[Item] = []

    def __key__(self):
        return self.username

    def get_balance(self) -> int:
        return self.balance

    def get_items(self) -> list[Item]:
        return self.myitems

    def add_balance(self, amount: int) -> bool:
        self.balance += amount
        return True

    def buy_item(self, amount: int, item: Item) -> bool:
        total_price = amount * item.get_price()

        if self.balance < total_price:
            raise NotEnoughBalance("Not enough balance to buy the item.")

        item.update_stock(-amount)

        self.balance -= total_price
        self.myitems.append(item)
        return True

    def bulk_purchase_with_tiers(self, cart: list[Item], quantities: list[int]) -> str:
        total_cost = 0

        for index in range(len(cart)):
            item = cart[index]
            requested_amount = quantities[index]

            if item.get_stock() >= requested_amount:
                current_item_cost = 0

                for unit in range(1, requested_amount + 1):
                    if unit > 50:
                        current_item_cost = current_item_cost + int(item.get_price() * 0.8)
                    elif unit > 10:
                        current_item_cost = current_item_cost + int(item.get_price() * 0.9)
                    else:
                        current_item_cost = current_item_cost + item.get_price()

                if (total_cost + current_item_cost) > self.balance:
                    raise NotEnoughBalance("Cannot afford the entire cart.")
                item.update_stock(-requested_amount)
                total_cost = total_cost + current_item_cost

                for copy in range(requested_amount):
                    self.myitems.append(item)
            else:
                logging.warning(f"Skipping {item} due to low stock.")

        self.balance -= total_cost

        return "Bulk purchase complete. Remaining balance: " + str(self.balance)

    def tempfunc(self):
        self.get_balance()

    def get_first_item(self) -> Item:
        self.myitems.append(Item("test", 10))

        return self.myitems[0]

    def type_test(self, hard: list[list[dict[Item, int]]], easy: list[list[Item]]) -> str:
        temp = easy[0][0]
        temp.get_stock()

        list(hard[0][0].keys())[0].get_stock()

        temp3 = easy[0][0]
        temp3.get_stock()

        temp4 = self.get_first_item()
        temp4.get_stock()

        stock_val = self.myitems[0].get_stock()

        something = Something()
        something.remote()

        lst = [self.myitems[0], self.myitems[1]]
        stock = lst[0].get_stock()

        something.remote()

        temp5 = self.myitems[0]
        temp5.get_stock()

        return "hello"


class Something:
    def __init__(self):
        self.value = 10

    def remote(self):
        self.value = 20

    def get_value(self) -> int:
        return self.value

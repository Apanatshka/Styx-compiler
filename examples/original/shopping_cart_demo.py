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

@entity
class Item:

    def __init__(self, price: int):
        self.stock: int = 0
        self.price: int = price

    def add_stock(self, amount: int) -> int:
        self.stock += amount
        return self.stock

    def remove_stock(self, amount: int):
        if self.stock - amount < 0:
            raise NotEnoughStock(f"Item does not have enough stock")
        self.stock -= amount

    def get_price(self) -> int:
        return self.price

    def find(self):
        return {"stock": self.stock, "price": self.price}


@entity
class User:

    def __init__(self):
        self.credit: int = 0

    def add_credit(self, amount: int):
        self.credit += amount
        return {"credit": self.credit}

    def remove_credit(self, amount: int):
        if self.credit - amount < 0:
            raise self.NotEnoughCredit(f"User does not have enough credit")
        self.credit -= amount

    def find(self):
        return {"credit": self.credit}


@entity
class Order:


    def __init__(self, user: User):
        self.paid: bool = False
        self.items: Dict[Item, int] = {}
        self.user: User = user           
        self.total_cost: int = 0

    def add_item(self, item: Item, quantity: int):
        
        price = item.get_price()

        if item in self.items:
            self.items[item] += quantity
        else:
            self.items[item] = quantity
            
        self.total_cost += quantity * price
        return self

    def checkout(self) -> str:
        for item, quantity in self.items.items():
            item.remove_stock(quantity)

        self.user.remove_credit(self.total_cost)

        self.paid = True
        return "Payment successful"

    def find(self):
        readable_items = {f"Item(price={k.price})": v for k, v in self.items.items()}
        return {
            "paid": self.paid,
            "items": readable_items,
            "user": self.user,
            "total_cost": self.total_cost
        }
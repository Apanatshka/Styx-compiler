
@entity
class Item:

    class OutOfStock(Exception):
        pass

    def __init__(self, item_name: str, price: int):
        self.item_name: str = item_name
        self.stock: int = 0
        self.price: int = price

    def get_price(self) -> int:
        return self.price

    def update_stock(self, amount: int) -> bool:
        if (self.stock + amount) < 0:  
            raise OutOfStock()
        
        self.stock += amount
        return True

    def __key__(self):
        return self.item_name

@entity
class User:

    class NotEnoughBalance(Exception):
        pass

    def __init__(self, username: str):
        self.username: str = username
        self.balance: int = 0

    def buy_item(self, amount: int, item: Item) -> bool:
        total_price = amount * item.get_price()

        if self.balance < total_price:
            raise NotEnoughBalance()
        

        item.update_stock(-amount)

        self.balance -= total_price
        return True
    
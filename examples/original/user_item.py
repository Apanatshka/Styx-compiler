
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
    
    def test_stack(self, user: User, amount: int) -> bool:
        user.buy_item(amount, self)
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

    def create_user_item(self, name: str, price: int) -> Item:
        new_item = Item(self.username + "_" + name, price)

        new_item.update_stock(1)

        return new_item

    def test_loop(self, amount, item: Item) -> bool:

        for i in range(amount):
            item.update_stock(-1)
            
        return True

    def process_inventory(self, budget: int, items: list[Item]) -> str:
        total_spent = 0
        logging.warn(f"Processing inventory for user {self.username} with budget {budget}")

        for item in items:
            logging.warn(f"Evaluating item {item}")
            price = item.get_price()
            logging.warn(f"Item price: {price}")
            stock = item.get_stock()
            logging.warn(f"Item stock: {stock}")

            if price < budget:
                if stock > 10:
                    item.update_stock(-5)
                elif stock > 0:
                    item.update_stock(-1)
            else:
                item.update_stock(1)

            total_spent = total_spent + price
            logging.warn(f"Total spent so far: {total_spent}")

            self.myitems.append(item)

        self.balance -= total_spent
        return "New balance: " + str(self.balance)

    def nested_loop_test(self, amount: int, items: list[Item]) -> str:
        total = 0

        for item in items:
            price = item.get_price()

            for i in range(amount):
                item.update_stock(-1)

            total = total + price

        self.balance -= total
        return "New balance: " + str(self.balance)

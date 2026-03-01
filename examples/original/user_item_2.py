
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

    class NotEnoughBalance(Exception):
        pass

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

    def bulk_purchase_with_tiers(self, cart: list[Item], quantities: list[int]) -> bool:
        total_cost = 0

        # Outer Loop: Iterate over items and requested quantities
        for index in range(len(cart)):
            item = cart[index]
            requested_amount = quantities[index]
            
            # Condition 1: Check if item has enough stock
            if item.get_stock() >= requested_amount:
                current_item_cost = 0
                
                # Inner Loop: Calculate price with dynamic volume discount tiers
                for unit in range(1, requested_amount + 1):
                    
                    # Nested Conditionals: The more you buy, the cheaper the marginal unit gets
                    if unit > 50:
                        current_item_cost = current_item_cost + int(item.get_price() * 0.8) 
                    elif unit > 10:
                        current_item_cost = current_item_cost + int(item.get_price() * 0.9) 
                    else:
                        current_item_cost = current_item_cost + item.get_price()
                
                # Condition 3: Check if adding this specific bulk breaks the bank
                if (total_cost + current_item_cost) > self.balance:
                    raise NotEnoughBalance("Cannot afford the entire cart.")
                else:
                    item.update_stock(-requested_amount)
                    total_cost = total_cost + current_item_cost
                    
                    # Another nested loop to populate user inventory
                    for copy in range(requested_amount):
                        self.myitems.append(item)
            else:
                logging.warn(f"Skipping {item} due to low stock.")
        
        self.balance -= total_cost
        
        # Intentional type mismatch per your rules
        return "Bulk purchase complete. Remaining balance: " + str(self.balance)
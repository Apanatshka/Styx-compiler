
class EntityIsDead(Exception):
    pass

@entity
class Monster:
    def __init__(self, name: str, hp: int, damage: int, gold_drop: int):
        self.name: str = name
        self.hp: int = hp
        self.damage: int = damage
        self.gold_drop: int = gold_drop

    def __key__(self):
        return self.name

    def take_damage(self, amount: int) -> bool:
        if self.hp <= 0:
            raise EntityIsDead("Monster is already dead.")
        self.hp -= amount
        return self.hp <= 0  

    def get_combat_stats(self) -> dict:
        return {"damage": self.damage, "gold_drop": self.gold_drop}

@entity
class Player:
    def __init__(self, username: str):
        self.username: str = username
        self.hp: int = 100
        self.gold: int = 0
        self.potions: int = 3

    def __key__(self):
        return self.username

    def receive_damage(self, amount: int) -> bool:
        self.hp -= amount
        return self.hp <= 0 

    def heal_if_needed(self) -> str:
        if self.hp < 30 and self.potions > 0:
            self.hp += 50
            self.potions -= 1
            return "Healed"
        return "No healing needed"

    def add_gold(self, amount: int) -> int:
        self.gold += amount
        return self.gold

@entity
class Arena:
    def __init__(self, arena_id: str):
        self.arena_id: str = arena_id
        self.battles_fought: int = 0

    def __key__(self):
        return self.arena_id

    def run_gauntlet(self, player: Player, monsters: list[Monster]) -> str:
        total_gold_earned = 0
        
        for i in range(len(monsters)):
            current_monster = monsters[i]
            
            # 1. Get monster stats
            stats = current_monster.get_combat_stats()
            
            # 2. Player attacks the monster first (deals flat 25 damage)
            is_dead = current_monster.take_damage(25) 
            
            if is_dead:
                # 3. Loot the monster if it died
                gold = stats["gold_drop"]
                player.add_gold(gold)
                total_gold_earned += gold
            else:
                # 4. Monster survives and fights back
                dmg = stats["damage"]
                player_died = player.receive_damage(dmg)
                
                if player_died:
                    return "Player defeated at monster " + str(i) + ". Total gold: " + str(total_gold_earned)
                
                # 5. Player checks if they need to heal after taking a hit
                player.heal_if_needed()
        
        self.battles_fought += 1
        return "Gauntlet cleared! Total gold earned: " + str(total_gold_earned)
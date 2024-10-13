import random
import discord
from discord import app_commands
from discord.ext import commands

class dnd_cog(commands.Cog):
    def __init__(self, client):
        self.client = client

    @app_commands.command(name="roll", description="Roll multiple dice at once")
    @app_commands.describe(dice="Dice to roll (e.g., 2d6 1d20)")
    async def roll_dice(self, interaction: discord.Interaction, dice: str):
        dice_list = dice.split()
        results = []
        total = 0

        for die in dice_list:
            try:
                num_dice, die_size = map(int, die.lower().split('d'))
                rolls = [random.randint(1, die_size) for _ in range(num_dice)]
                result_sum = sum(rolls)
                total += result_sum
                results.append(f"{die}: {rolls} (Sum: {result_sum})")
            except ValueError:
                results.append(f"Invalid input: {die}")

        response = "\n".join(results)
        if len(dice_list) > 1:
            response += f"\nTotal: {total}"
        await interaction.response.send_message(response)

    @app_commands.command(name="generate_stats", description="Generate character stats using various methods")
    @app_commands.describe(method="Stat generation method: standard, 4d6, or points_buy")
    async def generate_stats(self, interaction: discord.Interaction, method: str = "standard"):
        abilities = ["Strength", "Dexterity", "Constitution", "Intelligence", "Wisdom", "Charisma"]
        stats = {}

        if method == "standard":
            stats = {ability: random.choice([15, 14, 13, 12, 10, 8]) for ability in abilities}
        elif method == "4d6":
            for ability in abilities:
                rolls = sorted([random.randint(1, 6) for _ in range(4)], reverse=True)
                stats[ability] = sum(rolls[:3])
        elif method == "points_buy":
            point_costs = {8: 0, 9: 1, 10: 2, 11: 3, 12: 4, 13: 5, 14: 7, 15: 9}
            total_points = 27
            for ability in abilities:
                available_scores = [score for score, cost in point_costs.items() if cost <= total_points]
                score = random.choice(available_scores)
                stats[ability] = score
                total_points -= point_costs[score]
        else:
            await interaction.response.send_message("Invalid method. Use 'standard', '4d6', or 'points_buy'.")
            return

        response = "Generated stats:\n" + "\n".join(f"{ability}: {score}" for ability, score in stats.items())
        await interaction.response.send_message(response)

    @app_commands.command(name="coinflip", description="Flip a coin")
    async def coinflip(self, interaction: discord.Interaction):
        result = random.choice(["Heads", "Tails"])
        await interaction.response.send_message(f"Coinflip result: {result}")

    @app_commands.command(name="initiative", description="Roll initiative for combat")
    @app_commands.describe(modifier="Modifier to add to the roll")
    async def roll_initiative(self, interaction: discord.Interaction, modifier: int = 0):
        roll = random.randint(1, 20)
        total = roll + modifier
        await interaction.response.send_message(f"Initiative roll: {roll} + {modifier} = {total}")

    @app_commands.command(name="weather", description="Generate random weather conditions")
    async def generate_weather(self, interaction: discord.Interaction):
        conditions = ["Clear", "Partly cloudy", "Overcast", "Light rain", "Heavy rain", "Thunderstorm", "Snowing", "Foggy"]
        temperatures = ["Cold", "Cool", "Mild", "Warm", "Hot"]
        wind = ["Calm", "Light breeze", "Windy", "Strong winds"]

        weather = f"{random.choice(conditions)}, {random.choice(temperatures)}, {random.choice(wind)}"
        await interaction.response.send_message(f"Current weather: {weather}")

    @app_commands.command(name="generate_character", description="Generate a random D&D character")
    async def generate_character(self, interaction: discord.Interaction):
        races = ["Human", "Elf", "Dwarf", "Halfling", "Gnome", "Half-Elf", "Half-Orc", "Tiefling"]
        classes = ["Barbarian", "Bard", "Cleric", "Druid", "Fighter", "Monk", "Paladin", "Ranger", "Rogue", "Sorcerer", "Warlock", "Wizard"]
        backgrounds = ["Acolyte", "Charlatan", "Criminal", "Entertainer", "Folk Hero", "Guild Artisan", "Hermit", "Noble", "Outlander", "Sage", "Sailor", "Soldier", "Urchin"]
        alignments = ["Lawful Good", "Neutral Good", "Chaotic Good", "Lawful Neutral", "True Neutral", "Chaotic Neutral", "Lawful Evil", "Neutral Evil", "Chaotic Evil"]

        character = {
            "Race": random.choice(races),
            "Class": random.choice(classes),
            "Background": random.choice(backgrounds),
            "Alignment": random.choice(alignments),
        }

        # Generate stats using 4d6 method
        abilities = ["Strength", "Dexterity", "Constitution", "Intelligence", "Wisdom", "Charisma"]
        stats = {}
        for ability in abilities:
            rolls = sorted([random.randint(1, 6) for _ in range(4)], reverse=True)
            stats[ability] = sum(rolls[:3])

        character_sheet = (
            f"Race: {character['Race']}\n"
            f"Class: {character['Class']}\n"
            f"Background: {character['Background']}\n"
            f"Alignment: {character['Alignment']}\n\n"
            f"Abilities:\n"
            + "\n".join(f"  {ability}: {score}" for ability, score in stats.items())
        )

        await interaction.response.send_message(f"Generated Character:\n{character_sheet}")

    @app_commands.command(name="loot", description="Generate random loot")
    @app_commands.describe(rarity="Rarity of the loot: common, uncommon, rare, very_rare, or legendary")
    async def generate_loot(self, interaction: discord.Interaction, rarity: str = "common"):
        loot_tables = {
            "common": ["Potion of Healing", "Scroll of Identify", "10 gold pieces", "A silver ring"],
            "uncommon": ["Bag of Holding", "Boots of Elvenkind", "Cloak of Protection", "Wand of Magic Missiles"],
            "rare": ["Flame Tongue Sword", "Ring of Regeneration", "Staff of the Woodlands", "Wings of Flying"],
            "very_rare": ["Ammunition +3", "Cloak of Invisibility", "Rod of Absorption", "Tome of Clear Thought"],
            "legendary": ["Deck of Many Things", "Holy Avenger", "Ring of Djinni Summoning", "Vorpal Sword"]
        }

        if rarity not in loot_tables:
            await interaction.response.send_message("Invalid rarity. Choose from: common, uncommon, rare, very_rare, or legendary.")
            return

        loot = random.choice(loot_tables[rarity])
        await interaction.response.send_message(f"You found: {loot} (Rarity: {rarity.capitalize()})")

async def setup(client):
    await client.add_cog(DnDCog(client))
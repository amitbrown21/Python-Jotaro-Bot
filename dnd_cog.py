import random
from typing import List, Tuple

import discord
from discord import app_commands
from discord.ext import commands


def roll_dice_expr(dice: str) -> Tuple[List[str], int]:
    """Parse space-separated NdM tokens. Returns (lines, total of valid rolls)."""
    results: List[str] = []
    total = 0

    for die in dice.split():
        try:
            num_dice, die_size = map(int, die.lower().split('d'))
            if num_dice < 1 or die_size < 1:
                raise ValueError
            rolls = [random.randint(1, die_size) for _ in range(num_dice)]
            result_sum = sum(rolls)
            total += result_sum
            results.append(f"{die}: {rolls} (Sum: {result_sum})")
        except ValueError:
            results.append(f"Invalid input: {die}")

    return results, total


class dnd_cog(commands.Cog):
    def __init__(self, client):
        self.client = client

    @app_commands.command(name="roll", description="Roll multiple dice at once")
    @app_commands.describe(dice="Dice to roll (e.g., 2d6 1d20)")
    async def roll_dice(self, interaction: discord.Interaction, dice: str):
        results, total = roll_dice_expr(dice)
        response = "\n".join(results)
        if len(dice.split()) > 1:
            response += f"\nTotal: {total}"
        await interaction.response.send_message(response)

    @app_commands.command(name="generate_stats", description="Generate character stats using various methods")
    @app_commands.describe(method="Stat generation method")
    @app_commands.choices(method=[
        app_commands.Choice(name="standard", value="standard"),
        app_commands.Choice(name="4d6", value="4d6"),
        app_commands.Choice(name="points_buy", value="points_buy"),
    ])
    async def generate_stats(
        self,
        interaction: discord.Interaction,
        method: app_commands.Choice[str] = None,
    ):
        method_value = method.value if method else "standard"
        abilities = ["Strength", "Dexterity", "Constitution", "Intelligence", "Wisdom", "Charisma"]
        stats = {}

        if method_value == "standard":
            stats = {ability: random.choice([15, 14, 13, 12, 10, 8]) for ability in abilities}
        elif method_value == "4d6":
            for ability in abilities:
                rolls = sorted([random.randint(1, 6) for _ in range(4)], reverse=True)
                stats[ability] = sum(rolls[:3])
        else:
            point_costs = {8: 0, 9: 1, 10: 2, 11: 3, 12: 4, 13: 5, 14: 7, 15: 9}
            total_points = 27
            for ability in abilities:
                available_scores = [score for score, cost in point_costs.items() if cost <= total_points]
                score = random.choice(available_scores)
                stats[ability] = score
                total_points -= point_costs[score]

        response = "Generated stats:\n" + "\n".join(
            f"{ability}: {score}" for ability, score in stats.items()
        )
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
        conditions = [
            "Clear", "Partly cloudy", "Overcast", "Light rain",
            "Heavy rain", "Thunderstorm", "Snowing", "Foggy",
        ]
        temperatures = ["Cold", "Cool", "Mild", "Warm", "Hot"]
        wind = ["Calm", "Light breeze", "Windy", "Strong winds"]

        weather = f"{random.choice(conditions)}, {random.choice(temperatures)}, {random.choice(wind)}"
        await interaction.response.send_message(f"Current weather: {weather}")

    @app_commands.command(name="generate_character", description="Generate a random D&D character")
    async def generate_character(self, interaction: discord.Interaction):
        races = ["Human", "Elf", "Dwarf", "Halfling", "Gnome", "Half-Elf", "Half-Orc", "Tiefling"]
        classes = [
            "Barbarian", "Bard", "Cleric", "Druid", "Fighter", "Monk",
            "Paladin", "Ranger", "Rogue", "Sorcerer", "Warlock", "Wizard",
        ]
        backgrounds = [
            "Acolyte", "Charlatan", "Criminal", "Entertainer", "Folk Hero",
            "Guild Artisan", "Hermit", "Noble", "Outlander", "Sage", "Sailor",
            "Soldier", "Urchin",
        ]
        alignments = [
            "Lawful Good", "Neutral Good", "Chaotic Good", "Lawful Neutral",
            "True Neutral", "Chaotic Neutral", "Lawful Evil", "Neutral Evil",
            "Chaotic Evil",
        ]

        character = {
            "Race": random.choice(races),
            "Class": random.choice(classes),
            "Background": random.choice(backgrounds),
            "Alignment": random.choice(alignments),
        }

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
    @app_commands.describe(rarity="Rarity of the loot")
    @app_commands.choices(rarity=[
        app_commands.Choice(name="common", value="common"),
        app_commands.Choice(name="uncommon", value="uncommon"),
        app_commands.Choice(name="rare", value="rare"),
        app_commands.Choice(name="very_rare", value="very_rare"),
        app_commands.Choice(name="legendary", value="legendary"),
    ])
    async def generate_loot(
        self,
        interaction: discord.Interaction,
        rarity: app_commands.Choice[str] = None,
    ):
        rarity_value = rarity.value if rarity else "common"
        loot_tables = {
            "common": ["Potion of Healing", "Scroll of Identify", "10 gold pieces", "A silver ring"],
            "uncommon": ["Bag of Holding", "Boots of Elvenkind", "Cloak of Protection", "Wand of Magic Missiles"],
            "rare": ["Flame Tongue Sword", "Ring of Regeneration", "Staff of the Woodlands", "Wings of Flying"],
            "very_rare": ["Ammunition +3", "Cloak of Invisibility", "Rod of Absorption", "Tome of Clear Thought"],
            "legendary": ["Deck of Many Things", "Holy Avenger", "Ring of Djinni Summoning", "Vorpal Sword"],
        }

        loot = random.choice(loot_tables[rarity_value])
        await interaction.response.send_message(
            f"You found: {loot} (Rarity: {rarity_value.replace('_', ' ').title()})"
        )


async def setup(client):
    await client.add_cog(dnd_cog(client))

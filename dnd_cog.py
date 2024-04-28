import random

import discord
from discord import app_commands
from discord.ext import commands


class dnd_cog(commands.Cog):
    def __init__(self, client):
        self.client = client

    @app_commands.command(name="roll", description="Roll multiple dice at once")
    async def roll_dice(self, interaction: discord.Interaction, dice: str):
        dice_list = dice.split()  # Split the input string into a list of dice values
        results = []

        for die in dice_list:
            try:
                dienum = int(die)
                result = random.randint(1, dienum)
                results.append(f"d{dienum}: {result}")
            except ValueError:
                results.append(f"Invalid input: {die}")

        await interaction.response.send_message("\n".join(results))

    @app_commands.command(name="generate_stats", description="Generate character stats")
    async def generate_stats(self, interaction: discord.Interaction):
        stats = [random.randint(1, 6) for _ in range(4)]  # Roll 4 six-sided dice for each stat
        stats.sort(reverse=True)  # Sort in descending order
        total_stats = sum(stats[:3])  # Sum the highest 3 rolls
        await interaction.response.send_message(f"Generated stats: {', '.join(map(str, stats))}\nTotal: {total_stats}")

    @app_commands.command(name="coinflip", description="Flip a coin")
    async def coinflip(self, interaction: discord.Interaction):
        result = random.choice(["Heads", "Tails"])
        await interaction.response.send_message(f"Coinflip result: {result}")

    @app_commands.command(name="initiative", description="Roll initiative for combat")
    async def roll_initiative(self, interaction: discord.Interaction):
        result = random.randint(1, 20)  # Roll a 20-sided die for initiative
        await interaction.response.send_message(f"Initiative roll: {result}")

    @app_commands.command(name="weather", description="Generate random weather conditions")
    async def generate_weather(self, interaction: discord.Interaction):
        weather_conditions = random.choice(["Clear skies", "Rainy", "Snowy", "Foggy"])
        await interaction.response.send_message(f"Current weather: {weather_conditions}")

    @app_commands.command(name="generate_character", description="Generate a random D&D character")
    async def generate_character(self, interaction: discord.Interaction):
        races = ["Human", "Elf", "Dwarf", "Halfling", "Gnome"]
        classes = ["Fighter", "Wizard", "Cleric", "Rogue", "Barbarian", "Druid", "Warlock", "Sorcerer", "Monk"]
        backgrounds = ["Acolyte", "Sage", "Criminal", "Folk Hero", "Noble"]
        abilities = ["Strength", "Dexterity", "Constitution", "Intelligence", "Wisdom", "Charisma"]

        # Randomly select race, class, background
        random_race = random.choice(races)
        random_class = random.choice(classes)
        random_background = random.choice(backgrounds)

        # Roll abilities
        ability_scores = {ability: random.randint(8, 15) for ability in abilities}

        # Construct character sheet
        character_sheet = (
            f"Race: {random_race}\n"
            f"Class: {random_class}\n"
            f"Background: {random_background}\n\n"
            f"Abilities:\n"
            f"  {abilities[0]}: {ability_scores[abilities[0]]}\n"
            f"  {abilities[1]}: {ability_scores[abilities[1]]}\n"
            f"  {abilities[2]}: {ability_scores[abilities[2]]}\n"
            f"  {abilities[3]}: {ability_scores[abilities[3]]}\n"
            f"  {abilities[4]}: {ability_scores[abilities[4]]}\n"
            f"  {abilities[5]}: {ability_scores[abilities[5]]}\n"
        )

        await interaction.response.send_message(f"Generated Character:\n{character_sheet}")

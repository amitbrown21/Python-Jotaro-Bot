import discord
import asyncio
import sys
from discord.ext import commands
from config import TOKEN, prefix, guild_ids
from admin_cog import admin_cog
from dnd_cog import dnd_cog
from music_cog import music_cog

# Configure intents
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

# Create bot instance
client = commands.Bot(command_prefix=prefix, intents=intents)

# Windows-specific event loop policy
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@client.command(pass_context=True)
async def set_prefix(ctx, new_prefix: str):
    """Change the bot's command prefix."""
    client.command_prefix = new_prefix
    await ctx.send(f"Prefix is set to `{new_prefix}`, Yorokobe")


@client.command(pass_context=True)
async def sync(ctx):
    """Manually sync slash commands to guilds."""
    try:
        synced_count = 0
        for guild_id in guild_ids:
            synced = await client.tree.sync(guild=discord.Object(id=guild_id))
            synced_count += len(synced)
        await ctx.send(f"Synced {synced_count} commands")
    except discord.errors.Forbidden as e:
        print(f"Error syncing: {e}")
        await ctx.send("Error syncing commands - check permissions")


@client.event
async def on_ready():
    """Called when the bot is ready."""
    print(f"Logged in as {client.user.name} ({client.user.id})")
    
    # Set bot presence
    await client.change_presence(status=discord.Status.do_not_disturb)
    
    # Load cogs with error handling
    cogs = [
        ('music_cog', music_cog),
        ('admin_cog', admin_cog),
        ('dnd_cog', dnd_cog),
    ]
    
    for cog_name, cog_class in cogs:
        try:
            await client.add_cog(cog_class(client))
            print(f"Loaded cog: {cog_name}")
        except Exception as e:
            print(f"Failed to load {cog_name}: {e}")
    
    # Sync commands to guilds
    try:
        # Sync to specific guilds first (faster)
        for guild_id in guild_ids:
            await client.tree.sync(guild=discord.Object(id=guild_id))
        
        # Then sync globally
        await client.tree.sync()
        
        # Copy global commands to guilds
        for guild_id in guild_ids:
            client.tree.copy_global_to(guild=discord.Object(id=guild_id))
        
        print(f"Synced commands to {len(guild_ids)} guilds")
    except discord.errors.Forbidden as e:
        print(f"Error syncing commands: {e}")
    
    print('Yare Yare Daze...')
    print("----------------------------------------")


if __name__ == "__main__":
    client.run(TOKEN)

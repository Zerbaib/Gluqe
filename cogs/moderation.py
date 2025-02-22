import discord, asyncio, json, re, yaml, os, pickle, aiohttp, aiofiles
from discord.ext import commands, tasks
from datetime import datetime, timedelta
from helpers.checks import is_blacklisted, is_owner, load_blacklist

# Load configuration data from a YAML file
def load_config():
    with open('config.yml', 'r') as f:
        return yaml.safe_load(f)

def remove_words(text, words):
    if not words:
        return text
    pattern = re.compile(r'\b(' + '|'.join(map(re.escape, words)) + r')\b', flags=re.IGNORECASE)
    return pattern.sub('', text)

class Moderation(commands.Cog):
    def __init__(self, bot, threshold: float = 0.5, remove_list=None, log_channel_id: int = None):
        self.bot = bot
        self.threshold = threshold
        self.remove_list = remove_list if remove_list is not None else []
        config = load_config()
        self.log_channel_id = config['channels']['moderation_log']

        self.vectorizer = None  # Initialize to None
        self.classifier = None  # Initialize to None
        self.load_model_task = asyncio.create_task(self.load_model())  # Create a task

    async def load_model(self):
        base_dir = os.path.join(os.path.dirname(__file__), "..", "data")
        vectorizer_path = os.path.join(base_dir, "moderation_vectorizer.pkl")
        classifier_path = os.path.join(base_dir, "moderation_classifier.pkl")

        # Download the files if they don't exist
        files_to_download = [
            {"url": "https://github.com/Zluqe/Gluqe/raw/refs/heads/main/data/moderation_classifier.pkl", "filename": "moderation_classifier.pkl"},
            {"url": "https://github.com/Zluqe/Gluqe/raw/refs/heads/main/data/moderation_vectorizer.pkl", "filename": "moderation_vectorizer.pkl"},
        ]

        async with aiohttp.ClientSession() as session:
            for file_info in files_to_download:
                url = file_info["url"]
                filename = file_info["filename"]
                filepath = os.path.join(base_dir, filename)

                if not os.path.exists(filepath):
                    try:
                        async with session.get(url) as response:
                            if response.status != 200:
                                raise Exception(f"Error downloading {filename}: status code {response.status}")
                            async with aiofiles.open(filepath, "wb") as f:
                                while True:
                                    chunk = await response.content.read(8192)
                                    if not chunk:
                                        break
                                    await f.write(chunk)
                        print(f"Downloaded {filename} to {filepath}")
                    except Exception as e:
                        print(f"Error downloading {filename}: {e}")
                        return

        try:
            with open(vectorizer_path, "rb") as vf:
                self.vectorizer = pickle.load(vf)
            with open(classifier_path, "rb") as cf:
                self.classifier = pickle.load(cf)
            print("Moderation model loaded successfully.")
        except Exception as e:
            print("Error loading moderation model:", e)
            self.vectorizer = None
            self.classifier = None

    @commands.Cog.listener()
    async def on_ready(self):
        await self.load_model_task

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author == self.bot.user:
            return

        # Check for prohibited file extensions in attachments
        prohibited_extensions = ['.exe', '.bat', '.msi', '.vbs', '.sh', '.cmd']
        if any(attachment.filename.lower().endswith(tuple(prohibited_extensions)) for attachment in message.attachments):
            warning_msg = (f"{message.author.mention}, your message was deleted because it contained an attachment "
                           "with a prohibited file extension.")
            await message.delete()
            await message.channel.send(warning_msg, delete_after=15)
            return

        # Check for IP addresses (IPv4 format)
        ip_pattern = r'\b(?:\d{1,3}\.){3}\d{1,3}\b'
        if re.search(ip_pattern, message.content):
            warning_msg = (f"{message.author.mention}, your message was deleted because it contained an IP address.")
            await message.delete()
            await message.channel.send(warning_msg, delete_after=15)
            return

    def save_blacklist(self, blacklist):
        with open('data/blacklist.json', 'w') as f:
            json.dump(blacklist, f)

    @commands.hybrid_command(name="blacklist")
    @is_owner()
    async def blacklist(self, ctx, user: discord.User):
        """
        Toggle blacklist status for a user.
        """
        try:
            blacklist = load_blacklist()
            if user.id not in blacklist:
                blacklist.append(user.id)
                self.save_blacklist(blacklist)
                await ctx.reply(f"{user.name} has been blacklisted.", mention_author=True)
            else:
                blacklist.remove(user.id)
                self.save_blacklist(blacklist)
                await ctx.reply(f"{user.name} has been removed from the blacklist.", mention_author=True)
        except Exception as e:
            print(e)

    # Ban command
    @commands.hybrid_command(name="ban")
    @commands.has_permissions(ban_members=True)
    async def ban(self, ctx, member: discord.Member, *, reason=None):
        """
        Ban a user from the server.
        """
        try:
            await member.ban(reason=reason)
            await ctx.send(f"{member} has been banned from the server because {reason}.")
        except Exception as e:
            print(e)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        if message.mentions:
            return
        original_content = message.content.strip()
        if len(original_content) <= 2:
            return
        processed_content = remove_words(original_content, self.remove_list).strip()
        if not processed_content:
            return
        try:
            X = self.vectorizer.transform([processed_content])
        except Exception as e:
            print("Error processing message content:", e)
            return
        try:
            probabilities = self.classifier.predict_proba(X)[0]
        except Exception as e:
            print("Error predicting message probabilities:", e)
            return

        flagged_idx = None
        for idx, cls_label in enumerate(self.classifier.classes_):
            if cls_label == "FLAGGED":
                flagged_idx = idx
                break
        if flagged_idx is None:
            flagged_idx = 1

        flagged_prob = probabilities[flagged_idx]
        if flagged_prob >= self.threshold:
            try:
                # await message.delete()
                warn_message = (
                    f"{message.author.mention}, you message was flagged, nothing will be deleted, however, this incident has been logged.\n\nZluqe AI is still in development, please notify if there are any mistakes."
                )
                await message.channel.send(warn_message, delete_after=15)
                
                if self.log_channel_id:
                    log_channel = self.bot.get_channel(self.log_channel_id)
                    if log_channel:
                        embed = discord.Embed(title="Deleted Flagged Message", color=discord.Color.red())
                        embed.add_field(name="User", value=str(message.author), inline=True)
                        embed.add_field(name="Flagged Probability", value=f"{flagged_prob:.4f}", inline=True)
                        embed.add_field(name="Content", value=original_content or "N/A", inline=False)
                        await log_channel.send(embed=embed)
                    else:
                        print(f"Logging channel with ID {self.log_channel_id} not found.")
                else:
                    print("No logging channel ID provided.")
            except Exception as e:
                print("Error deleting flagged message:", e)

async def setup(bot):
    await bot.add_cog(Moderation(bot, threshold=0.5))
import asyncio
import discord
import yt_dlp as youtube_dl
import os

from dotenv import load_dotenv
from discord import app_commands
from youtube_search import YoutubeSearch
from discord.ext import commands
from flask import Flask


load_dotenv()
TOKEN = os.getenv("DISCORD_BOT_TOKEN")
youtube_dl.utils.bug_reports_message = lambda: ''

intents = discord.Intents.default()
intents.messages = True
intents.message_content = True
 
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

COOKIE_PATH = os.path.join(os.path.dirname(__file__), "cookies.txt")

def download_audio(url):
    ydl_opts = {
        'format': 'bestaudio/best',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'outtmpl': 'downloads/%(title)s.%(ext)s',
        'cookies': COOKIE_PATH,  # 쿠키 파일 경로
        'quiet': False,  # 디버그 로그 확인용
        'noplaylist': True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            return ydl.prepare_filename(info)
    except Exception as e:
        print(f"Error downloading audio: {e}")
        return None

app=Flask(__name__)

@app.route("/")
def home():
    return "Discord bot is running"
 
ytdl_format_options = {
    'format': 'bestaudio/best',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0',
}
 
ffmpeg_options = {
    'options': '-vn',
}
 
ytdl = youtube_dl.YoutubeDL(ytdl_format_options)
 

class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')
        self.url = data.get('url')
 
    @classmethod
    async def from_url(cls, url, *, loop=None, stream=False):
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))
 
        if 'entries' in data:
            data = data['entries'][0]
 
        filename = data['url'] if stream else ytdl.prepare_filename(data)
        return cls(discord.FFmpegPCMAudio(filename, **ffmpeg_options), data=data)
 
queues={}
repeat_flags={}

def get_guild_queue(guild_id):
    if guild_id not in queues:
        queues[guild_id] = asyncio.Queue()
    return queues[guild_id]

def toggle_repeat(guild_id):
    repeat_flags[guild_id] = not repeat_flags.get(guild_id, False)
    return repeat_flags[guild_id]



@bot.event
async def on_ready():
    await tree.sync()
    print(f"Logged in as {bot.user}")



# /play 명령어
@tree.command(name="play", description="검색어로 노래 재생")
async def play(interaction: discord.Interaction, search_query: str):
    await interaction.response.defer()

    # 1. 검색
    results = YoutubeSearch(search_query, max_results=1).to_dict()
    if not results:
        await interaction.followup.send("검색결과 없음", ephemeral=True)
        return

    video_url = f"https://www.youtube.com{results[0]['url_suffix']}"
    video_title = results[0]['title']

    # 2. 음성채널 연결
    if not interaction.user.voice:
        await interaction.followup.send("음성채널 찾을 수 없음", ephemeral=True)
        return

    voice_channel = interaction.user.voice.channel
    if interaction.guild.voice_client is None:
        await voice_channel.connect()
    elif interaction.guild.voice_client.channel != voice_channel:
        await interaction.guild.voice_client.move_to(voice_channel)

    # 3. 대기열 추가
    queue = get_guild_queue(interaction.guild.id)
    await queue.put((video_url, video_title))
    await interaction.followup.send(f"대기열에 추가됨: **{video_title}**", ephemeral=True)

    # 4. 재생 상태 확인 및 재생 시작
    if not interaction.guild.voice_client.is_playing():
        await play_next_in_queue(interaction.guild)

async def play_next_in_queue(guild):
    queue = get_guild_queue(guild.id)
    if queue.empty():
        return

    video_url, video_title = await queue.get()
    try:
        player = await YTDLSource.from_url(video_url, loop=bot.loop)
    except Exception as e:
        print(f"Error fetching video: {e}")
        await play_next_in_queue(guild)
        return

    def after_playing(error):
        if error:
            print(f"Playback error: {error}")
        if repeat_flags.get(guild.id, False):  # 반복 재생
            asyncio.run_coroutine_threadsafe(queue.put((video_url, video_title)), bot.loop)
        asyncio.run_coroutine_threadsafe(play_next_in_queue(guild), bot.loop)

    # guild.voice_client.play(player, after=after_playing)
    # channel = discord.utils.get(guild.channels, id=guild.voice_client.channel.id)
    # if channel:
    #     await channel.send(f"재생 중: **{video_title}**")

    text_channel = discord.utils.get(guild.text_channels, name="일반")
    if not text_channel:
        text_channel = guild.text_channels[0]

    guild.voice_client.play(player, after=after_playing)

    if text_channel:
        await text_channel.send(f"재생 중: **{video_title}**")

# /stop 명령어
@tree.command(name="stop", description="재생 중인 노래 정지")
async def stop(interaction: discord.Interaction):
    if interaction.guild.voice_client:
        await interaction.guild.voice_client.disconnect()
        await interaction.response.send_message("음성 채널에서 나감", ephemeral=True)
    else:
        await interaction.response.send_message("음성 채널에 연결되어 있지 않음", ephemeral=True)

# /queue 명령어
@tree.command(name="queue", description="현재 대기열 표시")
async def queue(interaction: discord.Interaction):
    queue = get_guild_queue(interaction.guild.id)
    if queue.empty():
        await interaction.response.send_message("대기열이 비어있음", ephemeral=True)
    else:
        items = list(queue._queue)  # asyncio.Queue의 대기열 항목 가져오기
        message = "\n".join([f"{i+1}. {title}" for i, (_, title) in enumerate(items)])
        await interaction.response.send_message(f"**현재 대기열:**\n{message}")

# /skip 명령어
@tree.command(name="skip", description="현재 재생 중인 노래 건너뛰기")
async def skip(interaction: discord.Interaction):
    if interaction.guild.voice_client and interaction.guild.voice_client.is_playing():
        interaction.guild.voice_client.stop()
        await interaction.response.send_message("현재 노래를 건너뜀", ephemeral=True)
    else:
        await interaction.response.send_message("재생 중인 노래가 없음", ephemeral=True)

# /repeat 명령어
@tree.command(name="repeat", description="repeat on(default)/off")
async def repeat(interaction: discord.Interaction):
    status = toggle_repeat(interaction.guild.id)
    if status:
        await interaction.response.send_message("반복 재생 활성화", ephemeral=True)
    else:
        await interaction.response.send_message("반복 재생 비활성화", ephemeral=True)

@bot.event
async def on_voice_state_update(member, before, after):
    # 봇이 속한 음성 채널 가져오기
    voice_client = discord.utils.get(bot.voice_clients, guild=member.guild)

    # 봇이 음성 채널에 있고, 현재 음성 채널을 확인할 수 있는 경우
    if voice_client and voice_client.is_connected():
        channel = voice_client.channel  # 봇이 있는 음성 채널

        # 음성 채널에 있는 멤버 목록 중 봇을 제외한 사용자 확인
        non_bot_members = [m for m in channel.members if not m.bot]

        # 음성 채널에 봇만 남아 있을 경우
        if len(non_bot_members) == 0:
            await voice_client.disconnect()

if __name__ == "__main__":
    from threading import Thread
    def run_flask():
        app.run(host="0.0.0.0", port=8080)

    flask_thread = Thread(target=run_flask)
    flask_thread.start()

bot.run(TOKEN)
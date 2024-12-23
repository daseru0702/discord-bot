import sys
import io
import asyncio
import discord
import yt_dlp as youtube_dl
import os
import datetime

from dotenv import load_dotenv
from discord import app_commands
from youtube_search import YoutubeSearch
from discord.ext import commands
from discord.ui import View, Button


load_dotenv()
TOKEN = os.getenv("DISCORD_BOT_TOKEN")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

youtube_dl.utils.bug_reports_message = lambda: ''

intents = discord.Intents.default()
intents.messages = True
intents.message_content = True
 
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

COOKIE_PATH = os.path.join(os.path.dirname(__file__), "cookies.txt")
if not os.path.exists(COOKIE_PATH):
    print(f"Error: cookies.txt not found at {COOKIE_PATH}")

def download_audio(url):
    ydl_opts = {
        'format': 'bestaudio/best',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'outtmpl': 'downloads/%(title)s.%(ext)s',
        'cookies': COOKIE_PATH,  # 쿠키 파일 경로 추가
        'quiet': False,
        'verbose': True,  # 디버그 로그 활성화
        'noplaylist': True,
        'nocheckcertificate': True,
        'default_search': 'auto',  # 기본 검색
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # 동영상 정보 추출 및 다운로드
            info = ydl.extract_info(url, download=True)
            return ydl.prepare_filename(info)  # 파일 경로 반환
    except Exception as e:
        print(f"Error downloading audio: {e}")
        return None
        
def get_recommended_songs(video_url):
    try:
        with youtube_dl.YoutubeDL({'quiet': True}) as ydl:
            info = ydl.extract_info(video_url, download=False)
            # 확인용 출력
            print("Extracted Info (Partial):", str(info)[:500])

            # 'related'가 유효한지 확인
            related_videos = info.get('entries')  # yt_dlp의 최신 동작 확인
            if not related_videos:
                print("No related videos found.")
                return []
            return [(video['title'].encode('utf-8', 'ignore').decode('utf-8'), f"https://www.youtube.com/watch?v={video['id']}") for video in related_videos]
    except UnicodeEncodeError as e:
        print(f"Unicode encoding error: {e}")
        return []
    except Exception as e:
        print(f"Error getting recommendations: {e}")
        return []

 
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
    'cookies': COOKIE_PATH,  # 쿠키 파일 경로 추가
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
        self.duration = data.get('duration')

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=False):
        loop = loop or asyncio.get_event_loop()

        try:
            data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))
        except Exception as e:
            print(f"Error extracting info: {e}")
            raise

        if 'entries' in data:
            data = data['entries'][0]

        filename = data['url'] if stream else ytdl.prepare_filename(data)
        return cls(discord.FFmpegPCMAudio(filename, **ffmpeg_options), data=data)
            
queues={}
repeat_flags={}
recommendation_settings = {}

def get_guild_queue(guild_id):
    if guild_id not in queues:
        queues[guild_id] = asyncio.Queue()
    return queues[guild_id]

# 대기열에 저장되는 데이터 구조: (url, title, added_by, is_recommended)
# - added_by: 곡을 추가한 사용자
# - is_recommended: 추천 곡 여부 (True/False)

def toggle_repeat(guild_id):
    repeat_flags[guild_id] = not repeat_flags.get(guild_id, False)
    return repeat_flags[guild_id]

def set_recommendation_status(guild_id):
    # 추천 기능 상태 토글
    recommendation_settings[guild_id] = not recommendation_settings.get(guild_id, True)
    return recommendation_settings[guild_id]

def is_recommendation_enabled(guild_id):
    # 추천 기능 활성화 여부 확인
    return recommendation_settings.get(guild_id, True)

@bot.event
async def on_ready():
    await tree.sync()
    print(f"Logged in as {bot.user}")

current_song_urls = {}

def set_current_song_url(guild_id, url):
    current_song_urls[guild_id] = url
    print(f"Set current song URL for guild {guild_id}: {url}") #디버깅용

def get_current_song_url(guild_id):
    url = current_song_urls.get(guild_id)
    print(f"Get current song URL for guild {guild_id}: {url}") #디버깅용
    return url

# /play 명령어
@tree.command(name="play", description="검색어로 노래 재생")
async def play(interaction: discord.Interaction, search_query: str):
    await interaction.response.defer(ephemeral=True)

    try:
        # 검색
        results = YoutubeSearch(search_query, max_results=1).to_dict()
        if not results:
            await interaction.followup.send("검색결과 없음", ephemeral=True)
            return

        video_url = f"https://www.youtube.com{results[0]['url_suffix']}"
        video_title = results[0]['title']

        set_current_song_url(interaction.guild.id, video_url) # 현재 곡 URL 저장

        # 음성 채널 연결
        if not interaction.user.voice:
            await interaction.followup.send("음성채널 찾을 수 없음", ephemeral=True)
            return

        voice_channel = interaction.user.voice.channel
        if interaction.guild.voice_client is None:
            await voice_channel.connect()
        elif interaction.guild.voice_client.channel != voice_channel:
            await interaction.guild.voice_client.move_to(voice_channel)

        # 대기열 추가
        queue = get_guild_queue(interaction.guild.id)
        await queue.put((video_url, video_title, interaction.user.display_name, False))
        await interaction.followup.send(f"대기열에 추가됨: **{video_title}**", ephemeral=True)

        # 재생 상태 확인 및 재생 시작
        if not interaction.guild.voice_client.is_playing():
            await play_next_in_queue(interaction.guild)

    except Exception as e:
        print(f"Error in play command: {e}")
        await interaction.followup.send("오류 발생: 재생 실패", ephemeral=True)

# play alias
@tree.command(name="p", description="검색어로 노래 재생")
async def p(interaction: discord.Interaction, search_query: str):
    await play(interaction, search_query)  # /play 명령어 호출

# 재생 처리
current_embed_messages = {}

class MusicControlView(View):
    def __init__(self, interaction):
        super().__init__(timeout=None)  # 버튼이 사라지지 않도록 timeout을 None으로 설정
        self.interaction = interaction

    @discord.ui.button(label="Queue", style=discord.ButtonStyle.primary)
    async def queue_button(self, interaction: discord.Interaction, button: Button):
        queue = get_guild_queue(self.interaction.guild.id)
        if queue.empty():
            await interaction.response.send_message("대기열이 비어있음", ephemeral=True)
        else:
            items = list(queue._queue)
            message = "\n".join([f"{i+1}. {title}" for i, (_, title) in enumerate(items)])
            await interaction.response.send_message(f"**현재 대기열:**\n{message}", ephemeral=True)

    @discord.ui.button(label="Skip", style=discord.ButtonStyle.secondary)
    async def skip_button(self, interaction: discord.Interaction, button: Button):
        if interaction.guild.voice_client and interaction.guild.voice_client.is_playing():
            interaction.guild.voice_client.stop()
            await interaction.response.send_message("현재 노래를 건너뜀", ephemeral=True)
        else:
            await interaction.response.send_message("재생 중인 노래가 없음", ephemeral=True)

    @discord.ui.button(label="Stop", style=discord.ButtonStyle.danger)
    async def stop_button(self, interaction: discord.Interaction, button: Button):
        if interaction.guild.voice_client:
            await interaction.guild.voice_client.disconnect()
            await interaction.response.send_message("음성 채널에서 나감", ephemeral=True)
        else:
            await interaction.response.send_message("음성 채널에 연결되어 있지 않음", ephemeral=True)

async def play_next_in_queue(guild):
    queue = get_guild_queue(guild.id)
    if queue.empty():
        if is_recommendation_enabled(guild.id):  # 추천곡 추가 활성화 여부
            current_song_url = get_current_song_url(guild.id)
            print(f"Current song URL: {current_song_url}")  # 디버깅용

            if current_song_url:
                recommended_songs = get_recommended_songs(current_song_url)
                print(f"Recommended Songs: {recommended_songs}")  # 디버깅용
        
                if recommended_songs:
                    for title, url in recommended_songs[:5]:  # 최대 5개의 추천 곡 추가
                        await queue.put((url, title, None, True))
                    text_channel = discord.utils.get(guild.text_channels, name="일반") or guild.text_channels[0]
                    await text_channel.send(f"대기열이 비어있어 자동으로 추가됨: {', '.join([title for title, _ in recommended_songs[:5]])}")
        # 재생이 끝났으므로 임베드 삭제
        if guild.id in current_embed_messages:
            try:
                await current_embed_messages[guild.id].delete()
                del current_embed_messages[guild.id]  # 메시지 기록 삭제
            except discord.NotFound:
                pass
        return

    # 다음 곡 불러오기
    video_url, video_title, added_by, is_recommended = await queue.get()

    try:
        player = await YTDLSource.from_url(video_url, loop=bot.loop)
    except Exception as e:
        print(f"Error fetching video: {e}")
        await play_next_in_queue(guild)
        return

    # 재생 시작
    def after_playing(error):
        if error:
            print(f"Playback error: {error}")
        # 재생이 끝났으므로 임베드 삭제
        if guild.id in current_embed_messages:
            coro = current_embed_messages[guild.id].delete()
            asyncio.run_coroutine_threadsafe(coro, bot.loop)
            del current_embed_messages[guild.id]

        if repeat_flags.get(guild.id, False):  # 반복 재생 여부 검사
            asyncio.run_coroutine_threadsafe(queue.put((video_url, video_title, added_by, is_recommended)), bot.loop)
        asyncio.run_coroutine_threadsafe(play_next_in_queue(guild), bot.loop)

    # 채팅 채널 검색
    text_channel = discord.utils.get(guild.text_channels, name="일반")
    if not text_channel:
        text_channel = guild.text_channels[0]

    # 음성 재생
    guild.voice_client.play(player, after=after_playing)

    # 임베드
    embed = discord.Embed(title="현재 재생 중", description=f"[{video_title}]\n{video_url}", color=discord.Color.red())
    duration = str(datetime.timedelta(seconds=player.data.get('duration', 0)))
    embed.add_field(name="길이", value=duration, inline=True)
    embed.add_field(name="채널", value=guild.voice_client.channel.name, inline=True)

    if is_recommended:
        embed.add_field(name="요청자", value="자동재생", inline=False)
    else:
        embed.add_field(name="요청자", value=f"{added_by}", inline=False)

    # View를 사용하여 버튼 추가
    view = MusicControlView(interaction=text_channel)  # View 생성
    message = await text_channel.send(embed=embed, view=view)  # View 포함 메시지 전송

    # 현재 임베드 저장
    current_embed_messages[guild.id] = message


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
        items = list(queue._queue)
        message = "\n".join([f"{i+1}. {title}" for i, (_, title) in enumerate(items)])
        await interaction.response.send_message(f"**현재 대기열:**\n{message}", ephemeral=True)

# /skip 명령어
@tree.command(name="skip", description="현재 재생 중인 노래 건너뛰기")
async def skip(interaction: discord.Interaction):
    if interaction.guild.voice_client and interaction.guild.voice_client.is_playing():
        interaction.guild.voice_client.stop()
        await interaction.response.send_message("현재 노래를 건너뜀", ephemeral=True)
    else:
        await interaction.response.send_message("재생 중인 노래가 없음", ephemeral=True)

# /repeat 명령어
@tree.command(name="repeat", description="현재 재생 중인 노래를 반복")
async def repeat(interaction: discord.Interaction):
    repeat_status = toggle_repeat(interaction.guild.id)
    if repeat_status:
        await interaction.response.send_message("반복 재생 활성화", ephemeral=True)
    else:
        await interaction.response.send_message("반복 재생 비활성화", ephemeral=True)

# /toggle_recommendations 명령어
@tree.command(name="toggle_recommendations", description="대기열이 비어있을 때 자동으로 추가")
async def toggle_recommendations(interaction: discord.Interaction):
    recommend_status = set_recommendation_status(interaction.guild.id)
    if recommend_status:
        await interaction.response.send_message("자동 재생 활성화", ephemeral=True)
    else:
        await interaction.response.send_message("자동 재생 비활성화", ephemeral=True)


# 음성 채널에 봇만 남은 경우 연결 끊기
@bot.event
async def on_voice_state_update(member, before, after):
    voice_client = discord.utils.get(bot.voice_clients, guild=member.guild)

    if voice_client and voice_client.is_connected():
        channel = voice_client.channel

        non_bot_members = [m for m in channel.members if not m.bot]

        if len(non_bot_members) == 0:
            await voice_client.disconnect()

bot.run(TOKEN)
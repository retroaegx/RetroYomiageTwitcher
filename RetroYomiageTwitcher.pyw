import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import configparser
import os
import io
import asyncio
import threading
import requests
from twitchio.ext import commands
import re
import aiohttp
import pygame
from google.cloud import texttospeech
from google.oauth2 import service_account
from langdetect import detect, LangDetectException
from pydub import AudioSegment
from pydub.playback import play
import math
from collections import deque
from collections import Counter

# ファイル名の定義
CONFIG_FILE = "settings.ini"
NG_FILE = "ng_settings.ini"
REPLACE_FILE = "replace_settings.ini"
VOICEBOX_SPEAKERS_FILE = "voicebox_speakers.ini"

# configparserの設定
config = configparser.ConfigParser()
ng_config = configparser.ConfigParser()
replace_config = configparser.ConfigParser()
voicebox_speakers_config = configparser.ConfigParser()

# 設定の保存
def save_settings(username, token, channels, credentials_path):
	config['Settings'] = {
		'username': username,
		'token': token,
		'credentials_path': credentials_path
	}
	config['Channels'] = {'list': ','.join(channels)}
	with open(CONFIG_FILE, 'w') as configfile:
		config.write(configfile)

# NG設定の保存
def save_ng_settings(ng_users, ng_comments):
	ng_config['NG'] = {
		'users': ','.join(ng_users),
		'comments': ','.join(ng_comments)
	}
	with open(NG_FILE, 'w') as ngfile:
		ng_config.write(ngfile)

# 読み替え設定の保存
def save_replace_settings(replace_dict):
	replace_config['Replace'] = replace_dict
	with open(REPLACE_FILE, 'w') as replacefile:
		replace_config.write(replacefile)

# VOICEBOXスピーカー設定の保存
def save_voicebox_speakers(speaker_map):
	voicebox_speakers_config['Speakers'] = speaker_map
	with open(VOICEBOX_SPEAKERS_FILE, 'w') as speakerfile:
		voicebox_speakers_config.write(speakerfile)

# 設定の読み込み
def load_settings():
	if os.path.exists(CONFIG_FILE):
		config.read(CONFIG_FILE)
		settings = config['Settings']
		return settings.get('username', ''), settings.get('token', ''), config.get('Channels', 'list', fallback='').split(','), settings.get('credentials_path', '')
	return '', '', [], ''

# NG設定の読み込み
def load_ng_settings():
	if os.path.exists(NG_FILE):
		ng_config.read(NG_FILE)
		ng_settings = ng_config['NG']
		ng_users = ng_settings.get('users', '').split(',')
		ng_comments = ng_settings.get('comments', '').split(',')

		# 空の文字列を除去
		ng_users = [user.strip() for user in ng_users if user.strip()]
		ng_comments = [comment.strip() for comment in ng_comments if comment.strip()]

		return ng_users, ng_comments
	return [], []

# 読み替え設定の読み込み
def load_replace_settings():
	if os.path.exists(REPLACE_FILE):
		replace_config.read(REPLACE_FILE)
		return dict(replace_config.items('Replace'))
	return {}

# VOICEBOXスピーカー設定の読み込み
def load_voicebox_speakers():
	if os.path.exists(VOICEBOX_SPEAKERS_FILE):
		voicebox_speakers_config.read(VOICEBOX_SPEAKERS_FILE)
		return dict(voicebox_speakers_config.items('Speakers'))
	return {}

class AudioQueue:
	def __init__(self):
		self.queue = asyncio.Queue()

	async def put(self, audio_data):
		await self.queue.put(audio_data)

	async def get(self):
		return await self.queue.get()

	def clear(self):
		while not self.queue.empty():
			try:
				self.queue.get_nowait()
			except asyncio.QueueEmpty:
				pass
				
class TTSQueue:
	def __init__(self):
		self.queue = deque()
		self.preloaded_audio = deque(maxlen=2)

	def add(self, text, volume, speed, language_code):
		self.queue.append((text, volume, speed, language_code))

	def get(self):
		if self.queue:
			return self.queue.popleft()
		return None

	def size(self):
		return len(self.queue)

	def add_preloaded_audio(self, audio_data):
		self.preloaded_audio.append(audio_data)

	def get_preloaded_audio(self):
		if self.preloaded_audio:
			return self.preloaded_audio.popleft()
		return None

class TTSManager:
	def __init__(self, japanese_engine, other_engine):
		self.japanese_engine = japanese_engine
		self.other_engine = other_engine
		self.queue = asyncio.Queue()
		self.queue_size = 0

	def add_to_queue(self, text, volume, speed, language_code):
		self.queue.put_nowait((text, volume, speed, language_code))
		self.queue_size += 1

	async def process_queue(self):
		while True:
			try:
				text, volume, speed, language_code = await self.queue.get()
				engine = self.japanese_engine if language_code == "ja-JP" else self.other_engine
				
				# 速度の調整
				adjusted_speed = self.adjust_speed(speed)
				
				await engine.speak(text, volume, adjusted_speed, language_code)
				self.queue_size -= 1
			except Exception as e:
				print(f"Error processing queue: {e}")
			finally:
				self.queue.task_done()

	def adjust_speed(self, original_speed):
		if self.queue_size <= 1:
			return original_speed
		elif self.queue_size == 2:
			return original_speed * 1.3
		else:
			return min(original_speed * ((self.queue_size - 2) ** 1.3),200)

	def clear_queue(self):
		while not self.queue.empty():
			try:
				self.queue.get_nowait()
				self.queue.task_done()
			except asyncio.QueueEmpty:
				pass
		self.queue_size = 0
		
		# エンジンのキューもクリア
		if isinstance(self.japanese_engine, VoiceBox):
			self.japanese_engine.clear_queue()
		if isinstance(self.other_engine, VoiceBox):
			self.other_engine.clear_queue()

class BouyomiChan:
	def __init__(self, host='localhost', port=50080):
		self.url = f"http://{host}:{port}/talk"

	async def generate_audio(self, text, volume, speed, language_code):
		payload = {
			'text': text,
			'volume': volume * 0.85,
			'speed': int(speed),
			'voice': 0 if language_code == "ja-JP" else 1,  # 0 for Japanese, 1 for English
			'api': 'true'  # Request audio data
		}
		try:
			async with aiohttp.ClientSession() as session:
				async with session.post(self.url, data=payload) as response:
					response.raise_for_status()
					return await response.read()
		except aiohttp.ClientError as e:
			print(f"Error: {e}")
			return None

	async def speak(self, text, volume, speed, language_code):
		audio_data = await self.generate_audio(text, volume, speed, language_code)
		if audio_data:
			audio_file = io.BytesIO(audio_data)
			pygame.mixer.music.load(audio_file, 'wav')
			pygame.mixer.music.set_volume(volume / 100.0)
			pygame.mixer.music.play()
			while pygame.mixer.music.get_busy():
				await asyncio.sleep(0.1)

class VoiceBox:
	def __init__(self, speaker):
		self.url = "http://127.0.0.1:50021"
		self.speaker = speaker
		pygame.mixer.init()
		self.lock = asyncio.Lock()
		self.audio_queue = AudioQueue()

	async def generate_audio(self, text, volume, speed, language_code):
		async with self.lock:
			try:
				async with aiohttp.ClientSession() as session:
					async with session.post(f"{self.url}/audio_query", params={"text": text, "speaker": self.speaker}) as resp:
						resp.raise_for_status()
						query_data = await resp.json()

					query_data["volumeScale"] = volume * 1.5 / 100.0
					query_data["speedScale"] = speed / 100.0

					async with session.post(f"{self.url}/synthesis", params={"speaker": self.speaker}, json=query_data) as resp:
						resp.raise_for_status()
						return await resp.read()

			except Exception as e:
				print(f"Error: {e}")
				return None

	async def speak(self, text, volume, speed, language_code):
		audio_data = await self.generate_audio(text, volume, speed, language_code)
		if audio_data:
			audio_file = io.BytesIO(audio_data)
			pygame.mixer.music.load(audio_file, 'wav')
			pygame.mixer.music.set_volume(volume / 100.0)
			pygame.mixer.music.play()
			while pygame.mixer.music.get_busy():
				await asyncio.sleep(0.1)
				
	def clear_queue(self):
		self.audio_queue.clear()
		pygame.mixer.music.stop()  # 現在再生中の音声も停止
		
	async def close(self):
		if self.session:
			await self.session.close()
		pygame.mixer.quit()

class GoogleTTS:
	def __init__(self, credentials_path):
		self.credentials_path = credentials_path
		pygame.mixer.init()

	async def generate_audio(self, text, volume, speed, language_code):
		credentials = service_account.Credentials.from_service_account_file(self.credentials_path)
		client = texttospeech.TextToSpeechClient(credentials=credentials)

		input_text = texttospeech.SynthesisInput(text=text)
		voice = texttospeech.VoiceSelectionParams(
			language_code=language_code, ssml_gender=texttospeech.SsmlVoiceGender.NEUTRAL)
		audio_config = texttospeech.AudioConfig(
			audio_encoding=texttospeech.AudioEncoding.MP3,
			speaking_rate=speed / 100.0,
			volume_gain_db=self.calculate_volume_gain(volume))

		response = client.synthesize_speech(
			input=input_text, voice=voice, audio_config=audio_config)

		return response.audio_content

	async def speak(self, text, volume, speed, language_code):
		audio_content = await self.generate_audio(text, volume, speed, language_code)
		if audio_content:
			mp3_file = io.BytesIO(audio_content)
			pygame.mixer.music.load(mp3_file, "mp3")
			pygame.mixer.music.set_volume(volume * 0.85 / 100.0)
			pygame.mixer.music.play()

			while pygame.mixer.music.get_busy():
				await asyncio.sleep(0.1)

	def calculate_volume_gain(self, volume):
		if volume == 0:
			return -96.0
		else:
			return 20 * math.log10(volume / 50.0)

def get_speaker_map():
	# VOICEBOXスピーカー設定を読み込み
	speaker_map = load_voicebox_speakers()
	if speaker_map:
		return speaker_map

	# 設定がない場合はhttp://localhost:50021/speakersから取得
	try:
		response = requests.get("http://localhost:50021/speakers")
		response.raise_for_status()
		speakers = response.json()
		speaker_map = {}

		# 対象キャラクター
		target_characters = {
			"ずんだもん": ["ノーマル", "あまあま"],
			"四国めたん": ["ノーマル", "あまあま"],
			"春日部つむぎ": ["ノーマル"],
			"冥鳴ひまり": ["ノーマル"]
		}

		for speaker in speakers:
			name = speaker["name"]
			if name in target_characters:
				for style in speaker["styles"]:
					style_name = style["name"]
					if style_name in target_characters[name]:
						speaker_id = style["id"]
						key = f"{name}（{style_name}）"
						speaker_map[key] = speaker_id

		# VOICEBOXスピーカー設定を保存
		save_voicebox_speakers(speaker_map)
	except requests.exceptions.RequestException as e:
		print(f"Error: {e}")

	return speaker_map

# 設定入力ダイアログ
class SettingsDialog(tk.Toplevel):
	def __init__(self, master, username='', token='', credentials_path=''):
		super().__init__(master)
		self.title("Settings")
		self.grab_set()  # モーダルにする

		tk.Label(self, text="Twitch Username:").pack(pady=5)
		self.username_entry = tk.Entry(self)
		self.username_entry.insert(0, username)
		self.username_entry.pack(pady=5)

		tk.Label(self, text="OAuth Token:").pack(pady=5)
		self.token_entry = tk.Entry(self)
		self.token_entry.insert(0, token)
		self.token_entry.pack(pady=5)

		tk.Label(self, text="Google Cloud TTS Credentials Path:").pack(pady=5)
		self.credentials_path_entry = tk.Entry(self)
		self.credentials_path_entry.insert(0, credentials_path)
		self.credentials_path_entry.pack(pady=5)

		self.browse_button = tk.Button(self, text="Browse", command=self.browse_file)
		self.browse_button.pack(pady=5)

		tk.Label(self, text="Get Twitch OAuth Token:").pack(pady=5)
		self.twitch_link = tk.Label(self, text="https://twitchapps.com/tmi/", fg="blue", cursor="hand2")
		self.twitch_link.pack(pady=5)
		self.twitch_link.bind("<Button-1>", lambda e: os.system("start " + self.twitch_link.cget("text")))

		tk.Label(self, text="Get Google Cloud TTS Json Key:").pack(pady=5)
		self.link = tk.Label(self, text="https://console.cloud.google.com/apis/library/texttospeech.googleapis.com", fg="blue", cursor="hand2")
		self.link.pack(pady=5)
		self.link.bind("<Button-1>", lambda e: os.system("start " + self.link.cget("text")))

		self.save_button = tk.Button(self, text="Save", command=self.save)
		self.save_button.pack(pady=10)

	def browse_file(self):
		file_path = filedialog.askopenfilename(filetypes=[("JSON files", "*.json")])
		if file_path:
			self.credentials_path_entry.delete(0, tk.END)
			self.credentials_path_entry.insert(0, file_path)

	def save(self):
		self.username = self.username_entry.get().strip()
		self.token = self.token_entry.get().strip()
		self.credentials_path = self.credentials_path_entry.get().strip()
		if self.username and self.token and self.credentials_path:
			self.destroy()
		else:
			messagebox.showerror("Error", "Please fill in all fields")

# NGユーザー・NGコメント管理画面
class NGDialog(tk.Toplevel):
	def __init__(self, master, title, ng_list, save_callback):
		super().__init__(master)
		self.title(title)
		self.grab_set()  # モーダルにする

		self.ng_list = ng_list
		self.save_callback = save_callback

		self.listbox = tk.Listbox(self, selectmode=tk.SINGLE)
		self.listbox.pack(pady=5, padx=5)
		self.update_listbox()

		entry_frame = tk.Frame(self)
		entry_frame.pack(pady=5, padx=5)

		self.entry = tk.Entry(entry_frame)
		self.entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

		button_frame = tk.Frame(self)
		button_frame.pack(pady=5)

		self.add_button = tk.Button(button_frame, text="追加", command=self.add_item)
		self.add_button.pack(side=tk.LEFT, padx=5)

		self.remove_button = tk.Button(button_frame, text="削除", command=self.remove_item)
		self.remove_button.pack(side=tk.LEFT, padx=5)

	def update_listbox(self):
		self.listbox.delete(0, tk.END)
		for item in self.ng_list:
			self.listbox.insert(tk.END, item)

	def add_item(self):
		item = self.entry.get().strip()
		if item and item not in self.ng_list:
			self.ng_list.append(item)
			self.update_listbox()
			self.entry.delete(0, tk.END)  # テキストボックスをクリア
			self.save_callback(self.ng_list)

	def remove_item(self):
		selected = self.listbox.curselection()
		if selected:
			item = self.listbox.get(selected[0])
			self.ng_list.remove(item)
			self.update_listbox()
			self.save_callback(self.ng_list)

# 読み替え設定管理画面
class ReplaceDialog(tk.Toplevel):
	def __init__(self, master, replace_dict, save_callback):
		super().__init__(master)
		self.title("読み替え設定")
		self.grab_set()

		self.replace_dict = replace_dict
		self.save_callback = save_callback

		self.listbox = tk.Listbox(self, selectmode=tk.SINGLE)
		self.listbox.pack(pady=5, padx=5)
		self.update_listbox()

		entry_frame = tk.Frame(self)
		entry_frame.pack(pady=5, padx=5)

		self.key_entry = tk.Entry(entry_frame)
		self.key_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
		self.value_entry = tk.Entry(entry_frame)
		self.value_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

		button_frame = tk.Frame(self)
		button_frame.pack(pady=5)

		self.add_button = tk.Button(button_frame, text="追加", command=self.add_item)
		self.add_button.pack(side=tk.LEFT, padx=5)

		self.remove_button = tk.Button(button_frame, text="削除", command=self.remove_item)
		self.remove_button.pack(side=tk.LEFT, padx=5)

	def update_listbox(self):
		self.listbox.delete(0, tk.END)
		for key, value in self.replace_dict.items():
			self.listbox.insert(tk.END, f"{key} => {value}")

	def add_item(self):
		key = self.key_entry.get().strip()
		value = self.value_entry.get().strip()
		if key and value:
			self.replace_dict[key] = value
			self.update_listbox()
			self.key_entry.delete(0, tk.END)
			self.value_entry.delete(0, tk.END)
			self.save_callback(self.replace_dict)

	def remove_item(self):
		selected = self.listbox.curselection()
		if selected:
			item = self.listbox.get(selected[0])
			key = item.split(' => ')[0]
			del self.replace_dict[key]
			self.update_listbox()
			self.save_callback(self.replace_dict)

# メインアプリケーション
class Application(tk.Tk):
	def __init__(self):
		super().__init__()
		
		self.title("Twitch IRC Client")
		self.geometry("400x450")
		self.resizable(False, False)

		self.protocol("WM_DELETE_WINDOW", self.on_closing)

		self.username, self.token, self.channel_list, self.credentials_path = load_settings()
		self.ng_users, self.ng_comments = load_ng_settings()
		self.replace_dict = load_replace_settings()

		if not self.username or not self.token or not self.credentials_path or not os.path.exists(self.credentials_path):
			self.get_settings()

		self.connected_channel = None
		self.bot = None

		self.speaker_map = get_speaker_map()

		self.create_menu()

		# ウィジェットの配置
		frame = tk.Frame(self)
		frame.pack(pady=10, padx=10, fill=tk.X)

		# チャンネル追加
		tk.Label(frame, text="チャンネル追加:").grid(row=0, column=0, pady=5, sticky=tk.W)
		self.channel_entry = tk.Entry(frame)
		self.channel_entry.grid(row=0, column=1, pady=5, padx=5, sticky=tk.W)

		self.add_button = tk.Button(frame, text="追加", command=self.add_channel)
		self.add_button.grid(row=0, column=2, pady=5, padx=5, sticky=tk.W)

		self.remove_button = tk.Button(frame, text="削除", command=self.remove_channel)
		self.remove_button.grid(row=0, column=3, pady=5, padx=5, sticky=tk.W)

		# チャンネル一覧
		tk.Label(frame, text="チャンネル一覧:").grid(row=1, column=0, pady=5, sticky=tk.W)
		self.channel_combobox = ttk.Combobox(frame, values=self.channel_list, state="readonly")
		self.channel_combobox.grid(row=1, column=1, pady=5, padx=5, sticky=tk.W)

		self.connect_button = tk.Button(frame, text="接続", command=self.connect_channel)
		self.connect_button.grid(row=1, column=2, pady=5, padx=5, sticky=tk.W)

		self.disconnect_button = tk.Button(frame, text="切断", command=self.disconnect_channel)
		self.disconnect_button.grid(row=1, column=3, pady=5, padx=5, sticky=tk.W)

		# 音量バー
		tk.Label(frame, text="音量:").grid(row=2, column=0, pady=5, sticky=tk.W)
		self.volume_scale = tk.Scale(frame, from_=0, to=100, orient=tk.HORIZONTAL)
		self.volume_scale.set(100)  # デフォルト音量は100
		self.volume_scale.grid(row=2, column=1, pady=5, padx=5, sticky=tk.W)

		# 読み上げ速度バー
		tk.Label(frame, text="速度:").grid(row=3, column=0, pady=5, sticky=tk.W)
		self.speed_scale = tk.Scale(frame, from_=50, to=200, orient=tk.HORIZONTAL)
		self.speed_scale.set(100)  # デフォルト速度は100
		self.speed_scale.grid(row=3, column=1, pady=5, padx=5, sticky=tk.W)

		# 接続状態表示ラベル
		self.status_label = tk.Label(self, text="")
		self.status_label.pack(pady=5)

		# 名前を呼び上げるチェックボックス
		self.name_var = tk.BooleanVar()
		self.name_checkbutton = tk.Checkbutton(frame, text="名前を呼び上げる", variable=self.name_var)
		self.name_checkbutton.grid(row=4, column=0, columnspan=2, pady=5, sticky=tk.W)
		
		# スタンプまとめ読みのチェックボックス
		self.stamp_var = tk.BooleanVar()
		self.stamp_checkbutton = tk.Checkbutton(frame, text="スタンプはまとめて一つに読み上げる", variable=self.stamp_var)
		self.stamp_checkbutton.grid(row=8, column=0, columnspan=2, pady=5, sticky=tk.W)

		# 日本語とそれ以外のラベルとリストボックス
		tk.Label(frame, text="日本語:").grid(row=5, column=0, pady=5, sticky=tk.W)
		self.japanese_combobox = ttk.Combobox(frame, values=list(self.speaker_map.keys()) + ["棒読みちゃん", "Google読み上げ"], state="readonly")
		self.japanese_combobox.grid(row=5, column=1, pady=5, padx=5, sticky=tk.W)

		tk.Label(frame, text="それ以外:").grid(row=6, column=0, pady=5, sticky=tk.W)
		self.other_combobox = ttk.Combobox(frame, values=list(self.speaker_map.keys()) + ["棒読みちゃん", "Google読み上げ"], state="readonly")
		self.other_combobox.grid(row=6, column=1, pady=5, padx=5, sticky=tk.W)

		# チェックボックスを一番下に配置
		self.name_checkbutton.grid(row=7, column=0, columnspan=2, pady=5, sticky=tk.W)

		# TTSManagerの初期化
		self.tts_manager = TTSManager(
			japanese_engine=self.get_engine(self.japanese_combobox.get()),
			other_engine=self.get_engine(self.other_combobox.get())
		)
		
		# 音声生成と再生のタスクを開始
		self.start_audio_tasks()
		
		self.japanese_combobox.set("棒読みちゃん")  # デフォルト値を設定
		self.other_combobox.set("棒読みちゃん")  # デフォルト値を設定

		self.japanese_combobox.bind("<<ComboboxSelected>>", self.update_engines)
		self.other_combobox.bind("<<ComboboxSelected>>", self.update_engines)

		self.update_engines()  # 初期エンジンを設定

		# TTSキューの処理を開始
		asyncio.run_coroutine_threadsafe(self.tts_manager.process_queue(), loop)

	def start_audio_tasks(self):
		asyncio.run_coroutine_threadsafe(self.tts_manager.process_queue(), loop)
		for engine in [self.tts_manager.japanese_engine, self.tts_manager.other_engine]:
			if isinstance(engine, VoiceBox):
				asyncio.run_coroutine_threadsafe(engine.initialize(), loop)
				asyncio.run_coroutine_threadsafe(engine.play_audio(), loop)
				
	def create_menu(self):
		menubar = tk.Menu(self)
		self.config(menu=menubar)

		settings_menu = tk.Menu(menubar, tearoff=0)
		menubar.add_cascade(label="設定", menu=settings_menu)
		settings_menu.add_command(label="読み替え", command=self.manage_replace)

		ng_menu = tk.Menu(menubar, tearoff=0)
		menubar.add_cascade(label="NG設定", menu=ng_menu)
		ng_menu.add_command(label="NGユーザー", command=self.manage_ng_users)
		ng_menu.add_command(label="NGコメント", command=self.manage_ng_comments)

	def manage_ng_users(self):
		NGDialog(self, "NGユーザー管理", self.ng_users, self.save_ng_users)

	def manage_ng_comments(self):
		NGDialog(self, "NGコメント管理", self.ng_comments, self.save_ng_comments)

	def manage_replace(self):
		ReplaceDialog(self, self.replace_dict, self.save_replace)

	def save_ng_users(self, ng_users):
		self.ng_users = ng_users
		save_ng_settings(self.ng_users, self.ng_comments)

	def save_ng_comments(self, ng_comments):
		self.ng_comments = ng_comments
		save_ng_settings(self.ng_users, self.ng_comments)

	def save_replace(self, replace_dict):
		self.replace_dict = replace_dict
		save_replace_settings(self.replace_dict)

	def update_channel_listbox(self):
		self.channel_combobox['values'] = self.channel_list

	def add_channel(self):
		channel = self.channel_entry.get().strip()
		if channel and channel not in self.channel_list:
			self.channel_list.append(channel)
			self.update_channel_listbox()
			save_settings(self.username, self.token, self.channel_list, self.credentials_path)

	def remove_channel(self):
		channel = self.channel_combobox.get()
		if channel in self.channel_list:
			self.channel_list.remove(channel)
			self.update_channel_listbox()
			save_settings(self.username, self.token, self.channel_list, self.credentials_path)

	def connect_channel(self):
		channel = self.channel_combobox.get()
		if channel:
			if self.connected_channel:
				self.disconnect_channel()
			self.connected_channel = channel
			self.bot = TwitchBot(
				username=self.username,
				token=self.token,
				channel=self.connected_channel,
				app=self,
				loop=loop
			)
			asyncio.run_coroutine_threadsafe(self.bot.start(), loop)
			self.status_label.config(text=f"{channel}に接続中")

	def disconnect_channel(self):
		if self.connected_channel:
			if self.bot:
				asyncio.run_coroutine_threadsafe(self.bot.close(), loop)
			self.connected_channel = None
			self.status_label.config(text="")
			
			# 読み上げバッファをクリア
			self.tts_manager.clear_queue()
			
			# 現在再生中の音声を停止
			pygame.mixer.music.stop()

	def get_engine(self, engine_name):
		if any(char in engine_name for char in ["ずんだもん", "四国めたん", "春日部つむぎ", "冥鳴ひまり"]):
			speaker_id = self.speaker_map.get(engine_name, None)
			if speaker_id is not None:
				return VoiceBox(speaker=speaker_id)
		elif engine_name == "Google読み上げ":
			return GoogleTTS(self.credentials_path)
		else:
			return BouyomiChan()


	async def on_message(self, message):
		if message.author.name in self.ng_users or any(ng_comment in message.content for ng_comment in self.ng_comments):
			return
		message_text = message.content
		if self.name_var.get():
			message_text = f"{message.author.name}：{message_text}"

		for key, value in self.replace_dict.items():
			message_text = re.sub(key, value, message_text)

		# スタンプをまとめる処理
		if self.stamp_var.get():
			message_text = self.summarize_stamps(message_text)

		if not self.japanese_combobox.get() or not self.other_combobox.get():
			return

		print(message_text)
		volume = self.volume_scale.get()
		speed = self.speed_scale.get()
		language_code = "ja-JP" if self.is_japanese(message_text) else self.detect_language(message_text)

		self.tts_manager.add_to_queue(message_text, volume, speed, language_code)

	def summarize_stamps(self, text):
		words = text.split()
		word_counts = Counter(words)
		
		summarized_words = []
		for word, count in word_counts.items():
			if count > 1:
				summarized_words.append(f"{word}、{count}個")
			else:
				summarized_words.append(word)
		
		return ' '.join(summarized_words)

	def is_japanese(self, text):
		return any(ord(char) >= 0x3040 and ord(char) <= 0x30FF or ord(char) >= 0x4E00 and ord(char) <= 0x9FFF for char in text)

	def detect_language(self, text):
		try:
			return detect(text)
		except LangDetectException:
			return "en"  # デフォルトは英語

	def update_engines(self, event=None):
		japanese_engine = self.get_engine(self.japanese_combobox.get())
		other_engine = self.get_engine(self.other_combobox.get())
		self.tts_manager = TTSManager(
			japanese_engine=japanese_engine,
			other_engine=other_engine
		)
		# TTSキューの処理を再開始
		asyncio.run_coroutine_threadsafe(self.tts_manager.process_queue(), loop)
	
	def get_settings(self):
		dialog = SettingsDialog(self, self.username, self.token, self.credentials_path)
		self.wait_window(dialog)
		self.username, self.token, self.credentials_path = dialog.username, dialog.token, dialog.credentials_path
		save_settings(self.username, self.token, self.channel_list, self.credentials_path)

	def on_closing(self):
		if self.connected_channel:
			self.disconnect_channel()
		for engine in [self.tts_manager.japanese_engine, self.tts_manager.other_engine]:
			if isinstance(engine, VoiceBox):
				asyncio.run_coroutine_threadsafe(engine.close(), loop)
			elif hasattr(engine, 'close'):  # 他のエンジンタイプにもcloseメソッドがある場合
				asyncio.run_coroutine_threadsafe(engine.close(), loop)
		self.destroy()
		loop.call_soon_threadsafe(loop.stop)

class TwitchBot(commands.Bot):
	def __init__(self, username, token, channel, app, loop):
		super().__init__(token=token, prefix='!', initial_channels=[channel])
		self.app = app
		self.loop = loop

	async def event_ready(self):
		print(f'Logged in as | {self.nick}')
		self.app.status_label.config(text=f"Logged in as {self.nick}")

	async def event_message(self, message):
		if message.author.name.lower() == self.nick.lower():
			return
		print(f'{message.author.name}: {message.content}')
		await self.app.on_message(message)

	async def close(self):
		await super().close()
		self.app.status_label.config(text="Disconnected")

def start_loop(loop):
	asyncio.set_event_loop(loop)
	loop.run_forever()

# メインの実行部分
if __name__ == "__main__":
	loop = asyncio.new_event_loop()
	threading.Thread(target=start_loop, args=(loop,), daemon=True).start()

	app = Application()
	app.mainloop()

	loop.call_soon_threadsafe(loop.stop)

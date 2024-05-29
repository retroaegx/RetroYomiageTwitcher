import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import configparser
import os
import asyncio
import threading
import requests
from twitchio.ext import commands
from pydub import AudioSegment
from pydub.playback import play
import io
import re
import aiohttp
from google.cloud import texttospeech
from google.oauth2 import service_account
from langdetect import detect, LangDetectException

# ファイル名の定義
CONFIG_FILE = "settings.ini"
NG_FILE = "ng_settings.ini"
REPLACE_FILE = "replace_settings.ini"

# configparserの設定
config = configparser.ConfigParser()
ng_config = configparser.ConfigParser()
replace_config = configparser.ConfigParser()

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

# 読み上げエンジンのクラス
class BouyomiChan:
	def __init__(self, host='localhost', port=50080):
		self.url = f"http://{host}:{port}/talk"

	def speak(self, text, volume, speed):
		payload = {
			'text': text,
			'volume': volume,
			'speed': speed
		}
		try:
			response = requests.post(self.url, data=payload)
			response.raise_for_status()
		except requests.exceptions.RequestException as e:
			print(f"Error: {e}")

class VoiceBox:
	def __init__(self, speaker):
		self.url = "http://localhost:50021"
		self.speaker = speaker

	async def speak(self, text, volume, speed):
		try:
			# 音声合成クエリの作成
			async with aiohttp.ClientSession() as session:
				async with session.post(f"{self.url}/audio_query", params={"text": text, "speaker": self.speaker}) as resp:
					resp.raise_for_status()
					query_data = await resp.json()

				# 音量と速度の設定
				query_data["volumeScale"] = volume / 100.0
				query_data["speedScale"] = speed / 100.0

				# 音声合成
				async with session.post(f"{self.url}/synthesis", params={"speaker": self.speaker}, json=query_data) as resp:
					resp.raise_for_status()
					audio_data = await resp.read()

				# 音声データを直接再生
				audio_segment = AudioSegment.from_file(io.BytesIO(audio_data), format="wav")
				play(audio_segment)

		except requests.exceptions.RequestException as e:
			print(f"Error: {e}")

class GoogleTTS:
	def __init__(self, credentials_path):
		self.credentials_path = credentials_path

	def speak(self, text, volume, speed, language_code):
		credentials = service_account.Credentials.from_service_account_file(self.credentials_path)
		client = texttospeech.TextToSpeechClient(credentials=credentials)

		input_text = texttospeech.SynthesisInput(text=text)
		voice = texttospeech.VoiceSelectionParams(
			language_code=language_code, ssml_gender=texttospeech.SsmlVoiceGender.NEUTRAL)
		audio_config = texttospeech.AudioConfig(
			audio_encoding=texttospeech.AudioEncoding.MP3,
			speaking_rate=speed / 100.0,
			volume_gain_db=volume / 100.0)

		response = client.synthesize_speech(
			input=input_text, voice=voice, audio_config=audio_config)

		audio_content = response.audio_content
		audio_segment = AudioSegment.from_file(io.BytesIO(audio_content), format="mp3")
		play(audio_segment)

def get_speaker_map():
	response = requests.get("http://localhost:50021/speakers")
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

		# 日本語とそれ以外のラベルとリストボックス
		tk.Label(frame, text="日本語:").grid(row=5, column=0, pady=5, sticky=tk.W)
		self.japanese_combobox = ttk.Combobox(frame, values=list(self.speaker_map.keys()) + ["棒読みちゃん", "Google読み上げ"], state="readonly")
		self.japanese_combobox.grid(row=5, column=1, pady=5, padx=5, sticky=tk.W)

		tk.Label(frame, text="それ以外:").grid(row=6, column=0, pady=5, sticky=tk.W)
		self.other_combobox = ttk.Combobox(frame, values=list(self.speaker_map.keys()) + ["棒読みちゃん", "Google読み上げ"], state="readonly")
		self.other_combobox.grid(row=6, column=1, pady=5, padx=5, sticky=tk.W)

		# 配信者のコメントを読み上げるチェックボックスを削除

		# チェックボックスを一番下に配置
		self.name_checkbutton.grid(row=7, column=0, columnspan=2, pady=5, sticky=tk.W)

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

	async def on_message(self, message):
		if message.author.name in self.ng_users or any(ng_comment in message.content for ng_comment in self.ng_comments):
			return
		message_text = message.content
		if self.name_var.get():
			message_text = f"{message.author.name}：{message_text}"

		for key, value in self.replace_dict.items():
			message_text = re.sub(key, value, message_text)

		if not self.japanese_combobox.get() or not self.other_combobox.get():
			return

		print(message_text)
		volume = self.volume_scale.get()
		speed = self.speed_scale.get()
		language_code = "ja-JP" if self.is_japanese(message_text) else self.detect_language(message_text)
		engine = self.japanese_combobox.get() if language_code == "ja-JP" else self.other_combobox.get()
		
		if "VOICEBOX" in engine:
			speaker_id = self.speaker_map.get(engine, None)
			if speaker_id is not None:
				await VoiceBox(speaker=speaker_id).speak(message_text, volume, speed)
		elif engine == "Google読み上げ":
			GoogleTTS(self.credentials_path).speak(message_text, volume, speed, language_code)
		else:
			BouyomiChan().speak(message_text, volume, speed)

	def is_japanese(self, text):
		# 簡単な日本語判定（ひらがな、カタカナ、漢字を含むかどうか）
		return any(ord(char) >= 0x3040 and ord(char) <= 0x30FF or ord(char) >= 0x4E00 and ord(char) <= 0x9FFF for char in text)

	def detect_language(self, text):
		try:
			return detect(text)
		except LangDetectException:
			return "en"  # デフォルトは英語

	def get_settings(self):
		dialog = SettingsDialog(self, self.username, self.token, self.credentials_path)
		self.wait_window(dialog)
		self.username, self.token, self.credentials_path = dialog.username, dialog.token, dialog.credentials_path
		save_settings(self.username, self.token, self.channel_list, self.credentials_path)

	def on_closing(self):
		if self.connected_channel:
			self.disconnect_channel()
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

if __name__ == "__main__":
	loop = asyncio.new_event_loop()
	threading.Thread(target=start_loop, args=(loop,), daemon=True).start()

	app = Application()
	app.mainloop()

	loop.call_soon_threadsafe(loop.stop)

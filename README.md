Twitchのコメントを日本語とそれ以外で読み上げソフトを切り替えることが出来るツールです。  
VOICEROID、Bouyomichanを使いたいけど、他の言語が混ざると読み上げられない問題を解決します。(日本語の中にマラヤーラム語が飛んできても、GoogleTTSで読み上げるということが可能です。)  
対応としてはVOICEROID、Bouyomichan、GoogleTTSに対応しています。VOICEROID、Bouyomichanは読み上げ前にアプリの起動が必要です。  
Twitchのコメント受信にはoauthコードが必要です。アプリ起動時のリンクから取得してください。  
GoogleTTSはAPI Keyが必要です。こちらもアプリ起動時のリンクから取得してください。  
GCPの機能なので使いすぎると料金が課金されますが、100 万文字までは無料なので使い切る可能性が低いと思います。  
  
コンパイルメモ  
pip install nuitka  
nuitka --standalone --onefile --windows-disable-console RetroYomiageTwitcher.pyw


# ==========================================
# 創研無限問題作成機 (デプロイ・Drive連携版)
# ==========================================
import streamlit as st
import google.generativeai as genai
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
import io
import json
import re
import time

# --- 設定と認証 ---
st.set_page_config(page_title="創研無限問題作成機", page_icon="🎓", layout="wide")

# 1. APIキーの取得 (Streamlit Secretsから)
try:
    api_key = st.secrets["GOOGLE_API_KEY"]
    genai.configure(api_key=api_key)
except:
    st.error("Secretsに GOOGLE_API_KEY が設定されていません。")
    st.stop()

# 2. Google Drive APIの認証 (Streamlit Secretsから)
SCOPES = ['https://www.googleapis.com/auth/drive']
FOLDER_NAME = "Soken_Quiz_Data" # ドライブ内のフォルダ名

def get_drive_service():
    try:
        # SecretsのJSON情報を辞書として取得
        key_dict = dict(st.secrets["gcp_service_account"])
        creds = service_account.Credentials.from_service_account_info(key_dict, scopes=SCOPES)
        return build('drive', 'v3', credentials=creds)
    except Exception as e:
        st.error(f"Google Drive接続エラー: {e}")
        return None

# --- デザイン ---
def apply_rich_css():
    st.markdown("""
    <style>
    .stApp { background-color: #f4f6f9; }
    .main-title {
        font-family: 'Helvetica Neue', Arial, sans-serif;
        font-size: 3em;
        font-weight: 800;
        background: linear-gradient(45deg, #4B0082 0%, #0000CD 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        text-align: center;
        text-shadow: 1px 1px 2px rgba(0,0,0,0.1);
        padding: 10px;
        margin-bottom: 20px;
    }
    .question-box {
        background: #ffffff;
        padding: 30px;
        margin: 20px 0;
        font-size: 1.3em;
        font-weight: bold;
        color: #333;
        border-radius: 12px;
        border-left: 8px solid #6a11cb;
        box-shadow: 0 4px 15px rgba(0,0,0,0.05);
    }
    .feedback-box {
        padding: 20px; border-radius: 12px; margin-top: 15px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.05); animation: fadeIn 0.5s;
    }
    .feedback-correct { background-color: #d4edda; border-left: 5px solid #28a745; color: #155724; }
    .feedback-wrong { background-color: #f8d7da; border-left: 5px solid #dc3545; color: #721c24; }
    @keyframes fadeIn { from { opacity: 0; transform: translateY(5px); } to { opacity: 1; transform: translateY(0); } }
    </style>
    """, unsafe_allow_html=True)

# --- Drive操作関数 ---
def get_folder_id(service, folder_name):
    # フォルダIDを探す
    query = f"name = '{folder_name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    results = service.files().list(q=query, fields="files(id, name)").execute()
    files = results.get('files', [])
    if not files:
        return None
    return files[0]['id']

def list_pdf_files(service, folder_id):
    # フォルダ内のPDF一覧取得
    query = f"'{folder_id}' in parents and mimeType = 'application/pdf' and trashed = false"
    results = service.files().list(q=query, fields="files(id, name)").execute()
    return results.get('files', [])

def upload_file_to_drive(service, folder_id, file_obj, file_name):
    # ファイルアップロード
    file_metadata = {'name': file_name, 'parents': [folder_id]}
    media = MediaIoBaseUpload(file_obj, mimetype='application/pdf', resumable=True)
    file = service.files().create(body=file_metadata, media_body=media, fields='id').execute()
    return file.get('id')

def download_file_from_drive(service, file_id):
    # ファイルダウンロード（メモリ上に）
    request = service.files().get_media(fileId=file_id)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while done is False:
        status, done = downloader.next_chunk()
    fh.seek(0)
    return fh

# --- Gemini関連関数 (これまでのロジックを流用) ---
def upload_to_gemini(file_obj, mime_type="application/pdf"):
    # StreamlitのUploadedFileやBytesIOをGeminiに渡すには、一度ローカルの一時ファイルにするのが確実
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(file_obj.getvalue() if hasattr(file_obj, 'getvalue') else file_obj.read())
        tmp_path = tmp.name
    
    file = genai.upload_file(tmp_path, mime_type=mime_type)
    return file

def wait_for_files_active(files):
    with st.spinner('AIが資料を読み込んでいます...'):
        for name in (file.name for file in files):
            file = genai.get_file(name)
            while file.state.name == "PROCESSING":
                time.sleep(2)
                file = genai.get_file(name)
            if file.state.name != "ACTIVE":
                raise Exception(f"File {file.name} failed to process")

def find_working_model():
    # 簡易版：Flashを優先
    return "models/gemini-1.5-flash"

def generate_with_retry(model_name, contents):
    model = genai.GenerativeModel(model_name, generation_config={"response_mime_type": "application/json"})
    try:
        return model.generate_content(contents)
    except:
        return None

def extract_json_robust(text):
    try: return json.loads(text)
    except: pass
    clean = re.sub(r"```json\s*|```", "", text).strip()
    try: return json.loads(clean)
    except: pass
    match = re.search(r'\[.*\]', text, re.DOTALL) or re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        try: return json.loads(match.group(0))
        except: pass
    return {}

def generate_quiz_batch(model_name, gemini_file, mode, history_list):
    count = 3
    avoid = "【重複禁止】:\\n" + "\\n".join(history_list[-30:]) if history_list else ""
    inst = "全て【記述式(論述)】" if mode == "記述問題" else "全て【4択】" if mode == "4択問題" else "記述と4択Mix"
    
    prompt = f"""
    この資料から学習用クイズを【{count}問】作成。
    条件: {inst}
    {avoid}
    JSON出力リスト形式:
    [ {{ "type": "choice/text", "question": "...", "options": [...], "answer": "...", "explanation": "..." }} ]
    """
    res = generate_with_retry(model_name, [gemini_file, prompt])
    if res:
        data = extract_json_robust(res.text)
        if isinstance(data, list) and data: return data
    
    # バックアップ（1問）
    prompt_single = f"クイズを1問作成。条件:{inst} {avoid} JSON出力。"
    res_s = generate_with_retry(model_name, [gemini_file, prompt_single])
    if res_s:
        d = extract_json_robust(res_s.text)
        if isinstance(d, dict): return [d]
    return []

def grade_answer_flexible(model_name, q, a, user_in):
    prompt = f"""
    採点してください。問題:{q} 模範解答:{a} 生徒回答:{user_in}
    一般知識も考慮し〇/△/×で評価。
    JSON出力: {{ "result": "〇/△/×", "score_percent": 数値, "feedback": "コメント" }}
    """
    res = generate_with_retry(model_name, prompt)
    if res:
        data = extract_json_robust(res.text)
        if "result" in data: return data
    return {"result": "×", "score_percent": 0, "feedback": "採点失敗"}

# ==========================================
# メイン画面処理
# ==========================================
def main():
    apply_rich_css()
    
    # セッション初期化
    if 'queue' not in st.session_state: st.session_state.queue = []
    if 'current' not in st.session_state: st.session_state.current = None
    if 'score' not in st.session_state: st.session_state.score = 0
    if 'total' not in st.session_state: st.session_state.total = 0
    if 'streak' not in st.session_state: st.session_state.streak = 0
    if 'answered' not in st.session_state: st.session_state.answered = False
    if 'result_data' not in st.session_state: st.session_state.result_data = None
    if 'history' not in st.session_state: st.session_state.history = []
    if 'input_key' not in st.session_state: st.session_state.input_key = 0
    if 'balloons_shown' not in st.session_state: st.session_state.balloons_shown = False
    if 'active_gemini_file' not in st.session_state: st.session_state.active_gemini_file = None
    if 'last_mode' not in st.session_state: st.session_state.last_mode = "記述問題"

    st.markdown('<div class="main-title">🎓 創研無限問題作成機</div>', unsafe_allow_html=True)

    # Drive接続
    drive_service = get_drive_service()
    if not drive_service:
        st.warning("⚠️ Google Driveに接続できませんでした。Secretsの設定を確認してください。")
        return

    # フォルダ確認
    folder_id = get_folder_id(drive_service, FOLDER_NAME)
    if not folder_id:
        st.error(f"Google Driveに '{FOLDER_NAME}' フォルダが見つかりません。作成してサービスアカウントに共有してください。")
        return

    # サイドバー
    with st.sidebar:
        st.header("📚 ライブラリ")
        
        # ファイル一覧取得
        files = list_pdf_files(drive_service, folder_id)
        file_map = {f['name']: f['id'] for f in files}
        options = ["(新規アップロード)"] + list(file_map.keys())
        
        selected = st.selectbox("学習資料を選択", options)
        
        if selected == "(新規アップロード)":
            uploaded = st.file_uploader("PDF追加", type=["pdf"])
            title = st.text_input("タイトル入力")
            if uploaded and title and st.button("保存"):
                with st.spinner("Driveに保存中..."):
                    safe_name = re.sub(r'[\\/:*?"<>|]+', '', title) + ".pdf"
                    upload_file_to_drive(drive_service, folder_id, uploaded, safe_name)
                    st.success("保存しました！")
                    time.sleep(1)
                    st.rerun()
        else:
            # 既存ファイル選択時の処理
            file_id = file_map[selected]
            # 前回と違うファイルなら読み込み直し
            if 'current_file_id' not in st.session_state or st.session_state.current_file_id != file_id:
                with st.spinner("クラウドから資料を読み込んでいます..."):
                    pdf_data = download_file_from_drive(drive_service, file_id)
                    gemini_file = upload_to_gemini(pdf_data)
                    wait_for_files_active([gemini_file])
                    
                    st.session_state.active_gemini_file = gemini_file
                    st.session_state.current_file_id = file_id
                    st.session_state.queue = [] # リセット
                    st.session_state.history = []
                    st.success(f"『{selected}』を読み込みました！")

        st.markdown("---")
        mode = st.radio("出題モード", ["記述問題", "4択問題", "おまかせ (Mix)"])
        if mode != st.session_state.last_mode:
            st.session_state.queue = []
            st.session_state.last_mode = mode

        st.metric("スコア", f"{st.session_state.score} / {st.session_state.total}")

    # メインロジック
    if st.session_state.active_gemini_file:
        # 問題補充
        if not st.session_state.queue and not st.session_state.current:
            with st.spinner("⚡ 問題を作成中..."):
                new_q = generate_quiz_batch("models/gemini-1.5-flash", st.session_state.active_gemini_file, mode, st.session_state.history)
                if new_q:
                    st.session_state.queue.extend(new_q)
                    for q in new_q: st.session_state.history.append(q['question'])
                    st.rerun()
                else:
                    st.error("作成失敗。")

        # 次へ
        if not st.session_state.current and st.session_state.queue:
            st.session_state.current = st.session_state.queue.pop(0)
            st.session_state.answered = False
            st.session_state.result_data = None
            st.session_state.input_key += 1
            st.session_state.balloons_shown = False
            st.rerun()

        # 表示
        if st.session_state.current:
            q = st.session_state.current
            st.markdown(f'<div class="question-box">Q. {q["question"]}</div>', unsafe_allow_html=True)

            if q['type'] == 'choice':
                with st.form("choice"):
                    sel = st.radio("選択", q.get('options', []))
                    if st.form_submit_button("回答"):
                        st.session_state.answered = True
                        st.session_state.total += 1
                        if sel == q['answer']:
                            st.session_state.score += 1
                            st.session_state.result_data = {"result": "〇", "feedback": "正解！"}
                        else:
                            st.session_state.result_data = {"result": "×", "feedback": "不正解"}
                        st.rerun()
            else:
                with st.form("text"):
                    txt = st.text_area("記述回答", key=f"txt_{st.session_state.input_key}")
                    if st.form_submit_button("採点"):
                        with st.spinner("採点中..."):
                            res = grade_answer_flexible("models/gemini-1.5-flash", q['question'], q['answer'], txt)
                            st.session_state.result_data = res
                            st.session_state.answered = True
                            st.session_state.total += 1
                            if res['result'] == "〇": st.session_state.score += 1
                            st.rerun()

            if st.session_state.answered and st.session_state.result_data:
                res = st.session_state.result_data
                cls = "correct" if res['result']=="〇" else "wrong"
                st.markdown(f'<div class="feedback-box feedback-{cls}">判定: {res["result"]} - {res["feedback"]}</div>', unsafe_allow_html=True)
                
                if res['result'] == "〇" and not st.session_state.balloons_shown:
                    st.balloons()
                    st.session_state.balloons_shown = True
                
                with st.expander("解説"):
                    st.write(q['answer'])
                    st.write(q['explanation'])
                
                c1, c2 = st.columns(2)
                if c1.button("次へ"):
                    st.session_state.current = None
                    st.session_state.answered = False
                    st.rerun()
                if res['result'] != "〇":
                    if c2.button("やり直す"):
                        st.session_state.answered = False
                        st.session_state.result_data = None
                        st.rerun()
    else:
        st.info("👈 左から資料を選択してください")

if __name__ == "__main__":
    main()

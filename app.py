# ==========================================
# 創研無限問題作成機 (完成・正答率＆連勝演出強化版)
# ==========================================
import streamlit as st
import google.generativeai as genai
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
import io
import json
import re
import time
import tempfile

# --- 設定と認証 ---
st.set_page_config(page_title="創研無限問題作成機", page_icon="🎓", layout="wide")

# 1. APIキー
try:
    api_key = st.secrets["GOOGLE_API_KEY"]
    genai.configure(api_key=api_key)
except:
    st.error("Secretsに GOOGLE_API_KEY が設定されていません。")
    st.stop()

# 2. Google Drive API
SCOPES = ['https://www.googleapis.com/auth/drive.readonly']
FOLDER_ID = "1KULNeMIXdpxhvrhcixZgXig6RZMsusxC" # あなたのID

# --- モデル自動選択 ---
def get_best_model():
    try:
        models = list(genai.list_models())
        available = [m.name for m in models if 'generateContent' in m.supported_generation_methods]
        for m in available:
            if "flash" in m.lower() and "1.5" in m: return m
        for m in available:
            if "pro" in m.lower() and "1.5" in m: return m
        return "models/gemini-pro"
    except:
        return "gemini-1.5-flash"

if 'use_model' not in st.session_state:
    st.session_state.use_model = get_best_model()

# --- Drive接続 ---
def get_drive_service():
    try:
        key_dict = dict(st.secrets["gcp_service_account"])
        creds = service_account.Credentials.from_service_account_info(key_dict, scopes=SCOPES)
        return build('drive', 'v3', credentials=creds)
    except Exception as e:
        st.error(f"Google Drive接続エラー: {e}")
        return None

# --- デザイン & アニメーション ---
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
    .question-box { background: #ffffff; padding: 30px; margin: 20px 0; font-size: 1.3em; font-weight: bold; border-radius: 12px; border-left: 8px solid #6a11cb; box-shadow: 0 4px 15px rgba(0,0,0,0.05); color: #333; }
    .feedback-box { padding: 20px; border-radius: 12px; margin-top: 15px; box-shadow: 0 2px 8px rgba(0,0,0,0.05); animation: fadeIn 0.5s; }
    .feedback-correct { background-color: #d4edda; border-left: 5px solid #28a745; color: #155724; }
    .feedback-wrong { background-color: #f8d7da; border-left: 5px solid #dc3545; color: #721c24; }
    
    /* 派手な表彰アニメーション */
    @keyframes popIn {
        0% { transform: scale(0); opacity: 0; }
        60% { transform: scale(1.1); opacity: 1; }
        100% { transform: scale(1); }
    }
    .celebration-banner {
        background: linear-gradient(90deg, #FFD700, #FFA500, #FFD700);
        color: #fff;
        padding: 20px;
        border-radius: 15px;
        text-align: center;
        font-size: 2em;
        font-weight: bold;
        text-shadow: 2px 2px 4px rgba(0,0,0,0.3);
        margin: 20px 0;
        animation: popIn 0.8s cubic-bezier(0.68, -0.55, 0.27, 1.55);
        box-shadow: 0 0 20px rgba(255, 215, 0, 0.6);
        border: 3px solid #fff;
    }
    
    @keyframes fadeIn { from { opacity: 0; transform: translateY(5px); } to { opacity: 1; transform: translateY(0); } }
    </style>
    """, unsafe_allow_html=True)

# --- 基本機能 ---
def list_pdf_files(service, folder_id):
    try:
        query = f"'{folder_id}' in parents and mimeType = 'application/pdf' and trashed = false"
        results = service.files().list(q=query, fields="files(id, name)", orderBy="name").execute()
        return results.get('files', [])
    except: return []

def download_file_from_drive(service, file_id):
    request = service.files().get_media(fileId=file_id)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while done is False:
        status, done = downloader.next_chunk()
    fh.seek(0)
    return fh

def upload_to_gemini(file_obj, mime_type="application/pdf"):
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

# --- 生成ロジック ---
def generate_with_retry(model_name, contents):
    safety_settings = [
        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
    ]
    model = genai.GenerativeModel(model_name=model_name, generation_config={"response_mime_type": "application/json"}, safety_settings=safety_settings)
    try: return model.generate_content(contents)
    except: return None

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
    出力形式(JSONリスト):
    [ {{ "type": "choice/text", "question": "...", "options": [...], "answer": "...", "explanation": "..." }} ]
    """
    res = generate_with_retry(model_name, [gemini_file, prompt])
    if res:
        data = extract_json_robust(res.text)
        if isinstance(data, list) and data: return data
    
    prompt_single = f"クイズを1問作成。条件:{inst} {avoid} JSON出力。"
    res_s = generate_with_retry(model_name, [gemini_file, prompt_single])
    if res_s:
        d = extract_json_robust(res_s.text)
        if isinstance(d, dict): return [d]
    return []

def grade_answer_flexible(model_name, q, a, user_in):
    prompt = f"""
    採点。問題:{q} 模範:{a} 回答:{user_in}
    〇/△/×で評価。JSON:{{ "result": "...", "score_percent": 0, "feedback": "..." }}
    """
    res = generate_with_retry(model_name, prompt)
    if res:
        data = extract_json_robust(res.text)
        if "result" in data: return data
    return {"result": "×", "score_percent": 0, "feedback": "採点失敗"}

# ==========================================
# メイン画面
# ==========================================
def main():
    apply_rich_css()
    # セッション初期化
    if 'queue' not in st.session_state: st.session_state.queue = []
    if 'current' not in st.session_state: st.session_state.current = None
    if 'score' not in st.session_state: st.session_state.score = 0
    if 'total' not in st.session_state: st.session_state.total = 0
    if 'streak' not in st.session_state: st.session_state.streak = 0 # 連続正解数
    if 'answered' not in st.session_state: st.session_state.answered = False
    if 'result_data' not in st.session_state: st.session_state.result_data = None
    if 'history' not in st.session_state: st.session_state.history = []
    if 'input_key' not in st.session_state: st.session_state.input_key = 0
    if 'balloons_shown' not in st.session_state: st.session_state.balloons_shown = False
    if 'active_gemini_file' not in st.session_state: st.session_state.active_gemini_file = None
    if 'last_mode' not in st.session_state: st.session_state.last_mode = "記述問題"

    st.markdown('<div class="main-title">🎓 創研無限問題作成機</div>', unsafe_allow_html=True)

    drive_service = get_drive_service()
    if not drive_service: return

    # --- サイドバー (スコア・正答率・連勝) ---
    with st.sidebar:
        st.header("📊 成績ボード")
        
        # 1. スコア表示
        st.metric("現在のスコア", f"{st.session_state.score} / {st.session_state.total}")
        
        # 2. 正答率計算
        if st.session_state.total > 0:
            accuracy = (st.session_state.score / st.session_state.total) * 100
        else:
            accuracy = 0.0
        st.metric("正答率", f"{accuracy:.1f}%")

        # 3. 連続正解数 (連勝)
        st.metric("連続正解", f"{st.session_state.streak} 連勝中🔥")

        st.markdown("---")
        st.header("📚 ライブラリ")
        if st.button("🔄 リスト更新"): st.rerun()

        files = list_pdf_files(drive_service, FOLDER_ID)
        file_map = {f['name']: f['id'] for f in files}
        options = ["(選択してください)"] + list(file_map.keys())
        selected = st.selectbox("学習する資料を選択", options)
        
        if selected != "(選択してください)":
            file_id = file_map[selected]
            if 'current_file_id' not in st.session_state or st.session_state.current_file_id != file_id:
                with st.spinner("クラウドから資料を読み込んでいます..."):
                    pdf_data = download_file_from_drive(drive_service, file_id)
                    gemini_file = upload_to_gemini(pdf_data)
                    wait_for_files_active([gemini_file])
                    st.session_state.active_gemini_file = gemini_file
                    st.session_state.current_file_id = file_id
                    st.session_state.queue = [] 
                    st.session_state.history = []
                    # ファイル変更時に連勝などはリセットしない（継続して学習できるよう）
                    st.success(f"『{selected}』を読み込みました！")

        st.markdown("---")
        mode = st.radio("出題モード", ["記述問題", "4択問題", "おまかせ (Mix)"])
        if mode != st.session_state.last_mode:
            st.session_state.queue = []
            st.session_state.last_mode = mode

    # メインロジック
    if st.session_state.active_gemini_file:
        # 問題補充
        if not st.session_state.queue and not st.session_state.current:
            with st.spinner("⚡ 問題を作成中..."):
                new_q = generate_quiz_batch(st.session_state.use_model, st.session_state.active_gemini_file, mode, st.session_state.history)
                if new_q:
                    st.session_state.queue.extend(new_q)
                    for q in new_q: st.session_state.history.append(q['question'])
                    st.rerun()
                else:
                    st.error("作成失敗。もう一度試してください。")

        # 次の問題へ
        if not st.session_state.current and st.session_state.queue:
            st.session_state.current = st.session_state.queue.pop(0)
            st.session_state.answered = False
            st.session_state.result_data = None
            st.session_state.input_key += 1
            st.session_state.balloons_shown = False
            st.rerun()

        # 問題表示
        if st.session_state.current:
            q = st.session_state.current
            st.markdown(f'<div class="question-box">Q. {q["question"]}</div>', unsafe_allow_html=True)
            
            # --- 回答処理 ---
            if q['type'] == 'choice':
                with st.form("choice"):
                    sel = st.radio("選択", q.get('options', []) or ["(選択肢エラー)"])
                    if st.form_submit_button("回答"):
                        st.session_state.answered = True
                        st.session_state.total += 1
                        if sel == q.get('answer', ''):
                            st.session_state.score += 1
                            st.session_state.streak += 1 # 連勝+1
                            st.session_state.result_data = {"result": "〇", "feedback": "正解！"}
                        else:
                            st.session_state.streak = 0 # 連勝ストップ
                            st.session_state.result_data = {"result": "×", "feedback": "不正解"}
                        st.rerun()
            else:
                with st.form("text"):
                    txt = st.text_area("記述回答", key=f"txt_{st.session_state.input_key}")
                    if st.form_submit_button("採点"):
                        with st.spinner("採点中..."):
                            res = grade_answer_flexible(st.session_state.use_model, q['question'], q.get('answer', '模範解答なし'), txt)
                            st.session_state.result_data = res
                            st.session_state.answered = True
                            st.session_state.total += 1
                            if res['result'] == "〇": 
                                st.session_state.score += 1
                                st.session_state.streak += 1 # 連勝+1
                            else:
                                st.session_state.streak = 0 # 連勝ストップ
                            st.rerun()
            
            # --- 結果表示 & お祝い演出 ---
            if st.session_state.answered and st.session_state.result_data:
                res = st.session_state.result_data
                cls = "correct" if res['result']=="〇" else "wrong"
                
                # ★ 派手な表彰ロジック (5の倍数の連勝時)
                current_streak = st.session_state.streak
                if res['result'] == "〇" and current_streak > 0 and current_streak % 5 == 0:
                    if not st.session_state.balloons_shown:
                        st.markdown(f"""
                        <div class="celebration-banner">
                        🎉 おめでとう！ {current_streak} 問連続正解！ 🏆
                        </div>
                        """, unsafe_allow_html=True)
                        st.balloons() # 風船を飛ばす
                        st.session_state.balloons_shown = True
                
                # 通常の正解風船（1問ごとのプチお祝い）は、連勝表彰がない時だけ飛ばす（うるさくなりすぎないよう）
                elif res['result'] == "〇" and not st.session_state.balloons_shown:
                    # st.balloons() # 毎回飛ばしたい場合はコメントアウトを外す
                    st.session_state.balloons_shown = True

                # 結果ボックス表示
                st.markdown(f'<div class="feedback-box feedback-{cls}">判定: {res["result"]} - {res["feedback"]}</div>', unsafe_allow_html=True)
                
                with st.expander("解説"):
                    st.write(f"**正解:** {q.get('answer', '（データなし）')}")
                    st.write(f"**解説:** {q.get('explanation', '（AIが解説を作成しませんでした）')}")

                c1, c2 = st.columns(2)
                if c1.button("次へ"):
                    st.session_state.current = None
                    st.session_state.answered = False
                    st.rerun()
                if res['result'] != "〇":
                    if c2.button("やり直す"):
                        # やり直すときは連勝カウントは戻さない（厳しい仕様）か、戻すか
                        # ここでは「やり直しても連勝は途切れたまま」にしています
                        st.session_state.answered = False
                        st.session_state.result_data = None
                        st.rerun()
    else:
        st.info("👈 左から資料を選択してください")

if __name__ == "__main__":
    main()

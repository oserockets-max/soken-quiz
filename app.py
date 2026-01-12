# ==========================================
# 創研無限問題作成機 (エラー診断モード)
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

# --- 設定 ---
st.set_page_config(page_title="診断モード", page_icon="🩺", layout="wide")

# Secretsチェック
if "gcp_service_account" not in st.secrets:
    st.error("❌ Secretsの設定が読み込めません。Manage app > Settings > Secrets を確認してください。")
    st.stop()

# --- 認証情報の表示（重要）---
key_dict = dict(st.secrets["gcp_service_account"])
robot_email = key_dict.get("client_email", "不明")

st.sidebar.header("🩺 診断情報")
st.sidebar.info(f"🤖 ロボットの正体:\n{robot_email}")
st.sidebar.warning("👆 Googleドライブの共有設定で、このメールアドレスが「編集者」になっているか確認してください！")

# --- 設定と認証 ---
try:
    api_key = st.secrets["GOOGLE_API_KEY"]
    genai.configure(api_key=api_key)
except:
    st.error("❌ APIキーの設定エラー")

# Google Drive API
SCOPES = ['https://www.googleapis.com/auth/drive']
FOLDER_ID = "1KULNeMIXdpxhvrhcixZgXig6RZMsusxC" # あなたが設定したID

def get_drive_service():
    try:
        creds = service_account.Credentials.from_service_account_info(key_dict, scopes=SCOPES)
        return build('drive', 'v3', credentials=creds)
    except Exception as e:
        st.error(f"❌ Google Drive接続エラー: {e}")
        return None

# --- ファイル操作 ---
def upload_file_to_drive(service, folder_id, file_obj, file_name):
    try:
        file_metadata = {'name': file_name, 'parents': [folder_id]}
        media = MediaIoBaseUpload(file_obj, mimetype='application/pdf', resumable=True)
        file = service.files().create(body=file_metadata, media_body=media, fields='id').execute()
        return file.get('id')
    except Exception as e:
        # ここで本当のエラーを表示します
        st.error(f"🛑 アップロード失敗！エラー詳細:\n{e}")
        raise e

def list_pdf_files(service, folder_id):
    try:
        query = f"'{folder_id}' in parents and mimeType = 'application/pdf' and trashed = false"
        results = service.files().list(q=query, fields="files(id, name)").execute()
        return results.get('files', [])
    except Exception as e:
        st.error(f"🛑 フォルダ読み込み失敗！IDが間違っているか、権限がありません。\nエラー詳細: {e}")
        return []

# --- デザイン ---
def apply_rich_css():
    st.markdown("""<style>.stApp { background-color: #fff0f0; }</style>""", unsafe_allow_html=True)

# --- メイン処理 ---
def main():
    apply_rich_css()
    st.title("🩺 エラー診断モード")
    
    drive_service = get_drive_service()
    if not drive_service: return

    # フォルダチェック
    st.write(f"📁 ターゲットフォルダID: `{FOLDER_ID}`")
    st.write("フォルダの中身を確認中...")
    
    files = list_pdf_files(drive_service, FOLDER_ID)
    if files:
        st.success(f"✅ 成功！ {len(files)} 個のファイルが見えました。接続は正常です。")
        st.write([f['name'] for f in files])
    else:
        st.warning("⚠️ ファイルが見つからないか、エラーが発生しています。")

    st.markdown("---")
    st.subheader("テストアップロード")
    uploaded = st.file_uploader("適当なPDFをアップロードしてテストしてください", type=["pdf"])
    
    if uploaded and st.button("アップロード実験"):
        with st.spinner("送信中..."):
            safe_name = "TEST_" + uploaded.name
            try:
                upload_file_to_drive(drive_service, FOLDER_ID, uploaded, safe_name)
                st.balloons()
                st.success("🎉 おめでとうございます！エラーは解決しました！")
                st.info("このコードを元のコードに戻せば完成です。")
            except:
                st.error("👆 上のエラーメッセージを確認してください")

if __name__ == "__main__":
    main()

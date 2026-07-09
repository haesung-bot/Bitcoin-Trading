"""
telegram_welcome_bot.py (용량 최적화 및 완전 자동화 버전)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. 그룹방: 신규 멤버 환영 및 버튼식 필터링 질문
2. 개인방: 구글 드라이브 다운로드 링크 + Render 연동 7일 체험 코드 자동 발급
3. FAQ: faq.json 기반 키워드 무인 자동 응답
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import logging
import threading
import json
import requests
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    ContextTypes,
    MessageHandler,
    CommandHandler,
    CallbackQueryHandler,
    filters,
)
from flask import Flask

# ─── [설정 항목] ───
BOT_TOKEN = os.environ.get("BOT_TOKEN", "여기에_실제_텔레그램_봇_토큰_입력")

# 내 Render 라이선스 서버 정보
LICENSE_SERVER_URL = "https://bitcoin-trading-1111.onrender.com"
ADMIN_SECRET = "a7b3c9d1e5f8g2h4i6j0k3l7m9qw"  # 서버 환경변수에 등록한 관리자 비번

# ⚠️ 여기에 프로그램 압축파일을 올린 구글 드라이브 또는 드롭박스 등의 다운로드 링크를 넣으세요!
PROGRAM_DOWNLOAD_URL = "https://github.com/haesung-bot/Bitcoin-Trading/releases/download/v1.0.0/pro.zip"

FAQ_FILE_PATH = "faq.json"

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── [1. 헬스체크용 Flask 서버] ───
flask_app = Flask(__name__)
@flask_app.route('/')
def home(): return "Telegram Bot is running!", 200
def run_health_server():
    port = int(os.environ.get("PORT", 10000))
    flask_app.run(host='0.0.0.0', port=port)

# ─── [2. FAQ 로드 함수] ───
def load_faq():
    if os.path.exists(FAQ_FILE_PATH):
        try:
            with open(FAQ_FILE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"FAQ 로드 실패: {e}")
    return []

# ─── [3. Render 서버 연동 7일 코드 발급 함수] ───
def generate_7day_code():
    url = f"{LICENSE_SERVER_URL}/admin/generate"
    headers = {
        "X-Admin-Secret": ADMIN_SECRET,
        "Content-Type": "application/json"
    }
    # 7일권 발급 (7일 = 604800초)
    body = {
        "note": "텔레그램 자동 유입 유저 (7일 체험)",
        "duration_seconds": 604800 
    }
    try:
        res = requests.post(url, headers=headers, json=body, timeout=10)
        if res.status_code == 200:
            data = res.json()
            if data.get("ok"):
                return data.get("code")
    except Exception as e:
        logger.error(f"라이선스 서버 통신 에러: {e}")
    return None

# ─── [4. 로직 구현: 그룹방 신규 멤버 환영] ───
async def greet_new_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for member in update.message.new_chat_members:
        if member.is_bot:
            continue
        
        name = member.full_name
        welcome_text = (
            f"안녕하세요, {name}님! 반가워요! 👋\n\n"
            f"혹시 Gate.io Binance Bybit OKX Bitget 거래소에서 안정적으로 수익을 내는\n"
            f" 자동매매 프로그램에 관심이 있으신가요?\n"
            f"아래 버튼을 눌러 답변해 주세요!"
        )
        keyboard = [
            [
                InlineKeyboardButton("네, 하고 싶어요! 🚀", callback_data="want_bot"),
                InlineKeyboardButton("아니요, 괜찮습니다 ❌", callback_data="no_thanks")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(welcome_text, reply_markup=reply_markup)

# ─── [5. 버튼 클릭 처리] ───
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    bot_username = context.bot.username

    if query.data == "want_bot":
        go_private_text = (
            "좋은 선택입니다! 👍\n"
            "아래 '개인 채팅방 가기' 버튼을 누른 후, "
            "화면 하단의 **[시작(Start)]** 버튼을 눌러주시면 "
            "즉시 프로그램 다운로드 링크와 7일 무료 라이선스 코드를 발급해 드립니다!"
        )
        keyboard = [[InlineKeyboardButton("💬 개인 채팅방 가기 (클릭)", url=f"https://t.me/{bot_username}?start=welcome")]]
        await query.edit_message_text(text=go_private_text, reply_markup=InlineKeyboardMarkup(keyboard))
        
    elif query.data == "no_thanks":
        await query.edit_message_text(text="알겠습니다! 편하게 방을 둘러보세요. 😊")

# ─── [6. 개인 챗방 진입 (/start) 시 파일 다운로드 링크 및 코드 전송] ───
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return

    await update.message.reply_text("안녕하세요! 자동매매 프로그램 배포 봇입니다. 🤖\n인증 및 링크 생성을 시작합니다...")

    # 1. 라이선스 코드를 Render 서버에서 즉시 받아옴
    license_code = generate_7day_code()
    
    if not license_code:
        await update.message.reply_text("⚠️ 현재 라이선스 서버 점검 중입니다. 잠시 후 다시 /start 를 입력해 주세요.")
        return

    # 2. 다운로드 가이드 및 라이선스 코드 안내 (버튼 결합)
    guide_text = (
        f"✅ **7일 무료 라이선스 및 프로그램 발급 완료**\n\n"
        f"🔑 체험 코드: `{license_code}`\n\n"
        f"아래 '프로그램 다운로드' 버튼을 클릭하여 압축파일을 내려받으신 후, "
        f"실행 창에 위 코드를 복사해서 붙여넣으시면 즉시 자동매매가 시작됩니다.\n\n"
        f"프로그램 연장을 원하시면 5개 거래소중 아무 거래소 상관없이 첨부파일에 있는 레퍼럴로 가입하시고 거래소와 UID 보내주시면 확인후 정식코드 발급해 드리겠습니다. 📈"
    )
    
    keyboard = [[InlineKeyboardButton("📦 프로그램 다운로드 (클릭)", url=PROGRAM_DOWNLOAD_URL)]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(guide_text, reply_markup=reply_markup, parse_mode="Markdown")

# ─── [7. 개인방 1:1 FAQ 자동 응답 무인화] ───
async def handle_faq_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return

    text = update.message.text.lower()
    faqs = load_faq()

    for item in faqs:
        for keyword in item.get("keywords", []):
            if keyword.lower() in text:
                await update.message.reply_text(item["answer"])
                return

    await update.message.reply_text(
        "죄송해요, 입력하신 질문은 자동 안내 항목에 없습니다. 🙏\n"
        "더 세부적인 문의는 총괄 운영자에게 직접 연락해 주시면 빠르게 도와드리겠습니다!"
    )

# ─── [메인 실행부] ───
def main():
    if BOT_TOKEN == "여기에_실제_텔레그램_봇_토큰_입력":
        print("⚠️ BOT_TOKEN을 변경해 주세요.")
        return

    threading.Thread(target=run_health_server, daemon=True).start()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, greet_new_member))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_faq_chat))

    print("🚀 완전 자동화 텔레그램 봇 기동 시작...", flush=True)
    app.run_polling()

if __name__ == "__main__":
    main()
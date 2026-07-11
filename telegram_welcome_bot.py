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
import asyncio
import json
import time
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
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8612743997:AAEkg107o_0nHAvPsGThZLZppvig0QicU6s")

# 내 Render 라이선스 서버 정보
LICENSE_SERVER_URL = "https://bitcoin-trading-1111.onrender.com"
ADMIN_SECRET = "a7b3c9d1e5f8g2h4i6j0k3l7m9qw"  # 서버 환경변수에 등록한 관리자 비번

# ⚠️ 여기에 프로그램 압축파일을 올린 구글 드라이브 또는 드롭박스 등의 다운로드 링크를 넣으세요!
PROGRAM_DOWNLOAD_URL = "https://github.com/haesung-bot/Bitcoin-Trading/releases/download/v1.0.0/pro.zip"

# ⚠️ 답을 못 찾을 때 안내할 운영자 개인 텔레그램 링크.
# 텔레그램 앱 → 설정 → 프로필 편집에서 @username을 만들거나 확인할 수 있습니다.
OPERATOR_PROFILE_LINK = "https://t.me/hhyuk0101"

# ⚠️ 코드 발급 알림을 받을 본인의 텔레그램 고유 ID.
# 모르면 이 봇에게 /whoami 라고 보내면 알려줍니다.
ADMIN_NOTIFY_ID = int(os.environ.get("ADMIN_NOTIFY_ID", "123456789"))

# ─────────────────────────────────────────────────────────
# 발급 코드 유효기간 설정
# None = 무기한 코드 발급 (지금은 신규 유저 유입 목적으로 이 상태)
# 나중에 유저가 많아지면 숫자로 바꾸세요 (예: 7 → 7일짜리 체험 코드로 전환)
# ─────────────────────────────────────────────────────────
TRIAL_DURATION_DAYS = None

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

# ─── [3-1. 중복 발급 방지: 라이선스 서버(SQLite DB)에 기록/조회 ───
def has_already_received(user_id):
    """이 텔레그램 유저가 이미 체험 코드를 받았는지 라이선스 서버에 물어본다."""
    url = f"{LICENSE_SERVER_URL}/admin/check_trial_user"
    headers = {"X-Admin-Secret": ADMIN_SECRET, "Content-Type": "application/json"}
    try:
        res = requests.post(url, headers=headers, json={"telegram_user_id": str(user_id)}, timeout=10)
        if res.status_code == 200:
            data = res.json()
            return bool(data.get("already_issued"))
    except Exception as e:
        logger.error(f"중복발급 확인 실패: {e}")
    # 서버 통신 자체가 안 되면, 안전하게 '이미 받은 것으로' 처리하지 않고 일단 진행되게 둔다
    # (서버 문제로 정상 유저가 코드를 못 받는 것을 막기 위함 — 실제 중복은 아래 mark 단계에서 막힘)
    return False


def save_issued_user(user_id, code):
    """이 유저가 코드를 받았다는 걸 라이선스 서버(SQLite DB)에 기록한다."""
    url = f"{LICENSE_SERVER_URL}/admin/mark_trial_issued"
    headers = {"X-Admin-Secret": ADMIN_SECRET, "Content-Type": "application/json"}
    try:
        requests.post(url, headers=headers, json={"telegram_user_id": str(user_id), "code": code}, timeout=10)
    except Exception as e:
        logger.error(f"발급 기록 저장 실패: {e}")


# ─── [3-2. Render 서버 연동 코드 발급 함수 (무기한/기간제는 TRIAL_DURATION_DAYS로 전환)] ───
def generate_trial_code():
    url = f"{LICENSE_SERVER_URL}/admin/generate"
    headers = {
        "X-Admin-Secret": ADMIN_SECRET,
        "Content-Type": "application/json"
    }
    if TRIAL_DURATION_DAYS is not None:
        note = f"텔레그램 자동 유입 유저 ({TRIAL_DURATION_DAYS}일 체험)"
        body = {"note": note, "duration_days": TRIAL_DURATION_DAYS}
    else:
        # duration_days를 아예 안 보내면 서버가 무기한 코드로 발급함
        note = "텔레그램 자동 유입 유저 (무기한, 신규유치 임시운영)"
        body = {"note": note}

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
        duration_label = f"{TRIAL_DURATION_DAYS}일" if TRIAL_DURATION_DAYS is not None else "무기한"
        go_private_text = (
            "좋은 선택입니다! 👍\n"
            "아래 '개인 채팅방 가기' 버튼을 누른 후, "
            "화면 하단의 **[시작(Start)]** 버튼을 눌러주시면 "
            f"즉시 프로그램 다운로드 링크와 {duration_label} 무료 라이선스 코드를 발급해 드립니다!"
        )
        keyboard = [[InlineKeyboardButton("💬 개인 채팅방 가기 (클릭)", url=f"https://t.me/{bot_username}?start=welcome")]]
        await query.edit_message_text(text=go_private_text, reply_markup=InlineKeyboardMarkup(keyboard))
        
    elif query.data == "no_thanks":
        await query.edit_message_text(text="알겠습니다! 편하게 방을 둘러보세요. 😊")

# ─── [6. 개인 챗방 진입 (/start) 시 파일 다운로드 링크 및 코드 전송] ───
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return

    user_id = update.effective_user.id

    # 이미 체험 코드를 받은 적 있는 유저인지 확인 (최초 1회만 발급)
    if has_already_received(user_id):
        keyboard = [[InlineKeyboardButton("💬 운영자에게 문의하기", url=OPERATOR_PROFILE_LINK)]]
        await update.message.reply_text(
            "이미 체험 코드를 받으신 계정입니다. 😊\n"
            "체험 코드는 계정당 1회만 발급됩니다.\n\n"
            "정식 이용권 문의는 아래 버튼으로 운영자에게 연락해주세요.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    await update.message.reply_text("안녕하세요! 자동매매 프로그램 배포 봇입니다. 🤖\n인증 및 링크 생성을 시작합니다...")

    # 1. 라이선스 코드를 Render 서버에서 즉시 받아옴
    #    (동기 함수라 그냥 부르면 봇 전체가 멈추므로, 별도 스레드에서 실행)
    license_code = await asyncio.to_thread(generate_trial_code)
    
    if not license_code:
        await update.message.reply_text("⚠️ 현재 라이선스 서버 점검 중입니다. 잠시 후 다시 /start 를 입력해 주세요.")
        return

    # 발급 성공 시 이 유저는 "이미 받음"으로 즉시 기록 (중복 발급 방지)
    save_issued_user(user_id, license_code)

    duration_label = f"{TRIAL_DURATION_DAYS}일" if TRIAL_DURATION_DAYS is not None else "무기한"

    # 운영자에게 발급 알림 전송 (실패해도 사용자 흐름에는 영향 없게 조용히 처리)
    try:
        requester = update.effective_user
        await context.bot.send_message(
            chat_id=ADMIN_NOTIFY_ID,
            text=(
                f"🔔 새 코드 발급 알림\n\n"
                f"유저: {requester.first_name or ''}"
                f"({'@' + requester.username if requester.username else requester.id})\n"
                f"코드: `{license_code}`\n"
                f"유효기간: {duration_label}"
            ),
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"운영자 알림 전송 실패: {e}")

    # 2. 다운로드 가이드 및 라이선스 코드 안내 (버튼 결합)
    guide_text = (
        f"✅ **{duration_label} 무료 라이선스 및 프로그램 발급 완료**\n\n"
        f"🔑 체험 코드: `{license_code}`\n\n"
        f"아래 '프로그램 다운로드' 버튼을 클릭하여 압축파일을 내려받으신 후, "
        f"실행 창에 위 코드를 복사해서 붙여넣으시면 즉시 자동매매가 시작됩니다.\n\n"
        f" 5개 거래소중 아무 거래소 상관없이 첨부파일에 있는 레퍼럴로 가입해서 써주시면 개발에 큰도움이 됩니다^^!. 📈"
    )
    
    keyboard = [[InlineKeyboardButton("📦 프로그램 다운로드 (클릭)", url=PROGRAM_DOWNLOAD_URL)]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(guide_text, reply_markup=reply_markup, parse_mode="Markdown")

# ─── [7-1. 내 텔레그램 ID 확인용 명령어] ───
async def whoami(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(
        f"당신의 텔레그램 ID: {user.id}\n"
        f"(닉네임: {user.first_name or ''} {'@' + user.username if user.username else ''})"
    )


# ─── [7-2. 개인방 1:1 FAQ 자동 응답 무인화] ───
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

    # FAQ에 없는 질문 → 운영자 개인 프로필 링크를 버튼으로 안내
    keyboard = [[InlineKeyboardButton("💬 운영자에게 직접 문의하기", url=OPERATOR_PROFILE_LINK)]]
    await update.message.reply_text(
        "죄송해요, 입력하신 질문은 자동 안내 항목에 없습니다. 🙏\n"
        "아래 버튼을 눌러 운영자에게 직접 문의해주세요!",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ─── [메인 실행부] ───
def main():
    if BOT_TOKEN == "8612743997:AAEkg107o_0nHAvPsGThZLZppvig0QicU6s":
        print("⚠️ BOT_TOKEN을 변경해 주세요.")
        return

    threading.Thread(target=run_health_server, daemon=True).start()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, greet_new_member))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("whoami", whoami))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_faq_chat))

    print("🚀 완전 자동화 텔레그램 봇 기동 시작...", flush=True)
    app.run_polling()

if __name__ == "__main__":
    main()
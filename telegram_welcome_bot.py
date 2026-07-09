"""
telegram_welcome_bot.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
텔레그램 그룹에 새 멤버가 들어오면 자동으로 환영 메시지를 보내는 봇.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[사전 준비]
1. 텔레그램에서 @BotFather 검색 → /newbot 입력 → 봇 이름 설정
   → 완료되면 "봇 토큰"을 줍니다 (예: 123456789:ABCdefGHIjklMNOpqrSTUvwxyz)
2. 만든 봇을 본인 그룹방에 초대
3. 그룹방 설정 → 관리자(Administrator)로 봇 승격
   (권한 중 최소 "메시지 삭제/멤버 관리"는 꺼도 되고, "메시지 보내기"만 있어도 동작함)
4. 그룹이 "새 멤버가 그룹에 들어왔다"는 이벤트를 봇에게 전달하려면,
   BotFather에서 해당 봇의 "Group Privacy"를 꺼야 할 수도 있습니다.
   (@BotFather → /mybots → 봇 선택 → Bot Settings → Group Privacy → Turn off)

[실행 방법]
    pip install python-telegram-bot --upgrade
    python telegram_welcome_bot.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import logging
import threading
import asyncio
import json
import requests
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

# ⚠️ 여기에 BotFather에게 받은 실제 토큰을 넣으세요.
import os
# 환경변수(BOT_TOKEN)가 있으면 그걸 우선 사용, 없으면 아래 값을 직접 채워서 사용
BOT_TOKEN = os.environ.get("BOT_TOKEN", "여기에_봇_토큰_입력")

# ─────────────────────────────────────────────────────────
# 라이선스 서버(license_code_server.py) 연동 설정
# ─────────────────────────────────────────────────────────
LICENSE_SERVER_URL = os.environ.get("LICENSE_SERVER_URL", "https://autobot-dblm.onrender.com")
LICENSE_ADMIN_SECRET = os.environ.get("LICENSE_ADMIN_SECRET", "CHANGE_THIS_ADMIN_SECRET")

# /gencode 명령어를 쓸 수 있는 사람의 텔레그램 고유 ID 목록.
# 본인 ID를 모르면 텔레그램에서 이 봇에게 /whoami 라고 보내면 알려줍니다.
# 확인 후 아래 중괄호 안에 숫자로 넣으세요. 예: {123456789}
ADMIN_TELEGRAM_IDS = {123456789}  # ⚠️ 반드시 본인 ID로 교체하세요

# ─────────────────────────────────────────────────────────
# 신규 유치 자동화 설정
# ─────────────────────────────────────────────────────────
# 체험 코드 유효기간(일)
TRIAL_CODE_DAYS = int(os.environ.get("TRIAL_CODE_DAYS", "7"))

# 프로그램 전달 방식: 아래 둘 중 하나만 설정하면 됨
#  1) PROGRAM_DOWNLOAD_URL: 구글드라이브/드롭박스 등 다운로드 링크 (추천 — 용량 제한 없음)
#  2) PROGRAM_ZIP_PATH: 서버(저장소)에 압축파일을 직접 올려두고 그 경로를 지정 (100MB 이하만 가능)
PROGRAM_DOWNLOAD_URL = os.environ.get("PROGRAM_DOWNLOAD_URL", "")
PROGRAM_ZIP_PATH = os.environ.get("PROGRAM_ZIP_PATH", "program_package.zip")

# 자주 묻는 질문(FAQ) 파일 경로 — 같은 폴더에 faq.json 파일을 두고 자유롭게 수정하세요.
FAQ_FILE_PATH = os.environ.get("FAQ_FILE_PATH", "faq.json")

# 환영 메시지 템플릿. {name}은 자동으로 새로 들어온 사람 이름으로 치환됩니다.
WELCOME_MESSAGE = (
    "🎉 {name}님, 환영합니다!\n\n"
    "이 방은 자동매매 프로그램 관련 공지/문의방입니다.\n"
    "궁금하신 점 있으시면 편하게 질문해주세요 🙌"
)

# 개인채팅에서 처음 보내는 안내 질문 문구
TRIAL_OFFER_MESSAGE = (
    "안녕하세요! 👋\n\n"
    "자동매매 프로그램에 관심이 있으신가요?\n"
    f"관심 있다고 답해주시면 프로그램과 {TRIAL_CODE_DAYS}일 무료 체험 코드를 바로 보내드립니다."
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────
# Render 무료 "Web Service" 플랜은 반드시 포트를 열고 있어야 살아있다고 인식한다.
# 텔레그램 봇 자체는 포트가 필요 없는(폴링 방식) 프로그램이라, 그냥 "나 살아있어요"만
# 응답하는 아주 작은 웹서버를 별도 스레드로 하나 같이 띄워서 포트를 열어둔다.
# (실제 환영 메시지 로직과는 무관, Render를 속이기 위한 용도)
# ─────────────────────────────────────────────────────────
health_app = Flask(__name__)


@health_app.route("/")
def health():
    return "Telegram welcome bot is running."


def run_health_server():
    print("ℹ️ 헬스체크 서버 스레드를 시작합니다...", flush=True)
    try:
        port = int(os.environ.get("PORT", 5000))
        print(f"ℹ️ 헬스체크 서버가 포트 {port}번으로 열립니다.", flush=True)
        health_app.run(host="0.0.0.0", port=port, use_reloader=False, threaded=True, debug=False)
    except Exception as e:
        import traceback
        print(f"❌ 헬스체크 서버 시작 실패: {e}", flush=True)
        traceback.print_exc()


async def whoami(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """이 명령어를 보낸 사람의 텔레그램 고유 ID를 알려준다.
    ADMIN_TELEGRAM_IDS에 본인 ID를 넣기 위해 사용."""
    user = update.effective_user
    await update.message.reply_text(
        f"당신의 텔레그램 ID: {user.id}\n"
        f"(닉네임: {user.first_name or ''} {'@' + user.username if user.username else ''})"
    )


async def gencode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """관리자 전용: 라이선스 서버에 기간제 코드 발급을 요청한다.
    사용법: /gencode 7 텔레그램닉네임메모 (7일짜리, 메모는 선택)
           /gencode 0.007 테스트  ← 분 단위 테스트는 /gencode_min 사용 권장"""
    user_id = update.effective_user.id
    if user_id not in ADMIN_TELEGRAM_IDS:
        await update.message.reply_text("⛔ 이 명령어는 운영자만 사용할 수 있습니다.")
        return

    if not context.args:
        await update.message.reply_text(
            "사용법: /gencode <일수> [메모]\n예: /gencode 7 텔레그램유저123"
        )
        return

    try:
        duration_days = int(context.args[0])
        if duration_days <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("일수는 0보다 큰 정수로 입력해주세요. 예: /gencode 7")
        return

    note = " ".join(context.args[1:]) if len(context.args) > 1 else ""

    await update.message.reply_text(f"⏳ {duration_days}일짜리 코드 발급 요청 중...")

    def _call_license_server():
        return requests.post(
            f"{LICENSE_SERVER_URL}/admin/generate",
            json={"note": note, "duration_days": duration_days},
            headers={"X-Admin-Secret": LICENSE_ADMIN_SECRET},
            timeout=15
        )

    try:
        # requests는 동기(blocking) 호출이라, 봇의 이벤트 루프가 멈추지 않도록 별도 스레드에서 실행
        resp = await asyncio.to_thread(_call_license_server)
        data = resp.json()
    except Exception as e:
        await update.message.reply_text(f"❌ 라이선스 서버 연결 실패: {e}")
        return

    if not data.get("ok"):
        await update.message.reply_text(f"❌ 발급 실패: {data.get('error', '알 수 없는 오류')}")
        return

    code = data["code"]
    await update.message.reply_text(
        f"✅ 코드 발급 완료!\n\n"
        f"코드: `{code}`\n"
        f"유효기간: {duration_days}일\n"
        f"메모: {note or '(없음)'}\n\n"
        f"이 코드를 사용자에게 전달하세요.",
        parse_mode="Markdown"
    )
    logger.info(f"코드 발급: {code} ({duration_days}일, 메모: {note}) — 요청자ID={user_id}")


async def gencode_min(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """관리자 전용: 분 단위 기간제 코드 발급 (테스트용).
    사용법: /gencode_min 10 테스트메모"""
    user_id = update.effective_user.id
    if user_id not in ADMIN_TELEGRAM_IDS:
        await update.message.reply_text("⛔ 이 명령어는 운영자만 사용할 수 있습니다.")
        return

    if not context.args:
        await update.message.reply_text(
            "사용법: /gencode_min <분> [메모]\n예: /gencode_min 10 테스트"
        )
        return

    try:
        duration_minutes = int(context.args[0])
        if duration_minutes <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("분(分)은 0보다 큰 정수로 입력해주세요. 예: /gencode_min 10")
        return

    note = " ".join(context.args[1:]) if len(context.args) > 1 else ""

    def _call_license_server():
        return requests.post(
            f"{LICENSE_SERVER_URL}/admin/generate",
            json={"note": note, "duration_minutes": duration_minutes},
            headers={"X-Admin-Secret": LICENSE_ADMIN_SECRET},
            timeout=15
        )

    try:
        resp = await asyncio.to_thread(_call_license_server)
        data = resp.json()
    except Exception as e:
        await update.message.reply_text(f"❌ 라이선스 서버 연결 실패: {e}")
        return

    if not data.get("ok"):
        await update.message.reply_text(f"❌ 발급 실패: {data.get('error', '알 수 없는 오류')}")
        return

    code = data["code"]
    await update.message.reply_text(
        f"✅ (테스트용) 코드 발급 완료!\n\n"
        f"코드: `{code}`\n"
        f"유효기간: {duration_minutes}분\n"
        f"메모: {note or '(없음)'}",
        parse_mode="Markdown"
    )
    logger.info(f"테스트 코드 발급: {code} ({duration_minutes}분) — 요청자ID={user_id}")


async def greet_new_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """새 멤버가 그룹에 들어올 때마다 호출되는 함수.
    환영 메시지 + '관심있어요' 버튼을 같이 보낸다.
    버튼은 봇과의 개인채팅을 여는 딥링크라서, 누르면 자동으로 /start interested가 전송된다."""
    if not update.message or not update.message.new_chat_members:
        return

    bot_username = context.bot.username

    for new_member in update.message.new_chat_members:
        # 봇 자기 자신이 초대된 경우는 환영 메시지를 보내지 않음
        if new_member.id == context.bot.id:
            continue

        display_name = new_member.first_name or new_member.username or "새 멤버"
        text = WELCOME_MESSAGE.format(name=display_name)

        deep_link = f"https://t.me/{bot_username}?start=interested"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🙋 자동매매 프로그램 안내받기", url=deep_link)]
        ])

        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=text,
            reply_markup=keyboard
        )
        logger.info(f"환영 메시지 전송: {display_name} (chat_id={update.effective_chat.id})")


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """개인채팅에서 /start 를 받았을 때 처리.
    그룹 환영 메시지의 딥링크 버튼을 눌러서 들어온 경우(payload='interested')
    바로 예/아니오 질문을 보여준다."""
    if update.effective_chat.type != "private":
        return  # 그룹에서 온 /start는 무시

    payload = context.args[0] if context.args else ""

    if payload == "interested":
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ 네, 하고 싶어요", callback_data="trial_yes")],
            [InlineKeyboardButton("❌ 아니요, 괜찮아요", callback_data="trial_no")],
        ])
        await update.message.reply_text(TRIAL_OFFER_MESSAGE, reply_markup=keyboard)
    else:
        await update.message.reply_text(
            "안녕하세요! 궁금하신 점을 편하게 물어보세요 🙌\n"
            "(자동매매 프로그램 안내는 그룹방의 안내 버튼을 눌러주세요)"
        )


async def handle_trial_response(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """예/아니오 버튼 클릭 처리. '예'를 누르면 프로그램 + 체험 코드를 자동 전송한다."""
    query = update.callback_query
    await query.answer()  # 버튼 로딩 스피너 멈추기(필수)
    chat_id = query.from_user.id

    if query.data == "trial_no":
        await query.edit_message_text("알겠습니다! 마음이 바뀌시면 언제든 다시 말씀해주세요 😊")
        return

    if query.data != "trial_yes":
        return

    await query.edit_message_text("🎉 감사합니다! 프로그램과 체험 코드를 준비하고 있어요, 잠시만 기다려주세요...")

    # ── 1. 프로그램 전달 (다운로드 링크 우선, 없으면 압축파일 직접 전송) ──
    if PROGRAM_DOWNLOAD_URL:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"📦 프로그램 다운로드 링크:\n{PROGRAM_DOWNLOAD_URL}"
        )
    elif os.path.exists(PROGRAM_ZIP_PATH):
        try:
            with open(PROGRAM_ZIP_PATH, "rb") as f:
                await context.bot.send_document(
                    chat_id=chat_id, document=f,
                    filename=os.path.basename(PROGRAM_ZIP_PATH)
                )
        except Exception as e:
            await context.bot.send_message(chat_id=chat_id, text=f"⚠️ 프로그램 파일 전송 실패: {e}")
    else:
        await context.bot.send_message(
            chat_id=chat_id,
            text="⚠️ 프로그램 파일을 찾을 수 없습니다. 운영자에게 문의해주세요."
        )

    # ── 2. 체험 코드 자동 발급 (라이선스 서버 호출) ──
    note = f"텔레그램자동발급_{query.from_user.id}_{query.from_user.username or ''}"

    def _call_license_server():
        return requests.post(
            f"{LICENSE_SERVER_URL}/admin/generate",
            json={"note": note, "duration_days": TRIAL_CODE_DAYS},
            headers={"X-Admin-Secret": LICENSE_ADMIN_SECRET},
            timeout=30
        )

    try:
        resp = await asyncio.to_thread(_call_license_server)
        data = resp.json()
    except Exception as e:
        await context.bot.send_message(chat_id=chat_id, text=f"⚠️ 코드 발급 중 오류: {e}\n운영자에게 문의해주세요.")
        return

    if not data.get("ok"):
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"⚠️ 코드 발급 실패: {data.get('error', '알 수 없는 오류')}\n운영자에게 문의해주세요."
        )
        return

    code = data["code"]
    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"✅ {TRIAL_CODE_DAYS}일 무료 체험 코드가 발급되었습니다!\n\n"
            f"코드: `{code}`\n\n"
            f"프로그램 실행 후 이 코드를 입력하시면 바로 사용하실 수 있어요.\n"
            f"사용법이나 궁금한 점은 이 채팅창에 자유롭게 물어보세요 🙌"
        ),
        parse_mode="Markdown"
    )
    logger.info(f"체험 코드 자동 발급: {code} (요청자ID={chat_id}, note={note})")


def load_faq():
    """faq.json 파일을 불러온다. 형식: [{"keywords": ["환불", "취소"], "answer": "..."},  ...]"""
    try:
        with open(FAQ_FILE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"FAQ 파일을 불러오지 못했습니다({e}) — 빈 목록으로 진행합니다.")
        return []


async def handle_private_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """개인채팅에서 일반 텍스트(명령어 아님)가 오면 FAQ에서 찾아 답변한다."""
    if update.effective_chat.type != "private":
        return
    if not update.message or not update.message.text:
        return

    text = update.message.text.lower()
    faqs = load_faq()

    for item in faqs:
        for keyword in item.get("keywords", []):
            if keyword.lower() in text:
                await update.message.reply_text(item["answer"])
                return

    # 매칭되는 FAQ가 없으면 기본 안내
    await update.message.reply_text(
        "죄송해요, 아직 답변을 준비하지 못한 질문이에요 🙏\n"
        "운영자에게 직접 문의해주시면 빠르게 답변드릴게요."
    )


def main():
    if BOT_TOKEN == "여기에_봇_토큰_입력":
        print("⚠️ BOT_TOKEN을 실제 값으로 바꿔주세요 (BotFather에게 받은 토큰).", flush=True)
        return

    # Render가 "포트 열려있음"을 확인할 수 있도록 헬스체크용 웹서버를 백그라운드로 실행
    threading.Thread(target=run_health_server, daemon=True).start()

    app = Application.builder().token(BOT_TOKEN).build()

    # "새 멤버가 그룹에 들어왔다"는 시스템 메시지를 감지하는 핸들러 등록
    app.add_handler(
        MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, greet_new_member)
    )
    app.add_handler(CommandHandler("whoami", whoami))
    app.add_handler(CommandHandler("gencode", gencode))
    app.add_handler(CommandHandler("gencode_min", gencode_min))
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CallbackQueryHandler(handle_trial_response))
    # 개인채팅에서 오는 일반 텍스트(명령어 제외)는 FAQ로 응답 — 반드시 다른 핸들러들 뒤에 등록
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, handle_private_text)
    )

    print("✅ 텔레그램 환영 봇이 시작되었습니다. 그룹에 새 멤버가 들어오는지 감시 중...", flush=True)
    app.run_polling()


if __name__ == "__main__":
    main()

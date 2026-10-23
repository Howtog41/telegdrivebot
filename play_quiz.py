import json
import os
import re
import asyncio
import html
import gc
import random
from datetime import datetime, timedelta
from aiogram import Router, F, Bot
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, 
    PollAnswer, Poll, ReplyKeyboardRemove, WebAppInfo
)
from aiogram.filters import CommandStart, CommandObject, Command
from aiogram.enums import ParseMode

from Helpers.database import db

router = Router()

# ==========================================
# 🟢 SMART ACCESS CHECKER 
# ==========================================
async def verify_user_premium_access(bot: Bot, user_id: int, educator_id: int):
    has_access = False
    is_admin = False

    # STEP 1: चेक करें कि क्या यूज़र के पास Fixed (Claimed) एक्सेस है?
    has_fixed = await db.check_premium_access(user_id, educator_id)
    if has_fixed:
        has_access = True

    # STEP 2: लाइव ग्रुप मेंबरशिप चेक करें (और चेक करें कि क्या वो एडमिन है)
    educator = await db.get_educator(educator_id)
    if educator:
        premium_groups = educator.get("premium_groups", [])
        old_group_id = educator.get("private_group_id")
        if old_group_id and not premium_groups:
            premium_groups = [{"id": old_group_id, "name": "Old Premium Group"}]

        for grp in premium_groups:
            try:
                member = await bot.get_chat_member(chat_id=grp["id"], user_id=user_id)
                if member.status in ["administrator", "creator"]:
                    has_access = True
                    is_admin = True
                    break
                elif member.status in ["member", "restricted"]:
                    has_access = True
            except Exception:
                continue

    return has_access, is_admin

# ==========================================
# 🟢 BACKGROUND QUEUE SYSTEM (10 Workers)
# ==========================================
answer_queue = asyncio.Queue()

@router.startup()
async def start_queue_worker():
    """Bot start hote hi 10 background worker chalu ho jayenge (Speed x10)"""
    for _ in range(10):
        asyncio.create_task(poll_answer_worker())
    print("✅ 10 Poll Answer Queue Workers Started successfully!")

async def poll_answer_worker():
    """Lagatar Queue se answer nikal kar process karega bina bot ko hang kiye"""
    while True:
        try:
            poll_answer, bot = await answer_queue.get()
            await process_single_answer(poll_answer, bot)
        except Exception as e:
            print(f"Queue Worker Error: {e}")
        finally:
            answer_queue.task_done()

# ==========================================
# 🟢 SHUFFLE HELPER FUNCTIONS (PRE-SHUFFLE FOR SPEED)
# ==========================================
def get_shuffle_mode(raw_setting: str) -> str:
    raw = str(raw_setting).lower()
    if "both" in raw: return "both"
    elif "question" in raw or "que" in raw: return "questions"
    elif "answer" in raw or "option" in raw or "ans" in raw: return "options"
    return "none"

def prepare_and_shuffle_polls(raw_polls: list, shuffle_mode: str) -> list:
    """Quiz start hone se pehle hi questions aur options ko memory me shuffle kar dega (Zero DB Delay during quiz)"""
    polls_list = [dict(p) for p in raw_polls]
    
    if shuffle_mode in ["questions", "both"]:
        random.shuffle(polls_list)

    for poll_data in polls_list:
        original_options = [str(opt).strip() for opt in poll_data.get('options', []) if str(opt).strip()]
        if len(original_options) < 2:
            original_options = ["Option A", "Option B"]
            
        try:
            correct_idx = int(poll_data.get('correct_option_id', 0))
        except (TypeError, ValueError):
            correct_idx = 0
            
        if correct_idx >= len(original_options) or correct_idx < 0:
            correct_idx = 0
            
        paired = list(enumerate(original_options))
        
        if shuffle_mode in ["options", "both"] and len(paired) > 1:
            original_paired = list(paired)
            for _ in range(10): 
                random.shuffle(paired)
                if paired != original_paired:
                    break

        shuffled_opts = [opt for i, opt in paired]
        new_correct_id = next(new_i for new_i, (old_i, opt) in enumerate(paired) if old_i == correct_idx)
        
        poll_data["options"] = shuffled_opts
        poll_data["correct_option_id"] = new_correct_id
        poll_data["opts_shuffled"] = True

    return polls_list

# ==========================================
# /start Command (Auto-Demo, Clone & Handles Deep Links)
# ==========================================
@router.message(CommandStart())
async def start_command_handler(message: Message, command: CommandObject, bot: Bot):
    quiz_id = command.args
    user_id = message.from_user.id
    chat_id = message.chat.id
    is_private = message.chat.type == "private"
    
    is_clone = os.getenv("IS_CLONE") == "True"
    custom_start_msg = os.getenv("CUSTOM_START_MSG", "")

    # --- AUTO-DEMO ACTIVATION ---
    is_new_user = False
    if is_private and not is_clone:
        current_user_profile = await db.get_educator(user_id)
        
        if not current_user_profile or "plan_expiry" not in current_user_profile:
            start_date = datetime.utcnow()
            expiry_date = start_date + timedelta(days=30)
            await db.educators.update_one(
                {"_id": user_id},
                {"$set": {
                    "plan_expiry": expiry_date,
                    "plan_type": "Demo",
                    "plan_start": start_date,
                    "has_claimed_demo": True
                }},
                upsert=True
            )
            is_new_user = True

    # ==========================================
    # Main Welcome Message
    # ==========================================
    if not quiz_id or not quiz_id.startswith("q_"):
        if not is_private: return 
        
        MINI_APP_URL = "https://examquiz1.vercel.app/viewedu.html"
        web_app_btn = WebAppInfo(url=MINI_APP_URL)
        
        markup = None 
        
        if is_clone:
            if custom_start_msg:
                welcome_text = custom_start_msg.replace("{name}", message.from_user.first_name)
            else:
                welcome_text = f"👋 <b>नमस्ते {message.from_user.first_name}!</b>\n\nइस एडवांस क्विज़ बॉट में आपका स्वागत है।"
            
            custom_buttons_env = os.getenv("CUSTOM_INLINE_BUTTONS", "[]")
            try:
                custom_buttons = json.loads(custom_buttons_env)
                buttons = [[InlineKeyboardButton(text="📚 Open Library (Mini App)", web_app=web_app_btn)]]
                if custom_buttons:
                    buttons.extend([[InlineKeyboardButton(text=btn["text"], url=btn["url"])] for btn in custom_buttons])
                markup = InlineKeyboardMarkup(inline_keyboard=buttons)
            except Exception:
                markup = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="📚 Open Library (Mini App)", web_app=web_app_btn)]
                ])
        else:
            welcome_text = (
                "👋 <b>Welcome to Advance MCQ Bot!</b>\n\n"
                "I am a fast and advanced quiz management bot. I help Educators easily create, manage, and automate their quizzes.\n\n"
                "✨ <b>Main Features:</b>\n"
                "🔹 Create quizzes directly from Text, CSV, or DOCX files\n"
                "🔹 Set Custom Timers and Negative Marking\n"
                "🔹 Auto-Schedule quizzes in Groups/Channels\n"
                "🔹 Support for Paid quizzes and advanced Leaderboards\n\n"
                "👉 Click /creat_quiz to create your new quiz."
            )
            if is_new_user:
                welcome_text += "\n\n🎁 <b>Bonus:</b> A <b>30-Day Free Trial</b> has been automatically activated for your account! Send /my_plan to check details."
            
            markup = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📚 Open Exam Library", web_app=web_app_btn)],
                [InlineKeyboardButton(text="👨‍💻 Owner", url="https://t.me/lkd_ak"),
                 InlineKeyboardButton(text="📢 Updates Channel", url="https://t.me/How_To_Google")]
            ])
            
        try:
            return await message.answer(welcome_text, reply_markup=markup, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        except Exception as e:
            print(f"⚠️ Cannot send welcome message to {chat_id}: {e}")
            return

    # ==========================================
    # Play Quiz Flow
    # ==========================================
    quiz = await db.get_quiz(quiz_id)
    if not quiz:
        return await message.answer("❌ <b>This quiz does not exist or has been deleted.</b>", parse_mode=ParseMode.HTML)

    educator_id = quiz["educator_id"]
    educator = await db.get_educator(educator_id)
    if not educator or educator.get("plan_expiry", datetime.min) < datetime.utcnow():
        return await message.answer("⚠️ <b>Educator plan expired, please contact the admin to renew the plan.</b>", parse_mode=ParseMode.HTML)

    if quiz.get("is_paid"):
        has_access, is_premium_admin = await verify_user_premium_access(bot, user_id, educator_id)

        if not has_access:
            try:
                ed_user = await bot.get_chat(educator_id)
                ed_username = ed_user.username
                url = f"https://t.me/{ed_username}" if ed_username else f"tg://user?id={educator_id}"
            except Exception:
                url = f"tg://user?id={educator_id}"

            markup = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💸 Pay & Access Quiz", callback_data=f"pay_access_{educator_id}")],
                [InlineKeyboardButton(text="📞 Contact Educator", url=url)]
            ])
            return await message.answer(
                "🔒 <b>Access Denied!</b>\n\nThis Quiz is <b>Paid</b> and can only be accessed by premium members.\n"
                "Please make the payment or contact the Educator for access.",
                reply_markup=markup,
                parse_mode=ParseMode.HTML
            )
            
        if has_access and not is_premium_admin and not is_private:
            return await message.answer(
                "⚠️ <b>Action Restricted!</b>\n\n"
                "आप इस Paid Quiz को केवल <b>Private Chat (Bot Inbox)</b> में अटेंड कर सकते हैं।\n\n"
                "❌ <i>क्विज़ को ग्रुप में स्टार्ट करने के लिए आपका प्रीमियम ग्रुप में <b>Admin</b> होना ज़रूरी है।</i>",
                parse_mode=ParseMode.HTML
            )

    shuffle_mode = get_shuffle_mode(quiz.get("shuffle", "none"))
    polls_list = prepare_and_shuffle_polls(quiz.get("polls", []), shuffle_mode)

    # ==========================================
    # 🟢 DIRECT GROUP START LOGIC 
    # ==========================================
    if not is_private:
        existing_session = await db.db.quiz_sessions.find_one({"chat_id": chat_id})
        if existing_session:
            return await message.answer("❌ <b>A quiz is already running in this group!</b>", parse_mode=ParseMode.HTML)

        session_data = {
            "chat_id": chat_id,
            "quiz_id": quiz_id,
            "quiz_title": quiz["title"],
            "initiator_id": user_id, 
            "polls": polls_list,
            "shuffle_setting": shuffle_mode, 
            "timer": int(quiz["timer"]),
            "nm_value": float(quiz.get("negative_marking", 0)),
            "marks_per_q": float(quiz.get("marks_per_question", 1)), 
            "current_index": 0,
            "participants": {},
            "answered_polls": {},
            "poll_id_map": {},
            "pending_submit": False, 
            "auto_paused": False, 
            "consecutive_misses": 0, 
            "current_poll_answered": False
        }
        
        await db.db.quiz_sessions.insert_one(session_data)
        
        total_q = len(polls_list)
        marks = session_data["marks_per_q"]
        total_marks = total_q * marks
        nm_val = session_data["nm_value"]
        nm_text = f"\n✶ <b>Negative Marking:</b> -{nm_val} per wrong answer" if nm_val > 0 else ""

        start_msg = (
            f"🎲 🚀 <i>Quiz <b>'{quiz['title']}'</b> is starting in 3 seconds... Best of Luck!</i>\n\n"
            f"🖊 <b>{total_q} questions</b> [ Total Marks: {total_marks} ]\n"
            f"⏱ <b>{quiz['timer']} seconds</b> per question{nm_text}\n\n"
            f"💡 <i>Admin can send /stop_quiz anytime to finish early.</i>"
        )
        try:
            await message.answer(start_msg, parse_mode=ParseMode.HTML)
            await asyncio.sleep(2)
            return await send_next_poll(bot, chat_id)
        except Exception as e:
            if "Forbidden" in str(e) or "kicked" in str(e):
                print(f"⚠️ Bot was kicked from group {chat_id}. Session deleted.")
                await db.db.quiz_sessions.delete_one({"chat_id": chat_id})
            return
 
    # ==========================================
    # PRIVATE CHAT PLATFORM SELECTION
    # ==========================================
    bot_me = await bot.get_me()
    bot_username = bot_me.username
    
    direct_app_url_exam = f"https://t.me/{bot_username}/app?startapp={quiz_id}"
    direct_app_url_prac = f"https://t.me/{bot_username}/app?startapp=practice_{quiz_id}"

    keyboard = []
    is_practice_on = quiz.get("practice_mode_enabled", False)
    
    if is_practice_on:
        keyboard.append([InlineKeyboardButton(text="📝 Exam Mode (Mini App)", url=direct_app_url_exam)])
        keyboard.append([InlineKeyboardButton(text="📚 Practice Mode (Mini App)", url=direct_app_url_prac)])
    else:
        keyboard.append([InlineKeyboardButton(text="📱 Play in Mini App (Fast)", url=direct_app_url_exam)])
        
    keyboard.append([InlineKeyboardButton(text="🤖 Play in Telegram Bot", callback_data=f"start_tg_quiz_{quiz_id}")])

    markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
    prac_status = "✅ Available" if is_practice_on else "❌ Not Available"

    start_msg = (
        f"✅ <b>Quiz: {quiz['title']}</b>\n\n"
        f"📝 <b>Total Questions:</b> {len(polls_list)}\n"
        f"🔢 <b>Marks per Question:</b> {quiz.get('marks_per_question', 1)}\n"
        f"⚖️ <b>Negative Marking:</b> {quiz.get('negative_marking', 0)}\n"
        f"🔀 <b>Shuffle Mode:</b> {shuffle_mode.title()}\n"
        f"⏱️ <b>Time per question:</b> {quiz['timer']} seconds\n"
        f"📚 <b>Practice Mode:</b> {prac_status}\n\n"
        f"🚀 <i>Where would you like to play this quiz?</i>"
    )
    
    await message.answer(start_msg, reply_markup=markup, parse_mode=ParseMode.HTML)

# ==========================================
# Triggered when User clicks "Play in Telegram Bot" 
# ==========================================
@router.callback_query(F.data.startswith("start_tg_quiz_"))
async def start_telegram_quiz(callback: CallbackQuery, bot: Bot):
    quiz_id = callback.data.replace("start_tg_quiz_", "")
    chat_id = callback.message.chat.id

    existing_session = await db.db.quiz_sessions.find_one({"chat_id": chat_id})
    if existing_session:
        return await callback.message.answer(
            "❌ <b>Active Quiz Detected!</b>\n\nYou already have a quiz running in this chat.\n"
            "Please type /stop_quiz to end it before starting a new one.", 
            parse_mode=ParseMode.HTML
        )

    quiz = await db.get_quiz(quiz_id)
    if not quiz:
        return await callback.answer("❌ Quiz no longer exists!", show_alert=True)

    shuffle_mode = get_shuffle_mode(quiz.get("shuffle", "none"))
    polls_list = prepare_and_shuffle_polls(quiz.get("polls", []), shuffle_mode)

    session_data = {
        "chat_id": chat_id,
        "quiz_id": quiz_id,
        "quiz_title": quiz["title"],
        "initiator_id": callback.from_user.id,
        "polls": polls_list,
        "shuffle_setting": shuffle_mode,
        "timer": int(quiz["timer"]),
        "nm_value": float(quiz.get("negative_marking", 0)),
        "marks_per_q": float(quiz.get("marks_per_question", 1)), 
        "current_index": 0,
        "participants": {},
        "answered_polls": {},
        "poll_id_map": {},
        "pending_submit": False, 
        "auto_paused": False, 
        "consecutive_misses": 0, 
        "current_poll_answered": False
    }
    
    await db.db.quiz_sessions.insert_one(session_data)
    
    total_q = len(polls_list)
    marks = session_data["marks_per_q"]
    total_marks = total_q * marks
    nm_val = session_data["nm_value"]
    nm_text = f"\n✶ <b>Negative Marking:</b> -{nm_val} per wrong answer" if nm_val > 0 else ""

    start_msg = (
        f"🎲 🚀 <i>Quiz <b>'{quiz['title']}'</b> is starting in 2 seconds... Best of Luck!</i>\n\n"
        f"🖊 <b>{total_q} questions</b> [ Total Marks: {total_marks} ]\n"
        f"⏱ <b>{quiz['timer']} seconds</b> per question{nm_text}\n\n"
        f"💡 <i>Send /stop_quiz anytime to finish early.</i>"
    )

    try:
        await callback.message.delete()
    except Exception:
        pass

    await bot.send_message(
        chat_id=chat_id, 
        text=start_msg, 
        parse_mode=ParseMode.HTML,
        reply_markup=ReplyKeyboardRemove()
    )
    
    await asyncio.sleep(2)
    await send_next_poll(bot, chat_id)

# ==========================================
# RESULT FILE SENDER HELPER 
# ==========================================
async def send_result_file(chat_id: int, quiz_id: str, bot: Bot):
    if chat_id > 0:
        return
        
    quiz = await db.get_quiz(quiz_id)
    if not quiz: return
    
    result_format = quiz.get("result_format", "off") 
    
    if result_format not in ["html", "pdf"]:
        return
        
    caption_text = "📕 <b>Here is your Quiz PDF!</b>" if result_format == "pdf" else "📄 <b>Here is your Quiz HTML!</b>"
    
    cached_file_id = quiz.get(f"cached_{result_format}_id")
    
    if cached_file_id:
        try:
            await bot.send_document(
                chat_id=chat_id,
                document=cached_file_id,
                caption=caption_text,
                parse_mode=ParseMode.HTML
            )
            return 
        except Exception:
            pass

    bot_info = await bot.get_me()
    try:
        creator_chat = await bot.get_chat(quiz["educator_id"])
        creator_name = creator_chat.first_name or creator_chat.title or "Educator"
        creator_username = creator_chat.username or "Unknown"
    except Exception:
        creator_name = "Educator"
        creator_username = "Unknown"

    try:
        if result_format == "html":
            from Handlers.html_generator import get_quiz_html_file
            file_obj = await get_quiz_html_file(quiz, bot_name=f"@{bot_info.username}")
        elif result_format == "pdf":
            from Handlers.html_generator import get_quiz_pdf_file
            file_obj = await get_quiz_pdf_file(
                quiz=quiz, 
                bot_first_name=bot_info.first_name, 
                bot_username=bot_info.username, 
                creator_name=creator_name, 
                creator_username=creator_username
            )

        storage_channel_id = os.getenv("STORAGE_CHANNEL_ID")
        
        if storage_channel_id:
            try:
                storage_msg = await bot.send_document(
                    chat_id=int(storage_channel_id),
                    document=file_obj,
                    caption=f"Backup File for Quiz ID: {quiz_id}"
                )
                new_file_id = storage_msg.document.file_id
                
                await db.quizzes.update_one(
                    {"quiz_id": quiz_id},
                    {"$set": {f"cached_{result_format}_id": new_file_id}}
                )
                
                await bot.send_document(
                    chat_id=chat_id, 
                    document=new_file_id, 
                    caption=caption_text,
                    parse_mode=ParseMode.HTML
                )
            except Exception:
                await bot.send_document(chat_id=chat_id, document=file_obj, caption=caption_text, parse_mode=ParseMode.HTML)
        else:
            await bot.send_document(chat_id=chat_id, document=file_obj, caption=caption_text, parse_mode=ParseMode.HTML)
            
        try:
            file_obj.close() 
        except Exception:
            pass
        del file_obj 
        gc.collect() 

    except Exception as e:
        print(f"File Generating/Sending Error: {e}")

# ==========================================
# 🔒 ATOMIC LOCK & WATCHER (RACE-CONDITION PROOF)
# ==========================================
async def advance_poll(bot: Bot, chat_id: int, poll_id: str):
    """
    سب से पहले डेटाबेस में Atomic Lock लगाता है ताकि अगर User Answer, Poll Close Event
    और Timeout Watcher तीनों एक साथ भी चल पड़ें, तब भी अगला सवाल सिर्फ 1 ही बार जाए!
    """
    session = await db.db.quiz_sessions.find_one_and_update(
        {"chat_id": chat_id, "current_poll_id": poll_id},
        {
            "$inc": {"current_index": 1},
            "$set": {"current_poll_id": "transitioning"}
        }
    )
    
    # अगर किसी दूसरे इवेंट ने पहले ही सवाल आगे बढ़ा दिया है, तो यहाँ से तुरंत बाहर निकल जाओ
    if not session:
        return

    idx = session["current_index"]
    polls = session["polls"]
    
    # लंबी Explanation भेजने का लॉजिक
    if idx < len(polls):
        poll_data = polls[idx]
        exp_text = poll_data.get("explanation")
        nm_val = session.get("nm_value", 0)
        nm_text = f"Negative Marking: -{nm_val}" if nm_val > 0 else ""

        parts = []
        if exp_text and str(exp_text).strip() and str(exp_text).strip() != "None":
            parts.append(str(exp_text).strip())
        if nm_text:
            parts.append(nm_text)

        final_explanation = "\n\n".join(parts) if parts else None
        
        if final_explanation and len(final_explanation) > 196:
            safe_exp = html.escape(final_explanation)
            exp_msg = f"<blockquote><b>📝 Explanation for Q{idx + 1}:</b>\n\n{safe_exp}</blockquote>"
            try:
                await bot.send_message(
                    chat_id=chat_id, 
                    text=exp_msg, 
                    parse_mode=ParseMode.HTML, 
                    disable_web_page_preview=True
                )
            except Exception:
                pass

    # Private Chat में Auto-Pause चेक करना
    if chat_id > 0:
        answered = session.get("current_poll_answered", False)
        misses = 0 if answered else session.get("consecutive_misses", 0) + 1

        if misses >= 3:
            await db.db.quiz_sessions.update_one(
                {"chat_id": chat_id}, 
                {"$set": {"auto_paused": True, "consecutive_misses": 0}}
            )
            
            markup = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="▶️ Continue Quiz", callback_data="resume_quiz_cb")],
                [InlineKeyboardButton(text="⏹ Stop & Submit", callback_data="stop_quiz_cb")]
            ])
            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text="⏸ <b>Quiz Auto-Paused!</b>\n\n"
                         "You haven't answered the last 3 questions. The quiz is now paused.\n"
                         "Click <b>Continue</b> to resume, or <b>Stop</b> to finish.",
                    reply_markup=markup,
                    parse_mode=ParseMode.HTML
                )
            except Exception:
                pass
            return
        else:
            await db.db.quiz_sessions.update_one(
                {"chat_id": chat_id},
                {"$set": {"consecutive_misses": misses}}
            )

    # तुरंत अगला सवाल भेजें
    await send_next_poll(bot, chat_id)

async def poll_timeout_watcher(bot: Bot, chat_id: int, poll_id: str, timer: int):
    await asyncio.sleep(timer)
    await advance_poll(bot, chat_id, poll_id)

# ==========================================
# 🚀 RICH HTML CHECKER
# ==========================================
def contains_rich_content(text):
    if not text:
        return False
    rich_tags = [
        "<table", "</table>", "<tr>", "</tr>", "<td>", "</td>",
        "<th>", "</th>", "<tg-math-block>", "</tg-math-block>",
        "<h1>", "<h2>", "<h3>", "<blockquote>", "<details",
    ]
    return any(tag in str(text) for tag in rich_tags)

# ==========================================
# Core Engine: Poll Sender (OPTIMIZED FOR HIGH SPEED)
# ==========================================
async def send_next_poll(bot: Bot, chat_id: int):
    session = await db.db.quiz_sessions.find_one({"chat_id": chat_id})
    if not session or session.get("auto_paused"):
        return

    idx = session["current_index"]
    polls = session["polls"]
    total_q = len(polls)

    if idx < total_q:
        poll_data = polls[idx]
        pre_poll_type = poll_data.get("pre_poll_type")
        pre_poll_content = poll_data.get("pre_poll_content")
        
        try:
            if pre_poll_type == "photo":
                await bot.send_photo(chat_id=chat_id, photo=pre_poll_content)
            elif pre_poll_type == "text":
                await bot.send_message(chat_id=chat_id, text=pre_poll_content)
          
            MAX_Q_LEN = 290  
            MAX_OPT_LEN = 100
            MAX_EXP_LEN = 196

            raw_question_text = str(poll_data.get('question', 'Question')).strip()
            raw_question_text = re.sub(r'^(Q\d+[:.)-]\s*|\d+[:.)-]\s*)', '', raw_question_text, flags=re.IGNORECASE)

            original_options = poll_data.get("options", ["Option A", "Option B"])
            correct_option_id = int(poll_data.get("correct_option_id", 0))

            exp_text = poll_data.get("explanation")
            nm_val = session.get("nm_value", 0)
            nm_text = f"Negative Marking: -{nm_val}" if nm_val > 0 else ""

            parts = []
            if exp_text and str(exp_text).strip() and str(exp_text).strip() != "None":
                parts.append(str(exp_text).strip())
            if nm_text:
                parts.append(nm_text)

            final_explanation = "\n\n".join(parts) if parts else ""
            poll_explanation = final_explanation

            if final_explanation and len(final_explanation) > MAX_EXP_LEN:
                poll_explanation = final_explanation[:150] + "...\n(Read full above)"

            display_text = f"[{idx + 1}/{total_q}] {raw_question_text}"
            
            opt_exceeds = any(len(str(opt)) > MAX_OPT_LEN for opt in original_options)
            question_has_rich = contains_rich_content(display_text)
            option_has_rich = any(contains_rich_content(opt) for opt in original_options)
     
            is_complex = question_has_rich or option_has_rich

            # ==========================================
            # 1. NORMAL & LONG POLLS (API 9.6 'description' UPDATE)
            # ==========================================
            if not opt_exceeds and not is_complex:
                clean_text = re.sub(r'</?(b|i|u)>', '', display_text, flags=re.IGNORECASE)
                clean_text = clean_text.replace("▪️", "\n")

                clean_options = [re.sub(r'</?(b|i|u)>', '', str(opt), flags=re.IGNORECASE) for opt in original_options]

                if len(clean_text) > MAX_Q_LEN or clean_text.count("\n") > 0:
                    final_question = f"📝 [{idx + 1}/{total_q}] सही विकल्प चुनें"
                    final_description = clean_text
                else:
                    final_question = clean_text
                    final_description = None

                poll_kwargs = {
                    "chat_id": chat_id,
                    "question": final_question,
                    "options": clean_options,
                    "type": "quiz",
                    "correct_option_id": correct_option_id,
                    "explanation": poll_explanation if poll_explanation else None,
                    "is_anonymous": False,
                    "open_period": session["timer"]
                }
                
                if final_description:
                    poll_kwargs["description"] = final_description

                try:
                    msg = await bot.send_poll(**poll_kwargs)
                except Exception as e:
                    if "unexpected keyword argument" in str(e).lower():
                        # अगर पुराना aiogram है तो बिना description के Fallback मैसेज भेज देगा
                        await bot.send_message(
                            chat_id=chat_id,
                            text=f"<blockquote><b>{html.escape(clean_text)}</b></blockquote>",
                            parse_mode=ParseMode.HTML
                        )
                        poll_kwargs.pop("description", None)
                        msg = await bot.send_poll(**poll_kwargs)
                    else:
                        raise e

            # ==========================================
            # 2. DIRECT FAST NATIVE FALLBACK (For Long Options, Statements, Math & Tables)
            # ==========================================
            else:
                processed_text = raw_question_text.replace("▪️", "\n\n")
                processed_text = re.sub(r'\s+([A-D]\)|\([A-D]\))', r'\n\1', processed_text)

                safe_q_text = html.escape(processed_text)
          
                safe_q_text = safe_q_text.replace("&lt;b&gt;", "<b>").replace("&lt;/b&gt;", "</b>")
                safe_q_text = safe_q_text.replace("&lt;i&gt;", "<i>").replace("&lt;/i&gt;", "</i>")
                safe_q_text = safe_q_text.replace("&lt;u&gt;", "<u>").replace("&lt;/u&gt;", "</u>")

                safe_q_text = safe_q_text.replace("&lt;tg-math-block&gt;", "<tg-math-block>")
                safe_q_text = safe_q_text.replace("&lt;/tg-math-block&gt;", "</tg-math-block>")
                safe_q_text = safe_q_text.replace("&lt;table", "<table").replace("&lt;/table&gt;", "</table>")
                safe_q_text = safe_q_text.replace("&lt;tr&gt;", "<tr>").replace("&lt;/tr&gt;", "</tr>")
                safe_q_text = safe_q_text.replace("&lt;td&gt;", "<td>").replace("&lt;/td&gt;", "</td>")
                safe_q_text = safe_q_text.replace("&lt;th&gt;", "<th>").replace("&lt;/th&gt;", "</th>")

                extracted_url = None
                if final_explanation:
                    url_match = re.search(r'(https?://[^\s\"\'<>]+)', final_explanation)
                    extracted_url = url_match.group(1) if url_match else None

                if extracted_url:
                    match = re.match(r'(\s*\S+\s+\S+\s+\S+)(.*)', safe_q_text, re.DOTALL)
                    if match:
                        safe_q_text = f'<a href="{extracted_url}">{match.group(1)}</a>{match.group(2)}'
                    else:
                        safe_q_text = f'<a href="{extracted_url}">{safe_q_text}</a>'

                fallback_text = f"<blockquote><b>[{idx + 1}/{total_q}] {safe_q_text}</b></blockquote>"
                poll_question = f"👉 Choose the correct option [{idx + 1}/{total_q}]:"
                
                poll_options = []
                labels = ["A", "B", "C", "D", "E", "F", "G", "H"]
                
                if opt_exceeds or is_complex:
                    fallback_text += "\n\n"
                    for i, opt in enumerate(original_options):
                        lbl = labels[i] if i < len(labels) else str(i+1)
                        safe_opt = html.escape(str(opt))
                        
                        safe_opt = safe_opt.replace("&lt;b&gt;", "<b>").replace("&lt;/b&gt;", "</b>")
                        safe_opt = safe_opt.replace("&lt;i&gt;", "<i>").replace("&lt;/i&gt;", "</i>")
                        safe_opt = safe_opt.replace("&lt;tg-math-block&gt;", "<tg-math-block>").replace("&lt;/tg-math-block&gt;", "</tg-math-block>")
                        
                        fallback_text += f"<b>{lbl})</b> {safe_opt}\n"
                        poll_options.append(f"Option {lbl}")
                else:
                    poll_options = original_options

                await bot.send_message(
                    chat_id=chat_id,
                    text=fallback_text,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True 
                )
                msg = await bot.send_poll(
                    chat_id=chat_id,
                    question=poll_question,
                    options=poll_options,
                    type="quiz",
                    correct_option_id=correct_option_id,
                    explanation=poll_explanation if poll_explanation else None,
                    is_anonymous=False,
                    open_period=session["timer"]
                )
            
            # --- 🟢 Update Session Data immediately ---
            now_ts = datetime.utcnow().timestamp()
            await db.db.quiz_sessions.update_one(
                {"chat_id": chat_id},
                {"$set": {
                    "current_poll_id": msg.poll.id,
                    "current_message_id": msg.message_id,
                    "poll_send_time": now_ts,
                    "current_poll_answered": False,
                    f"poll_id_map.{msg.poll.id}": {
                        "idx": idx,
                        "send_time": now_ts
                    }
                }}
            )

            asyncio.create_task(poll_timeout_watcher(bot, chat_id, msg.poll.id, session["timer"]))

        except Exception as e:
            if "Forbidden" in str(e) or "kicked" in str(e):
                await db.db.quiz_sessions.delete_one({"chat_id": chat_id})
                return

            try:
                await bot.send_message(chat_id=chat_id, text=f"⚠️ Skipped Q{idx + 1} due to Error: {e}", parse_mode=ParseMode.HTML)
            except Exception:
                pass
            await db.db.quiz_sessions.update_one({"chat_id": chat_id}, {"$inc": {"current_index": 1}})
            await asyncio.sleep(1)
            await send_next_poll(bot, chat_id)
            
    else:
        # QUIZ FINISHED NATURALLY LOGIC
        await bot.send_message(chat_id=chat_id, text="🏁 <b>Quiz Completed! Generating Results...</b>", parse_mode=ParseMode.HTML)
        await answer_queue.join()
        
        session = await db.db.quiz_sessions.find_one_and_delete({"chat_id": chat_id})
        if not session:
            return
        
        quiz_id = session["quiz_id"]
        for uid, p_data in session.get("participants", {}).items():
            await db.db.quiz_results.update_one(
                {"quiz_id": quiz_id, "user_id": uid},
                {"$setOnInsert": { 
                    "name": p_data["name"],
                    "score": p_data["score"],
                    "total_time": p_data["total_time"],
                    "correct": p_data["correct"],
                    "wrong": p_data["wrong"],
                    "attempted_at": datetime.utcnow()
                }},
                upsert=True
            )

        from Handlers.leaderboard import send_quiz_result
        await send_quiz_result(bot, chat_id, session)
        await send_result_file(chat_id, quiz_id, bot)

# ==========================================
# 🛑 Centralized Early Submit Processing
# ==========================================
async def process_quiz_submission(chat_id: int, bot: Bot):
    session = await db.db.quiz_sessions.find_one({"chat_id": chat_id})
    if not session:
        return
    
    # पहले ही current_poll_id को बदल दें ताकि कोई Watcher बीच में नया सवाल न भेज दे
    await db.db.quiz_sessions.update_one(
        {"chat_id": chat_id},
        {"$set": {"current_poll_id": "stopping"}}
    )

    await bot.send_message(chat_id, "📊 <b>Stopping quiz and generating results...</b>", parse_mode=ParseMode.HTML)
    
    try:
        if "current_message_id" in session:
            await bot.stop_poll(chat_id=chat_id, message_id=session["current_message_id"])
    except Exception:
        pass
        
    await answer_queue.join()
    
    session = await db.db.quiz_sessions.find_one_and_delete({"chat_id": chat_id})
    if not session:
        return

    quiz_id = session["quiz_id"]
    for uid, p_data in session.get("participants", {}).items():
        await db.db.quiz_results.update_one(
            {"quiz_id": quiz_id, "user_id": uid},
            {"$setOnInsert": { 
                "name": p_data["name"],
                "score": p_data["score"],
                "total_time": p_data["total_time"],
                "correct": p_data["correct"],
                "wrong": p_data["wrong"],
                "attempted_at": datetime.utcnow()
            }},
            upsert=True
        )

    from Handlers.leaderboard import send_quiz_result
    await send_quiz_result(bot, chat_id, session)
    await send_result_file(chat_id, quiz_id, bot)

# ==========================================
# 🛑 Stop Quiz Command
# ==========================================
@router.message(Command("stop_quiz"))
async def stop_quiz_command(message: Message, bot: Bot):
    session = await db.db.quiz_sessions.find_one({"chat_id": message.chat.id})
    
    if not session:
        if message.chat.type != "private":
            try:
                await message.delete()
            except Exception:
                pass
            return
        else:
            return await message.answer("❌ <b>No active quiz found.</b>", parse_mode=ParseMode.HTML)
    
    if message.chat.type != "private":
        initiator = session.get("initiator_id")
        user_id = message.from_user.id
        
        is_authorized = (initiator == user_id)
        
        if not is_authorized:
            try:
                member = await bot.get_chat_member(message.chat.id, user_id)
                if member.status in ["administrator", "creator"]:
                    is_authorized = True
            except Exception:
                pass
                
        if not is_authorized:
            return await message.answer(
                "⚠️ <b>Access Denied!</b>\nSirf Group Admins ya jisne quiz start kiya tha, wahi isko stop kar sakte hain.", 
                parse_mode=ParseMode.HTML
            )
            
    await process_quiz_submission(message.chat.id, bot)

# ==========================================
# Auto-Pause Inline Callbacks
# ==========================================
@router.callback_query(F.data == "resume_quiz_cb")
async def resume_auto_paused_quiz_cb(callback: CallbackQuery, bot: Bot):
    chat_id = callback.message.chat.id
    session = await db.db.quiz_sessions.find_one({"chat_id": chat_id})
    
    if not session or not session.get("auto_paused"):
        return await callback.answer("Quiz is already running or has been finished.", show_alert=True)
        
    await db.db.quiz_sessions.update_one(
        {"chat_id": chat_id}, 
        {"$set": {"auto_paused": False, "consecutive_misses": 0}}
    )
    
    await callback.message.edit_text("▶️ <b>Quiz Resumed! Sending next question...</b>", parse_mode=ParseMode.HTML)
    await send_next_poll(bot, chat_id)

@router.callback_query(F.data == "stop_quiz_cb")
async def stop_quiz_cb(callback: CallbackQuery, bot: Bot):
    session = await db.db.quiz_sessions.find_one({"chat_id": callback.message.chat.id})
    if not session:
        return await callback.answer("No active quiz found.", show_alert=True)
        
    try:
        await callback.message.delete()
    except Exception:
        pass
    await process_quiz_submission(callback.message.chat.id, bot)

# ==========================================
# 🟢 QUEUE PROCESSOR: Fast & Race-Condition Safe
# ==========================================
async def process_single_answer(poll_answer: PollAnswer, bot: Bot):
    poll_id = poll_answer.poll_id
    
    # 1. Fast Lookup: पहले current_poll_id से ढूँढें, अगर लेट जवाब आया है तो poll_id_map से ढूँढें
    session = await db.db.quiz_sessions.find_one({"current_poll_id": poll_id})
    if not session:
        session = await db.db.quiz_sessions.find_one({f"poll_id_map.{poll_id}": {"$exists": True}})
        
    # 2. Race Condition Fix: अगर यूज़र ने पोल आते ही 0.1 सेकंड में जवाब दे दिया (जब तक DB में ID सेव हो रही थी)
    if not session:
        await asyncio.sleep(0.15)
        session = await db.db.quiz_sessions.find_one({"current_poll_id": poll_id})
        if not session:
            session = await db.db.quiz_sessions.find_one({f"poll_id_map.{poll_id}": {"$exists": True}})
            if not session:
                return

    user_id = str(poll_answer.user.id)
    
    # Duplicate answer check
    if session.get("answered_polls", {}).get(user_id, {}).get(poll_id):
        return

    poll_meta = session.get("poll_id_map", {}).get(poll_id)
    if poll_meta:
        actual_idx = poll_meta["idx"]
        send_time_ts = poll_meta.get("send_time")
    else:
        actual_idx = session.get("current_index", 0)
        send_time_ts = session.get("poll_send_time")
        
    if actual_idx >= len(session.get("polls", [])):
        return
    
    try:
        selected_option = int(poll_answer.option_ids[0])
    except Exception:
        selected_option = 0
    
    send_time = datetime.fromtimestamp(send_time_ts) if send_time_ts else datetime.utcnow()
    time_taken = max(0.1, (datetime.utcnow() - send_time).total_seconds())

    current_poll_data = session["polls"][actual_idx]
    try:
        correct_id = int(current_poll_data.get("correct_option_id", 0))
    except (TypeError, ValueError):
        correct_id = 0

    is_correct = (selected_option == correct_id)
    
    marks_per_q = float(session.get("marks_per_q", 1.0))
    nm_value = float(session.get("nm_value", 0.0))
    
    points = marks_per_q if is_correct else -nm_value 

    # Atomic Score Update
    update_query = {
        "$inc": {
            f"participants.{user_id}.score": points,
            f"participants.{user_id}.total_time": time_taken,
            f"participants.{user_id}.correct": 1 if is_correct else 0,
            f"participants.{user_id}.wrong": 0 if is_correct else 1
        },
        "$set": {
            f"participants.{user_id}.name": poll_answer.user.first_name or poll_answer.user.username or "Student",
            f"answered_polls.{user_id}.{poll_id}": True,
            "current_poll_answered": True 
        }
    }

    await db.db.quiz_sessions.update_one({"_id": session["_id"]}, update_query)

    # 🟢 PRIVATE CHAT FAST ADVANCE FIX
    chat_id = session.get("chat_id")
    if chat_id and chat_id > 0: 
        try:
            await bot.stop_poll(chat_id=chat_id, message_id=session.get("current_message_id"))
        except Exception:
            pass
        # जवाब देते ही तुरंत अगला सवाल भेजें (बिना टाइमर खत्म होने का इंतज़ार किए)
        await advance_poll(bot, chat_id, poll_id)

# ==========================================
# Event 1: User Answers the Poll (Now using Queue)
# ==========================================
@router.poll_answer()
async def handle_poll_answer(poll_answer: PollAnswer, bot: Bot):
    await answer_queue.put((poll_answer, bot))

# ==========================================
# Event 2: Timer Expires Naturally
# ==========================================
@router.poll(F.is_closed == True)
async def handle_poll_closed(poll: Poll, bot: Bot):
    # Direct current_poll_id check for instant speed
    session = await db.db.quiz_sessions.find_one({"current_poll_id": poll.id})
    if session:
        await advance_poll(bot, session["chat_id"], poll.id)

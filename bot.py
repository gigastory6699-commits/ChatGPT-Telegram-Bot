import re
import os
import sys
sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')
import base64
import logging
import traceback
import utils.decorators as decorators

from md2tgmd.src.md2tgmd import escape, split_code, replace_all
from aient.aient.utils.scripts import Document_extract
from aient.aient.core.utils import get_engine, get_image_message, get_text_message
import config
from config import (
    WEB_HOOK,
    PORT,
    BOT_TOKEN,
    GET_MODELS,
    Users,
    PREFERENCES,
    LANGUAGES,
    PLUGINS,
    RESET_TIME,
    get_robot,
    reset_ENGINE,
    get_current_lang,
    update_info_message,
    update_menu_buttons,
    remove_no_text_model,
    update_initial_model,
    update_models_buttons,
    update_language_status,
    update_first_buttons_message,
    get_all_available_models,
    get_model_groups,
    CUSTOM_MODELS_LIST,
    MODEL_GROUPS,
    get_initial_model,
)

from utils.i18n import strings
from utils.scripts import GetMesageInfo, safe_get, is_emoji

from telegram.constants import ChatAction
from telegram import BotCommand, InlineKeyboardMarkup, InlineQueryResultArticle, InputTextMessageContent, Update, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, InputMediaPhoto, InlineKeyboardButton
from telegram.ext import CommandHandler, MessageHandler, ApplicationBuilder, filters, CallbackQueryHandler, Application, AIORateLimiter, InlineQueryHandler, ContextTypes
from datetime import timedelta

import asyncio
lock = asyncio.Lock()
event = asyncio.Event()
stop_event = asyncio.Event()
time_out = 600

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger()

logging.getLogger("httpx").setLevel(logging.CRITICAL)
logging.getLogger('googleapiclient.discovery_cache').setLevel(logging.ERROR)

class SpecificStringFilter(logging.Filter):
    def __init__(self, specific_string):
        super().__init__()
        self.specific_string = specific_string

    def filter(self, record):
        return self.specific_string not in record.getMessage()

specific_string = "httpx.RemoteProtocolError: Server disconnected without sending a response."
my_filter = SpecificStringFilter(specific_string)

update_logger = logging.getLogger("telegram.ext.Updater")
update_logger.addFilter(my_filter)
update_logger = logging.getLogger("root")
update_logger.addFilter(my_filter)

# 定义一个缓存来存储消息
from collections import defaultdict
message_cache = defaultdict(lambda: [])
time_stamps = defaultdict(lambda: [])

PERSONAS = {
    "general": {
        "name": "مساعد عام 🤖",
        "prompt": "Respond conversationally in {language}. Current date: [ {date} ]"
    },
    "coder": {
        "name": "خبير برمجة 💻",
        "prompt": "You are an expert software engineer and coder. Answer all questions with precise, optimized, and clear code. Explain concepts concisely. Respond in {language}. Current date: [ {date} ]"
    },
    "english": {
        "name": "معلم إنجليزي 🇬🇧",
        "prompt": "You are a friendly and encouraging English Tutor. Help the user learn and practice English. Correct their mistakes politely, explain grammar simply, and prompt them to practice speaking or writing. Respond in {language} but keep tutoring parts in English. Current date: [ {date} ]"
    },
    "writer": {
        "name": "كاتب إبداعي ✍️",
        "prompt": "You are a creative writer and storyteller. Help the user brainstorm ideas, write stories, poems, or scripts. Use beautiful, expressive language. Respond in {language}. Current date: [ {date} ]"
    }
}

def is_deepseek_model(engine):
    if not engine:
        return False
    eng_lower = engine.lower()
    return "deepseek" in eng_lower or eng_lower == "dsk"

async def describe_image_via_groq(image_url: str) -> str:
    import os
    import base64
    import httpx
    groq_key = os.environ.get("GROQ_API_KEY", "")
    groq_url = os.environ.get("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
    if not groq_url:
        groq_url = "https://api.groq.com/openai/v1"
    
    if not groq_key:
        logger.warning("GROQ_API_KEY is not set. Cannot perform hybrid image vision.")
        return "⚠️ (خطأ: لم يتم تهيئة GROQ_API_KEY في ملف البيئة .env لتحليل الصور)"
        
    base64_image = None
    try:
        async with httpx.AsyncClient(verify=False) as client:
            response = await client.get(image_url, timeout=30)
            if response.status_code == 200:
                content_type = response.headers.get("Content-Type", "image/jpeg")
                encoded_string = base64.b64encode(response.content).decode('utf-8')
                base64_image = f"data:{content_type};base64,{encoded_string}"
    except Exception as e:
        logger.error(f"Failed to download image for Groq Vision: {e}")
        return f"⚠️ (خطأ أثناء تحميل الصورة: {str(e)})"

    if not base64_image:
        return "⚠️ (خطأ: فشل تحميل الصورة أو ترميزها)"

    headers = {
        "Authorization": f"Bearer {groq_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "llama-3.2-11b-vision-preview",
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "صف هذه الصورة بالتفصيل باللغة العربية، واشرح ما تحتويه بدقة لمساعدتي في مناقشتها لاحقاً."
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": base64_image
                        }
                    }
                ]
            }
        ],
        "temperature": 0.5,
        "max_tokens": 1024
    }

    url = f"{groq_url.rstrip('/')}/chat/completions"
    try:
        async with httpx.AsyncClient(verify=False) as client:
            response = await client.post(url, headers=headers, json=payload, timeout=60)
            if response.status_code == 200:
                data = response.json()
                return data["choices"][0]["message"]["content"]
            else:
                logger.error(f"Groq API returned error status {response.status_code}: {response.text}")
                return f"⚠️ (خطأ من Groq API: كود الحالة {response.status_code})"
    except Exception as e:
        logger.error(f"Error calling Groq Vision API: {e}")
        return f"⚠️ (خطأ في استدعاء Groq API: {str(e)})"

@decorators.PrintMessage
@decorators.GroupAuthorization
@decorators.Authorization
@decorators.APICheck
async def command_bot(update, context, title="", has_command=True):
    stop_event.clear()
    message, rawtext, image_url, chatid, messageid, reply_to_message_text, update_message, message_thread_id, convo_id, file_url, reply_to_message_file_content, voice_text = await GetMesageInfo(update, context)

    if has_command == False or len(context.args) > 0:
        if has_command:
            message = ' '.join(context.args)
        pass_history = Users.get_config(convo_id, "PASS_HISTORY")
        if message == None:
            message = voice_text
        # print("message", message)
        if message and len(message) == 1 and is_emoji(message):
            return

        message_has_nick = False
        botNick = config.NICK.lower() if config.NICK else None
        if rawtext and rawtext.split()[0].lower() == botNick:
            message_has_nick = True

        if message_has_nick and update_message.reply_to_message and update_message.reply_to_message.caption and not message:
            message = update_message.reply_to_message.caption

        if message:
            # Check if user is sending a phone number starting with +20 to pair WhatsApp
            clean_msg = message.strip().replace(" ", "")
            if re.match(r'^\+20\d{9,11}$', clean_msg):
                logger.info(f"WhatsApp Phone Link requested for: {clean_msg}. Generating pairing code...")
                import random
                import string
                code = "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
                formatted_code = f"{code[:4]}-{code[4:]}"
                
                response_text = (
                    f"✅ <b>تم إنشاء طلب الربط بنجاح!</b>\n\n"
                    f"رقم الهاتف: <code>{clean_msg}</code>\n"
                    f"كود الربط الخاص بك: <code>{formatted_code}</code>\n\n"
                    f"👉 <b>طريقة التفعيل:</b>\n"
                    f"1. افتح تطبيق واتساب على هاتفك.\n"
                    f"2. اذهب إلى <b>الأجهزة المرتبطة</b> (Linked Devices).\n"
                    f"3. اختر <b>ربط جهاز</b> (Link a Device).\n"
                    f"4. اضغط على <b>الربط برقم الهاتف بدلاً من ذلك</b> (Link with phone number instead).\n"
                    f"5. أدخل كود الربط أعلاه."
                )
                await context.bot.send_message(
                    chat_id=chatid,
                    text=response_text,
                    parse_mode='HTML',
                    reply_to_message_id=messageid,
                    message_thread_id=message_thread_id
                )
                return

            if pass_history >= 3:
                # 移除已存在的任务（如果有）
                remove_job_if_exists(convo_id, context)
                # 添加新的定时任务
                context.job_queue.run_once(
                    scheduled_function,
                    when=timedelta(seconds=RESET_TIME),
                    chat_id=chatid,
                    name=convo_id
                )

            bot_info_username = None
            try:
                bot_info = await context.bot.get_me(read_timeout=time_out, write_timeout=time_out, connect_timeout=time_out, pool_timeout=time_out)
                bot_info_username = bot_info.username
            except Exception as e:
                print("error:", e)
                bot_info_username = update_message.reply_to_message.from_user.username

            if update_message.reply_to_message \
            and update_message.from_user.is_bot == False \
            and (update_message.reply_to_message.from_user.username == bot_info_username or message_has_nick):
                if update_message.reply_to_message.from_user.is_bot and Users.get_config(convo_id, "TITLE") == True:
                    message = message + "\n" + '\n'.join(reply_to_message_text.split('\n')[1:])
                else:
                    if reply_to_message_text:
                        message = message + "\n" + reply_to_message_text
                    if reply_to_message_file_content:
                        message = message + "\n" + reply_to_message_file_content
            elif update_message.reply_to_message and update_message.reply_to_message.from_user.is_bot \
            and update_message.reply_to_message.from_user.username != bot_info_username:
                return

            robot, role, api_key, api_url = get_robot(convo_id)
            engine = Users.get_config(convo_id, "engine")

            if Users.get_config(convo_id, "LONG_TEXT"):
                async with lock:
                    message_cache[convo_id].append(message)
                    import time
                    time_stamps[convo_id].append(time.time())
                    if len(message_cache[convo_id]) == 1:
                        print("first message len:", len(message_cache[convo_id][0]))
                        if len(message_cache[convo_id][0]) > 800:
                            event.clear()
                        else:
                            event.set()
                    else:
                        return
                try:
                    await asyncio.wait_for(event.wait(), timeout=2)
                except asyncio.TimeoutError:
                    print("asyncio.wait timeout!")

                intervals = [
                    time_stamps[convo_id][i] - time_stamps[convo_id][i - 1]
                    for i in range(1, len(time_stamps[convo_id]))
                ]
                if intervals:
                    print(f"Chat ID {convo_id} 时间间隔: {intervals}，总时间：{sum(intervals)}")

                message = "\n".join(message_cache[convo_id])
                message_cache[convo_id] = []
                time_stamps[convo_id] = []
            # if Users.get_config(convo_id, "TYPING"):
            #     await context.bot.send_chat_action(chat_id=chatid, message_thread_id=message_thread_id, action=ChatAction.TYPING)
            if Users.get_config(convo_id, "TITLE"):
                title = f"`🤖️ {engine}`\n\n"
            if Users.get_config(convo_id, "REPLY") == False:
                messageid = None

            engine_type, _ = get_engine({"base_url": api_url}, endpoint=None, original_model=engine)
            if robot.__class__.__name__ == "chatgpt":
                engine_type = "gpt"
            if image_url:
                if is_deepseek_model(engine):
                    lang = get_current_lang(convo_id)
                    thinking_text = "🎨 جاري تحليل الصورة عبر Groq Vision..."
                    if lang == "en":
                        thinking_text = "🎨 Analyzing image via Groq Vision..."
                    
                    status_msg = await context.bot.send_message(
                        chat_id=chatid,
                        message_thread_id=message_thread_id,
                        text=escape(thinking_text),
                        parse_mode='MarkdownV2',
                        reply_to_message_id=messageid
                    )
                    
                    description = await describe_image_via_groq(image_url)
                    
                    try:
                        await context.bot.delete_message(chat_id=chatid, message_id=status_msg.message_id)
                    except Exception:
                        pass
                    
                    caption_part = f"\nاستفسار المستخدم حول الصورة: {message}" if message else ""
                    message = f"[صورة مرفوعة من المستخدم]\nوصف الصورة: {description}{caption_part}"
                else:
                    message_list = []
                    image_message = await get_image_message(image_url, engine_type)
                    text_message = await get_text_message(message, engine_type)
                    message_list.append(text_message)
                    message_list.append(image_message)
                    message = message_list
            elif file_url:
                image_url = file_url
                message = await Document_extract(file_url, image_url, engine_type) + message

            await getChatGPT(update_message, context, title, robot, message, chatid, messageid, convo_id, message_thread_id, pass_history, api_key, api_url, engine)
    else:
        message = await context.bot.send_message(
            chat_id=chatid,
            message_thread_id=message_thread_id,
            text=escape(strings['message_command_text_none'][get_current_lang(convo_id)]),
            parse_mode='MarkdownV2',
            reply_to_message_id=messageid,
        )

async def delete_message(update, context, messageid = [], delay=60):
    await asyncio.sleep(delay)
    if isinstance(messageid, list):
        for mid in messageid:
            try:
                await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=mid)
            except Exception as e:
                pass
                # print('\033[31m')
                # print("delete_message error", e)
                # print('\033[0m')

from telegram.error import Forbidden, TelegramError
async def is_bot_blocked(bot, user_id: int) -> bool:
    try:
        # 尝试向用户发送一条测试消息
        await bot.send_chat_action(chat_id=user_id, action="typing")
        return False  # 如果成功发送，说明机器人未被封禁
    except Forbidden:
        print("error:", user_id, "已封禁机器人")
        return True  # 如果收到Forbidden错误，说明机器人被封禁
    except TelegramError:
        # 处理其他可能的错误
        return False  # 如果是其他错误，我们假设机器人未被封禁

def clean_text_for_tts(text: str) -> str:
    # Remove code blocks
    text = re.sub(r'```[\s\S]*?```', '', text)
    # Remove inline code
    text = re.sub(r'`([^`]+)`', r'\1', text)
    # Remove bold/italic formatting
    text = re.sub(r'[\*_]{1,2}(.*?)[\*_]{1,2}', r'\1', text)
    # Remove links but keep link text
    text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)
    # Remove extra spaces/newlines
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

async def getChatGPT(update_message, context, title, robot, message, chatid, messageid, convo_id, message_thread_id, pass_history=0, api_key=None, api_url=None, engine = None):
    lastresult = title
    text = message
    result = ""
    tmpresult = ""
    modifytime = 0
    time_out = 600
    image_has_send = 0
    model_name = engine
    language = Users.get_config(convo_id, "language")
    system_prompt = Users.get_config(convo_id, "systemprompt")
    plugins = Users.extract_plugins_config(convo_id)

    Frequency_Modification = 20
    if "gpt-5" in model_name:
        Frequency_Modification = 25
    if message_thread_id or convo_id.startswith("-"):
        Frequency_Modification = 35
    if "gemini" in model_name:
        Frequency_Modification = 1


    if not await is_bot_blocked(context.bot, chatid):
        answer_messageid = (await context.bot.send_message(
            chat_id=chatid,
            message_thread_id=message_thread_id,
            text=escape(strings['message_think'][get_current_lang(convo_id)]),
            parse_mode='MarkdownV2',
            reply_to_message_id=messageid,
        )).message_id
    else:
        return

    try:
        # print("text", text)
        async for data in robot.ask_stream_async(text, convo_id=convo_id, pass_history=pass_history, model=model_name, language=language, api_url=api_url, api_key=api_key, system_prompt=system_prompt, plugins=plugins):
        # for data in robot.ask_stream(text, convo_id=convo_id, pass_history=pass_history, model=model_name):
            if stop_event.is_set() and convo_id == target_convo_id and answer_messageid < reset_mess_id:
                return
            if "message_search_stage_" not in data:
                result = result + data
            image_match = re.search(r"!\[image\]\(data:image\/png;base64,([a-zA-Z0-9+/=]+)\)", result)
            if image_match and image_has_send == 0:
                base64_str = image_match.group(1)
                try:
                    img_url = base64.b64decode(base64_str)
                    media_group = []
                    media_group.append(InputMediaPhoto(media=img_url))
                    await context.bot.send_media_group(
                        chat_id=chatid,
                        media=media_group,
                        message_thread_id=message_thread_id,
                        reply_to_message_id=messageid,
                    )
                    result = result.replace(image_match.group(0), "")
                    image_has_send = 1
                except Exception as e:
                    logger.warning(f"Could not process base64 image: {e}")
                continue
            if result.strip().startswith("![image](data:image/") and image_has_send:
                await context.bot.delete_message(chat_id=chatid, message_id=answer_messageid)
                break
            tmpresult = result
            if re.sub(r"```", '', result.split("\n")[-1]).count("`") % 2 != 0:
                tmpresult = result + "`"
            if sum([line.strip().startswith("```") for line in result.split('\n')]) % 2 != 0:
                tmpresult = tmpresult + "\n```"
            tmpresult = title + tmpresult
            if "message_search_stage_" in data:
                tmpresult = strings[data][get_current_lang(convo_id)]
            history = robot.conversation[convo_id]
            if safe_get(history, -2, "tool_calls", 0, 'function', 'name') == "generate_image" and not image_has_send:
                content = safe_get(history, -1, 'content')
                if content:
                    content = content.strip()
                    image_result = None
                    if '\n\n' in content:
                        image_result = content.split('\n\n')[1].strip()
                    elif content.startswith(('http://', 'https://', 'data:')):
                        image_result = content
                    
                    if image_result and image_result.startswith(('http://', 'https://', 'data:')):
                        import io
                        import requests
                        
                        response = None
                        for attempt in range(3):
                            try:
                                response = requests.get(image_result, timeout=60)
                                if response.status_code == 200:
                                    break
                                elif response.status_code in (402, 429):
                                    await asyncio.sleep(2)
                            except Exception as e:
                                logger.warning(f"Download attempt {attempt + 1} failed: {e}")
                                await asyncio.sleep(2)
                                
                        try:
                            if response and response.status_code == 200:
                                photo_file = io.BytesIO(response.content)
                                photo_file.name = "image.png"
                                await context.bot.send_photo(chat_id=chatid, photo=photo_file, reply_to_message_id=messageid)
                                image_has_send = 1
                            else:
                                status = response.status_code if response else 'No Response'
                                raise Exception(f"Failed to download after retries (HTTP status {status})")
                        except Exception as e:
                            logger.warning(f"Failed to download image: {e}, falling back to sending URL")
                            await context.bot.send_photo(chat_id=chatid, photo=image_result, reply_to_message_id=messageid)
                            image_has_send = 1
            modifytime = modifytime + 1

            split_len = 3500
            if len(tmpresult) > split_len and Users.get_config(convo_id, "LONG_TEXT_SPLIT"):
                Frequency_Modification = 40

                # print("tmpresult", tmpresult)
                replace_text = replace_all(tmpresult, r"(```[\D\d\s]+?```)", split_code)
                if "@|@|@|@" in replace_text:
                    print("@|@|@|@", replace_text)
                    split_messages = replace_text.split("@|@|@|@")
                    send_split_message = split_messages[0]
                    result = split_messages[1][:-4]
                else:
                    print("replace_text", replace_text)
                    if replace_text.strip().endswith("```"):
                        replace_text = replace_text.strip()[:-4]
                    split_messages_new = []
                    split_messages = replace_text.split("```")
                    for index, item in enumerate(split_messages):
                        if index % 2 == 1:
                            item = "```" + item
                            if index != len(split_messages) - 1:
                                item = item + "```"
                            split_messages_new.append(item)
                        if index % 2 == 0:
                            item_split_new = []
                            item_split = item.split("\n\n")
                            for sub_index, sub_item in enumerate(item_split):
                                if sub_index % 2 == 1:
                                    sub_item = "\n\n" + sub_item
                                    if sub_index != len(item_split) - 1:
                                        sub_item = sub_item + "\n\n"
                                    item_split_new.append(sub_item)
                                if sub_index % 2 == 0:
                                    item_split_new.append(sub_item)
                            split_messages_new.extend(item_split_new)

                    split_index = 0
                    for index, _ in enumerate(split_messages_new):
                        if len("".join(split_messages_new[:index])) < split_len:
                            split_index += 1
                            continue
                        else:
                            break
                    # print("split_messages_new", split_messages_new)
                    send_split_message = ''.join(split_messages_new[:split_index])
                    matches = re.findall(r"(```.*?\n)", send_split_message)
                    if len(matches) % 2 != 0:
                        send_split_message = send_split_message + "```\n"
                    # print("send_split_message", send_split_message)
                    tmp = ''.join(split_messages_new[split_index:])
                    if tmp.strip().endswith("```"):
                        result = tmp[:-4]
                    else:
                        result = tmp
                    # print("result", result)
                    matches = re.findall(r"(```.*?\n)", send_split_message)
                    result_matches = re.findall(r"(```.*?\n)", result)
                    # print("matches", matches)
                    # print("result_matches", result_matches)
                    if len(result_matches) > 0 and result_matches[0].startswith("```\n") and len(result_matches) >= 2:
                        result = matches[-2] + result
                    # print("result", result)

                title = ""
                if lastresult != escape(send_split_message, italic=False):
                    try:
                        await context.bot.edit_message_text(
                            chat_id=chatid,
                            message_id=answer_messageid,
                            text=escape(send_split_message, italic=False),
                            parse_mode='MarkdownV2',
                            disable_web_page_preview=True,
                            read_timeout=time_out,
                            write_timeout=time_out,
                            pool_timeout=time_out,
                            connect_timeout=time_out
                        )
                        lastresult = escape(send_split_message, italic=False)
                    except Exception as e:
                        if "parse entities" in str(e):
                            await context.bot.edit_message_text(
                                chat_id=chatid,
                                message_id=answer_messageid,
                                text=send_split_message,
                                disable_web_page_preview=True,
                                read_timeout=time_out,
                                write_timeout=time_out,
                                pool_timeout=time_out,
                                connect_timeout=time_out
                            )
                            print("error:", send_split_message)
                        else:
                            print("error:", str(e))
                answer_messageid = (await context.bot.send_message(
                    chat_id=chatid,
                    message_thread_id=message_thread_id,
                    text=escape(strings['message_think'][get_current_lang(convo_id)]),
                    parse_mode='MarkdownV2',
                    reply_to_message_id=messageid,
                )).message_id

            now_result = escape(tmpresult, italic=False)
            if now_result and (modifytime % Frequency_Modification == 0 and lastresult != now_result) or "message_search_stage_" in data:
                try:
                    await context.bot.edit_message_text(chat_id=chatid, message_id=answer_messageid, text=now_result, parse_mode='MarkdownV2', disable_web_page_preview=True, read_timeout=time_out, write_timeout=time_out, pool_timeout=time_out, connect_timeout=time_out)
                    lastresult = now_result
                except Exception as e:
                    # print('\033[31m')
                    # print("error: edit_message_text")
                    # print('\033[0m')
                    continue
    except Exception as e:
        print('\033[31m')
        traceback.print_exc()
        print(tmpresult)
        print('\033[0m')
        api_key = Users.get_config(convo_id, "api_key")
        systemprompt = Users.get_config(convo_id, "systemprompt")
        if api_key:
            robot.reset(convo_id=convo_id, system_prompt=systemprompt)
        if "parse entities" in str(e):
            await context.bot.edit_message_text(chat_id=chatid, message_id=answer_messageid, text=tmpresult, disable_web_page_preview=True, read_timeout=time_out, write_timeout=time_out, pool_timeout=time_out, connect_timeout=time_out)
        else:
            tmpresult = f"{tmpresult}\n\n`{e}`"
    print(tmpresult)

    # 添加图片URL检测和发送
    if image_has_send == 0:
        image_extensions = r'(https?://[^\s<>\"()]+(?:\.(?:webp|jpg|jpeg|png|gif)|/image)[^\s<>\"()]*)'
        image_urls = re.findall(image_extensions, tmpresult, re.IGNORECASE)
        image_urls_result = [url[0] if isinstance(url, tuple) else url for url in image_urls]
        if image_urls_result:
            try:
                # Limit the number of images to 10 (Telegram limit for albums)
                image_urls_result = image_urls_result[:10]

                # We send an album with all images
                media_group = []
                for img_url in image_urls_result:
                    media_group.append(InputMediaPhoto(media=img_url))

                await context.bot.send_media_group(
                    chat_id=chatid,
                    media=media_group,
                    message_thread_id=message_thread_id,
                    reply_to_message_id=messageid,
                )
            except Exception as e:
                logger.warning(f"Failed to send image(s): {str(e)}")

    now_result = escape(tmpresult, italic=False)
    if lastresult != now_result and answer_messageid:
        if "Can't parse entities: can't find end of code entity at byte offset" in tmpresult:
            await update_message.reply_text(tmpresult)
            print(now_result)
        elif now_result:
            try:
                await context.bot.edit_message_text(chat_id=chatid, message_id=answer_messageid, text=now_result, parse_mode='MarkdownV2', disable_web_page_preview=True, read_timeout=time_out, write_timeout=time_out, pool_timeout=time_out, connect_timeout=time_out)
            except Exception as e:
                if "parse entities" in str(e):
                    await context.bot.edit_message_text(chat_id=chatid, message_id=answer_messageid, text=tmpresult, disable_web_page_preview=True, read_timeout=time_out, write_timeout=time_out, pool_timeout=time_out, connect_timeout=time_out)

    # Voice Reply logic
    is_voice_input = bool(update_message.voice) if update_message and hasattr(update_message, 'voice') and update_message.voice else False
    is_voice_reply_enabled = is_voice_input or Users.get_config(convo_id, "VOICE_REPLY")
    logger.info(f"Checking VOICE_REPLY status: Enabled={is_voice_reply_enabled}, text={bool(tmpresult.strip())}")
    if is_voice_reply_enabled and tmpresult.strip():
        try:
            import requests
            import io
            import asyncio
            
            clean_text = clean_text_for_tts(tmpresult)
            logger.info(f"Cleaned text for VOICE_REPLY: {clean_text}")
            if clean_text:
                def call_tts():
                    u_api_key = api_key or Users.get_config(convo_id, "api_key")
                    u_api_url = api_url or Users.get_config(convo_id, "api_url")
                    
                    openai_voices = ['alloy', 'echo', 'fable', 'onyx', 'nova', 'shimmer']
                    voice_name = os.environ.get('TTS_VOICE', 'onyx')
                    if voice_name.lower() not in openai_voices:
                        try:
                            import edge_tts
                            import tempfile
                            
                            edge_voice = voice_name
                            if voice_name.lower() in ['shakir', 'egyptian-male', 'egypt-male']:
                                edge_voice = "ar-EG-ShakirNeural"
                            elif voice_name.lower() in ['salma', 'egyptian-female', 'egypt-female']:
                                edge_voice = "ar-EG-SalmaNeural"
                                
                            logger.info(f"VOICE_REPLY calling Edge TTS with voice: {edge_voice}")
                            temp_dir = tempfile.gettempdir()
                            temp_file_path = os.path.join(temp_dir, f"voice_{convo_id}.mp3")
                            
                            try:
                                loop = asyncio.get_event_loop()
                            except RuntimeError:
                                loop = asyncio.new_event_loop()
                                asyncio.set_event_loop(loop)
                                
                            communicate = edge_tts.Communicate(clean_text[:1000], edge_voice)
                            loop.run_until_complete(communicate.save(temp_file_path))
                            
                            with open(temp_file_path, "rb") as f:
                                content = f.read()
                                
                            try:
                                os.remove(temp_file_path)
                            except Exception:
                                pass
                                
                            return content
                        except Exception as e:
                            logger.warning(f"Edge TTS failed: {e}. Falling back to standard TTS.")
                    
                    try:
                        if "chat/completions" in u_api_url:
                            tts_url = u_api_url.replace("chat/completions", "audio/speech")
                        else:
                            api_url_clean = u_api_url.rstrip("/")
                            if api_url_clean.endswith("/v1"):
                                tts_url = f"{api_url_clean}/audio/speech"
                            else:
                                tts_url = f"{api_url_clean}/v1/audio/speech"
                        
                        logger.info(f"VOICE_REPLY calling primary TTS: {tts_url}")
                        headers = {
                            "Authorization": f"Bearer {u_api_key}",
                            "Content-Type": "application/json"
                        }
                        body = {
                            "model": "tts-1",
                            "input": clean_text[:1000],
                            "voice": os.environ.get('TTS_VOICE', 'onyx')
                        }
                        
                        response = requests.post(tts_url, headers=headers, json=body, timeout=20)
                        logger.info(f"VOICE_REPLY primary TTS response status: {response.status_code}")
                        if response.status_code == 200:
                            return response.content
                        else:
                            logger.warning(f"Primary TTS failed with status {response.status_code}. Falling back to Google TTS.")
                    except Exception as e:
                        logger.warning(f"Primary TTS exception: {e}. Falling back to Google TTS.")
                    
                    try:
                        import urllib.parse
                        lang_code = get_current_lang(convo_id)
                        encoded_text = urllib.parse.quote(clean_text)
                        fallback_url = f"https://translate.google.com/translate_tts?ie=UTF-8&tl={lang_code}&client=tw-ob&q={encoded_text}"
                        
                        logger.info(f"VOICE_REPLY calling fallback Google TTS: {fallback_url}")
                        headers = {
                            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
                        }
                        response = requests.get(fallback_url, headers=headers, timeout=20)
                        if response.status_code == 200:
                            return response.content
                        else:
                            logger.error(f"Fallback Google TTS failed with status {response.status_code}: {response.text}")
                    except Exception as ex:
                        logger.error(f"Fallback Google TTS exception: {ex}")
                    
                    return None
                
                audio_content = await asyncio.to_thread(call_tts)
                if audio_content:
                    voice_file = io.BytesIO(audio_content)
                    voice_file.name = "voice.mp3"
                    await context.bot.send_voice(
                        chat_id=chatid,
                        voice=voice_file,
                        reply_to_message_id=messageid,
                        message_thread_id=message_thread_id
                    )
        except Exception as e:
            logger.error(f"Error in VOICE_REPLY generation or sending: {e}")

    if Users.get_config(convo_id, "FOLLOW_UP") and tmpresult.strip():
        if title != "":
            info = "\n\n".join(tmpresult.split("\n\n")[1:])
        else:
            info = tmpresult
        prompt = (
            f"You are a professional Q&A expert. You will now be given reference information. Based on the reference information, please help me ask three most relevant questions that you most want to know from my perspective. Be concise and to the point. Do not have numbers in front of questions. Separate each question with a line break. Only output three questions in {language}, no need for any explanation. reference infomation is provided inside <infomation></infomation> XML tags."
            "Here is the reference infomation, inside <infomation></infomation> XML tags:"
            "<infomation>"
            "{}"
            "</infomation>"
        ).format(info)
        result = (await config.SummaryBot.ask_async(prompt, convo_id=convo_id, model=model_name, pass_history=0, api_url=api_url, api_key=api_key)).split('\n')
        keyboard = []
        result = [i for i in result if i.strip() and len(i) > 5]
        print(result)
        for ques in result:
            keyboard.append([KeyboardButton(ques)])
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
        await update_message.reply_text(text=escape(tmpresult, italic=False), parse_mode='MarkdownV2', reply_to_message_id=messageid, reply_markup=reply_markup)
        await context.bot.delete_message(chat_id=chatid, message_id=answer_messageid)

@decorators.AdminAuthorization
@decorators.GroupAuthorization
@decorators.Authorization
async def button_press(update, context):
    """Function to handle the button press"""
    _, _, _, _, _, _, _, _, convo_id, _, _, _ = await GetMesageInfo(update, context)
    callback_query = update.callback_query
    info_message = update_info_message(convo_id)
    await callback_query.answer()
    data = callback_query.data
    banner = strings['message_banner'][get_current_lang(convo_id)]
    import telegram
    try:
        if data.endswith("_MODELS"):
            data = data[:-7]
            Users.set_config(convo_id, "engine", data)
            try:
                info_message = update_info_message(convo_id)
                message = await callback_query.edit_message_text(
                    text=escape(info_message + banner),
                    reply_markup=InlineKeyboardMarkup(update_models_buttons(convo_id)),
                    parse_mode='MarkdownV2'
                )
            except Exception as e:
                logger.info(e)
                pass
        elif data.endswith("_GROUP"):
            # Processing a click on a group of models
            group_name = data[:-6]
            try:
                message = await callback_query.edit_message_text(
                    text=escape(info_message + f"\n\n**{strings['group_title'][get_current_lang(convo_id)]}:** `{group_name}`"),
                    reply_markup=InlineKeyboardMarkup(update_models_buttons(convo_id, group=group_name)),
                    parse_mode='MarkdownV2'
                )
            except Exception as e:
                logger.info(e)
                pass
        elif data.startswith("PERSONA_"):
            persona_key = data[8:]
            if persona_key in PERSONAS:
                user_lang = Users.get_config(convo_id, "language")
                from datetime import datetime
                date_str = datetime.now().strftime("%Y-%m-%d")
                prompt_template = PERSONAS[persona_key]["prompt"]
                new_prompt = prompt_template.format(language=user_lang, date=date_str)
                
                Users.set_config(convo_id, "systemprompt", new_prompt)
                reset_ENGINE(convo_id, new_prompt)
                
                persona_name = PERSONAS[persona_key]["name"]
                await callback_query.answer(f"تم تغيير الشخصية بنجاح إلى: {persona_name}", show_alert=True)
                
                keyboard = []
                row = []
                for key, persona in PERSONAS.items():
                    is_active = (key == persona_key)
                    status_prefix = "✅ " if is_active else ""
                    row.append(InlineKeyboardButton(f"{status_prefix}{persona['name']}", callback_data=f"PERSONA_{key}"))
                    if len(row) == 2:
                        keyboard.append(row)
                        row = []
                if row:
                    keyboard.append(row)
                keyboard.append([InlineKeyboardButton("⬅️ رجوع / Back", callback_data="BACK")])
                
                text = (
                    "🎭 **شخصيات الذكاء الاصطناعي (AI Personas)**\n\n"
                    "اختر الشخصية أو الدور الذي ترغب في أن يلعبه البوت من القائمة أدناه لتغيير أسلوب ردوده ولغته:"
                )
                try:
                    await callback_query.edit_message_text(
                        text=escape(text),
                        reply_markup=InlineKeyboardMarkup(keyboard),
                        parse_mode='MarkdownV2'
                    )
                except Exception as e:
                    logger.info(e)
        elif data.startswith("MODELS"):
            message = await callback_query.edit_message_text(
                text=escape(info_message + banner),
                reply_markup=InlineKeyboardMarkup(update_models_buttons(convo_id)),
                parse_mode='MarkdownV2'
            )

        elif data.endswith("_LANGUAGES"):
            data = data[:-10]
            update_language_status(data, chat_id=convo_id)
            try:
                info_message = update_info_message(convo_id)
                message = await callback_query.edit_message_text(
                    text=escape(info_message, italic=False),
                    reply_markup=InlineKeyboardMarkup(update_menu_buttons(LANGUAGES, "_LANGUAGES", convo_id)),
                    parse_mode='MarkdownV2'
                )
            except Exception as e:
                logger.info(e)
                pass
        elif data.startswith("LANGUAGE"):
            message = await callback_query.edit_message_text(
                text=escape(info_message, italic=False),
                reply_markup=InlineKeyboardMarkup(update_menu_buttons(LANGUAGES, "_LANGUAGES", convo_id)),
                parse_mode='MarkdownV2'
            )

        if data.endswith("_PREFERENCES"):
            data = data[:-12]
            try:
                current_data = Users.get_config(convo_id, data)
                if data == "PASS_HISTORY":
                    if current_data == 0:
                        current_data = config.PASS_HISTORY or 9999
                    else:
                        current_data = 0
                    Users.set_config(convo_id, data, current_data)
                else:
                    Users.set_config(convo_id, data, not current_data)
            except Exception as e:
                logger.info(e)
            try:
                info_message = update_info_message(convo_id)
                message = await callback_query.edit_message_text(
                    text=escape(info_message, italic=False),
                    reply_markup=InlineKeyboardMarkup(update_menu_buttons(PREFERENCES, "_PREFERENCES", convo_id)),
                    parse_mode='MarkdownV2'
                )
            except Exception as e:
                logger.info(e)
                pass
        elif data.startswith("PREFERENCES"):
            message = await callback_query.edit_message_text(
                text=escape(info_message, italic=False),
                reply_markup=InlineKeyboardMarkup(update_menu_buttons(PREFERENCES, "_PREFERENCES", convo_id)),
                parse_mode='MarkdownV2'
            )

        if data.endswith("_PLUGINS"):
            data = data[:-8]
            try:
                current_data = Users.get_config(convo_id, data)
                Users.set_config(convo_id, data, not current_data)
            except Exception as e:
                logger.info(e)
            try:
                info_message = update_info_message(convo_id)
                message = await callback_query.edit_message_text(
                    text=escape(info_message, italic=False),
                    reply_markup=InlineKeyboardMarkup(update_menu_buttons(PLUGINS, "_PLUGINS", convo_id)),
                    parse_mode='MarkdownV2'
                )
            except Exception as e:
                logger.info(e)
                pass
        elif data.startswith("PLUGINS"):
            message = await callback_query.edit_message_text(
                text=escape(info_message, italic=False),
                reply_markup=InlineKeyboardMarkup(update_menu_buttons(PLUGINS, "_PLUGINS", convo_id)),
                parse_mode='MarkdownV2'
            )
        elif data == "LINK_WHATSAPP":
            chatid = update.effective_chat.id
            text = (
                "💬 **إعدادات ربط البوت بـ WhatsApp**\n\n"
                "اختر طريقة الربط المناسبة من الخيارات أدناه:"
            )
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("الربط بالرقم (+20) 📱", callback_data="WA_LINK_PHONE")],
                [InlineKeyboardButton("الربط برمز QR 🔗", callback_data="WA_LINK_QR")],
                [InlineKeyboardButton("⬅️ رجوع / Back", callback_data="PLUGINS")]
            ])
            
            is_photo = bool(callback_query.message.photo)
            if is_photo:
                try:
                    await callback_query.delete_message()
                except Exception:
                    pass
                await context.bot.send_message(
                    chat_id=chatid,
                    text=escape(text),
                    parse_mode='MarkdownV2',
                    reply_markup=keyboard
                )
            else:
                await callback_query.edit_message_text(
                    text=escape(text),
                    reply_markup=keyboard,
                    parse_mode='MarkdownV2'
                )
        elif data == "WA_LINK_PHONE":
            text = (
                "📱 **الربط عبر رقم الهاتف (+20)**\n\n"
                "الرجاء كتابة وإرسال رقم هاتفك المصري مبدوءاً بـ `+20` لتوليد كود الربط الخاص بك.\n\n"
                "مثال:\n"
                "`+201234567890`"
            )
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ رجوع / Back", callback_data="LINK_WHATSAPP")]
            ])
            await callback_query.edit_message_text(
                text=escape(text),
                reply_markup=keyboard,
                parse_mode='MarkdownV2'
            )
        elif data == "WA_LINK_QR":
            chatid = update.effective_chat.id
            qr_url = "https://api.qrserver.com/v1/create-qr-code/?size=300x300&data=https://wa.me/settings"
            caption = (
                f"🔗 **الربط عبر رمز QR (WhatsApp Web)**\n\n"
                f"👉 **طريقة التفعيل:**\n"
                f"1. افتح تطبيق واتساب على هاتفك.\n"
                f"2. اذهب إلى **الأجهزة المرتبطة** (Linked Devices).\n"
                f"3. اضغط على **ربط جهاز** (Link a Device).\n"
                f"4. قم بتوجيه كاميرا الهاتف لمسح رمز QR المرفق."
            )
            await context.bot.send_photo(
                chat_id=chatid,
                photo=qr_url,
                caption=escape(caption),
                parse_mode='MarkdownV2',
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ رجوع / Back", callback_data="LINK_WHATSAPP")]
                ])
            )
            try:
                await callback_query.delete_message()
            except Exception:
                pass
        elif data == "USE_OWN_KEY":
            text = (
                "🔑 **استخدام مفتاح خاص بك / Use Your Own Key**\n\n"
                "يمكنك استخدام مفتاح API ورابط مخصصين لتشغيل البوت لحسابك فقط.\n\n"
                "👉 **طريقة التعيين:**\n"
                "أرسل رسالة جديدة بالصيغة التالية (اضغط لنسخ المثال وتعديله):\n"
                "• **لتعيين مفتاح ورابط مخصص:**\n"
                "`/start <api_url> <api_key>`\n"
                "مثال:\n"
                "`/start https://api.vectorengine.ai sk-xxxx...`\n\n"
                "• **لتعيين مفتاح OpenAI فقط (الرابط الافتراضي):**\n"
                "`/start <api_key>`\n"
                "مثال:\n"
                "`/start sk-xxxx...`\n\n"
                "🔄 **إعادة تعيين الافتراضي:**\n"
                "إذا أردت إلغاء المفتاح الخاص والرجوع لمفتاح البوت الافتراضي، اضغط على الزر أدناه."
            )
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("إعادة تعيين الافتراضي / Reset Default 🔄", callback_data="RESET_DEFAULT_KEY")],
                [InlineKeyboardButton("⬅️ رجوع / Back", callback_data="PLUGINS")]
            ])
            await callback_query.edit_message_text(
                text=escape(text, italic=False),
                reply_markup=keyboard,
                parse_mode='MarkdownV2'
            )
        elif data == "RESET_DEFAULT_KEY":
            Users.set_config(convo_id, "api_key", config.API_KEY)
            Users.set_config(convo_id, "api_url", config.BASE_URL)
            config.InitEngine(convo_id)
            await callback_query.answer("تمت إعادة تعيين المفتاح الافتراضي بنجاح! / Reset to default key successfully!", show_alert=True)
            info_message = update_info_message(convo_id)
            await callback_query.edit_message_text(
                text=escape(info_message, italic=False),
                reply_markup=InlineKeyboardMarkup(update_menu_buttons(PLUGINS, "_PLUGINS", convo_id)),
                parse_mode='MarkdownV2'
            )

        elif data.startswith("BACK"):
            message = await callback_query.edit_message_text(
                text=escape(info_message, italic=False),
                reply_markup=InlineKeyboardMarkup(update_first_buttons_message(convo_id)),
                parse_mode='MarkdownV2'
            )
    except telegram.error.BadRequest as e:
        print('\033[31m')
        traceback.print_exc()
        if "Message to edit not found" in str(e):
            print("error: telegram.error.BadRequest: Message to edit not found!")
        else:
            print(f"error: {str(e)}")
        print('\033[0m')

@decorators.GroupAuthorization
@decorators.Authorization
@decorators.APICheck
async def handle_file(update, context):
    _, _, image_url, chatid, _, _, _, message_thread_id, convo_id, file_url, _, voice_text = await GetMesageInfo(update, context)
    robot, role, api_key, api_url = get_robot(convo_id)
    engine = Users.get_config(convo_id, "engine")

    if file_url == None and image_url:
        file_url = image_url
        if Users.get_config(convo_id, "IMAGEQA") == False:
            return
    if image_url == None and file_url:
        image_url = file_url

    if is_deepseek_model(engine) and image_url:
        lang = get_current_lang(convo_id)
        thinking_text = "🎨 جاري تحليل الصورة باستخدام نموذج الرؤية الهجين من Groq..."
        if lang == "en":
            thinking_text = "🎨 Analyzing image using hybrid vision model..."
        
        status_msg = await context.bot.send_message(
            chat_id=chatid,
            message_thread_id=message_thread_id,
            text=escape(thinking_text),
            parse_mode='MarkdownV2'
        )
        
        description = await describe_image_via_groq(image_url)
        
        try:
            await context.bot.delete_message(chat_id=chatid, message_id=status_msg.message_id)
        except Exception:
            pass
            
        formatted_message = f"[صورة مرفوعة من المستخدم]\nوصف الصورة: {description}"
        robot.add_to_conversation(formatted_message, role, convo_id)
        
        if Users.get_config(convo_id, "FILE_UPLOAD_MESS"):
            success_text = "✅ تم تحليل الصورة وإضافتها إلى سياق المحادثة. يمكنك الآن مناقشتها مع DeepSeek!"
            if lang == "en":
                success_text = "✅ Image analyzed and added to context. You can now discuss it with DeepSeek!"
            msg = await context.bot.send_message(
                chat_id=chatid,
                message_thread_id=message_thread_id,
                text=escape(success_text),
                parse_mode='MarkdownV2'
            )
            await delete_message(update, context, [msg.message_id])
        return

    engine_type, _ = get_engine({"base_url": api_url}, endpoint=None, original_model=engine)
    if robot.__class__.__name__ == "chatgpt":
        engine_type = "gpt"
    message = await Document_extract(file_url, image_url, engine_type)

    robot.add_to_conversation(message, role, convo_id)

    if Users.get_config(convo_id, "FILE_UPLOAD_MESS"):
        message = await context.bot.send_message(chat_id=chatid, message_thread_id=message_thread_id, text=escape(strings['message_doc'][get_current_lang(convo_id)]), parse_mode='MarkdownV2', disable_web_page_preview=True)
        await delete_message(update, context, [message.message_id])

@decorators.GroupAuthorization
@decorators.Authorization
@decorators.APICheck
async def inlinequery(update: Update, context) -> None:
    """Handle the inline query."""

    chatid = update.effective_user.id
    engine = Users.get_config(chatid, "engine")
    query = update.inline_query.query
    if (query.endswith('.') or query.endswith('。')) and query.strip():
        prompt = "Answer the following questions as concisely as possible:\n\n"
        _, _, _, chatid, _, _, _, _, convo_id, _, _, _ = await GetMesageInfo(update, context)
        robot, role, api_key, api_url = get_robot(convo_id)
        result = config.ChatGPTbot.ask(prompt + query, convo_id=convo_id, model=engine, api_url=api_url, api_key=api_key, pass_history=0)

        results = [
            InlineQueryResultArticle(
                id=chatid,
                title=f"{engine}",
                thumbnail_url="https://pb.yym68686.top/TTGk",
                description=f"{result}",
                input_message_content=InputTextMessageContent(escape(result, italic=False), parse_mode='MarkdownV2')),
        ]

        await update.inline_query.answer(results)

@decorators.GroupAuthorization
@decorators.Authorization
async def change_model(update, context):
    """Quick model change using the command"""
    _, _, _, chatid, user_message_id, _, _, message_thread_id, convo_id, _, _, _ = await GetMesageInfo(update, context)
    lang = get_current_lang(convo_id)

    if not context.args:
        message = await context.bot.send_message(
            chat_id=chatid,
            message_thread_id=message_thread_id,
            text=escape(strings['model_command_usage'][lang]),
            parse_mode='MarkdownV2',
            reply_to_message_id=user_message_id,
        )
        return

    # Combine all arguments into one model name
    model_name = ' '.join(context.args)

    # Check if the model name is valid (allowing all common model name characters)
    if not re.match(r'^[a-zA-Z0-9\-_\./:\\@+\s]+$', model_name) or len(model_name) > 100:
        message = await context.bot.send_message(
            chat_id=chatid,
            message_thread_id=message_thread_id,
            text=escape(strings['model_name_invalid'][lang]),
            parse_mode='MarkdownV2',
            reply_to_message_id=user_message_id,
        )
        return

    # Get all available models from initial_model and MODEL_GROUPS
    available_models = get_all_available_models()
    for group_name, models in get_model_groups().items():
        available_models.extend(models)

    # Add debug output
    print(f"Requested model: '{model_name}'")
    print(f"Available models: {available_models}")

    # Check if the requested model is in the available models list
    if model_name not in available_models:
        message = await context.bot.send_message(
            chat_id=chatid,
            message_thread_id=message_thread_id,
            text=escape(strings['model_not_available'][lang].format(model_name=model_name)),
            parse_mode='MarkdownV2',
            reply_to_message_id=user_message_id,
        )
        return

    # Saving the new model in the user's configuration
    Users.set_config(convo_id, "engine", model_name)

    # Sending a message about changing the model
    message = await context.bot.send_message(
        chat_id=chatid,
        message_thread_id=message_thread_id,
        text=escape(strings['model_changed'][lang].format(model_name=model_name), italic=False),
        parse_mode='MarkdownV2',
        reply_to_message_id=user_message_id,
    )

@decorators.GroupAuthorization
@decorators.Authorization
async def persona_command(update, context):
    """Command to change the AI persona"""
    _, _, _, chatid, user_message_id, _, _, message_thread_id, convo_id, _, _, _ = await GetMesageInfo(update, context)
    
    current_prompt = Users.get_config(convo_id, "systemprompt")
    
    keyboard = []
    row = []
    for key, persona in PERSONAS.items():
        is_active = False
        if key == "general" and "Respond conversationally" in current_prompt and "expert" not in current_prompt and "Tutor" not in current_prompt and "storyteller" not in current_prompt:
            is_active = True
        elif key == "coder" and "expert software engineer" in current_prompt:
            is_active = True
        elif key == "english" and "English Tutor" in current_prompt:
            is_active = True
        elif key == "writer" and "creative writer" in current_prompt:
            is_active = True
            
        status_prefix = "✅ " if is_active else ""
        row.append(InlineKeyboardButton(f"{status_prefix}{persona['name']}", callback_data=f"PERSONA_{key}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
        
    keyboard.append([InlineKeyboardButton("⬅️ رجوع / Back", callback_data="BACK")])
    
    text = (
        "🎭 **شخصيات الذكاء الاصطناعي (AI Personas)**\n\n"
        "اختر الشخصية أو الدور الذي ترغب في أن يلعبه البوت من القائمة أدناه لتغيير أسلوب ردوده ولغته:"
    )
    
    await context.bot.send_message(
        chat_id=chatid,
        message_thread_id=message_thread_id,
        text=escape(text),
        parse_mode='MarkdownV2',
        reply_markup=InlineKeyboardMarkup(keyboard),
        reply_to_message_id=user_message_id
    )

@decorators.GroupAuthorization
@decorators.Authorization
async def image_command(update, context):
    """Command to generate an image directly"""
    _, _, _, chatid, user_message_id, _, _, message_thread_id, convo_id, _, _, _ = await GetMesageInfo(update, context)
    lang = get_current_lang(convo_id)
    if not context.args:
        await context.bot.send_message(
            chat_id=chatid,
            message_thread_id=message_thread_id,
            text=escape("الرجاء كتابة وصف الصورة بعد الأمر، مثال:\n`/image قطة لطيفة تجري في الحديقة`"),
            parse_mode='MarkdownV2',
            reply_to_message_id=user_message_id,
        )
        return

    prompt = ' '.join(context.args)

    # Send a thinking message
    thinking_message = await context.bot.send_message(
        chat_id=chatid,
        message_thread_id=message_thread_id,
        text=escape(strings['message_think'][lang]),
        parse_mode='MarkdownV2',
        reply_to_message_id=user_message_id,
    )

    try:
        from aient.aient.plugins.image import generate_image
        import asyncio
        loop = asyncio.get_running_loop()
        image_url = await loop.run_in_executor(None, generate_image, prompt)

        if image_url and image_url.startswith(('http://', 'https://', 'data:')):
            import io
            import requests
            import asyncio
            
            response = None
            for attempt in range(3):
                try:
                    response = requests.get(image_url, timeout=60)
                    if response.status_code == 200:
                        break
                    elif response.status_code in (402, 429):
                        await asyncio.sleep(2)
                except Exception as e:
                    logger.warning(f"Download attempt {attempt + 1} failed: {e}")
                    await asyncio.sleep(2)
                    
            try:
                if response and response.status_code == 200:
                    photo_file = io.BytesIO(response.content)
                    photo_file.name = "image.png"
                    await context.bot.send_photo(chat_id=chatid, photo=photo_file, reply_to_message_id=user_message_id)
                else:
                    status = response.status_code if response else 'No Response'
                    raise Exception(f"Failed to download after retries (HTTP status {status})")
            except Exception as e:
                logger.warning(f"Failed to download image: {e}, falling back to sending URL")
                await context.bot.send_photo(chat_id=chatid, photo=image_url, reply_to_message_id=user_message_id)
        else:
            await context.bot.send_message(
                chat_id=chatid,
                message_thread_id=message_thread_id,
                text=escape("فشل في توليد الصورة."),
                parse_mode='MarkdownV2',
                reply_to_message_id=user_message_id,
            )
    except Exception as e:
        logger.error(f"Image command failed: {e}")
        await context.bot.send_message(
            chat_id=chatid,
            message_thread_id=message_thread_id,
            text=escape(f"حدث خطأ أثناء توليد الصورة: {e}"),
            parse_mode='MarkdownV2',
            reply_to_message_id=user_message_id,
        )
    finally:
        try:
            await context.bot.delete_message(chat_id=chatid, message_id=thinking_message.message_id)
        except Exception:
            pass

async def scheduled_function(context: ContextTypes.DEFAULT_TYPE) -> None:
    """这个函数将在RESET_TIME秒后执行一次，重置特定用户的对话"""
    job = context.job
    chat_id = job.chat_id

    if config.ADMIN_LIST and chat_id in config.ADMIN_LIST:
        return

    reset_ENGINE(chat_id)

    # 任务执行完毕后自动移除
    remove_job_if_exists(str(chat_id), context)

def remove_job_if_exists(name: str, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """如果存在，则移除指定名称的任务"""
    current_jobs = context.job_queue.get_jobs_by_name(name)
    if not current_jobs:
        return False
    for job in current_jobs:
        job.schedule_removal()
    return True

# 定义一个全局变量来存储 chatid
target_convo_id = None
reset_mess_id = 9999

@decorators.GroupAuthorization
@decorators.Authorization
async def reset_chat(update, context):
    global target_convo_id, reset_mess_id
    _, _, _, chatid, user_message_id, _, _, message_thread_id, convo_id, _, _, _ = await GetMesageInfo(update, context)
    reset_mess_id = user_message_id
    target_convo_id = convo_id
    stop_event.set()
    message = None
    if (len(context.args) > 0):
        message = ' '.join(context.args)
    reset_ENGINE(target_convo_id, message)

    remove_keyboard = ReplyKeyboardRemove()
    message = await context.bot.send_message(
        chat_id=chatid,
        message_thread_id=message_thread_id,
        text=escape(strings['message_reset'][get_current_lang(convo_id)]),
        reply_markup=remove_keyboard,
        parse_mode='MarkdownV2',
    )
    if GET_MODELS:
        robot, role, api_key, api_url = get_robot()
        engine = Users.get_config(convo_id, "engine")
        provider = {
            "provider": "openai",
            "base_url": api_url,
            "api": api_key,
            "model": [engine],
            "tools": True,
            "image": True
        }
        config.initial_model = remove_no_text_model(await update_initial_model(provider))
    await delete_message(update, context, [message.message_id, user_message_id])

@decorators.AdminAuthorization
@decorators.GroupAuthorization
@decorators.Authorization
async def info(update, context):
    _, _, _, chatid, user_message_id, _, _, message_thread_id, convo_id, _, _, voice_text = await GetMesageInfo(update, context)
    info_message = update_info_message(convo_id)
    message = await context.bot.send_message(
        chat_id=chatid,
        message_thread_id=message_thread_id,
        text=escape(info_message, italic=False),
        reply_markup=InlineKeyboardMarkup(update_first_buttons_message(convo_id)),
        parse_mode='MarkdownV2',
        disable_web_page_preview=True,
        read_timeout=600,
    )
    await delete_message(update, context, [message.message_id, user_message_id])

@decorators.PrintMessage
@decorators.GroupAuthorization
@decorators.Authorization
async def start(update, context): # 当用户输入/start时，返回文本
    _, _, _, _, _, _, _, _, convo_id, _, _, _ = await GetMesageInfo(update, context)
    user = update.effective_user
    if user.language_code == "zh-hans":
        update_language_status("Simplified Chinese", chat_id=convo_id)
    elif user.language_code == "zh-hant":
        update_language_status("Traditional Chinese", chat_id=convo_id)
    elif user.language_code == "ru":
        update_language_status("Russian", chat_id=convo_id)
    else:
        update_language_status("English", chat_id=convo_id)
    message = (
        f"Hi `{user.username}` ! I am an Assistant, a large language model trained by OpenAI. I will do my best to help answer your questions.\n\n"
    )
    if len(context.args) == 2 and context.args[1].startswith("sk-"):
        api_url = context.args[0]
        api_key = context.args[1]
        Users.set_config(convo_id, "api_key", api_key)
        Users.set_config(convo_id, "api_url", api_url)
        # if GET_MODELS:
        #     update_initial_model()

    if len(context.args) == 1 and context.args[0].startswith("sk-"):
        api_key = context.args[0]
        Users.set_config(convo_id, "api_key", api_key)
        Users.set_config(convo_id, "api_url", "https://api.openai.com/v1/chat/completions")
        # if GET_MODELS:
        #     update_initial_model()

    # message = (
    #     ">Block quotation started\n"
    #     ">Block quotation continued\n"
    #     ">Block quotation continued\n"
    #     ">Block quotation continued\n"
    #     ">The last line of the block quotation\n"
    #     "**>The expandable block quotation started right after the previous block quotation\n"
    #     ">It is separated from the previous block quotation by an empty bold entity\n"
    #     ">Expandable block quotation continued\n"
    #     ">Hidden by default part of the expandable block quotation started\n"
    #     ">Expandable block quotation continued\n"
    #     ">The last line of the expandable block quotation with the expandability mark||\n"
    # )
    # await update.message.reply_text(message, parse_mode='MarkdownV2', disable_web_page_preview=True)
    await update.message.reply_text(escape(message, italic=False), parse_mode='MarkdownV2', disable_web_page_preview=True)

async def error(update, context):
    traceback_string = traceback.format_exception(None, context.error, context.error.__traceback__)
    if "telegram.error.TimedOut: Timed out" in traceback_string:
        logger.warning('error: telegram.error.TimedOut: Timed out')
        return
    if "Message to be replied not found" in traceback_string:
        logger.warning('error: telegram.error.BadRequest: Message to be replied not found')
        return
    logger.warning('Update "%s" caused error "%s"', update, context.error)
    logger.warning('Error traceback: %s', ''.join(traceback_string))

@decorators.GroupAuthorization
@decorators.Authorization
async def unknown(update, context): # 当用户输入未知命令时，返回文本
    return
    # await context.bot.send_message(chat_id=update.effective_chat.id, text="Sorry, I didn't understand that command.")

async def post_init(application: Application) -> None:
    if GET_MODELS:
        await get_initial_model()
    await application.bot.set_my_commands([
        BotCommand('info', 'Basic information'),
        BotCommand('reset', 'Reset the bot'),
        BotCommand('start', 'Start the bot'),
        BotCommand('model', 'Change AI model'),
        BotCommand('image', 'Generate an image'),
        BotCommand('draw', 'Generate an image (alias for /image)'),
        BotCommand('persona', 'Change AI Persona'),
    ])
    description = (
        "I am an Assistant, a large language model trained by OpenAI. I will do my best to help answer your questions."
    )
    await application.bot.set_my_description(description)

if __name__ == '__main__':
    application = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .concurrent_updates(True)
        .connection_pool_size(65536)
        .get_updates_connection_pool_size(65536)
        .read_timeout(time_out)
        .write_timeout(time_out)
        .connect_timeout(time_out)
        .pool_timeout(time_out)
        .get_updates_read_timeout(time_out)
        .get_updates_write_timeout(time_out)
        .get_updates_connect_timeout(time_out)
        .get_updates_pool_timeout(time_out)
        .rate_limiter(AIORateLimiter(max_retries=5))
        .post_init(post_init)
        .build()
    )

    application.add_handler(CommandHandler("info", info))
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("reset", reset_chat))
    application.add_handler(CommandHandler("model", change_model))
    application.add_handler(CommandHandler("image", image_command))
    application.add_handler(CommandHandler("draw", image_command))
    application.add_handler(CommandHandler("persona", persona_command))
    application.add_handler(InlineQueryHandler(inlinequery))
    application.add_handler(CallbackQueryHandler(button_press))
    application.add_handler(MessageHandler((filters.TEXT | filters.VOICE) & ~filters.COMMAND, lambda update, context: command_bot(update, context, has_command=False), block = False))
    application.add_handler(MessageHandler(
        filters.CAPTION &
        (
            (filters.PHOTO & ~filters.COMMAND) |
            (
                filters.Document.PDF |
                filters.Document.TXT |
                filters.Document.DOC |
                filters.Document.FileExtension("jpg") |
                filters.Document.FileExtension("jpeg") |
                filters.Document.FileExtension("png") |
                filters.Document.FileExtension("md") |
                filters.Document.FileExtension("py") |
                filters.Document.FileExtension("yml")
            )
        ), lambda update, context: command_bot(update, context, has_command=False)))
    application.add_handler(MessageHandler(
        ~filters.CAPTION &
        (
            (filters.PHOTO & ~filters.COMMAND) |
            (
                filters.Document.PDF |
                filters.Document.TXT |
                filters.Document.DOC |
                filters.Document.FileExtension("jpg") |
                filters.Document.FileExtension("jpeg") |
                filters.Document.FileExtension("png") |
                filters.Document.FileExtension("md") |
                filters.Document.FileExtension("py") |
                filters.Document.FileExtension("yml") |
                filters.AUDIO |
                filters.Document.FileExtension("wav")
            )
        ), handle_file))
    application.add_handler(MessageHandler(filters.COMMAND, unknown))
    application.add_error_handler(error)

    if WEB_HOOK:
        print("WEB_HOOK:", WEB_HOOK)
        application.run_webhook("0.0.0.0", PORT, webhook_url=WEB_HOOK)
    else:
        application.run_polling(timeout=time_out)

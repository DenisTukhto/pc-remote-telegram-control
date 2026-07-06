import os
import re
import subprocess
import telebot
import pyautogui
import psutil
from telebot import types

# Импортируем конфиг с личными данными. 
# На GitHub этот файл выкладывать НЕЛЬЗЯ!
try:
    import config
    TOKEN = config.TOKEN
    ALLOWED_CHAT_ID = config.ALLOWED_CHAT_ID
    START_DIR = config.START_DIR
except ImportError:
    print("Ошибка: Создайте файл config.py рядом со скриптом!")
    exit(1)

bot = telebot.TeleBot(TOKEN)

# ==========================================
# БЛОК 1: ХРАНИЛИЩЕ ПУТЕЙ (ФАЙЛОВЫЙ МЕНЕДЖЕР)
# ==========================================
# Используется для обхода жесткого лимита Telegram (64 байта) на callback_data в кнопках.
# Вместо полных путей в кнопки зашиваются короткие ключи (dir_1, file_5), 
# а скрипт сопоставляет их с реальными путями в этом словаре.
path_storage = {}
path_counter = 0

def register_path(path_type, full_path):
    """Регистрирует полный путь в хранилище и возвращает короткий уникальный ключ"""
    global path_counter
    path_counter += 1
    key = f"{path_type}_{path_counter}"
    path_storage[key] = full_path
    return key

def get_available_drives():
    """Сканирует систему и возвращает список всех активных дисков (C:\\, D:\\ и т.д.)"""
    drives = []
    for part in psutil.disk_partitions(all=False):
        if os.name == 'nt' and 'cdrom' in part.opts:
            continue
        if part.device:
            drives.append(part.device)
    return drives

# ==========================================
# БЛОК 2: КЛАВИАТУРЫ И ИНТЕРФЕЙС БОТА
# ==========================================
def check_user(message):
    """Проверка, что боту пишет именно владелец ПК"""
    return message.chat.id == ALLOWED_CHAT_ID

def get_main_keyboard():
    """Главное меню с кнопками управления (Reply-клавиатура)"""
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    btn_screen = types.KeyboardButton("📸 Сделать скриншот")
    btn_status = types.KeyboardButton("📊 Статус системы")
    btn_files = types.KeyboardButton("📂 Проводник файлов")
    btn_timer = types.KeyboardButton("⏱ Таймер выключения")
    btn_lock = types.KeyboardButton("🔒 Заблокировать ПК")
    btn_shutdown = types.KeyboardButton("🛑 Выключить ПК")
    
    markup.add(btn_screen, btn_status)
    markup.add(btn_files, btn_timer)
    markup.add(btn_lock, btn_shutdown)
    return markup

def get_timer_keyboard():
    """Меню выбора времени для таймера автовыключения"""
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    btn_30m = types.KeyboardButton("⏳ 30 минут")
    btn_1h = types.KeyboardButton("⏳ 1 час")
    btn_2h = types.KeyboardButton("⏳ 2 часа")
    btn_cancel_timer = types.KeyboardButton("❌ Сбросить таймер")
    btn_back = types.KeyboardButton("⬅️ Назад в меню")
    markup.add(btn_30m, btn_1h, btn_2h)
    markup.add(btn_cancel_timer)
    markup.add(btn_back)
    return markup

def build_file_explorer(path):
    """Динамически строит список инлайн-кнопок для файлов и папок"""
    markup = types.InlineKeyboardMarkup(row_width=1)
    
    # Если зашли в корень «Компьютер» — выводим список дисков
    if path == "ROOT":
        drives = get_available_drives()
        for drive in drives:
            key = register_path("dir", drive)
            markup.add(types.InlineKeyboardButton(f"💽 Диск {drive}", callback_data=key))
        return markup

    # Попытка прочитать содержимое текущей директории
    try:
        items = os.listdir(path)
    except Exception:
        markup.add(types.InlineKeyboardButton("🚫 Нет доступа к папке", callback_data="ignore"))
        parent_dir = os.path.dirname(path)
        back_key = register_path("dir", "ROOT" if parent_dir == path else parent_dir)
        markup.add(types.InlineKeyboardButton("⬅️ Назад", callback_data=back_key))
        return markup

    # Кнопка возврата (На уровень вверх или к списку дисков)
    parent_dir = os.path.dirname(path)
    if parent_dir == path:
        back_key = register_path("dir", "ROOT")
        markup.add(types.InlineKeyboardButton("💽 .. (К списку дисков)", callback_data=back_key))
    else:
        back_key = register_path("dir", parent_dir)
        markup.add(types.InlineKeyboardButton("📁 .. (Вверх)", callback_data=back_key))

    folders, files = [], []

    try:
        for item in items:
            # Игнорируем скрытые и системные файлы
            if item.startswith('.') or item.startswith('$') or item.lower() in ['ntuser.dat', 'desktop.ini']:
                continue
            full_path = os.path.join(path, item)
            if os.path.isdir(full_path):
                folders.append((item, full_path))
            elif os.path.isfile(full_path):
                # Ограничение на отправку файлов через бот — до 50 МБ
                if os.path.getsize(full_path) < 50 * 1024 * 1024:
                    files.append((item, full_path))
    except Exception:
        pass

    # Сортируем и выводим элементы (лимит 15 в ряд, чтобы не перегружать чат)
    folders.sort(key=lambda x: x[0].lower())
    for name, full_p in folders[:15]:
        key = register_path("dir", full_p)
        markup.add(types.InlineKeyboardButton(f"📁 {name}", callback_data=key))
        
    files.sort(key=lambda x: x[0].lower())
    for name, full_p in files[:15]:
        key = register_path("file", full_p)
        markup.add(types.InlineKeyboardButton(f"📄 {name}", callback_data=key))

    return markup

# ==========================================
# БЛОК 3: ПАРСЕРЫ И СБОР ДАННЫХ ЖЕЛЕЗА
# ==========================================
def parse_time_to_seconds(text):
    """Разбирает текстовый ввод времени (например '1:20', '45', '2 часа') в секунды"""
    text = text.lower().strip()
    match_colon = re.match(r'^(\d+):(\d+)$', text)
    if match_colon: return (int(match_colon.group(1)) * 3600) + (int(match_colon.group(2)) * 60)
    if text.isdigit(): return int(text) * 60
    hours, minutes = 0, 0
    match_hours = re.search(r'(\d+)\s*(?:ч|h|час)', text)
    match_minutes = re.search(r'(\d+)\s*(?:м|m|мин)', text)
    if match_hours: hours = int(match_hours.group(1))
    if match_minutes: minutes = int(match_minutes.group(1))
    if hours > 0 or minutes > 0: return (hours * 3600) + (minutes * 60)
    return None

def get_pc_status():
    """Собирает телеметрию: загрузку CPU, RAM, диска C и видеокарты NVIDIA"""
    cpu_usage = psutil.cpu_percent(interval=0.5)
    ram = psutil.virtual_memory()
    ram_used = round(ram.used / (1024 ** 3), 1)
    ram_total = round(ram.total / (1024 ** 3), 1)
    disk = psutil.disk_usage('C:\\')
    disk_free = round(disk.free / (1024 ** 3), 1)

    # Пошаговый безопасный опрос nvidia-smi
    gpu_util, gpu_temp, gpu_power = "Н/Д", "Н/Д", "Н/Д"
    try: gpu_util = subprocess.check_output("nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,nounits", shell=True).decode('utf-8').strip() + "%"
    except Exception: pass
    try: gpu_temp = "🔥 " + subprocess.check_output("nvidia-smi --query-gpu=temperature.gpu --format=csv,noheader,nounits", shell=True).decode('utf-8').strip() + "°C"
    except Exception: pass
    try: gpu_power = "⚡ " + subprocess.check_output("nvidia-smi --query-gpu=power.draw --format=csv,noheader,nounits", shell=True).decode('utf-8').strip() + " Вт"
    except Exception: pass

    return (
        "💻 **СТАТУС СИСТЕМЫ:**\n\n"
        f"ℹ️ **Процессор:** Загрузка: {cpu_usage}%\n"
        f"🧠 **ОЗУ:** Занято: {ram_used} ГБ из {ram_total} ГБ ({ram.percent}%)\n"
        f"🎮 **Видеокарта:** {gpu_util} | {gpu_temp} | {gpu_power}\n"
        f"💾 **Диск C:** Свободно: {disk_free} ГБ"
    )

# ==========================================
# БЛОК 4: ОБРАБОТЧИКИ СОБЫТИЙ И КОМАНД
# ==========================================
@bot.message_handler(commands=['start', 'help'])
def send_welcome_command(message):
    """Ответ на текстовые команды /start и /help"""
    if check_user(message):
        bot.send_message(ALLOWED_CHAT_ID, "Главное меню:", reply_markup=get_main_keyboard())

@bot.callback_query_handler(func=lambda call: True)
def handle_query(call):
    """Логика взаимодействия с инлайн-кнопками проводника (клики по папкам/файлам)"""
    if call.message.chat.id != ALLOWED_CHAT_ID: return

    key = call.data
    if key not in path_storage:
        bot.answer_callback_query(call.id, "❌ Сессия устарела. Открой Проводник заново.", show_alert=True)
        return

    full_path = path_storage[key]

    if key.startswith("dir_"):
        title = "💽 Выберите диск:" if full_path == "ROOT" else f"📁 Папка: `{full_path}`"
        bot.edit_message_text(title, ALLOWED_CHAT_ID, call.message.message_id, parse_mode="Markdown", reply_markup=build_file_explorer(full_path))
        
    elif key.startswith("file_"):
        bot.send_message(ALLOWED_CHAT_ID, f"⏳ Отправляю файл: `{os.path.basename(full_path)}`...", parse_mode="Markdown")
        try:
            with open(full_path, 'rb') as f:
                bot.send_document(ALLOWED_CHAT_ID, f)
        except Exception as e:
            bot.send_message(ALLOWED_CHAT_ID, f"❌ Ошибка отправки файла: {e}")

@bot.message_handler(func=lambda message: True)
def handle_text_commands(message):
    """Основной диспетчер текстовых кнопок и команд управления ОС"""
    if not check_user(message): return

    if message.text in ["/start", "/help", "⬅️ Назад в меню"]:
        bot.send_message(ALLOWED_CHAT_ID, "Главное меню:", reply_markup=get_main_keyboard())

    elif message.text == "📸 Сделать скриншот":
        bot.send_message(ALLOWED_CHAT_ID, "Фиксирую экран...")
        try:
            screen = pyautogui.screenshot()
            path = os.path.join(START_DIR, "pc_screen.png")
            screen.save(path)
            with open(path, 'rb') as photo: bot.send_photo(ALLOWED_CHAT_ID, photo)
            os.remove(path)
        except Exception as e: bot.send_message(ALLOWED_CHAT_ID, f"Ошибка: {e}")

    elif message.text == "📊 Статус системы":
        bot.send_message(ALLOWED_CHAT_ID, "Собираю данные...")
        bot.send_message(ALLOWED_CHAT_ID, get_pc_status(), parse_mode="Markdown")

    elif message.text == "📂 Проводник файлов":
        bot.send_message(ALLOWED_CHAT_ID, f"📁 Папка: `{START_DIR}`", parse_mode="Markdown", reply_markup=build_file_explorer(START_DIR))

    elif message.text == "🔒 Заблокировать ПК":
        bot.send_message(ALLOWED_CHAT_ID, "Блокирую систему...")
        os.system("rundll32.exe user32.dll,LockWorkStation")

    elif message.text == "🛑 Выключить ПК":
        bot.send_message(ALLOWED_CHAT_ID, "Выключение через 10 секунд!")
        os.system("shutdown /s /t 10")

    elif message.text == "⏱ Таймер выключения":
        bot.send_message(ALLOWED_CHAT_ID, "Выбери готовое время или отправь текстом (например `3:35`):", reply_markup=get_timer_keyboard())

    elif message.text == "⏳ 30 минут":
        os.system("shutdown /a"); os.system("shutdown /s /t 1800")
        bot.send_message(ALLOWED_CHAT_ID, "Выключение через 30 минут.", reply_markup=get_main_keyboard())

    elif message.text == "⏳ 1 час":
        os.system("shutdown /a"); os.system("shutdown /s /t 3600")
        bot.send_message(ALLOWED_CHAT_ID, "Выключение через 1 час.", reply_markup=get_main_keyboard())

    elif message.text == "⏳ 2 часа":
        os.system("shutdown /a"); os.system("shutdown /s /t 7200")
        bot.send_message(ALLOWED_CHAT_ID, "Выключение через 2 часа.", reply_markup=get_main_keyboard())

    elif message.text == "❌ Сбросить таймер":
        os.system("shutdown /a")
        bot.send_message(ALLOWED_CHAT_ID, "Выключение отменено.", reply_markup=get_main_keyboard())

    else:
        seconds = parse_time_to_seconds(message.text)
        if seconds is not None and seconds > 0:
            os.system("shutdown /a"); os.system(f"shutdown /s /t {seconds}")
            h, m = seconds // 3600, (seconds % 3600) // 60
            time_str = f"{h} ч. {m} мин." if h > 0 else f"{m} мин."
            bot.send_message(ALLOWED_CHAT_ID, f"Выключение через **{time_str}**.", parse_mode="Markdown", reply_markup=get_main_keyboard())
        else:
            bot.send_message(ALLOWED_CHAT_ID, "Неизвестная команда.", reply_markup=get_main_keyboard())

bot.infinity_polling()
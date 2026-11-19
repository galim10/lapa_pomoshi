import asyncio
import logging
import os
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv('BOT_TOKEN', '').strip()
CONTACT = os.getenv('VOLUNTEER_CONTACT', '').strip()
STAFF = {int(x.strip()) for x in os.getenv('STAFF_IDS', '').split(',') if x.strip()}
TZ = ZoneInfo('Europe/Moscow')
db = sqlite3.connect(os.getenv('BOT_DB', 'shelter.sqlite3'))
db.row_factory = sqlite3.Row
db.executescript('''
CREATE TABLE IF NOT EXISTS needs (id INTEGER PRIMARY KEY, name TEXT UNIQUE, active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS visits (id INTEGER PRIMARY KEY, need INTEGER NOT NULL, donor INTEGER NOT NULL, person TEXT NOT NULL, arrival TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending');
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS feedback (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, person TEXT NOT NULL, text TEXT NOT NULL, created TEXT NOT NULL);
''')
db.commit()
dp = Dispatcher()
editing = set()
writing_feedback = set()

def keyboard(rows):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=c) for t, c in row] for row in rows])

def menu():
    return keyboard([[('🐾 Нужды приюта', 'needs')], [('ℹ️ О приюте и контакты', 'info')], [('💬 Оставить отзыв', 'feedback')]])

def now():
    return datetime.now(TZ)

def available(need):
    return not db.execute("SELECT 1 FROM visits WHERE need=? AND status IN ('pending','confirmed') AND arrival>?", (need, now().isoformat())).fetchone()

def visit_text(row):
    return f"Заявка №{row['id']}\n{row['name']}\n{datetime.fromisoformat(row['arrival']).strftime('%d.%m.%Y в %H:%M')} (Москва)\n{row['person']}\nСтатус: {row['status']}"

@dp.message(CommandStart())
async def start(message: Message):
    await message.answer('🐾 Лапа помощи — бот помощи собакам приюта «Бирюлёво».', reply_markup=menu())

@dp.message(Command('id'))
async def user_id(message: Message):
    await message.answer(f'Ваш Telegram ID: {message.from_user.id}')

@dp.message(Command('admin'))
async def admin(message: Message):
    if message.from_user.id in STAFF:
        await message.answer('Панель волонтёра', reply_markup=keyboard([[('Обновить список нужд', 'edit')], [('Предстоящие заявки', 'visits')]]))

@dp.message(F.text)
async def update_needs(message: Message):
    uid = message.from_user.id
    if uid in writing_feedback:
        text = message.text.strip()
        if not text:
            await message.answer('Отзыв не должен быть пустым.')
            return
        person = message.from_user.full_name + (f' (@{message.from_user.username})' if message.from_user.username else f' (ID {uid})')
        with db:
            db.execute('INSERT INTO feedback (user_id, person, text, created) VALUES (?, ?, ?, ?)', (uid, person, text, now().isoformat()))
        writing_feedback.discard(uid)
        await message.answer('Спасибо за отзыв!', reply_markup=menu())
        for staff in STAFF:
            try:
                await message.bot.send_message(staff, f'Новый отзыв от {person}:\n{text}')
            except Exception:
                logging.exception('Не удалось отправить отзыв волонтёру %s', staff)
        return
    if uid not in editing or uid not in STAFF:
        return
    names = list(dict.fromkeys(line.strip() for line in message.text.splitlines() if line.strip()))
    if not names:
        await message.answer('Пришлите хотя бы одну позицию.')
        return
    with db:
        db.execute('UPDATE needs SET active=0')
        for name in names:
            db.execute('INSERT INTO needs(name, active) VALUES (?, 1) ON CONFLICT(name) DO UPDATE SET active=1', (name,))
        db.execute("INSERT OR REPLACE INTO settings VALUES ('updated', ?)", (now().strftime('%d.%m.%Y %H:%M'),))
    editing.discard(message.from_user.id)
    await message.answer('Список обновлён.', reply_markup=menu())

@dp.callback_query(F.data == 'menu')
async def callback_menu(q: CallbackQuery, bot: Bot):
    await q.answer()
    data = q.data or ''
    uid = q.from_user.id
    await q.message.answer('Выберите раздел:', reply_markup=menu())

@dp.callback_query(F.data == 'feedback')
async def callback_feedback(q: CallbackQuery):
    await q.answer()
    editing.discard(q.from_user.id)
    writing_feedback.add(q.from_user.id)
    await q.message.answer('Напишите отзыв одним сообщением.', reply_markup=keyboard([[('Отмена', 'cancel_feedback')]]))

@dp.callback_query(F.data == 'cancel_feedback')
async def callback_cancel_feedback(q: CallbackQuery):
    await q.answer()
    writing_feedback.discard(q.from_user.id)
    await q.message.answer('Отправка отзыва отменена.', reply_markup=menu())

@dp.callback_query(F.data == 'info')
async def callback_info(q: CallbackQuery, bot: Bot):
    await q.answer()
    data = q.data or ''
    uid = q.from_user.id
    contact = f'\nКонтакт волонтёра: {CONTACT}' if CONTACT else ''
    await q.message.answer('Приют «Бирюлёво»\nИнформация и правила посещения: https://izpriuta.ru/blog/pravila-poseshcheniya-priyuta\nКак помочь: https://izpriuta.ru/pomoch-priutu\nКонтакты: https://izpriuta.ru/contacts' + contact, reply_markup=menu())

@dp.callback_query(F.data == 'needs')
async def callback_needs(q: CallbackQuery, bot: Bot):
    await q.answer()
    data = q.data or ''
    uid = q.from_user.id
    rows = db.execute('SELECT * FROM needs WHERE active=1 ORDER BY id').fetchall()
    updated = db.execute("SELECT value FROM settings WHERE key='updated'").fetchone()
    text = 'Нужды приюта' + (f" (обновлено {updated[0]}, Москва)" if updated else '') + ':\n' + ('\n'.join(f"• {r['name']}" + ('' if available(r['id']) else ' — уже обещали') for r in rows) if rows else 'Список пока пуст.')
    buttons = [[(f"Привезу: {r['name'][:40]}", f"need:{r['id']}")] for r in rows if available(r['id'])]
    await q.message.answer(text, reply_markup=keyboard(buttons + [[('🏠 В меню', 'menu')]]))

@dp.callback_query(F.data == 'edit')
async def callback_edit(q: CallbackQuery, bot: Bot):
    await q.answer()
    data = q.data or ''
    uid = q.from_user.id
    if uid not in STAFF:
        return
    writing_feedback.discard(uid)
    editing.add(uid)
    await q.message.answer('Пришлите новый список нужд: по одной позиции на строку. Он полностью заменит текущий список.')

@dp.callback_query(F.data == 'visits')
async def callback_visits(q: CallbackQuery, bot: Bot):
    await q.answer()
    data = q.data or ''
    uid = q.from_user.id
    if uid not in STAFF:
        return
    rows = db.execute("SELECT visits.*, needs.name FROM visits JOIN needs ON needs.id=visits.need WHERE status IN ('pending','confirmed') AND arrival>? ORDER BY arrival", (now().isoformat(),)).fetchall()
    if not rows:
        await q.message.answer('Предстоящих заявок нет.')
    for r in rows:
        await q.message.answer(visit_text(r), reply_markup=keyboard([[('Подтвердить', f"status:{r['id']}:confirmed"), ('Отклонить', f"status:{r['id']}:rejected")]]))

@dp.callback_query(F.data.startswith('need:'))
async def callback_need(q: CallbackQuery, bot: Bot):
    await q.answer()
    data = q.data or ''
    uid = q.from_user.id
    nid = int(data.split(':')[1])
    if not db.execute('SELECT 1 FROM needs WHERE id=? AND active=1', (nid,)).fetchone() or not available(nid):
        await q.message.answer('Эта позиция уже недоступна.'); return
    days = [now().date() + timedelta(days=i) for i in range(2, 16)]
    await q.message.answer('Выберите дату визита:', reply_markup=keyboard([[(d.strftime('%d.%m (%a)'), f'date:{nid}:{d.isoformat()}')] for d in days if d.weekday() >= 2]))

@dp.callback_query(F.data.startswith('date:'))
async def callback_date(q: CallbackQuery, bot: Bot):
    await q.answer()
    data = q.data or ''
    uid = q.from_user.id
    _, nid, day = data.split(':')
    await q.message.answer('Выберите время по Москве:', reply_markup=keyboard([[(f'{h}:00', f'book:{nid}:{day}:{h}') for h in range(10, 14)], [(f'{h}:00', f'book:{nid}:{day}:{h}') for h in range(14, 17)]]))

@dp.callback_query(F.data.startswith('book:'))
async def callback_book(q: CallbackQuery, bot: Bot):
    await q.answer()
    data = q.data or ''
    uid = q.from_user.id
    _, nid, day, hour = data.split(':')
    arrival = datetime.fromisoformat(day).replace(hour=int(hour), tzinfo=TZ)
    delta = (arrival.date() - now().date()).days
    if not (2 <= delta <= 15 and arrival.weekday() >= 2 and 10 <= arrival.hour <= 16) or not db.execute('SELECT 1 FROM needs WHERE id=? AND active=1', (nid,)).fetchone() or not available(nid):
        await q.message.answer('Это время или позиция уже недоступны.'); return
    person = q.from_user.full_name + (f' (@{q.from_user.username})' if q.from_user.username else f' (ID {uid})')
    with db:
        cur = db.execute('INSERT INTO visits(need,donor,person,arrival) VALUES (?,?,?,?)', (nid, uid, person, arrival.isoformat()))
    row = db.execute('SELECT visits.*, needs.name FROM visits JOIN needs ON needs.id=visits.need WHERE visits.id=?', (cur.lastrowid,)).fetchone()
    await q.message.answer('Заявка создана. Ожидайте ответа волонтёра.\n' + visit_text(row), reply_markup=keyboard([[('Отменить заявку', f"cancel:{row['id']}")]]))
    for staff in STAFF:
        try:
            await bot.send_message(staff, visit_text(row), reply_markup=keyboard([[('Подтвердить', f"status:{row['id']}:confirmed"), ('Отклонить', f"status:{row['id']}:rejected")]]))
        except Exception:
            logging.exception('Не удалось уведомить волонтёра %s', staff)

@dp.callback_query(F.data.startswith('cancel:'))
async def callback_cancel(q: CallbackQuery, bot: Bot):
    await change_visit_status(q, bot)

@dp.callback_query(F.data.startswith('status:'))
async def callback_status(q: CallbackQuery, bot: Bot):
    await change_visit_status(q, bot)

async def change_visit_status(q: CallbackQuery, bot: Bot):
    await q.answer()
    data = q.data or ''
    uid = q.from_user.id
    parts = data.split(':'); vid = int(parts[1])
    row = db.execute('SELECT visits.*, needs.name FROM visits JOIN needs ON needs.id=visits.need WHERE visits.id=?', (vid,)).fetchone()
    if not row or row['status'] not in ('pending', 'confirmed') or datetime.fromisoformat(row['arrival']) <= now():
        await q.message.answer('Заявка уже закрыта или время визита прошло.'); return
    if parts[0] == 'cancel' and uid == row['donor']:
        status = 'cancelled'
    elif parts[0] == 'status' and uid in STAFF and len(parts) == 3 and parts[2] in ('confirmed', 'rejected') and row['status'] == 'pending':
        status = parts[2]
    else:
        return
    with db:
        db.execute('UPDATE visits SET status=? WHERE id=?', (status, vid))
    labels = {'confirmed': 'подтверждена', 'rejected': 'отклонена', 'cancelled': 'отменена'}
    text = f'Заявка №{vid} {labels[status]}.'
    await q.message.answer(text)
    recipients = STAFF if status == 'cancelled' else {row['donor']}
    for recipient in recipients - {uid}:
        try:
            await bot.send_message(recipient, text, reply_markup=keyboard([[('Отменить заявку', f'cancel:{vid}')]]) if status == 'confirmed' else None)
        except Exception:
            logging.exception('Не удалось отправить уведомление %s', recipient)

async def main():
    if not TOKEN:
        raise SystemExit('Укажите BOT_TOKEN в .env')
    bot = Bot(TOKEN)
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        db.close()

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())

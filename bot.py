import datetime
import hashlib
import logging
import ssl
import time
from math import ceil

import pymysql as sql
import requests
import telebot
from aiohttp import web
from dateutil import parser

import const
import markups

WEBHOOK_HOST = '78.155.218.194'
WEBHOOK_PORT = 443  # 443, 80, 88 or 8443 (port need to be 'open')
WEBHOOK_LISTEN = '0.0.0.0'  # In some VPS you may need to put here the IP address

WEBHOOK_SSL_CERT = './webhook_cert.pem'
WEBHOOK_SSL_PRIV = './webhook_pkey.pem'

WEBHOOK_URL_BASE = "https://{}:{}".format(WEBHOOK_HOST, WEBHOOK_PORT)
WEBHOOK_URL_PATH = "/{}/".format(const.token)

logger = telebot.logger
telebot.logger.setLevel(level=logging.INFO)

loggerPy = logging.getLogger('bot.py')
loggerPy.setLevel(logging.DEBUG)
ch = logging.FileHandler('journal.log')
formatter = logging.Formatter('%(levelname)s : DATE ---> %(asctime)s - %(message)s')
ch.setFormatter(formatter)
loggerPy.addHandler(ch)

bot = telebot.TeleBot(const.token)

app = web.Application()


# Process webhook calls
async def handle(request):
    if request.match_info.get('token') == bot.token:
        request_body_dict = await request.json()
        update = telebot.types.Update.de_json(request_body_dict)
        bot.process_new_updates([update])
        return web.Response()
    else:
        return web.Response(status=403)


app.router.add_post('/{token}/', handle)


def connect():
    return sql.connect("localhost", "root", "churchbynewton", "TRADER", use_unicode=True, charset="utf8")


def daily_check():
    db = connect()
    cur = db.cursor()
    r = 'SELECT uid, end_date FROM payments'
    cur.execute(r)
    res = cur.fetchall()
    r = "SELECT state, days FROM demo WHERE id = 1"
    cur.execute(r)
    state, days_left = cur.fetchone()
    if state:
        if days_left == 0:
            r = "UPDATE demo SET state = 0 WHERE id = 1"
        else:
            r = "UPDATE demo SET days = days - 1 WHERE id = 1"
        cur.execute(r)
    today = str(datetime.datetime.now()).split(' ')[0]
    after_tomorrow = parser.parse(today) + datetime.timedelta(days=2)
    for user in res:
        if after_tomorrow == parser.parse(str(user[1])):
            text = 'У вас истекает подписка.'
            bot.send_message(user[0], text, reply_markup=markups.payBtnMarkup())
            time.sleep(0.1)
        if parser.parse(str(user[1])) <= parser.parse(today):
            text = 'Время действия вашей подписки окончено.'
            bot.send_message(user[0], text)
            r = 'DELETE FROM payments WHERE uid=%s; INSERT INTO lost_subs(uid, end_date) VALUES (%s, %s);'
            cur.execute(r, user[0], user[0], user[1])
            time.sleep(0.1)
    db.commit()
    db.close()


def addInvitation(user_id, invited_user_id):
    db = connect()
    cur = db.cursor()
    r = "SELECT * FROM INVITATIONS WHERE INVITED=%s"
    cur.execute(r, invited_user_id)
    if not cur.fetchone() and int(user_id) != int(invited_user_id):
        r = "INSERT INTO INVITATIONS (ID, INVITED) VALUES (%s, %s)"
        cur.execute(r, (user_id, invited_user_id))
        db.commit()
    db.close()


@bot.message_handler(regexp="⬅️Назад")
@bot.message_handler(commands=["start"])
def start(message):
    text = message.text.split(" ")
    if len(text) == 2:
        if text[1].isdigit():
            initial_id = text[1]
            addInvitation(initial_id, message.chat.id)
    db = connect()
    cur = db.cursor()
    r = 'SELECT * FROM users WHERE uid = %s'
    cur.execute(r, message.chat.id)
    if not cur.fetchone():
        r = "INSERT INTO users (uid, first_name, last_name, alias) VALUE (%s,%s,%s,%s)"
        cur.execute(r, (message.chat.id, message.from_user.first_name,
                        message.from_user.last_name, message.from_user.username))
        db.commit()
    bot.send_message(message.chat.id, const.startMsg % message.chat.id, reply_markup=markups.mainMenu(message.chat.id),
                     parse_mode="html")
    db.close()


def get_user_balance(uid):
    db = connect()
    cur = db.cursor()
    r = "SELECT balance FROM users WHERE uid = %s"
    cur.execute(r, uid)
    balance = cur.fetchone()
    db.close()
    return balance[0] / 100000000


def get_ids():
    db = connect()
    cur = db.cursor()
    r = "SELECT uid FROM users"
    cur.execute(r)
    data = cur.fetchall()
    db.close()
    res = []
    for i in data:
        res.append(i[0])
    return res


def get_paid_ids():
    db = connect()
    cur = db.cursor()
    r = "SELECT uid FROM payments"
    cur.execute(r)
    data = cur.fetchall()
    db.close()
    res = []
    for i in data:
        res.append(i[0])
    return res


def get_lost_subs_ids():
    db = connect()
    cur = db.cursor()
    r = "SELECT uid FROM lost_subs"
    cur.execute(r)
    data = cur.fetchall()
    db.close()
    res = []
    for i in data:
        res.append(i[0])
    return res


# Admin
@bot.message_handler(regexp="👤 Админ-панель")
def admin(message):
    if message.chat.id in const.admin:
        bot.send_message(message.chat.id, "Админ-панель", reply_markup=markups.adminPanel())


@bot.callback_query_handler(func=lambda call: call.data == "admin")
def admin2(call):
    bot.edit_message_text("Админ-панель", call.message.chat.id, call.message.message_id)
    bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=markups.adminPanel())


@bot.callback_query_handler(func=lambda call: call.data == "addVideo")
def add_video(call):
    msg = bot.send_message(call.message.chat.id, "Введите ссылку на видео")
    bot.register_next_step_handler(msg, get_video)


@bot.callback_query_handler(func=lambda call: call.data == "usersTypes")
def user_types(call):
    bot.edit_message_text("Список пользователей", call.message.chat.id, call.message.message_id,
                          reply_markup=markups.usersTypes())


@bot.callback_query_handler(func=lambda call: call.data[:5] == "users")
def show_users(call):
    const.listPointer = 0
    get_users(call.data[6:])
    bot.edit_message_text("Список пользователей", call.message.chat.id, call.message.message_id)
    bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=markups.users())


@bot.callback_query_handler(func=lambda call: call.data == "nextList")
def listforward(call):
    const.listPointer += 1
    bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=markups.users())


@bot.callback_query_handler(func=lambda call: call.data == "prevList")
def listback(call):
    const.listPointer -= 1
    bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=markups.users())


def get_users(user_type):
    db = connect()
    cur = db.cursor()
    r = "SELECT * FROM users"
    cur.execute(r)
    data = cur.fetchall()

    if user_type == "paid":
        r = "SELECT * FROM payments"
        cur.execute(r)
        data_paid = cur.fetchall()
        db.close()
        const.userList.clear()
        for user in data:
            for user_p in data_paid:
                if user[0] == user_p[0]:
                    now = time.time()  # Время в секундах настоящее
                    sub_date = time.strptime(user_p[1], "%Y-%m-%d")  # Время в struct_time подписки
                    sub_s = time.mktime(sub_date)  # Время подписки в секундах
                    delta = ceil((sub_s - now) / (60 * 60 * 24))

                    s = user[2] + ' '
                    if user[3] is not None:
                        s += user[3] + ' '
                    s += str(delta) + '%' + str(user[0])
                    const.userList.append(s)
        return const.userList
    elif user_type == "not_paid":
        r = "SELECT * FROM payments"
        cur.execute(r)
        data_paid = cur.fetchall()
        const.userList.clear()
        db.close()
        for user in data:
            if user[0] not in [user_p[0] for user_p in data_paid]:
                s = user[2] + ' '
                if user[3] is not None:
                    s += user[3] + ' '
                s += '%' + str(user[0])
                const.userList.append(s)
        return const.userList
    else:
        const.userList.clear()
        db.close()
        for user in data:
            s = user[2] + ' '
            if user[3] is not None:
                s += user[3] + ' '
            s += '%' + str(user[0])
            const.userList.append(s)
        const.userList.sort()
        return const.userList


@bot.callback_query_handler(func=lambda call: call.data[0] == '<')
def detailed_info(call):
    db = connect()
    cur = db.cursor()
    r = "SELECT * FROM payments WHERE uid = %s"
    cur.execute(r, call.data[1:])
    data = cur.fetchone()
    if data:
        now = time.time()  # Время в секундах настоящее
        sub_date = time.strptime(data[1], "%Y-%m-%d")  # Время в struct_time подписки
        sub_s = time.mktime(sub_date)  # Время подписки в секундах
        delta = ceil((sub_s - now) / (60 * 60 * 24))

        text = "Куплена подписка до %s\n" \
               "Оставшееся количество дней: %s " % (data[1], str(delta))
    else:
        text = "У данного пользователя не куплена подписка"
    r = "SELECT INVITED FROM INVITATIONS WHERE ID = %s"
    cur.execute(r, call.data[1:])
    ids = cur.fetchall()
    if ids:
        text += "\nПригласил следующий список пользователей:\n"
        for user in ids:
            r = "SELECT first_name, last_name FROM users WHERE uid = %s"
            cur.execute(r, user[0])
            try:
                text += "<b>" + " ".join(cur.fetchone()) + "</b>\n"
            except Exception:
                pass
                # text += "<b>" + str(user) + "</b>\n"
    else:
        text += "\nЕще не пригласил ни одного пользователя"
    db.close()
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, parse_mode="html")
    bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id,
                                  reply_markup=markups.showDetails(call.data[1:]))


@bot.callback_query_handler(func=lambda call: call.data == "changePrices")
def change_prices(call):
    bot.edit_message_text("Изменить цену на подписку", call.message.chat.id, call.message.message_id)
    bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id,
                                  reply_markup=markups.chooseMonth())


@bot.callback_query_handler(func=lambda call: call.data[0:2] == "$$")
def show_info(call):
    text = "Текущая цена подписки: {price}\nВведите новую цену в биткойнах, цифры разделены точкой (0.15)"
    if call.data[2:] == "15":
        text = text.format(price=str(const.days15))
        msg = bot.send_message(call.message.chat.id, text)
        bot.register_next_step_handler(msg, change15)
    if call.data[2:] == "30":
        text = text.format(price=str(const.days30))
        msg = bot.send_message(call.message.chat.id, text)
        bot.register_next_step_handler(msg, change30)
    if call.data[2:] == "60":
        text = text.format(price=str(const.days60))
        msg = bot.send_message(call.message.chat.id, text)
        bot.register_next_step_handler(msg, change60)
    if call.data[2:] == "90":
        text = text.format(price=str(const.days90))
        msg = bot.send_message(call.message.chat.id, text)
        bot.register_next_step_handler(msg, change90)
    if call.data[2:] == "forever":
        text = text.format(price=str(const.days_forever))
        msg = bot.send_message(call.message.chat.id, text)
        bot.register_next_step_handler(msg, change_forever)


def change15(message):
    try:
        db = connect()
        cur = db.cursor()
        r = "UPDATE prices SET price=%s WHERE days=15"
        cur.execute(r, float(message.text))
        db.commit()
        db.close()
        const.days15 = float(message.text)
        bot.send_message(message.chat.id, "Цена изменена", reply_markup=markups.adminPanel())
    except ValueError:
        bot.send_message(message.chat.id, "Неправильный формат", reply_markup=markups.adminPanel())


def change30(message):
    try:
        db = connect()
        cur = db.cursor()
        r = "UPDATE prices SET price=%s WHERE days=30"
        cur.execute(r, float(message.text))
        db.commit()
        db.close()
        const.days30 = float(message.text)
        bot.send_message(message.chat.id, "Цена изменена", reply_markup=markups.adminPanel())
    except ValueError:
        bot.send_message(message.chat.id, "Неправильный формат", reply_markup=markups.adminPanel())


def change60(message):
    try:
        db = connect()
        cur = db.cursor()
        r = "UPDATE prices SET price=%s WHERE days=60"
        cur.execute(r, float(message.text))
        db.commit()
        db.close()
        const.days60 = float(message.text)
        bot.send_message(message.chat.id, "Цена изменена", reply_markup=markups.adminPanel())
    except ValueError:
        bot.send_message(message.chat.id, "Неправильный формат", reply_markup=markups.adminPanel())


def change90(message):
    try:
        db = connect()
        cur = db.cursor()
        r = "UPDATE prices SET price=%s WHERE days=90"
        cur.execute(r, float(message.text))
        db.commit()
        db.close()
        const.days90 = float(message.text)
        bot.send_message(message.chat.id, "Цена изменена", reply_markup=markups.adminPanel())
    except ValueError:
        bot.send_message(message.chat.id, "Неправильный формат", reply_markup=markups.adminPanel())


def change_forever(message):
    try:
        db = connect()
        cur = db.cursor()
        r = "UPDATE prices SET price=%s WHERE days=0"
        cur.execute(r, float(message.text))
        db.commit()
        db.close()
        const.days_forever = float(message.text)
        bot.send_message(message.chat.id, "Цена изменена", reply_markup=markups.adminPanel())
    except ValueError:
        bot.send_message(message.chat.id, "Неправильный формат", reply_markup=markups.adminPanel())


@bot.callback_query_handler(func=lambda call: call.data[0:10] == "changeDate")
def change_date(call):
    const.chosenUserId = call.data[10:]
    msg = bot.send_message(call.message.chat.id, "Введите дату формате 2017-03-23 <b>(гггг-мм-дд)</b>\n",
                           parse_mode="html")
    bot.register_next_step_handler(msg, confirm_date)


def confirm_date(message):
    if len(message.text) == 10:
        date = message.text.replace(".", "-")
        db = connect()
        cur = db.cursor()
        r = "SELECT * FROM payments WHERE uid = %s"
        cur.execute(r, const.chosenUserId)
        if not cur.fetchone():
            r = "INSERT INTO payments (end_date, uid) VALUE (%s,%s)"
        else:
            r = "UPDATE payments SET end_date = %s WHERE uid = %s"
        cur.execute(r, (date, const.chosenUserId))
        db.commit()
        db.close()
        bot.send_message(message.chat.id, "Срок подписки изменен", reply_markup=markups.adminPanel())
    else:
        bot.send_message(message.chat.id, "Неправильный формат ввода", reply_markup=markups.adminPanel())


def get_video(message):
    db = connect()
    cur = db.cursor()
    r = 'INSERT INTO VIDEO (link) VALUES (%s)'
    cur.execute(r, message.text)
    db.commit()
    db.close()
    bot.send_message(message.chat.id, "Видео успешно добавлено!")


@bot.callback_query_handler(func=lambda call: call.data == "demo on")
def turn_on_demo(call):
    db = connect()
    cur = db.cursor()
    r = "SELECT state FROM demo"
    cur.execute(r)
    state = int(cur.fetchone()[0])
    db.close()
    if state:
        bot.send_message(call.message.chat.id, "Демо режим уже включен", reply_markup=markups.adminPanel())
    else:
        msg = bot.send_message(call.message.chat.id, "Введите количество дней, на которое хотите включить")
        bot.register_next_step_handler(msg, handle_days)


@bot.callback_query_handler(func=lambda call: call.data == "demo off")
def turn_off(call):
    db = connect()
    cur = db.cursor()
    r = "SELECT state, days FROM demo"
    cur.execute(r)
    state, days = cur.fetchone()
    db.close()
    if state:
        bot.send_message(call.message.chat.id, "Демо режим будет работать для пользователей еще %s дней" % str(days),
                         reply_markup=markups.adminPanel())
    else:
        bot.send_message(call.message.chat.id, "Демо режим выключен", reply_markup=markups.adminPanel())


def handle_days(message):
    try:
        days = int(message.text)
        db = connect()
        cur = db.cursor()
        r = "UPDATE demo SET state = 1"
        cur.execute(r)
        r = "UPDATE demo SET days = %s"
        cur.execute(r, days)
        r = "SELECT uid FROM users"
        cur.execute(r)
        ids = cur.fetchall()
        today = str(datetime.datetime.now()).split(' ')[0]
        end_day = str(parser.parse(today) + datetime.timedelta(days=days)).split(' ')[0]
        for user in ids:
            r = "SELECT * FROM payments WHERE uid = (%s)"
            cur.execute(r, user)
            if cur.fetchone():
                continue
            else:
                request = "INSERT INTO payments (uid, end_date) VALUE (%s,%s)"
                cur.execute(request, (user, end_day))
            time.sleep(0.1)
        db.commit()
        db.close()
        bot.send_message(message.chat.id, "Демо режим включен для всех пользователей на %s дней" % message.text,
                         reply_markup=markups.adminPanel())
    except ValueError:
        bot.send_message(message.chat.id, "Неправильный формат")


@bot.callback_query_handler(func=lambda call: call.data == "toAll")
def get_text(call):
    msg = bot.send_message(call.message.chat.id, "Введите текст, который хотите отправить всем пользователям")
    bot.register_next_step_handler(msg, simple_distribution)


def simple_distribution(message):
    count = 0
    for user_id in get_ids():
        if user_id in const.admin:
            continue
        if count == 20:
            time.sleep(1)
        bot.send_message(user_id, message.text)
        count += 1
    bot.send_message(message.chat.id, "Сообщение успешно отправлено всем пользователям!")


@bot.callback_query_handler(func=lambda call: call.data == "toPaid")
def get_text1(call):
    msg = bot.send_message(call.message.chat.id, "Введите текст, который хотите отправить пользователям,"
                                                 " которые оплатили подписку")
    bot.register_next_step_handler(msg, paid_distribution)


def paid_distribution(message):
    count = 0
    for user_id in get_paid_ids():
        if user_id in const.admin:
            continue
        if count == 20:
            time.sleep(1)
        bot.send_message(user_id, message.text)
        count += 1
    bot.send_message(message.chat.id, "Сообщение успешно отправлено всем пользователям!")


@bot.callback_query_handler(func=lambda call: call.data == "toLostSubs")
def get_text2(call):
    msg = bot.send_message(call.message.chat.id, "Введите текст, который хотите отправить пользователям,"
                                                 " у которых кончилась подписка")
    bot.register_next_step_handler(msg, lost_subs_distribution)


def lost_subs_distribution(message):
    count = 0
    for user_id in get_lost_subs_ids():
        if user_id in const.admin:
            continue
        if count == 20:
            time.sleep(1)
        bot.send_message(user_id, message.text)
        count += 1
    bot.send_message(message.chat.id, "Сообщение успешно отправлено пользователям, у которых кончилась подписка!")


@bot.callback_query_handler(func=lambda call: call.data == "toNotPaid")
def get_text3(call):
    msg = bot.send_message(call.message.chat.id, "Введите текст, который хотите отправить пользователям,"
                                                 " которые не оплатили подписку")
    bot.register_next_step_handler(msg, not_paid_distribution)


def not_paid_distribution(message):
    count = 0
    get_users("not_paid")
    for user in const.userList:
        symbol = user.find('%')
        uid = int(user[symbol + 1:])
        if uid in const.admin:
            continue
        if count % 20 == 0:
            time.sleep(1)
        bot.send_message(uid, message.text)
        count += 1
    bot.send_message(message.chat.id, "Сообщение успешно отправлено всем пользователям, которые не оплатили подписку!")


# Первая клавиатура
@bot.message_handler(regexp="👥 Партнерская программа")
def materials(message):
    balance = "<b>Ваш баланс:</b> %s BTC\n" % get_user_balance(message.chat.id)
    text = "<b>Ваша реферальная ссылка:</b>\nhttps://t.me/BestCryptoInsideBot?start=%s" % message.chat.id
    bot.send_message(message.chat.id, const.marketingMsg + balance + text, parse_mode="html",
                     reply_markup=markups.withdrawBtn())


@bot.callback_query_handler(func=lambda call: call.data == "withdraw")
def withdraw(call):
    msg = bot.send_message(call.message.chat.id, "Введите сумму, которую хотите вывести")
    bot.register_next_step_handler(msg, check_sum)


def check_sum(message):
    try:
        value = float(message.text)
        if get_user_balance(message.chat.id) >= value > 0:
            const.values[message.chat.id] = value
            msg = bot.send_message(message.chat.id, "Введите адрес, на который будет произведена выплата")
            bot.register_next_step_handler(msg, send_request)
        else:
            bot.send_message(message.chat.id, "Недостаточно средств")
    except:
        bot.send_message(message.chat.id, "Неккоректная сумма")


def send_request(message):
    bot.send_message(const.admin[0], "Новая заявка на вывод %s BTC на адрес %s"
                     % (const.values.get(message.chat.id), message.text))
    bot.send_message(message.chat.id, "Ваша заявка отпралена!\nОжидайте подтверждения.")


@bot.callback_query_handler(func=lambda call: call.data == "inv_users")
def inv_users(call):
    db = connect()
    cur = db.cursor()
    r = "SELECT INVITED FROM INVITATIONS WHERE ID = %s"
    cur.execute(r, str(call.message.chat.id))
    inv_ids = cur.fetchall()
    db.close()
    if inv_ids:
        s = "Вы пригласили: \n"
        for id in inv_ids:
            r = "SELECT * FROM users WHERE uid=%s"
            cur.execute(r, str(id[0]))
            user = cur.fetchone()
            s += user[2]
            if user[3] is not None:
                s += user[3]
            s += '\n'
            bot.edit_message_text(s, call.message.chat.id, call.message.message_id)
    else:
        bot.edit_message_text("Вы никого не пригласили", call.message.chat.id, call.message.message_id)


@bot.message_handler(regexp="Посмотреть отзывы")
def show_videos(message):
    db = connect()
    cur = db.cursor()
    r = "SELECT link FROM VIDEO"
    cur.execute(r)
    data = cur.fetchall()
    db.close()
    for i in data:
        bot.send_message(message.chat.id, i[0])
        time.sleep(1)


@bot.message_handler(regexp="Начать работу")
def start_work(message):
    bot.send_message(message.chat.id, "Текст 2 ", reply_markup=markups.mainMenu(message.chat.id))


# Вторая клавиатура
@bot.message_handler(regexp="📈 О BestCryptoInsideBot")
def about(msg):
    txt = const.aboutMsg
    bot.send_message(msg.chat.id, txt, parse_mode="html")


@bot.message_handler(regexp="📱 Статус подписки")
def subscription_status(msg):
    db = connect()
    cur = db.cursor()
    r = "SELECT end_date FROM payments WHERE uid = %s"
    cur.execute(r, msg.chat.id)
    end = cur.fetchone()
    db.close()
    if end:
        now = time.time()  # Время в секундах настоящее
        sub_date = time.strptime(end[0], "%Y-%m-%d")  # Время в struct_time подписки
        sub_s = time.mktime(sub_date)  # Время подписки в секундах
        delta = ceil((sub_s - now) / (60 * 60 * 24))
        bot.send_message(msg.chat.id, "Количество оставшихся дней подписки: " + str(delta))
    else:
        bot.send_message(msg.chat.id, "Вы ещё не купили подписку")


@bot.message_handler(regexp="🌏 Купить VIP подписку")
def buy_vip(message):
    bot.send_message(message.chat.id, const.startWorkMsg,
                     reply_markup=markups.startWork())


@bot.message_handler(regexp="🔧 Связаться со службой поддержки")
def support(msg):
    message = bot.send_message(msg.chat.id, "Опишите свою проблему: \n\n"
                                            'Для отмены введите "Отмена"',
                               reply_markup=telebot.types.ReplyKeyboardRemove(selective=True))
    bot.register_next_step_handler(message, send_to_support)


def send_to_support(message):
    if message.text.upper() != "ОТМЕНА":
        bot.send_message(const.admin[0], "Новое обращение в службу поддержки:\n" + message.text)
        bot.send_message(message.chat.id, "Ваше сообщение принято на рассмотрение, "
                                          "администратор свяжетя с вами в ближайшее время.",
                         reply_markup=markups.mainMenu(message.chat.id))
    else:
        bot.send_message(message.chat.id, "Отменено",
                         reply_markup=markups.mainMenu(message.chat.id))


@bot.message_handler(regexp="📊 Посмотреть результаты")
def results(msg):
    bot.send_message(msg.chat.id, "Результаты вы можете посмотреть по кнопке ниже",
                     reply_markup=telebot.types.InlineKeyboardMarkup().row(telebot.types.InlineKeyboardButton(
                         "Результаты",
                         url='https://t.me/results_crypto_signal')))


@bot.callback_query_handler(func=lambda call: call.data == "conditions")
def show_conditions(call):
    bot.send_message(call.message.chat.id, const.conditionsMsg)


@bot.callback_query_handler(func=lambda call: call.data == "news")
def channel_link(call):
    bot.send_message(call.message.chat.id, const.channelLink)


@bot.callback_query_handler(func=lambda call: call.data == "socialNetworks")
def show_media(call):
    bot.edit_message_text("текст", call.message.chat.id, call.message.message_id)
    bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=markups.socialNetworks())


@bot.callback_query_handler(func=lambda call: call.data == "profit")
def show_profit(call):
    bot.send_message(call.message.chat.id, "Выберите подписку", reply_markup=markups.chooseDuration())


@bot.callback_query_handler(func=lambda call: call.data == "processPayment")
def choose_duration(call):
    bot.send_message(call.message.chat.id, const.profitMsg, reply_markup=markups.payBtnMarkup())


@bot.callback_query_handler(func=lambda call: call.data[:4] == "days")
def process_payment(call):
    days = call.data[4:]
    if days == "15":
        pay = const.days15
    elif days == "30":
        pay = const.days30
    elif days == "60":
        pay = const.days60
    elif days == "90":
        pay = const.days90
    else:
        pay = const.days_forever
    address = create_btc_address()
    db = connect()
    cur = db.cursor()
    r = 'SELECT * FROM TEMP_DETAILS WHERE ID = %s'
    cur.execute(r, call.message.chat.id)
    if cur.fetchone():
        r = 'UPDATE TEMP_DETAILS SET BTC_ADDRESS = %s WHERE ID = %s'
        cur.execute(r, (address, call.message.chat.id))
    else:
        r = 'INSERT INTO TEMP_DETAILS (ID, BTC_ADDRESS) VALUES (%s, %s)'
        cur.execute(r, (call.message.chat.id, address))
    db.commit()
    db.close()
    bot.send_message(call.message.chat.id, const.paymentMsg.format(pay, address), parse_mode="html")


def create_btc_address():
    sign = hashlib.md5("".join((const.wallet_id, const.walletApiKey)).encode()).hexdigest()
    data = {
        "wallet_id": const.wallet_id,
        "sign": sign,
        "action": "create_btc_address"
    }
    url = "https://wallet.free-kassa.ru/api_v1.php"
    response = requests.post(url, data).json()
    return response.get("data").get("address")


def send_payment_message(cid):
    bot.send_message(256711367,
                     "Сообщение отправлено из блока send_payment_message с переданным сюда параметром " + cid)


# if __name__ == '__main__':
#     with sqlite3.connect(const.dbName) as db:
#         cursor = db.cursor()
#         sql = '''CREATE TABLE IF NOT EXISTS payments (
#             id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
#             uid INTEGER NOT NULL,
#             end_date TEXT NOT NULL)'''
#         cursor.execute(sql)
#         db.commit()
#     bot.remove_webhook()
#     bot.polling()


def init_bot():
    bot.remove_webhook()
    bot.set_webhook(url=WEBHOOK_URL_BASE + WEBHOOK_URL_PATH,
                    certificate=open(WEBHOOK_SSL_CERT, 'r'))

    context = ssl.SSLContext(ssl.PROTOCOL_TLSv1_2)
    context.load_cert_chain(WEBHOOK_SSL_CERT, WEBHOOK_SSL_PRIV)

    db = connect()
    cur = db.cursor()
    r = "SELECT * FROM prices ORDER BY days"
    cur.execute(r)
    prices = cur.fetchall()
    const.days_forever = float(prices[0][1])
    const.days15 = float(prices[1][1])
    const.days30 = float(prices[2][1])
    const.days60 = float(prices[3][1])
    const.days90 = float(prices[4][1])
    db.close()

    web.run_app(
        app,
        host=WEBHOOK_LISTEN,
        port=WEBHOOK_PORT,
        ssl_context=context,
    )


if __name__ == '__main__':
    init_bot()

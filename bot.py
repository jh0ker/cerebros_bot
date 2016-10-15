import logging
from datetime import datetime
from io import BytesIO, BufferedReader

from telegram.ext import Updater, CommandHandler, RegexHandler, \
    MessageHandler, Filters, CallbackQueryHandler, ConversationHandler
from telegram.ext.dispatcher import run_async
from telegram import ParseMode, ReplyKeyboardMarkup, ReplyKeyboardHide, \
    ChatAction, ForceReply, InlineKeyboardMarkup, InlineKeyboardButton, Emoji
from telegram.utils.botan import Botan
from pony.orm import db_session, select, desc

from credentials import TOKEN, BOTAN_TOKEN
from start_bot import start_bot
from database import db

from admin import Admin
from believer import Believer
from reporter import Reporter

# States the bot can have (maintained per chat id)
ADD, REMOVE, EDIT, WAIT, PHONE_NR, ACCOUNT_NR, BANK_NAME, REMARK, ATTACHMENT = range(9)

options = {PHONE_NR: "Phone number", ACCOUNT_NR: "Telegram ID",
           BANK_NAME: "Name of bank account owner", REMARK: "DNA",
           ATTACHMENT: "Attachment"}

# Enable reverse lookup
for k, v in list(options.items()):
    options[v] = k

_grid = [[options[ACCOUNT_NR]],
         [options[BANK_NAME]],
         [options[PHONE_NR]],
         [options[REMARK]],
         [options[ATTACHMENT]],
         ['/cancel']]

CAT_KEYBOARD = ReplyKeyboardMarkup(_grid, selective=True)
DB_NAME = 'bot.sqlite'

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.DEBUG)
logger = logging.getLogger(__name__)

u = Updater(TOKEN)
dp = u.dispatcher

db.bind('sqlite', DB_NAME, create_db=True)
db.generate_mapping(create_tables=True)

with db_session:
    if len(select(a for a in Admin if a.id is 10049375)) is 0:
        # Create initial admin account
        Admin(id=10049375, first_name="Jannes", super_admin=True)
    if len(select(a for a in Admin if a.id is 46348706)) is 0:
        # Create initial admin account
        Admin(id=46348706, first_name="Jackson", super_admin=True)
        # pass

botan = False
if BOTAN_TOKEN:
    botan = Botan(BOTAN_TOKEN)

help_text = "This bot keeps a database of known trustworthy bitcoin traders by recording " \
            "their phone number, bank account number and name.\n\n" \
            "<b>Usage:</b>\n" \
            "/search - Search the database for reports\n\n" \
            "Donations via BTC are welcome: 1EPu17mBM2zw4LcupURgwsAuFeKQrTa1jy"

admin_help_text = "\n\n" \
                  "<b>Admin commands:</b>\n" \
                  "/new - Add a new trusted trader\n" \
                  "/edit - Edit an existing trusted trader\n" \
                  "/delete - Delete a trusted trader\n" \
                  "/cancel - Cancel current operation"

super_admin_help_text = "\n\n" \
                        "<b>Super Admin commands:</b>\n" \
                        "/add_admin - Register a new admin\n" \
                        "/remove_admin - Remove an admin\n" \
                        "/download_database - Download complete database"


def error(bot, update, error):
    """ Simple error handler """
    logger.exception(error)


@db_session
def help(bot, update):
    """ Handler for the /help command """
    from_user = update.message.from_user
    admin = get_admin(from_user)

    text = help_text

    if admin:
        text += admin_help_text
        if admin.super_admin:
            text += super_admin_help_text

    update.message.reply_text(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


def get_admin(from_user):
    admin = Admin.get(id=from_user.id)
    if admin:
        admin.first_name = from_user.first_name
        admin.last_name = from_user.last_name
        admin.username = from_user.username
    return admin


def get_reporter(from_user):
    reporter = Reporter.get(id=from_user.id)
    if reporter:
        reporter.first_name = from_user.first_name
        reporter.last_name = from_user.last_name
        reporter.username = from_user.username
    return reporter


@run_async
def track(update, event_name):
    if botan:
        botan.track(message=update.message, event_name=event_name)


@db_session
def add_believer(bot, update):
    admin = get_admin(update.message.from_user)

    if not admin:
        return ConversationHandler.END

    update.message.reply_text("Forward me a message of the user that is reporting the trustworthy "
                              "bitcoin trader or use /cancel to cancel")

    return ADD


@db_session
def add_believer_2(bot, update, user_data):
    forward_from = update.message.forward_from
    reporter = get_reporter(forward_from)

    if not reporter:
        reporter = Reporter(id=forward_from.id,
                            first_name=forward_from.first_name,
                            last_name=forward_from.last_name,
                            username=forward_from.username)
        track(update, 'new_reporter')

    believer = Believer(added_by=get_admin(update.message.from_user))
    believer.reported_by.add(reporter)
    track(update, 'new_report')
    db.commit()

    update.message.reply_text(
        "Created report <b>#%d</b>! Please enter trustworthy bitcoin trader information:"
        % believer.id,
        reply_markup=CAT_KEYBOARD,
        parse_mode=ParseMode.HTML)

    user_data['id'] = believer.id
    return EDIT


@db_session
def remove_believer(bot, update):
    admin = get_admin(update.message.from_user)

    if not admin:
        return ConversationHandler.END

    update.message.reply_text(
        "Please send the Report # of the report you wish to remove or send /cancel to cancel",
        reply_markup=ForceReply(selective=True))

    return REMOVE


@db_session
def remove_believer_2(bot, update):
    try:
        report_id = int(update.message.text.replace('#', ''))
    except ValueError:
        update.message.reply_text("Not a valid report number. Try again or use /cancel to abort.")

    else:
        believer = Believer.get(id=report_id)
        if believer:
            believer.delete()
            update.message.reply_text("Deleted report!")
            return ConversationHandler.END
        else:
            update.message.reply_text(
                "Could not find report number. Try again or use /cancel to abort.",
                reply_markup=ForceReply(selective=True))


def edit_believer(bot, update):
    global state
    with db_session:
        admin = get_admin(update.message.from_user)
    if not admin:
        return ConversationHandler.END

    update.message.reply_text(
        "Please send the Report # of the report you wish to edit or send /cancel to cancel",
        reply_markup=ForceReply(selective=True))

    return WAIT


@db_session
def edit_believer_2(bot, update, user_data):
    try:
        believer_id = int(update.message.text.replace('#', ''))

    except ValueError:
        update.message.reply_text("Not a valid report number. Try again or use /cancel to abort.")

    else:
        believer = Believer.get(id=believer_id)

        if believer:
            update.message.reply_text(
                "%s\n\nPlease enter new trustworthy bitcoin trader information:" % str(believer),
                reply_markup=CAT_KEYBOARD)

            user_data['id'] = believer.id
            return EDIT

        else:
            update.message.reply_text(
                "Could not find report number. Try again or use /cancel to abort.")


@db_session
def select_option(bot, update, user_data):
    option = options[update.message.text]
    user_data['option'] = option

    if option != ATTACHMENT:
        update.message.reply_text("Please enter " + update.message.text,
                                  reply_markup=ForceReply(selective=True))
    else:
        update.message.reply_text("Please send a photo or file to attach to this report",
                                  reply_markup=ForceReply(selective=True))

    return option


@db_session
def edit_phone_nr(bot, update, user_data):
    believer = Believer.get(id=user_data['id'])
    believer.phone_nr = update.message.text

    update.message.reply_text("Add more info or send /cancel if you're done.",
                              reply_markup=CAT_KEYBOARD)

    return EDIT


@db_session
def edit_account_nr(bot, update, user_data):
    believer = Believer.get(id=user_data['id'])
    believer.account_nr = update.message.text

    update.message.reply_text("Add more info or send /cancel if you're done.",
                              reply_markup=CAT_KEYBOARD)

    return EDIT


@db_session
def edit_bank_name(bot, update, user_data):
    believer = Believer.get(id=user_data['id'])
    believer.bank_name = update.message.text

    update.message.reply_text("Add more info or send /cancel if you're done.",
                              reply_markup=CAT_KEYBOARD)

    return EDIT


@db_session
def edit_remark(bot, update, user_data):
    believer = Believer.get(id=user_data['id'])
    believer.remark = update.message.text

    update.message.reply_text("Add more info or send /cancel if you're done.",
                              reply_markup=CAT_KEYBOARD)

    return EDIT


@db_session
def edit_attachment(bot, update, user_data):
    believer = Believer.get(id=user_data['id'])

    if update.message.photo:
        believer.attached_file = \
            'photo:' + update.message.photo[-1].file_id
    elif update.message.document:
        believer.attached_file = \
            'document:' + update.message.document.file_id

    update.message.reply_text("Add more info or send /cancel if you're done.",
                              reply_markup=CAT_KEYBOARD)

    return EDIT

@db_session
def add_admin(bot, update):
    global state
    admin = get_admin(update.message.from_user)

    if not admin or not admin.super_admin:
        return ConversationHandler.END

    update.message.reply_text(
        "Forward me a message of the user you want to add as admin or send /cancel to cancel")

    return ADD


@db_session
def add_admin_2(bot, update):
    forward_from = update.message.forward_from
    admin = get_admin(forward_from)

    if not admin:
        Admin(id=forward_from.id,
              first_name=forward_from.first_name,
              last_name=forward_from.last_name,
              username=forward_from.username)
        update.message.reply_text("Successfully added admin")

    else:
        update.message.reply_text("This user is already an admin")

    return ConversationHandler.END


@db_session
def remove_admin(bot, update):
    admin = get_admin(update.message.from_user)

    if not admin or not admin.super_admin:
        return ConversationHandler.END

    update.message.reply_text(
        "Forward me a message of the admin you want to remove or send /cancel to cancel")

    return ADD


@db_session
def remove_admin_2(bot, update):
    admin = get_admin(update.message.forward_from)

    if admin and not admin.super_admin:
        admin.delete()
        update.message.reply_text("Successfully removed admin")
    else:
        update.message.reply_text("This user is not an admin")


def cancel(bot, update):
    update.message.reply_text("Current operation canceled", reply_markup=ReplyKeyboardHide())
    return ConversationHandler.END


def search(bot, update, user_data):
    user_data['search_time'] = datetime.now()

    update.message.reply_text("Enter search query:", reply_markup=ForceReply(selective=True))

    return WAIT


@db_session
def search_2(bot, update, user_data):
    issued = user_data['search_time']
    if (datetime.now() - issued).seconds > 30:
        update.message.reply_text("Please send your /search query within 30 seconds.")

    else:
        text = update.message.text.replace('%', '')

        believers = select(
            s for s in Believer if
            text in s.phone_nr or
            text in s.account_nr or
            text in s.bank_name or
            text in s.remark
        ).order_by(
            desc(Believer.created)
        )[0:1]

        if believers:
            believer = believers[0]
            reporter = get_reporter(update.message.from_user)

            kb = search_keyboard(offset=0,
                                 show_download=True,
                                 disabled_attachments=[],
                                 confirmed=reporter in believer.reported_by
                                 if reporter
                                 else False,
                                 query=text)

            update.message.reply_text(str(believer),
                                      reply_markup=InlineKeyboardMarkup(kb),
                                      parse_mode=ParseMode.HTML)

        else:
            update.message.reply_text("No search results")

        track(update, 'search')

    return ConversationHandler.END


@db_session
def callback_query(bot, update):
    cb = update.callback_query
    chat_id = cb.message.chat_id

    data = update.callback_query.data

    logger.info(data)

    data = data.split('%')

    action = ''
    offset = 0
    disabled_attachments = set()
    query = ''
    confirmed = False
    show_download = True

    for elem in data:
        name, *args = elem.split('=')

        if name == 'act':
            action = args[0]
        elif name == 'off':
            offset = int(args[0])
        elif name == 'noatt':
            disabled_attachments = set(int(arg) for arg in args if arg != '')
        elif name == 'qry':
            query = '='.join(args)
        elif name == 'cnf':
            confirmed = bool(int(args[0]))
        elif name == 'dl':
            show_download = bool(int(args[0]))

    reporter = get_reporter(cb.from_user)

    if action == 'old':
        new_offset = offset + 1
    elif action == 'new':
        new_offset = offset - 1
    else:
        new_offset = offset

    try:
        believers = select(
            s for s in Believer if
            query in s.phone_nr or
            query in s.account_nr or
            query in s.bank_name or
            query in s.remark
        ).order_by(
            desc(Believer.created)
        )[new_offset:new_offset + 1]

    except TypeError:
        believers = None

    else:
        offset = new_offset

    reply = None

    if action in ('old', 'new'):
        if believers:
            believer = believers[0]
            reply = str(believer)

            if not believer.attached_file:
                disabled_attachments.add(offset)

            confirmed = reporter in believer.reported_by if reporter else False

        else:
            update.callback_query.answer("No more results")
            return

    elif action == 'confirm':
        if not believers:
            update.callback_query.answer("Not found, please search again")
            return

        believer = believers[0]
        if not confirmed:
            if not reporter:
                reporter = Reporter(id=cb.from_user.id,
                                    first_name=cb.from_user.first_name,
                                    last_name=cb.from_user.last_name,
                                    username=cb.from_user.username)
                track(update, 'new_reporter')

            believer.reported_by.add(reporter)
            update.callback_query.answer("You confirmed this report.")
        else:
            believer.reported_by.remove(reporter)
            update.callback_query.answer("You removed your confirmation.")

        confirmed = not confirmed
        reply = str(believer)

    elif action == 'att':
        if not believers:
            update.callback_query.answer("Not found, please search again")
            return

        kind, _, file_id = believers[0].attached_file.partition(':')

        if kind == 'photo':
            bot.sendPhoto(chat_id, photo=file_id,
                          reply_to_message_id=cb.message.message_id)
        elif kind == 'document':
            bot.sendDocument(chat_id, document=file_id,
                             reply_to_message_id=cb.message.message_id)

        disabled_attachments.add(offset)

    elif action == 'dl':
        bot.sendChatAction(chat_id, action=ChatAction.UPLOAD_DOCUMENT)

        with db_session:
            believers = select(s for s in Believer if
                              query in s.phone_nr or
                              query in s.account_nr or
                              query in s.bank_name or
                              query in s.remark).limit(100)

            content = "\r\n\r\n".join(str(s) for s in believers)

        file = BytesIO(content.encode())
        show_download = False

        bot.sendDocument(chat_id, document=BufferedReader(file),
                         filename='search.txt',
                         reply_to_message_id=update.callback_query.message.message_id)

    kb = search_keyboard(offset=offset, show_download=show_download,
                         disabled_attachments=disabled_attachments, confirmed=confirmed,
                         query=query)

    reply_markup = InlineKeyboardMarkup(kb)

    if reply:
        bot.editMessageText(chat_id=chat_id, message_id=cb.message.message_id, text=reply,
                            reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    else:
        bot.editMessageReplyMarkup(chat_id=chat_id,
                                   message_id=update.callback_query.message.message_id,
                                   reply_markup=reply_markup)


def search_keyboard(offset, show_download, disabled_attachments, confirmed, query):
    data = list()

    data.append('dl=' + str(int(show_download)))

    data.append('noatt=' + '='.join(str(da) for da in disabled_attachments))

    data.append('cnf=' + str(int(confirmed)))

    data.append('off=' + str(int(offset)))

    data.append('qry=' + query)

    data = '%'.join(data)

    kb = [[
        InlineKeyboardButton(
            text='<< ' + Emoji.BLACK_RIGHT_POINTING_TRIANGLE,
            callback_data='act=old%' + data
        ),
        InlineKeyboardButton(
            text=(Emoji.THUMBS_UP_SIGN) if not confirmed else
            ('Unliked'),
            callback_data='act=confirm%' + data
        ),
        InlineKeyboardButton(
            text=Emoji.BLACK_LEFT_POINTING_TRIANGLE + ' >>',
            callback_data='act=new%' + data
        ),
    ], list()]

#    if offset not in disabled_attachments:
#        kb[1].append(
#            InlineKeyboardButton(
#                text=Emoji.FLOPPY_DISK + ' Attachment',
#                callback_data='act=att%' + data
#            )
#        )

#    if show_download:
#        kb[1].append(
#            InlineKeyboardButton(
#                text=Emoji.BLACK_DOWN_POINTING_DOUBLE_TRIANGLE + ' Download all',
#                callback_data='act=dl%' + data
#            )
#        )
    return kb


@db_session
def download_db(bot, update):
    global state
    admin = get_admin(update.message.from_user)

    if not admin or not admin.super_admin:
        return

    update.message.chat.send_action(ChatAction.UPLOAD_DOCUMENT)
    update.message.reply_document(open(DB_NAME, 'rb'), filename='trustworthy.sqlite')


# Add all handlers to the dispatcher and run the bot
dp.add_handler(CommandHandler('start', help))
dp.add_handler(CommandHandler('help', help))
dp.add_handler(CallbackQueryHandler(callback_query))
dp.add_handler(CommandHandler('download_database', download_db))

cancel_handler = CommandHandler('cancel', cancel)
select_option_handler = MessageHandler([Filters.text], select_option, pass_user_data=True)
edit_option_dict = {
    PHONE_NR: [MessageHandler([Filters.text], edit_phone_nr, pass_user_data=True)],
    ACCOUNT_NR: [MessageHandler([Filters.text], edit_account_nr, pass_user_data=True)],
    BANK_NAME: [MessageHandler([Filters.text], edit_bank_name, pass_user_data=True)],
    REMARK: [MessageHandler([Filters.text], edit_remark, pass_user_data=True)],
    ATTACHMENT: [MessageHandler([Filters.photo, Filters.document],
                                edit_attachment,
                                pass_user_data=True)],
}

conv_add_admin = ConversationHandler(
    entry_points=[CommandHandler('add_admin', add_admin)],
    states={
        ADD: [MessageHandler([Filters.forwarded], add_admin_2)],
    },
    fallbacks=[cancel_handler]
)

conv_remove_admin = ConversationHandler(
    entry_points=[CommandHandler('remove_admin', remove_admin)],
    states={
        REMOVE: [MessageHandler([Filters.forwarded], remove_admin_2)],
    },
    fallbacks=[cancel_handler]
)

conv_search = ConversationHandler(
    entry_points=[CommandHandler('search', search, pass_user_data=True)],
    states={
        WAIT: [MessageHandler([Filters.text], search_2, pass_user_data=True)],
    },
    fallbacks=[cancel_handler]
)

conv_add_believer = ConversationHandler(
    entry_points=[CommandHandler('new', add_believer)],
    states={
        ADD: [MessageHandler([Filters.forwarded], add_believer_2, pass_user_data=True)],
        EDIT: [select_option_handler],
        **edit_option_dict,
    },
    fallbacks=[cancel_handler]
)

conv_remove_believer = ConversationHandler(
    entry_points=[CommandHandler('delete', remove_believer)],
    states={
        REMOVE: [MessageHandler([Filters.forwarded], remove_believer_2, pass_user_data=True)],
    },
    fallbacks=[cancel_handler]
)

conv_edit = ConversationHandler(
    entry_points=[CommandHandler('edit', edit_believer)],
    states={
        WAIT: [RegexHandler(r'^\d+$', edit_believer_2, pass_user_data=True)],
        EDIT: [select_option_handler],
        **edit_option_dict,
    },
    fallbacks=[cancel_handler]
)

dp.add_handler(conv_add_admin)
dp.add_handler(conv_remove_admin)
dp.add_handler(conv_edit)
dp.add_handler(conv_search)
dp.add_handler(conv_add_believer)
dp.add_handler(conv_remove_believer)

dp.addErrorHandler(error)

start_bot(u)
u.idle()

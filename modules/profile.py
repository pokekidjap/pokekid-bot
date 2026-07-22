import asyncio
import re
from html import escape

from telegram import Update
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
)

from keyboards.profile import (
    profile_back_keyboard,
    profile_data_keyboard,
    profile_delete_confirmation_keyboard,
    profile_form_cancel_keyboard,
    profile_form_review_keyboard,
    profile_keyboard,
)
from services.bot_db import (
    delete_profile,
    get_profile,
    save_profile,
)


(
    PROFILE_NAME,
    PROFILE_EMAIL,
    PROFILE_PHONE,
    PROFILE_ADDRESS,
    PROFILE_POSTAL_CODE,
    PROFILE_CITY,
    PROFILE_PROVINCE,
    PROFILE_REVIEW,
) = range(8)


PRIVACY_TEXT = (
    "🔒 <b>Privacy</b>\n"
    "I dati inseriti saranno utilizzati esclusivamente per la "
    "gestione delle tue richieste di spedizione e conservati "
    "unicamente per agevolare le richieste future. "
    "Potrai richiederne la modifica o la cancellazione "
    "in qualsiasi momento."
)


def clean_text(value: str | None) -> str:
    """
    Pulisce il testo ricevuto dall'utente.
    """
    if value is None:
        return ""

    return str(value).strip()


def format_profile_data(
    profile: dict,
) -> str:
    """
    Prepara i dati del profilo in formato HTML.
    """
    name = escape(
        profile.get("NOME", "")
    )

    email = escape(
        profile.get("EMAIL", "")
    )

    phone = escape(
        profile.get("TELEFONO", "")
    )

    address = escape(
        profile.get("INDIRIZZO", "")
    )

    postal_code = escape(
        profile.get("CAP", "")
    )

    city = escape(
        profile.get("CITTA", "")
    )

    province = escape(
        profile.get("PROVINCIA", "")
    )

    updated_at = escape(
        profile.get(
            "DATA_AGGIORNAMENTO",
            "",
        )
    )

    location_line = " ".join(
        part
        for part in [
            postal_code,
            city,
        ]
        if part
    )

    if province:
        location_line = (
            f"{location_line} ({province})"
            if location_line
            else f"({province})"
        )

    text = (
        "📍 <b>Dati di spedizione salvati</b>\n\n"
        f"👤 <b>{name}</b>\n"
        f"📧 {email}\n"
        f"📞 {phone}\n\n"
        f"🏠 {address}\n"
        f"{location_line}"
    )

    if updated_at:
        text += (
            "\n\n"
            "🕒 Ultimo aggiornamento:\n"
            f"{updated_at}"
        )

    text += (
        "\n\n"
        f"{PRIVACY_TEXT}"
    )

    return text


def format_profile_review(
    data: dict,
) -> str:
    """
    Prepara il riepilogo dei dati prima del salvataggio.
    """
    name = escape(
        data.get("name", "")
    )

    email = escape(
        data.get("email", "")
    )

    phone = escape(
        data.get("phone", "")
    )

    address = escape(
        data.get("address", "")
    )

    postal_code = escape(
        data.get("postal_code", "")
    )

    city = escape(
        data.get("city", "")
    )

    province = escape(
        data.get("province", "")
    )

    return (
        "📋 <b>Controlla i dati inseriti</b>\n\n"
        f"👤 <b>{name}</b>\n"
        f"📧 {email}\n"
        f"📞 {phone}\n\n"
        f"🏠 {address}\n"
        f"📮 {postal_code} {city} ({province})\n\n"
        "Se i dati sono corretti, premi "
        "<b>Salva dati</b>."
    )


async def show_profile(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Mostra la schermata principale del Profilo.
    """
    query = update.callback_query
    user = query.from_user

    try:
        profile = await asyncio.to_thread(
            get_profile,
            user.id,
        )

    except Exception:
        await query.edit_message_text(
            text=(
                "❌ Non è stato possibile leggere il profilo.\n\n"
                "Riprova tra qualche minuto."
            ),
            reply_markup=profile_back_keyboard(),
        )
        return

    if profile:
        text = (
            "👤 <b>Il mio profilo</b>\n\n"
            "✅ Hai già dei dati di spedizione salvati.\n\n"
            "Da questa sezione puoi visualizzarli, "
            "modificarli oppure cancellarli."
        )

    else:
        text = (
            "👤 <b>Il mio profilo</b>\n\n"
            "Non hai ancora inserito i tuoi dati "
            "di spedizione.\n\n"
            "Potrai salvarli per velocizzare le future "
            "richieste di spedizione.\n\n"
            f"{PRIVACY_TEXT}"
        )

    await query.edit_message_text(
        text=text,
        reply_markup=profile_keyboard(
            has_profile=profile is not None,
        ),
        parse_mode="HTML",
    )


async def show_profile_shipping_data(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Visualizza i dati di spedizione salvati.
    """
    query = update.callback_query
    user = query.from_user

    try:
        profile = await asyncio.to_thread(
            get_profile,
            user.id,
        )

    except Exception:
        await query.edit_message_text(
            text=(
                "❌ Non è stato possibile leggere "
                "i dati di spedizione."
            ),
            reply_markup=profile_back_keyboard(),
        )
        return

    if not profile:
        await query.edit_message_text(
            text=(
                "📍 <b>Dati di spedizione</b>\n\n"
                "Non risultano dati salvati."
            ),
            reply_markup=profile_keyboard(
                has_profile=False,
            ),
            parse_mode="HTML",
        )
        return

    await query.edit_message_text(
        text=format_profile_data(
            profile
        ),
        reply_markup=profile_data_keyboard(),
        parse_mode="HTML",
    )


async def ask_profile_delete_confirmation(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Chiede conferma prima di eliminare il profilo.
    """
    query = update.callback_query

    await query.edit_message_text(
        text=(
            "🗑 <b>Eliminazione dati</b>\n\n"
            "Confermi di voler eliminare tutti i dati "
            "di spedizione salvati?\n\n"
            "Questa operazione non può essere annullata."
        ),
        reply_markup=profile_delete_confirmation_keyboard(),
        parse_mode="HTML",
    )


async def remove_profile(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Elimina i dati di spedizione dell'utente.
    """
    query = update.callback_query
    user = query.from_user

    try:
        deleted = await asyncio.to_thread(
            delete_profile,
            user.id,
            user.username,
        )

    except Exception:
        await query.edit_message_text(
            text=(
                "❌ Non è stato possibile eliminare "
                "i dati salvati.\n\n"
                "Riprova tra qualche minuto."
            ),
            reply_markup=profile_back_keyboard(),
        )
        return

    if deleted:
        text = (
            "✅ <b>Dati eliminati</b>\n\n"
            "I tuoi dati di spedizione sono stati "
            "cancellati correttamente."
        )

    else:
        text = (
            "ℹ️ <b>Nessun dato trovato</b>\n\n"
            "Non risultano dati di spedizione salvati."
        )

    await query.edit_message_text(
        text=text,
        reply_markup=profile_keyboard(
            has_profile=False,
        ),
        parse_mode="HTML",
    )


async def show_profile_shipments(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Mostra la sezione delle spedizioni dell'utente.
    """
    query = update.callback_query

    await query.edit_message_text(
        text=(
            "🚚 <b>Le mie spedizioni</b>\n\n"
            "Non risultano ancora richieste di spedizione.\n\n"
            "In questa sezione potrai visualizzare lo stato "
            "delle richieste, il corriere e il tracking."
        ),
        reply_markup=profile_back_keyboard(),
        parse_mode="HTML",
    )


async def start_profile_form(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """
    Avvia l'inserimento o la modifica dei dati.
    """
    query = update.callback_query
    await query.answer()

    context.user_data["profile_form"] = {}

    await query.edit_message_text(
        text=(
            "👤 <b>Inserimento dati di spedizione</b>\n\n"
            "Scrivi il tuo <b>nome e cognome</b>.\n\n"
            "Esempio:\n"
            "<code>Mario Rossi</code>\n\n"
            f"{PRIVACY_TEXT}"
        ),
        reply_markup=profile_form_cancel_keyboard(),
        parse_mode="HTML",
    )

    return PROFILE_NAME


async def receive_profile_name(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """
    Riceve nome e cognome.
    """
    name = clean_text(
        update.message.text
    )

    if len(name) < 3:
        await update.message.reply_text(
            "⚠️ Inserisci un nome e cognome validi."
        )
        return PROFILE_NAME

    context.user_data["profile_form"]["name"] = name

    await update.message.reply_text(
        text=(
            "📧 Inserisci il tuo <b>indirizzo email</b>.\n\n"
            "Esempio:\n"
            "<code>mario@email.it</code>"
        ),
        reply_markup=profile_form_cancel_keyboard(),
        parse_mode="HTML",
    )

    return PROFILE_EMAIL


async def receive_profile_email(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """
    Riceve e controlla l'indirizzo email.
    """
    email = clean_text(
        update.message.text
    ).lower()

    email_pattern = (
        r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
    )

    if not re.match(
        email_pattern,
        email,
    ):
        await update.message.reply_text(
            "⚠️ L'indirizzo email non sembra valido.\n\n"
            "Inseriscilo nuovamente."
        )
        return PROFILE_EMAIL

    context.user_data["profile_form"]["email"] = email

    await update.message.reply_text(
        text=(
            "📞 Inserisci il tuo "
            "<b>numero di telefono</b>.\n\n"
            "Esempio:\n"
            "<code>3471234567</code>"
        ),
        reply_markup=profile_form_cancel_keyboard(),
        parse_mode="HTML",
    )

    return PROFILE_PHONE


async def receive_profile_phone(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """
    Riceve il numero di telefono.
    """
    phone = clean_text(
        update.message.text
    )

    phone_pattern = r"^[0-9+\s\-]{7,20}$"

    if not re.match(
        phone_pattern,
        phone,
    ):
        await update.message.reply_text(
            "⚠️ Il numero di telefono non sembra valido.\n\n"
            "Inseriscilo nuovamente."
        )
        return PROFILE_PHONE

    context.user_data["profile_form"]["phone"] = phone

    await update.message.reply_text(
        text=(
            "🏠 Inserisci il tuo "
            "<b>indirizzo completo</b>.\n\n"
            "Scrivi via, numero civico ed eventuale interno.\n\n"
            "Esempio:\n"
            "<code>Via Roma 25, interno 3</code>"
        ),
        reply_markup=profile_form_cancel_keyboard(),
        parse_mode="HTML",
    )

    return PROFILE_ADDRESS


async def receive_profile_address(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """
    Riceve l'indirizzo.
    """
    address = clean_text(
        update.message.text
    )

    if len(address) < 5:
        await update.message.reply_text(
            "⚠️ Inserisci un indirizzo completo."
        )
        return PROFILE_ADDRESS

    context.user_data["profile_form"]["address"] = address

    await update.message.reply_text(
        text=(
            "📮 Inserisci il <b>CAP</b>.\n\n"
            "Esempio:\n"
            "<code>16121</code>"
        ),
        reply_markup=profile_form_cancel_keyboard(),
        parse_mode="HTML",
    )

    return PROFILE_POSTAL_CODE


async def receive_profile_postal_code(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """
    Riceve il CAP.
    """
    postal_code = clean_text(
        update.message.text
    )

    if not re.fullmatch(
        r"\d{5}",
        postal_code,
    ):
        await update.message.reply_text(
            "⚠️ Il CAP deve essere composto da 5 numeri."
        )
        return PROFILE_POSTAL_CODE

    context.user_data["profile_form"][
        "postal_code"
    ] = postal_code

    await update.message.reply_text(
        text=(
            "🏙 Inserisci la <b>città</b>.\n\n"
            "Esempio:\n"
            "<code>Genova</code>"
        ),
        reply_markup=profile_form_cancel_keyboard(),
        parse_mode="HTML",
    )

    return PROFILE_CITY


async def receive_profile_city(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """
    Riceve la città.
    """
    city = clean_text(
        update.message.text
    )

    if len(city) < 2:
        await update.message.reply_text(
            "⚠️ Inserisci una città valida."
        )
        return PROFILE_CITY

    context.user_data["profile_form"]["city"] = city

    await update.message.reply_text(
        text=(
            "📍 Inserisci la sigla della "
            "<b>provincia</b>.\n\n"
            "Esempio:\n"
            "<code>GE</code>"
        ),
        reply_markup=profile_form_cancel_keyboard(),
        parse_mode="HTML",
    )

    return PROFILE_PROVINCE


async def receive_profile_province(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """
    Riceve la provincia e mostra il riepilogo.
    """
    province = clean_text(
        update.message.text
    ).upper()

    if not re.fullmatch(
        r"[A-Z]{2}",
        province,
    ):
        await update.message.reply_text(
            "⚠️ Inserisci la sigla della provincia "
            "con due lettere.\n\n"
            "Esempio: GE"
        )
        return PROFILE_PROVINCE

    context.user_data["profile_form"][
        "province"
    ] = province

    data = context.user_data[
        "profile_form"
    ]

    await update.message.reply_text(
        text=format_profile_review(
            data
        ),
        reply_markup=profile_form_review_keyboard(),
        parse_mode="HTML",
    )

    return PROFILE_REVIEW


async def save_profile_form(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """
    Salva definitivamente i dati nel BOT DB.
    """
    query = update.callback_query
    await query.answer()

    user = query.from_user

    data = context.user_data.get(
        "profile_form",
        {},
    )

    required_fields = {
        "name",
        "email",
        "phone",
        "address",
        "postal_code",
        "city",
        "province",
    }

    if not required_fields.issubset(
        data.keys()
    ):
        await query.edit_message_text(
            text=(
                "❌ I dati inseriti risultano incompleti.\n\n"
                "Ricomincia la procedura dal Profilo."
            ),
            reply_markup=profile_back_keyboard(),
        )

        context.user_data.pop(
            "profile_form",
            None,
        )

        return ConversationHandler.END

    try:
        await asyncio.to_thread(
            save_profile,
            telegram_id=user.id,
            username=user.username,
            name=data["name"],
            email=data["email"],
            phone=data["phone"],
            address=data["address"],
            postal_code=data["postal_code"],
            city=data["city"],
            province=data["province"],
        )

    except Exception:
        await query.edit_message_text(
            text=(
                "❌ Non è stato possibile salvare i dati.\n\n"
                "Riprova tra qualche minuto."
            ),
            reply_markup=profile_back_keyboard(),
        )

        return ConversationHandler.END

    context.user_data.pop(
        "profile_form",
        None,
    )

    await query.edit_message_text(
        text=(
            "✅ <b>Dati salvati correttamente</b>\n\n"
            "I tuoi dati di spedizione sono stati registrati.\n\n"
            "Potrai modificarli o cancellarli in qualsiasi momento."
        ),
        reply_markup=profile_keyboard(
            has_profile=True,
        ),
        parse_mode="HTML",
    )

    return ConversationHandler.END


async def restart_profile_form(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """
    Ricomincia il modulo dal nome e cognome.
    """
    query = update.callback_query
    await query.answer()

    context.user_data["profile_form"] = {}

    await query.edit_message_text(
        text=(
            "✏️ <b>Ricomincia inserimento</b>\n\n"
            "Scrivi nuovamente il tuo "
            "<b>nome e cognome</b>."
        ),
        reply_markup=profile_form_cancel_keyboard(),
        parse_mode="HTML",
    )

    return PROFILE_NAME


async def cancel_profile_form(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """
    Annulla il modulo e cancella i dati temporanei.
    """
    context.user_data.pop(
        "profile_form",
        None,
    )

    if update.callback_query:
        query = update.callback_query
        await query.answer()

        await query.edit_message_text(
            text=(
                "❌ <b>Inserimento annullato</b>\n\n"
                "Nessun dato è stato salvato."
            ),
            reply_markup=profile_back_keyboard(),
            parse_mode="HTML",
        )

    elif update.message:
        await update.message.reply_text(
            text=(
                "❌ Inserimento annullato.\n\n"
                "Nessun dato è stato salvato."
            )
        )

    return ConversationHandler.END
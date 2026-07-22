import asyncio
from html import escape

from telegram import Update
from telegram.ext import ContextTypes

from keyboards.profile import (
    profile_back_keyboard,
    profile_data_keyboard,
    profile_delete_confirmation_keyboard,
    profile_keyboard,
)
from services.bot_db import (
    delete_profile,
    get_profile,
)


PRIVACY_TEXT = (
    "🔒 <b>Privacy</b>\n"
    "I dati inseriti saranno utilizzati esclusivamente per la "
    "gestione delle tue richieste di spedizione e conservati "
    "unicamente per agevolare le richieste future. "
    "Potrai richiederne la modifica o la cancellazione "
    "in qualsiasi momento."
)


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
            f"🕒 Ultimo aggiornamento:\n"
            f"{updated_at}"
        )

    text += (
        "\n\n"
        f"{PRIVACY_TEXT}"
    )

    return text


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

    Verrà collegata al foglio SPEDIZIONI
    in uno step successivo.
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


async def show_profile_data_form_placeholder(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Schermata temporanea in attesa del modulo
    di inserimento guidato dei dati.
    """
    query = update.callback_query

    await query.edit_message_text(
        text=(
            "✏️ <b>Dati di spedizione</b>\n\n"
            "Nel prossimo passaggio attiveremo "
            "l'inserimento guidato di:\n\n"
            "• Nome e cognome\n"
            "• Email\n"
            "• Numero di telefono\n"
            "• Indirizzo\n"
            "• CAP\n"
            "• Città\n"
            "• Provincia\n\n"
            f"{PRIVACY_TEXT}"
        ),
        reply_markup=profile_back_keyboard(),
        parse_mode="HTML",
    )
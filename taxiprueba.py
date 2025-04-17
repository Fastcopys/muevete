import logging
import re
import urllib.parse
import time
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardRemove,
    KeyboardButton,
    ReplyKeyboardMarkup
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    ConversationHandler,
    CallbackQueryHandler,
    filters,
    PicklePersistence
)

# Configuración básica
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Estados de la conversación
HORA, RECOGIDA, DESTINO, PASAJEROS, EQUIPAJE, MASCOTA, TELEFONO, CONFIRMAR = range(8)
ADMINS = [7302458830, 976535162, 7116847683]
BOT_TOKEN = "8186803657:AAFwTXKaD4MSNJBAM8z_Nh3r26H5Bebz7I0"

# Almacenamiento de datos
solicitudes = {}
confirmaciones_admin = {}

async def group_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Maneja los mensajes en grupos"""
    if update.message.from_user and update.message.from_user.is_bot:
        return
    
    keyboard = [
        [
            InlineKeyboardButton(
                "🚖 Solicitar taxi",
                url=f"https://t.me/{context.bot.username}?start=solicitar_taxi"
            ),
            InlineKeyboardButton("📋 Solicitar información", callback_data="info")
        ]
    ]
    await update.message.reply_text(
        "¡Hola! ¿En qué puedo ayudarte?",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Inicia la conversación en privado"""
    if update.message.chat.type != "private":
        return ConversationHandler.END
    
    if context.args and context.args[0] == "solicitar_taxi":
        await update.message.reply_text("🕒 ¿A qué hora necesitas el viaje?")
        return HORA
    else:
        await update.message.reply_text("Usa el botón en el grupo para solicitar un taxi.")
        return ConversationHandler.END

async def hora(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['hora'] = update.message.text
    await update.message.reply_text("📍 ¿Dirección de recogida?")
    return RECOGIDA

async def recogida(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['recogida'] = update.message.text
    await update.message.reply_text("🎯 ¿Dirección de destino?")
    return DESTINO

async def destino(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['destino'] = update.message.text
    await update.message.reply_text("👥 ¿Cantidad de pasajeros?")
    return PASAJEROS

async def pasajeros(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['pasajeros'] = update.message.text
    await update.message.reply_text("🧳 ¿Llevarán equipaje? (Sí/No)")
    return EQUIPAJE

async def equipaje(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['equipaje'] = update.message.text
    await update.message.reply_text("🐶 ¿Llevarán mascotas? (Sí/No)")
    return MASCOTA

async def mascota(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['mascota'] = update.message.text
    await update.message.reply_text(
        "📱 Por favor, comparte tu número de teléfono con código de país (Ej: Cuba +53):",
        reply_markup=ReplyKeyboardMarkup(
            [[KeyboardButton("Compartir teléfono", request_contact=True)]],
            one_time_keyboard=True,
            resize_keyboard=True))
    return TELEFONO

async def telefono(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Procesa el número de teléfono"""
    if update.message.contact:
        numero = update.message.contact.phone_number
    else:
        numero = update.message.text
    
    if not re.match(r'^\+\d{8,15}$', numero):
        await update.message.reply_text("⚠️ Formato incorrecto. Ejemplo válido: +5351234567")
        return TELEFONO
    
    context.user_data['telefono'] = numero
    return await mostrar_resumen(update, context)

async def mostrar_resumen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Muestra el resumen de la solicitud"""
    user_data = context.user_data
    resumen = (
        "📝 Resumen de tu solicitud:\n\n"
        f"🕒 Hora: {user_data['hora']}\n"
        f"📍 Recogida: {user_data['recogida']}\n"
        f"🎯 Destino: {user_data['destino']}\n"
        f"👥 Pasajeros: {user_data['pasajeros']}\n"
        f"🧳 Equipaje: {user_data['equipaje']}\n"
        f"🐶 Mascotas: {user_data['mascota']}\n"
        f"📱 Teléfono: {user_data.get('telefono', 'No proporcionado')}")
    
    keyboard = [
        [InlineKeyboardButton(f"Editar {campo}", callback_data=f"editar_{campo}")] 
        for campo in user_data.keys()
    ]
    keyboard.append([InlineKeyboardButton("✅ Confirmar", callback_data="confirmar")])
    
    await update.message.reply_text(resumen, reply_markup=InlineKeyboardMarkup(keyboard))
    return CONFIRMAR

async def editar_campo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Permite editar campos del formulario"""
    query = update.callback_query
    await query.answer()
    campo = query.data.split('_')[1]
    context.user_data['editar'] = campo
    await query.edit_message_text(text=f"Introduce el nuevo valor para {campo}:")
    return globals()[campo.upper()]

def crear_whatsapp_link(user_data: dict) -> str:
    """Genera enlace de WhatsApp con formato internacional"""
    telefono = user_data.get('telefono', '')
    if not telefono or not re.match(r'^\+\d{8,15}$', telefono):
        return ""
    
    mensaje = (
        "🚖 *Detalles del Viaje*:\n"
        f"• Hora: {user_data['hora']}\n"
        f"• Recogida: {user_data['recogida']}\n"
        f"• Destino: {user_data['destino']}\n"
        f"• Pasajeros: {user_data['pasajeros']}\n"
        f"• Equipaje: {user_data['equipaje']}\n"
        f"• Mascotas: {user_data['mascota']}")
    
    return f"https://wa.me/{telefono.lstrip('+')}?text={urllib.parse.quote(mensaje)}"

def formatear_solicitud_admin(user_data: dict, user: dict, solicitud_id: str) -> tuple:
    """Crea el mensaje para administradores"""
    texto = (
        "🚨 *Nueva Solicitud de Taxi* 🚨\n\n"
        f"🕒 Hora: {user_data['hora']}\n"
        f"📍 Recogida: {user_data['recogida']}\n"
        f"🎯 Destino: {user_data['destino']}\n"
        f"👥 Pasajeros: {user_data['pasajeros']}\n"
        f"🧳 Equipaje: {user_data['equipaje']}\n"
        f"🐶 Mascotas: {user_data['mascota']}\n"
        f"📱 Teléfono: {user_data.get('telefono', 'No proporcionado')}\n\n"
        f"👤 Usuario: {user.full_name}\n"
        f"🆔 ID: {user.id}")

    keyboard = [
        [InlineKeyboardButton("✅ Confirmar Carrera", callback_data=f"confirmar_{solicitud_id}")],
        [
            InlineKeyboardButton("👤 Chat Directo", url=f"tg://user?id={user.id}"),
            InlineKeyboardButton("📱 WhatsApp", url=crear_whatsapp_link(user_data))
        ] if crear_whatsapp_link(user_data) else [
            InlineKeyboardButton("👤 Chat Directo", url=f"tg://user?id={user.id}")
        ]
    ]
    
    return texto, InlineKeyboardMarkup(keyboard)

async def confirmar_solicitud(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Envía la solicitud a los administradores"""
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    user_data = context.user_data.copy()
    solicitud_id = f"{user.id}-{int(time.time())}"
    
    texto_admin, markup_admin = formatear_solicitud_admin(user_data, user, solicitud_id)
    
    solicitudes[solicitud_id] = {
        'user_data': user_data,
        'user': user,
        'mensaje_original': texto_admin,
        'markup': markup_admin
    }
    
    # Notificar a todos los admins
    for admin in ADMINS:
        try:
            mensaje = await context.bot.send_message(
                chat_id=admin,
                text=texto_admin,
                reply_markup=markup_admin,
                parse_mode="Markdown")
            confirmaciones_admin.setdefault(solicitud_id, []).append(mensaje.message_id)
        except Exception as e:
            logger.error(f"Error notificando al admin {admin}: {e}")
    
    await query.edit_message_text("✅ Solicitud enviada a administradores")
    return ConversationHandler.END

async def confirmar_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Procesa la confirmación de administradores"""
    query = update.callback_query
    await query.answer()
    solicitud_id = query.data.split('_')[1]
    admin = update.effective_user
    
    if solicitud_id not in solicitudes:
        await query.edit_message_text("⚠️ Solicitud no encontrada")
        return
    
    solicitud = solicitudes[solicitud_id]
    nuevo_texto = f"{solicitud['mensaje_original']}\n\n✅ Confirmado por: {admin.full_name}"
    
    # Actualizar todos los mensajes
    for admin_id in ADMINS:
        for msg_id in confirmaciones_admin.get(solicitud_id, []):
            try:
                # Mantener botones originales solo para el admin que confirmó
                if admin_id == admin.id:
                    nuevo_markup = solicitud['markup']
                else:
                    # Eliminar botón de confirmar pero mantener contactos
                    nuevo_markup = InlineKeyboardMarkup([
                        row for row in solicitud['markup'].inline_keyboard 
                        if not any(btn.callback_data and 'confirmar' in btn.callback_data for btn in row)
                    ])
                
                await context.bot.edit_message_text(
                    chat_id=admin_id,
                    message_id=msg_id,
                    text=nuevo_texto,
                    parse_mode="Markdown",
                    reply_markup=nuevo_markup)
            except Exception as e:
                logger.error(f"Error actualizando mensaje: {e}")
    
    await query.edit_message_text(text=nuevo_texto, parse_mode="Markdown", reply_markup=solicitud['markup'])

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancela la operación"""
    await update.message.reply_text('Operación cancelada', reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

def main() -> None:
    """Configuración principal del bot"""
    persistence = PicklePersistence(filepath='taxi_bot_data.pickle')
    application = ApplicationBuilder()\
        .token(BOT_TOKEN)\
        .persistence(persistence)\
        .build()

    # Handler para grupos
    application.add_handler(MessageHandler(
        filters.ChatType.GROUPS & ~filters.UpdateType.EDITED,
        group_message_handler))

    # Conversación principal
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            HORA: [MessageHandler(filters.TEXT & ~filters.COMMAND, hora)],
            RECOGIDA: [MessageHandler(filters.TEXT & ~filters.COMMAND, recogida)],
            DESTINO: [MessageHandler(filters.TEXT & ~filters.COMMAND, destino)],
            PASAJEROS: [MessageHandler(filters.TEXT & ~filters.COMMAND, pasajeros)],
            EQUIPAJE: [MessageHandler(filters.TEXT & ~filters.COMMAND, equipaje)],
            MASCOTA: [MessageHandler(filters.TEXT & ~filters.COMMAND, mascota)],
            TELEFONO: [MessageHandler(filters.CONTACT | filters.TEXT, telefono)],
            CONFIRMAR: [
                CallbackQueryHandler(editar_campo, pattern='^editar_'),
                CallbackQueryHandler(confirmar_solicitud, pattern='^confirmar$')
            ]
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        persistent=True,
        name="taxi_conversation")
    
    application.add_handler(conv_handler)
    application.add_handler(CallbackQueryHandler(confirmar_admin, pattern=r'^confirmar_'))
    application.run_polling()

if __name__ == '__main__':
    while True:
        try:
            main()
        except Exception as e:
            logger.error(f"Error crítico: {e}")
            time.sleep(10)
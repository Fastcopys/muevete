import logging
import re
from urllib.parse import quote
import time
import sqlite3
from datetime import datetime
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardRemove
)
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    ConversationHandler,
    CallbackQueryHandler,
    filters,
    PicklePersistence,
    JobQueue
)
from telegram.constants import ChatType, ParseMode 

# Configuración básica
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Estados de la conversación
(
    HORA,
    DIR_RECOGIDA,
    DESTINO,
    CANT_PERSONAS,
    EQUIPAJE,
    MASCOTA,
    TELEFONO,
    TIPO_VIAJE,
    TIEMPO_ESPERA,
    CONFIRMACION,
) = range(10)

ADMINS = [7302458830, 976535162, 7116847683]
BOT_TOKEN = "8186803657:AAFwTXKaD4MSNJBAM8z_Nh3r26H5Bebz7I0"
DB_NAME = "taxi_bot.db"

mensajes_a_mantener = {}

# ===== Funciones de Base de Datos =====
def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS taxi_requests
                 (id TEXT PRIMARY KEY,
                  user_id INTEGER,
                  user_name TEXT,
                  data TEXT,
                  status TEXT,
                  assigned_admin INTEGER,
                  created_at TIMESTAMP)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS info_requests
                 (id TEXT PRIMARY KEY,
                  user_id INTEGER,
                  user_name TEXT,
                  status TEXT,
                  assigned_admin INTEGER,
                  created_at TIMESTAMP)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS info_messages
                 (request_id TEXT,
                  admin_id INTEGER,
                  message_id INTEGER,
                  PRIMARY KEY (request_id, admin_id))''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS taxi_messages
                 (request_id TEXT,
                  admin_id INTEGER,
                  message_id INTEGER,
                  PRIMARY KEY (request_id, admin_id))''')
    
    conn.commit()
    conn.close()

init_db()

def save_taxi_request(request_id, user, request_data):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''INSERT INTO taxi_requests 
                 VALUES (?, ?, ?, ?, ?, ?, ?)''',
              (request_id, user.id, user.full_name,
               str(request_data), 'pendiente', None, datetime.now()))
    conn.commit()
    conn.close()

def save_info_request(request_id, user):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''INSERT INTO info_requests 
                 VALUES (?, ?, ?, ?, ?, ?)''',
              (request_id, user.id, user.full_name,
               'pendiente', None, datetime.now()))
    conn.commit()
    conn.close()

def update_request_status(request_id, new_status, admin_id=None, request_type='taxi'):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    table = 'taxi_requests' if request_type == 'taxi' else 'info_requests'
    c.execute(f'''UPDATE {table} 
                 SET status = ?, assigned_admin = ?
                 WHERE id = ?''', (new_status, admin_id, request_id))
    conn.commit()
    conn.close()

def get_pending_requests(request_type='taxi'):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    table = 'taxi_requests' if request_type == 'taxi' else 'info_requests'
    c.execute(f'''SELECT * FROM {table} WHERE status = 'pendiente' 
                 ORDER BY created_at DESC''')
    results = c.fetchall()
    conn.close()
    return results

# ===== Sistema de Limpieza de Mensajes =====
async def delete_old_messages(context: ContextTypes.DEFAULT_TYPE):
    current_time = time.time()
    for chat_id in list(mensajes_a_mantener.keys()):
        try:
            data = mensajes_a_mantener[chat_id]
            
            if (current_time - data['bot_message_time']) > 180:
                await context.bot.delete_message(chat_id, data['bot_message_id'])
                del mensajes_a_mantener[chat_id]
                logger.info(f"Mensaje del bot eliminado en chat {chat_id}")
                continue
                
            messages = await context.bot.get_chat(chat_id).get_messages(limit=20)
            for message in messages:
                try:
                    if message.message_id != data['bot_message_id']:
                        if (current_time - message.date.timestamp()) > 60:
                            await message.delete()
                            logger.info(f"Mensaje de usuario eliminado: {message.message_id}")
                except Exception as e:
                    logger.error(f"Error eliminando mensaje {message.message_id}: {e}")
                    
        except Exception as e:
            logger.error(f"Error en limpieza del chat {chat_id}: {e}")
            del mensajes_a_mantener[chat_id]

async def group_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        await update.message.delete()
        logger.info(f"Mensaje de usuario eliminado: {update.message.message_id}")
    except Exception as e:
        logger.error(f"No se pudo eliminar mensaje del usuario: {e}")
        return

    try:
        mensaje = await context.bot.send_message(
            chat_id=update.message.chat_id,
            text="¡Hola! ¿En qué puedo ayudarte?",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("🚖 Solicitar taxi", 
                                       url=f"https://t.me/{context.bot.username}?start=solicitar_taxi"),
                    InlineKeyboardButton("📋 Solicitar información", callback_data="info")
                ]
            ])
        )
        
        mensajes_a_mantener[update.message.chat_id] = {
            'bot_message_id': mensaje.message_id,
            'bot_message_time': time.time()
        }
        
    except Exception as e:
        logger.error(f"Error enviando mensaje de bot: {e}")

# ===== Handlers de Taxi =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    
    if chat.type in [ChatType.GROUP, ChatType.SUPERGROUP]:
        if user.id not in ADMINS:
            await group_message_handler(update, context)
        return
    
    context.user_data.clear()
    
    if context.args and context.args[0] == "solicitar_taxi":
        await update.message.reply_text("🕒 Ingresa la hora del viaje (Formato HH:MM):")
        return HORA
    
    keyboard = [
        [InlineKeyboardButton("🚖 Solicitar Taxi", callback_data="solicitar_taxi")],
        [InlineKeyboardButton("ℹ️ Información", callback_data="info")]
    ]
    
    # Añadir paréntesis de cierre aquí
    await update.message.reply_text(
        f"¡Hola {user.first_name}! ¿En qué puedo ayudarte?",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return ConversationHandler.END

async def get_hora(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["hora"] = update.message.text
    
    if context.user_data.get('modo_edicion'):
        del context.user_data['modo_edicion']
        return await mostrar_resumen(update, context)
    
    await update.message.reply_text("📍 Dirección de recogida:")
    return DIR_RECOGIDA

async def get_direccion(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["direccion"] = update.message.text
    
    if context.user_data.get('modo_edicion'):
        del context.user_data['modo_edicion']
        return await mostrar_resumen(update, context)
    
    await update.message.reply_text("🏁 Dirección de destino:")
    return DESTINO

async def get_destino(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["destino"] = update.message.text
    
    if context.user_data.get('modo_edicion'):
        del context.user_data['modo_edicion']
        return await mostrar_resumen(update, context)
    
    await update.message.reply_text("👥 Cantidad de personas:")
    return CANT_PERSONAS

async def get_personas(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["personas"] = update.message.text
    
    if context.user_data.get('modo_edicion'):
        del context.user_data['modo_edicion']
        return await mostrar_resumen(update, context)
    
    keyboard = [
        [InlineKeyboardButton("Sí", callback_data="equipaje_si"),
         InlineKeyboardButton("No", callback_data="equipaje_no")]
    ]
    await update.message.reply_text("¿Lleva equipaje?", reply_markup=InlineKeyboardMarkup(keyboard))
    return EQUIPAJE

async def get_equipaje(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["equipaje"] = "Sí" if query.data == "equipaje_si" else "No"
    
    if context.user_data.get('modo_edicion'):
        del context.user_data['modo_edicion']
        return await mostrar_resumen(update, context)
    
    keyboard = [
        [InlineKeyboardButton("Sí", callback_data="mascota_si"),
         InlineKeyboardButton("No", callback_data="mascota_no")]
    ]
    await query.edit_message_text("¿Lleva mascotas?", reply_markup=InlineKeyboardMarkup(keyboard))
    return MASCOTA

async def get_mascota(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["mascota"] = "Sí" if query.data == "mascota_si" else "No"
    
    if context.user_data.get('modo_edicion'):
        del context.user_data['modo_edicion']
        return await mostrar_resumen(update, context)
    
    await query.edit_message_text("📱 Teléfono (Ejemplo: +53123456):")
    return TELEFONO

async def get_telefono(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["telefono"] = update.message.text
    
    if context.user_data.get('modo_edicion'):
        del context.user_data['modo_edicion']
        return await mostrar_resumen(update, context)
    
    keyboard = [
        [InlineKeyboardButton("Solo Ida", callback_data="tipo_ida"),
         InlineKeyboardButton("Ida y Vuelta", callback_data="tipo_ida_vuelta")]
    ]
    await update.message.reply_text("Tipo de viaje:", reply_markup=InlineKeyboardMarkup(keyboard))
    return TIPO_VIAJE

async def get_tipo_viaje(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["tipo_viaje"] = query.data
    
    if context.user_data.get('modo_edicion'):
        del context.user_data['modo_edicion']
        return await mostrar_resumen(update, context)
    
    if "vuelta" in query.data:
        await query.edit_message_text("⏱ Tiempo de espera (minutos):")
        return TIEMPO_ESPERA
    return await mostrar_resumen(update, context)

async def get_tiempo_espera(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["tiempo_espera"] = update.message.text
    
    if context.user_data.get('modo_edicion'):
        del context.user_data['modo_edicion']
        return await mostrar_resumen(update, context)
    
    return await mostrar_resumen(update, context)

async def mostrar_resumen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    data = context.user_data
    resumen = (
        "🚖 *Resumen del Viaje*\n\n"
        f"🕒 Hora: {data['hora']}\n"
        f"📍 Recogida: {data['direccion']}\n"
        f"🏁 Destino: {data['destino']}\n"
        f"👥 Personas: {data['personas']}\n"
        f"🧳 Equipaje: {data['equipaje']}\n"
        f"🐾 Mascotas: {data['mascota']}\n"
        f"📱 Teléfono: {data['telefono']}\n"
        f"🔁 Tipo Viaje: {'Ida y Vuelta' if data['tipo_viaje'] == 'tipo_ida_vuelta' else 'Solo Ida'}\n"
        f"⏱ Espera: {data.get('tiempo_espera', 'N/A')}"
    )
    
    keyboard = [
        [InlineKeyboardButton("✅ Confirmar", callback_data="confirmar")],
        [InlineKeyboardButton("✏️ Editar", callback_data="editar_menu")] 
    ]
    
    await update.effective_message.reply_text(
        resumen, 
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return CONFIRMACION

async def mostrar_menu_edicion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    keyboard = [
        [InlineKeyboardButton("Hora 🕒", callback_data="editar_hora")],
        [InlineKeyboardButton("Dirección de Recogida 📍", callback_data="editar_direccion")],
        [InlineKeyboardButton("Destino 🏁", callback_data="editar_destino")],
        [InlineKeyboardButton("Personas 👥", callback_data="editar_personas")],
        [InlineKeyboardButton("Equipaje 🧳", callback_data="editar_equipaje")],
        [InlineKeyboardButton("Mascotas 🐾", callback_data="editar_mascota")],
    ]
    
    await query.edit_message_text(
        "✏️ Selecciona el campo a editar:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return CONFIRMACION

async def editar_campo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    
    campo = query.data.split('_')[1]
    context.user_data['modo_edicion'] = True
    
    estados = {
        'hora': HORA,
        'direccion': DIR_RECOGIDA,
        'destino': DESTINO,
        'personas': CANT_PERSONAS,
        'equipaje': EQUIPAJE,
        'mascota': MASCOTA,
        'tipo_viaje': TIPO_VIAJE
    }
    
    if campo in ['mascota', 'equipaje', 'tipo_viaje']:
        if campo == 'mascota':
            keyboard = [
                [InlineKeyboardButton("Sí", callback_data="mascota_si"),
                InlineKeyboardButton("No", callback_data="mascota_no")]
            ]
            mensaje = "¿Lleva mascotas?"
            
        elif campo == 'equipaje':
            keyboard = [
                [InlineKeyboardButton("Sí", callback_data="equipaje_si"),
                InlineKeyboardButton("No", callback_data="equipaje_no")]
            ]
            mensaje = "¿Lleva equipaje?"
            
        elif campo == 'tipo_viaje':
            keyboard = [
                [InlineKeyboardButton("Solo Ida", callback_data="tipo_ida"),
                InlineKeyboardButton("Ida y Vuelta", callback_data="tipo_ida_vuelta")]
            ]
            mensaje = "✏️ Editar **Tipo de Viaje**:\nSelecciona una opción:"
        
        await query.edit_message_text(
            mensaje,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        return globals()[campo.upper()]
    
    else:
        await query.edit_message_text(
            text=f"✏️ Introduce el nuevo valor para **{campo.capitalize()}**:",
            parse_mode="Markdown"
        )
        return estados[campo]

# En la función confirmar_solicitud
async def confirmar_solicitud(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    data = context.user_data.copy()
    solicitud_id = f"taxi_{user.id}_{int(time.time())}"
    
    # Limpiar y formatear teléfono
    telefono = re.sub(r'[^\d+]', '', data["telefono"])
    if not telefono.startswith('+'):
        telefono = f'+{telefono}'
    
    # Crear mensaje estructurado para WhatsApp
    mensaje_whatsapp = (
        "🚖 *Resumen de tu viaje*\n\n"
        f"• Hora: {data['hora']}\n"
        f"• Recogida: {data['direccion']}\n"
        f"• Destino: {data['destino']}\n"
        f"• Pasajeros: {data['personas']}\n"
        f"• Equipaje: {data['equipaje']}\n"
        f"• Mascotas: {data['mascota']}\n\n"
        "¡Su conductor estará en contacto pronto!"
    )
    
    # Codificar mensaje para URL
    mensaje_codificado = quote(mensaje_whatsapp)
    url_whatsapp = f"https://wa.me/{telefono}?text={mensaje_codificado}"
    
    save_taxi_request(solicitud_id, user, data)
    
    # Mensaje para administradores
    texto_admin = (
        "🚨 *Nueva Solicitud de Taxi* 🚨\n\n"
        + "\n".join([f"• {k.capitalize()}: {v}" for k, v in data.items()])
        + f"\n\n👤 Cliente: {user.mention_markdown()}"
    )
    
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    for admin in ADMINS:
        try:
            mensaje = await context.bot.send_message(
                chat_id=admin,
                text=texto_admin,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("📞 Enviar resumen por WhatsApp", url=url_whatsapp),
                        InlineKeyboardButton("📱 Contactar por Telegram", url=f"tg://user?id={user.id}")
                    ],
                    [InlineKeyboardButton("✅ Asignar conductor", callback_data=f"atender_taxi_{solicitud_id}")]
                ])
            )
            c.execute('''INSERT INTO taxi_messages VALUES (?, ?, ?)''', 
                    (solicitud_id, admin, mensaje.message_id))
        except Exception as e:
            logger.error(f"Error notificando al admin {admin}: {e}")
    
    conn.commit()
    conn.close()
    
    # Confirmación al usuario
    await query.edit_message_text(
        "✅ Solicitud confirmada! Un administrador se contactará contigo",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Nueva solicitud", callback_data="solicitar_taxi")]])
    )
    context.user_data.clear()
    return ConversationHandler.END

async def handle_atender_taxi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    parts = query.data.split('_')
    request_id = '_'.join(parts[2:])
    admin = update.effective_user
    
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    try:
        c.execute('''SELECT status, user_id FROM taxi_requests WHERE id = ?''', (request_id,))
        resultado = c.fetchone()
        
        if not resultado:
            await query.answer("⚠️ Solicitud no encontrada", show_alert=True)
            return
            
        estado_actual, user_id = resultado
        
        if estado_actual != 'pendiente':
            await query.answer("⚠️ Esta solicitud ya está atendida", show_alert=True)
            return
        
        c.execute('''UPDATE taxi_requests 
                   SET status = 'completada', assigned_admin = ?
                   WHERE id = ?''', (admin.id, request_id))
        conn.commit()
        
        c.execute('''SELECT data FROM taxi_requests WHERE id = ?''', (request_id,))
        data_str = c.fetchone()[0]
        data = eval(data_str)
        
        nuevo_texto = (
            "🚖 *Solicitud de Taxi - COMPLETADA*\n\n"
            + "\n".join([f"• {k.capitalize()}: {v}" for k, v in data.items()])
            + f"\n\n✅ Atendida por: {admin.mention_markdown()}"
        )
        
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("👤 Contactar", url=f"tg://user?id={user_id}")]])
        
        c.execute('''SELECT admin_id, message_id FROM taxi_messages WHERE request_id = ?''', (request_id,))
        mensajes = c.fetchall()
        
        for admin_id, message_id in mensajes:
            try:
                await context.bot.edit_message_text(
                    chat_id=admin_id,
                    message_id=message_id,
                    text=nuevo_texto,
                    reply_markup=markup,
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"Error actualizando mensaje: {e}")
        
        await context.bot.send_message(
            user_id,
            f"✅ Tu solicitud de taxi fue atendida por: {admin.mention_markdown()}",
            parse_mode="Markdown"
        )
        
    except Exception as e:
        logger.error(f"Error en handle_atender_taxi: {e}")
        await query.answer("❌ Error al procesar la solicitud", show_alert=True)
        
    finally:
        conn.close()

# ===== Handlers de Información =====
async def handle_info_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    
    try:
        await query.message.delete()
    except Exception as e:
        logger.error(f"Error eliminando mensaje de callback: {e}")
    
    user = query.from_user
    request_id = f"info_{user.id}_{int(time.time())}"
    
    save_info_request(request_id, user)
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    for admin in ADMINS:
        try:
            mensaje = await context.bot.send_message(
                chat_id=admin,
                text=(
                    f"ℹ️ **Nueva solicitud de información**\n\n"
                    f"👤 Usuario: {user.full_name}\n"
                    f"🆔 ID: {user.id}"
                ),
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("👤 Contactar", url=f"tg://user?id={user.id}"),
                        InlineKeyboardButton("✅ Yo lo atiendo", callback_data=f"atender_info_{request_id}")
                    ]
                ]),
                parse_mode="Markdown"
            )
            c.execute('''INSERT INTO info_messages 
                       VALUES (?, ?, ?)''',
                    (request_id, admin, mensaje.message_id))
        except Exception as e:
            logger.error(f"Error notificando al admin {admin}: {e}")
    
    conn.commit()
    conn.close()

async def handle_atender_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    
    parts = query.data.split('_')
    request_id = '_'.join(parts[2:])
    admin = update.effective_user
    
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    try:
        c.execute('''SELECT status, user_id FROM info_requests WHERE id = ?''', (request_id,))
        resultado = c.fetchone()
        
        if not resultado:
            await query.answer("⚠️ Solicitud no encontrada", show_alert=True)
            return
            
        estado_actual, user_id = resultado
        
        if estado_actual != 'pendiente':
            await query.answer("⚠️ Esta solicitud ya está atendida", show_alert=True)
            return
        
        c.execute('''UPDATE info_requests 
                   SET status = 'completada', assigned_admin = ?
                   WHERE id = ?''', (admin.id, request_id))
        conn.commit()
        
        c.execute('''SELECT admin_id, message_id FROM info_messages 
                   WHERE request_id = ?''', (request_id,))
        mensajes = c.fetchall()
        
        user = await context.bot.get_chat(user_id)
        nuevo_texto = (
            f"ℹ️ **Solicitud de información - COMPLETADA**\n\n"
            f"👤 Usuario: {user.full_name}\n"
            f"🆔 ID: {user.id}\n"
            f"✅ Atendida por: {admin.full_name}"
        )
        
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("👤 Contactar", url=f"tg://user?id={user.id}")]])
        
        for admin_id, message_id in mensajes:
            try:
                await context.bot.edit_message_text(
                    chat_id=admin_id,
                    message_id=message_id,
                    text=nuevo_texto,
                    reply_markup=markup,
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"Error actualizando mensaje: {e}")
        
        await context.bot.send_message(
            chat_id=user.id,
            text=f"✅ Tu solicitud fue atendida por: {admin.full_name}"
        )
        
    except Exception as e:
        logger.error(f"Error en handle_atender_info: {e}")
        await query.answer("❌ Error al procesar la solicitud", show_alert=True)
        
    finally:
        conn.close()

# ===== Menú Administrativo =====
async def admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMINS:
        await update.message.reply_text("⛔ Acceso no autorizado")
        return

    keyboard = [
        [InlineKeyboardButton("📋 Solicitudes pendientes", callback_data="admin_pendientes")],
        [InlineKeyboardButton("🔍 Consultar por usuario", callback_data="admin_consulta_user")],
        [InlineKeyboardButton("❌ Cerrar menú", callback_data="admin_close")]
    ]
    
    await update.message.reply_text(
        "🛠 *Menú de Administración*:\nSelecciona una opción:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def admin_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "admin_pendientes":
        await show_pending_requests(update, context)
    elif query.data == "admin_consulta_user":
        await query.edit_message_text("📥 Envía el ID del usuario:\nEjemplo: /consulta 123456789")
    elif query.data == "admin_close":
        await query.delete_message()

def get_request_details(request_id: str, request_type: str = 'taxi'):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    table = 'taxi_requests' if request_type == 'taxi' else 'info_requests'
    c.execute(f'''SELECT * FROM {table} WHERE id = ?''', (request_id,))
    result = c.fetchone()
    
    conn.close()
    return result

# ===== Modificar show_pending_requests =====
async def show_pending_requests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    taxi_requests = get_pending_requests('taxi')
    info_requests = get_pending_requests('info')
    
    response = "📋 *Solicitudes Pendientes*\n\n"
    
    if taxi_requests:
        response += "🚖 *Taxis:*\n"
        for req in taxi_requests:
            keyboard = [[InlineKeyboardButton("🔍 Ver detalles", callback_data=f"detalles_taxi_{req[0]}")]]
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"📌 Solicitud `{req[0]}`\n👤 Usuario: {req[2]}\n🕒 Fecha: {req[6][:16]}",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
    
    if info_requests:
        response += "\nℹ️ *Información:*\n"
        for req in info_requests:
            keyboard = [[InlineKeyboardButton("🔍 Ver detalles", callback_data=f"detalles_info_{req[0]}")]]
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"📌 Solicitud `{req[0]}`\n👤 Usuario: {req[2]}\n🕒 Fecha: {req[5][:16]}",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
    
    if not taxi_requests and not info_requests:
        await context.bot.send_message(
            update.effective_chat.id,
            "✅ No hay solicitudes pendientes",
            parse_mode="Markdown"
        )
        
async def show_request_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    parts = query.data.split('_')
    request_type = parts[1]  # taxi o info
    request_id = '_'.join(parts[2:])
    
    request_data = get_request_details(request_id, request_type)
    
    if not request_data:
        await query.edit_message_text("⚠️ La solicitud no existe o ya fue procesada")
        return
    
    if request_type == 'taxi':
        user_data = eval(request_data[3])  # Convertir string a dict
        detalles = (
            "🚖 *Detalles de Solicitud de Taxi*\n\n"
            f"🆔 ID: `{request_data[0]}`\n"
            f"👤 Usuario: {request_data[2]} (ID: {request_data[1]})\n"
            f"🕒 Hora: {user_data.get('hora', 'N/A')}\n"
            f"📍 Recogida: {user_data.get('direccion', 'N/A')}\n"
            f"🏁 Destino: {user_data.get('destino', 'N/A')}\n"
            f"👥 Personas: {user_data.get('personas', 'N/A')}\n"
            f"🧳 Equipaje: {user_data.get('equipaje', 'N/A')}\n"
            f"🐾 Mascotas: {user_data.get('mascota', 'N/A')}\n"
            f"📱 Teléfono: {user_data.get('telefono', 'N/A')}\n"
            f"🔁 Tipo Viaje: {user_data.get('tipo_viaje', 'N/A').replace('tipo_', '').replace('_', ' ').title()}\n"
            f"⏱ Espera: {user_data.get('tiempo_espera', 'N/A')} minutos\n"
            f"📅 Creado: {request_data[6][:16]}"
        )
    else:
        detalles = (
            "ℹ️ *Detalles de Solicitud de Información*\n\n"
            f"🆔 ID: `{request_data[0]}`\n"
            f"👤 Usuario: {request_data[2]} (ID: {request_data[1]})\n"
            f"📅 Creado: {request_data[5][:16]}"
        )
    
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("🗑 Eliminar solicitud", callback_data=f"eliminar_{request_type}_{request_id}")],
        [InlineKeyboardButton("◀️ Volver al listado", callback_data="admin_pendientes")]
    ])
    
    await query.edit_message_text(
        detalles,
        parse_mode="Markdown",
        reply_markup=markup
    )

# ===== Función de Cancelación =====
async def cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        '❌ Operación cancelada',
        reply_markup=ReplyKeyboardRemove()
    )
    context.user_data.clear()
    return ConversationHandler.END

# ===== Configuración Final =====
def main() -> None:
    persistence = PicklePersistence(filepath='taxi_bot_data.pickle')
    application = ApplicationBuilder()\
        .token(BOT_TOKEN)\
        .persistence(persistence)\
        .build()

    application.add_handler(MessageHandler(filters.ChatType.GROUPS & ~filters.UpdateType.EDITED, group_message_handler))
    application.add_handler(CallbackQueryHandler(handle_info_request, pattern="^info$"))
    application.add_handler(CallbackQueryHandler(handle_atender_taxi, pattern=r"^atender_taxi_"))
    application.add_handler(CallbackQueryHandler(handle_atender_info, pattern=r"^atender_info_"))
    application.add_handler(CallbackQueryHandler(start, pattern="^solicitar_taxi$"))
    application.add_handler(CallbackQueryHandler(show_request_details, pattern=r"^detalles_(taxi|info)_"))
    
    # Añadir también para el botón "Volver al listado"
    application.add_handler(CallbackQueryHandler(lambda update, ctx: show_pending_requests(update, ctx),pattern="^admin_pendientes$"))
    # Handlers administrativos
    application.add_handler(CommandHandler("admin", admin_menu))
    application.add_handler(CallbackQueryHandler(admin_button_handler, pattern=r"^admin_"))
    application.add_handler(CommandHandler("pendientes", show_pending_requests))

    # Conversation Handler
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            HORA: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_hora)],
            DIR_RECOGIDA: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_direccion)],
            DESTINO: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_destino)],
            CANT_PERSONAS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_personas)],
            EQUIPAJE: [CallbackQueryHandler(get_equipaje, pattern=r"^equipaje_(si|no)$")],
            MASCOTA: [CallbackQueryHandler(get_mascota, pattern=r"^mascota_(si|no)$")],
            TELEFONO: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_telefono)],
            TIPO_VIAJE: [CallbackQueryHandler(get_tipo_viaje, pattern=r"^tipo_(ida|ida_vuelta)$")],
            TIEMPO_ESPERA: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_tiempo_espera)],
            CONFIRMACION: [
                CallbackQueryHandler(confirmar_solicitud, pattern='^confirmar$'),
                CallbackQueryHandler(mostrar_menu_edicion, pattern='^editar_menu$'),
                CallbackQueryHandler(editar_campo, pattern=r'^editar_')
            ]
        },
        fallbacks=[CommandHandler('cancelar', cancelar)],
        persistent=True,
        name="taxi_conversation"
    )
    
    application.add_handler(conv_handler)
    application.job_queue.run_repeating(delete_old_messages, interval=30, first=10)
    application.run_polling()

if __name__ == '__main__':
    while True:
        try:
            main()
        except Exception as e:
            logger.error(f"Error crítico: {e}")
            time.sleep(10)
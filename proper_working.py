import os
import json
import base64
import asyncio
import websockets
import re
import logging
from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.websockets import WebSocketDisconnect
from twilio.twiml.voice_response import VoiceResponse, Connect, Say, Stream
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

# Configuration
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
PORT = int(os.getenv('PORT', 5050))
VOICE = 'alloy'
LOG_EVENT_TYPES = [
    'error', 'response.content.done', 'rate_limits.updated',
    'response.done', 'input_audio_buffer.committed',
    'input_audio_buffer.speech_stopped', 'input_audio_buffer.speech_started',
    'session.created', 'session.updated'
]
SHOW_TIMING_MATH = False

app = FastAPI()

if not OPENAI_API_KEY:
    raise ValueError('Missing the OpenAI API key. Please set it in the .env file.')

# Enhanced Language Detection Function
def detect_language(text):
    """
    Detect language based on script and common words
    Returns: 'english', 'hindi', or 'marathi'
    """
    if not text:
        return 'english'
    
    # Check for Devanagari script (Hindi/Marathi)
    devanagari_chars = len([c for c in text if '\u0900' <= c <= '\u097F'])
    total_chars = len([c for c in text if c.isalpha()])
    
    if total_chars == 0:
        return 'english'
    
    devanagari_ratio = devanagari_chars / total_chars
    
    # If more than 30% Devanagari characters
    if devanagari_ratio > 0.3:
        # Distinguish between Hindi and Marathi
        marathi_indicators = [
            'आहे', 'आपल', 'त्या', 'मी', 'तू', 'काय', 'कसे', 'कुठे', 'केव्हा', 'कोण',
            'मला', 'तुला', 'त्याला', 'तिला', 'आम्हा', 'तुम्हा', 'त्यांना',
            'करत', 'येत', 'जात', 'होत', 'पाहिजे', 'शकत', 'लागत', 'आहेत', 'होते'
        ]
        
        hindi_indicators = [
            'है', 'हैं', 'हूं', 'हूँ', 'में', 'को', 'से', 'का', 'की', 'के', 'पर', 'और',
            'या', 'तो', 'जो', 'वो', 'यह', 'वह', 'मैं', 'तुम', 'आप', 'वे', 'हम',
            'कैसे', 'क्या', 'कहाँ', 'कब', 'कौन', 'कितना', 'क्यों', 'होता', 'होती'
        ]
        
        marathi_score = sum(1 for word in marathi_indicators if word in text)
        hindi_score = sum(1 for word in hindi_indicators if word in text)
        
        if marathi_score > hindi_score:
            return 'marathi'
        else:
            return 'hindi'
    
    return 'english'

# System Messages for Different Languages
SYSTEM_MESSAGE_ENGLISH = """
You are a helpful and professional AI voice assistant for Ethernet Express (EXPL), a leading internet service provider in Goa, India. You assist customers with their internet, phone, and technical support needs.

COMPANY INFORMATION:
- Company: Ethernet Xpress India Pvt. Ltd.
- Serving Goa since 2007 with 35,000+ residential customers
- Location: Nova Cidade Complex, Alto Porvorim, Goa
- Helpline: 1800 266 4986 (9am-6pm, Mon-Sat)
- WhatsApp Support: 88888 86672
- Email: support@expl.in
- Customer Portal: customer.expl.in

RESIDENTIAL BROADBAND PLANS:
1. STARTER - ₹695/month + GST
   - Speed: 150 Mbps, Post FUP: 5 Mbps
   - Data: 300 GB, 15 OTT apps, Unlimited voice

2. STANDARD (Popular) - ₹999/month + GST
   - Speed: 150 Mbps, Post FUP: 10 Mbps
   - Data: 700 GB, 15 OTT apps, Unlimited voice

3. PREMIUM - ₹1499/month + GST
   - Speed: 300 Mbps, Post FUP: 25 Mbps
   - Data: 1.5 TB, 15 OTT apps, Unlimited voice

4. SUPER - ₹1999/month + GST
   - Speed: 300 Mbps, Post FUP: 50 Mbps
   - Data: 3 TB, 15 OTT apps, Unlimited voice

PLUS PLANS (with 23 OTT apps):
- STARTER+ ₹920, STANDARD+ ₹1224, PREMIUM+ ₹1724, SUPER+ ₹2224, ULTRA+ ₹3999 (1 Gbps)

SERVICES:
- Fiber optic internet up to 1 Gbps
- FTTH (Fiber to Home)
- Landline with unlimited voice
- Internet Leased Lines for business
- SIP Trunk, Wi-Fi Hotspot
- Parental controls, Intercom
- Free Wi-Fi installation

OFFERS:
- Pay 5 months get 1 month FREE
- Pay 10 months get 3 months FREE
- Refer-a-friend: You get 300 Mbps plan, friend gets 150 Mbps plan (30 days validity)

OTT APPS INCLUDED: Netflix, Amazon Prime, Disney+ Hotstar, ZEE5, SonyLIV, Voot, AltBalaji, Hoichoi, ShemarooME, Lionsgate Play, Discovery+, Eros Now, JioCinema, JioSaavn, and more.

TROUBLESHOOTING TIPS:
- Slow speed: Check data usage, restart router, run speed test
- No internet: Check cables, power cycle modem, contact support
- WiFi issues: Check router placement, reduce interference

Always be helpful, professional, and provide accurate information. If you don't know something specific, direct them to call the helpline or visit the website. Keep responses concise and natural for voice conversations.
"""

SYSTEM_MESSAGE_HINDI = """
आप Ethernet Express (EXPL) के लिए एक सहायक और पेशेवर AI वॉयस असिस्टेंट हैं, जो गोवा, भारत में एक प्रमुख इंटरनेट सेवा प्रदाता है। आप ग्राहकों की इंटरनेट, फोन और तकनीकी सहायता की जरूरतों में मदत करते हैं।

कंपनी की जानकारी:
- कंपनी: Ethernet Xpress India Pvt. Ltd.
- 2007 से गोवा में सेवा, 35,000+ आवासीय ग्राहक
- स्थान: Nova Cidade Complex, Alto Porvorim, Goa
- हेल्पलाइन: 1800 266 4986 (सुबह 9-शाम 6, सोम-शनि)
- WhatsApp सपोर्ट: 88888 86672
- ईमेल: support@expl.in
- कस्टमर पोर्टल: customer.expl.in

आवासीय ब्रॉडबैंड प्लान:
1. STARTER - ₹695/महीना + GST
   - स्पीड: 150 Mbps, FUP के बाद: 5 Mbps
   - डेटा: 300 GB, 15 OTT ऐप्स, असीमित वॉयस

2. STANDARD (लोकप्रिय) - ₹999/महीना + GST
   - स्पीड: 150 Mbps, FUP के बाद: 10 Mbps
   - डेटा: 700 GB, 15 OTT ऐप्स, असीमित वॉयस

3. PREMIUM - ₹1499/महीना + GST
   - स्पीड: 300 Mbps, FUP के बाद: 25 Mbps
   - डेटा: 1.5 TB, 15 OTT ऐप्स, असीमित वॉयस

4. SUPER - ₹1999/महीना + GST
   - स्पीड: 300 Mbps, FUP के बाद: 50 Mbps
   - डेटा: 3 TB, 15 OTT ऐप्स, असीमित वॉयस

PLUS प्लान (23 OTT ऐप्स के साथ):
- STARTER+ ₹920, STANDARD+ ₹1224, PREMIUM+ ₹1724, SUPER+ ₹2224, ULTRA+ ₹3999 (1 Gbps)

सेवाएं:
- 1 Gbps तक फाइबर ऑप्टिक इंटरनेट
- FTTH (Fiber to Home)
- असीमित वॉयस के साथ लैंडलाइन
- व्यापार के लिए Internet Leased Lines
- SIP Trunk, Wi-Fi Hotspot
- पैरेंटल कंट्रोल, इंटरकॉम
- मुफ्त Wi-Fi इंस्टॉलेशन

ऑफर:
- 5 महीने का भुगतान करें, 1 महीना मुफ्त पाएं
- 10 महीने का भुगतान करें, 3 महीने मुफ्त पाएं
- दोस्त को रेफर करें: आपको 300 Mbps प्लान, दोस्त को 150 Mbps प्लान (30 दिन वैधता)

शामिल OTT ऐप्स: Netflix, Amazon Prime, Disney+ Hotstar, ZEE5, SonyLIV, Voot, AltBalaji, Hoichoi, ShemarooME, Lionsgate Play, Discovery+, Eros Now, JioCinema, JioSaavn, और अन्य।

समस्या निवारण युक्तियां:
- धीमी स्पीड: डेटा उपयोग जांचें, राउटर रीस्टार्ट करें, स्पीड टेस्ट करें
- इंटरनेट नहीं: केबल जांचें, मॉडेम को पावर साइकल करें, सपोर्ट से संपर्क करें
- WiFi समस्याएं: राउटर की जगह जांचें, हस्तक्षेप कम करें

हमेशा सहायक, पेशेवर रहें और सटीक जानकारी प्रदान करें। आवाज़ की बातचीत के लिए संक्षिप्त और प्राकृतिक उत्तर दें।
"""

SYSTEM_MESSAGE_MARATHI = """
तुम्ही Ethernet Express (EXPL) साठी एक सहायक आणि व्यावसायिक AI आवाज सहाय्यक आहात, जे गोव्यातील एक आघाडीचा इंटरनेट सेवा प्रदाता आहे. तुम्ही ग्राहकांच्या इंटरनेट, फोन आणि तांत्रिक सहाय्याच्या गरजांमध्ये मदत करता.

कंपनीची माहिती:
- कंपनी: Ethernet Xpress India Pvt. Ltd.
- २००७ पासून गोव्यात सेवा, ३५,०००+ निवासी ग्राहक
- स्थान: Nova Cidade Complex, Alto Porvorim, Goa
- हेल्पलाइन: 1800 266 4986 (सकाळी ९-संध्याकाळी ६, सोम-शनि)
- WhatsApp सपोर्ट: 88888 86672
- ईमेल: support@expl.in
- कस्टमर पोर्टल: customer.expl.in

निवासी ब्रॉडबँड प्लॅन:
१. STARTER - ₹६९५/महिना + GST
   - स्पीड: १५० Mbps, FUP नंतर: ५ Mbps
   - डेटा: ३०० GB, १५ OTT अॅप्स, अमर्यादित आवाज

२. STANDARD (लोकप्रिय) - ₹९९९/महिना + GST
   - स्पीड: १५० Mbps, FUP नंतर: १० Mbps
   - डेटा: ७०० GB, १५ OTT अॅप्स, अमर्यादित आवाज

३. PREMIUM - ₹१४९९/महिना + GST
   - स्पीड: ३०० Mbps, FUP नंतर: २५ Mbps
   - डेटा: १.५ TB, १५ OTT अॅप्स, अमर्यादित आवाज

४. SUPER - ₹१९९९/महिना + GST
   - स्पीड: ३०० Mbps, FUP नंतर: ५० Mbps
   - डेटा: ३ TB, १५ OTT अॅप्स, अमर्यादित आवाज

PLUS प्लॅन (२३ OTT अॅप्ससह):
- STARTER+ ₹९२०, STANDARD+ ₹१२२४, PREMIUM+ ₹१७२४, SUPER+ ₹२२२४, ULTRA+ ₹३९९९ (१ Gbps)

सेवा:
- १ Gbps पर्यंत फायबर ऑप्टिक इंटरनेट
- FTTH (Fiber to Home)
- अमर्यादित आवाजासह लँडलाइन
- व्यवसायासाठी Internet Leased Lines
- SIP Trunk, Wi-Fi Hotspot
- पॅरेंटल कंट्रोल, इंटरकॉम
- मोफत Wi-Fi इन्स्टॉलेशन

ऑफर:
- ५ महिन्यांचे पेमेंट करा, १ महिना मोफत मिळवा
- १० महिन्यांचे पेमेंट करा, ३ महिने मोफत मिळवा
- मित्राला रेफर करा: तुम्हाला ३०० Mbps प्लॅन, मित्राला १५० Mbps प्लॅन (३० दिवस वैधता)

समाविष्ट OTT अॅप्स: Netflix, Amazon Prime, Disney+ Hotstar, ZEE5, SonyLIV, Voot, AltBalaji, Hoichoi, ShemarooME, Lionsgate Play, Discovery+, Eros Now, JioCinema, JioSaavn आणि इतर.

समस्यानिवारण टिप्स:
- मंद स्पीड: डेटा वापर तपासा, राउटर रीस्टार्ट करा, स्पीड टेस्ट करा
- इंटरनेट नाही: केबल तपासा, मॉडेम पावर सायकल करा, सपोर्टशी संपर्क साधा
- WiFi समस्या: राउटरची जागा तपासा, हस्तक्षेप कमी करा

नेहमी सहायक, व्यावसायिक राहा आणि अचूक माहिती द्या. आवाजाच्या संभाषणासाठी संक्षिप्त आणि नैसर्गिक उत्तरे द्या.
"""

@app.get("/", response_class=JSONResponse)
async def index_page():
    return {"message": "Ethernet Express Voice Assistant is running!", "status": "healthy"}

@app.api_route("/incoming-call", methods=["GET", "POST"])
async def handle_incoming_call(request: Request):
    """Handle incoming call and return TwiML response to connect to Media Stream."""
    try:
        response = VoiceResponse()
        response.say("Welcome to Ethernet Express customer support. Please wait while we connect you to our AI voice assistant.")
        response.pause(length=1)
        response.say("You can speak in English, Hindi, or Marathi. How can I help you today?")
        
        # Get the host from request headers to handle different environments
        host = request.headers.get('host') or request.url.hostname
        protocol = 'wss' if request.url.scheme == 'https' else 'ws'
        
        connect = Connect()
        connect.stream(url=f'{protocol}://{host}/media-stream')
        response.append(connect)
        
        logger.info(f"Incoming call handled, redirecting to {protocol}://{host}/media-stream")
        return HTMLResponse(content=str(response), media_type="application/xml")
    except Exception as e:
        logger.error(f"Error handling incoming call: {e}")
        # Fallback response
        response = VoiceResponse()
        response.say("Sorry, we're experiencing technical difficulties. Please try again later.")
        return HTMLResponse(content=str(response), media_type="application/xml")

@app.websocket("/media-stream")
async def handle_media_stream(websocket: WebSocket):
    """Handle WebSocket connections between Twilio and OpenAI."""
    logger.info("Client connected to media stream")
    await websocket.accept()

    try:
        async with websockets.connect(
            'wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview-2024-10-01',
            extra_headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "OpenAI-Beta": "realtime=v1"
            },
            ping_interval=20,
            ping_timeout=10
        ) as openai_ws:
            
            current_language = 'english'
            current_system_message = SYSTEM_MESSAGE_ENGLISH

            async def initialize_session(openai_ws, system_message):
                session_update = {
                    "type": "session.update",
                    "session": {
                        "turn_detection": {"type": "server_vad"},
                        "input_audio_format": "g711_ulaw",
                        "output_audio_format": "g711_ulaw",
                        "voice": VOICE,
                        "instructions": system_message,
                        "modalities": ["text", "audio"],
                        "temperature": 0.7,
                    }
                }
                logger.info(f'Sending session update for language: {current_language}')
                await openai_ws.send(json.dumps(session_update))

            await initialize_session(openai_ws, current_system_message)

            # Connection specific state
            stream_sid = None
            latest_media_timestamp = 0
            last_assistant_item = None
            mark_queue = []
            response_start_timestamp_twilio = None

            async def receive_from_twilio():
                nonlocal stream_sid, latest_media_timestamp
                try:
                    async for message in websocket.iter_text():
                        data = json.loads(message)
                        if data['event'] == 'media' and openai_ws.open:
                            latest_media_timestamp = int(data['media']['timestamp'])
                            audio_append = {
                                "type": "input_audio_buffer.append",
                                "audio": data['media']['payload']
                            }
                            await openai_ws.send(json.dumps(audio_append))
                        elif data['event'] == 'start':
                            stream_sid = data['start']['streamSid']
                            logger.info(f"Incoming stream has started {stream_sid}")
                            response_start_timestamp_twilio = None
                            latest_media_timestamp = 0
                            last_assistant_item = None
                        elif data['event'] == 'mark':
                            if mark_queue:
                                mark_queue.pop(0)
                except WebSocketDisconnect:
                    logger.info("Client disconnected from Twilio")
                    if openai_ws.open:
                        await openai_ws.close()
                except Exception as e:
                    logger.error(f"Error receiving from Twilio: {e}")

            async def send_to_twilio():
                nonlocal stream_sid, last_assistant_item, response_start_timestamp_twilio, current_language, current_system_message
                try:
                    async for openai_message in openai_ws:
                        response = json.loads(openai_message)
                        if response['type'] in LOG_EVENT_TYPES:
                            logger.info(f"Received event: {response['type']}")

                        if response.get('type') == 'response.audio.delta' and 'delta' in response:
                            audio_payload = base64.b64encode(base64.b64decode(response['delta'])).decode('utf-8')
                            audio_delta = {
                                "event": "media",
                                "streamSid": stream_sid,
                                "media": {
                                    "payload": audio_payload
                                }
                            }
                            await websocket.send_json(audio_delta)

                            if response_start_timestamp_twilio is None:
                                response_start_timestamp_twilio = latest_media_timestamp
                                if SHOW_TIMING_MATH:
                                    logger.info(f"Setting start timestamp for new response: {response_start_timestamp_twilio}ms")

                            if response.get('item_id'):
                                last_assistant_item = response['item_id']

                            await send_mark(websocket, stream_sid)

                        elif response.get('type') == 'conversation.item.created':
                            item = response.get('item', {})
                            if item.get('role') == 'user' and item.get('content'):
                                for content in item['content']:
                                    if content['type'] == 'input_text' and content['text']:
                                        user_input = content['text']
                                        logger.info(f"User said: {user_input}")

                                        # Detect language using enhanced detection
                                        detected_language = detect_language(user_input)
                                        logger.info(f"Detected language: {detected_language}")

                                        # Switch system message if language changed
                                        if detected_language != current_language:
                                            current_language = detected_language
                                            if detected_language == 'hindi':
                                                current_system_message = SYSTEM_MESSAGE_HINDI
                                            elif detected_language == 'marathi':
                                                current_system_message = SYSTEM_MESSAGE_MARATHI
                                            else:
                                                current_system_message = SYSTEM_MESSAGE_ENGLISH
                                            
                                            logger.info(f"Switching to {detected_language} instructions.")
                                            await initialize_session(openai_ws, current_system_message)

                        elif response.get('type') == 'input_audio_buffer.speech_started':
                            logger.info("Speech started detected.")
                            if last_assistant_item:
                                logger.info(f"Interrupting response with id: {last_assistant_item}")
                                await handle_speech_started_event()
                except Exception as e:
                    logger.error(f"Error in send_to_twilio: {e}")

            async def handle_speech_started_event():
                nonlocal response_start_timestamp_twilio, last_assistant_item
                logger.info("Handling speech started event.")
                if mark_queue and response_start_timestamp_twilio is not None:
                    elapsed_time = latest_media_timestamp - response_start_timestamp_twilio
                    if SHOW_TIMING_MATH:
                        logger.info(f"Calculating elapsed time for truncation: {latest_media_timestamp} - {response_start_timestamp_twilio} = {elapsed_time}ms")

                    if last_assistant_item:
                        truncate_event = {
                            "type": "conversation.item.truncate",
                            "item_id": last_assistant_item,
                            "content_index": 0,
                            "audio_end_ms": elapsed_time
                        }
                        await openai_ws.send(json.dumps(truncate_event))

                    await websocket.send_json({
                        "event": "clear",
                        "streamSid": stream_sid
                    })

                    mark_queue.clear()
                    last_assistant_item = None
                    response_start_timestamp_twilio = None

            async def send_mark(connection, stream_sid):
                if stream_sid:
                    mark_event = {
                        "event": "mark",
                        "streamSid": stream_sid,
                        "mark": {"name": "responsePart"}
                    }
                    await connection.send_json(mark_event)
                    mark_queue.append('responsePart')

            await asyncio.gather(receive_from_twilio(), send_to_twilio())

    except Exception as e:
        logger.error(f"Error in media stream: {e}")
    finally:
        logger.info("Media stream connection closed")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)

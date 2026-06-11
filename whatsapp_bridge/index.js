const { default: makeWASocket, useMultiFileAuthState, DisconnectReason, fetchLatestBaileysVersion, downloadMediaMessage } = require('@whiskeysockets/baileys');
const pino = require('pino');
const express = require('express');
const bodyParser = require('body-parser');
const axios = require('axios');
const dotenv = require('dotenv');
const fs = require('fs');
const QRCode = require('qrcode');
const path = require('path');

dotenv.config({ path: '../.env' });

const BOT_TOKEN = process.env.BOT_TOKEN;
const PORT = process.env.PORT || 5001;

let sock = null;
let qrCode = null;
let connectionState = 'DISCONNECTED';
let telegramChatId = null;
let reconnectTimeout = null;
let aiAutoReply = false;
let aiVoiceReply = false;

// Load AI auto-reply config if exists
try {
    if (fs.existsSync('session/config.json')) {
        const configData = JSON.parse(fs.readFileSync('session/config.json', 'utf-8'));
        aiAutoReply = !!configData.ai_auto_reply;
        aiVoiceReply = !!configData.ai_voice_reply;
        console.log("Loaded WhatsApp bridge config, AI Auto-Reply:", aiAutoReply, "AI Voice Reply:", aiVoiceReply);
    }
} catch (e) {
    console.error("Failed to load config on startup:", e.message);
}

const app = express();
app.use(bodyParser.json());

async function sendTelegramMessage(text) {
    if (!BOT_TOKEN || !telegramChatId) {
        console.error("Missing BOT_TOKEN or telegramChatId", { BOT_TOKEN, telegramChatId });
        return;
    }
    const url = `https://api.telegram.org/bot${BOT_TOKEN}/sendMessage`;
    try {
        await axios.post(url, {
            chat_id: telegramChatId,
            text: text,
            parse_mode: 'HTML'
        });
        console.log("Notification sent to Telegram");
    } catch (e) {
        console.error("Failed to send Telegram message:", e.response ? e.response.data : e.message);
    }
}

function escapeHtml(str) {
    if (!str) return "";
    return str
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;");
}

async function getAIResponse(userMessage, base64Image = null) {
    const groqKey = process.env.GROQ_API_KEY;
    
    if (!groqKey) {
        console.error("GROQ_API_KEY not found in environment variables.");
        return null;
    }

    try {
        let messages = [];
        if (base64Image) {
            console.log("Generating AI response with Groq Vision...");
            messages = [
                {
                    role: "system",
                    content: "أنت مساعد ذكي ومحترف تجيب على رسائل الواتساب وتستطيع رؤية الصور وتحليلها بدقة واختصار وباللغة العربية الفصحى. تجنب تماماً استخدام أي علامات تنسيق ماركداون (مثل ** أو * أو `) في إجابتك."
                },
                {
                    role: "user",
                    content: [
                        {
                            type: "text",
                            text: userMessage || "صف هذه الصورة بالتفصيل."
                        },
                        {
                            type: "image_url",
                            image_url: {
                                url: `data:image/jpeg;base64,${base64Image}`
                            }
                        }
                    ]
                }
            ];
        } else {
            console.log(`Generating AI response for message: "${userMessage}"`);
            messages = [
                {
                    role: "system",
                    content: "أنت مساعد ذكي ومحترف تجيب على رسائل الواتساب بدقة واختصار وباللغة العربية الفصحى. تجنب تماماً استخدام أي علامات تنسيق ماركداون (مثل ** أو * أو `) في إجابتك، واجعل الرد يبدو طبيعياً كرسالة واتساب عادية."
                },
                {
                    role: "user",
                    content: userMessage
                }
            ];
        }

        const response = await axios.post(
            "https://api.groq.com/openai/v1/chat/completions",
            {
                model: base64Image ? "llama-3.2-11b-vision-preview" : (process.env.GROQ_MODEL || "llama-3.3-70b-versatile"),
                messages: messages,
                temperature: 0.7
            },
            {
                headers: {
                    "Authorization": `Bearer ${groqKey}`,
                    "Content-Type": "application/json"
                },
                timeout: 25000
            }
        );

        if (response.data && response.data.choices && response.data.choices[0]) {
            return response.data.choices[0].message.content.trim();
        }
    } catch (error) {
        console.error("Failed to fetch AI response from Groq:", error.response ? error.response.data : error.message);
    }
    return null;
}

async function handleIncomingWhatsAppMessage(msg) {
    const senderJid = msg.key.remoteJid;
    const senderNumber = senderJid.split('@')[0];
    const senderName = msg.pushName || senderNumber;
    
    // Extract text content
    let text = "";
    if (msg.message.conversation) {
        text = msg.message.conversation;
    } else if (msg.message.extendedTextMessage) {
        text = msg.message.extendedTextMessage.text;
    } else if (msg.message.imageMessage) {
        text = `[صورة] ${msg.message.imageMessage.caption || ''}`;
    } else if (msg.message.videoMessage) {
        text = `[فيديو] ${msg.message.videoMessage.caption || ''}`;
    } else if (msg.message.documentMessage) {
        text = `[ملف] ${msg.message.documentMessage.fileName || ''}`;
    } else if (msg.message.audioMessage) {
        text = `[صوت]`;
    } else {
        text = `[رسالة غير مدعومة]`;
    }
    
    const escapedText = escapeHtml(text);
    const escapedSenderName = escapeHtml(senderName);
    
    const notification = 
        `💬 <b>رسالة جديدة من واتساب</b>\n\n` +
        `👤 <b>المرسل:</b> ${escapedSenderName}\n` +
        `📱 <b>الرقم:</b> <code>+${senderNumber}</code>\n\n` +
        `📝 <b>الرسالة:</b>\n${escapedText}\n\n` +
        `_________________\n` +
        `<i>رد على هذه الرسالة للرد على المرسل في واتساب.</i>\n` +
        `[From: <code>${senderJid}</code>]`;
        
    await sendTelegramMessage(notification);

    const isGroup = senderJid.endsWith('@g.us');
    
    if (aiAutoReply && !isGroup) {
        // Run AI response asynchronously to avoid blocking the main event flow
        (async () => {
            try {
                let base64Image = null;
                let rawText = text;
                
                // 1. Handle Voice Note inputs (Transcription)
                if (msg.message.audioMessage) {
                    try {
                        console.log("Downloading WhatsApp audio message...");
                        const buffer = await downloadMediaMessage(
                            msg,
                            'buffer',
                            {},
                            { 
                                logger: pino({ level: 'error' }),
                                reuploadRequest: sock.updateMediaMessage
                            }
                        );
                        
                        console.log("Transcribing audio via proxy...");
                        const transResp = await axios.post("http://localhost:8090/transcribe", buffer, {
                            headers: { 'Content-Type': 'audio/ogg' }
                        });
                        
                        if (transResp.data && transResp.data.text) {
                            console.log(`Transcribed text: "${transResp.data.text}"`);
                            rawText = transResp.data.text;
                        } else {
                            rawText = "";
                        }
                    } catch (transErr) {
                        console.error("Transcription failed:", transErr.message);
                        rawText = "";
                    }
                }
                
                // 2. Handle Image inputs (Groq Vision)
                else if (msg.message.imageMessage) {
                    try {
                        console.log("Downloading WhatsApp image message...");
                        const buffer = await downloadMediaMessage(
                            msg,
                            'buffer',
                            {},
                            { 
                                logger: pino({ level: 'error' }),
                                reuploadRequest: sock.updateMediaMessage
                            }
                        );
                        base64Image = buffer.toString('base64');
                        rawText = msg.message.imageMessage.caption || "صف هذه الصورة بالتفصيل.";
                    } catch (downloadErr) {
                        console.error("Failed to download media message:", downloadErr.message);
                        rawText = "";
                    }
                }
                
                // 3. Filter out placeholder text
                const isPlaceholderText = rawText.startsWith('[') && rawText.endsWith(']');
                if (isPlaceholderText) {
                    rawText = "";
                }

                if (rawText && rawText.trim().length > 0) {
                    // Send composing or recording presence
                    try {
                        const presence = aiVoiceReply ? 'recording' : 'composing';
                        await sock.sendPresenceUpdate(presence, senderJid);
                    } catch (presenceErr) {}

                    // Get AI response
                    const aiResponse = await getAIResponse(rawText, base64Image);
                    if (aiResponse) {
                        if (aiVoiceReply) {
                            try {
                                console.log("Calling Edge TTS via proxy...");
                                const ttsUrl = `http://localhost:8090/tts?text=${encodeURIComponent(aiResponse)}`;
                                const ttsResp = await axios.get(ttsUrl, { responseType: 'arraybuffer' });
                                
                                const tempFilePath = `session/temp_voice_${Date.now()}.mp3`;
                                fs.writeFileSync(tempFilePath, Buffer.from(ttsResp.data));
                                
                                await sock.sendMessage(senderJid, { 
                                    audio: { url: tempFilePath }, 
                                    mimetype: 'audio/mp4', 
                                    ptt: true 
                                });
                                console.log(`Auto-replied to ${senderJid} with AI Voice message.`);
                                
                                try {
                                    fs.unlinkSync(tempFilePath);
                                } catch (cleanErr) {}
                            } catch (ttsErr) {
                                console.error("TTS generation or sending failed, falling back to text:", ttsErr.message);
                                await sock.sendMessage(senderJid, { text: aiResponse });
                            }
                        } else {
                            await sock.sendMessage(senderJid, { text: aiResponse });
                            console.log(`Auto-replied to ${senderJid} with AI text response.`);
                        }
                    }
                }
            } catch (err) {
                console.error("AI Auto-reply error:", err.message);
            } finally {
                // Stop presence
                try {
                    await sock.sendPresenceUpdate('paused', senderJid);
                } catch (presenceErr) {}
            }
        })();
    }
}

async function initWhatsApp(chatId, pairPhone = null, forceNewSession = false) {
    telegramChatId = chatId;
    
    if (reconnectTimeout) {
        clearTimeout(reconnectTimeout);
        reconnectTimeout = null;
    }
    
    if (!fs.existsSync('session')) {
        fs.mkdirSync('session');
    }
    fs.writeFileSync('session/telegram_chat_id.txt', String(chatId));

    // Delete any old QR code files to avoid serving stale QRs
    if (fs.existsSync('session/qr.png')) {
        try {
            fs.unlinkSync('session/qr.png');
        } catch (err) {
            console.error("Failed to delete old QR code:", err.message);
        }
    }
    qrCode = null;

    console.log(`initWhatsApp called for chatId: ${chatId}, pairPhone: ${pairPhone}, forceNewSession: ${forceNewSession}`);

    if (sock && connectionState === 'CONNECTED' && !pairPhone) {
        console.log("Baileys socket already connected, skipping init.");
        return null;
    }

    if (forceNewSession) {
        console.log("Clearing old session credentials to generate fresh QR code (forced new session).");
        if (fs.existsSync('session/creds.json')) {
            try {
                fs.unlinkSync('session/creds.json');
            } catch (err) {
                console.error("Failed to delete credentials file:", err.message);
            }
        }
    }

    if (sock) {
        console.log("Closing existing disconnected socket to re-initialize.");
        try {
            sock.end();
        } catch (e) {
            console.error("Error closing old socket:", e);
        }
        sock = null;
    }

    let version = [2, 3000, 1015901307]; // Fallback version
    try {
        const { version: latestVersion } = await fetchLatestBaileysVersion();
        version = latestVersion;
        console.log(`Using latest WA version: ${version.join('.')}`);
    } catch (err) {
        console.warn("Failed to fetch latest WA version, using fallback:", err.message);
    }

    const { state, saveCreds } = await useMultiFileAuthState('session');
    
    sock = makeWASocket({
        version,
        auth: state,
        printQRInTerminal: false,
        logger: pino({ level: 'error' }),
        connectTimeoutMs: 30000,
        defaultQueryTimeoutMs: 30000,
        keepAliveIntervalMs: 10000
    });
    
    sock.ev.on('creds.update', saveCreds);
    
    sock.ev.on('connection.update', async (update) => {
        const { connection, lastDisconnect, qr } = update;
        console.log("Connection update:", { connection, lastDisconnect: lastDisconnect ? lastDisconnect.message : null, qr: !!qr });
        
        if (qr) {
            qrCode = qr;
            try {
                await QRCode.toFile('./session/qr.png', qr);
                console.log("QR code saved to session/qr.png");
            } catch (err) {
                console.error("Failed to save QR code file:", err);
            }
        }
        
        if (connection === 'close') {
            const shouldReconnect = lastDisconnect?.error?.output?.statusCode !== DisconnectReason.loggedOut;
            console.log('Connection closed. Reconnecting:', shouldReconnect);
            sock = null;
            connectionState = 'DISCONNECTED';
            if (shouldReconnect) {
                if (!reconnectTimeout) {
                    reconnectTimeout = setTimeout(() => {
                        reconnectTimeout = null;
                        initWhatsApp(telegramChatId);
                    }, 5000);
                }
            } else {
                console.log("Logged out from WhatsApp. Clearing session credentials.");
                if (fs.existsSync('session/creds.json')) {
                    try {
                        fs.unlinkSync('session/creds.json');
                    } catch (err) {
                        console.error("Failed to delete credentials file:", err.message);
                    }
                }
            }
        } else if (connection === 'open') {
            console.log('WhatsApp connection opened successfully!');
            connectionState = 'CONNECTED';
            qrCode = null;
            sendTelegramMessage("✅ <b>تم ربط واتساب بنجاح!</b>\nالبوت متصل الآن ويمكنه استقبال وإرسال رسائل واتساب.");
        }
    });
    
    sock.ev.on('messages.upsert', async (m) => {
        if (m.type === 'notify') {
            for (const msg of m.messages) {
                if (!msg.key.fromMe && msg.message) {
                    await handleIncomingWhatsAppMessage(msg);
                }
            }
        }
    });
    
    if (pairPhone) {
        await new Promise(resolve => setTimeout(resolve, 3000));
        try {
            let code = await sock.requestPairingCode(pairPhone);
            return code;
        } catch (e) {
            console.error("Pairing code error:", e);
            throw e;
        }
    }
    
    return null;
}

// Auto-restore session if exists
if (fs.existsSync('session/creds.json') && fs.existsSync('session/telegram_chat_id.txt')) {
    try {
        telegramChatId = fs.readFileSync('session/telegram_chat_id.txt', 'utf-8').trim();
        if (telegramChatId) {
            console.log("Auto-restoring WhatsApp connection for chatId:", telegramChatId);
            initWhatsApp(telegramChatId);
        }
    } catch (e) {
        console.error("Failed to auto-restore connection:", e.message);
    }
}

// HTTP API endpoints
app.get('/status', (req, res) => {
    res.json({
        state: connectionState,
        qr: qrCode ? true : false
    });
});

app.post('/init', async (req, res) => {
    const { chat_id } = req.body;
    try {
        await initWhatsApp(chat_id, null, true);
        res.json({ success: true, state: connectionState });
    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

app.post('/pair', async (req, res) => {
    const { phone, chat_id } = req.body;
    try {
        const cleanPhone = phone.replace(/[^0-9]/g, '');
        const code = await initWhatsApp(chat_id, cleanPhone, true);
        res.json({ success: true, code: code });
    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

app.post('/send', async (req, res) => {
    const { to, message } = req.body;
    if (!sock || connectionState !== 'CONNECTED') {
        return res.status(400).json({ error: 'WhatsApp not connected' });
    }
    try {
        await sock.sendMessage(to, { text: message });
        res.json({ success: true });
    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

app.post('/disconnect', async (req, res) => {
    try {
        if (sock) {
            try {
                await sock.logout();
            } catch (logoutErr) {
                console.error("Logout error (likely already disconnected):", logoutErr.message);
            }
            sock = null;
        }
        connectionState = 'DISCONNECTED';
        qrCode = null;
        if (fs.existsSync('session')) {
            fs.rmSync('session', { recursive: true, force: true });
        }
        res.json({ success: true, message: 'Disconnected and session cleared' });
    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

app.get('/config', (req, res) => {
    res.json({
        ai_auto_reply: aiAutoReply,
        ai_voice_reply: aiVoiceReply
    });
});

app.post('/config', (req, res) => {
    const { ai_auto_reply, ai_voice_reply } = req.body;
    if (ai_auto_reply !== undefined) aiAutoReply = !!ai_auto_reply;
    if (ai_voice_reply !== undefined) aiVoiceReply = !!ai_voice_reply;
    try {
        if (!fs.existsSync('session')) {
            fs.mkdirSync('session');
        }
        fs.writeFileSync('session/config.json', JSON.stringify({
            ai_auto_reply: aiAutoReply,
            ai_voice_reply: aiVoiceReply
        }, null, 2));
        res.json({ success: true, ai_auto_reply: aiAutoReply, ai_voice_reply: aiVoiceReply });
    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

app.get('/qr.png', (req, res) => {
    if (fs.existsSync('session/qr.png')) {
        res.sendFile(path.resolve('session/qr.png'));
    } else {
        res.status(404).send('No QR code generated yet');
    }
});

app.get('/', (req, res) => {
    res.sendFile(path.resolve('panel.html'));
});

app.get('/panel', (req, res) => {
    res.sendFile(path.resolve('panel.html'));
});

app.listen(PORT, () => {
    console.log(`WhatsApp-Telegram bridge running on port ${PORT}`);
});

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
let aiTranscribeVoice = true;
let aiImageVision = true;
let profilePicUrl = null;
let stats = { repliesSent: 0, voiceRepliesSent: 0, messagesReceived: 0 };
let liveLogs = [];

let db = { chats: {}, keywords: [], config: {}, campaign: { running: false, total: 0, sent: 0, failed: 0, list: [], message: "", delay: 5 } };

function loadDb() {
    try {
        if (fs.existsSync('session/messages_db.json')) {
            db = JSON.parse(fs.readFileSync('session/messages_db.json', 'utf-8'));
        }
    } catch (e) {
        console.error("Failed to load db:", e.message);
    }
    // Default values
    if (!db.chats) db.chats = {};
    if (!db.keywords || db.keywords.length === 0) {
        db.keywords = [
            { keyword: "مرحبا", response: "أهلاً بك! كيف يمكنني مساعدتك اليوم؟" },
            { keyword: "سعر", response: "سعر الاشتراك في باقتنا المميزة هو 99 دولار شهرياً شاملة الدعم الفني وتحديثات الذكاء الاصطناعي." }
        ];
    }
    if (!db.config) db.config = {};
    if (!db.config.system_prompt) db.config.system_prompt = "أنت مساعد ذكي ومحترف تجيب على رسائل الواتساب بدقة واختصار وباللغة العربية الفصحى. تجنب تماماً استخدام أي علامات تنسيق ماركداون (مثل ** أو * أو `) في إجابتك، واجعل الرد يبدو طبيعياً كرسالة واتساب عادية.";
    if (!db.config.selected_model) db.config.selected_model = "llama-3.3-70b-versatile";
    if (!db.config.tts_voice) db.config.tts_voice = "egyptian-male";
    if (db.config.temperature === undefined) db.config.temperature = 0.7;
    if (!db.config.api_keys) db.config.api_keys = { groq: "", fal: "" };
    
    // SaaS CRM additions
    if (db.config.translation_enabled === undefined) db.config.translation_enabled = false;
    if (!db.config.translation_lang) db.config.translation_lang = "ar";
    if (db.config.welcome_menu_enabled === undefined) db.config.welcome_menu_enabled = false;
    if (!db.config.welcome_menu_text) db.config.welcome_menu_text = "مرحباً بك! يرجى الرد برقم الخيار:\n1️⃣ للأسعار والخدمات\n2️⃣ لحجز موعد استشارة\n3️⃣ للتحدث مع موظف خدمة العملاء";
    if (!db.config.welcome_menu_actions) db.config.welcome_menu_actions = { 
        "1": "سعر باقة الاشتراك هو 99$ شهرياً شامل التحديثات والدعم.",
        "2": "يمكنك حجز موعد بكتابة 'احجز موعد السبت القادم الساعة 4' وسنقوم بجدولته فوراً.",
        "3": "تم إرسال طلبك للدعم البشري، سيتحدث معك أحد وكلائنا قريباً."
    };
    
    if (!db.campaign) db.campaign = { running: false, total: 0, sent: 0, failed: 0, list: [], message: "", delay: 5 };
    if (!db.appointments) db.appointments = [];
    if (!db.scheduledMessages) db.scheduledMessages = [];
}

function saveDb() {
    try {
        if (!fs.existsSync('session')) {
            fs.mkdirSync('session');
        }
        fs.writeFileSync('session/messages_db.json', JSON.stringify(db, null, 2));
    } catch (e) {
        console.error("Failed to save db:", e.message);
    }
}

function logMessage(jid, fromMe, senderName, text, translation = null) {
    if (!jid || jid.endsWith('@g.us')) return; // ignore groups or empty JIDs
    
    if (!db.chats[jid]) {
        db.chats[jid] = {
            name: senderName || jid.split('@')[0],
            phone: jid.split('@')[0],
            lastMessage: text || '',
            timestamp: new Date().toLocaleTimeString('ar-EG', { hour: '2-digit', minute: '2-digit' }),
            messages: [],
            notes: [],
            leadInfo: { name: senderName || "", email: "", phone: "", category: "general", summary: "" }
        };
    }
    db.chats[jid].lastMessage = text || '';
    db.chats[jid].timestamp = new Date().toLocaleTimeString('ar-EG', { hour: '2-digit', minute: '2-digit' });
    if (senderName) {
        db.chats[jid].name = senderName;
    }
    
    db.chats[jid].messages.push({
        id: Math.random().toString(36).substring(7),
        fromMe: fromMe,
        text: text || '',
        translation: translation,
        timestamp: new Date().toLocaleTimeString('ar-EG', { hour: '2-digit', minute: '2-digit' })
    });
    
    if (db.chats[jid].messages.length > 50) {
        db.chats[jid].messages.shift();
    }
    saveDb();
}

function checkKeywordResponders(text) {
    if (!text) return null;
    const cleanText = text.trim().toLowerCase();
    for (const item of db.keywords) {
        if (cleanText.includes(item.keyword.trim().toLowerCase())) {
            return item.response;
        }
    }
    return null;
}

let campaignInterval = null;

function startCampaign(list, message, delaySeconds) {
    if (db.campaign.running) {
        return { success: false, error: "Campaign is already running" };
    }
    
    const phoneList = list
        .split(/[,\n]/)
        .map(p => p.trim().replace(/[^0-9]/g, ''))
        .filter(p => p.length >= 8);
        
    if (phoneList.length === 0) {
        return { success: false, error: "No valid phone numbers provided" };
    }
    
    db.campaign = {
        running: true,
        total: phoneList.length,
        sent: 0,
        failed: 0,
        list: phoneList.map(num => `${num}@s.whatsapp.net`),
        message: message,
        delay: delaySeconds || 5
    };
    saveDb();
    
    addLog('info', `بدء حملة الإرسال الجماعي إلى ${db.campaign.total} جهة اتصال...`);
    
    let currentIndex = 0;
    
    async function sendNext() {
        if (!db.campaign.running) {
            if (campaignInterval) {
                clearInterval(campaignInterval);
                campaignInterval = null;
            }
            return;
        }
        
        if (currentIndex >= db.campaign.list.length) {
            db.campaign.running = false;
            saveDb();
            if (campaignInterval) {
                clearInterval(campaignInterval);
                campaignInterval = null;
            }
            addLog('success', `اكتملت حملة الإرسال الجماعي بنجاح! تم إرسال: ${db.campaign.sent}، فشل: ${db.campaign.failed}`);
            return;
        }
        
        const targetJid = db.campaign.list[currentIndex];
        const targetNumber = targetJid.split('@')[0];
        
        try {
            if (!sock || connectionState !== 'CONNECTED') {
                throw new Error("WhatsApp disconnected during campaign");
            }
            
            await sock.sendMessage(targetJid, { text: db.campaign.message });
            db.campaign.sent++;
            logMessage(targetJid, true, sock.user.name || 'مستشار المبيعات', db.campaign.message);
            addLog('success', `تم الإرسال بنجاح إلى: +${targetNumber}`);
        } catch (err) {
            console.error(`Failed to send campaign to ${targetNumber}:`, err.message);
            db.campaign.failed++;
            addLog('error', `فشل الإرسال إلى: +${targetNumber} - ${err.message}`);
        }
        
        currentIndex++;
        saveDb();
    }
    
    sendNext();
    campaignInterval = setInterval(sendNext, (delaySeconds || 5) * 1000);
    return { success: true, total: db.campaign.total };
}

function cancelCampaign() {
    if (campaignInterval) {
        clearInterval(campaignInterval);
        campaignInterval = null;
    }
    if (db.campaign.running) {
        db.campaign.running = false;
        saveDb();
        addLog('warning', "تم إيقاف حملة الإرسال الجماعي بواسطة المسؤول.");
        return { success: true };
    }
    return { success: false, error: "No active campaign to cancel" };
}

async function extractLeadInfo(jid, text) {
    const groqKey = (db.config.api_keys && db.config.api_keys.groq) || process.env.GROQ_API_KEY;
    if (!groqKey) return;
    
    try {
        console.log(`Extracting lead info for JID: ${jid}...`);
        const systemPrompt = 
            "أنت نظام استخراج بيانات ذكي ومحترف. قم بتحليل الرسالة الأخيرة للعميل واستخرج البيانات التالية بصيغة JSON فقط دون أي نصوص أخرى:\n" +
            "{\n" +
            "  \"name\": \"اسم العميل إذا تم ذكره، وإلا اتركه فارغاً\",\n" +
            "  \"email\": \"بريد العميل الإلكتروني إذا تم ذكره، وإلا اتركه فارغاً\",\n" +
            "  \"phone\": \"رقم الهاتف الإضافي إذا تم ذكره، وإلا اتركه فارغاً\",\n" +
            "  \"category\": \"تصنيف العميل: اختر قيمة واحدة فقط من (hot, warm, cold, general)\",\n" +
            "  \"summary\": \"ملخص من جملة واحدة لاحتياج العميل\"\n" +
            "}";
            
        const response = await axios.post(
            "https://api.groq.com/openai/v1/chat/completions",
            {
                model: "llama-3.1-8b-instant",
                messages: [
                    { role: "system", content: systemPrompt },
                    { role: "user", content: text }
                ],
                temperature: 0.1,
                response_format: { type: "json_object" }
            },
            {
                headers: {
                    "Authorization": `Bearer ${groqKey}`,
                    "Content-Type": "application/json"
                },
                timeout: 15000
            }
        );
        
        if (response.data && response.data.choices && response.data.choices[0]) {
            const result = JSON.parse(response.data.choices[0].message.content.trim());
            console.log("Extracted Lead Data:", result);
            
            if (!db.chats[jid]) return;
            if (!db.chats[jid].leadInfo) {
                db.chats[jid].leadInfo = { name: "", email: "", phone: "", category: "general", summary: "" };
            }
            
            if (result.name) db.chats[jid].leadInfo.name = result.name;
            if (result.email) db.chats[jid].leadInfo.email = result.email;
            if (result.phone) db.chats[jid].leadInfo.phone = result.phone;
            if (result.category) db.chats[jid].leadInfo.category = result.category;
            if (result.summary) db.chats[jid].leadInfo.summary = result.summary;
            
            const lowerText = text.toLowerCase();
            if (lowerText.includes("موعد") || lowerText.includes("احجز") || lowerText.includes("حجز") || lowerText.includes("ساعة") || lowerText.includes("يوم")) {
                detectAppointment(jid, text);
            }
            
            saveDb();
        }
    } catch (e) {
        console.error("Failed to extract lead info:", e.message);
    }
}

async function detectAppointment(jid, text) {
    const groqKey = (db.config.api_keys && db.config.api_keys.groq) || process.env.GROQ_API_KEY;
    if (!groqKey) return;
    
    try {
        const systemPrompt = 
            "أنت مساعد جدولة ذكي. قم بتحليل النص واستخرج تفاصيل الموعد بصيغة JSON فقط:\n" +
            "{\n" +
            "  \"isBooking\": true/false,\n" +
            "  \"dateTime\": \"تاريخ ووقت الموعد المستخرج باللغة العربية (مثال: غدا الساعة 5 مساء)\",\n" +
            "  \"title\": \"عنوان أو غرض الحجز (مثال: معاينة عقار أو استشارة)\"\n" +
            "}";
            
        const response = await axios.post(
            "https://api.groq.com/openai/v1/chat/completions",
            {
                model: "llama-3.1-8b-instant",
                messages: [
                    { role: "system", content: systemPrompt },
                    { role: "user", content: text }
                ],
                temperature: 0.1,
                response_format: { type: "json_object" }
            },
            {
                headers: {
                    "Authorization": `Bearer ${groqKey}`,
                    "Content-Type": "application/json"
                },
                timeout: 10000
            }
        );
        
        if (response.data && response.data.choices && response.data.choices[0]) {
            const result = JSON.parse(response.data.choices[0].message.content.trim());
            if (result.isBooking && result.dateTime) {
                if (!db.appointments) db.appointments = [];
                
                // Avoid duplicate appointments for the same JID within a close range
                const exists = db.appointments.some(app => app.jid === jid && app.dateTime === result.dateTime);
                if (!exists) {
                    db.appointments.unshift({
                        id: Math.random().toString(36).substring(7),
                        jid: jid,
                        contactName: db.chats[jid] ? db.chats[jid].name : jid.split('@')[0],
                        dateTime: result.dateTime,
                        title: result.title || "استشارة عامة"
                    });
                    
                    if (db.appointments.length > 30) db.appointments.pop();
                    
                    addLog('success', `تم جدولة موعد جديد تلقائياً لـ ${db.chats[jid].name || jid}: ${result.dateTime} - ${result.title}`);
                    saveDb();
                }
            }
        }
    } catch (err) {
        console.error("Failed to detect appointment booking:", err.message);
    }
}

// Load database immediately
loadDb();

function addLog(type, message) {
    const timestamp = new Date().toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
    liveLogs.unshift({ timestamp, type, message });
    if (liveLogs.length > 20) {
        liveLogs.pop();
    }
}

// Translation utility
async function translateText(text, targetLang = "ar") {
    const groqKey = (db.config.api_keys && db.config.api_keys.groq) || process.env.GROQ_API_KEY;
    if (!groqKey || !text) return text;
    try {
        const systemPrompt = `You are a professional translator. Translate the text input exactly into the target language "${targetLang}". Return ONLY the translated text, do not add any quotes, explanations, markdown formatting or extra text. Keep the style completely natural.`;
        const response = await axios.post(
            "https://api.groq.com/openai/v1/chat/completions",
            {
                model: "llama-3.1-8b-instant",
                messages: [
                    { role: "system", content: systemPrompt },
                    { role: "user", content: text }
                ],
                temperature: 0.1
            },
            {
                headers: {
                    "Authorization": `Bearer ${groqKey}`,
                    "Content-Type": "application/json"
                },
                timeout: 10000
            }
        );
        if (response.data && response.data.choices && response.data.choices[0]) {
            return response.data.choices[0].message.content.trim();
        }
    } catch (e) {
        console.error("Translation failed:", e.message);
    }
    return text;
}

// Scheduled follow-ups background runner
setInterval(async () => {
    if (!sock || connectionState !== 'CONNECTED') return;
    const now = Date.now();
    if (!db.scheduledMessages) db.scheduledMessages = [];
    
    const dueMessages = db.scheduledMessages.filter(msg => !msg.sent && !msg.failed && new Date(msg.sendAt).getTime() <= now);
    
    for (const msg of dueMessages) {
        try {
            await sock.sendMessage(msg.jid, { text: msg.message });
            msg.sent = true;
            logMessage(msg.jid, true, sock.user.name || 'مستشار المبيعات', msg.message);
            addLog('success', `[جدولة تلقائية] تم إرسال رسالة متابعة مجدولة إلى ${msg.contactName || msg.jid}`);
        } catch (err) {
            console.error(`Failed to send scheduled message:`, err.message);
            msg.failed = true;
            addLog('error', `فشل إرسال رسالة مجدولة لـ ${msg.jid}: ${err.message}`);
        }
    }
    if (dueMessages.length > 0) {
        // Keep active or clean old sent items
        db.scheduledMessages = db.scheduledMessages.filter(msg => !msg.sent && !msg.failed);
        saveDb();
    }
}, 10000);

// Load AI auto-reply config if exists
try {
    if (fs.existsSync('session/config.json')) {
        const configData = JSON.parse(fs.readFileSync('session/config.json', 'utf-8'));
        aiAutoReply = !!configData.ai_auto_reply;
        aiVoiceReply = !!configData.ai_voice_reply;
        if (configData.ai_transcribe_voice !== undefined) aiTranscribeVoice = !!configData.ai_transcribe_voice;
        if (configData.ai_image_vision !== undefined) aiImageVision = !!configData.ai_image_vision;
        console.log("Loaded WhatsApp bridge config, AI Auto-Reply:", aiAutoReply, "AI Voice Reply:", aiVoiceReply, "Transcribe:", aiTranscribeVoice, "Vision:", aiImageVision);
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

async function getAIResponse(senderJid, userMessage, base64Image = null) {
    const groqKey = (db.config.api_keys && db.config.api_keys.groq) || process.env.GROQ_API_KEY;
    
    if (!groqKey) {
        console.error("GROQ_API_KEY not found in environment variables or config.");
        return null;
    }

    try {
        let messages = [];
        const systemPrompt = db.config.system_prompt || "أنت مساعد ذكي ومحترف تجيب على رسائل الواتساب بدقة واختصار وباللغة العربية الفصحى. تجنب تماماً استخدام أي علامات تنسيق ماركداون (مثل ** أو * أو `) في إجابتك، واجعل الرد يبدو طبيعياً كرسالة واتساب عادية.";
        const model = base64Image ? "llama-3.2-11b-vision-preview" : (db.config.selected_model || "llama-3.3-70b-versatile");
        const temp = db.config.temperature !== undefined ? parseFloat(db.config.temperature) : 0.7;

        // 1. إضافة توجيه النظام (System Prompt)
        messages.push({
            role: "system",
            content: base64Image ? systemPrompt + " وأنت تستطيع رؤية الصور وتحليلها بدقة واختصار." : systemPrompt
        });

        // 2. تحميل الذاكرة وتاريخ المحادثة من قاعدة البيانات المحلية للـ CRM
        if (senderJid && db.chats[senderJid] && db.chats[senderJid].messages) {
            // جلب آخر 12 رسالة كحد أقصى لتمثيل الذاكرة الفعالة
            const history = db.chats[senderJid].messages;
            const historyMessages = history.slice(-12).map(m => {
                let role = m.fromMe ? "assistant" : "user";
                let textContent = m.text || "";
                
                // تنظيف رسائل الفويسات والصوتيات من الدلائل الخاصة
                let cleanContent = textContent
                    .replace(/^🎙️ \[رد صوتي\] /, "")
                    .replace(/^🎙️ \[رد صوتي تلقائي\] /, "")
                    .replace(/^🎙️ \[فويس مترجم\]: /, "");
                    
                return {
                    role: role,
                    content: cleanContent
                };
            }).filter(m => m.content.length > 0);

            messages.push(...historyMessages);
        }

        // 3. إضافة الرسالة الحالية
        if (base64Image) {
            // للتأكد من عدم تكرار الرسالة إذا كانت مسجلة بالفعل في الذاكرة بنص عادي
            if (messages.length > 0 && messages[messages.length - 1].role === "user" && typeof messages[messages.length - 1].content === "string" && messages[messages.length - 1].content === userMessage) {
                messages.pop();
            }
            
            messages.push({
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
            });
        } else {
            // إذا كانت الرسالة موجودة بالفعل كآخر رسالة في الذاكرة (تم تسجيلها عند الاستلام) فلا نقوم بتكرارها
            const hasCurrentMessageInHistory = messages.length > 0 && 
                messages[messages.length - 1].role === "user" && 
                messages[messages.length - 1].content === userMessage;
                
            if (!hasCurrentMessageInHistory) {
                messages.push({
                    role: "user",
                    content: userMessage
                });
            }
        }

        console.log(`Calling Groq API with conversation history context (Memory size: ${messages.length - 1} messages)`);

        const response = await axios.post(
            "https://api.groq.com/openai/v1/chat/completions",
            {
                model: model,
                messages: messages,
                temperature: temp
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
    
    stats.messagesReceived++;
    
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
    
    // Log incoming message to CRM database
    logMessage(senderJid, false, senderName, text);
    
    // Background AI profiling of contacts
    extractLeadInfo(senderJid, text).catch(e => console.error("Lead extraction error:", e));
    
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
        
    // await sendTelegramMessage(notification);

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
                            // Update text log in database to include transcribed text
                            logMessage(senderJid, false, senderName, `🎙️ [فويس مترجم]: ${rawText}`);
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
                    // Check static keywords responders first
                    const keywordReply = checkKeywordResponders(rawText);
                    if (keywordReply) {
                        console.log(`Keyword responder triggered for: "${rawText}" -> "${keywordReply}"`);
                        
                        try {
                            const presence = aiVoiceReply ? 'recording' : 'composing';
                            await sock.sendPresenceUpdate(presence, senderJid);
                        } catch (presenceErr) {}
                        
                        if (aiVoiceReply) {
                            try {
                                console.log("Calling Edge TTS via proxy for keyword reply...");
                                const voiceName = db.config.tts_voice || "egyptian-male";
                                const ttsUrl = `http://localhost:8090/tts?text=${encodeURIComponent(keywordReply)}&voice=${voiceName}`;
                                const ttsResp = await axios.get(ttsUrl, { responseType: 'arraybuffer' });
                                
                                const tempFilePath = `session/temp_voice_${Date.now()}.mp3`;
                                fs.writeFileSync(tempFilePath, Buffer.from(ttsResp.data));
                                
                                await sock.sendMessage(senderJid, { 
                                    audio: { url: tempFilePath }, 
                                    mimetype: 'audio/mp4', 
                                    ptt: true 
                                });
                                
                                logMessage(senderJid, true, sock.user.name || 'الرد التلقائي', `🎙️ [رد صوتي] ${keywordReply}`);
                                stats.voiceRepliesSent++;
                                
                                try {
                                    fs.unlinkSync(tempFilePath);
                                } catch (cleanErr) {}
                            } catch (ttsErr) {
                                console.error("TTS generation for keyword failed, falling back to text:", ttsErr.message);
                                await sock.sendMessage(senderJid, { text: keywordReply });
                                logMessage(senderJid, true, sock.user.name || 'الرد التلقائي', keywordReply);
                                stats.repliesSent++;
                            }
                        } else {
                            await sock.sendMessage(senderJid, { text: keywordReply });
                            logMessage(senderJid, true, sock.user.name || 'الرد التلقائي', keywordReply);
                            stats.repliesSent++;
                        }
                        return; // Stop here, do not run AI
                    }
                    
                    // Get AI response if no keyword matched
                    try {
                        const presence = aiVoiceReply ? 'recording' : 'composing';
                        await sock.sendPresenceUpdate(presence, senderJid);
                    } catch (presenceErr) {}
                    
                    const aiResponse = await getAIResponse(senderJid, rawText, base64Image);
                    if (aiResponse) {
                        if (aiVoiceReply) {
                            try {
                                console.log("Calling Edge TTS via proxy...");
                                const voiceName = db.config.tts_voice || "egyptian-male";
                                const ttsUrl = `http://localhost:8090/tts?text=${encodeURIComponent(aiResponse)}&voice=${voiceName}`;
                                const ttsResp = await axios.get(ttsUrl, { responseType: 'arraybuffer' });
                                
                                const tempFilePath = `session/temp_voice_${Date.now()}.mp3`;
                                fs.writeFileSync(tempFilePath, Buffer.from(ttsResp.data));
                                
                                await sock.sendMessage(senderJid, { 
                                    audio: { url: tempFilePath }, 
                                    mimetype: 'audio/mp4', 
                                    ptt: true 
                                });
                                console.log(`Auto-replied to ${senderJid} with AI Voice message.`);
                                
                                logMessage(senderJid, true, sock.user.name || 'الرد الذكي', `🎙️ [رد صوتي] ${aiResponse}`);
                                stats.voiceRepliesSent++;
                                
                                try {
                                    fs.unlinkSync(tempFilePath);
                                } catch (cleanErr) {}
                            } catch (ttsErr) {
                                console.error("TTS generation or sending failed, falling back to text:", ttsErr.message);
                                await sock.sendMessage(senderJid, { text: aiResponse });
                                logMessage(senderJid, true, sock.user.name || 'الرد الذكي', aiResponse);
                                stats.repliesSent++;
                            }
                        } else {
                            await sock.sendMessage(senderJid, { text: aiResponse });
                            console.log(`Auto-replied to ${senderJid} with AI text response.`);
                            
                            logMessage(senderJid, true, sock.user.name || 'الرد الذكي', aiResponse);
                            stats.repliesSent++;
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
        
        if (connection === 'connecting') {
            connectionState = 'CONNECTING';
            addLog('info', 'جاري محاولة الاتصال بسيرفرات واتساب...');
        }
        
        if (qr) {
            qrCode = qr;
            addLog('info', 'تم توليد رمز QR جديد لربط الحساب');
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
            profilePicUrl = null;
            addLog('warning', `تم إغلاق الاتصال. إعادة المحاولة: ${shouldReconnect}`);
            if (shouldReconnect) {
                if (!reconnectTimeout) {
                    reconnectTimeout = setTimeout(() => {
                        reconnectTimeout = null;
                        initWhatsApp(telegramChatId);
                    }, 5000);
                }
            } else {
                console.log("Logged out from WhatsApp. Clearing session credentials.");
                addLog('error', 'تم تسجيل الخروج من جهاز الواتساب. جاري مسح ملفات الجلسة.');
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
            const wasConnected = connectionState === 'CONNECTED';
            connectionState = 'CONNECTED';
            qrCode = null;
            
            const myName = sock.user.name || 'مستخدم واتساب';
            const myPhone = sock.user.id.split(':')[0].split('@')[0];
            addLog('success', `تم ربط الحساب وفتح الاتصال بنجاح! الحساب المرتبط: ${myName} (${myPhone})`);
            
            // Fetch profile picture URL asynchronously
            try {
                sock.profilePictureUrl(sock.user.id, 'image')
                    .then(url => { 
                        profilePicUrl = url;
                        addLog('info', 'تم تحميل الصورة الشخصية للحساب بنجاح.');
                    })
                    .catch(err => { profilePicUrl = null; });
            } catch (e) {
                profilePicUrl = null;
            }
            
            if (!wasConnected) {
                sendTelegramMessage("✅ <b>تم ربط واتساب بنجاح!</b>\nالبوت متصل الآن ويمكنه استقبال وإرسال رسائل واتساب.");
            }
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
    const user = sock && sock.user ? {
        name: sock.user.name || 'حساب واتساب',
        phone: sock.user.id.split(':')[0].split('@')[0],
        avatar: profilePicUrl
    } : null;

    res.json({
        state: connectionState,
        qr: qrCode ? true : false,
        user: user,
        stats: stats,
        logs: liveLogs
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
        logMessage(to, true, sock.user.name, message); // Log it to DB
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
        ai_voice_reply: aiVoiceReply,
        ai_transcribe_voice: aiTranscribeVoice,
        ai_image_vision: aiImageVision,
        system_prompt: db.config.system_prompt,
        selected_model: db.config.selected_model,
        tts_voice: db.config.tts_voice,
        temperature: db.config.temperature,
        api_keys: db.config.api_keys,
        translation_enabled: db.config.translation_enabled,
        translation_lang: db.config.translation_lang,
        welcome_menu_enabled: db.config.welcome_menu_enabled,
        welcome_menu_text: db.config.welcome_menu_text,
        welcome_menu_actions: db.config.welcome_menu_actions
    });
});

app.post('/config', (req, res) => {
    const { 
        ai_auto_reply, 
        ai_voice_reply, 
        ai_transcribe_voice, 
        ai_image_vision,
        system_prompt,
        selected_model,
        tts_voice,
        temperature,
        api_keys,
        translation_enabled,
        translation_lang,
        welcome_menu_enabled,
        welcome_menu_text,
        welcome_menu_actions
    } = req.body;
    
    if (ai_auto_reply !== undefined) aiAutoReply = !!ai_auto_reply;
    if (ai_voice_reply !== undefined) aiVoiceReply = !!ai_voice_reply;
    if (ai_transcribe_voice !== undefined) aiTranscribeVoice = !!ai_transcribe_voice;
    if (ai_image_vision !== undefined) aiImageVision = !!ai_image_vision;
    
    if (system_prompt !== undefined) db.config.system_prompt = system_prompt;
    if (selected_model !== undefined) db.config.selected_model = selected_model;
    if (tts_voice !== undefined) db.config.tts_voice = tts_voice;
    if (temperature !== undefined) db.config.temperature = temperature;
    if (api_keys !== undefined) db.config.api_keys = api_keys;
    if (translation_enabled !== undefined) db.config.translation_enabled = !!translation_enabled;
    if (translation_lang !== undefined) db.config.translation_lang = translation_lang;
    if (welcome_menu_enabled !== undefined) db.config.welcome_menu_enabled = !!welcome_menu_enabled;
    if (welcome_menu_text !== undefined) db.config.welcome_menu_text = welcome_menu_text;
    if (welcome_menu_actions !== undefined) db.config.welcome_menu_actions = welcome_menu_actions;
    
    try {
        if (!fs.existsSync('session')) {
            fs.mkdirSync('session');
        }
        fs.writeFileSync('session/config.json', JSON.stringify({
            ai_auto_reply: aiAutoReply,
            ai_voice_reply: aiVoiceReply,
            ai_transcribe_voice: aiTranscribeVoice,
            ai_image_vision: aiImageVision
        }, null, 2));
        
        saveDb();
        
        res.json({ 
            success: true, 
            ai_auto_reply: aiAutoReply, 
            ai_voice_reply: aiVoiceReply,
            ai_transcribe_voice: aiTranscribeVoice,
            ai_image_vision: aiImageVision,
            system_prompt: db.config.system_prompt,
            selected_model: db.config.selected_model,
            tts_voice: db.config.tts_voice,
            temperature: db.config.temperature,
            api_keys: db.config.api_keys,
            translation_enabled: db.config.translation_enabled,
            translation_lang: db.config.translation_lang,
            welcome_menu_enabled: db.config.welcome_menu_enabled,
            welcome_menu_text: db.config.welcome_menu_text,
            welcome_menu_actions: db.config.welcome_menu_actions
        });
    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

// New premium API endpoints
app.get('/chats', (req, res) => {
    const chatsList = Object.keys(db.chats).map(jid => ({
        jid: jid,
        name: db.chats[jid].name,
        phone: db.chats[jid].phone,
        lastMessage: db.chats[jid].lastMessage,
        timestamp: db.chats[jid].timestamp,
        leadInfo: db.chats[jid].leadInfo || null,
        notes: db.chats[jid].notes || [],
        state: db.chats[jid].state || ''
    }));
    res.json(chatsList);
});

// SaaS CRM endpoints
app.post('/chats/:jid/lead-category', (req, res) => {
    const jid = req.params.jid;
    const { category } = req.body;
    if (db.chats[jid]) {
        if (!db.chats[jid].leadInfo) {
            db.chats[jid].leadInfo = { name: db.chats[jid].name || "", email: "", phone: db.chats[jid].phone || "", category: "general", summary: "" };
        }
        db.chats[jid].leadInfo.category = category;
        saveDb();
        res.json({ success: true, leadInfo: db.chats[jid].leadInfo });
    } else {
        res.status(404).json({ error: "Chat not found" });
    }
});

app.post('/chats/:jid/notes', (req, res) => {
    const jid = req.params.jid;
    const { note } = req.body;
    if (db.chats[jid]) {
        if (!db.chats[jid].notes) db.chats[jid].notes = [];
        db.chats[jid].notes.push({
            id: Math.random().toString(36).substring(7),
            text: note,
            timestamp: new Date().toLocaleString('ar-EG')
        });
        saveDb();
        res.json({ success: true, notes: db.chats[jid].notes });
    } else {
        res.status(404).json({ error: "Chat not found" });
    }
});

app.get('/chats/:jid/notes', (req, res) => {
    const jid = req.params.jid;
    if (db.chats[jid]) {
        res.json(db.chats[jid].notes || []);
    } else {
        res.json([]);
    }
});

app.get('/scheduled-messages', (req, res) => {
    res.json(db.scheduledMessages || []);
});

app.post('/scheduled-messages', (req, res) => {
    const { jid, message, sendAt } = req.body;
    if (!db.scheduledMessages) db.scheduledMessages = [];
    const newMsg = {
        id: Math.random().toString(36).substring(7),
        jid,
        contactName: db.chats[jid] ? db.chats[jid].name : jid.split('@')[0],
        message,
        sendAt,
        sent: false,
        failed: false
    };
    db.scheduledMessages.push(newMsg);
    saveDb();
    res.json({ success: true, scheduledMessage: newMsg });
});

app.post('/scheduled-messages/delete', (req, res) => {
    const { id } = req.body;
    if (db.scheduledMessages) {
        db.scheduledMessages = db.scheduledMessages.filter(msg => msg.id !== id);
        saveDb();
        res.json({ success: true });
    } else {
        res.status(400).json({ error: "No scheduled messages" });
    }
});

app.post('/chats/:jid/send-voice', async (req, res) => {
    const jid = req.params.jid;
    const { text, voice } = req.body;
    if (!sock || connectionState !== 'CONNECTED') {
        return res.status(400).json({ error: 'WhatsApp not connected' });
    }
    try {
        const voiceName = voice || db.config.tts_voice || "egyptian-male";
        const ttsUrl = `http://localhost:8090/tts?text=${encodeURIComponent(text)}&voice=${voiceName}`;
        const ttsResp = await axios.get(ttsUrl, { responseType: 'arraybuffer' });
        
        const tempFilePath = `session/temp_voice_${Date.now()}.mp3`;
        fs.writeFileSync(tempFilePath, Buffer.from(ttsResp.data));
        
        await sock.sendMessage(jid, { 
            audio: { url: tempFilePath }, 
            mimetype: 'audio/mp4', 
            ptt: true 
        });
        
        logMessage(jid, true, sock.user.name || 'مستشار المبيعات', `🎙️ [رد صوتي] ${text}`);
        stats.voiceRepliesSent++;
        
        try {
            fs.unlinkSync(tempFilePath);
        } catch (cleanErr) {}
        
        res.json({ success: true });
    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

app.get('/chats/:jid/messages', (req, res) => {
    const jid = req.params.jid;
    if (db.chats[jid]) {
        res.json(db.chats[jid].messages);
    } else {
        res.json([]);
    }
});

app.post('/chats/:jid/send', async (req, res) => {
    const jid = req.params.jid;
    const { message } = req.body;
    
    if (!sock || connectionState !== 'CONNECTED') {
        return res.status(400).json({ error: 'WhatsApp not connected' });
    }
    
    try {
        await sock.sendMessage(jid, { text: message });
        logMessage(jid, true, sock.user.name || 'مستشار المبيعات', message);
        res.json({ success: true });
    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

app.get('/keywords', (req, res) => {
    res.json(db.keywords);
});

app.post('/keywords', (req, res) => {
    const { keywords } = req.body;
    if (Array.isArray(keywords)) {
        db.keywords = keywords;
        saveDb();
        res.json({ success: true, keywords: db.keywords });
    } else {
        res.status(400).json({ error: "Invalid keywords format" });
    }
});

app.post('/campaign/start', (req, res) => {
    const { list, message, delay } = req.body;
    const result = startCampaign(list, message, delay);
    if (result.success) {
        res.json(result);
    } else {
        res.status(400).json(result);
    }
});

app.get('/campaign/status', (req, res) => {
    res.json(db.campaign);
});

app.post('/campaign/cancel', (req, res) => {
    const result = cancelCampaign();
    if (result.success) {
        res.json(result);
    } else {
        res.status(400).json(result);
    }
});

app.get('/analytics', (req, res) => {
    const totalChats = Object.keys(db.chats).length;
    let hot = 0, warm = 0, cold = 0, general = 0;
    
    Object.keys(db.chats).forEach(jid => {
        const cat = (db.chats[jid].leadInfo && db.chats[jid].leadInfo.category) || 'general';
        if (cat === 'hot') hot++;
        else if (cat === 'warm') warm++;
        else if (cat === 'cold') cold++;
        else general++;
    });
    
    res.json({
        totalChats,
        leads: { hot, warm, cold, general },
        totalAppointments: db.appointments ? db.appointments.length : 0,
        totalCampaignsSent: db.campaign ? db.campaign.sent : 0
    });
});

app.get('/appointments', (req, res) => {
    res.json(db.appointments || []);
});

app.post('/appointments', (req, res) => {
    const { jid, dateTime, title } = req.body;
    if (!db.appointments) db.appointments = [];
    const newApp = {
        id: Math.random().toString(36).substring(7),
        jid: jid || 'manual',
        contactName: jid && db.chats[jid] ? db.chats[jid].name : 'عميل خارجي',
        dateTime,
        title: title || 'استشارة مخصصة'
    };
    db.appointments.unshift(newApp);
    saveDb();
    res.json({ success: true, appointment: newApp });
});

app.post('/appointments/delete', (req, res) => {
    const { id } = req.body;
    if (db.appointments) {
        db.appointments = db.appointments.filter(app => app.id !== id);
        saveDb();
        res.json({ success: true });
    } else {
        res.status(400).json({ error: "No appointments database" });
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

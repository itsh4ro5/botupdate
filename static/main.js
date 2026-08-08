// ==========================================
// CORE STREAMING ENGINE (GramJS)
// ==========================================

async function initStreaming() {
    const config = window.TG_CONFIG;
    if (!config || !config.chatId || !config.msgId) {
        console.log('⚠️ [Main] Missing video parameters. Engine Sleeping.');
        return;
    }

    const setPlayerStatus = window.setPlayerStatus || console.log;

    if ('serviceWorker' in navigator) {
        try {
            await navigator.serviceWorker.register('/sw.js?v=30');
            await navigator.serviceWorker.ready;
            console.log('✅ [Main] Service Worker Active');
        } catch (err) {
            console.error('❌ [Main] SW Reg Failed:', err);
        }
    }

    try {
        setPlayerStatus("Checking Secure Session...");
        const res = await fetch(`/api/session/get/${config.userId}`);
        const data = await res.json();
        
        if (data.success && data.session) {
            setPlayerStatus("Connecting to Telegram...");
            await connectAndStream(data.session, config);
        } else {
            setPlayerStatus("🔒 Session Required. Login via Bot.", true);
        }
    } catch (e) { 
        setPlayerStatus("Network Error. Please Login.", true);
    }
}

async function connectAndStream(sessionStr, config) {
    const { TelegramClient } = window.telegram;
    const { StringSession } = window.telegram.sessions;
    const bigIntFn = window.bigInt; 

    const session = new StringSession(sessionStr);
    const DC_HOSTNAMES = {
        1: 'pluto.web.telegram.org', 2: 'venus.web.telegram.org',
        3: 'aurora.web.telegram.org', 4: 'vesta.web.telegram.org', 5: 'flora.web.telegram.org'
    };
    
    const originalSetDC = session.setDC.bind(session);
    session.setDC = (dcId, serverAddress, port, downloadDC) => {
        return originalSetDC(dcId, DC_HOSTNAMES[dcId] || serverAddress, port, downloadDC);
    };
    if (session.dcId && DC_HOSTNAMES[session.dcId]) {
        session.setDC(session.dcId, DC_HOSTNAMES[session.dcId], 443);
    }

    let client = new TelegramClient(session, config.apiId, config.apiHash, { connectionRetries: 5, useWSS: true });
    
    try {
        await client.connect();
        console.log('✅ [Main] Connected to Telegram DC!');
    } catch (err) {
        window.setPlayerStatus("Session Expired. Please Re-login via Bot.", true);
        return;
    }

    window.setPlayerStatus("Extracting Video...");
    
    const messages = await client.getMessages(config.chatId, { ids: config.msgId });
    const media = messages[0]?.media;

    if (!media || !media.document) {
        window.setPlayerStatus("Error: Video not found or deleted.", true);
        return;
    }
    
    const fileSize = Number(media.document.size);
    const activeStreams = new Set();

    navigator.serviceWorker.addEventListener('message', async (event) => {
        if (event.data.type === 'CANCEL_STREAM') {
            activeStreams.delete(event.data.streamId);
            return;
        }

        if (event.data.type === 'FETCH_RANGE') {
            const { range, streamId } = event.data;
            activeStreams.add(streamId);
            const port = event.ports[0];

            const parts = range.replace(/bytes=/, "").split("-");
            const reqStart = parseInt(parts[0], 10) || 0;
            const reqEnd = parts[1] ? parseInt(parts[1], 10) : fileSize - 1;

            const ALIGNMENT = 512 * 1024; 
            const tgStart = Math.floor(reqStart / ALIGNMENT) * ALIGNMENT;
            const tgEnd = Math.ceil((reqEnd + 1) / ALIGNMENT) * ALIGNMENT - 1;
            const tgLimit = tgEnd - tgStart + 1;

            port.postMessage({ type: 'METADATA', start: reqStart, end: reqEnd, totalSize: fileSize });

            try {
                let currentOffset = reqStart;
                let isFirstChunk = true;
                let currentMedia = media;

                while (currentOffset <= reqEnd) {
                    if (!activeStreams.has(streamId)) break;
                    
                    const alignedStart = Math.floor(currentOffset / ALIGNMENT) * ALIGNMENT;
                    const alignedEnd = Math.ceil((reqEnd + 1) / ALIGNMENT) * ALIGNMENT - 1;
                    const alignedLimit = alignedEnd - alignedStart + 1;

                    try {
                        for await (const chunk of client.iterDownload({
                            file: currentMedia, 
                            offset: bigIntFn(alignedStart), 
                            limit: bigIntFn(alignedLimit), 
                            requestSize: ALIGNMENT
                        })) {
                            if (!activeStreams.has(streamId)) break; 

                            let bufferToSend = chunk;
                            if (isFirstChunk) {
                                const skipBytes = currentOffset - alignedStart;
                                if (skipBytes > 0) bufferToSend = chunk.slice(skipBytes);
                                isFirstChunk = false;
                            }

                            const maxRemaining = (reqEnd - currentOffset) + 1;
                            if (bufferToSend.length > maxRemaining) {
                                bufferToSend = bufferToSend.slice(0, maxRemaining);
                            }

                            if (bufferToSend.length > 0) {
                                const arrayBuffer = new Uint8Array(bufferToSend).buffer;
                                port.postMessage({ type: 'CHUNK', buffer: arrayBuffer }, [arrayBuffer]);
                                currentOffset += bufferToSend.length;
                            }

                            if (currentOffset > reqEnd) break;
                        }
                        break; 
                    } catch (err) {
                        if (err.message && (err.message.includes('FILE_REFERENCE_EXPIRED') || err.message.includes('FILE_REFERENCE_EMPTY'))) {
                            console.warn("🔄 File Reference Expired Mid-Stream! Auto-refreshing fresh link...");
                            const freshMsgs = await client.getMessages(config.chatId, { ids: config.msgId });
                            currentMedia = freshMsgs[0]?.media;
                            if (!currentMedia) throw new Error("Could not refresh media link.");
                            isFirstChunk = true; 
                            continue; 
                        } else {
                            throw err;
                        }
                    }
                } 

                if (activeStreams.has(streamId)) {
                    port.postMessage({ type: 'DONE' });
                    activeStreams.delete(streamId);
                }
            } catch (err) {
                if (err.message && !err.message.includes('LIMIT_INVALID')) console.error("Stream Error:", err.message);
                port.postMessage({ type: 'ERROR', error: err.message });
                activeStreams.delete(streamId);
            }
        }
    });

    const videoElement = document.getElementById('video-player');
    videoElement.src = `/tg-stream/video_${config.msgId}.mp4`;
    console.log('🚀 [Main] Native Live Stream URL assigned!');
    
    videoElement.addEventListener('canplay', () => {
        document.getElementById('player-status').classList.add('hidden');
        videoElement.play(); 
    });
}

// Smart Poller: Waits for GramJS and BigInt to download before booting engine
let loadAttempts = 0;
function bootEngine() {
    if (typeof window.telegram !== 'undefined' && typeof window.bigInt !== 'undefined') {
        initStreaming();
    } else {
        loadAttempts++;
        if (loadAttempts > 40) {
            window.setPlayerStatus("Network Timeout. Please refresh.", true);
        } else {
            setTimeout(bootEngine, 500);
        }
    }
}
window.addEventListener('load', bootEngine);

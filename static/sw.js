// public/sw.js
self.addEventListener('install', (e) => self.skipWaiting());
self.addEventListener('activate', (e) => e.waitUntil(self.clients.claim()));

self.addEventListener('fetch', (event) => {
    const url = new URL(event.request.url);
    if (url.pathname.startsWith('/tg-stream')) {
        event.respondWith(handleStream(event.request));
    }
});

async function handleStream(request) {
    const range = request.headers.get('Range') || 'bytes=0-';
    const clientsArr = await clients.matchAll({ type: 'window', includeUncontrolled: true });
    const client = clientsArr[0];
    
    if (!client) return new Response('No client', { status: 500 });

    const streamId = Math.random().toString(36).substring(2, 10);
    const mc = new MessageChannel();

    return new Promise((resolve) => {
        mc.port1.onmessage = (e) => {
            if (e.data.type === 'METADATA') {
                const { start, end, totalSize } = e.data;
                
                const stream = new ReadableStream({
                    start(controller) {
                        mc.port1.onmessage = (msg) => {
                            if (msg.data.type === 'CHUNK') controller.enqueue(new Uint8Array(msg.data.buffer));
                            else if (msg.data.type === 'DONE') controller.close();
                            else if (msg.data.type === 'ERROR') controller.error(msg.data.error);
                        };
                    },
                    cancel() {
                        client.postMessage({ type: 'CANCEL_STREAM', streamId });
                    }
                });

                resolve(new Response(stream, {
                    status: 206,
                    headers: {
                        'Content-Type': 'video/mp4',
                        'Accept-Ranges': 'bytes',
                        'Content-Range': `bytes ${start}-${end}/${totalSize}`,
                        'Content-Length': (end - start + 1).toString(),
                        'Cache-Control': 'no-store'
                    }
                }));
            }
        };
        client.postMessage({ type: 'FETCH_RANGE', range, streamId }, [mc.port2]);
    });
}

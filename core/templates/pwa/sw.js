// Service worker de Fresh Analytics (generado por la vista service_worker).
//
// Alcance deliberadamente mínimo: NO guarda en caché ninguna pantalla con
// datos (todas son privadas y cambian a diario; además llevan never_cache).
// Solo guarda la pantalla "Sin conexión" para mostrarla si se cae la red,
// en vez del error genérico del navegador. Todo lo demás va a la red.
const VERSION = "{{ version }}";
const URL_SIN_CONEXION = "{{ url_sin_conexion }}";

self.addEventListener("install", (evento) => {
    evento.waitUntil(
        caches.open(VERSION).then((cache) => cache.add(new Request(URL_SIN_CONEXION, { cache: "reload" })))
    );
    self.skipWaiting();
});

self.addEventListener("activate", (evento) => {
    // Borra las cachés de versiones anteriores.
    evento.waitUntil(
        caches.keys().then((nombres) =>
            Promise.all(nombres.filter((n) => n !== VERSION).map((n) => caches.delete(n)))
        )
    );
    self.clients.claim();
});

self.addEventListener("fetch", (evento) => {
    // Solo intervenimos en la navegación entre pantallas: si la red falla,
    // mostramos la pantalla "Sin conexión" guardada.
    if (evento.request.mode !== "navigate") return;
    evento.respondWith(
        fetch(evento.request).catch(() => caches.match(URL_SIN_CONEXION))
    );
});

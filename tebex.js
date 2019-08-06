// change to your vipps client id
const CLIENT_ID = '';
const API_BASE = '';

(async () => {
    const orderId = '{REFERENCE}';
    const r = await fetch(`${API_BASE}/payments`, {
        method: 'POST',
        headers: {
            'Accept': 'application/json',
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({
            client_id: CLIENT_ID,
            order_id: orderId
        })
    });

    const data = await r.json();
    window.location.replace(data.url)
})();
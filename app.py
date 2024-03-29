import aiohttp
import asyncio
import datetime
import json

from sanic import Sanic
from sanic import response
from sanic.exceptions import NotFound
from sanic_cors import CORS

BASE = 'https://api.vipps.no'
TEBEX_BASE = 'https://plugin.buycraft.net'

with open('config.json', 'r') as f:
    raw = json.loads(f.read())
    API_BASE = raw['api_base']

test_environment_enabled = (raw.get('test_environment', {})).get('enabled', False)
if test_environment_enabled:
    BASE = 'https://apitest.vipps.no'

app = Sanic()
CORS(app, automatic_options=True)

class VippsException(Exception):
    pass

class User:
    def __init__(self, app, email, data):
        self.app = app
        self.email = email
        self.client_id = data['client_id']
        self.client_secret = data['client_secret']
        self.subscription_key = data['subscription_key']
        self.merchant_serial_number = data['merchant_serial_number']
        self.tebex_secret = data['tebex_secret']
        
        self.order_processing_futures = {}

    async def fetch_access_token(self, future=None):
        headers = {
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'Ocp-Apim-Subscription-Key': self.subscription_key
        }

        async with app.client_session.post(f'{BASE}/accessToken/get', headers=headers) as r:
            if r.status != 200:
                text = await r.text()
                if future is not None:
                    return future.set_exception(
                        TypeError(
                            f'Something went wrong with {self.email}. API resp: {text}')
                    )
                else:
                    raise TypeError(
                        f'Something went wrong with {self.email}. API resp: {text}')

            data = await r.json()
            self.set_values(data)

            if future is not None:
                future.set_result(None)
            await self.run_refresh_waiter()

    def set_values(self, data):
        self.token_type = data['token_type']
        self.expires_in = data['expires_in']
        self.ext_expires_in = data['ext_expires_in']
        self.expires_on = data['expires_on']
        self.not_before = data['not_before']
        self.resource = data['resource']
        self.access_token = data['access_token']

    async def run_refresh_waiter(self):
        await asyncio.sleep(int(self.expires_in) - 300)
        await self.fetch_access_token()

    async def setup(self):
        future = self.app.loop.create_future()
        self.app.loop.create_task(self.fetch_access_token(future=future))
        await future

        async with self.app.client_session.get(f'{TEBEX_BASE}/information', headers={
            'X-Buycraft-Secret': self.tebex_secret
        }) as r:
            self.tebex_information = await r.json()

    async def init_payment(self, order_id, amount, text, phone_number=None):
        headers = {
            'Ocp-Apim-Subscription-Key': self.subscription_key,
            'Authorization': f'Bearer {self.access_token}'
        }

        body = {
            "customerInfo": {
                "mobileNumber": phone_number or ''
            },
            "merchantInfo": {
                "callbackPrefix": f'{API_BASE}/{self.client_id}',
                "fallBack": f'{API_BASE}/{self.client_id}/{order_id}/redirect',
                "isApp": False,
                "merchantSerialNumber": self.merchant_serial_number,
                "paymentType": "eComm Regular Payment"
            },
            "transaction": {
                "amount": amount,
                "orderId": order_id,
                "timeStamp": datetime.datetime.now().isoformat(),
                "transactionText": text
            }
        }

        async with self.app.client_session.post(
            f'{BASE}/ecomm/v2/payments',
            headers=headers,
            json=body
        ) as r:
            if r.status != 200:
                raise VippsException(
                    f'Could not initiate a payment. Client id: {self.client_id} | Order id: {order_id}')

            return await r.json()

    async def fetch_vipps_payment_status(self, order_id):
        async with self.app.client_session.get(f'{BASE}/ecomm/v2/payments/{order_id}/status', headers={
            'orderId': order_id,
            'Authorization': f'Bearer {self.access_token}',
            'Ocp-Apim-Subscription-Key': self.subscription_key
        }) as r:
            return await r.json()

    async def fetch_tebex_payment(self, order_id):
        async with self.app.client_session.get(f'{TEBEX_BASE}/payments/{order_id}', headers={
            'X-Buycraft-Secret': self.tebex_secret
        }) as r:
            return await r.json()

    async def confirm_tebex_payment(self, order_id):
        async with self.app.client_session.put(
            f'{TEBEX_BASE}/payments/{order_id}',
            headers={
                'X-Buycraft-Secret': self.tebex_secret
            },
            json={
                'status': 'complete'
            }
        ) as r:
            return r.status == 204

    async def capture_vipps_payment(self, order_id):
        async with self.app.client_session.post(
            f'{BASE}/ecomm/v2/payments/{order_id}/capture',
            headers={
                'orderId': order_id,
                'Authorization': f'Bearer {self.access_token}',
                'Ocp-Apim-Subscription-Key': self.subscription_key
            },
            json={
                "merchantInfo": {
                    "merchantSerialNumber": self.merchant_serial_number
                },
                "transaction": {
                    "amount": 0,
                    "transactionText": f'Ditt kjøp hos {self.tebex_information["account"]["name"]} er nå fullført.'
                }
            }
        ) as r:
            if r.status != 200:
                print(f'Could not capture order {order_id} for provider {self.tebex_information["account"]["name"]}')
            

    async def process_order(self, order_id):
        if order_id not in self.order_processing_futures.keys():
            self.order_processing_futures[order_id] = self.app.loop.create_future()
        
        val = False
        status = await self.fetch_vipps_payment_status(order_id)
        if status['transactionInfo']['status'] == 'RESERVE':
            res = await self.confirm_tebex_payment(order_id)
            if res is True:
                val = True

        self.order_processing_futures[order_id].set_result(val)
        self.app.loop.create_task(self.delete_after(order_id, seconds=10))

    async def await_order_processing(self, order_id):
        if order_id not in self.order_processing_futures.keys():
            self.order_processing_futures[order_id] = self.app.loop.create_future()

        result = False
        try:
            result = await asyncio.wait_for(self.order_processing_futures[order_id], timeout=5)
        except asyncio.TimeoutError:
            pass

        return result

    async def delete_after(self, order_id, seconds):
        await asyncio.sleep(seconds)
        try:
            del self.order_processing_futures[order_id]
        except KeyError:
            pass

@app.listener('before_server_start')
async def before_server_start(app, loop):
    app.client_session = aiohttp.ClientSession(loop=loop)

    tasks = []
    users = {}

    raw_users = raw['users'] if test_environment_enabled is False else raw['test_environment']['users']

    for email, data in raw_users.items():
        user = User(app, email, data)
        users[user.client_id] = user

        tasks.append(app.loop.create_task(user.setup()))

    await asyncio.wait(tasks)
    app.users = users

@app.listener('before_server_stop')
async def before_server_stop(app, loop):
    await app.client_session.close()

@app.route('/payments', methods=['POST'])
async def init_payment(request):
    client_id = request.json['client_id']
    order_id = request.json['order_id']

    user = app.users[client_id]
    tebex_payment = await user.fetch_tebex_payment(order_id)
    if tebex_payment['status'] != 'Pending Capture':
        return response.json(
            {'error_message': 'Invalid payment state.'},
            status=400
        )

    if tebex_payment['currency']['iso_4217'] != 'NOK':
        return response.json(
            {'error_message': 'Only payments in NOK is accepted.'},
            status=403
        )

    res = await user.init_payment(
        order_id,
        tebex_payment['amount'].replace('.', ''),
        f'Betaling til {user.tebex_information["account"]["name"]}.'
    )

    return response.json(
        res,
        status=200
    )

@app.route(f'/<client_id>/v2/payments/<order_id>', methods=['POST'])
async def purchase_callback(request, client_id, order_id):
    user = app.users[client_id]
    await user.process_order(order_id)

    return response.text('', status=204)

@app.route(f'/<client_id>/<order_id>/redirect', methods=['GET'])
async def purchase_redirect(request, client_id, order_id):
    user = app.users[client_id]

    res = await user.await_order_processing(order_id)

    app.loop.create_task(user.capture_vipps_payment(order_id))

    if res is True:
        return response.redirect(f"{user.tebex_information['account']['domain']}/checkout/complete")
    return response.redirect(f"{user.tebex_information['account']['domain']}/checkout/error")

@app.route('/robots.txt', methods=['GET'])
async def robots(request):
    return await response.file(raw['robots_path'])

@app.exception(NotFound)
async def ingore_404(request, exception):
    return response.json({'error_message': 'Requested URL not found.'}, status=404)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)

# Tebex Vipps Gateway
Tebex vipps gateway made for MCHost and their users by Terbau.

## Backend Steps
1. Enter all necessary information in config.json about users and the base of the API.
2. Change API_BASE in tebex.js to the base of the API.
3. Run `py -3 app.py` in console to start the app.

## Frontend Steps (What tebex admins must do)
First of all the admin must contact MCHost and ask them to be added to config.json

1. Navigate to tebex.io dashboard for your server.
2. Payments -> Payment Gateways -> Setup Payment Gateway -> Manual Payments
3. Enter all necessary information and paste the code from tebex.js (WITH THE CORRECT VIPPS CLIENT ID) into the javascript box.
4. Save.

## Requirements
Python 3.6+

- aiohttp
- sanic
- sanic-cors
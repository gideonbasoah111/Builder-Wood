# Builder Wood — FastAPI starter

A starter web shop for construction timber. Includes a product catalogue, basic product/vehicle/order APIs, and initial vehicle allocation logic. It is not yet production-ready: authentication, secure admin permissions, payment integration, route-based distance calculation, and a complete cart/checkout flow still need to be implemented.

## Run in PyCharm on Windows

1. Extract the ZIP and open the `builder_wood` folder in PyCharm.
2. Create/select a Python 3.11 or 3.12 virtual environment.
3. Open the PyCharm terminal in the project root and run:

   ```powershell
   python -m pip install -r requirements.txt
   ```

4. Start the server:

   ```powershell
   python -m uvicorn app.main:app --reload
   ```

5. Open `http://127.0.0.1:8000` for the shop, or `http://127.0.0.1:8000/docs` to test the API.

The first run uses a local SQLite database so it can start without MySQL setup. Demo products and vehicle values are inserted automatically. Replace all demo prices/capacities before real use.

## Use MySQL

1. In XAMPP, start MySQL and create a database named `builder_wood` using phpMyAdmin.
2. Set an environment variable in the PyCharm run configuration or terminal, e.g.:

   ```powershell
   $env:DATABASE_URL="mysql+pymysql://root:YOUR_PASSWORD@127.0.0.1:3306/builder_wood"
   python -m uvicorn app.main:app --reload
   ```

   Replace `YOUR_PASSWORD` with your actual MySQL password. If the MySQL account has no password, use an empty password segment, for example `mysql+pymysql://root:@127.0.0.1:3306/builder_wood`.

## API endpoints

- `GET /api/products` — list products
- `GET /api/products?search=timber` — search products
- `POST /api/products` — add a product (currently not protected; do not expose publicly)
- `GET /api/vehicles` — list configured vehicles
- `POST /api/vehicles` — add a vehicle (currently not protected)
- `POST /api/orders` — place a simple single-product order
- `GET /docs` — interactive API documentation

Example order JSON:

```json
{
  "customer_name": "Kwame",
  "phone": "0200000000",
  "delivery_address": "Construction site, Accra",
  "distance_km": 4.5,
  "product_id": 1,
  "quantity": 10
}
```

Distance must currently be supplied by the client. Before production, replace this with a trusted routing/geocoding service or an admin-confirmed distance; do not trust arbitrary customer-supplied distances for delivery pricing.

## Delivery allocation notes

The Aboboyaa is hard-limited to 5 km in code, even if its database setting is accidentally higher. Its sample capacity and all other sample vehicle capacities/rates are illustrative only. The smallest eligible configured vehicle is selected only if its configured piece count and maximum timber length can accommodate the order. For a real timber business, add weight, width/volume, legal payload, loading method, availability, and safe overhang checks.

## Important next steps

- Implement customer registration/login and role-based admin access.
- Protect product/vehicle management endpoints.
- Build a cart and multi-item checkout.
- Add order details per line item and transactional stock updates.
- Integrate Paystack only after merchant setup and secure webhook verification.
- Calculate distance from real yard and site locations.
- Add order status management, delivery assignment, logging, and automated tests.

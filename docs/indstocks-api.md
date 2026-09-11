# INDstocks API Suite — Full Documentation

> Complete plain-text export of the INDstocks Trading API documentation, flattened
> into a single file for LLMs and coding agents. Generated from the same Markdown
> sources as https://api-docs.indstocks.com/ — that site is the canonical version.

Base URL: https://api.indstocks.com
Documentation: https://api-docs.indstocks.com/
OpenAPI 3.0 spec: https://api-docs.indstocks.com/openapi-spec.yaml (also inlined at the end of this file)
Support: instockssupport@indmoney.com
Docs last updated: unknown

This file is a point-in-time snapshot and the documentation is updated
frequently. Re-download https://api-docs.indstocks.com/llms-full.md
for the current version, and compare the "Docs last updated" date above
against your copy to check whether it is stale.

Rate limits, error codes and request/response shapes in this file are the
authoritative ones. Contents:

- https://api-docs.indstocks.com/
- https://api-docs.indstocks.com/getting-started/
- https://api-docs.indstocks.com/introduction/
- https://api-docs.indstocks.com/api-overview/
- https://api-docs.indstocks.com/conventions/
- https://api-docs.indstocks.com/Users/
- https://api-docs.indstocks.com/instruments/
- https://api-docs.indstocks.com/MarketQuote/
- https://api-docs.indstocks.com/historicalData/
- https://api-docs.indstocks.com/contracts/
- https://api-docs.indstocks.com/Websockets/
- https://api-docs.indstocks.com/utility/
- https://api-docs.indstocks.com/normal_orders/
- https://api-docs.indstocks.com/smart_orders/
- https://api-docs.indstocks.com/margin_calculation/
- https://api-docs.indstocks.com/portfolio_funds/
- https://api-docs.indstocks.com/errors/
- https://api-docs.indstocks.com/glossary/
- https://api-docs.indstocks.com/faq/
- https://api-docs.indstocks.com/changelog/


---

# Source: https://api-docs.indstocks.com/

# Algo Trading API - Best Algorithmic Trading Platform in India

🚀 **Build Your Algo Trading Strategies** - Complete algo trading API platform for automated and algorithmic trading in India. Free access to advanced algo trading tools, real-time market data, and smart order execution.

**TIP: Building with an AI coding agent?**

Give it the whole API reference as a single Markdown file —
[Export for LLMs](https://api-docs.indstocks.com/llms/).

## Key Features

- 🎯 **Smart Orders** - Multi-leg GTT strategies with OCO support
- 📊 **Low Latency WebSockets** - Real-time market data streaming
- 🌐 **Multi-Asset Support** - All asset classes and multi-exchange connectivity
- 🛡️ **Enterprise Security** - Token-based authentication and encryption
- 💰 **Free API Access** - No subscription fees
- 📈 **Flat ₹10 Brokerage** - Transparent pricing per order

## Quick Navigation

| **Getting Started** | **Core Features** | **Advanced Trading** |
|---------------------|-------------------|---------------------|
| 🚀 [Introduction](https://api-docs.indstocks.com/introduction/) | 📈 [Smart Orders](https://api-docs.indstocks.com/smart_orders/) | 📊 [Market Data](https://api-docs.indstocks.com/MarketQuote/) |
| 📖 [API Conventions](https://api-docs.indstocks.com/conventions/) | 💼 [Portfolio Management](https://api-docs.indstocks.com/portfolio_funds/) | 🔄 [WebSockets](https://api-docs.indstocks.com/Websockets/) |
| 🔐 [Authentication](https://api-docs.indstocks.com/Users/) | 📋 [API Overview](https://api-docs.indstocks.com/api-overview/) | ⚙️ [Utility APIs](https://api-docs.indstocks.com/utility/) |

## Getting Started

**For Developers**: Begin with the [Introduction](https://api-docs.indstocks.com/introduction/) → [API Conventions](https://api-docs.indstocks.com/conventions/) → [Authentication](https://api-docs.indstocks.com/Users/) → [Order Management](https://api-docs.indstocks.com/normal_orders/).

**For Algorithmic Traders**: Jump to [Smart Orders (GTT)](https://api-docs.indstocks.com/smart_orders/) for advanced trading strategies.

**For Integration**: Check out our [API Overview](https://api-docs.indstocks.com/api-overview/) for complete understanding of capabilities.

---

💡 **Need help?** Our APIs provide reliable performance and comprehensive features for financial trading applications.


---

# Source: https://api-docs.indstocks.com/getting-started/

# Getting Started with INDstocks API

Welcome! This guide will walk you through everything you need to start trading with the INDstocks API.

**TIP: Prefer Postman?**

Every endpoint on this site is also available as a ready-to-run request in our
<a href="https://documenter.getpostman.com/view/56363899/2sBY4LShyj" target="_blank" rel="noopener noreferrer">published Postman collection</a> —
import it, drop in your `access_token`, and start sending requests without copy-pasting curl.

Choose your path based on how you want to use the API:

<div class="grid cards" markdown>

-   :material-code-braces: **For Developers (DIY)**

    ---

    Write your own trading code in Python, JavaScript, or any language. Full control and customization.
    
    [Get Started with DIY →](#for-developers-diy-approach)

-   :material-robot: **For Tradetron Users**

    ---

    Use INDstocks API with Tradetron algo trading platform to automate your strategies.
    
    [Get Started with Tradetron →](#for-algo-platform-users)

</div>

---

## 📋 Prerequisites

Before you begin, make sure you have:

- [x] **An INDstocks account** - [Sign up here](https://indstocks.com) (free)
- [x] **Completed KYC verification** - Required by SEBI regulations
- [x] **Funds in your account** - For placing actual trades

---

## For Algo Platform Users

If you want to use INDstocks API with algo trading platforms like **Tradetron**, follow these simple steps:

### Step 1: Get Your Access Token

1. **Log in** to your INDstocks account at [indstocks.com](https://www.indstocks.com)
2. **Navigate to** [indstocks.com/app/api-trading/access-tokens](https://www.indstocks.com/app/api-trading/access-tokens)
3. Click on **Claim Your API Access**
4. **Generate** your access token (this same page also has a **Setup TOTP** option and Static IP setup — see [Getting Your Access Token](https://api-docs.indstocks.com/Users/#getting-your-access-token))
5. **Copy** your access token

**WARNING: Token Security**

- Your access token is like a password - keep it secure
- Never share it publicly or commit it to version control
- Tokens expire after 24 hours and must be regenerated
- Revoke immediately if compromised

### Step 2: Connect to Tradetron - Get FREE and Unlimited deployments only with INDmoney 

1. **Open** [Tradetron](https://tradetron.tech) and log in to your account
2. **Navigate to** the broker integration or API settings section
3. **Select** "INDmoney - Free" as your broker
4. **Save** and then click on **Generate Access Token** 
5. **Login** to your INDmoney Account
6. Go to **My Strategies** - Select **INDmoney - Free** as the broker and deploy for FREE.

**TIP: You're All Set!**

Your Tradetron account is now connected to INDstocks. You can start deploying strategies, placing orders, and managing your portfolio through Tradetron's interface.

### What's Next?

- Create and deploy trading strategies on Tradetron
- Monitor your orders and positions
- Backtest your strategies with historical data
- Set up automated trading rules

**TIP: Need Help?**

If you face issues connecting to Tradetron, reach out to us at instockssupport@indmoney.com

---

## For Developers (DIY Approach)

If you want to build your own trading applications, bots, or custom integrations, this comprehensive guide will walk you through everything - from authentication to placing your first order.

### Step 1: Get Your Access Token 

Your access token is your key to the INDstocks API. Here's how to get it:

1. **Log in** to [indstocks.com](https://indstocks.com)
2. **Navigate to** [indstocks.com/app/api-trading/access-tokens](https://indstocks.com/app/api-trading/access-tokens)
3. **Generate** your access token
4. **Copy** your access token

**WARNING: Security Best Practice**

- Never commit your access token to version control
- Store it securely (environment variables, secrets manager)
- Tokens expire after 24 hours and must be regenerated
- Revoke immediately if compromised

---

### Step 2: Make Your First API Call 

Let's verify your setup by fetching your user profile.

**Python**

```python
import requests
import os

# Get access token from environment variable
access_token = os.getenv("INDSTOCKS_TOKEN")

# API base URL
base_url = "https://api.indstocks.com"

# Headers for authentication
headers = {"Authorization": access_token, "Content-Type": "application/json"}

# Fetch user profile
response = requests.get(f"{base_url}/user/profile", headers=headers)

if response.status_code == 200:
    profile = response.json()
    print(f"✅ Connected! Welcome, {profile['data']['first_name']} {profile['data']['last_name']}")
    print(f"User ID: {profile['data']['user_id']}")
    print(f"Email: {profile['data']['email']}")
else:
    print(f"❌ Error: {response.json()}")
```

**JavaScript**

```javascript
const fetch = require('node-fetch');

// Get access token from environment variable
const accessToken = process.env.INDSTOCKS_TOKEN;

// API base URL
const baseUrl = 'https://api.indstocks.com';

// Headers for authentication
const headers = {
    'Authorization': accessToken,
    'Content-Type': 'application/json'
};

// Fetch user profile
async function getUserProfile() {
    try {
        const response = await fetch(`${baseUrl}/user/profile`, {
            headers: headers
        });

        if (response.ok) {
            const data = await response.json();
            console.log(`✅ Connected! Welcome, ${data.data.first_name} ${data.data.last_name}`);
            console.log(`User ID: ${data.data.user_id}`);
            console.log(`Email: ${data.data.email}`);
        } else {
            console.log('❌ Error:', await response.json());
        }
    } catch (error) {
        console.error('Connection error:', error);
    }
}

getUserProfile();
```

**cURL**

```bash
curl -X GET "https://api.indstocks.com/user/profile" \
  -H "Authorization: YOUR_ACCESS_TOKEN" \
  -H "Content-Type: application/json"
```

**Expected Response:**

```json
{
  "status": "success",
  "data": {
    "user_id": "5960668",
    "email": "john@example.com",
    "first_name": "John",
    "last_name": "Doe",
    "demat_id": "",
    "is_nse_onboarded": true,
    "is_bse_onboarded": true,
    "is_nse_fno_onboarded": true,
    "is_bse_fno_onboarded": true
  }
}
```

**TIP: Congratulations!**

If you see your profile details, you're all set! You've successfully authenticated with the API.

---

### Step 3: Get Market Data 

Now let's fetch real-time quotes for a stock.

**Python**

```python
import requests
import os

access_token = os.getenv("INDSTOCKS_TOKEN")
base_url = "https://api.indstocks.com"
headers = {"Authorization": access_token}

# Fetch real-time quotes for Reliance (NSE_2885) and TCS (NSE_11536)
# Get scrip codes from the instruments API
params = {"scrip-codes": "NSE_2885,NSE_11536"}

response = requests.get(f"{base_url}/market/quotes/full", headers=headers, params=params)

if response.status_code == 200:
    quotes = response.json()
    for symbol, data in quotes["data"].items():
        print(f"\n📈 {symbol}")
        print(f"   LTP: ₹{data['live_price']}")
        print(f"   Change: {data['day_change_percentage']}%")
        print(f"   Volume: {data['volume']:,}")
else:
    print(f"Error: {response.json()}")
```

**JavaScript**

```javascript
const fetch = require('node-fetch');

const accessToken = process.env.INDSTOCKS_TOKEN;
const baseUrl = 'https://api.indstocks.com';
const headers = {'Authorization': accessToken};

// Fetch real-time quotes for Reliance (NSE_2885) and TCS (NSE_11536)
// Get scrip codes from the instruments API
async function getMarketQuotes() {
    const scripCodes = 'NSE_2885,NSE_11536';
    const url = `${baseUrl}/market/quotes/full?scrip-codes=${scripCodes}`;

    try {
        const response = await fetch(url, { headers });
        const quotes = await response.json();

        if (response.ok) {
            for (const [symbol, data] of Object.entries(quotes.data)) {
                console.log(`\n📈 ${symbol}`);
                console.log(`   LTP: ₹${data.live_price}`);
                console.log(`   Change: ${data.day_change_percentage}%`);
                console.log(`   Volume: ${data.volume.toLocaleString()}`);
            }
        } else {
            console.log('Error:', quotes);
        }
    } catch (error) {
        console.error('Request failed:', error);
    }
}

getMarketQuotes();
```

**cURL**

```bash
curl -X GET "https://api.indstocks.com/market/quotes/full?scrip-codes=NSE_2885,NSE_11536" \
  -H "Authorization: YOUR_ACCESS_TOKEN"
```

**Sample Output:**

```
📈 NSE_2885
   LTP: ₹2,456.75
   Change: +1.23%
   Volume: 12,345,678

📈 NSE_11536
   LTP: ₹3,890.50
   Change: -0.45%
   Volume: 5,678,901
```

---

### Step 4: Place Your First Order

Ready to place a trade? Let's place a simple limit order to buy shares.

**WARNING: Real Money Alert**

The following code places real orders with real money. Start with small quantities to test your integration!

**Python**

```python
import requests
import os

access_token = os.getenv("INDSTOCKS_TOKEN")
base_url = "https://api.indstocks.com"
headers = {"Authorization": access_token, "Content-Type": "application/json"}

# Order parameters
order_data = {
    "txn_type": "BUY",  # BUY or SELL
    "exchange": "NSE",  # NSE or BSE
    "segment": "EQUITY",  # EQUITY, FNO, etc.
    "security_id": "2885",  # Reliance security ID (get from instruments API)
    "qty": 1,  # Quantity to buy
    "order_type": "LIMIT",  # LIMIT, MARKET, STOP_LOSS, etc.
    "limit_price": 2450.00,  # Limit price
    "validity": "DAY",  # DAY or IOC
    "product": "CNC",  # CNC (delivery) or INTRADAY (intraday) or MARGIN (derivatives)
    "is_amo": False,  # After Market Order flag
    "algo_id": "99999",  # Required: Use 99999 for regular orders
}

# Place the order
response = requests.post(f"{base_url}/order", headers=headers, json=order_data)

if response.status_code == 200:
    result = response.json()
    print(f"✅ Order placed successfully!")
    print(f"Order ID: {result['data']['order_id']}")
    print(f"Status: {result['data']['order_status']}")
else:
    error = response.json()
    print(f"❌ Order failed: {error.get('message', error)}")
```

**JavaScript**

```javascript
const fetch = require('node-fetch');

const accessToken = process.env.INDSTOCKS_TOKEN;
const baseUrl = 'https://api.indstocks.com';
const headers = {
    'Authorization': accessToken,
    'Content-Type': 'application/json'
};

// Order parameters
const orderData = {
    txn_type: 'BUY',
    exchange: 'NSE',
    segment: 'EQUITY',
    security_id: '2885',        // Reliance security ID
    qty: 1,
    order_type: 'LIMIT',
    limit_price: 2450.00,
    validity: 'DAY',
    product: 'CNC',
    is_amo: false,              // After Market Order flag
    algo_id: '99999'            // Required: Use 99999 for regular orders
};

// Place the order
async function placeOrder() {
    try {
        const response = await fetch(`${baseUrl}/order`, {
            method: 'POST',
            headers: headers,
            body: JSON.stringify(orderData)
        });

        const result = await response.json();

        if (response.ok) {
            console.log('✅ Order placed successfully!');
            console.log(`Order ID: ${result.data.order_id}`);
            console.log(`Status: ${result.data.order_status}`);
        } else {
            console.log(`❌ Order failed: ${result.message || JSON.stringify(result)}`);
        }
    } catch (error) {
        console.error('Request failed:', error);
    }
}

placeOrder();
```

**cURL**

```bash
curl -X POST "https://api.indstocks.com/order" \
  -H "Authorization: YOUR_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "txn_type": "BUY",
    "exchange": "NSE",
    "segment": "EQUITY",
    "security_id": "2885",
    "qty": 1,
    "order_type": "LIMIT",
    "limit_price": 2450.00,
    "validity": "DAY",
    "product": "CNC",
    "is_amo": false,
    "algo_id": "99999"
  }'
```

**Success Response:**

```json
{
  "status": "success",
  "data": {
    "order_id": "DRV-29301125",
    "order_status": "O-PENDING"
  }
}
```

### Understanding Order Parameters

| Parameter | Description | Example Values |
|-----------|-------------|----------------|
| `txn_type` | Transaction type | `BUY`, `SELL` |
| `exchange` | Stock exchange | `NSE`, `BSE` |
| `segment` | Market segment | `EQUITY`, `FNO`, `CURRENCY` |
| `security_id` | Instrument ID | Get from [Instruments API](https://api-docs.indstocks.com/instruments/) |
| `qty` | Quantity | `1`, `10`, `100` |
| `order_type` | Order type | `LIMIT`, `MARKET`, `STOP_LOSS` |
| `limit_price` | Price limit | `2450.00` (for LIMIT orders) |
| `product` | Product type | `CNC` (delivery), `INTRADAY` (intraday), `MARGIN` (derivatives) |
| `validity` | Order validity | `DAY`, `IOC` |
| `algo_id` | Algo identifier (required) | `99999` (for regular orders) |

---

### Step 5: Check Your Order Status

After placing an order, you'll want to check its status:

**Python**

```python
import requests
import os

access_token = os.getenv("INDSTOCKS_TOKEN")
base_url = "https://api.indstocks.com"
headers = {"Authorization": access_token}

# Get order book (all orders for the day)
response = requests.get(f"{base_url}/order-book", headers=headers)

if response.status_code == 200:
    orders = response.json()
    print("\n📋 Today's Orders:\n")
    for order in orders["data"]:
        print(f"Order ID: {order['id']}")
        print(f"Symbol: {order['name']}")
        print(f"Status: {order['status']}")
        print(f"Qty: {order['requested_qty']} @ ₹{order['requested_price']}")
        print(f"Traded: {order['traded_qty']} @ ₹{order['traded_price']}")
        print("---")
```

**JavaScript**

```javascript
const fetch = require('node-fetch');

const accessToken = process.env.INDSTOCKS_TOKEN;
const baseUrl = 'https://api.indstocks.com';
const headers = {'Authorization': accessToken};

async function getOrderBook() {
    try {
        const response = await fetch(`${baseUrl}/order-book`, { headers });
        const orders = await response.json();

        if (response.ok) {
            console.log('\n📋 Today\'s Orders:\n');
            orders.data.forEach(order => {
                console.log(`Order ID: ${order.id}`);
                console.log(`Symbol: ${order.name}`);
                console.log(`Status: ${order.status}`);
                console.log(`Qty: ${order.requested_qty} @ ₹${order.requested_price}`);
                console.log(`Traded: ${order.traded_qty} @ ₹${order.traded_price}`);
                console.log('---');
            });
        }
    } catch (error) {
        console.error('Request failed:', error);
    }
}

getOrderBook();
```

---

### 🎯 What's Next?

Congratulations! You've successfully:

- ✅ Authenticated with the INDstocks API
- ✅ Fetched real-time market data
- ✅ Placed your first order
- ✅ Checked order status

#### Continue Your Journey

<div class="grid cards" markdown>

-   :material-rocket-launch: **Build Trading Strategies**

    ---

    Learn about [Smart Orders (GTT)](https://api-docs.indstocks.com/smart_orders/) for automated trading strategies

-   :material-chart-line: **Real-time Data**

    ---

    Integrate [WebSocket streaming](https://api-docs.indstocks.com/Websockets/) for live market data

-   :material-history: **Backtesting**

    ---

    Access [Historical Data](https://api-docs.indstocks.com/historicalData/) for strategy backtesting

-   :material-calculator: **Risk Management**

    ---

    Calculate margins before orders with [Margin API](https://api-docs.indstocks.com/margin_calculation/)

</div>

---

### 📚 Essential Resources

| Resource | Description |
|----------|-------------|
| [API Overview](https://api-docs.indstocks.com/api-overview/) | Complete endpoint catalog |
| [Order Management](https://api-docs.indstocks.com/normal_orders/) | Advanced order types and management |
| [Market Data](https://api-docs.indstocks.com/MarketQuote/) | Real-time quotes and market depth |
| [WebSockets](https://api-docs.indstocks.com/Websockets/) | Live streaming data |
| [Error Handling](https://api-docs.indstocks.com/errors/) | Error codes and troubleshooting |
| [FAQ](https://api-docs.indstocks.com/faq/) | Common questions answered |

---

### 🐛 Common Issues & Solutions

### TokenException: Invalid token

**Problem**: Your access token is invalid or expired.

**Solution**:
1. Log in to [indstocks.com](https://indstocks.com)
2. Go to [indstocks.com/app/api-trading/access-tokens](https://indstocks.com/app/api-trading/access-tokens)
3. Generate a new access token
4. Update your code with the new token

### OrderException: Insufficient margin

**Problem**: Not enough funds in your account.

**Solution**:
1. Check available funds: `GET /funds`
2. Add funds to your account
3. Reduce order quantity
4. Use intraday (MIS) product for lower margin

### InputException: Invalid security_id

**Problem**: The security_id doesn't exist or is incorrect.

**Solution**:
1. Download instruments master: `GET /market/instruments`
2. Search for your symbol in the CSV
3. Use the correct `security_id` from the file

### NetworkException: Connection timeout

**Problem**: Network connectivity issues.

**Solution**:
1. Check your internet connection
2. Verify API endpoint URL
3. Implement retry logic with exponential backoff

---

### 💡 Best Practices

**TIP: Production-Ready Code**

1. **Error Handling**: Always wrap API calls in try-catch blocks
2. **Rate Limiting**: Respect rate limits — see the full table in [API Conventions](https://api-docs.indstocks.com/conventions/#rate-limiting)
3. **Retry Logic**: Implement exponential backoff for transient errors
4. **Logging**: Log all API requests and responses for debugging
5. **Testing**: Test thoroughly with small quantities before scaling up
6. **Security**: Never expose your access token in client-side code

#### Example: Production-Grade Order Placement

**Python**

```python
import requests
import time
import logging
from typing import Dict, Optional

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class INDstocksAPI:
    def __init__(self, access_token: str):
        self.base_url = "https://api.indstocks.com"
        self.headers = {"Authorization": access_token, "Content-Type": "application/json"}
        self.max_retries = 3

    def place_order(self, order_data: Dict) -> Optional[Dict]:
        """Place an order with retry logic and error handling"""
        for attempt in range(self.max_retries):
            try:
                response = requests.post(
                    f"{self.base_url}/order", headers=self.headers, json=order_data, timeout=10
                )

                if response.status_code == 200:
                    result = response.json()
                    logger.info(
                        f"Order placed: {result['data']['order_id']} - Status: {result['data']['order_status']}"
                    )
                    return result

                elif response.status_code == 429:  # Rate limit
                    wait_time = 2**attempt  # Exponential backoff
                    logger.warning(f"Rate limited. Retrying in {wait_time}s...")
                    time.sleep(wait_time)
                    continue

                else:
                    error = response.json()
                    logger.error(f"Order failed: {error['message']}")
                    return None

            except requests.exceptions.Timeout:
                logger.warning(f"Request timeout. Attempt {attempt + 1}/{self.max_retries}")
                time.sleep(2**attempt)

            except Exception as e:
                logger.error(f"Unexpected error: {str(e)}")
                return None

        logger.error("Max retries exceeded")
        return None


# Usage
api = INDstocksAPI(os.getenv("INDSTOCKS_TOKEN"))
order = {
    "txn_type": "BUY",
    "exchange": "NSE",
    "segment": "EQUITY",
    "security_id": "2885",
    "qty": 1,
    "order_type": "LIMIT",
    "limit_price": 2450.00,
    "validity": "DAY",
    "product": "CNC",
    "is_amo": False,
    "algo_id": "99999",
}
result = api.place_order(order)
```

---

### 🚀 Take It Further

#### Build a Simple Trading Bot

Want to automate your trading? Here's a simple example:

```python
import requests
import time
import os


class SimpleBot:
    def __init__(self, access_token):
        self.api_url = "https://api.indstocks.com"
        self.headers = {"Authorization": access_token}

    def get_ltp(self, scrip_code):
        """Get last traded price"""
        response = requests.get(
            f"{self.api_url}/market/quotes/ltp",
            headers=self.headers,
            params={"scrip-codes": scrip_code},
        )
        return response.json()["data"][scrip_code]["live_price"]

    def buy_at_support(self, scrip_code, security_id, support_price, qty):
        """Buy when price hits support level"""
        while True:
            current_price = self.get_ltp(scrip_code)
            print(f"Current price: ₹{current_price} | Target: ₹{support_price}")

            if current_price <= support_price:
                # Place order
                order_data = {
                    "txn_type": "BUY",
                    "exchange": "NSE",
                    "segment": "EQUITY",
                    "security_id": security_id,
                    "qty": qty,
                    "order_type": "MARKET",
                    "product": "CNC",
                    "is_amo": False,
                    "validity": "DAY",
                    "algo_id": "99999",
                }
                response = requests.post(
                    f"{self.api_url}/order", headers=self.headers, json=order_data
                )
                print(f"Order placed: {response.json()}")
                break

            time.sleep(5)  # Check every 5 seconds


# Usage
bot = SimpleBot(os.getenv("INDSTOCKS_TOKEN"))
# bot.buy_at_support('NSE_2885', '2885', 2400, 1)  # Reliance
```

**WARNING: Trading Bot Disclaimer**

This is a educational example. Real trading bots require sophisticated risk management, error handling, and testing. Always backtest strategies before deploying with real money.

---

## 🆘 Need Help?

Having trouble getting started? We're here to help!

- 📧 **Email**: instockssupport@indmoney.com
- 📚 **Documentation**: [api-docs.indstocks.com](https://api-docs.indstocks.com)
- ❓ **FAQ**: [Frequently Asked Questions](https://api-docs.indstocks.com/faq/)
- 💬 **Community**: Developer community coming soon

---

## See Also

- [API Conventions](https://api-docs.indstocks.com/conventions/) - Standards and best practices
- [Smart Orders Guide](https://api-docs.indstocks.com/smart_orders/) - Automated trading strategies
- [Portfolio Management](https://api-docs.indstocks.com/portfolio_funds/) - Track holdings and positions

---

**Ready to build something amazing? Let's trade! 🚀**


---

# Source: https://api-docs.indstocks.com/introduction/

# Introduction

Welcome to the official documentation for the INDstocks API (v1).

The INDstocks API is a state-of-the-art platform for building advanced trading and investment services. It is a set of RESTful APIs that provide deep integration into our trading platform, allowing you to execute orders in real-time, manage your portfolio, access live market data, and much more.

Our APIs use resource-based URLs, accept JSON-encoded request bodies, return JSON-encoded responses, and use standard HTTP response codes for errors.

## Getting Started

To begin using the INDstocks API, follow these steps:

1.  **Review the `API Conventions`**: Understand our standards for requests, responses, and error handling.
2.  **Authentication**: Learn how to authenticate your requests by reviewing the `Users` section.
3.  **Explore Endpoints**: Dive into the specific API sections like `Order Management` or `Market Quotes` to see what's possible.

---

## See Also

- [Quick Start Tutorial](https://api-docs.indstocks.com/getting-started/) - Get up and running in 5 minutes
- [API Overview](https://api-docs.indstocks.com/api-overview/) - Complete endpoint catalog
- [API Conventions](https://api-docs.indstocks.com/conventions/) - Standards and best practices
- [User Authentication](https://api-docs.indstocks.com/Users/) - Learn how to authenticate


---

# Source: https://api-docs.indstocks.com/api-overview/

# INDstocks API Suite - Complete Overview

## API Summary

The **INDstocks API Suite v1** is a comprehensive RESTful API designed for financial trading applications. This API suite provides real-time market data, order management, and portfolio tracking capabilities with robust error handling and extensive market coverage.

## Pricing & Cost Structure

- **API Access**: **Free** - No subscription fees or API charges
- **Brokerage**: **₹10 per order** - Flat rate regardless of order size
- **No Hidden Costs**: Transparent pricing with no additional charges

## Core Features

### 🚀 **Performance**
- **Sub-20ms latency** for order execution
- **WebSocket streams** for real-time market data
- **Optimized JSON responses** with minimal payload sizes

### 🔒 **Security**
- Token-based authentication
- Rate limiting and abuse prevention
- Encryption in transit

### 📊 **Market Coverage**
- Multi-exchange support (NSE and more)
- All asset classes: Equity, Derivatives, Options, Futures
- Real-time market quotes
- Historical data with extensive time series

### 🎯 **Trading Features**
- **Smart Orders (GTT)** with advanced multi-leg trading strategies
- **Stop-loss and target automation** with intelligent routing
- **Portfolio management** with real-time P&L tracking
- **Advanced margin calculations** with dynamic risk assessment
- **Multi-exchange support** (NSE, BSE) with unified interface
- **All asset classes**: Equity, Derivatives, Options, Futures

## Complete API Endpoint Catalog

### **1. User Management & Profile**
| Endpoint | Method | Purpose | Documentation |
|----------|--------|---------|---------------|
| `/user/profile` | GET | User profile and account details | [Users Guide](https://api-docs.indstocks.com/Users/) |
| `/funds` | GET | Available and utilized funds | [Users Guide](https://api-docs.indstocks.com/Users/) |

**Note**: Authentication is handled via access tokens obtained from the [indstocks.com dashboard](https://indstocks.com).

### **2. Market Data APIs**
| Endpoint | Method | Purpose | Documentation |
|----------|--------|---------|---------------|
| `/market/quotes/full` | GET | Real-time market quotes | [Market Quotes](https://api-docs.indstocks.com/MarketQuote/) |
| `/market/quotes/ltp` | GET | Last traded price only | [Market Quotes](https://api-docs.indstocks.com/MarketQuote/) |
| `/market/quotes/mkt` | GET | Market depth/order book | [Market Quotes](https://api-docs.indstocks.com/MarketQuote/) |
| `/market/historical/{interval}` | GET | Historical OHLCV data | [Historical Data](https://api-docs.indstocks.com/historicalData/) |
| `/market/instruments` | GET | Instrument master CSV | [Instruments Data](https://api-docs.indstocks.com/instruments/) |

### **3. Order Management**
| Endpoint | Method | Purpose | Documentation |
|----------|--------|---------|---------------|
| `/order` | POST | Place new orders | [Orders Guide](https://api-docs.indstocks.com/normal_orders/) |
| `/order/modify` | POST | Modify pending orders | [Orders Guide](https://api-docs.indstocks.com/normal_orders/) |
| `/order/cancel` | POST | Cancel orders | [Orders Guide](https://api-docs.indstocks.com/normal_orders/) |
| `/order-book` | GET | Daily order history | [Orders Guide](https://api-docs.indstocks.com/normal_orders/) |
| `/trades/{order_id}` | GET | Trade confirmations for an order | [Orders Guide](https://api-docs.indstocks.com/normal_orders/) |
| `/trade-book` | GET | Trade book for segment | [Orders Guide](https://api-docs.indstocks.com/normal_orders/) |

### **4. Smart Orders**
| Endpoint | Method | Purpose | Documentation |
|----------|--------|---------|---------------|
| `/smart/order` | POST | Multi-leg GTT orders | [Smart Orders Guide](https://api-docs.indstocks.com/smart_orders/) |
| `/smart/order/modify` | POST | Smart order modifications | [Smart Orders Guide](https://api-docs.indstocks.com/smart_orders/) |
| `/smart/order/cancel` | POST | Smart order cancellation | [Smart Orders Guide](https://api-docs.indstocks.com/smart_orders/) |

### **5. Portfolio & Risk Management**
| Endpoint | Method | Purpose | Documentation |
|----------|--------|---------|---------------|
| `/portfolio/holdings` | GET | Equity holdings in Demat account | [Portfolio & Holdings](https://api-docs.indstocks.com/portfolio_funds/) |
| `/portfolio/positions` | GET | Open derivative positions | [Portfolio & Holdings](https://api-docs.indstocks.com/portfolio_funds/) |
| `/funds` | GET | Available and utilized funds | [Users Guide](https://api-docs.indstocks.com/Users/) |
| `/margin` | GET | Margin calculation for orders | [Margin Calculation](https://api-docs.indstocks.com/margin_calculation/) |

### **6. WebSocket Streaming**
| Stream Type | Purpose | Documentation |
|-------------|---------|---------------|
| Market Data | Live quotes/ticks | [WebSockets Guide](https://api-docs.indstocks.com/Websockets/) |
| Order Updates | Trade confirmations | [WebSockets Guide](https://api-docs.indstocks.com/Websockets/) |
| Portfolio Changes | Position updates | [WebSockets Guide](https://api-docs.indstocks.com/Websockets/) |

### **7. Utility & System APIs**
| Endpoint | Method | Purpose | Documentation |
|----------|--------|---------|---------------|
| `/market/option-chain` | GET | Option chain with Greeks and IV | [Options Toolkit](https://api-docs.indstocks.com/utility/#option-chain) |

## 📚 **Complete Documentation Guide**

For detailed implementation guides, refer to these comprehensive documentation pages:

### **Getting Started**
- **[Introduction](https://api-docs.indstocks.com/introduction/)** - API overview and getting started guide
- **[API Conventions](https://api-docs.indstocks.com/conventions/)** - Standards, formats, and best practices
- **[Users Guide](https://api-docs.indstocks.com/Users/)** - Authentication and user management

### **Market Data & Instruments**
- **[Instruments Data](https://api-docs.indstocks.com/instruments/)** - Market instruments and symbols
- **[Market Quotes](https://api-docs.indstocks.com/MarketQuote/)** - Real-time market data and quotes
- **[Historical Data](https://api-docs.indstocks.com/historicalData/)** - Historical OHLCV data access

### **Trading & Orders**
- **[Orders](https://api-docs.indstocks.com/normal_orders/)** - Standard order placement and management
- **[Smart Orders (GTT)](https://api-docs.indstocks.com/smart_orders/)** - Advanced multi-leg trading strategies

### **Portfolio & Risk**
- **[Portfolio & Holdings](https://api-docs.indstocks.com/portfolio_funds/)** - Portfolio management and funds
- **[Margin Calculation](https://api-docs.indstocks.com/margin_calculation/)** - Risk management and margin requirements

### **Real-time & Utilities**
- **[WebSockets](https://api-docs.indstocks.com/Websockets/)** - Real-time data streaming
- **[Utility APIs](https://api-docs.indstocks.com/utility/)** - Helper functions and system utilities
- **[Error Handling](https://api-docs.indstocks.com/errors/)** - Comprehensive error codes and handling

### **Developer Resources**
- **<a href="https://documenter.getpostman.com/view/56363899/2sBY4LShyj" target="_blank" rel="noopener noreferrer">Postman Collection</a>** - Every endpoint as a ready-to-run request, with saved example responses
- **[OpenAPI Spec](https://api-docs.indstocks.com/openapi-spec.yaml)** - Machine-readable spec for codegen and API clients
- **[Glossary & Constants](https://api-docs.indstocks.com/glossary/)** - Shared enums, ID prefixes, and constants
- **[Error Bucket](https://api-docs.indstocks.com/errors/)** - Every error type and RMS rejection message in one place
- **[LLM Metadata](https://api-docs.indstocks.com/llm-metadata/)** - AI/LLM optimized documentation

## Technical Specifications

**How to Get Your Access Token:**
1. Login to [indstocks.com](https://indstocks.com)
2. Go to [indstocks.com/app/api-trading/access-tokens](https://indstocks.com/app/api-trading/access-tokens)
3. Generate your access token (dashboard or TOTP-based — see [Getting Your Access Token](https://api-docs.indstocks.com/Users/#getting-your-access-token))
4. Copy your access token for API requests

### **Error Handling Excellence**
- **HTTP Status Codes**: Standard compliance
- **Error Response Format**: Structured JSON
- **Error Categories**: 400-499 (Client), 500-599 (Server)

## Integration Examples

### **Quick Start (Python)**
```python
import requests

# Get your access token from indstocks.com dashboard
access_token = "YOUR_ACCESS_TOKEN_FROM_DASHBOARD"

# Place Order
order_response = requests.post(
    "https://api.indstocks.com/order",
    headers={"Authorization": access_token, "Content-Type": "application/json"},
    json={
        "txn_type": "BUY",
        "exchange": "NSE",
        "segment": "EQUITY",
        "security_id": "12345",
        "qty": 100,
        "order_type": "LIMIT",
        "limit_price": 150.50,
        "validity": "DAY",
        "product": "CNC",
        "is_amo": False,
        "algo_id": "99999",
    },
)
```

### **WebSocket Integration**
```javascript
const ws = new WebSocket('wss://api.indstocks.com/ws');
ws.on('message', (data) => {
    const marketData = JSON.parse(data);
    console.log('Live Quote:', marketData);
});
```

## Performance Metrics & Benchmarks

### **Latency Performance**
- **Order Execution**: Fast response time for order placement
- **WebSocket Data**: Real-time market feeds
- **API Response**: Optimized for standard API calls

### **Reliability Metrics**
- **Error Rate**: Low system error rate
- **Throughput**: High concurrent request support

### **Cost Efficiency**
- **API Access**: Free (no subscription charges)
- **Brokerage**: Flat ₹10 per order (regardless of size)
- **Data Feeds**: Real-time data included at no extra cost
- **Historical Data**: 10+ years of historical data available

## Key Benefits

### **1. Developer Experience**
- Comprehensive documentation with interactive examples
- Official SDKs (Python, JavaScript, Java) — coming soon; use the REST API directly with the cURL/Python/JavaScript examples on each page until then
- Developer support via [instockssupport@indmoney.com](mailto:instockssupport@indmoney.com)

### **2. Reliability & Performance**
- Financial-grade infrastructure
- Optimized for low-latency access
- Auto-scaling to handle peak trading volumes

### **3. Feature Completeness**
- All order types supported (Market, Limit, Stop-Loss, GTT)
- Multi-asset support (Equity, F&O, Commodities)
- Advanced analytics with portfolio insights
- Compliance ready

---

## Conclusion

The **INDstocks API Suite** provides enterprise-grade reliability and comprehensive functionality for financial trading applications. The API is designed for trading algorithms, fintech applications, and systems requiring high-quality API integration.

**Contact**: instockssupport@indmoney.com | **Documentation**: https://api-docs.indstocks.com


---

# Source: https://api-docs.indstocks.com/conventions/

# API Conventions

This document outlines the general conventions and standards used across the INDstocks API.

## Requests & Responses
- **Base URL**: The root for all API requests is `https://api.indstocks.com`.
- **Data Format**: All request bodies and responses are in `JSON` format.
- **Timestamps**: All timestamps in requests and responses are in IST and represented in Unix epoch milliseconds, unless specified otherwise.
- **HTTP Verbs**: We use standard HTTP verbs to indicate actions:
    - `GET`: To retrieve resources.
    - `POST`: To create new resources.

## Authentication
All protected API endpoints require an `access_token` to be included in the `Authorization` header of your request.

- **Header Format**: `Authorization: <your_access_token>`

Refer to the [Users](https://api-docs.indstocks.com/Users/) section for details on how to obtain an access token.

## Error Handling
We use standard HTTP status codes to indicate the success or failure of an API request.
- `2xx` codes indicate success.
- `4xx` codes indicate a client-side error (e.g., invalid parameters, authentication failure).
- `5xx` codes indicate a server-side error.

In addition to the HTTP status code, error responses include a JSON body with specific details:
```json
{
  "status": "error",
  "message": "A human-readable error message.",
  "error_code": "INVALID_INPUT"
}
```
Refer to the [Errors](https://api-docs.indstocks.com/errors/) section for a complete list of error codes.

## Rate Limiting
The API enforces rate limits to ensure high availability for all users. Limits are applied based on the category of the API endpoint.

Exceeding these limits will result in a `429 Too Many Requests` error.

| Category               | Rate Limit per Second | Rate Limit per Minute | Rate Limit per Hour | Rate Limit per Day | Notes                                       |
|------------------------|:---------------------:|:---------------------:|:-------------------:|:------------------:|---------------------------------------------|
| Order APIs             | 10                    | -                   | -               | -              | Max 25 modifications per order.             |
| Data APIs              | 5                     | -                     | -                   | 100,000            | Includes Instruments, Historical Data and the Option Chain. |
| Quote APIs             | 5                     | -                     | -                   | 100,000                  |                                             |
| Non-Trading APIs       | 15                    | -                     | -                   | 100,000                  | Includes Profile, Funds, Order History, etc. |
| Token Generation       | -                     | 1                     | -                   | -                  | `/generate/token` (TOTP). Repeated failures also trigger a lockout — see [TOTP limits and lockouts](https://api-docs.indstocks.com/Users/#totp-limits-and-lockouts). |
| WebSocket Connections  | -                     | -                     | -                   | -                  | Up to 3 active connections per user.        |
| WebSocket Subscriptions| -                     | -                     | -                   | -                  | Up to 3000 instruments per connection.      |


---

# Source: https://api-docs.indstocks.com/Users/

# User APIs

This section covers endpoints for retrieving user-specific information like account profile and funds.

| Request Type | Path             | Description                                          |
|--------------|------------------|------------------------------------------------------|
| **GET**      | [`/user/profile`](#user-profile) | Get the profile information for the logged-in user.  |
| **GET**      | [`/funds`](#get-funds)         | Fetches the user's available and utilized funds. |
| **POST**     | [`/generate/token`](#method-2-totp-based-token-generation) | Generate an access token via TOTP. |

---

## Static IP Settings

Static IP whitelisting is required for **order placement** — placing, modifying, and cancelling
orders via the API. Read-only endpoints (market quotes, historical data, order book, profile,
funds) do not require a whitelisted IP.

Static IPs are set on the same **Access Tokens** page in the dashboard —
[indstocks.com/app/api-trading/access-tokens](https://www.indstocks.com/app/api-trading/access-tokens)
— which has two slots, **Primary** and **Secondary**.

- **Both slots accept either IPv4 or IPv6**, in any combination. You can use IPv4 in one slot and
  IPv6 in the other, or IPv6 in both.
- **A static IP cannot be updated more than once a calendar week.** This is a regulatory
  constraint — see NSE Circular NSE/INVG/67858 (May 5, 2025), Annexure Section A, Point 6.
- **A slot cannot be cleared back to blank.** Once a slot holds a value, it can only be replaced
  with another valid IP. Leaving an old, unused IP in a slot is not a problem — it has no effect
  on compliance, audit standing, or order routing.

---

## Getting Your Access Token

There are two ways to get the `access_token` used in the `Authorization` header on every request.

### Method 1: Dashboard token generation

Individual traders can directly get their Access Token from web.indstocks.com. All INDstocks users are eligible to get free access to Trading APIs. Here's how to get your Access Token:

1. Log in to [indstocks.com](https://indstocks.com)
2. Go to [indstocks.com/app/api-trading/access-tokens](https://www.indstocks.com/app/api-trading/access-tokens) and generate your access token.

### Method 2: TOTP-based token generation

For server-side / headless integrations, you can generate an access token using a TOTP
(Time-based One-Time Password) instead of logging into the dashboard each time.

**Setup (one-time)**

1. Log in to [indstocks.com](https://indstocks.com) and go to
   [indstocks.com/app/api-trading/access-tokens](https://indstocks.com/app/api-trading/access-tokens)
   — the same page where you generate your dashboard access token and set up your Static IP.
2. Click **Setup TOTP** and follow the steps to link an authenticator app. Scan the QR code (or
   enter the key manually), then submit one code from the app to confirm.
3. Once TOTP setup succeeds, the page displays your **Client ID** — this is the static
   `x-api-key` value for your account, used in place of the `Authorization` header for this
   endpoint only.
4. You can now generate a fresh access token at any time by calling the endpoint below instead
   of returning to the dashboard.

**WARNING: Setup constraints**

- **You have 5 minutes** to complete setup once the secret and QR code are displayed. If you
  don't submit a confirming code in that window, the pending secret is discarded and you
  start over. Closing the browser mid-setup has the same effect — TOTP only becomes active
  after a successful confirmation code.
- **The secret is shown exactly once and is never retrievable afterwards.** Store it in your
  authenticator app (and, if you need a backup, your own secrets manager) before leaving the
  page. If you lose it, you must disable TOTP and re-enroll to get a new secret.
- **Setup is web-only.** There is no API to enable, reset, or read the TOTP secret — it
  requires a logged-in session on the website. Only token generation is available over the API.

**Endpoint**
```
POST /generate/token
```

**Headers**

| Header         | Required | Description                                                        |
|----------------|:--------:|----------------------------------------------------------------------|
| `x-api-key`    | ✅       | Your Client ID — a static identifier for your account, shown on the dashboard after successful TOTP setup. Distinct from `access_token`; not sent as `Authorization`. |
| `Content-Type` | ✅       | `application/json`                                                  |

**Request Body**

| Parameter | Type   | Required | Description                                              |
|-----------|--------|:--------:|-------------------------------------------------------------|
| `mpin`    | string | ✅       | Your INDstocks account MPIN.                             |
| `totp`    | string | ✅       | The current 6-digit code from your authenticator app.    |

**Example Request**
```bash
curl --location 'https://api.indstocks.com/generate/token' \
--header 'x-api-key: YOUR_API_KEY' \
--header 'Content-Type: application/json' \
--data '{
    "mpin": "YOUR_MPIN",
    "totp": "123456"
}'
```

**Response Payload (Success)**

The response returns the access token in a field named `token` (not `access_token`). Use its
value in the `Authorization` header of every other request, same as the dashboard-generated token.

**Token lifecycle**

**Only one TOTP-generated token is live at a time.** Each successful call to `/generate/token`
invalidates the token issued by the previous call. There is no way to hold two valid TOTP tokens
concurrently.

The practical consequences:

- Generate your token **once per session** and reuse it for the rest of the day. Don't call
  `/generate/token` before each request — you'll invalidate the token your other processes are using.
- If you run multiple processes or machines, have **one** of them generate the token and share it
  with the others. Two processes each generating their own token will keep killing each other's.
- A token remains valid for **24 hours** from generation, unless it's replaced by a newer one,
  revoked from the dashboard, or invalidated by disabling TOTP.
- The currently-live token is displayed on the dashboard next to your Client ID, with an option
  to **revoke** it. Revoking takes effect immediately — in-flight and subsequent calls with that
  token will fail with `TokenException`.

**TOTP limits and lockouts**

| Rule | Limit | Notes |
|---|---|---|
| Minimum gap between token generations | **1 token per 60 seconds** | A token lasts 24 hours, so a correctly-written client never needs another this soon. This exists to stop a looping script. |
| Wrong TOTP codes before lockout | **5 failed attempts → 15-minute lockout** | Applies to the `totp` field specifically. |
| Window for counting failed attempts | **Rolling 15 minutes** | Isolated typos hours apart don't accumulate into a lockout. |
| Repeated lockouts | **3 lockouts within 1 hour → 1-hour lockout** | Also triggers an email alert to the account holder. If you hit this without knowing why, contact `instockssupport@indmoney.com`. |
| Attempts while already locked out | Rejected, and **do not extend the lockout** | A client that keeps retrying during a lockout won't lock itself out indefinitely — but you should still back off. |

**WARNING: A lockout does not kill your existing access token**

Being locked out blocks **new** token generation only. An access token that was already issued
keeps working until its normal 24-hour expiry. This is deliberate: a lockout is usually a
misconfigured or clock-skewed client, and revoking a live token would strand a running strategy
with open positions. If you believe your credentials are actually compromised, revoke the token
from the dashboard rather than waiting for the lockout to clear.

These numbers are the launch values and may be tuned once we see real traffic. Build your client to
read the error response rather than hard-coding these thresholds, and always back off on failure
instead of retrying immediately.

**Failure cases**

| Scenario | Result | What to do |
|---|---|---|
| Wrong `mpin` | Rejected. | Fix the MPIN. Don't retry with the same value. |
| Wrong or expired `totp` | Rejected, and the attempt is counted toward the 5-attempt lockout. | Wait for the next code from your authenticator app — don't retry the same code. |
| Called again within 60 seconds of a successful generation | Rejected by the throttle. | Cache and reuse the token you already have; see [Token lifecycle](#token-lifecycle). |
| Locked out (5 wrong codes, or 3 lockouts in an hour) | Rejected for 15 minutes / 1 hour. | Back off for the full window. Check your server clock before trying again. |
| Server clock drift | Small drift is tolerated; large drift causes every code to fail. | Fix it on your side — sync via NTP. A host whose clock has drifted by more than about a minute will fail every attempt while showing a "valid" code in the app. |
| TOTP disabled from the dashboard | The secret is deleted **and the active token is revoked immediately.** | Re-run setup to get a new secret, then generate a fresh token. |
| Using a token that was replaced or revoked | Calls fail with `TokenException` (403). | Generate a new token. If this happens unexpectedly, check whether another process is also calling `/generate/token`. |

**TIP: Lost your authenticator device?**

There is no way to recover or re-display an existing secret. Log in to the website, choose
**Disable TOTP**, then run setup again for a fresh secret. If you can't log in to the website
at all, use the standard forgot-password / account-unlock flow, or contact
`instockssupport@indmoney.com` for a support-assisted disable.

---

## User Profile

Retrieves the profile information for the authenticated user. This is a useful endpoint to test if your access token is valid.

**Endpoint**
```
GET /user/profile
```

**Example Request**
```bash
curl --location 'https://api.indstocks.com/user/profile' \
--header 'Authorization: YOUR_ACCESS_TOKEN'
```

**Response Payload (Success)**
```json
{
  "status": "success",
  "data": {
    "user_id": "1234567",
    "email": "john.doe@example.com",
    "first_name": "John",
    "last_name": "Doe",
    "demat_id": "",
    "is_nse_onboarded": true,
    "is_bse_onboarded": true,
    "is_nse_fno_onboarded": true,
    "is_bse_fno_onboarded": true,
    "ucc": "1ABCDE2N7X",
    "is_ddpi_active": true
  }
}
```

**Response Fields**

| Field | Type | Description |
|---|---|---|
| `ucc` | string | Unique Client Code — the exchange-assigned client identifier. |
| `is_ddpi_active` | boolean | Whether Demat Debit and Pledge Instruction (DDPI) is active for this account, allowing delivery sells without a separate CDSL TPIN authorization. |

---

## Get Funds

Retrieves the funds utilization and availability for the authenticated user.

**Endpoint**
```
GET /funds
```

**Example Request**
```bash
curl --location 'https://api.indstocks.com/funds' \
--header 'Authorization: YOUR_ACCESS_TOKEN'
```

**Response Payload (Success)**
```json
{
    "status": "success",
    "data": {
        "sod_balance": 4996.47,
        "pledge_received": 0,
        "pledge_remained": 0,
        "detailed_avl_balance": {
            "option_sell": 2980.40,
            "future": 2980.40,
            "option_buy": 4449.65,
            "comm_option_buy": 2980.40,
            "eq_mis": 2980.40,
            "eq_cnc": 2980.40,
            "eq_mtf": 2980.40
        },
        "withdrawal_balance": 2983.47,
        "funds_added": 0,
        "funds_withdrawn": 0,
        "realized_pnl": -751.92,
        "unrealized_pnl": 62.15,
        "brokerage": 0,
        "eq_charges": 0,
        "fno_charges": 0
    }
}
```

**Response Fields**

| Field | Type | Description |
|---|---|---|
| `detailed_avl_balance.comm_option_buy` | number | Available balance for commodity option buying. |
| `brokerage` | number | Brokerage accrued for the day. |
| `eq_charges` | number | Equity segment charges accrued for the day. |
| `fno_charges` | number | F&O segment charges accrued for the day. |

---

## See Also
- [API Conventions](https://api-docs.indstocks.com/conventions/) — request/response format and rate limits
- [Glossary & Constants](https://api-docs.indstocks.com/glossary/) — TOTP fields, ID prefixes, shared enums
- [Error Bucket](https://api-docs.indstocks.com/errors/) — `TokenException` and other auth-related errors


---

# Source: https://api-docs.indstocks.com/instruments/

# Instruments

This endpoint provides a downloadable CSV file containing a list of all tradable instruments and their properties for a given market segment. This is essential for fetching the correct `security_id` to be used in other API calls, such as placing orders or subscribing to market data feeds.

| Request Type | Path                         | Description                                            |
|--------------|------------------------------|--------------------------------------------------------|
| **GET**      | [`/market/instruments`](#get-instrument-list) | Fetches the CSV file for a specific market segment.    |

---

## Get Instrument List

Retrieves a CSV file (often called a scrip master or instrument master) containing all tradable symbols for the specified segment.

**Endpoint**
```
GET /market/instruments
```

**Query Parameters**

| Parameter  | Description                                        |
|------------|----------------------------------------------------|
| `source`   | The market segment for which to fetch instruments. <br> **Enum**: `equity`, `fno`, `index` |

**Example Request**
```bash
curl --location 'https://api.indstocks.com/market/instruments?source=fno' \
--header 'Authorization: YOUR_ACCESS_TOKEN' \
--output instruments.csv
```
**NOTE**

The response for this endpoint is not JSON, but a raw CSV file. The example above shows how to save it directly to a file named `instruments.csv` using the `--output` flag in curl.

**CSV File Structure**

The downloaded file will contain the following columns:

| Column Name      | Description                                          |
|------------------|------------------------------------------------------|
| `EXCH`           | The exchange identifier (e.g., `NSE`, `BSE`).          |
| `SEGMENT`        | The market segment (e.g., `E` for Equity, `FNO`).      |
| `SECURITY_ID`    | The unique ID for the instrument.                    |
| `INSTRUMENT_NAME`| The type of instrument (e.g., `EQUITY`, `FUTCUR`).     |
| `EXPIRY_CODE`    | Numeric code for the expiry; `0` for non-derivatives.  |
| `TRADING_SYMBOL` | The symbol used for trading on the exchange.         |
| `LOT_UNITS`      | The lot size for F&O contracts.                      |
| `CUSTOM_SYMBOL`  | A more descriptive symbol for the instrument.        |
| `EXPIRY_DATE`    | The expiry date for derivative contracts.            |
| `STRIKE_PRICE`   | The strike price for options contracts.              |
| `OPTION_TYPE`    | The option type (`CE` for Call, `PE` for Put).       |
| `TICK_SIZE`      | The minimum price movement for the instrument.       |
| `EXPIRY_FLAG`    | Flag indicating expiry type (e.g., `M` for monthly). |
| `SEM_EXCH_INSTRUMENT_TYPE`| The instrument type as defined by the exchange.      |
| `SERIES`         | The series code (e.g., `EQ`).                        |
| `SYMBOL_NAME`    | The base symbol name (e.g., `HDFCBANK`).             |

**NOTE: `source=index` returns a different, three-column file**

The 16-column structure above applies to `source=equity` and `source=fno`. The index file is
**three columns only** — `EXCH`, `SEGMENT`, `SECURITY_ID` — for example:

```csv
EXCH,SEGMENT,SECURITY_ID
NSE,NIFTY 50,40000001
NSE,NIFTY IT,40000004
BSE,BSE Focused IT,40000129
```

Be aware that in this file the second column is **labelled `SEGMENT` but contains the index
name**, so parsing it by header name is misleading. Read it positionally.

**TIP: Getting an underlying's `SECURITY_ID` for the option chain**

The [Option Chain](https://api-docs.indstocks.com/utility/#option-chain) endpoint's `underlying-scrip` parameter takes the
`SECURITY_ID` of the **underlying**, which you look up here:

- **Index underlying** → `source=index` (e.g. `40000001` for NIFTY 50)
- **Stock underlying** → `source=equity`, using the **cash-market** row (e.g. `2885` for RELIANCE on NSE)

Do not use a `SECURITY_ID` from `source=fno` — those identify individual option and futures
contracts, not the underlying.

---

## See Also
- [Market Quotes](https://api-docs.indstocks.com/MarketQuote/) — use `SECURITY_ID` to build `scrip-codes`
- [Option Chain](https://api-docs.indstocks.com/utility/#option-chain) — use an underlying's `SECURITY_ID` as `underlying-scrip`
- [Order Management](https://api-docs.indstocks.com/normal_orders/) — use `SECURITY_ID` as `security_id` when placing orders
- [Error Bucket](https://api-docs.indstocks.com/errors/) — `DataException` for invalid instrument tokens


---

# Source: https://api-docs.indstocks.com/MarketQuote/

# Market Quotes

This section covers endpoints for retrieving real-time market data for one or more instruments, including full quotes, Last Traded Price (LTP), and market depth.

| Request Type | Path                         | Description                                            |
|--------------|------------------------------|--------------------------------------------------------|
| **GET**      | [`/market/quotes/full`](#get-full-market-quotes) | Retrieve full market quotes for one or more instruments. |
| **GET**      | [`/market/quotes/ltp`](#get-ltp-quote)      | Retrieve only the LTP for one or more instruments.     |
| **GET**      | [`/market/quotes/mkt`](#get-market-depth)   | Retrieve market depth for one or more instruments.     |

---

### Query Parameters

All quote endpoints accept a `scrip-codes` query parameter to specify the instrument(s).

| Parameter     | Description                                                                                                                                |
|---------------|--------------------------------------------------------------------------------------------------------------------------------------------|
| `scrip-codes` | A comma-separated list of instrument identifiers. Each identifier is a combination of the exchange segment and the instrument token from the [Instruments](https://api-docs.indstocks.com/instruments/) file. <br> **Format**: `SEGMENT_INSTRUMENTTOKEN` <br> **Example**: `NSE_3045,NFO_51011` |

---

## Get Full Market Quotes

This endpoint retrieves a comprehensive market data snapshot for up to 1000 instruments at once. The response includes OHLC, day's change, volume, circuit limits, and market depth.

**Endpoint**
```
GET /market/quotes/full
```

#### Example Request
```bash
curl --location 'https://api.indstocks.com/market/quotes/full?scrip-codes=NSE_3045' \
--header 'Authorization: YOUR_ACCESS_TOKEN'
```

#### Response Payload (Success)
```json
{
  "status": "success",
  "data": {
    "NSE_3045": {
      "live_price": 788.8,
      "day_change": -3.5,
      "day_change_percentage": -0.44,
      "day_low": 788.35,
      "day_high": 795.5,
      "day_open": 792.5,
      "prev_close": 792.3,
      "52week_high": 899,
      "52week_low": 680,
      "upper_circuit": 871.5,
      "lower_circuit": 713.1,
      "market_depth": { /* ... market depth object ... */ },
      "volume": 3546732
    }
  }
}
```

---

## Get LTP Quote

This endpoint retrieves only the Last Traded Price (LTP) for up to 1000 instruments. It is a lightweight alternative to the full quote endpoint.

**Endpoint**
```
GET /market/quotes/ltp
```

#### Example Request
```bash
curl --location 'https://api.indstocks.com/market/quotes/ltp?scrip-codes=NSE_3045' \
--header 'Authorization: YOUR_ACCESS_TOKEN'
```

#### Response Payload (Success)
```json
{
  "status": "success",
  "data": {
    "NSE_3045": {
      "live_price": 792.5
    }
  }
}
```

---

## Get Market Depth

This endpoint retrieves the 5-level market depth for one or more instruments.

**Endpoint**
```
GET /market/quotes/mkt
```

#### Example Request
```bash
curl --location 'https://api.indstocks.com/market/quotes/mkt?scrip-codes=NSE_3045' \
--header 'Authorization: YOUR_ACCESS_TOKEN'
```

#### Response Payload (Success)
```json
{
  "status": "success",
  "data": {
    "NSE_3045": {
      "market_depth": {
        "aggregate": {
          "total_buy": "5,82,909",
          "total_sell": "11,01,938",
          "buy_percentage": 34.6,
          "sell_percentage": 65.4
        },
        "depth": [
          { "buy": { "quantity": "6.00", "price": "788.95" }, "sell": { "quantity": "21.00", "price": "789.00" } },
          { "buy": { "quantity": "756.00", "price": "788.70" }, "sell": { "quantity": "255.00", "price": "789.05" } },
          { "buy": { "quantity": "456.00", "price": "788.65" }, "sell": { "quantity": "264.00", "price": "789.10" } },
          { "buy": { "quantity": "2,318", "price": "788.60" }, "sell": { "quantity": "1,792", "price": "789.15" } },
          { "buy": { "quantity": "1,644", "price": "788.55" }, "sell": { "quantity": "1,328", "price": "789.20" } }
        ]
      }
    }
  }
}
```

---

## See Also
- [Instruments](https://api-docs.indstocks.com/instruments/) — look up the `SECURITY_ID` used to build `scrip-codes`
- [Historical Data](https://api-docs.indstocks.com/historicalData/) — OHLCV candles instead of a live snapshot
- [WebSockets](https://api-docs.indstocks.com/Websockets/) — subscribe to continuous live updates instead of polling
- [Glossary & Constants](https://api-docs.indstocks.com/glossary/) — `SEGMENT_TOKEN` vs `SEGMENT:TOKEN` formats
- [Error Bucket](https://api-docs.indstocks.com/errors/) — `DataException` for invalid scrip codes


---

# Source: https://api-docs.indstocks.com/historicalData/

# Historical Data

The Historical Data API provides time-series OHLCV (Open, High, Low, Close, Volume) data for instruments across various intervals. This is ideal for charting, analysis, and building trading strategies.

Two endpoints share the same candle contract, the same intervals and the same per-call limits: one for instruments that are still trading, and one for F&O contracts that have expired. They differ only in how an instrument is identified. The expired-contract endpoint is [coming soon](#get-expired-contract-candles); its contract is documented below so you can build against it ahead of time.

| Request Type | Path                               | Description                             |
|--------------|------------------------------------|-----------------------------------------|
| **GET**      | [`/market/historical/{interval}`](#get-historical-data) | Fetches historical OHLCV data.          |
| **GET**      | [`/market/historical/expired/{interval}`](#get-expired-contract-candles) | Fetches historical OHLCV data for expired F&O contracts. *Coming soon.* |

---

## Get Historical Data

**Endpoint**
```
GET /market/historical/{interval}
```

**Path Parameters**

| Parameter  | Description                                                                  |
|------------|------------------------------------------------------------------------------|
| `interval` | The time interval for each candle. See the table of [Supported Intervals](#supported-intervals--maximum-time-range) below. |

**Query Parameters**

| Parameter     | Type   | Required | Description                                                                      |
|---------------|--------|----------|----------------------------------------------------------------------------------|
| `scrip-codes` | string | Yes      | A comma-separated list of **at most 5** instrument identifiers. Example: `NSE_3045,NFO_51011` |
| `start_time`  | int64  | Yes      | Start timestamp (Unix epoch milliseconds, inclusive).                              |
| `end_time`    | int64  | Yes      | End timestamp (Unix epoch milliseconds, exclusive).                                |

**NOTE: Up to 5 instruments per request**

`scrip-codes` takes a maximum of five identifiers; requests with more return
`400 {"debug_info": "Invalid scrip codes", "message": "Bad Request"}`. Repeated codes are
de-duplicated and count once. For a larger universe, batch your instruments five at a time.

The segment travels in each code's prefix — `NSE_` for cash, `NFO_` for F&O — so there is no
separate `segment` parameter to set.

**Example Request**
```bash
curl --location 'https://api.indstocks.com/market/historical/1minute?scrip-codes=NSE_3045&start_time=1750055540000&end_time=1750141940000' \
--header 'Authorization: YOUR_ACCESS_TOKEN'
```

**Response Format**
The response uses a `success` boolean. `data` is keyed by scrip code, and each scrip's `candles` is an array of **objects**.

| Candle field | Description |
|---|---|
| `ts` | Candle **open** time, Unix epoch **seconds** (IST). See [Candle timestamps](#candle-timestamps). |
| `o` | Open price |
| `h` | High price |
| `l` | Low price |
| `c` | Close price |
| `v` | Volume |

```json
{
  "success": true,
  "data": {
    "NSE_1594": {
      "candles": [
        { "ts": 1782877500, "o": 1007, "h": 1013.9, "l": 999.3, "c": 1000.4, "v": 2847163 },
        { "ts": 1782881100, "o": 1000.4, "h": 1002, "l": 996.7, "c": 999.6, "v": 1389042 }
      ]
    }
  }
}
```

**NOTE: Request times are milliseconds, candle times are seconds**

`start_time` and `end_time` are epoch **milliseconds**, while the `ts` on each returned candle
is epoch **seconds**. Multiply by 1000 when using a returned `ts` to build the next request.

**NOTE: Candle fields**

Candles carry `ts`, `o`, `h`, `l`, `c` and `v` across every interval and segment. For open
interest, use the [Option Chain](https://api-docs.indstocks.com/utility/#option-chain), which returns OI and previous OI
per leg.

**NOTE: Response shape**

- This endpoint wraps its response in a **`success` boolean** — check `success` rather than
  `status` when handling it.
- Each requested code gets its own key under `data` with its own `candles` array — they are
  not merged into a single list.
- Codes with no data in the requested window are not included under `data`, so read the keys
  returned rather than assuming one per code requested.

### Candle timestamps

`ts` is the time at which the candle **opens**. Each candle covers the half-open interval
`[ts, ts + interval)` — its own opening time is included, the next candle's opening time is not.

Worked example, on `5minute`:

| Candle `ts` | Covers the minutes | Next candle opens |
|-------------|--------------------|-------------------|
| `09:15`     | 09:15 – 09:19      | `09:20`           |
| `09:20`     | 09:20 – 09:24      | `09:25`           |
| `09:25`     | 09:25 – 09:29      | `09:30`           |

The `09:20` candle opens on the first trade at or after 09:20:00 and closes on the last trade
before 09:25:00. The rule is the same on every interval, and on both endpoints.

**NOTE: Deriving a closing time**

If your charting or backtesting engine indexes bars by closing time rather than opening time,
add the interval to `ts` as you read it: a `5minute` candle stamped `09:20` closes at `09:25`.

**NOTE: Intraday candles are anchored to the session open**

Buckets are aligned to **09:15 IST**, the start of the trading session, rather than to
wall-clock `:00`/`:05` boundaries. `5minute` candles fall at 09:15, 09:20, 09:25 …,
`15minute` candles at 09:15, 09:30, 09:45 …, and `60minute` candles at 09:15, 10:15,
11:15 …

---

## Get Expired Contract Candles

**NOTE: Coming soon**

This endpoint is not yet available on production. The contract below is settled and is
published so you can build against it in advance; requests to it will start returning data
once it is enabled. This page and the [Changelog](https://api-docs.indstocks.com/changelog/) will be updated when it goes
live.

The same OHLCV candles, for F&O contracts whose expiry has already passed —
[Get Historical Data](#get-historical-data) covers instruments that are still trading. Together
the two endpoints will give a strategy the full price history of a contract, from listing through
to expiry.

Contracts here are identified by **`trading_symbol`**, the identifier returned by
[Search Expired Contracts](https://api-docs.indstocks.com/contracts/#search-expired-contracts) and
[Contracts for an Expired Expiry](https://api-docs.indstocks.com/contracts/#get-contracts-for-an-expired-expiry). Exchanges
reuse numeric instrument tokens once a contract has expired, so `trading_symbol` is the stable
way to address a contract that is no longer trading.

**Endpoint**
```
GET /market/historical/expired/{interval}
```

**Path Parameters**

| Parameter  | Description                                                                  |
|------------|------------------------------------------------------------------------------|
| `interval` | The time interval for each candle. Same values and same per-call maximums as the live endpoint — see [Supported Intervals & Maximum Time Range](#supported-intervals--maximum-time-range). |

**Query Parameters**

| Parameter         | Type   | Required | Description                                                                      |
|-------------------|--------|----------|----------------------------------------------------------------------------------|
| `trading-symbols` | string | Yes      | A comma-separated list of **1 to 50** `trading_symbol`s. Example: `NIFTY26AUG2523950CE` |
| `start_time`      | int64  | Yes      | Start timestamp (Unix epoch milliseconds, inclusive).                             |
| `end_time`        | int64  | Yes      | End timestamp (Unix epoch milliseconds, exclusive).                               |
| `segment`         | string |          | The exchange derivatives segment the contracts belong to. <br> **Enum**: `NFO`, `BFO` <br> **Default**: `NFO` |

**NOTE: Up to 50 symbols per request**

`trading-symbols` takes between one and fifty identifiers; requests with more return
`400 {"debug_info": "trading-symbols must contain 1-50 symbols.", "message": "Bad request"}`.
[Get Historical Data](#get-historical-data) takes up to five `scrip-codes`, so size your
batching per endpoint rather than sharing one constant across both.

**NOTE: Set `segment` to reach BSE derivatives**

`segment` selects the exchange's derivatives segment. Only F&O contracts expire, so two
values apply:

| Value | Segment               |
|-------|-----------------------|
| `NFO` | NSE Derivatives (F&O) |
| `BFO` | BSE Derivatives (F&O) |

`NFO` is the default, so NSE contracts need no `segment` at all. **BSE contracts must be
requested with `segment=BFO`** — a BSE `trading_symbol` sent without it is looked up against
NSE, finds no match, and comes back absent from `data`.

These are the exchange-segment codes used across the API, the same ones that prefix a
`scrip-code` or a WebSocket instrument — see
[Instrument Code Formats](https://api-docs.indstocks.com/glossary/#instrument-code-formats). They are not the
`segment=DERIVATIVE` value taken by
[Contracts & Expiries](https://api-docs.indstocks.com/contracts/#shared-parameters).

`trading-symbols`, `start_time`, `end_time` and `segment` are the complete query contract; your
account is resolved from the access token.

**Example Request**

These calls will work once the endpoint is enabled. NSE derivatives, using the default segment:

```bash
curl --location 'https://api.indstocks.com/market/historical/expired/1minute?trading-symbols=NIFTY26AUG2523950CE&start_time=1787542200000&end_time=1787652000000' \
--header 'Authorization: YOUR_ACCESS_TOKEN'
```

BSE derivatives, with `segment=BFO`:

```bash
curl --location 'https://api.indstocks.com/market/historical/expired/1minute?trading-symbols=SENSEX26AUG2581000CE&segment=BFO&start_time=1787542200000&end_time=1787652000000' \
--header 'Authorization: YOUR_ACCESS_TOKEN'
```

**Response Format**
Identical in shape to [Get Historical Data](#get-historical-data) — a `success` boolean, `data`
keyed per instrument, each with an array of `{ts, o, h, l, c, v}` candle objects. The only
difference is the key: `trading_symbol` rather than scrip code.

```json
{
  "success": true,
  "data": {
    "NIFTY26AUG2523950CE": {
      "candles": [
        { "ts": 1787543100, "o": 374.1, "h": 395.15, "l": 374.1, "c": 390, "v": 19955 },
        { "ts": 1787543160, "o": 390, "h": 402.5, "l": 388.2, "c": 399.7, "v": 14320 }
      ]
    }
  }
}
```

The candle contract is identical to the live endpoint. `ts` is the candle's **open** time in
epoch **seconds** and covers `[ts, ts + interval)` (see
[Candle timestamps](#candle-timestamps)), `start_time` and `end_time` are epoch
**milliseconds**, intraday buckets are anchored to the 09:15 IST session open, and each interval
carries the same [per-call maximum range](#supported-intervals--maximum-time-range).

**NOTE: Read the keys returned**

`data` carries an entry for each requested symbol that has candles in the window. A symbol
with no data in that window, and any symbol the endpoint does not recognise — a numeric
`security_id` sent in place of a trading symbol, or a BSE contract requested without
`segment=BFO` — is absent from `data` rather than returned empty. Match the keys in the
response against the symbols you requested.

A zero-width window, where `start_time` equals `end_time`, returns `"candles": null`.

**NOTE: Candle fields**

Candles carry `ts`, `o`, `h`, `l`, `c` and `v`, the same set as the live endpoint. For open
interest, use the [Option Chain](https://api-docs.indstocks.com/utility/#option-chain), which returns OI and previous OI
per leg.

---

## Supported Intervals & Maximum Time Range

| Interval Label | Value        | Max Range Per Call |
|----------------|--------------|--------------------|
| 1 Minute       | `1minute`    | 7 Days          |
| 2 Minutes      | `2minute`    | 7 Days          |
| 3 Minutes      | `3minute`    | 7 Days          |
| 4 Minutes      | `4minute`    | 7 Days          |
| 5 Minutes      | `5minute`    | 7 Days          |
| 10 Minutes     | `10minute`   | 7 Days          |
| 15 Minutes     | `15minute`   | 7 Days          |
| 30 Minutes     | `30minute`   | 7 Days          |
| 1 Hour         | `60minute`   | 15 Days         |
| 2 Hours        | `120minute`  | 15 Days         |
| 3 Hours        | `180minute`  | 15 Days         |
| 4 Hours        | `240minute`  | 15 Days         |
| 1 Day          | `1day`       | 1 Year          |
| 1 Week         | `1week`      | 1 Year          |
| 1 Month        | `1month`     | 1 Year          |

These intervals and maximums apply to both [Get Historical Data](#get-historical-data) and
[Get Expired Contract Candles](#get-expired-contract-candles).

This is the ceiling on a **single call**, not the depth of history available. Reaching further
back is a matter of making more calls — one per window, walking backwards — not of asking for a
wider window.

The maximum applies to the **window you request**, not to the span you get back. On `1week` and
`1month` the returned candles land on week and month boundaries, so a full one-year request comes
back spanning slightly less than a year.

**NOTE: Use the exact interval values**

The table above is the complete set. Hour intervals are expressed in minutes — `60minute`,
`120minute`, `180minute`, `240minute` — and the daily, weekly and monthly intervals take the
`1` prefix: `1day`, `1week`, `1month`. Other values return
`400 {"debug_info": "invalid interval.", "message": "Bad request"}`.

### Paging by date range

**WARNING: A wider request returns the maximum window, not an error**

The maximum is applied to every call. A request for a window wider than the interval's
maximum returns `200` with `"success": true` and the maximum window's data, so the width of
the request is what determines the coverage you receive.

Both [Get Historical Data](#get-historical-data) and
[Get Expired Contract Candles](#get-expired-contract-candles) work this way, and neither
returns a continuation token — paging is by date range. Size each request to the maximum for
its interval, and check the candles you receive against the candles the window should
contain.

To build a series longer than one window, issue one request per maximum-width window and walk
backwards, stitching the results together.

---

## Notes
- `start_time`/`end_time` query parameters are in **IST** and **Unix epoch milliseconds**; the
  `ts` field inside each returned candle is in **Unix epoch seconds**.
- Keep each request within the maximum range for the chosen interval. A wider window returns the
  maximum range's data rather than an error, so size each request to the limit in the table above
  and page by date range for longer histories.
- `start_time` must not be later than `end_time`, which returns
  `400 {"debug_info": "start_time must not be after end_time.", "message": "Bad request"}`.
- If requesting multiple instruments, each one gets its own `candles` array under `data` — they
  are not merged into a single list.
- The two endpoints take different identifiers and different batch sizes: `scrip-codes` with a
  maximum of five for live instruments, `trading-symbols` with a maximum of fifty for expired
  contracts.
- Both endpoints return the same candle fields, `ts`, `o`, `h`, `l`, `c` and `v`. For open
  interest, use the [Option Chain](https://api-docs.indstocks.com/utility/#option-chain).

---

## See Also
- [Market Quotes](https://api-docs.indstocks.com/MarketQuote/) — live snapshot instead of historical candles
- [Instruments](https://api-docs.indstocks.com/instruments/) — look up the `SECURITY_ID` used to build `scrip-codes`
- [Contracts & Expiries](https://api-docs.indstocks.com/contracts/) — find the `trading_symbol` of an expired contract
- [Error Bucket](https://api-docs.indstocks.com/errors/) — `DataException` for invalid interval/time-range parameters

---


---

# Source: https://api-docs.indstocks.com/contracts/

# Contracts & Expiries

These endpoints let you discover individual derivative contracts and the expiry dates they belong
to — both currently trading and long expired — by querying on an underlying instead of downloading
and filtering the whole [Instruments Master](https://api-docs.indstocks.com/instruments/) CSV. The expired side is what makes
historical options work: it tells you which strikes existed for an expiry that has already passed.

| Request Type | Path                                            | Description                                          |
|--------------|-------------------------------------------------|------------------------------------------------------|
| **GET**      | [`/market/instruments/search`](#search-contracts) | Search currently trading contracts for an underlying. |
| **GET**      | [`/market/instruments/expiries`](#list-expiries) | List upcoming expiry dates for an underlying.        |
| **GET**      | [`/market/instruments/expired/search`](#search-expired-contracts) | Search contracts whose expiry has passed.            |
| **GET**      | [`/market/instruments/expired/expiries`](#list-expired-expiries) | List past expiry dates within a window.              |
| **GET**      | [`/market/instruments/expired/contracts`](#get-contracts-for-an-expired-expiry) | Full contract chain for one past expiry.             |

---

## Shared Parameters

The five endpoints draw on a common set of parameters and enums.

| Parameter         | Type   | Description                                                                 |
|-------------------|--------|-----------------------------------------------------------------------------|
| `underlying`      | string | The underlying symbol, e.g. `NIFTY`, `RELIANCE`. Required on every endpoint. |
| `segment`         | string | Market segment. Required on every endpoint. **Only `DERIVATIVE` is supported.** |
| `instrument_type` | string | Narrows results to one contract type. <br> **Enum**: `OPTIDX`, `OPTSTK`, `FUTIDX`, `FUTSTK` |
| `option_type`     | string | Narrows options to calls or puts. <br> **Enum**: `CE`, `PE`                  |
| `expiry`          | date   | A single expiry date, `YYYY-MM-DD`.                                          |

All date values, in requests and in responses, are `YYYY-MM-DD`.

**NOTE: Supported segment**

These endpoints serve the derivatives segment, so `segment=DERIVATIVE` is the value to pass.
Support for `segment=EQUITY` is on the roadmap; for cash-market instruments today, use the
[Instruments Master](https://api-docs.indstocks.com/instruments/) CSV with `source=equity`.

---

## Search Contracts

Returns the individual contracts currently trading on an underlying — options, futures, or both.

**Endpoint**
```
GET /market/instruments/search
```

**Query Parameters**

| Parameter         | Type    | Required | Description                                                        |
|-------------------|---------|:--------:|--------------------------------------------------------------------|
| `underlying`      | string  | ✅       | The underlying symbol, e.g. `NIFTY`.                               |
| `segment`         | string  | ✅       | `DERIVATIVE`.                                                      |
| `instrument_type` | string  |          | `OPTIDX`, `OPTSTK`, `FUTIDX` or `FUTSTK`. Omit to get every type.  |
| `expiry`          | date    |          | Restrict to one expiry, `YYYY-MM-DD`.                              |
| `strike_from`     | number  |          | Lower bound on strike price. Options only.                         |
| `strike_to`       | number  |          | Upper bound on strike price. Options only.                         |
| `option_type`     | string  |          | `CE` or `PE`. Options only.                                        |
| `page`            | integer |          | Page number. Default `1`.                                          |
| `page_size`       | integer |          | Results per page. Default `50`, maximum `100`.                     |

**Example Request**
```bash
curl --location 'https://api.indstocks.com/market/instruments/search?underlying=NIFTY&segment=DERIVATIVE&instrument_type=FUTIDX' \
--header 'Authorization: YOUR_ACCESS_TOKEN'
```

**Response Payload (Success)**
```json
{
  "status": "success",
  "data": {
    "count": 804,
    "page": 1,
    "page_size": 50,
    "instruments": [
      {
        "security_id": "58072",
        "trading_symbol": "NIFTY26AUG25FUT",
        "expiry": "2026-08-25",
        "strike_price": null,
        "option_type": null,
        "lot_size": 75
      }
    ]
  }
}
```

**Response Fields**

| Field                    | Type    | Description                                                          |
|--------------------------|---------|----------------------------------------------------------------------|
| `count`                  | integer | Total contracts matching the filters, across all pages.              |
| `page` / `page_size`     | integer | Echo of the pagination you requested.                                |
| `instruments[].security_id`   | string  | The instrument's numeric ID, for `scrip-codes` and order placement. |
| `instruments[].trading_symbol` | string  | The exchange trading symbol.                                       |
| `instruments[].expiry`   | date    | Contract expiry, `YYYY-MM-DD`.                                       |
| `instruments[].strike_price` | number  | Strike price for options; `null` for futures.                     |
| `instruments[].option_type`  | string  | `CE` or `PE` for options; `null` for futures.                     |
| `instruments[].lot_size` | integer | Contract lot size.                                                   |

**NOTE: Omitting `instrument_type` mixes options and futures**

With no `instrument_type` filter the response contains both, distinguishable by the `null`
`strike_price` and `option_type` on futures rows. Filter explicitly when you want one or the
other.

**NOTE: Index futures are listed on monthly expiries**

Index futures follow the exchange's monthly expiry cycle, so `instrument_type=FUTIDX` returns
rows for monthly expiries. A weekly expiry has options but no index futures.

---

## List Expiries

Returns the upcoming expiry dates available for an underlying — the values you feed into `expiry`
elsewhere.

**Endpoint**
```
GET /market/instruments/expiries
```

**Query Parameters**

| Parameter    | Type   | Required | Description                          |
|--------------|--------|:--------:|--------------------------------------|
| `underlying` | string | ✅       | The underlying symbol, e.g. `NIFTY`. |
| `segment`    | string | ✅       | `DERIVATIVE`.                        |

**Example Request**
```bash
curl --location 'https://api.indstocks.com/market/instruments/expiries?underlying=NIFTY&segment=DERIVATIVE' \
--header 'Authorization: YOUR_ACCESS_TOKEN'
```

**Response Payload (Success)**

`data` is a flat array of dates in **ascending** order, covering upcoming expiries only.

```json
{
  "status": "success",
  "data": ["2026-08-25", "2026-09-01", "2026-09-08"]
}
```

---

## Search Expired Contracts

The same search as [Search Contracts](#search-contracts), over contracts whose expiry has already
passed.

**Endpoint**
```
GET /market/instruments/expired/search
```

**Query Parameters**

Every parameter from [Search Contracts](#search-contracts), plus a mandatory expiry window:

| Parameter     | Type | Required | Description                                              |
|---------------|------|:--------:|----------------------------------------------------------|
| `expiry_from` | date | ✅       | Start of the expiry window, `YYYY-MM-DD`.                |
| `expiry_to`   | date | ✅       | End of the expiry window, `YYYY-MM-DD`. Max span 5 years. |

**Example Request**
```bash
curl --location 'https://api.indstocks.com/market/instruments/expired/search?underlying=NIFTY&segment=DERIVATIVE&instrument_type=OPTIDX&expiry_from=2024-08-19&expiry_to=2026-07-20&page=1&page_size=50' \
--header 'Authorization: YOUR_ACCESS_TOKEN'
```

**Response Payload (Success)**

Identical in shape to [Search Contracts](#search-contracts), with one difference: there is **no
`security_id`**.

```json
{
  "status": "success",
  "data": {
    "count": 804,
    "page": 1,
    "page_size": 50,
    "instruments": [
      {
        "trading_symbol": "NIFTY26JUL2825950CE",
        "expiry": "2026-07-28",
        "strike_price": 25950,
        "option_type": "CE",
        "lot_size": 75
      }
    ]
  }
}
```

**NOTE: Expired contracts are identified by `trading_symbol`**

Exchanges reuse numeric instrument tokens once a contract has expired, so `trading_symbol` is
the stable identifier for historical contracts and is what this endpoint returns. It is what
[Get Expired Contract Candles](https://api-docs.indstocks.com/historicalData/#get-expired-contract-candles) will take as
`trading-symbols` once that endpoint is available.

Both `expiry_from` and `expiry_to` are mandatory, the window may not span more than **5 years**, and
an inverted window (`expiry_from` later than `expiry_to`) is rejected.

---

## List Expired Expiries

Returns past expiry dates for an underlying within a window.

**Endpoint**
```
GET /market/instruments/expired/expiries
```

**Query Parameters**

| Parameter     | Type   | Required | Description                                              |
|---------------|--------|:--------:|----------------------------------------------------------|
| `underlying`  | string | ✅       | The underlying symbol, e.g. `NIFTY`.                     |
| `segment`     | string | ✅       | `DERIVATIVE`.                                            |
| `expiry_from` | date   | ✅       | Start of the window, `YYYY-MM-DD`.                       |
| `expiry_to`   | date   | ✅       | End of the window, `YYYY-MM-DD`. Max span 1 year.        |

**Example Request**
```bash
curl --location 'https://api.indstocks.com/market/instruments/expired/expiries?underlying=NIFTY&segment=DERIVATIVE&expiry_from=2025-10-23&expiry_to=2026-08-09' \
--header 'Authorization: YOUR_ACCESS_TOKEN'
```

**Response Payload (Success)**

`data` is a flat array of dates in **descending** order — newest expiry first, the opposite of
[List Expiries](#list-expiries).

```json
{
  "status": "success",
  "data": ["2026-08-04", "2026-07-28", "2026-07-21"]
}
```

---

## Get Contracts for an Expired Expiry

Returns the full contract chain for one expiry that has already passed.

**Endpoint**
```
GET /market/instruments/expired/contracts
```

**Query Parameters**

| Parameter         | Type   | Required | Description                                                            |
|-------------------|--------|:--------:|------------------------------------------------------------------------|
| `underlying`      | string | ✅       | The underlying symbol, e.g. `NIFTY`.                                   |
| `segment`         | string | ✅       | `DERIVATIVE`.                                                          |
| `expiry`          | date   | ✅       | The expiry, `YYYY-MM-DD`. Must be a date returned by [List Expired Expiries](#list-expired-expiries). |
| `instrument_type` | string |          | `OPTIDX`, `OPTSTK`, `FUTIDX` or `FUTSTK`. Omit to get every type.      |

**Example Request**
```bash
curl --location 'https://api.indstocks.com/market/instruments/expired/contracts?underlying=NIFTY&segment=DERIVATIVE&expiry=2026-07-28' \
--header 'Authorization: YOUR_ACCESS_TOKEN'
```

**Response Payload (Success)**

`data` is a **flat array** of contracts — not the paginated object the two search endpoints return.

```json
{
  "status": "success",
  "data": [
    {
      "trading_symbol": "NIFTY26JUL2825950CE",
      "instrument_type": "OPTIDX",
      "strike_price": 25950,
      "option_type": "CE",
      "lot_size": 75,
      "expiry": "2026-07-28"
    }
  ]
}
```

**NOTE: The full chain arrives in a single response**

This endpoint returns the complete chain for the expiry in one array, so no pagination is
needed. A NIFTY expiry is typically **460–480 rows** — a `CE` and a `PE` at each strike, plus
the futures contract on monthly expiries. Size your client for the full chain, and pass
`instrument_type` when you only need one contract type.

**NOTE: Take `expiry` from the expiry list**

Pass a date returned by [List Expired Expiries](#list-expired-expiries) for the same
underlying. Other dates return
`400 {"debug_info": "Invalid or unknown underlying/segment/expiry passed", "message": "Bad Request"}`.

---

## Errors

Validation errors on these endpoints carry two fields: `message` with the error category, and
`debug_info` with the specific detail.

```json
{
  "debug_info": "segment=EQUITY is not yet supported on this endpoint; use segment=DERIVATIVE",
  "message": "Not Supported"
}
```

Surface `debug_info` in your logs — it identifies which parameter needs attention. See
[Error Bucket](https://api-docs.indstocks.com/errors/) for the response shapes used across the API.

---

## See Also
- [Instruments Master](https://api-docs.indstocks.com/instruments/) — the full CSV dump, and the only place to look up cash-market instruments
- [Historical Data](https://api-docs.indstocks.com/historicalData/) — fetch candles for the contracts you find here; candles for expired contracts are [coming soon](https://api-docs.indstocks.com/historicalData/#get-expired-contract-candles)
- [Option Chain](https://api-docs.indstocks.com/utility/#option-chain) — live strike-by-strike quotes for a current expiry
- [Error Bucket](https://api-docs.indstocks.com/errors/) — error shapes and `error_type` values


---

# Source: https://api-docs.indstocks.com/Websockets/

# WebSocket Streaming

Our WebSocket API provides a fast, efficient, and low-latency way to receive real-time data, including market quotes and order status updates. This is the preferred method for streaming high-frequency data.

We offer two distinct WebSocket endpoints for different types of real-time data:

- **Price Feed WebSocket** - For live market data including LTP (Last Traded Price) and real-time quotes
- **Order Updates WebSocket** - For real-time updates on your order statuses and trade confirmations

Authentication for both endpoints is handled via an `Authorization` header passed during the initial connection handshake.

```
Authorization: YOUR_ACCESS_TOKEN
```

---

## Price Feed

Use this endpoint to stream live market data for instruments.

**Endpoint**: `wss://ws-prices.indstocks.com/api/v1/ws/prices`

**Subscription**

Once connected, you send JSON messages to subscribe to or unsubscribe from instrument feeds.

**Request Structure**
A subscription message consists of an `action`, a `mode`, and an array of `instruments`.

| Parameter     | Type    | Description                                                     |
|---------------|---------|-----------------------------------------------------------------|
| `action`      | string  | The action to perform. **Enum**: `"subscribe"`, `"unsubscribe"`   |
| `mode`        | string  | The data mode. **Enum**: `"ltp"`, `"quote"`                       |
| `instruments` | array   | An array of instrument tokens to subscribe to.                  |

**Instrument Format**
Instrument tokens are strings that identify a specific security or index, formatted as `SEGMENT:TOKEN`.

| Type                | Prefix    | Example                 |
|---------------------|-----------|-------------------------|
| NSE Equity          | `NSE:`    | `"NSE:2885"`            |
| BSE Equity          | `BSE:`    | `"BSE:500325"`          |
| NSE Derivatives (F&O) | `NFO:`    | `"NFO:51011"`           |
| BSE Derivatives (F&O) | `BFO:`    | `"BFO:12345"`           |
| NSE Index           | `NIDX:`   | `"NIDX:26000"`          |
| BSE Index           | `BIDX:`   | `"BIDX:1"`              |

**Subscription Examples**

*   **To subscribe to LTP mode for an instrument:**
    ```json
    {
        "action":"subscribe",
        "mode": "ltp",
        "instruments": ["NSE:2885"]
    }
    ```

*   **To subscribe to Quote mode:**
    ```json
    {
        "action":"subscribe",
        "mode": "quote",
        "instruments": ["NSE:2885"]
    }
    ```

**Data Response**

The data from the server will be a JSON string. You will need to parse this string to get the JSON object.

*   **LTP Response Format:**
    ```json
    {
        "mode": "ltp",
        "instrument": "2885",
        "timestamp": 1750138351089,
        "data": {
            "ltp": 1426
        }
    }
    ```

---

## Order Updates Feed

This endpoint streams all real-time updates for your orders.

**Endpoint**: `wss://ws-order-updates.indstocks.com/api/v1/ws/trades`

**Subscription**

To start receiving order updates, send a single subscription message after connecting.

*   **Subscription Message:**
    ```json
    {
        "action": "subscribe",
        "mode": "order_update"
    }
    ```
Once subscribed, all updates for your orders (e.g., placement, execution, cancellation) will be pushed to you automatically.

**Order Update Response**

The server will push a JSON message for any change in an order's state.

*   **Example Order Update:**
    ```json
    {
      "type": "order",
      "order_id": "INDM20250512ABC123",
      "order_status": "PARTIALLY_EXECUTED",
      "filled_quantity": 5,
      "remaining_quantity": 5,
      "average_price": 2500.40,
      "timestamp": 1678886530456
    }
    ```

**NOTE: Heartbeats**

The server may send periodic heartbeat messages to keep the connection alive. Your client should be configured to handle these, typically by ignoring them.

---

## See Also
- [Market Quotes](https://api-docs.indstocks.com/MarketQuote/) — REST alternative for on-demand (rather than streaming) quotes
- [Orders](https://api-docs.indstocks.com/normal_orders/) — reconcile order state via [Get Order Book](https://api-docs.indstocks.com/normal_orders/#get-order-book) instead of blindly retrying after a dropped connection
- [Glossary & Constants](https://api-docs.indstocks.com/glossary/) — `SEGMENT:TOKEN` instrument format used only on WebSocket


---

# Source: https://api-docs.indstocks.com/utility/

# Options Toolkit

This section covers the option chain endpoint, which returns the full strike ladder for an underlying
along with **Greeks and implied volatility in the same response**.

| Request Type | Path                                                | Description                                                        | Status |
|--------------|-----------------------------------------------------|--------------------------------------------------------------------|--------|
| **GET**      | [`/market/option-chain`](#option-chain)             | Get the option chain for an underlying, including Greeks and IV    | Live |

---

## Option Chain

Retrieves the option chain for one underlying and one expiry. Each strike returns both the call (`ce`)
and put (`pe`) leg with live price, open interest, volume, top-of-book bid/ask, implied volatility and
Greeks — so a single call is enough to build a chain view or drive a strategy.

**Endpoint**
```
GET /market/option-chain
```

**Query Parameters**

| Parameter          | Required | Description                                                                                                         |
|--------------------|----------|---------------------------------------------------------------------------------------------------------------------|
| `exchange`         | Yes      | The exchange of the option contracts. <br> **Enum**: `NSE`, `BSE`                                                   |
| `segment`          | Yes      | The segment of the **underlying**, which determines where its `underlying-scrip` comes from. <br> **Enum**: `INDEX`, `EQUITY` |
| `underlying-scrip` | Yes      | The `SECURITY_ID` of the **underlying** — not of an option contract. See [Finding `underlying-scrip`](#finding-underlying-scrip). |
| `expiry`           | Yes      | Contract expiry in `YYYY-MM-DD` format (e.g. `2026-08-18`).                                                         |
| `strike_count`     | No       | Number of strikes to return **on each side** of the at-the-money strike. Defaults to `10`.                          |

#### Example Request
```bash
curl --location 'https://api.indstocks.com/market/option-chain?exchange=NSE&segment=INDEX&underlying-scrip=40000001&expiry=2026-08-18&strike_count=10' \
--header 'Authorization: YOUR_ACCESS_TOKEN'
```

#### Response Payload (Success)
```json
{
  "status": "success",
  "data": {
    "underlying_ltp": 24471.7,
    "expiry": "2026-08-18",
    "strikes": {
      "24450": {
        "ce": {
          "security_id": "45108",
          "trading_symbol": "NIFTY-Aug2026-24450-CE",
          "last_price": 167.9,
          "previous_close_price": 274.95,
          "oi": 1608490,
          "previous_oi": 1606988,
          "volume": 9079330,
          "top_bid_price": 166.05,
          "top_bid_quantity": 195,
          "top_ask_price": 167.5,
          "top_ask_quantity": 130,
          "iv": 10.5,
          "greeks": {
            "delta": 0.56,
            "gamma": 0.0011,
            "theta": -10.04,
            "vega": 13.39
          }
        },
        "pe": {
          "security_id": "45109",
          "trading_symbol": "NIFTY-Aug2026-24450-PE",
          "last_price": 117.65,
          "previous_close_price": 88.9,
          "oi": 1579370,
          "previous_oi": 1579129,
          "volume": 11974495,
          "top_bid_price": 117.65,
          "top_bid_quantity": 195,
          "top_ask_price": 119.4,
          "top_ask_quantity": 195,
          "iv": 10.4,
          "greeks": {
            "delta": -0.44,
            "gamma": 0.0011,
            "theta": -9.95,
            "vega": 13.39
          }
        }
      },
      "24500": { "ce": { /* ... */ }, "pe": { /* ... */ } }
    }
  }
}
```

**Response Fields**

| Field | Description |
|-------|-------------|
| `underlying_ltp` | Last traded price of the underlying. |
| `expiry` | The expiry the returned chain belongs to, `YYYY-MM-DD`. |
| `strikes` | An **object keyed by strike price**, each holding a `ce` and a `pe` leg. |

Each `ce` / `pe` leg contains:

| Field | Description |
|-------|-------------|
| `security_id` | The contract's `SECURITY_ID`. Pass this straight to [Place Order](https://api-docs.indstocks.com/normal_orders/) as `security_id`. |
| `trading_symbol` | The contract's exchange trading symbol, matching `TRADING_SYMBOL` in the [Instruments](https://api-docs.indstocks.com/instruments/) file. |
| `last_price` | Last traded price of the contract. |
| `previous_close_price` | Previous close of the contract. |
| `oi` | Current open interest. |
| `previous_oi` | Previous day's open interest — subtract to get the OI change. |
| `volume` | Day's traded volume. |
| `top_bid_price` / `top_bid_quantity` | Best bid and its quantity. |
| `top_ask_price` / `top_ask_quantity` | Best ask and its quantity. |
| `iv` | Implied volatility, as a **percentage** (e.g. `10.5` means 10.5%). |
| `greeks` | Object containing `delta`, `gamma`, `theta` and `vega`. |

**NOTE: Response Notes**

- `strikes` is a **JSON object keyed by strike price, not an array**. JSON object key order is not
  guaranteed, so sort the keys numerically if you need an ordered ladder.
- `strike_count` is the number of strikes **per side** of the at-the-money strike, so the response
  contains `(2 × strike_count) + 1` strikes. `strike_count=3` returns 7 strikes; the default of
  `10` returns 21.
- `iv` is a **percentage** and is a sibling of `greeks`, not a member of it.
- `greeks` contains exactly `delta`, `gamma`, `theta` and `vega`. There is no `rho`.
- The response does not include an OI-change field, a put-call ratio, the list of other expiries,
  or `lot_size`/`tick_size`. Compute OI change from `oi` and `previous_oi`; take lot and tick size
  from the [Instruments](https://api-docs.indstocks.com/instruments/) file.
- Market depth is **top-of-book only** (one bid and one ask). For 5-level depth on a specific
  contract, use [Market Depth](https://api-docs.indstocks.com/MarketQuote/#get-market-depth).

### Finding `underlying-scrip`

`underlying-scrip` is the `SECURITY_ID` of the **underlying**, which you look up in the
[Instruments](https://api-docs.indstocks.com/instruments/) file. Which file depends on the `segment` you are querying:

| `segment` | Instruments source | Example |
|-----------|--------------------|---------|
| `INDEX`   | `/market/instruments?source=index`  | `40000001` — NIFTY 50 |
| `EQUITY`  | `/market/instruments?source=equity` | `2885` — RELIANCE on NSE |

**WARNING: Use the underlying's ID, not a contract's**

A common mistake is passing the `SECURITY_ID` of an option or futures contract from
`source=fno`. That is the contract's ID, not the underlying's, and it will be rejected. For a
stock underlying use its **cash-market** row from `source=equity`.

**Example — RELIANCE option chain**
```bash
curl --location 'https://api.indstocks.com/market/option-chain?exchange=NSE&segment=EQUITY&underlying-scrip=2885&expiry=2026-08-25&strike_count=5' \
--header 'Authorization: YOUR_ACCESS_TOKEN'
```

#### Errors

| Condition | HTTP | Response |
|-----------|------|----------|
| Any of `exchange`, `segment`, `underlying-scrip` or `expiry` is missing or invalid | `400` | `{"message": "Bad Request", "debug_info": "Invalid exchange, segment, underlying-scrip or expiry passed"}` |
| `Authorization` header not sent | `400` | `{"message": "authorization not sent in request. Please try again.", "success": false}` |
| Rate limit exceeded | `429` | `{"error": "Rate limit exceeded", "success": false}` |

**WARNING: Error shapes on this endpoint differ from the standard envelope**

This endpoint does not use the `{"status": "error", "error_type": "..."}` envelope described in
the [Error Bucket](https://api-docs.indstocks.com/errors/). Check the HTTP status code first, then read whichever of
`debug_info`, `message` or `error` is present. Note also that an invalid `expiry` **format**
(for example `20260818` instead of `2026-08-18`) produces the same `400` as an unknown
underlying, so validate the format on your side before calling.

---

## See Also
- [Instruments](https://api-docs.indstocks.com/instruments/) — look up the `SECURITY_ID` used as `underlying-scrip`
- [Market Quotes](https://api-docs.indstocks.com/MarketQuote/) — live quotes and 5-level depth for a specific contract
- [Order Management](https://api-docs.indstocks.com/normal_orders/) — place an order using a leg's `security_id`
- [Glossary & Constants](https://api-docs.indstocks.com/glossary/) — `segment` values and instrument code formats
- [Error Bucket](https://api-docs.indstocks.com/errors/) — error shapes and retry guidance


---

# Source: https://api-docs.indstocks.com/normal_orders/

# Orders

This section outlines the APIs for placing, modifying, canceling, and retrieving standard trading orders.

| Request Type | Path                                     | Description                    |
|--------------|------------------------------------------|--------------------------------|
| **POST**     | [`/order`](#place-order)                 | Place a new order              |
| **POST**     | [`/order/modify`](#modify-order)         | Modify a pending order         |
| **POST**     | [`/order/cancel`](#cancel-order)         | Cancel a pending order         |
| **GET**      | [`/order-book`](#get-order-book)         | Get the daily order book       |
| **GET**      | [`/order`](#get-order-details)           | Get details for a single order |
| **GET**      | [`/order/trades`](#get-trades)           | Get trades for a single order  |
| **GET**      | [`/trade-book`](#get-trade-book)         | Get trade book for a segment   |

---

## Order Status Types

The following table describes the various order statuses that can be returned by the API:

| Status                      | Description                                                                 |
|-----------------------------|-----------------------------------------------------------------------------|
| **QUEUED**                  | Order has been queued for processing                                        |
| **O-PENDING**               | After Market Order (AMO) is pending execution                               |
| **SL-PENDING**              | Stop Loss order is pending trigger                                          |
| **PROCESSING**              | Order is currently being processed                                          |
| **ABORTED**                 | Order was aborted due to system or validation issues                        |
| **INITIATED**               | Order has been initiated and sent to the exchange                           |
| **SUCCESS**                 | Order has been successfully executed                                         |
| **CANCELLED**               | Order has been cancelled by user or system                                  |
| **MODIFIED**                | Order has been successfully modified                                         |
| **PENDING**                 | Order is pending execution at the exchange                                  |
| **EXPIRED**                 | Order has expired without execution                                          |
| **FAILED**                  | Order execution failed due to technical or other issues                     |
| **PARTIALLY FILLED**        | Order has been partially executed                                            |
| **PARTIALLY FILLED - CANCELLED** | Order was partially executed and remaining quantity was cancelled     |
| **PARTIALLY FILLED - EXPIRED**   | Order was partially executed and remaining quantity expired           |

---

## Place Order

This API allows you to place a new standard order.

**Endpoint**
```
POST /order
```

**Request Body**

| Parameter     | Type    | Mandatory | Description                                                                 |
|---------------|---------|:---------:|-----------------------------------------------------------------------------|
| `txn_type`    | string  | ✅        | The transaction type. **Enum**: `"BUY"`, `"SELL"`                             |
| `exchange`    | string  | ✅        | The exchange to place the order on. **Enum**: `"NSE"`, `"BSE"`                |
| `segment`     | string  | ✅        | The market segment. **Enum**: `"DERIVATIVE"`, `"EQUITY"`                      |
| `product`     | string  | ✅        | The product type. **Enum**: `"MARGIN"`, `"INTRADAY"`, `"CNC"`                 |
| `order_type`  | string  | ✅        | The type of order. **Enum**: `"LIMIT"`, `"MARKET"`. Note: `MARKET` orders are automatically converted to `LIMIT` at the live price (see note below). |
| `validity`    | string  | ✅        | The order validity. **Enum**: `"DAY"`, `"IOC"`                                |
| `security_id` | string  | ✅        | The unique identifier for the instrument.                                   |
| `qty`         | integer | ✅        | The quantity of the instrument to trade.                                    |
| `algo_id`     | string  | ✅        | Algo identifier for the order. Use `"99999"` for NSE, `"9999999999999999"` for BSE orders. |
| `limit_price` | number  | ❌        | The price for a `LIMIT` order. Required if `order_type` is `"LIMIT"`. For `MARKET` orders, the live market price is used as the limit price. |
| `is_amo`      | boolean | ❌        | Set to `true` for After Market Orders (AMO). Defaults to `false`.             |
| `remarks`     | string  | ❌        | Your own free-text tag for the order — a strategy name, a signal id, anything you want to reconcile against later. Stored with the order and echoed back by [Get Order Details](#get-order-details), [Get Order Book](#get-order-book) and [Get Trade Book](#get-trade-book). See [Order Remarks](#order-remarks). |

**Example Request**
```bash
curl --location 'https://api.indstocks.com/order' \
--header 'Authorization: YOUR_ACCESS_TOKEN' \
--header 'Content-Type: application/json' \
--data '{
  "txn_type": "BUY",
  "exchange": "BSE",
  "segment": "EQUITY",
  "product": "CNC",
  "order_type": "LIMIT",
  "limit_price": 850,
  "validity": "DAY",
  "security_id": "500112",
  "qty": 1,
  "is_amo": false,
  "algo_id": "99999",
  "remarks": "momentum-v2/sig-4471"
}'
```

**Response Payload (Success)**
```json
{
    "status": "success",
    "data": {
        "order_id": "EQ-93586788",
        "order_status": "INITIATED"
    }
}
```

**NOTE: Validations**

- **DayValidityAllowed**: Order should be placed with DAY validity
- **QtyMustBeAboveZero**: Qty must be specified and greater than zero
- **LimitPriceMustBeAboveZero**: Limit price must be specified and greater than zero
- **QtyWithinFreezeQty**: Qty should be less than freeze qty
- **AmoMustBeTrue**: In case of after market orders, amo flag must be true
- **PriceWithinRange**: Limit price must be within the allowed range
- **MaxValueOfOption**: Max value of option allowed is enforced
- **QtyMultipleOfLotSize**: Qty should be a multiple of lot size
- **ReservedRemarks**: `remarks` must not use a value reserved for INDstocks' internal channel tags (see [Order Remarks](#order-remarks))

**NOTE: Market Orders Are Converted to Limit Orders**

API trading does not support pure `MARKET` orders. If you submit `order_type: "MARKET"`, the order is automatically converted to a `LIMIT` order priced at the current live market price before being sent to the exchange.

---

## Order Remarks

`remarks` is an optional free-text tag you attach to an order at placement. It is meant for your own bookkeeping — the strategy that generated the order, a signal id, a backtest run, a basket name. INDstocks stores it against the order and gives it back to you on every read, so you can reconcile fills against your own system without keeping a separate order-id map.

Both [`POST /order`](#place-order) and [`POST /smart/order`](https://api-docs.indstocks.com/smart_orders/#place-smart-order) accept it.

**Where it comes back**

| Endpoint | Field |
|----------|-------|
| [`GET /order`](#get-order-details) | `remarks` |
| [`GET /order-book`](#get-order-book) | `remarks` |
| [`GET /trade-book`](#get-trade-book) | `remarks` |

The field is omitted from the response when the order carried no remark.

**Rules**

| Rule | Behaviour |
|------|-----------|
| Maximum length | **100 characters.** A longer value is silently truncated to the first 100 characters and the order is still placed. It is not rejected — trim it yourself if the exact text matters. |
| Set at placement only | Neither [`/order/modify`](#modify-order) nor [`/smart/order/modify`](https://api-docs.indstocks.com/smart_orders/#modify-smart-order) accepts `remarks`. Modifying an order keeps the remark it was placed with. |
| Reserved values | A small set of values is reserved for INDstocks' own internal channel tags. Sending one is rejected with a `RequestValidationException` naming the reserved values. The current reserved value is `TV-TERMINAL`. Matching runs on the **stored** value — that is, after the 100-character truncation above — and ignores case and surrounding whitespace. So `tv-terminal`, `" TV-TERMINAL "`, and a value padded so that only the reserved tag survives truncation are all rejected. A value that merely *contains* a reserved word and keeps other characters after truncation (for example `my-TV-TERMINAL-clone`) is accepted. |
| Smart orders | A smart order's remark is carried onto every leg. When a stop-loss or target leg triggers, the live order created from it inherits the same remark. |
| Not sent to the exchange | The remark stays inside INDstocks. It is never forwarded to the exchange and never appears in exchange or contract-note records. |

**TIP: Use it as a correlation key**

Writing your own order id into `remarks` is the cheapest way to line up INDstocks fills with your strategy's internal state, because the same tag appears on the order book *and* on the trade book entry for every fill of that order.

It is **not** an idempotency key. INDstocks does not deduplicate on `remarks` — two orders sent with the same tag are two orders.

---

## Modify Order

This API allows you to modify a pending standard order.

**Endpoint**
```
POST /order/modify
```

**Request Body**

| Parameter     | Type    | Mandatory | Description                                                                 |
|---------------|---------|:---------:|-----------------------------------------------------------------------------|
| `order_id`    | string  | ✅        | The unique ID of the order to be modified.                                  |
| `segment`     | string  | ✅        | The market segment. **Enum**: `"DERIVATIVE"`, `"EQUITY"`                    |
| `qty`         | integer | ✅        | The new quantity for the order.                                             |
| `limit_price` | number  | ✅        | The new limit price for the order.                                          |

**NOTE: `remarks` cannot be changed**

This endpoint does not accept `remarks`. The order keeps the remark it was placed with. See [Order Remarks](#order-remarks).

**Example Request**
```bash
curl --location 'https://api.indstocks.com/order/modify' \
--header 'Authorization: YOUR_ACCESS_TOKEN' \
--data '{
  "segment": "DERIVATIVE",
  "limit_price": 73,
  "qty": 75,
  "order_id": "DRV-2049"
}'
```

**Response Payload (Success)**
```json
{
  "status": "success",
  "data": {
    "order_id": "DRV-2049",
    "order_status": "MODIFIED"
  }
}
```

**NOTE: Validations**

- **OrderIdMissing**: Order ID is missing or invalid
- **QtyMustBeAboveZero**: Qty must be specified and greater than zero
- **LimitPriceMustBeAboveZero**: Limit price must be specified and greater than zero
- **QtyWithinFreezeQty**: Qty should be less than freeze qty
- **AmoMustBeTrue**: In case of after market orders, amo flag must be true
- **PriceWithinRange**: Limit price must be within the allowed range
- **MaxValueOfOption**: Max value of option allowed is enforced (for derivative orders)
- **QtyMultipleOfLotSize**: Qty should be a multiple of lot size (for derivative orders)
- **OrderCannotBeModified**: Order is not eligible for modification

---

## Cancel Order

This API allows you to cancel a pending standard order.

**Endpoint**
```
POST /order/cancel
```

**Request Body**

| Parameter  | Type   | Mandatory | Description                                                                 |
|------------|--------|:---------:|-----------------------------------------------------------------------------|
| `order_id` | string | ✅        | The unique ID of the order to be cancelled.                                 |
| `segment`  | string | ✅        | The market segment. **Enum**: `"DERIVATIVE"`, `"EQUITY"`                    |

**Example Request**
```bash
curl --location 'https://api.indstocks.com/order/cancel' \
--header 'Authorization: YOUR_ACCESS_TOKEN' \
--data '{
  "segment": "DERIVATIVE",
  "order_id": "DRV-2049"
}'
```

**Response Payload (Success)**
```json
{
  "status": "success",
  "data": {
    "order_id": "DRV-2049",
    "order_status": "CANCELLED"
  }
}
```

**NOTE: Validations**

- **OrderIdMissing**: Order ID is missing or invalid
- **OrderCannotBeCancelled**: Order is not eligible for cancellation

---

## Get Order Book
Retrieves the list of all orders placed during the current trading day.

**Endpoint**
```
GET /order-book
```

**Example Request**
```bash
curl --location 'https://api.indstocks.com/order-book' \
--header 'Authorization: YOUR_ACCESS_TOKEN'
```

**Response Payload (Success)**
```json
{
  "status": "success",
  "data": [
    {
      "created_at": "2025-07-02T15:47:07.079035+05:30",
      "updated_at": "2025-07-02T17:43:02.635379+05:30",
      "user_id": "710354",
      "security_id": "58757",
      "isin": "",
      "name": "NIFTY 3 JUL 27400 CE",
      "id": "GTT-2914581",
      "exch_order_id": "",
      "txn_type": "SELL",
      "exchange": "NSE",
      "segment": "DERIVATIVE",
      "product": "MARGIN",
      "order_type": "OCO",
      "validity": "",
      "mkt_type": "NL",
      "off_mkt_flag": "",
      "traded_qty": 0,
      "requested_qty": 75,
      "requested_price": "",
      "traded_price": "",
      "sl_trigger_price": "0.3",
      "sl_limit_price": "0.2",
      "tgt_trigger_price": "0.75",
      "tgt_limit_price": "",
      "status": "CANCELLED",
      "extra_info": "",
      "remarks": "momentum-v2/sig-4471"
    },
    {
      "created_at": "2025-07-02T09:18:40.446948+05:30",
      "updated_at": "2025-07-02T09:18:40.498595+05:30",
      "user_id": "710354",
      "security_id": "56998",
      "isin": "",
      "name": "NIFTY 3 JUL 25700 CE",
      "id": "DRV-28131451",
      "exch_order_id": "1300000002340881",
      "txn_type": "BUY",
      "exchange": "NSE",
      "segment": "DERIVATIVE",
      "product": "MARGIN",
      "order_type": "MARKET",
      "validity": "DAY",
      "mkt_type": "NL",
      "off_mkt_flag": "false",
      "traded_qty": 75,
      "requested_qty": 75,
      "requested_price": "43.55",
      "traded_price": "43.55",
      "sl_trigger_price": "",
      "sl_limit_price": "",
      "tgt_trigger_price": "",
      "tgt_limit_price": "",
      "status": "SUCCESS",
      "extra_info": ""
    },
    {
      "created_at": "2025-07-02T17:59:57.799576+05:30",
      "updated_at": "2025-07-02T18:05:03.660538+05:30",
      "user_id": "710354",
      "security_id": "56888",
      "isin": "",
      "name": "NIFTY 03 Jul ₹25550 Call",
      "id": "DRV-28209665",
      "exch_order_id": "",
      "txn_type": "BUY",
      "exchange": "NSE",
      "segment": "DERIVATIVE",
      "product": "MARGIN",
      "order_type": "LIMIT",
      "validity": "DAY",
      "mkt_type": "NL",
      "off_mkt_flag": "true",
      "traded_qty": 0,
      "requested_qty": 225,
      "requested_price": "32.1",
      "traded_price": "",
      "sl_trigger_price": "",
      "sl_limit_price": "",
      "tgt_trigger_price": "",
      "tgt_limit_price": "",
      "status": "O-PENDING",
      "extra_info": ""
    }
  ]
}
```

**NOTE: Response Field Notes**

- For derivative orders, the `isin` field may be empty.
- Smart orders (GTT) will have `sl_trigger_price`, `sl_limit_price`, `tgt_trigger_price`, and `tgt_limit_price` fields populated.
- Regular orders will have these smart order fields as empty strings.
- Orders with a [trailing stop loss](https://api-docs.indstocks.com/smart_orders/#trailing-stop-loss-tsl) additionally carry `is_tsl: true` and `tsl_step_size`. For these, `sl_trigger_price` is the **live trailed trigger** rather than the price originally submitted. *(TSL is not yet live — no order currently returns these fields.)*
- Order IDs starting with "GTT-" indicate smart orders, while "DRV-" indicates derivative orders.
- The `order_type` field may include "OCO" (One Cancels Other) for smart orders.
- Field names use underscore notation (e.g., `requested_price`, `traded_price`) instead of the older "per_share" suffix.
- The `extra_info` field contains rejection reasons or exchange messages when an order fails. It is empty for successful or pending orders.
- `remarks` echoes the tag you sent at placement. It is **absent** from the payload for orders placed without one. See [Order Remarks](#order-remarks).

---

## Get Order Details
Retrieves the complete details and history of a single order.

**Endpoint**
```
GET /order
```

**Request Body**

| Parameter  | Type   | Mandatory | Description                                              |
|------------|--------|:---------:|----------------------------------------------------------|
| `order_id` | string | ✅        | The unique ID of the order to retrieve.                  |
| `segment`  | string | ✅        | The market segment. **Enum**: `"DERIVATIVE"`, `"EQUITY"` |

**Example Request**
```bash
curl --location --request GET 'https://api.indstocks.com/order' \
--header 'Authorization: YOUR_ACCESS_TOKEN' \
--data '{
    "order_id": "DRV-27373858",
    "segment": "DERIVATIVE"
}'
```

**Response Payload (Success)**
```json
{
  "status": "success",
  "data": {
    "created_at": "2025-07-02T09:18:40.446948+05:30",
    "updated_at": "2025-07-02T09:18:40.498595+05:30",
    "user_id": "710354",
    "security_id": "56998",
    "isin": "",
    "name": "NIFTY 3 JUL 25700 CE",
    "id": "DRV-28131451",
    "exch_order_id": "1300000002340881",
    "txn_type": "BUY",
    "exchange": "NSE",
    "segment": "DERIVATIVE",
    "product": "MARGIN",
    "order_type": "MARKET",
    "validity": "DAY",
    "mkt_type": "NL",
    "off_mkt_flag": "false",
    "traded_qty": 75,
    "requested_qty": 75,
    "requested_price": "43.55",
    "traded_price": "43.55",
    "sl_trigger_price": "",
    "sl_limit_price": "",
    "tgt_trigger_price": "",
    "tgt_limit_price": "",
    "status": "SUCCESS",
    "extra_info": "",
    "remarks": "momentum-v2/sig-4471"
  }
}
```

**NOTE: `remarks`**

Present only when the order was placed with a remark. See [Order Remarks](#order-remarks).

---

## Get Trades
Retrieves the list of executed trades (fills) for a specific order.

**Endpoint**
```
GET /order/trades
```

**NOTE: This GET request sends a JSON body**

Like [Get Order Details](#get-order-details), this endpoint takes `order_id` and `segment` as
a JSON request body rather than a path parameter or query string.

**Request Body**

| Parameter  | Type   | Mandatory | Description                                              |
|------------|--------|:---------:|------------------------------------------------------------|
| `order_id` | string | ✅        | The unique ID of the order to fetch trades for.            |
| `segment`  | string | ✅        | The market segment. **Enum**: `"DERIVATIVE"`, `"EQUITY"`   |

**Example Request**
```bash
curl --location --request GET 'https://api.indstocks.com/order/trades' \
--header 'Authorization: YOUR_ACCESS_TOKEN' \
--header 'Content-Type: application/json' \
--data '{
    "order_id": "DRV-85322703",
    "segment": "DERIVATIVE"
}'
```

**Response Payload (Success)**
```json
{
    "status": "success",
    "data": [
        {
            "fill_id": 1279916,
            "exch_order_id": "1100000017281712",
            "quantity": 65,
            "price": 77.8,
            "trade_date": "2026-07-20T09:31:20+05:30"
        }
    ]
}
```

**Response Fields**

| Field           | Type    | Description                                      |
|-----------------|---------|---------------------------------------------------|
| `fill_id`       | integer | Unique identifier for the trade fill.              |
| `exch_order_id` | string  | Exchange-generated order ID.                       |
| `quantity`      | integer | Quantity filled in this trade.                     |
| `price`         | number  | Price at which the trade was executed.             |
| `trade_date`    | string  | Timestamp of trade execution (ISO 8601, IST).      |

**NOTE: No `remarks` here**

This per-order view does not carry `remarks` — you already hold the `order_id`. Use [Get Trade Book](#get-trade-book) if you want the tag alongside each fill.

---

## Get Trade Book

Retrieves the list of all executed trades for a specific segment during the current trading day. The trade book shows all filled orders with their execution details.

**Endpoint**
```
GET /trade-book
```

**Query Parameters**

| Parameter | Type   | Description                                                          |
|-----------|--------|----------------------------------------------------------------------|
| `segment` | string | The market segment. **Enum**: `"EQUITY"`, `"DERIVATIVE"` (Required) |

**Example Request**

```bash
curl --location 'https://api.indstocks.com/trade-book?segment=DERIVATIVE' \
--header 'Authorization: YOUR_ACCESS_TOKEN'
```

**Example Request for Equity Segment**

```bash
curl --location 'https://api.indstocks.com/trade-book?segment=EQUITY' \
--header 'Authorization: YOUR_ACCESS_TOKEN'
```

**Response Payload (Success)**
```json
{
  "status": "success",
  "data": [
    {
      "fill_id": 1020280,
      "exch_order_id": "2400000124991381",
      "quantity": 2425,
      "price": 1.55,
      "trade_date": "2025-11-11T17:48:23+05:30",
      "trade_serial_no": "17628437030186581215",
      "scrip_code": "99133",
      "remarks": "momentum-v2/sig-4471"
    },
    {
      "fill_id": 1022519,
      "exch_order_id": "2400000124697541",
      "quantity": 2425,
      "price": 0.55,
      "trade_date": "2025-11-11T17:49:17+05:30",
      "trade_serial_no": "17628437564178181815",
      "scrip_code": "80958"
    }
  ]
}
```

**Response Fields**

| Field              | Type    | Description                                           |
|--------------------|---------|-------------------------------------------------------|
| `fill_id`          | integer | Unique identifier for the trade fill                  |
| `exch_order_id`    | string  | Exchange-generated order ID                           |
| `quantity`         | integer | Quantity of shares/contracts traded                   |
| `price`            | number  | Price at which the trade was executed                 |
| `trade_date`       | string  | Timestamp of trade execution (ISO 8601 format)        |
| `trade_serial_no`  | string  | Unique serial number for the trade from exchange      |
| `scrip_code`       | string  | Security/instrument code                              |
| `remarks`          | string  | The tag sent on the order that produced this fill. Absent when that order carried no remark. Every fill of the same order repeats the same value. See [Order Remarks](#order-remarks) |

**NOTE: Trade Book vs Order Book**

- **Order Book** (`/order-book`): Shows all orders placed, including pending, cancelled, and executed orders
- **Trade Book** (`/trade-book`): Shows only executed trades with their fill prices and quantities
- Trade book entries represent actual transactions, while order book shows order status
- A single order can have multiple trade entries if filled in parts
- Use the `segment` query parameter to filter trades by EQUITY or DERIVATIVE segment

---

## See Also
- [Smart Orders (GTT)](https://api-docs.indstocks.com/smart_orders/) — multi-leg orders with stop-loss/target legs
- [Margin Calculator](https://api-docs.indstocks.com/margin_calculation/) — check required margin before placing an order
- [Glossary & Constants](https://api-docs.indstocks.com/glossary/) — `txn_type`/`segment`/`product`/`order_type` enums and ID prefixes
- [Error Bucket](https://api-docs.indstocks.com/errors/) — RMS rejection messages and `OrderException` handling
- [Order Updates WebSocket](https://api-docs.indstocks.com/Websockets/#order-updates-feed) — real-time order status instead of polling


---

# Source: https://api-docs.indstocks.com/smart_orders/

# Smart Order APIs (GTT)

This section outlines the APIs for placing, modifying, and canceling multi-leg "Good Till Triggered" (GTT) orders, which include simultaneous stop-loss and target legs, as well as Trigger orders.

**NOTE: Supported Segments**

Smart Orders (GTT) and Trigger Orders are supported for both **Equity** and **Derivative** instruments.

---

## How Smart Orders Work

When you place a smart order via `/smart/order`, the system creates two linked orders:

1. **Parent Order** — The primary order (MARKET, LIMIT, or TRIGGER) that gets sent to the exchange first (except if the entered limit price is outside the circuit bounds, then parent order is placed as a GTT order instead).
2. **Child Order** — A GTT (Good Till Triggered) order containing the stop-loss and/or target legs. It is linked to the parent and only activates once the parent order is successfully executed.

### Order ID Prefixes

Each order ID carries a prefix that indicates its type:

| Prefix | Meaning | Used For |
|--------|---------|----------|
| `EQ-`  | Equity order | Parent orders in the EQUITY segment |
| `DRV-` | Derivative order | Parent orders in the DERIVATIVE segment |
| `GTT-` | Good Till Triggered | Child orders (always), and parent orders when the limit price falls outside the circuit range |

- A parent order normally receives an `EQ-` or `DRV-` prefix depending on the segment.
- If the entered limit price is outside the circuit bounds, the parent order is placed as a GTT order instead and receives a `GTT-` prefix.
- Child orders always carry a `GTT-` prefix.

### Placement Response

The API response returns both order IDs in a single payload:

```json
{
  "status": "success",
  "data": {
    "order_data": [
      {
        "order_id": "DRV-28131451",
        "order_status": "CREATED",
        "child_order_details": {
          "order_id": "GTT-2914581",
          "order_status": "CREATED"
        }
      }
    ]
  }
}
```

### Modification and Cancellation

Parent and child orders are independent entities. To modify or cancel a smart order, you must operate on each order separately using its own `order_id`:

- Use `/smart/order/modify` with the parent `order_id` (e.g. `DRV-28131451`) to modify the parent.
- Use `/smart/order/modify` with the child `order_id` (e.g. `GTT-2914581`) to modify the child.
- The same applies to `/smart/order/cancel` — each order must be cancelled individually.

**WARNING: Child Order Lifecycle**

The child order will not activate until the parent order is successfully executed. If the parent order is cancelled, rejected, or fails, the linked child order remains inactive.

---

| Request Type | Path                                         | Description                      |
|--------------|----------------------------------------------|----------------------------------|
| **POST**     | [`/smart/order`](#place-smart-order)         | Place a new multi-leg smart order |
| **POST**     | [`/smart/order/modify`](#modify-smart-order) | Modify a pending smart order     |
| **POST**     | [`/smart/order/cancel`](#cancel-smart-order) | Cancel a pending smart order     |

---

## Place Smart Order

This API allows you to place a new multi-leg smart order (GTT).

**Endpoint**
```
POST /smart/order
```

**Request Body**

| Parameter             | Type    | Mandatory | Description                                                                 |
|-----------------------|---------|:---------:|-----------------------------------------------------------------------------|
| `txn_type`            | string  | ✅        | The transaction type. **Enum**: `"BUY"`, `"SELL"`                           |
| `exchange`            | string  | ✅        | The exchange to place the order on. **Enum**: `"NSE"`                       |
| `segment`             | string  | ✅        | The market segment. **Enum**: `"EQUITY"`, `"DERIVATIVE"`                    |
| `product`             | string  | ✅        | The product type. For Equity: `"CNC"`, `"INTRADAY"`. For Derivative: `"MARGIN"`, `"INTRADAY"` |
| `order_type`          | string  | ✅        | The type of order. **Enum**: `"LIMIT"`, `"MARKET"`, `"TRIGGER"`. Note: `MARKET` orders are automatically converted to `LIMIT` at the live price (see note below). |
| `validity`            | string  | ✅        | The order validity. **Enum**: `"DAY"`                                       |
| `security_id`         | string  | ✅        | The unique identifier for the instrument.                                   |
| `qty`                 | integer | ✅        | The quantity of the instrument to trade.                                    |
| `algo_id`             | string  | ✅        | Algo identifier for the smart order. Use `"99999"` for NSE, `"9999999999999999"` for BSE orders. |
| `limit_price`         | number  | ❌        | The price for the main `LIMIT` order. Required if `order_type` is `"LIMIT"`. For `MARKET` orders the live market price is used. **Not used for `"TRIGGER"` orders — omit it** (it is not currently rejected there; see the warning below). |
| `trigger_price`       | number  | ❌        | The trigger price for the order. Required if `order_type` is `"TRIGGER"`. Must be a multiple of the instrument's tick size. For BUY: must be strictly greater than CMP. For SELL: must be strictly less than CMP. |
| `trigger_limit_price` | number  | ❌        | Optional limit price for a trigger-limit order. If provided alongside `trigger_price`, the order executes as trigger-limit; otherwise it executes as trigger-market. If omitted, `trigger_limit_price` is automatically set equal to `trigger_price`. Must be a multiple of the instrument's tick size. |
| `sl_trigger_price`    | number  | ❌        | The trigger price for the stop-loss leg. Must sit below the entry price on a BUY, above it on a SELL. If set, `sl_limit_price` must also be provided or the order is rejected. |
| `tgt_trigger_price`   | number  | ❌        | The trigger price for the target (profit) leg. Must sit above the entry price on a BUY, below it on a SELL. If set, `tgt_limit_price` must also be provided or the order is rejected. |
| `sl_limit_price`      | number  | ❌        | The limit price for the stop-loss order once triggered. Required when `sl_trigger_price` is set. |
| `tgt_limit_price`     | number  | ❌        | The limit price for the target order once triggered. Required when `tgt_trigger_price` is set. |
| `is_tsl`              | boolean | ❌        | **Currently ignored — TSL is not live.** Intended: set to `true` to make the stop-loss leg a **trailing** stop-loss. Requires a stop-loss leg and `tsl_step_size`. Not supported when `order_type` is `"TRIGGER"`. See [Trailing Stop Loss](#trailing-stop-loss-tsl). |
| `tsl_step_size`       | number  | ❌        | **Currently ignored — TSL is not live.** Intended: the trailing step, **in rupees**. Must be greater than zero and a multiple of the instrument's tick size. Required when `is_tsl` is `true`, and must be omitted (or zero) when `is_tsl` is `false`. |
| `remarks`             | string  | ❌        | Your own free-text tag for the order — a strategy name, a signal id, anything you want to reconcile against later. Carried onto every leg, including the live order created when a stop-loss or target leg triggers. Max 100 characters, silently truncated beyond that; cannot be changed on modify. See [Order Remarks](https://api-docs.indstocks.com/normal_orders/#order-remarks). |

**Example Request — LIMIT order (Derivative)**
```bash
curl --location 'https://api.indstocks.com/smart/order' \
--header 'Authorization: YOUR_ACCESS_TOKEN' \
--header 'Content-Type: application/json' \
--data '{
  "txn_type": "BUY",
  "exchange": "NSE",
  "segment": "DERIVATIVE",
  "product": "MARGIN",
  "order_type": "LIMIT",
  "validity": "DAY",
  "security_id": "51011",
  "qty": 75,
  "limit_price": 37,
  "sl_trigger_price": 34,
  "tgt_trigger_price": 41,
  "sl_limit_price": 33,
  "tgt_limit_price": 38,
  "algo_id": "99999",
  "remarks": "momentum-v2/sig-4471"
}'
```

**Example Request — Trigger-Market order (Equity)**
```bash
curl --location 'https://api.indstocks.com/smart/order' \
--header 'Authorization: YOUR_ACCESS_TOKEN' \
--header 'Content-Type: application/json' \
--data '{
  "txn_type": "BUY",
  "exchange": "NSE",
  "segment": "EQUITY",
  "product": "CNC",
  "order_type": "TRIGGER",
  "validity": "DAY",
  "security_id": "3045",
  "qty": 10,
  "trigger_price": 1520.00,
  "algo_id": "99999"
}'
```

**Example Request — Trigger-Limit order (Derivative)**
```bash
curl --location 'https://api.indstocks.com/smart/order' \
--header 'Authorization: YOUR_ACCESS_TOKEN' \
--header 'Content-Type: application/json' \
--data '{
  "txn_type": "BUY",
  "exchange": "NSE",
  "segment": "DERIVATIVE",
  "product": "MARGIN",
  "order_type": "TRIGGER",
  "validity": "DAY",
  "security_id": "51011",
  "qty": 75,
  "trigger_price": 38.50,
  "trigger_limit_price": 38.75,
  "algo_id": "99999"
}'
```

**Example Request — Trigger order with stop-loss and target legs (Derivative)**
```bash
curl --location 'https://api.indstocks.com/smart/order' \
--header 'Authorization: YOUR_ACCESS_TOKEN' \
--header 'Content-Type: application/json' \
--data '{
  "txn_type": "BUY",
  "exchange": "NSE",
  "segment": "DERIVATIVE",
  "product": "MARGIN",
  "order_type": "TRIGGER",
  "validity": "DAY",
  "security_id": "46997",
  "qty": 65,
  "trigger_price": 318.75,
  "trigger_limit_price": 319,
  "sl_trigger_price": 316.65,
  "sl_limit_price": 316.40,
  "tgt_trigger_price": 328.65,
  "tgt_limit_price": 328.90,
  "algo_id": "99999"
}'
```

Here the legs are checked against an entry price of `319` — the `trigger_limit_price` — rather than `limit_price`, which plays no part in a `TRIGGER` order's execution.

**NOTE: Validations**

- **QtyMustBeAboveZero**: Qty must be specified and greater than zero
- **LimitPriceMustBeAboveZero**: Limit price must be specified and greater than zero (applies to `LIMIT` orders)
- **QtyWithinFreezeQty**: Qty should be less than freeze qty
- **MaxValueOfOption**: Max Value of option allowed is enforced
- **QtyMultipleOfLotSize**: Qty should be multiple of lot size
- **MaxSlTriggerPrice / MinSlTriggerPrice**: the stop-loss trigger must sit below the entry price on a BUY, above it on a SELL
- **MaxSlLimitPrice / MinSlLimitPrice**: the stop-loss limit must sit below the stop-loss trigger on a BUY, above it on a SELL
- **MinTgtTriggerPrice / MaxTgtTriggerPrice**: the target trigger must sit above the entry price on a BUY, below it on a SELL
- **MinTgtLimitPrice / MaxTgtLimitPrice**: the target limit must sit above the target trigger on a BUY, below it on a SELL
- **TriggerPriceMustBeAboveZero**: `trigger_price` must be present and greater than zero when `order_type` is `"TRIGGER"`
- **TriggerPriceTickSize**: `trigger_price` must be a multiple of the instrument's tick size
- **TriggerLimitPriceTickSize**: `trigger_limit_price` (if provided) must be a multiple of the instrument's tick size
- **TriggerPriceVsCMP (BUY)**: `trigger_price` must be strictly greater than the current market price
- **TriggerPriceVsCMP (SELL)**: `trigger_price` must be strictly less than the current market price
- *The five `Tsl*` rules below are **not currently enforced** — TSL is not live and both fields are ignored. See [Trailing Stop Loss](#trailing-stop-loss-tsl).*
- **TslStepSizeRequiredWhenEnabled**: `tsl_step_size` must be specified and greater than zero when `is_tsl` is `true`
- **TslFlagRequiredWithStepSize**: `is_tsl` must be `true` when `tsl_step_size` is provided
- **TslRequiresStopLoss**: a trailing stop loss requires a stop-loss leg (`sl_trigger_price` and `sl_limit_price`)
- **TslNotAllowedForTriggerOrder**: a trailing stop loss is not supported for `order_type: "TRIGGER"`
- **TslStepSizeMultipleOfTickSize**: `tsl_step_size` must be a multiple of the instrument's tick size
- **ReservedRemarks**: `remarks` must not use a value reserved for INDstocks' internal channel tags (see [Order Remarks](https://api-docs.indstocks.com/normal_orders/#order-remarks))

**NOTE: Market Orders Are Converted to Limit Orders**

API trading does not support pure `MARKET` orders. If you submit `order_type: "MARKET"`, the order is automatically converted to a `LIMIT` order priced at the current live market price before being sent to the exchange.

Similarly, for `TRIGGER` orders, if `trigger_limit_price` is omitted it is automatically set equal to `trigger_price`, so the order executes as a trigger-limit order at the trigger price.

**WARNING: Stop-Loss and Target Legs Require a Limit Price**

If you provide `sl_trigger_price`, you must also provide `sl_limit_price`. Likewise, if you provide `tgt_trigger_price`, you must also provide `tgt_limit_price`. Submitting a stop-loss or target leg without its corresponding limit price will cause the order to be rejected.

**NOTE: Where the Stop-Loss and Target Legs Must Sit**

Both legs are validated against the parent order's **entry price**, which depends on `order_type`:

| `order_type` | Entry price |
|--------------|-------------|
| `LIMIT`      | `limit_price` |
| `MARKET`     | the live market price |
| `TRIGGER`    | `trigger_limit_price`, or `trigger_price` when that is omitted |

On a **BUY** the stop-loss must sit below the entry price and the target above it. On a **SELL** it is the mirror image — stop-loss above, target below. A leg on the wrong side is rejected.

**WARNING: Do Not Send `limit_price` on a `TRIGGER` Order**

A trigger order's entry price comes from `trigger_limit_price` (or `trigger_price`); `limit_price` plays no part in it.

Sending `limit_price` anyway is **not currently rejected** — the request succeeds — but it is unsupported on a `TRIGGER` order and can change how the order is handled internally. Omit the field.

---

## Trailing Stop Loss (TSL)

**WARNING: Trailing Stop Loss Is Not Yet Available**

`is_tsl` and `tsl_step_size` are **not live**. They are accepted and then silently ignored — the request succeeds and your order is placed with an **ordinary, non-trailing** stop-loss. You will not receive an error.

Do not rely on trailing behaviour until this notice is removed. The rest of this section describes the intended behaviour once the feature ships.

A **trailing stop loss** is a stop-loss leg whose trigger price follows the market in your favour. As the price moves favourably, the stop-loss trigger is stepped along behind it by `tsl_step_size`; when the price moves against you, the trigger **stays where it is**. It only ever ratchets one way, which is what locks in gains.

TSL is not a separate order type — it is a modifier on the stop-loss leg of a smart order. You enable it with two fields:

| Field | Meaning |
|-------|---------|
| `is_tsl` | `true` turns the stop-loss leg into a trailing stop-loss |
| `tsl_step_size` | How far, in rupees, the trigger trails behind the price |

**Example Request — Trailing stop loss (Equity)**
```bash
curl --location 'https://api.indstocks.com/smart/order' \
--header 'Authorization: YOUR_ACCESS_TOKEN' \
--header 'Content-Type: application/json' \
--data '{
  "txn_type": "BUY",
  "exchange": "NSE",
  "segment": "EQUITY",
  "product": "CNC",
  "order_type": "LIMIT",
  "validity": "DAY",
  "security_id": "2885",
  "qty": 1,
  "limit_price": 1400.05,
  "sl_trigger_price": 1375.00,
  "sl_limit_price": 1374.50,
  "is_tsl": true,
  "tsl_step_size": 0.05,
  "algo_id": "99999"
}'
```

In this example the stop-loss starts at ₹1375.00. If the price climbs, the trigger is trailed upward in ₹0.05 steps; if the price falls back, the trigger holds at the highest level it reached.

### Requirements

- A **stop-loss leg is mandatory**: send both `sl_trigger_price` and `sl_limit_price`. TSL rides on the stop-loss leg, so a request with `is_tsl: true` and no stop-loss is rejected.
- `is_tsl` and `tsl_step_size` must agree — `is_tsl: true` requires `tsl_step_size > 0`, and sending a `tsl_step_size` without `is_tsl: true` is rejected.
- `tsl_step_size` must be a multiple of the instrument's tick size.
- **Not supported for `order_type: "TRIGGER"`.** A trigger order can carry an ordinary stop-loss leg, but that leg cannot trail.
- Supported on `/smart/order` only. `is_tsl` sent to the plain [`/order`](https://api-docs.indstocks.com/normal_orders/) endpoint is ignored.

**WARNING: The Trail Starts Only After the Parent Order Executes**

Like every child leg (see [Child Order Lifecycle](#modification-and-cancellation)), the trailing stop-loss becomes active only once the **parent order is successfully executed**. Until the parent fills, the stop-loss is recorded against the order but is not yet trailing. If the parent is cancelled or rejected, the trail never starts.

### Reading the trailed price

On [`/order`](https://api-docs.indstocks.com/normal_orders/#get-order-details) and [`/order-book`](https://api-docs.indstocks.com/normal_orders/#get-order-book), a trailing stop-loss order carries two extra fields:

| Field | Type | Description |
|-------|------|-------------|
| `is_tsl` | boolean | `true` when the order has an active trailing stop-loss |
| `tsl_step_size` | number | The trailing step in rupees |

For a TSL order, **`sl_trigger_price` reflects the live trailed trigger** — the current, stepped-up value — not the price you originally submitted. `sl_limit_price` moves with it, preserving your original trigger-to-limit gap.

### Changing a trailing stop loss

**NOTE: The Step Size Is Fixed Once the Order Is Placed**

`tsl_step_size` cannot be changed, and TSL cannot be switched off, through `/smart/order/modify` — those fields are not accepted on modify. To use a different step size, **cancel the order and place a new one**.

You can still modify the other attributes of a trailing order (for example `sl_trigger_price`, `sl_limit_price`, or `qty`) in the normal way. Simply omit the TSL fields and the trail is preserved.

---

## Modify Smart Order

This API allows you to modify a pending smart order.

**Endpoint**
```
POST https://api.indstocks.com/smart/order/modify
```

**Request Body**

| Parameter             | Type     | Mandatory | Description                                                |
|-----------------------|----------|:---------:|------------------------------------------------------------|
| `order_id`            | string   | ✅        | The unique ID of the order to be modified                  |
| `segment`             | string   | ✅        | The market segment. **Enum**: `"EQUITY"`, `"DERIVATIVE"`   |
| `algo_id`             | string   | ✅        | Algo identifier. Use `"99999"` for NSE orders.             |
| `order_type`          | string   | ❌        | The type of order. **Enum**: `"LIMIT"`, `"MARKET"`, `"TRIGGER"`. Must match the existing order type. |
| `qty`                 | integer  | ❌        | The quantity of the instrument to trade                    |
| `limit_price`         | number   | ❌        | The price for the main LIMIT order (applies to `LIMIT` orders only) |
| `trigger_price`       | number   | ❌        | The trigger price for the order. Required when modifying a `TRIGGER` order. Must be a multiple of tick size. For BUY: must be strictly greater than CMP. For SELL: must be strictly less than CMP. |
| `trigger_limit_price` | number   | ❌        | Optional limit price for a trigger-limit order. Must be a multiple of tick size. |
| `sl_trigger_price`    | number   | ❌        | The trigger price for the stop-loss leg                    |
| `tgt_trigger_price`   | number   | ❌        | The trigger price for the target (profit) leg              |
| `sl_limit_price`      | number   | ❌        | The limit price for the stop-loss order                    |
| `tgt_limit_price`     | number   | ❌        | The limit price for the target order                       |

**NOTE: `remarks` cannot be changed**

This endpoint does not accept `remarks`. The order keeps the remark it was placed with. See [Order Remarks](https://api-docs.indstocks.com/normal_orders/#order-remarks).

**Example Request — Modify LIMIT order**
```bash
curl --location 'https://api.indstocks.com/smart/order/modify' \
--header 'Authorization: YOUR_ACCESS_TOKEN' \
--data '{
  "order_id": "DRV-123",
  "segment": "DERIVATIVE",
  "algo_id": "99999",
  "order_type": "LIMIT",
  "qty": 20,
  "limit_price": 0.35,
  "sl_trigger_price": 0.15,
  "tgt_trigger_price": 41,
  "sl_limit_price": 0.1,
  "tgt_limit_price": 42
}'
```

**Example Request — Modify Trigger order**
```bash
curl --location 'https://api.indstocks.com/smart/order/modify' \
--header 'Authorization: YOUR_ACCESS_TOKEN' \
--data '{
  "order_id": "EQ-456",
  "segment": "EQUITY",
  "algo_id": "99999",
  "order_type": "TRIGGER",
  "qty": 10,
  "trigger_price": 1530.00,
  "trigger_limit_price": 1532.00
}'
```

**WARNING: Order Type Mismatch**

The `order_type` in the modify request must match the type of the existing order. Sending `order_type: "TRIGGER"` for a `LIMIT` order (or vice versa) will be rejected.

**NOTE: Trailing Stop Loss on Modify**

`is_tsl` and `tsl_step_size` are **not accepted on modify**. Modifying any other field of a trailing order leaves the trail running with its original step size — see [Changing a trailing stop loss](#changing-a-trailing-stop-loss).

---

## Cancel Smart Order

This API allows you to cancel a pending smart order.

**Endpoint**
```
POST /smart/order/cancel
```

**Request Body**

| Parameter  | Type   | Mandatory | Description                                                     |
|------------|--------|:---------:|-----------------------------------------------------------------|
| `order_id` | string | ✅        | The unique ID of the order to be cancelled.                     |
| `segment`  | string | ✅        | The market segment. **Enum**: `"EQUITY"`, `"DERIVATIVE"`        |

**Example Request**
```bash
curl --location 'https://api.indstocks.com/smart/order/cancel' \
--header 'Authorization: YOUR_ACCESS_TOKEN' \
--data '{
  "segment": "DERIVATIVE",
  "order_id": "123456789"
}'
```

**NOTE: Validations**

- **OrderIdMissing**: Order ID is missing or invalid
- **OrderCannotBeCancelled**: Order is not eligible for cancellation

---

## See Also
- [Orders](https://api-docs.indstocks.com/normal_orders/) — standard (non-GTT) order placement and management
- [Margin Calculator](https://api-docs.indstocks.com/margin_calculation/) — check required margin before placing an order
- [Glossary & Constants](https://api-docs.indstocks.com/glossary/) — `EQ-`/`DRV-`/`GTT-` ID prefixes and shared enums
- [Error Bucket](https://api-docs.indstocks.com/errors/) — RMS rejection messages and `OrderException` handling


---

# Source: https://api-docs.indstocks.com/margin_calculation/

# Margin Calculation

This API allows you to calculate the margin requirement for an order before placing it. This helps you understand the funds needed and plan your trades accordingly.

| Request Type | Path                                     | Description                                              |
|--------------|------------------------------------------|----------------------------------------------------------|
| **GET**      | [`/margin`](#margin-calculation) | Calculate margin requirement for an order                     |

---

## Margin Calculation

Calculate the margin requirement for a single order before placing it.

**Endpoint**
```
GET /margin
```

**Request Body**

| Parameter     | Type    | Mandatory | Description                                                                 |
|---------------|---------|:---------:|-----------------------------------------------------------------------------|
| `segment`     | string  | ✅        | The market segment. **Enum**: `"DERIVATIVE"`, `"EQUITY"`                    |
| `exchange`    | string  | ✅        | The exchange. **Enum**: `"NSE"`, `"BSE"`                                   |
| `securityID`  | string  | ✅        | The unique identifier for the instrument                                    |
| `txnType`     | string  | ✅        | The transaction type. **Enum**: `"BUY"`, `"SELL"`                          |
| `quantity`    | string  | ✅        | The quantity of the instrument to trade                                     |
| `price`       | string  | ✅        | The price per unit                                                          |
| `product`     | string  | ✅        | The product type. **Enum**: `"MARGIN"`, `"INTRADAY"`, `"CNC"`              |

**Example Request**
```bash
curl --location --request GET 'https://api.indstocks.com/margin' \
--header 'Authorization: YOUR_ACCESS_TOKEN' \
--header 'Content-Type: application/json' \
--data '{
    "segment": "DERIVATIVE",
    "txnType": "BUY",
    "quantity": "75",
    "price": "10",
    "product": "MARGIN",
    "securityID": "40131",
    "exchange": "NSE"
}'
```

**Response Payload (Success)**
```json
{
  "status": "success",
  "data": {
    "total_margin": 750,
    "span_margin": 0,
    "hedge_benefit": 0,
    "exposure_margin": 0,
    "available_balance": 0,
    "var_margin": 0,
    "insufficient_balance": 0,
    "delivery_margin": 0,
    "brokerage": 0,
    "charges": {
      "stt": 0,
      "exchange_charges": 0,
      "stamp_duty": 0,
      "sebi_turn_over_charges": 0,
      "brokerage": 10,
      "gst": 1.8,
      "IPFTCharges": 0,
      "total_charges": 11.8
    }
  }
}
```

**Response Fields**

| Field                | Type   | Description                                                |
|----------------------|--------|------------------------------------------------------------|
| `total_margin`       | number | Total margin required for the order                        |
| `span_margin`        | number | SPAN margin requirement                                    |
| `hedge_benefit`      | number | Margin benefit from hedged positions                       |
| `exposure_margin`    | number | Exposure margin requirement                                |
| `available_balance`  | number | Available balance after margin requirement                 |
| `var_margin`         | number | Value at Risk (VAR) margin                                 |
| `insufficient_balance` | number | Shortfall amount if balance is insufficient              |
| `delivery_margin`    | number | Delivery margin for equity trades                          |
| `brokerage`          | number | Brokerage amount (typically ₹10 per order for API users)                  |

**Charges Breakdown**

| Field                    | Type   | Description                                    |
|--------------------------|--------|------------------------------------------------|
| `stt`                    | number | Securities Transaction Tax                     |
| `exchange_charges`       | number | Exchange transaction charges                   |
| `stamp_duty`             | number | Government stamp duty                          |
| `sebi_turn_over_charges` | number | SEBI turnover charges                          |
| `brokerage`              | number | Brokerage charges (₹10 per order)               |
| `gst`                    | number | GST on brokerage and charges (18%)             |
| `IPFTCharges`            | number | Investor Protection Fund Trust charges         |
| `total_charges`          | number | Sum of all charges                             |

**NOTE: Response Notes**

- All amounts are in INR (Indian Rupees)
- Brokerage is flat ₹10 per order regardless of order size
- GST is calculated at 18% on brokerage and other charges
- `total_margin` represents the total funds required to place the order
- For derivative orders, SPAN and exposure margins may apply
- For equity delivery orders, delivery margin may be applicable

---

## See Also
- [Orders](https://api-docs.indstocks.com/normal_orders/) — place the order once margin is confirmed
- [Get Funds](https://api-docs.indstocks.com/Users/#get-funds) — check available balance against the calculated margin
- [Error Bucket](https://api-docs.indstocks.com/errors/) — RMS margin-related rejection messages


---

# Source: https://api-docs.indstocks.com/portfolio_funds/

# Portfolio

This section provides endpoints for retrieving a user's portfolio, including current holdings and open positions.

| Request Type | Path                 | Description                                    |
|--------------|----------------------|------------------------------------------------|
| **GET**      | [`/portfolio/holdings`](#get-holdings)  | Retrieves the user's equity holdings.          |
| **GET**      | [`/portfolio/positions`](#get-positions) | Retrieves the user's open positions.           |

---

## Get Holdings

Retrieves the user's current equity holdings (stocks held in their Demat account).

**Endpoint**
```
GET /portfolio/holdings
```

**Example Request**
```bash
curl --location 'https://api.indstocks.com/portfolio/holdings' \
--header 'Authorization: YOUR_ACCESS_TOKEN'
```

**Response Payload (Success)**
```json
{
    "status": "success",
    "data": [
        {
            "security_id": "18520",
            "symbol": "CUPID",
            "isin": "INE509F01029",
            "total_qty": 1,
            "used_qty": 0,
            "avg_price": 217.3,
            "t1_qty": 1,
            "t1_avg_price": 217.3,
            "dp_qty": 0,
            "dp_avg_price": 0
        }
    ]
}
```

**Response Fields**

| Field          | Type    | Description                                                        |
|----------------|---------|----------------------------------------------------------------------|
| `security_id`  | string  | The unique identifier for the instrument.                          |
| `symbol`       | string  | The trading symbol for the instrument.                              |
| `isin`         | string  | The ISIN of the instrument.                                         |
| `total_qty`    | number  | Total quantity held (T1 + DP holdings).                             |
| `used_qty`     | number  | Quantity currently pledged, sold, or otherwise blocked.             |
| `avg_price`    | number  | Average buy price across `total_qty`.                               |
| `t1_qty`       | number  | Quantity settled T1 (not yet moved to the Demat/DP account).        |
| `t1_avg_price` | number  | Average price for the `t1_qty` portion.                             |
| `dp_qty`       | number  | Quantity already settled into the Demat (DP) account.                |
| `dp_avg_price` | number  | Average price for the `dp_qty` portion.                             |

---

## Get Positions

Retrieves the user's open positions, such as intraday trades and F&O positions.

**Endpoint**
```
GET /portfolio/positions
```

**Example Request**

**For Derivative Positions (MARGIN/INTRADAY):**
```bash
curl --location 'https://api.indstocks.com/portfolio/positions?segment=derivative&product=margin' \
--header 'Authorization: YOUR_ACCESS_TOKEN'
```

**For Equity Positions (CNC/INTRADAY):**
```bash
curl --location 'https://api.indstocks.com/portfolio/positions?segment=equity&product=cnc' \
--header 'Authorization: YOUR_ACCESS_TOKEN'
```

**Query Parameters:**
- `segment`: `derivative` or `equity`
- `product`: 
  - For derivative: `margin` or `intraday`
  - For equity: `cnc` or `intraday`

**Response Payload (Success) — Derivative**
```json
{
    "status": "success",
    "data": [
        {
            "position_id": "535654528",
            "security_id": "823580",
            "symbol": "SENSEX",
            "segment": "DERIVATIVE",
            "product": "MARGIN",
            "exchange": "",
            "drv_instrument": "OPTIDX",
            "drv_expiry_date": "07/16/2026 14:00",
            "drv_option_type": "CE",
            "drv_strike_price": 82000,
            "net_qty": 0,
            "avg_price": 1.2,
            "buy_qty": 20,
            "buy_avg": 1.25,
            "sell_qty": 20,
            "sell_avg": 1.2,
            "realized_profit": -1.0,
            "day_buy_qty": null,
            "day_buy_val": null,
            "day_sell_qty": null,
            "day_sell_val": null,
            "cf_buy_qty": null,
            "cf_buy_val": null,
            "cf_sell_qty": null,
            "cf_sell_val": null
        }
    ]
}
```

**Response Payload (Success) — Equity**
```json
{
    "status": "success",
    "data": [
        {
            "position_id": "86016462",
            "security_id": "1521",
            "symbol": "INDIAGLYCO",
            "segment": "EQUITY",
            "product": "INTRADAY",
            "exchange": "NSE",
            "isin": "INE560A01023",
            "drv_instrument": "",
            "net_qty": 0,
            "avg_price": 1146.85,
            "buy_qty": 1,
            "buy_avg": 1149.4,
            "sell_qty": 1,
            "sell_avg": 1146.85,
            "realized_profit": -2.55,
            "day_buy_qty": 1,
            "day_buy_val": 1149.4,
            "day_sell_qty": 1,
            "day_sell_val": 1146.85,
            "cf_buy_qty": null,
            "cf_buy_val": null,
            "cf_sell_qty": null,
            "cf_sell_val": null
        }
    ]
}
```

**Response Fields**

| Field              | Type          | Description                                                                 |
|--------------------|---------------|-------------------------------------------------------------------------------|
| `position_id`      | string        | Unique identifier for this position.                                        |
| `security_id`      | string        | The unique identifier for the instrument.                                   |
| `symbol`           | string        | The trading symbol for the instrument.                                       |
| `segment`          | string        | `EQUITY` or `DERIVATIVE`.                                                    |
| `product`          | string        | `MARGIN`, `INTRADAY`, or `CNC` depending on the `product` query parameter.   |
| `exchange`         | string        | The exchange (`NSE`/`BSE`). May be empty for some derivative index positions. |
| `isin`             | string        | ISIN of the instrument (equity positions only).                             |
| `drv_instrument`   | string        | Derivative instrument type (e.g. `OPTIDX`, `FUTSTK`). Empty for equity.      |
| `drv_expiry_date`  | string        | Expiry date/time for derivative contracts.                                  |
| `drv_option_type`  | string        | `CE` or `PE` for options. Absent for futures/equity.                        |
| `drv_strike_price` | number        | Strike price for options. Absent for futures/equity.                        |
| `net_qty`          | number        | Net open quantity (buy − sell).                                             |
| `avg_price`        | number        | Average price of the net open quantity.                                     |
| `buy_qty`/`buy_avg`   | number/number | Total bought quantity and its average price.                             |
| `sell_qty`/`sell_avg` | number/number | Total sold quantity and its average price.                               |
| `realized_profit`  | number        | Realized P&L for this position so far today.                                |
| `day_buy_qty`/`day_buy_val`   | number\|null | Same-day buy quantity/value (equity intraday). `null` where not applicable. |
| `day_sell_qty`/`day_sell_val` | number\|null | Same-day sell quantity/value. `null` where not applicable.        |
| `cf_buy_qty`/`cf_buy_val`     | number\|null | Carried-forward buy quantity/value. `null` where not applicable.  |
| `cf_sell_qty`/`cf_sell_val`   | number\|null | Carried-forward sell quantity/value. `null` where not applicable. |

---

## See Also
- [Market Quotes](https://api-docs.indstocks.com/MarketQuote/) — live prices to compute current valuation/P&L
- [Get Funds](https://api-docs.indstocks.com/Users/#get-funds) — available and utilized funds
- [Glossary & Constants](https://api-docs.indstocks.com/glossary/) — `segment`/`product` casing gotcha for this endpoint
- [Error Bucket](https://api-docs.indstocks.com/errors/) — error handling for portfolio endpoints


---

# Source: https://api-docs.indstocks.com/errors/

# Error Bucket

This is the one place to check when a request fails. Every `error_type` below maps to a specific,
diagnosable cause and an expected client action — if you hit something not listed here, treat it
as a bug report waiting to happen and let us know (`instockssupport@indmoney.com`).

## Response Shape

Most failed requests return an HTTP 4xx/5xx status with this JSON body:

```json
{
  "status": "error",
  "message": "A human-readable message providing details about the error.",
  "error_type": "TokenException"
}
```

**WARNING: Other error shapes also occur — always check the HTTP status first**

Not every endpoint uses the envelope above. Three further shapes have been observed:

| Shape | Where seen |
|---|---|
| `{"message": "...", "success": false}` | Instruments and Market Quotes failures; missing `Authorization` on the Option Chain |
| `{"message": "Bad Request", "debug_info": "..."}` | [Option Chain](https://api-docs.indstocks.com/utility/#option-chain) and [Contracts & Expiries](https://api-docs.indstocks.com/contracts/) parameter validation — `message` carries the category, `debug_info` the specific detail |
| `{"error": "Rate limit exceeded", "success": false}` | Rate limiting on the Option Chain |

So: **check the HTTP status code first**, then read whichever of `error_type`, `debug_info`,
`message` or `error` is present for the human-readable reason. Do not hard-code a check for
`status == "error"`, and do not assume `message` carries the detail — on the Option Chain it is
the generic string `"Bad Request"` while `debug_info` holds the actual cause.

---

## General API Errors

| `error_type` | HTTP Status | Meaning | Expected client action |
|---|---|---|---|
| `InputException` | 400 | Malformed JSON, missing parameters, or wrong data types. | Read `message` for specifics; fix the request body/params. Not retryable as-is. |
| `TokenException` | 403 (sometimes 401 — see caveat above) | `access_token` is invalid, expired, or revoked. | Re-authenticate: generate a new token (dashboard or [TOTP](https://api-docs.indstocks.com/Users/#method-2-totp-based-token-generation)) and retry. |
| `UserException` | 403 | The authenticated user can't perform this action — account status or a segment (e.g. F&O) isn't activated. | Not retryable by the client; the user needs to complete onboarding/activation for that segment. |
| `NotFoundException` | 404 | The endpoint or resource (e.g. a specific order ID) doesn't exist. | Check the path/ID. Not retryable as-is. |
| `MethodNotAllowedException` | 405 | Wrong HTTP method for this endpoint (e.g. `GET` where `POST` is required). | Fix the method. Not retryable as-is. |
| `DataException` | 400 | Bad market/historical data parameters — invalid timeframe or instrument token. | Check `scrip-codes`/`interval`/time range against [Historical Data](https://api-docs.indstocks.com/historicalData/). Not retryable as-is. |
| `NetworkException` | 503 | Temporary issue reaching an upstream service (e.g. an exchange). | Safe to retry after a short delay with backoff. |
| `GeneralException` | 500 | Unexpected server-side error. | Safe to retry once; report if it persists. |
| `ServiceUnavailableException` | 503 | API is temporarily down for maintenance or overloaded. | Retry after a short delay with backoff. |
| `GatewayTimeoutException` | 504 | Timeout communicating with an upstream service. | Safe to retry after a short delay with backoff. |
| `429 Too Many Requests` | 429 | You exceeded a rate limit — see [API Conventions](https://api-docs.indstocks.com/conventions/#rate-limiting) for the per-category limits. | Back off and retry with a client-side rate limiter; do not hammer the endpoint. |

### Retry guidance

- **Safe to retry with backoff**: `NetworkException`, `GatewayTimeoutException`, any bare `5xx`,
  and `429` (after backing off).
- **Not retryable as-is**: `InputException`, `TokenException` (fix the token first),
  `UserException`, `NotFoundException`, `MethodNotAllowedException`, `DataException` — these need
  the request itself fixed, not a retry.
- **Never blindly retry order placement.** If a place-order call times out or errors, you could
  end up with a duplicate order. Check [Get Order Book](https://api-docs.indstocks.com/normal_orders/#get-order-book) (or
  listen on the [Order Updates WebSocket](https://api-docs.indstocks.com/Websockets/#order-updates-feed)) to confirm the
  order's actual state before resending.

---

## Token Generation Errors (TOTP)

These apply to `POST /generate/token` only — the [TOTP-based token flow](https://api-docs.indstocks.com/Users/#method-2-totp-based-token-generation).
That endpoint uses `x-api-key` rather than `Authorization`, so an auth failure here means a bad
Client ID, MPIN, or TOTP code — not an expired `access_token`.

| Situation | Meaning | Expected client action |
|---|---|---|
| Wrong `mpin` | The MPIN doesn't match the account. | Fix the MPIN. Not retryable as-is. |
| Wrong or expired `totp` | The code was mistyped, already used, or generated outside its validity window. **Counts toward the lockout.** | Wait for the next code from the authenticator app. Never retry the same code. |
| Throttled | You called the endpoint more than once in 60 seconds. | Reuse the token you already hold — see [Token lifecycle](https://api-docs.indstocks.com/Users/#token-lifecycle). |
| Locked out | 5 wrong codes in 15 minutes (15-minute lockout), or 3 lockouts within an hour (1-hour lockout). | Back off for the full window; retrying during a lockout will keep failing. Verify your server clock. |
| Every code rejected, app shows a valid code | Server clock drift — TOTP is time-derived, so a skewed clock produces codes the server won't accept. | Sync the host clock via NTP. |
| TOTP was disabled from the dashboard | The stored secret is deleted and the active token is revoked. | Re-run setup on the website, then generate a fresh token. |

**WARNING: Exact status codes and `error_type` values here are not yet confirmed**

The behavior above is accurate, but the specific HTTP status and `error_type` returned for each
case have not been verified against a live deployment. **Don't branch on a specific
`error_type` string for this endpoint yet** — read the HTTP status and the `message` field, and
treat any non-2xx as "no new token; back off." We'll pin the exact codes here once confirmed.

A lockout blocks *new* token generation only. Any access token already issued keeps working until
its normal 24-hour expiry — see [TOTP limits and lockouts](https://api-docs.indstocks.com/Users/#totp-limits-and-lockouts).

---

## Order-Specific Errors (RMS)

When an order is rejected, it typically returns an `OrderException` (`400 Bad Request`) with one
of the following messages from our Risk Management System (RMS). These are free-text `message`
values, not separate `error_type` codes:

| Message | Meaning |
|---|---|
| `RMS: Margin exceeds ...` | The order requires more margin than is available in the account. |
| `RMS: Rule: Check ...` | A custom risk rule was triggered, preventing the order. |
| `RMS: Blocked for ...` | The account or security is blocked for trading by the RMS team for surveillance reasons. |
| `The instrument is not tradable.` | The specified security is not available for trading in the requested segment. |
| `The quantity is not a multiple of the lot size.` | For F&O instruments, the order quantity must be a multiple of the lot size. |
| `The price is out of the circuit limit.` | The order price is outside the security's daily upper or lower circuit limit. |
| `Order price must be a multiple of the tick size.` | The order price is not a valid multiple of the instrument's minimum price movement (tick size). |
| `Market orders are blocked for this instrument.` | Market orders are disabled for this security, often due to low liquidity. Use a limit order instead. |
| `The order quantity exceeds the freeze limit.` | The order quantity is larger than the maximum allowed for a single order by the exchange. Break it into smaller chunks. |
| `Position could not be found.` | An attempt was made to modify or cancel an order that doesn't exist or has already completed. |
| `The order is already pending...` | The order is already awaiting exchange confirmation, so it can't be modified right now. |

---

## Quick Troubleshooting

| Situation | Typical HTTP | What to do |
|---|---|---|
| Every request fails immediately | 403 / 401 | `access_token` is empty, wrong, or expired — regenerate it. |
| Token stopped working mid-day, well before 24h | 403 | Something replaced or revoked it: another process called `/generate/token` (only the newest TOTP token stays valid), it was revoked from the dashboard, or TOTP was disabled. See [Token lifecycle](https://api-docs.indstocks.com/Users/#token-lifecycle). |
| `/generate/token` keeps failing | — | Wrong MPIN/TOTP, the 60-second throttle, a lockout, or server clock drift — see [Token Generation Errors](#token-generation-errors-totp). |
| One specific request fails | 400 | Read `message`; check the request body/params against that endpoint's page and the [Glossary](https://api-docs.indstocks.com/glossary/) for correct enum casing. |
| Requests started failing under load | 429 | You hit a rate limit — see [API Conventions](https://api-docs.indstocks.com/conventions/#rate-limiting). |
| Order rejected | 400 (`OrderException`) | Check the RMS message table above. |
| Intermittent failures, same request otherwise works | 5xx | Transient — retry idempotent reads with backoff; for orders, reconcile via Order Book first. |


---

# Source: https://api-docs.indstocks.com/glossary/

# Glossary & Constants

---

## Order ID Prefixes

| Prefix | Meaning            | Used For                                                            |
|--------|---------------------|----------------------------------------------------------------------|
| `EQ-`  | Equity order        | Standard and smart-order parent orders in the `EQUITY` segment.      |
| `DRV-` | Derivative order     | Standard and smart-order parent orders in the `DERIVATIVE` segment.  |
| `GTT-` | Good Till Triggered  | Smart order child legs (always), and smart order parents when the limit price falls outside the circuit range. |

See [Smart Orders (GTT)](https://api-docs.indstocks.com/smart_orders/#order-id-prefixes) for the parent/child relationship
these prefixes describe.

---

## Core Request Enums

| Field         | Values                                              | Used In                                      |
|---------------|------------------------------------------------------|-----------------------------------------------|
| `txn_type`    | `BUY`, `SELL`                                        | Orders, Smart Orders, Margin                  |
| `exchange`    | `NSE`, `BSE`                                          | Orders, Smart Orders, Margin                  |
| `segment`     | `EQUITY`, `DERIVATIVE` *(uppercase)*                  | Orders, Smart Orders, Margin, Order/Trade lookups |
| `segment`     | `equity`, `derivative` *(lowercase)*                  | Portfolio Positions query parameter only      |
| `segment`     | `INDEX`, `EQUITY` *(uppercase)*                       | [Option Chain](https://api-docs.indstocks.com/utility/#option-chain) query parameter only |
| `product`     | `CNC`, `INTRADAY`, `MARGIN` *(uppercase)*             | Orders, Smart Orders, Margin                  |
| `product`     | `cnc`, `intraday`, `margin` *(lowercase)*             | Portfolio Positions query parameter only      |
| `order_type`  | `LIMIT`, `MARKET` (Orders); `LIMIT`, `MARKET`, `TRIGGER` (Smart Orders) | Orders, Smart Orders          |
| `validity`    | `DAY`, `IOC` (Orders); `DAY` only (Smart Orders)       | Orders, Smart Orders                          |
| `algo_id`     | `99999` (NSE), `9999999999999999` (BSE)               | Orders, Smart Orders                          |
| `source`      | `equity`, `fno`, `index`                              | Instruments CSV download                      |

**WARNING: `segment` means three different things**

Watch the casing and the value set — they are not interchangeable:

- **Orders / Smart Orders / Margin / lookups**: uppercase `EQUITY` or `DERIVATIVE`
- **Portfolio Positions**: lowercase `equity` or `derivative`
- **Option Chain**: uppercase `INDEX` or `EQUITY` — here it describes the *underlying's* segment,
  so `DERIVATIVE` is not a valid value even though the chain returns derivative contracts

---

## Instrument Code Formats

Two different formats are used depending on whether you're calling REST or WebSocket:

| Context | Format | Separator | Example |
|---|---|---|---|
| REST (`scrip-codes` query param) | `SEGMENT_TOKEN` | underscore `_` | `NSE_3045`, `NFO_51011` |
| WebSocket (`instruments` array) | `SEGMENT:TOKEN` | colon `:` | `NSE:2885`, `NFO:51011` |
| REST (`security_id`, `underlying-scrip`) | bare token, **no prefix** | — | `2885`, `40000001` |

The bare form is used wherever the segment is already given by another parameter — `security_id` on
the order endpoints, and `underlying-scrip` on the [Option Chain](https://api-docs.indstocks.com/utility/#option-chain), where
`segment` carries that information instead.

**WARNING: Index tokens: take them from the instruments file for the endpoint you are calling**

For `underlying-scrip`, NIFTY 50 is `40000001`, as listed by
`/market/instruments?source=index`. Note that the [WebSocket](https://api-docs.indstocks.com/Websockets/) documentation uses
`NIDX:26000` as its NSE index example, so the two surfaces may not share the same index token.
Do not carry a token from one to the other — look it up per surface.

**WebSocket segment prefixes:**

| Prefix  | Meaning              |
|---------|------------------------|
| `NSE:`  | NSE Equity             |
| `BSE:`  | BSE Equity             |
| `NFO:`  | NSE Derivatives (F&O)  |
| `BFO:`  | BSE Derivatives (F&O)  |
| `NIDX:` | NSE Index              |
| `BIDX:` | BSE Index              |

The underlying instrument identifier (`SECURITY_ID` / scrip code / token) is the same number in
both formats — only the segment prefix and separator differ. Look up the right `SECURITY_ID` for
a symbol via the [Instruments API](https://api-docs.indstocks.com/instruments/).

---

## Order Status Values

The full list of order statuses (`QUEUED`, `INITIATED`, `SUCCESS`, `CANCELLED`, etc.) is
maintained in one place: [Orders — Order Status Types](https://api-docs.indstocks.com/normal_orders/#order-status-types).

---

## WebSocket Message Fields

| Field | Values | Feed |
|---|---|---|
| `action` | `subscribe`, `unsubscribe` | Price Feed |
| `action` | `subscribe` | Order Updates Feed |
| `mode` | `ltp`, `quote` | Price Feed |
| `mode` | `order_update` | Order Updates Feed |

See the [WebSockets guide](https://api-docs.indstocks.com/Websockets/) for full subscription payloads and response shapes.

---

## TOTP Authentication

| Field | Description |
|---|---|
| `mpin` | Your INDstocks account MPIN. |
| `totp` | The current 6-digit TOTP code from your authenticator app. |
| `x-api-key` | Your Client ID — a static, per-account identifier shown on the dashboard after successful TOTP setup. Sent as a header instead of `Authorization`, and distinct from `access_token`. |

**Limits at a glance**

| Rule | Value |
|---|---|
| Minimum gap between token generations | 1 per 60 seconds |
| Wrong TOTP codes before lockout | 5 in a rolling 15 minutes → 15-minute lockout |
| Repeated lockouts | 3 within 1 hour → 1-hour lockout + email alert |
| Time to complete TOTP setup | 5 minutes |
| Concurrent TOTP tokens | 1 — a new token invalidates the previous one |
| Access token validity | 24 hours |

See [Getting Your Access Token — Method 2](https://api-docs.indstocks.com/Users/#method-2-totp-based-token-generation) and
[TOTP limits and lockouts](https://api-docs.indstocks.com/Users/#totp-limits-and-lockouts).


---

# Source: https://api-docs.indstocks.com/faq/

# Frequently Asked Questions (FAQ)

Find answers to the most common questions about the INDstocks Trading API.

## General

### Who can use INDstocks APIs?

INDstocks APIs can be used by:

- **Individual Traders & Investors** - Anyone with an INDstocks account and completed KYC verification can access the APIs
- **Algorithmic Traders** - Build and deploy automated trading strategies with advanced order types
- **Fintech Developers** - Integrate trading capabilities into financial applications
- **Trading Platform Users** - Connect INDstocks to algo platforms like Tradetron for automated trading
- **Institutional Traders** - Enterprise-grade APIs suitable for institutional platforms
- **Quant Traders** - Access historical data and real-time market feeds for backtesting and strategy development

The API is free to access with no subscription fees - you only pay ₹10 flat brokerage per order.

### Why should I use INDstocks APIs?

**Key Benefits:**

- **Free API Access** - No subscription fees or API charges, only ₹10 per order brokerage
- **High Performance** - Sub-100ms latency for order execution
- **Real-Time Data** - WebSocket streams for live market data and order updates
- **Comprehensive Coverage** - Multi-exchange support (NSE, BSE) and all asset classes (Equity, Derivatives, Options, Futures)
- **Advanced Trading Features** - Smart Orders (GTT) with multi-leg strategies, OCO support, and automated stop-loss/target execution
- **Enterprise-Grade Security** - Token-based authentication with encryption in transit
- **Developer-Friendly** - RESTful APIs with JSON responses, extensive documentation, and code examples in Python, JavaScript, and cURL
- **Transparent Pricing** - Flat ₹10 per order with no hidden costs

### What types of APIs does INDstocks provide?

INDstocks provides a comprehensive suite of RESTful APIs organized into these categories:

**1. User Management & Authentication**

- User profile and account details
- Funds and margin information

**2. Market Data APIs**

- Real-time market quotes (full quotes, LTP, market depth)
- Historical OHLCV data with multiple intervals
- Instruments master data (CSV download)

**3. Order Management**

- Place, modify, and cancel orders
- Order book and trade history
- Support for multiple order types (LIMIT, MARKET, STOP_LOSS, etc.)

**4. Smart Orders (GTT)**

- Multi-leg trading strategies
- Automated stop-loss and target orders
- OCO (One-Cancels-Other) support

**5. Portfolio & Risk Management**

- Holdings and positions tracking
- Real-time P&L calculations
- Margin calculations for orders

**6. WebSocket Streaming**

- Live market data streaming
- Real-time order updates
- Portfolio change notifications

**7. Utility APIs**

- Option chain data, with Greeks (Delta, Gamma, Theta, Vega) and implied volatility included

See the [API Overview](https://api-docs.indstocks.com/api-overview/) for complete documentation.

### Can I integrate INDstocks Trading API into my trading platform?

**Yes, absolutely!** You can integrate INDstocks APIs in two ways:

**Option 1: For Algo Platform Users (e.g., Tradetron)**

- Get your access token from [indstocks.com/app/api-trading/access-tokens](https://indstocks.com/app/api-trading/access-tokens)
- Connect to your algo platform by selecting "INDmoney" as your broker
- Paste your access token and start trading

**Option 2: For Custom Integration (Developers)**

- Build your own trading application using our RESTful APIs
- Available in any programming language (Python, JavaScript, Java, etc.)
- Comprehensive documentation with code examples
- Deep integration capabilities for order execution, market data, and portfolio management
- WebSocket support for real-time updates

The API uses standard RESTful conventions with JSON payloads, making it easy to integrate with any platform or application.

Check out our [Getting Started Guide](https://api-docs.indstocks.com/getting-started/) for detailed integration instructions.

### What are the prerequisites for accessing INDstocks API?

Before you can access the INDstocks API, you need:

- ✅ **An INDstocks account** - [Sign up at indstocks.com](https://indstocks.com) (free registration)
- ✅ **Completed KYC verification** - Required by SEBI regulations for trading in Indian markets
- ✅ **Funds in your account** - For placing actual trades (no minimum investment required for API access)
- ✅ **Basic programming knowledge** (for DIY developers) - Python or JavaScript recommended, but any language that can make HTTP requests works

That's it! No additional approvals or subscriptions needed.

### How do I get access to INDstocks API?

Getting access is quick and simple:

**Step 1: Create Your Account**

- Sign up at [indstocks.com](https://indstocks.com)
- Complete your KYC verification (SEBI requirement)

**Step 2: Get Your Access Token**

- Log in to your INDstocks account
- Navigate to [indstocks.com/app/api-trading/access-tokens](https://indstocks.com/app/api-trading/access-tokens)
- Generate your access token (or set up TOTP-based generation — see [Getting Your Access Token](https://api-docs.indstocks.com/Users/#getting-your-access-token))
- Copy your access token

**Step 3: Start Using the API**

- **For developers**: Use the token in your API requests (see [Getting Started Guide](https://api-docs.indstocks.com/getting-started/))
- **For algo platforms**: Paste the token into your platform's broker integration settings

**Important Notes:**

- Access tokens expire after 24 hours and must be regenerated
- API access is free - no subscription required
- You can start making API calls immediately after getting your token

### Is there a minimum investment amount?

**For API Access**: **No minimum investment required** to access the APIs. You can generate your access token and explore the APIs even with zero balance.

**For Trading**: You need sufficient funds in your account to place actual trades:

- The minimum depends on the specific stock/instrument you want to trade
- For equity delivery (CNC), you need funds to buy at least 1 share
- For intraday (INTRADAY) or derivatives (MARGIN), margin requirements apply based on the instrument

**Brokerage**: ₹10 flat per order, regardless of order size

**Recommendation**: Start with small quantities to test your integration before scaling up to larger trades.

## Getting Started

### How do I get started with the INDstocks API?

Getting started is simple:

1. **Sign up** at [indstocks.com](https://indstocks.com)
2. **Complete KYC** verification
3. **Go to** [indstocks.com/app/api-trading/access-tokens](https://indstocks.com/app/api-trading/access-tokens)
4. **Generate access token** (dashboard or TOTP-based — see [Getting Your Access Token](https://api-docs.indstocks.com/Users/#getting-your-access-token))
5. **Start making API calls** using the access token

Check out our [Getting Started Guide](https://api-docs.indstocks.com/getting-started/) for a detailed walkthrough.

### Do I need programming experience to use the API?

Basic programming knowledge is recommended. You should be familiar with:

- Making HTTP requests
- Working with JSON data
- Understanding REST API concepts

Official SDKs are coming soon (see [Do you provide SDKs?](#sdks--integration) below) — until
then, every endpoint page has ready-to-use cURL, Python, and JavaScript examples.

### Is there a sandbox or testing environment?

We recommend starting with small quantities to test your integration and strategies. All API calls are made in the live environment, so please test carefully with minimal risk.

## Authentication & Security

### How do I authenticate API requests?

Authentication is simple:

1. Log in to [indstocks.com](https://indstocks.com)
2. Go to [indstocks.com/app/api-trading/access-tokens](https://indstocks.com/app/api-trading/access-tokens) and generate your access token
3. Copy your access token
4. Include it in the `Authorization` header of every API request

```python
headers = {"Authorization": "YOUR_ACCESS_TOKEN"}
```

See the [Authentication Guide](https://api-docs.indstocks.com/Users/) for more details.

### How long is my access token valid?

Access tokens expire after 24 hours. You will need to generate a new token from your dashboard once your current token expires. For security:

- Tokens automatically expire after 24 hours
- Generate a new token when the old one expires
- Revoke tokens immediately if compromised
- Never share tokens or commit them to version control

Note that only **one TOTP-generated token is live at a time** — generating a new one
invalidates the previous one. See [Token lifecycle](https://api-docs.indstocks.com/Users/#token-lifecycle).

### Can I generate my access token from a script, without logging in?

Yes — set up TOTP once on the website, then call `POST /generate/token` with your Client ID,
MPIN, and a current TOTP code. This is the intended path for unattended/headless strategies.

Two things to keep in mind:

- **Generate once per session, not per request.** Only the newest TOTP token is valid, so a
  second call invalidates the token your other processes are using. There's also a hard limit
  of **1 token per 60 seconds**.
- **Setup itself is web-only.** Enabling, resetting, and disabling TOTP all require a
  logged-in session on the website; there's no API for it.

Full details in [Getting Your Access Token — Method 2](https://api-docs.indstocks.com/Users/#method-2-totp-based-token-generation).

### I'm locked out of TOTP token generation. What now?

Five wrong TOTP codes within 15 minutes triggers a **15-minute lockout**; three lockouts
within an hour triggers a **1-hour lockout** plus an email alert. Retrying during a lockout
just fails — it won't extend the lockout, but it won't help either.

While you wait:

- **Check your server clock.** This is the most common cause. TOTP codes are derived from the
  current time, so a host whose clock has drifted will generate codes the server rejects even
  though the app displays them as valid. Sync via NTP.
- **Your existing access token still works.** A lockout blocks new token generation only; a
  token already issued keeps working until its 24-hour expiry, so a running strategy isn't
  interrupted.
- If you're being locked out repeatedly with no explanation, email `instockssupport@indmoney.com`.

See [TOTP limits and lockouts](https://api-docs.indstocks.com/Users/#totp-limits-and-lockouts).

### I lost the phone with my authenticator app. How do I recover?

The TOTP secret is shown exactly once during setup and can never be re-displayed, so there's
nothing to recover — you re-enroll instead:

1. Log in to the website and choose **Disable TOTP**. This deletes the stored secret and
   revokes your currently-active token.
2. Run **Setup TOTP** again to get a fresh secret and QR code.
3. Generate a new access token and update your application.

If you can't log in to the website at all, use the standard forgot-password / account-unlock
journey, or contact `instockssupport@indmoney.com` for a support-assisted disable.

### What should I do if my access token is compromised?

1. **Immediately revoke** the compromised token from your dashboard
2. **Generate a new token** 
3. **Update your application** with the new token
4. **Review account activity** for unauthorized actions
5. **Contact support** if you notice suspicious activity

### What is the Static IP Settings panel for?

Static IP whitelisting is required for order placement — placing, modifying, and cancelling
orders via the API — per NSE Circular NSE/INVG/67858 (May 5, 2025), Section A. Market data,
order-book, and other read-only endpoints are not affected.

See [Static IP Settings](https://api-docs.indstocks.com/Users/#static-ip-settings).

### Does INDstocks support IPv6 static IPs?

Yes. Both the Primary and Secondary Static IP fields accept IPv4 and IPv6 addresses in
standard notation. You can mix formats, or use IPv6 for both slots.

### How many static IPs can I register?

Two — a Primary and a Secondary. The Secondary slot exists for backup/failover; both are
treated identically for order-placement whitelisting.

### How often can I change a static IP?

Per NSE Circular NSE/INVG/67858 (May 5, 2025), Annexure Section A, Point 6, a static IP
cannot be updated more than once a calendar week.

### Can I clear a slot entirely, leaving it blank?

No. Once a slot has a value, it can only be replaced with another valid IP — there's no
supported way to blank it out.

### I've decommissioned the server behind my old IP. Is it safe to leave it in the slot?

Yes. An inactive, no-longer-used IP sitting in a whitelist slot doesn't affect order routing,
compliance, or audit standing. If you'd like to stop seeing it, replace it with your current
active IP whenever your next weekly change window opens — there's no urgency either way.

## Pricing & Costs

### How much does the API cost?

**API Access: FREE** ✅

- No subscription fees
- No API usage charges
- No hidden costs

**Brokerage: ₹10 per order** (regardless of order size)

This makes INDstocks a cost-effective trading API option.

### Are there any rate limits?

Yes, to ensure fair usage and system stability. Rate limits vary by endpoint category
(Order APIs, Data/Quote APIs, Non-Trading APIs) — see the authoritative table in
[API Conventions](https://api-docs.indstocks.com/conventions/#rate-limiting) rather than a single flat number.
WebSocket connection and subscription limits are documented in the
[WebSockets guide](https://api-docs.indstocks.com/Websockets/).

Contact us for higher limits if you need them for institutional use.

### Is there a minimum deposit required?

No minimum deposit is required to open an account, but you need sufficient funds/margin to place trades based on your trading strategy.

## Trading & Orders

### Why is my order being rejected?

Common reasons for order rejection:

| Error | Solution |
|-------|----------|
| **Insufficient margin** | Add more funds to your account |
| **Invalid price** | Ensure price is within circuit limits and tick size |
| **Invalid quantity** | For F&O, quantity must be in multiples of lot size |
| **Market closed** | Orders can only be placed during trading hours |
| **Instrument blocked** | Some instruments may be blocked by RMS |

See our [Error Handling Guide](https://api-docs.indstocks.com/errors/) for comprehensive error codes.

### What order types are supported?

We support all major order types:

- **MARKET** - Execute at best available price
- **LIMIT** - Execute at specified price or better
- **STOP_LOSS** - Trigger when price reaches stop level
- **STOP_LOSS_MARKET** - Market order triggered at stop price
- **GTT (Good Till Triggered)** - Advanced conditional orders

Learn more in our [Order Management Guide](https://api-docs.indstocks.com/normal_orders/).

### Can I place orders outside market hours?

Yes! You can place orders anytime, and they will be queued for execution when the market opens. This is particularly useful for:

- Pre-market orders
- Algorithmic strategies that run 24/7
- International users in different time zones

### How do I modify or cancel an order?

**To modify an order:**
```python
response = requests.post(
    "https://api.indstocks.com/order/modify",
    headers={"Authorization": access_token},
    json={"order_id": "ORDER_ID", "qty": 200, "limit_price": 155.50},
)
```

**To cancel an order:**
```python
response = requests.post(
    "https://api.indstocks.com/order/cancel",
    headers={"Authorization": access_token},
    json={"order_id": "ORDER_ID"},
)
```

See [Order Management](https://api-docs.indstocks.com/normal_orders/) for details.

## Market Data

### How do I get real-time market quotes?

Use the Market Quotes API:

```python
response = requests.get(
    "https://api.indstocks.com/market/quotes/full",
    headers={"Authorization": access_token},
    params={"symbols": "NSE:RELIANCE,NSE:TCS"},
)
quotes = response.json()
```

For streaming data, use [WebSockets](https://api-docs.indstocks.com/Websockets/) for lower latency.

### How much historical data is available?

We provide **10+ years** of historical data for all instruments including:

- 1-minute candles
- 5-minute candles
- 15-minute candles
- 1-hour candles
- Daily candles

See [Historical Data API](https://api-docs.indstocks.com/historicalData/) for details.

### What's the difference between REST API and WebSocket for market data?

| Feature | REST API | WebSocket |
|---------|----------|-----------|
| **Latency** | Standard | Real-time |
| **Update frequency** | On-demand | Real-time push |
| **Use case** | Periodic updates | High-frequency trading |
| **Connection** | Request-response | Persistent connection |

Use REST for occasional updates, WebSocket for continuous streaming.

## WebSockets

### How do I connect to the WebSocket?

```javascript
const ws = new WebSocket('wss://api.indstocks.com/ws');

ws.on('open', () => {
    // Subscribe to symbols
    ws.send(JSON.stringify({
        action: 'subscribe',
        symbols: ['NSE:RELIANCE', 'NSE:TCS']
    }));
});

ws.on('message', (data) => {
    const tick = JSON.parse(data);
    console.log('Live quote:', tick);
});
```

Check the [WebSocket Guide](https://api-docs.indstocks.com/Websockets/) for comprehensive documentation.

### Why is my WebSocket connection dropping?

Common causes:

- **No heartbeat**: Send ping messages every 30 seconds
- **Network issues**: Implement automatic reconnection
- **Too many subscriptions**: Limit to 3,000 instruments per connection (see [WebSockets](https://api-docs.indstocks.com/Websockets/))
- **Invalid authentication**: Ensure token is valid

Always implement reconnection logic in production applications.

## Smart Orders (GTT)

### What are Smart Orders?

Smart Orders (GTT - Good Till Triggered) are advanced conditional orders that execute automatically when specified conditions are met:

- **Single Trigger**: Execute one order when price target is reached
- **OCO (One Cancels Other)**: Place both stop-loss and target together
- **Multi-leg strategies**: Complex conditional logic

Learn more in our [Smart Orders Guide](https://api-docs.indstocks.com/smart_orders/).

### How long do Smart Orders remain active?

Smart Orders remain active for up to **365 days** or until:

- The trigger condition is met
- You manually cancel them
- The instrument expires (for derivatives)

## Performance & Reliability

### What is the API uptime?

We maintain high availability with:

- Redundant infrastructure across multiple availability zones
- Real-time monitoring and alerts
- Automatic failover capabilities
- Regular maintenance during non-trading hours

### What is the typical API latency?

Our API is optimized for fast performance:

- **Order execution**: Fast response time for order placement
- **WebSocket data**: Real-time data streaming
- **REST API calls**: Optimized response times
- **Authentication**: Quick token validation

### How do you ensure data accuracy?

- Direct exchange connectivity
- Real-time validation and reconciliation
- Multiple data source cross-verification
- Checksums and integrity verification

## SDKs & Integration

### Do you provide SDKs?

Official SDKs are in progress, not yet published:

- **Python**: Coming soon — currently in development.
- **JavaScript/Node.js**: Coming soon.
- **Java**: Coming soon.

Until an SDK ships, use the REST API directly — every endpoint page on this site has
ready-to-use cURL, Python (`requests`), and JavaScript (`fetch`) examples.

### Can I use the API with any programming language?

Yes! Our REST API can be used with any language that supports HTTP requests. We provide examples in:

- Python
- JavaScript
- cURL
- Java (coming soon)
- C# (coming soon)

## Troubleshooting

### I'm getting 'TokenException' errors

This means your access token is invalid or expired:

1. Verify the token is correctly copied from dashboard
2. Check for extra spaces or newlines
3. Ensure you're including it in the `Authorization` header
4. Generate a new token if the issue persists

### Orders are taking longer than expected to execute

Check these factors:

- **Market volatility**: High volatility can cause delays
- **Exchange load**: Peak hours may have higher latency
- **Order type**: Market orders execute faster than limit orders
- **Network latency**: Use servers in India for lowest latency

We optimize for fast order execution.

### I'm not receiving WebSocket updates

Debug checklist:

- [ ] Connection is established (check 'open' event)
- [ ] Authentication is successful
- [ ] Subscription message was sent correctly
- [ ] Symbol format is correct (e.g., 'NSE:RELIANCE')
- [ ] You're listening for 'message' events
- [ ] Heartbeat/ping is being sent

## Support & Documentation

### Where can I get help?

Multiple support channels:

- **Email**: instockssupport@indmoney.com
- **Documentation**: [api-docs.indstocks.com](https://api-docs.indstocks.com)
- **Community**: Developer community coming soon
- **Status Page**: Check system status at status.indstocks.com

### How can I report a bug or request a feature?

- **Bugs**: Email instockssupport@indmoney.com with details
- **Feature requests**: Submit via our feedback form
- **Security issues**: security@indstocks.com (we have a responsible disclosure policy)

### Is there API versioning?

Yes, we follow semantic versioning:

- **Current version**: v1 (stable)
- **Deprecation notice**: 12 months before any breaking changes
- **Backwards compatibility**: Guaranteed for 2 years
- **Version header**: Include `API-Version: v1` in requests

---

## Still Have Questions?

Can't find what you're looking for? 

- 📧 Email us at **instockssupport@indmoney.com**
- 📚 Check our [complete API documentation](https://api-docs.indstocks.com/api-overview/)
- 💬 Developer community coming soon

---

## Related Pages

- [Getting Started Guide](https://api-docs.indstocks.com/getting-started/)
- [API Overview](https://api-docs.indstocks.com/api-overview/)
- [Authentication](https://api-docs.indstocks.com/Users/)
- [Order Management](https://api-docs.indstocks.com/normal_orders/)
- [Error Handling](https://api-docs.indstocks.com/errors/)
- [WebSockets](https://api-docs.indstocks.com/Websockets/)


---

# Source: https://api-docs.indstocks.com/changelog/

# Changelog

Track all updates, new features, improvements, and bug fixes to the INDstocks Trading API.

**TIP: Stay Updated**

Subscribe to our [API Status Page](https://status.indstocks.com) for real-time updates and maintenance notifications.

---

## [Unreleased]

### ✨ New Features

- **Order Remarks**: `/order` and `/smart/order` accept an optional `remarks` string — your own tag for an order (strategy name, signal id, anything you reconcile against). It comes back on `GET /order`, `GET /order-book` and `GET /trade-book`, so fills line up with your own system without a separate order-id map. Max 100 characters (longer is truncated, not rejected), fixed once placed, carried onto every leg of a smart order, and never sent to the exchange. A small reserved set is refused. See [Order Remarks](https://api-docs.indstocks.com/normal_orders/#order-remarks).
- **Option Chain with Greeks**: `GET /market/option-chain` returns the full strike ladder for an index or stock underlying in one call, with per-leg last price, OI and previous OI, volume, top-of-book bid/ask, implied volatility and Greeks (`delta`, `gamma`, `theta`, `vega`). Takes `exchange`, `segment` (`INDEX`/`EQUITY`), `underlying-scrip`, `expiry` (`YYYY-MM-DD`) and an optional `strike_count` (per side of ATM, default `10`). See [Option Chain](https://api-docs.indstocks.com/utility/#option-chain).
- **Contracts & Expiries**: five endpoints for discovering derivative contracts without parsing the instruments master CSV. `GET /market/instruments/search` and `GET /market/instruments/expiries` cover currently trading contracts and their upcoming expiries; `GET /market/instruments/expired/search`, `GET /market/instruments/expired/expiries` and `GET /market/instruments/expired/contracts` do the same for expiries that have already passed, which is what makes historical options work. Filter by `instrument_type`, `expiry`, `option_type` and a `strike_from`/`strike_to` band. Only `segment=DERIVATIVE` is supported for now. Expired contracts are keyed by `trading_symbol` rather than `security_id`, because exchanges recycle instrument tokens after expiry. See [Contracts & Expiries](https://api-docs.indstocks.com/contracts/).
- **Historical Data for Expired Contracts** — *coming soon; not yet enabled on production*: `GET /market/historical/expired/{interval}` will return OHLCV candles for F&O contracts whose expiry has already passed, completing the price history of a contract from listing through to expiry. Contracts are addressed by `trading-symbols` — the `trading_symbol` from [Contracts & Expiries](https://api-docs.indstocks.com/contracts/) — rather than `scrip-codes`, since exchanges reuse numeric instrument tokens once a contract has expired. Up to 50 symbols per call, from either derivatives segment — `segment=NFO` for NSE (the default) and `segment=BFO` for BSE. The candle contract, the supported intervals and the per-call maximum ranges all match [Get Historical Data](https://api-docs.indstocks.com/historicalData/#get-historical-data). See [Get Expired Contract Candles](https://api-docs.indstocks.com/historicalData/#get-expired-contract-candles).

- **Trailing Stop Loss (TSL) for Smart Orders** — *not yet enabled on production; `is_tsl` and `tsl_step_size` are currently accepted and ignored*: `/smart/order` will accept `is_tsl` and `tsl_step_size`, turning the stop-loss leg into a trailing stop-loss that steps along behind a favourable move and holds when the price turns. `/order` and `/order-book` expose `is_tsl` and `tsl_step_size`, and report the live trailed trigger in `sl_trigger_price`. Requires a stop-loss leg; not supported for `TRIGGER` orders; the step size is fixed once placed. See [Trailing Stop Loss](https://api-docs.indstocks.com/smart_orders/#trailing-stop-loss-tsl).

### 📚 Documentation

- **Candle timestamps**: [Historical Data](https://api-docs.indstocks.com/historicalData/#candle-timestamps) now specifies that `ts` is the candle's **open** time and that each candle covers `[ts, ts + interval)` — opening time inclusive, next candle's opening time exclusive — with a worked `5minute` example and a note on deriving a closing time. It also names the intraday anchor: buckets align to the 09:15 IST session open rather than to wall-clock `:00`/`:05` boundaries.
- **Paging by date range**: the per-call maximum range and how to page past it now have their own section, [Paging by date range](https://api-docs.indstocks.com/historicalData/#paging-by-date-range), covering both historical endpoints.

### 🐛 Bug Fixes

- **Stop-loss and target legs on `TRIGGER` smart orders**: a `TRIGGER` parent carrying a stop-loss leg (BUY) or a target leg (SELL) was rejected with a nonsensical bound — for example `SL Trigger Price should be less than -0.05`. The legs were being checked against `limit_price`. A `TRIGGER` order enters at its `trigger_limit_price` and does not use `limit_price`, so for any request following the documented contract the bound was computed from zero and collapsed to one tick either side of it. Both legs are now checked against the order's entry price: `trigger_limit_price`, or `trigger_price` when that is omitted. The same correction closes the opposite case, where a leg on the wrong side of the entry — a BUY target below it, or a SELL stop-loss below it — was accepted silently. See [Smart Orders](https://api-docs.indstocks.com/smart_orders/).

---

## [v1.3.0] - 2024-12-15

### ✨ New Features

- **Option Greeks API**: Calculate real-time Greeks (Delta, Gamma, Theta, Vega, Rho) for options
- **Multi-leg Order Support**: Place complex multi-leg strategies in a single API call
- **Enhanced Historical Data**: Extended historical data availability to 15 years
- **Instrument Search API**: New endpoint to search instruments by name/symbol

### 🚀 Improvements

- **Faster order execution**: Optimized order routing for improved performance
- **WebSocket reliability**: Improved connection stability with automatic reconnection
- **Better error messages**: More descriptive error responses with actionable solutions
- **Rate limit headers**: Added `X-RateLimit-*` headers to all responses

### 🐛 Bug Fixes

- Fixed issue where some F&O instruments showed incorrect lot sizes
- Resolved WebSocket disconnection issues during high market volatility
- Fixed timezone handling in historical data API
- Corrected margin calculation for spread orders

### 📚 Documentation

- Added comprehensive [FAQ page](https://api-docs.indstocks.com/faq/)
- New [Getting Started tutorial](https://api-docs.indstocks.com/getting-started/)
- Enhanced [WebSocket guide](https://api-docs.indstocks.com/Websockets/) with reconnection examples

---

## [v1.2.5] - 2024-10-28

### 🚀 Improvements

- **Faster market quotes**: Reduced latency for `/market/quotes` endpoint by 40%
- **Batch order support**: Place up to 50 orders in a single API call
- **Enhanced portfolio API**: Added real-time P&L calculations
- **Better GTT order handling**: Improved trigger accuracy for Smart Orders

### 🐛 Bug Fixes

- Fixed race condition in order modification during high-frequency trading
- Resolved issue with incorrect available margin calculation
- Fixed WebSocket subscription limits not being enforced correctly

### 📚 Documentation

- Added Python SDK examples to all major endpoints
- Updated [Order Management guide](https://api-docs.indstocks.com/normal_orders/) with batch order examples
- Improved [Error Handling documentation](https://api-docs.indstocks.com/errors/)

---

## [v1.2.0] - 2024-09-10

### ✨ New Features

- **Smart Orders (GTT)**: Launch of advanced Good Till Triggered orders
- **OCO Orders**: One-Cancels-Other order type for automated risk management
- **Option Chain API**: Get complete option chain data with Greeks
- **Funds API Enhancement**: Added detailed fund breakdown and utilization

### 🚀 Improvements

- **Infrastructure upgrades**: Improved reliability and performance
- **Enhanced security**: Added rate limiting and DDoS protection
- **Better WebSocket performance**: Improved message delivery
- **Improved margin API**: Real-time margin requirements for complex orders

### 🐛 Bug Fixes

- Fixed issue with historical data gaps during market holidays
- Resolved incorrect LTP for illiquid securities
- Fixed order book pagination issues

---

## [v1.1.5] - 2024-07-22

### 🚀 Improvements

- **Order execution speed**: Improved performance
- **Historical data**: Added 5-minute candle intervals
- **Position tracking**: Enhanced positions API with average price calculations
- **Better error codes**: More specific error types for easier debugging

### 🐛 Bug Fixes

- Fixed timezone issues in historical data responses
- Resolved WebSocket authentication errors on reconnection
- Fixed incorrect holdings valuation for bonus shares

### 📚 Documentation

- Added JavaScript SDK examples
- Improved [API Conventions documentation](https://api-docs.indstocks.com/conventions/)
- New examples for [Portfolio API](https://api-docs.indstocks.com/portfolio_funds/)

---

## [v1.1.0] - 2024-06-05

### ✨ New Features

- **WebSocket API**: Real-time market data streaming with <5ms latency
- **Order Updates Stream**: Real-time order and trade confirmations via WebSocket
- **Historical Data API**: Access 10+ years of OHLCV data
- **Margin Calculator**: Pre-calculate margin requirements before placing orders

### 🚀 Improvements

- **API performance**: 50% reduction in average response time
- **Better authentication**: More detailed token validation errors
- **Enhanced holdings API**: Added average price and P&L fields
- **Improved instruments data**: Daily updates with corporate actions

### 🐛 Bug Fixes

- Fixed order rejection for AMO (After Market Orders)
- Resolved issues with special characters in instrument names
- Fixed incorrect order status in edge cases

---

## [v1.0.8] - 2024-04-15

### 🚀 Improvements

- **Order placement**: Reduced order execution time by 25%
- **Market data**: Added market depth (Level 2) data
- **Portfolio API**: Enhanced with realized P&L tracking
- **Better logging**: Improved request tracking for debugging

### 🐛 Bug Fixes

- Fixed issue with duplicate order IDs in rare cases
- Resolved rate limiting false positives
- Fixed incorrect exchange segment mapping for some instruments

### 📚 Documentation

- Added comprehensive [Margin Calculation guide](https://api-docs.indstocks.com/margin_calculation/)
- New examples for basket orders
- Improved [Market Quotes documentation](https://api-docs.indstocks.com/MarketQuote/)

---

## [v1.0.5] - 2024-02-20

### ✨ New Features

- **Positions API**: Track open derivative positions with real-time P&L
- **Trade History**: New endpoint to retrieve detailed trade confirmations
- **Instrument Master**: Daily CSV file with all tradeable instruments

### 🚀 Improvements

- **Better error handling**: More descriptive error messages
- **Enhanced order types**: Added support for bracket orders
- **Improved performance**: Faster response times across all endpoints
- **Security enhancements**: Additional validation for order parameters

### 🐛 Bug Fixes

- Fixed issues with limit orders at circuit limits
- Resolved timeout errors during market open
- Fixed incorrect quantity validation for F&O orders

---

## [v1.0.0] - 2024-01-10

### 🎉 Initial Release

The INDstocks Trading API v1 is now live!

#### Core Features

- **Order Management**: Place, modify, and cancel orders across all segments
- **Market Data**: Real-time quotes, LTP, and market depth
- **Portfolio Management**: Holdings, positions, and funds APIs
- **Authentication**: Simple token-based authentication
- **User Profile**: Access account details and preferences

#### Supported Segments

- Equity (NSE, BSE)
- Derivatives (Futures & Options)
- Currency derivatives
- Commodity derivatives

#### Technical Specs

- **Rate limits**: 10 orders/sec, 100 API calls/sec
- **API access**: Free

---

## Version History Summary

| Version | Release Date | Highlights |
|---------|-------------|------------|
| v1.3.0 | 2024-12-15 | Option Greeks, Multi-leg orders, 15yr historical data |
| v1.2.5 | 2024-10-28 | Batch orders, Faster quotes, Enhanced GTT |
| v1.2.0 | 2024-09-10 | Smart Orders (GTT), OCO orders |
| v1.1.5 | 2024-07-22 | Enhanced execution, 5-min candles |
| v1.1.0 | 2024-06-05 | WebSocket API, Historical data, Margin calculator |
| v1.0.8 | 2024-04-15 | Market depth, Enhanced portfolio |
| v1.0.5 | 2024-02-20 | Positions API, Trade history |
| v1.0.0 | 2024-01-10 | Initial release |

---

## Upcoming Features

We're constantly improving the INDstocks API. Here's what's coming next:

### 🔜 Q1 2025

- [ ] **Algo Trading Framework**: Built-in strategy templates and backtesting
- [ ] **Advanced Analytics**: Pre-built indicators and signals
- [ ] **Mobile SDKs**: Native iOS and Android SDKs
- [ ] **GraphQL API**: Alternative to REST for complex queries

### 🔮 Q2 2025

- [ ] **Webhooks**: Push notifications for order updates and triggers
- [ ] **Enhanced Greeks**: Historical Greeks data and implied volatility
- [ ] **Social Trading**: Copy trading and strategy sharing

### 💡 Roadmap

Want to influence our roadmap? Share your feature requests at api-feedback@indstocks.com

---

## Deprecation Policy

We're committed to backwards compatibility:

- **Deprecation notice**: 12 months advance warning
- **Support period**: 24 months for deprecated features
- **Migration guides**: Provided for all breaking changes
- **API versioning**: Semantic versioning (MAJOR.MINOR.PATCH)

### Currently Deprecated

*No features are currently deprecated.*

---

## Migration Guides

### Migrating from v1.2.x to v1.3.x

No breaking changes. All v1.2.x code is compatible with v1.3.x.

**New optional fields:**
- Added `greeks` field to option quotes (opt-in via query parameter)
- Added `strategy_type` field for multi-leg orders

### Migrating from v1.1.x to v1.2.x

No breaking changes. All v1.1.x code is compatible with v1.2.x.

**Enhanced features:**
- GTT orders now support multiple triggers (backwards compatible)
- Historical data now includes adjusted prices (new field, old field unchanged)

---

## Release Notes Format

Each release includes:

- **✨ New Features**: Brand new capabilities
- **🚀 Improvements**: Enhancements to existing features
- **🐛 Bug Fixes**: Resolved issues and bugs
- **📚 Documentation**: Documentation updates and additions
- **⚠️ Breaking Changes**: Incompatible changes (rare, with migration guide)
- **🔒 Security**: Security-related improvements

---

## Stay Connected

- 📧 **Email**: api-updates@indstocks.com
- 📊 **Status Page**: [status.indstocks.com](https://status.indstocks.com)
- 🐦 **Twitter**: [@INDstocksApp](https://twitter.com/INDstocksApp)
- 💬 **Community**: Developer community coming soon

---

## Need Help?

Questions about a specific version or feature?

- Check our [FAQ](https://api-docs.indstocks.com/faq/)
- Read the [API Overview](https://api-docs.indstocks.com/api-overview/)
- Contact support at instockssupport@indmoney.com


---

# Source: https://api-docs.indstocks.com/openapi-spec.yaml

OpenAPI 3.0 specification, verbatim.

```yaml
openapi: 3.0.3
info:
  title: INDstocks API Suite
  description: |
    The INDstocks API Suite v1 — trading, market data, portfolio, and Smart Order (GTT)
    endpoints for the INDstocks (INDmoney) broker platform.

    ## Base URL
    Production: `https://api.indstocks.com`

    ## Authentication
    Every protected endpoint requires an `access_token` sent as a raw value (no `Bearer` prefix)
    in the `Authorization` header: `Authorization: <access_token>`. See
    [Getting Your Access Token](https://api-docs.indstocks.com/Users/#getting-your-access-token).

    ## Rate Limiting
    See the authoritative table at
    [API Conventions](https://api-docs.indstocks.com/conventions/#rate-limiting) — limits vary by
    endpoint category (Order / Data & Quote / Non-Trading) and are not a single flat number.

    ## Errors
    See the [Error Bucket](https://api-docs.indstocks.com/errors/) for the full list of
    `error_type` values, HTTP statuses, and RMS order-rejection messages.
  version: "1.0.0"
  contact:
    name: INDstocks API Support
    email: instockssupport@indmoney.com
    url: https://api-docs.indstocks.com
  license:
    name: INDstocks API License
    url: https://indstocks.com/api-license
  termsOfService: https://indstocks.com/terms-of-service

servers:
  - url: https://api.indstocks.com
    description: Production server

security:
  - AccessTokenAuth: []

tags:
  - name: User & Account
    description: Profile, funds, and access-token generation
  - name: Instruments
    description: Instrument master (scrip) CSV download
  - name: Market Data
    description: Real-time quotes, market depth, and historical candles
  - name: Orders
    description: Standard order placement, modification, cancellation, and history
  - name: Smart Orders
    description: Multi-leg GTT orders with stop-loss and target legs
  - name: Portfolio
    description: Holdings and open positions
  - name: Margin
    description: Pre-trade margin and charges calculation
  - name: Utility
    description: Option chain and Greeks

paths:
  /user/profile:
    get:
      tags: [User & Account]
      summary: Get user profile
      description: Returns the authenticated user's profile. Useful to verify a token is valid.
      responses:
        '200':
          description: Profile retrieved
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/UserProfileResponse'
        '401':
          description: Token invalid/expired (see error shape caveat in the Error Bucket)
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ErrorResponse'

  /funds:
    get:
      tags: [User & Account]
      summary: Get funds
      description: Available and utilized funds, balances, and P&L.
      responses:
        '200':
          description: Funds retrieved
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/FundsResponse'

  /generate/token:
    post:
      tags: [User & Account]
      summary: Generate access token via TOTP (provisional)
      description: |
        **Provisional — not yet confirmed against a live deployment.** Generates an
        `access_token` using an account MPIN plus a TOTP code, as an alternative to the
        dashboard token-generation flow. Uses a distinct auth model: `x-api-key` header instead
        of `Authorization`. See
        [Getting Your Access Token — Method 2](https://api-docs.indstocks.com/Users/#method-2-totp-based-token-generation)
        for the current caveats — success/error response shapes are not yet confirmed.

        ## Limits

        - **1 token per 60 seconds.** Call this once per session and cache the result; do not
          call it before each request.
        - **Only the newest token is valid.** Each success invalidates the token from the
          previous call, so two processes generating independently will keep killing each
          other's token. Have one process generate and share it.
        - **Lockout:** 5 wrong `totp` codes in a rolling 15 minutes locks generation for 15
          minutes; 3 lockouts within an hour locks it for 1 hour. A lockout blocks new tokens
          only — an already-issued token keeps working until its 24-hour expiry.
        - TOTP enrolment, reset, and disable are **web-only**; there is no API for them.

        See
        [TOTP limits and lockouts](https://api-docs.indstocks.com/Users/#totp-limits-and-lockouts).
      security:
        - ApiKeyAuth: []
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              required: [mpin, totp]
              properties:
                mpin:
                  type: string
                  description: Account MPIN
                  example: "9828"
                totp:
                  type: string
                  description: Current 6-digit TOTP code
                  example: "610446"
      responses:
        '200':
          description: "Token generated (response shape not yet confirmed)"

  /market/instruments:
    get:
      tags: [Instruments]
      summary: Get instrument list (CSV)
      description: Downloads the scrip/instrument master as a CSV file for a market segment.
      parameters:
        - in: query
          name: source
          required: true
          schema:
            type: string
            enum: [equity, fno, index]
          example: fno
      responses:
        '200':
          description: CSV file of instruments
          content:
            text/csv:
              schema:
                type: string
        '400':
          description: Source not present or invalid
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ErrorResponse'

  /market/instruments/search:
    get:
      tags: [Instruments]
      summary: Search live derivative contracts
      description: |
        Individual contracts currently trading on an underlying. Only `segment=DERIVATIVE` is
        supported — `EQUITY` returns 400. Futures rows carry `strike_price: null` and
        `option_type: null`. Pass `expiry` to get the contracts for a single expiry; there is no
        separate live "contracts for an expiry" endpoint.
      parameters:
        - $ref: '#/components/parameters/Underlying'
        - $ref: '#/components/parameters/Segment'
        - $ref: '#/components/parameters/InstrumentType'
        - $ref: '#/components/parameters/Expiry'
        - in: query
          name: strike_from
          schema: { type: number }
          description: Lower bound on strike price. Options only.
        - in: query
          name: strike_to
          schema: { type: number }
          description: Upper bound on strike price. Options only.
        - $ref: '#/components/parameters/OptionType'
        - $ref: '#/components/parameters/Page'
        - $ref: '#/components/parameters/PageSize'
      responses:
        '200':
          description: Matching contracts
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/InstrumentSearchResponse'
        '400':
          description: Unsupported segment or invalid parameter
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/DebugInfoErrorResponse'

  /market/instruments/expiries:
    get:
      tags: [Instruments]
      summary: List upcoming expiries
      description: Upcoming expiry dates for an underlying, ascending. Feed these into `expiry`.
      parameters:
        - $ref: '#/components/parameters/Underlying'
        - $ref: '#/components/parameters/Segment'
      responses:
        '200':
          description: Expiry dates, ascending
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ExpiryListResponse'
        '400':
          description: Unsupported segment or invalid parameter
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/DebugInfoErrorResponse'

  /market/instruments/expired/search:
    get:
      tags: [Instruments]
      summary: Search expired derivative contracts
      description: |
        As `/market/instruments/search`, over contracts whose expiry has passed. `expiry_from` and
        `expiry_to` are mandatory and may not span more than 5 years. The response omits
        `security_id`: exchanges recycle instrument tokens after expiry, so `trading_symbol` is the
        only stable key for an expired contract.
      parameters:
        - $ref: '#/components/parameters/Underlying'
        - $ref: '#/components/parameters/Segment'
        - $ref: '#/components/parameters/InstrumentType'
        - $ref: '#/components/parameters/ExpiryFrom'
        - $ref: '#/components/parameters/ExpiryTo'
        - in: query
          name: strike_from
          schema: { type: number }
          description: Lower bound on strike price. Options only.
        - in: query
          name: strike_to
          schema: { type: number }
          description: Upper bound on strike price. Options only.
        - $ref: '#/components/parameters/OptionType'
        - $ref: '#/components/parameters/Page'
        - $ref: '#/components/parameters/PageSize'
      responses:
        '200':
          description: Matching expired contracts
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ExpiredInstrumentSearchResponse'
        '400':
          description: Missing expiry window, span over 5 years, inverted range, or unsupported segment
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/DebugInfoErrorResponse'

  /market/instruments/expired/expiries:
    get:
      tags: [Instruments]
      summary: List past expiries
      description: |
        Past expiry dates within a window, **descending**. The window is capped at 1 year and the
        boundary is strict — a 363-day span is accepted, 399 days is rejected. Walk backwards a
        year at a time for deeper history.
      parameters:
        - $ref: '#/components/parameters/Underlying'
        - $ref: '#/components/parameters/Segment'
        - $ref: '#/components/parameters/ExpiryFrom'
        - $ref: '#/components/parameters/ExpiryTo'
      responses:
        '200':
          description: Expiry dates, descending
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ExpiryListResponse'
        '400':
          description: Missing expiry window, span over 1 year, or inverted range
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/DebugInfoErrorResponse'

  /market/instruments/expired/contracts:
    get:
      tags: [Instruments]
      summary: Get the contract chain for one past expiry
      description: |
        The full chain for a single past expiry. `data` is a flat array — **not** paginated, unlike
        the search endpoints — and runs roughly 460-480 rows for a NIFTY expiry. `expiry` must be a
        date returned by `/market/instruments/expired/expiries`.
      parameters:
        - $ref: '#/components/parameters/Underlying'
        - $ref: '#/components/parameters/Segment'
        - in: query
          name: expiry
          required: true
          schema: { type: string, format: date }
          description: An expiry returned by `/market/instruments/expired/expiries`
          example: '2026-07-28'
        - $ref: '#/components/parameters/InstrumentType'
      responses:
        '200':
          description: Full contract chain for the expiry
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ExpiredContractListResponse'
        '400':
          description: Unknown underlying, segment or expiry
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/DebugInfoErrorResponse'

  /market/quotes/full:
    get:
      tags: [Market Data]
      summary: Get full market quotes
      description: Full snapshot (OHLC, day change, volume, circuit limits, market depth) for up to 1000 instruments.
      parameters:
        - $ref: '#/components/parameters/ScripCodes'
      responses:
        '200':
          description: Quotes retrieved
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/FullQuoteResponse'

  /market/quotes/ltp:
    get:
      tags: [Market Data]
      summary: Get LTP quote
      description: Lightweight endpoint returning only the Last Traded Price per instrument.
      parameters:
        - $ref: '#/components/parameters/ScripCodes'
      responses:
        '200':
          description: LTP retrieved
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/LtpQuoteResponse'

  /market/quotes/mkt:
    get:
      tags: [Market Data]
      summary: Get market depth
      description: 5-level bid/ask ladder (market depth) for one or more instruments.
      parameters:
        - $ref: '#/components/parameters/ScripCodes'
      responses:
        '200':
          description: Market depth retrieved

  /market/historical/{interval}:
    get:
      tags: [Market Data]
      summary: Get historical OHLCV data
      description: |
        Response uses a `success` boolean (not `status`) and `data` keyed per scrip code, with
        each candle as an object `{ts, o, h, l, c, v}` — not a positional array.
      parameters:
        - in: path
          name: interval
          required: true
          schema:
            type: string
            enum: [1minute, 2minute, 3minute, 4minute, 5minute, 10minute, 15minute, 30minute, 60minute, 120minute, 180minute, 240minute, 1day, 1week, 1month]
        - in: query
          name: scrip-codes
          required: true
          schema:
            type: string
          example: NSE_3045
        - in: query
          name: start_time
          required: true
          schema:
            type: integer
            format: int64
          description: Unix epoch milliseconds (IST), inclusive
        - in: query
          name: end_time
          required: true
          schema:
            type: integer
            format: int64
          description: Unix epoch milliseconds (IST), exclusive
      responses:
        '200':
          description: Historical data retrieved
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/HistoricalDataResponse'

  /market/historical/expired/{interval}:
    get:
      tags: [Market Data]
      summary: Get historical OHLCV data for expired contracts (coming soon)
      description: |
        **Coming soon — not yet enabled on production.** The contract below is settled and is
        published so clients can build against it in advance.

        OHLCV candles for F&O contracts whose expiry has passed, keyed by `trading_symbol`.
        Exchanges reuse numeric instrument tokens once a contract has expired, so `trading_symbol`
        is the stable identifier for a contract that is no longer trading.

        Same candle contract as `/market/historical/{interval}`: a `success` boolean (not
        `status`), `data` keyed per instrument, and each candle an object `{ts, o, h, l, c, v}`.
        `ts` is the candle's OPEN time in epoch seconds and covers `[ts, ts + interval)`.

        Up to 50 symbols per call. Set `segment=BFO` for BSE derivatives; `NFO` (NSE) is the
        default. Supported intervals and the per-call maximum range are the same as
        `/market/historical/{interval}`. A request for a wider window returns the maximum
        window's data rather than an error, and there is no continuation token: page by date
        range.
      parameters:
        - in: path
          name: interval
          required: true
          schema:
            type: string
            enum: [1minute, 2minute, 3minute, 4minute, 5minute, 10minute, 15minute, 30minute, 60minute, 120minute, 180minute, 240minute, 1day, 1week, 1month]
        - in: query
          name: trading-symbols
          required: true
          schema:
            type: string
          description: 1-50 comma-separated trading symbols. Unknown symbols are omitted from `data` rather than rejected.
          example: NIFTY26AUG2523950CE
        - in: query
          name: segment
          required: false
          schema:
            type: string
            enum: [NFO, BFO]
            default: NFO
          description: >-
            Exchange derivatives segment. `NFO` for NSE F&O (the default), `BFO` for BSE F&O.
            A BSE trading symbol requested without `segment=BFO` is looked up against NSE and
            is absent from `data`.
        - in: query
          name: start_time
          required: true
          schema:
            type: integer
            format: int64
          description: Unix epoch milliseconds (IST), inclusive
        - in: query
          name: end_time
          required: true
          schema:
            type: integer
            format: int64
          description: Unix epoch milliseconds (IST), exclusive
      responses:
        '200':
          description: Historical data retrieved
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/HistoricalDataResponse'
        '400':
          description: Missing/invalid parameter, invalid interval, or more than 50 symbols
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ErrorResponse'

  /order:
    post:
      tags: [Orders]
      summary: Place order
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/OrderRequest'
      responses:
        '200':
          description: Order placed
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/OrderActionResponse'
        '400':
          description: Validation or RMS rejection
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ErrorResponse'
    get:
      tags: [Orders]
      summary: Get order details
      description: Sends `order_id`/`segment` as a JSON body on a GET request.
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              required: [order_id, segment]
              properties:
                order_id:
                  type: string
                segment:
                  type: string
                  enum: [EQUITY, DERIVATIVE]
      responses:
        '200':
          description: Order details retrieved
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/OrderDetails'

  /order/modify:
    post:
      tags: [Orders]
      summary: Modify pending order
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/OrderModifyRequest'
      responses:
        '200':
          description: Order modified
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/OrderActionResponse'

  /order/cancel:
    post:
      tags: [Orders]
      summary: Cancel pending order
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              required: [order_id, segment]
              properties:
                order_id:
                  type: string
                segment:
                  type: string
                  enum: [EQUITY, DERIVATIVE]
      responses:
        '200':
          description: Order cancelled
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/OrderActionResponse'

  /order-book:
    get:
      tags: [Orders]
      summary: Get order book
      description: All orders placed during the current trading day.
      responses:
        '200':
          description: Order book retrieved
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/OrderBookResponse'

  /order/trades:
    get:
      tags: [Orders]
      summary: Get trades for an order
      description: |
        Sends `order_id`/`segment` as a JSON body on a GET request. Response uses the same
        shape as [Get Trade Book](#/Orders/get_trade_book) scoped to a single order.
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              required: [order_id, segment]
              properties:
                order_id:
                  type: string
                segment:
                  type: string
                  enum: [EQUITY, DERIVATIVE]
      responses:
        '200':
          description: Trades retrieved
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/TradeListResponse'

  /trade-book:
    get:
      operationId: get_trade_book
      tags: [Orders]
      summary: Get trade book
      description: All executed trades (fills) for a segment during the current trading day.
      parameters:
        - in: query
          name: segment
          required: true
          schema:
            type: string
            enum: [EQUITY, DERIVATIVE]
      responses:
        '200':
          description: Trade book retrieved
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/TradeBookResponse'

  /smart/order:
    post:
      tags: [Smart Orders]
      summary: Place smart order (GTT)
      description: Places a multi-leg GTT order. Creates a parent order plus a linked child `GTT-` order for the stop-loss/target legs.
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/SmartOrderRequest'
      responses:
        '200':
          description: Smart order placed
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/SmartOrderActionResponse'

  /smart/order/modify:
    post:
      tags: [Smart Orders]
      summary: Modify smart order
      description: Modify one leg (parent or child) of a pending smart order by its own `order_id`.
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/SmartOrderModifyRequest'
      responses:
        '200':
          description: Smart order modified
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/OrderActionResponse'

  /smart/order/cancel:
    post:
      tags: [Smart Orders]
      summary: Cancel smart order
      description: Cancel one leg (parent or child) of a pending smart order by its own `order_id`.
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              required: [order_id, segment]
              properties:
                order_id:
                  type: string
                segment:
                  type: string
                  enum: [EQUITY, DERIVATIVE]
      responses:
        '200':
          description: Smart order cancelled
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/OrderActionResponse'

  /portfolio/holdings:
    get:
      tags: [Portfolio]
      summary: Get holdings
      description: Equity holdings (Demat account). No live valuation/P&L fields — combine with Market Quotes for that.
      responses:
        '200':
          description: Holdings retrieved
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/HoldingsResponse'

  /portfolio/positions:
    get:
      tags: [Portfolio]
      summary: Get positions
      description: |
        Flat array of open positions (not a `net_positions`/`day_positions` wrapper). Note:
        `segment`/`product` query values here are **lowercase**, unlike the uppercase values
        used by Orders/Margin.
      parameters:
        - in: query
          name: segment
          required: true
          schema:
            type: string
            enum: [equity, derivative]
        - in: query
          name: product
          required: true
          schema:
            type: string
            enum: [cnc, intraday, margin]
      responses:
        '200':
          description: Positions retrieved
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/PositionsResponse'

  /margin:
    get:
      tags: [Margin]
      summary: Calculate margin
      description: Sends the request as a JSON body on a GET request. Calculates margin requirement and charges breakdown before placing an order.
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/MarginRequest'
      responses:
        '200':
          description: Margin calculated
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/MarginResponse'

  /market/option-chain:
    get:
      tags: [Utility]
      summary: Get the option chain for an underlying, including Greeks and IV
      description: |
        Returns the strike ladder for one underlying and one expiry. Each strike carries a call
        (`ce`) and put (`pe`) leg with last price, open interest, volume, top-of-book bid/ask,
        implied volatility and Greeks.

        `strikes` is an object keyed by strike price, not an array; key order is not guaranteed.
        `strike_count` counts strikes on each side of the at-the-money strike, so the response
        holds `(2 * strike_count) + 1` strikes.

        See https://api-docs.indstocks.com/utility/#option-chain
      parameters:
        - in: query
          name: exchange
          required: true
          schema:
            type: string
            enum: [NSE, BSE]
          description: Exchange of the option contracts.
          example: NSE
        - in: query
          name: segment
          required: true
          schema:
            type: string
            enum: [INDEX, EQUITY]
          description: >-
            Segment of the underlying, which determines the instruments file `underlying-scrip`
            is taken from.
          example: INDEX
        - in: query
          name: underlying-scrip
          required: true
          schema:
            type: string
          description: >-
            `SECURITY_ID` of the underlying (not of a contract). Index underlyings come from
            `/market/instruments?source=index`; stock underlyings from the cash row of
            `/market/instruments?source=equity`.
          example: "40000001"
        - in: query
          name: expiry
          required: true
          schema:
            type: string
            format: date
          description: Contract expiry in `YYYY-MM-DD` format.
          example: "2026-08-18"
        - in: query
          name: strike_count
          required: false
          schema:
            type: integer
            default: 10
          description: Strikes to return on each side of the at-the-money strike.
          example: 10
      responses:
        '200':
          description: Option chain for the requested underlying and expiry
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/OptionChainResponse'
        '400':
          description: >-
            A required parameter is missing or invalid, or the `Authorization` header was not sent.
            Note this endpoint does not use the standard error envelope.
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/OptionChainErrorResponse'
        '429':
          description: Rate limit exceeded
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/RateLimitErrorResponse'

components:
  parameters:
    ScripCodes:
      in: query
      name: scrip-codes
      required: true
      schema:
        type: string
      description: Comma-separated `SEGMENT_TOKEN` instrument identifiers, e.g. `NSE_3045,NFO_51011`
      example: NSE_3045

    Underlying:
      in: query
      name: underlying
      required: true
      schema:
        type: string
      description: Underlying symbol
      example: NIFTY

    Segment:
      in: query
      name: segment
      required: true
      schema:
        type: string
        enum: [DERIVATIVE]
      description: Market segment. Only `DERIVATIVE` is supported; `EQUITY` returns 400.
      example: DERIVATIVE

    InstrumentType:
      in: query
      name: instrument_type
      schema:
        type: string
        enum: [OPTIDX, OPTSTK, FUTIDX, FUTSTK]
      description: |
        Contract type. Omit to return every type. `EQ` is not usable — it would require
        `segment=EQUITY`, which is not yet supported.
      example: OPTIDX

    OptionType:
      in: query
      name: option_type
      schema:
        type: string
        enum: [CE, PE]
      description: Option side. Options only.
      example: CE

    Expiry:
      in: query
      name: expiry
      schema:
        type: string
        format: date
      description: Restrict results to a single expiry
      example: '2026-08-25'

    ExpiryFrom:
      in: query
      name: expiry_from
      required: true
      schema:
        type: string
        format: date
      description: Start of the expiry window
      example: '2025-10-23'

    ExpiryTo:
      in: query
      name: expiry_to
      required: true
      schema:
        type: string
        format: date
      description: End of the expiry window
      example: '2026-08-09'

    Page:
      in: query
      name: page
      schema:
        type: integer
        default: 1
      description: Page number

    PageSize:
      in: query
      name: page_size
      schema:
        type: integer
        default: 50
        maximum: 100
      description: Results per page

  securitySchemes:
    AccessTokenAuth:
      type: apiKey
      in: header
      name: Authorization
      description: Raw access token — no `Bearer` prefix. Example header value is the token itself.
    ApiKeyAuth:
      type: apiKey
      in: header
      name: x-api-key
      description: Used only by `/generate/token` (provisional) — distinct from AccessTokenAuth.

  schemas:
    UserProfileResponse:
      type: object
      properties:
        status: { type: string, example: success }
        data:
          type: object
          properties:
            user_id: { type: string }
            email: { type: string }
            first_name: { type: string }
            last_name: { type: string }
            demat_id: { type: string }
            is_nse_onboarded: { type: boolean }
            is_bse_onboarded: { type: boolean }
            is_nse_fno_onboarded: { type: boolean }
            is_bse_fno_onboarded: { type: boolean }
            ucc: { type: string }
            is_ddpi_active: { type: boolean }

    FundsResponse:
      type: object
      properties:
        status: { type: string, example: success }
        data:
          type: object
          properties:
            sod_balance: { type: number }
            pledge_received: { type: number }
            pledge_remained: { type: number }
            detailed_avl_balance:
              type: object
              properties:
                option_sell: { type: number }
                future: { type: number }
                option_buy: { type: number }
                comm_option_buy: { type: number }
                eq_mis: { type: number }
                eq_cnc: { type: number }
                eq_mtf: { type: number }
            withdrawal_balance: { type: number }
            funds_added: { type: number }
            funds_withdrawn: { type: number }
            realized_pnl: { type: number }
            unrealized_pnl: { type: number }
            brokerage: { type: number }
            eq_charges: { type: number }
            fno_charges: { type: number }

    FullQuoteResponse:
      type: object
      properties:
        status: { type: string, example: success }
        data:
          type: object
          additionalProperties:
            type: object
            properties:
              live_price: { type: number }
              day_change: { type: number }
              day_change_percentage: { type: number }
              day_low: { type: number }
              day_high: { type: number }
              day_open: { type: number }
              prev_close: { type: number }
              52week_high: { type: number }
              52week_low: { type: number }
              upper_circuit: { type: number }
              lower_circuit: { type: number }
              volume: { type: integer }
              market_depth: { type: object }

    LtpQuoteResponse:
      type: object
      properties:
        status: { type: string, example: success }
        data:
          type: object
          additionalProperties:
            type: object
            properties:
              live_price: { type: number }

    InstrumentContract:
      type: object
      properties:
        security_id: { type: string, example: '58072', description: Numeric instrument ID. Absent on expired contracts. }
        trading_symbol: { type: string, example: NIFTY26AUG25FUT }
        instrument_type: { type: string, enum: [OPTIDX, OPTSTK, FUTIDX, FUTSTK] }
        expiry: { type: string, format: date, example: '2026-08-25' }
        strike_price: { type: number, nullable: true, description: 'null for futures' }
        option_type: { type: string, nullable: true, enum: [CE, PE], description: 'null for futures' }
        lot_size: { type: integer, example: 75 }

    InstrumentSearchResponse:
      type: object
      properties:
        status: { type: string, example: success }
        data:
          type: object
          properties:
            count: { type: integer, example: 804, description: Total matches across all pages }
            page: { type: integer, example: 1 }
            page_size: { type: integer, example: 50 }
            instruments:
              type: array
              items:
                $ref: '#/components/schemas/InstrumentContract'

    ExpiredInstrumentSearchResponse:
      allOf:
        - $ref: '#/components/schemas/InstrumentSearchResponse'
        - description: |
            Same shape as InstrumentSearchResponse, except `security_id` is never present —
            expired contracts are keyed by `trading_symbol` because exchange tokens are recycled
            after expiry.

    ExpiryListResponse:
      type: object
      properties:
        status: { type: string, example: success }
        data:
          type: array
          description: Expiry dates. Ascending on the live endpoint, descending on the expired one.
          items: { type: string, format: date }
          example: ['2026-08-25', '2026-09-01', '2026-09-08']

    ExpiredContractListResponse:
      type: object
      description: Flat array — this endpoint is not paginated.
      properties:
        status: { type: string, example: success }
        data:
          type: array
          items:
            $ref: '#/components/schemas/InstrumentContract'

    DebugInfoErrorResponse:
      type: object
      description: |
        Error shape used by the contract/expiry endpoints and the Option Chain. `message` is a
        generic label; the actionable reason is in `debug_info`.
      properties:
        message: { type: string, example: Bad Request }
        debug_info: { type: string, example: 'Invalid or unknown underlying/segment/expiry passed' }

    HistoricalDataResponse:
      type: object
      properties:
        success: { type: boolean, example: true }
        data:
          type: object
          additionalProperties:
            type: object
            properties:
              candles:
                type: array
                items:
                  type: object
                  properties:
                    ts: { type: integer, description: "Candle OPEN time, Unix epoch seconds (IST). The candle covers [ts, ts + interval)." }
                    o: { type: number }
                    h: { type: number }
                    l: { type: number }
                    c: { type: number }
                    v: { type: integer }

    OrderRequest:
      type: object
      required: [txn_type, exchange, segment, product, order_type, validity, security_id, qty, algo_id]
      properties:
        txn_type: { type: string, enum: [BUY, SELL] }
        exchange: { type: string, enum: [NSE, BSE] }
        segment: { type: string, enum: [EQUITY, DERIVATIVE] }
        product: { type: string, enum: [CNC, INTRADAY, MARGIN] }
        order_type:
          type: string
          enum: [LIMIT, MARKET]
          description: "MARKET is auto-converted to LIMIT at the live price before reaching the exchange."
        validity: { type: string, enum: [DAY, IOC] }
        security_id: { type: string }
        qty: { type: integer, minimum: 1 }
        algo_id:
          type: string
          description: '"99999" for NSE, "9999999999999999" for BSE'
        limit_price: { type: number, description: "Required if order_type is LIMIT" }
        is_amo: { type: boolean, default: false }
        remarks:
          type: string
          maxLength: 100
          description: >-
            Caller-supplied free-text tag (strategy name, signal id) stored with the order and
            echoed back by GET /order, GET /order-book and GET /trade-book. Longer than 100
            characters is silently truncated, not rejected. Cannot be changed on modify. Values
            reserved for INDstocks' internal channel tags (currently TV-TERMINAL, matched
            case-insensitively) are rejected. Never sent to the exchange.
          example: momentum-v2/sig-4471

    OrderModifyRequest:
      type: object
      required: [order_id, segment, qty, limit_price]
      properties:
        order_id: { type: string }
        segment: { type: string, enum: [EQUITY, DERIVATIVE] }
        qty: { type: integer, minimum: 1 }
        limit_price: { type: number }

    OrderActionResponse:
      type: object
      properties:
        status: { type: string, example: success }
        data:
          type: object
          properties:
            order_id: { type: string, example: "EQ-93586788" }
            order_status:
              type: string
              enum: [QUEUED, O-PENDING, SL-PENDING, PROCESSING, ABORTED, INITIATED, SUCCESS, CANCELLED, MODIFIED, PENDING, EXPIRED, FAILED, "PARTIALLY FILLED", "PARTIALLY FILLED - CANCELLED", "PARTIALLY FILLED - EXPIRED"]

    OrderDetails:
      type: object
      properties:
        status: { type: string, example: success }
        data:
          type: object
          properties:
            created_at: { type: string, format: date-time }
            updated_at: { type: string, format: date-time }
            user_id: { type: string }
            security_id: { type: string }
            isin: { type: string }
            name: { type: string }
            id: { type: string }
            exch_order_id: { type: string }
            txn_type: { type: string }
            exchange: { type: string }
            segment: { type: string }
            product: { type: string }
            order_type: { type: string }
            validity: { type: string }
            traded_qty: { type: integer }
            requested_qty: { type: integer }
            requested_price: { type: string }
            traded_price: { type: string }
            sl_trigger_price:
              type: string
              description: >-
                Stop-loss trigger price. For a trailing stop-loss (is_tsl true) this is the
                live trailed trigger, not the originally submitted price.
            sl_limit_price: { type: string }
            tgt_trigger_price: { type: string }
            tgt_limit_price: { type: string }
            status: { type: string }
            extra_info: { type: string }
            is_tsl:
              type: boolean
              description: >-
                NOT YET LIVE - no order currently returns this field. Intended: true when
                the order has an active trailing stop-loss.
            tsl_step_size:
              type: number
              description: Trailing step in rupees. Present only when is_tsl is true.
            remarks:
              type: string
              description: >-
                The tag supplied when the order was placed. Absent when the order carried
                no remark.
              example: momentum-v2/sig-4471

    OrderBookResponse:
      type: object
      properties:
        status: { type: string, example: success }
        data:
          type: array
          items:
            $ref: '#/components/schemas/OrderDetails/properties/data'

    TradeListResponse:
      type: object
      description: Fills for a single order (GET /order/trades). Carries no remarks — the caller already holds the order id.
      properties:
        status: { type: string, example: success }
        data:
          type: array
          items:
            type: object
            properties:
              fill_id: { type: integer }
              exch_order_id: { type: string }
              quantity: { type: integer }
              price: { type: number }
              trade_date: { type: string, format: date-time }

    TradeBookResponse:
      type: object
      description: All fills for a segment on the current trading day (GET /trade-book).
      properties:
        status: { type: string, example: success }
        data:
          type: array
          items:
            type: object
            properties:
              fill_id: { type: integer }
              exch_order_id: { type: string }
              quantity: { type: integer }
              price: { type: number }
              trade_date: { type: string, format: date-time }
              trade_serial_no: { type: string }
              scrip_code: { type: string }
              remarks:
                type: string
                description: >-
                  The tag supplied on the order that produced this fill. Absent when that
                  order carried no remark. Repeated on every fill of the same order.
                example: momentum-v2/sig-4471

    SmartOrderRequest:
      type: object
      required: [txn_type, exchange, segment, product, order_type, validity, security_id, qty, algo_id]
      properties:
        txn_type: { type: string, enum: [BUY, SELL] }
        exchange: { type: string, enum: [NSE] }
        segment: { type: string, enum: [EQUITY, DERIVATIVE] }
        product: { type: string, enum: [CNC, INTRADAY, MARGIN] }
        order_type: { type: string, enum: [LIMIT, MARKET, TRIGGER] }
        validity: { type: string, enum: [DAY] }
        security_id: { type: string }
        qty: { type: integer, minimum: 1 }
        algo_id: { type: string }
        limit_price:
          type: number
          description: >-
            Required if order_type is LIMIT. Not used for TRIGGER orders - omit it. It is not
            currently rejected on a TRIGGER request, but it is unsupported there and can change
            how the order is handled.
        trigger_price: { type: number, description: "Required if order_type is TRIGGER" }
        trigger_limit_price: { type: number }
        sl_trigger_price:
          type: number
          description: >-
            Stop-loss trigger. Requires sl_limit_price. Must sit below the entry price on a
            BUY and above it on a SELL, where the entry price is limit_price for LIMIT, the
            live market price for MARKET, and trigger_limit_price (or trigger_price when
            omitted) for TRIGGER.
        sl_limit_price: { type: number }
        tgt_trigger_price:
          type: number
          description: >-
            Target trigger. Requires tgt_limit_price. Must sit above the entry price on a
            BUY and below it on a SELL.
        tgt_limit_price: { type: number }
        is_tsl:
          type: boolean
          description: >-
            NOT YET LIVE - currently accepted and silently ignored; the order is placed with
            an ordinary non-trailing stop-loss. Intended: makes the stop-loss leg a trailing
            stop-loss. Requires a stop-loss leg (sl_trigger_price + sl_limit_price) and
            tsl_step_size. Not supported when order_type is TRIGGER. The trail activates only
            after the parent order executes.
        tsl_step_size:
          type: number
          description: >-
            Trailing step in rupees. Must be greater than zero and a multiple of the
            instrument's tick size. Required when is_tsl is true. Cannot be changed on modify.
        remarks:
          type: string
          maxLength: 100
          description: >-
            Caller-supplied free-text tag carried onto every leg, including the live order
            created when a stop-loss or target leg triggers. Longer than 100 characters is
            silently truncated, not rejected. Cannot be changed on modify. Values reserved for
            INDstocks' internal channel tags (currently TV-TERMINAL, matched case-insensitively)
            are rejected. Never sent to the exchange.
          example: momentum-v2/sig-4471

    SmartOrderModifyRequest:
      type: object
      required: [order_id, segment, algo_id]
      properties:
        order_id: { type: string }
        segment: { type: string, enum: [EQUITY, DERIVATIVE] }
        algo_id: { type: string }
        order_type:
          type: string
          enum: [LIMIT, MARKET, TRIGGER]
          description: Must match the existing order's type
        qty: { type: integer }
        limit_price: { type: number }
        trigger_price: { type: number }
        trigger_limit_price: { type: number }
        sl_trigger_price: { type: number }
        sl_limit_price: { type: number }
        tgt_trigger_price: { type: number }
        tgt_limit_price: { type: number }

    SmartOrderActionResponse:
      type: object
      properties:
        status: { type: string, example: success }
        data:
          type: object
          properties:
            order_data:
              type: array
              items:
                type: object
                properties:
                  order_id: { type: string }
                  order_status: { type: string }
                  child_order_details:
                    type: object
                    properties:
                      order_id: { type: string }
                      order_status: { type: string }

    HoldingsResponse:
      type: object
      properties:
        status: { type: string, example: success }
        data:
          type: array
          items:
            type: object
            properties:
              security_id: { type: string }
              symbol: { type: string }
              isin: { type: string }
              total_qty: { type: number }
              used_qty: { type: number }
              avg_price: { type: number }
              t1_qty: { type: number }
              t1_avg_price: { type: number }
              dp_qty: { type: number }
              dp_avg_price: { type: number }

    PositionsResponse:
      type: object
      properties:
        status: { type: string, example: success }
        data:
          type: array
          items:
            type: object
            properties:
              position_id: { type: string }
              security_id: { type: string }
              symbol: { type: string }
              segment: { type: string }
              product: { type: string }
              exchange: { type: string }
              isin: { type: string }
              drv_instrument: { type: string }
              drv_expiry_date: { type: string }
              drv_option_type: { type: string }
              drv_strike_price: { type: number }
              net_qty: { type: number }
              avg_price: { type: number }
              buy_qty: { type: number }
              buy_avg: { type: number }
              sell_qty: { type: number }
              sell_avg: { type: number }
              realized_profit: { type: number }
              day_buy_qty: { type: number, nullable: true }
              day_buy_val: { type: number, nullable: true }
              day_sell_qty: { type: number, nullable: true }
              day_sell_val: { type: number, nullable: true }
              cf_buy_qty: { type: number, nullable: true }
              cf_buy_val: { type: number, nullable: true }
              cf_sell_qty: { type: number, nullable: true }
              cf_sell_val: { type: number, nullable: true }

    MarginRequest:
      type: object
      required: [segment, exchange, securityID, txnType, quantity, price, product]
      properties:
        segment: { type: string, enum: [EQUITY, DERIVATIVE] }
        exchange: { type: string, enum: [NSE, BSE] }
        securityID: { type: string }
        txnType: { type: string, enum: [BUY, SELL] }
        quantity: { type: string }
        price: { type: string }
        product: { type: string, enum: [MARGIN, INTRADAY, CNC] }

    MarginResponse:
      type: object
      properties:
        status: { type: string, example: success }
        data:
          type: object
          properties:
            total_margin: { type: number }
            span_margin: { type: number }
            hedge_benefit: { type: number }
            exposure_margin: { type: number }
            available_balance: { type: number }
            var_margin: { type: number }
            insufficient_balance: { type: number }
            delivery_margin: { type: number }
            brokerage: { type: number }
            charges:
              type: object
              properties:
                stt: { type: number }
                exchange_charges: { type: number }
                stamp_duty: { type: number }
                sebi_turn_over_charges: { type: number }
                brokerage: { type: number }
                gst: { type: number }
                IPFTCharges: { type: number }
                total_charges: { type: number }

    ErrorResponse:
      type: object
      description: |
        Primary error shape. A second, undocumented-by-design shape also occurs on some
        endpoints: `{"message": "...", "success": false}` with no `status`/`error_type` — see
        the Error Bucket for details.
      properties:
        status: { type: string, example: error }
        message: { type: string }
        error_type:
          type: string
          enum: [InputException, TokenException, UserException, NotFoundException, MethodNotAllowedException, DataException, NetworkException, GeneralException, ServiceUnavailableException, GatewayTimeoutException, OrderException]

    OptionChainErrorResponse:
      type: object
      description: |
        Error shape used by the Option Chain endpoint. It does NOT use the standard
        `status`/`error_type` envelope. For parameter validation the reason is in `debug_info`
        while `message` is the generic string `Bad Request`; for a missing `Authorization` header
        the reason is in `message` and `success` is present instead.
      properties:
        message:
          type: string
          example: Bad Request
        debug_info:
          type: string
          example: Invalid exchange, segment, underlying-scrip or expiry passed
        success:
          type: boolean
          example: false

    RateLimitErrorResponse:
      type: object
      description: Rate-limit shape used by the Option Chain endpoint.
      properties:
        error:
          type: string
          example: Rate limit exceeded
        success:
          type: boolean
          example: false

    OptionChainGreeks:
      type: object
      description: Greeks for a single option contract. There is no `rho`.
      properties:
        delta: { type: number, format: double, example: 0.56 }
        gamma: { type: number, format: double, example: 0.0011 }
        theta: { type: number, format: double, example: -10.04 }
        vega: { type: number, format: double, example: 13.39 }

    OptionChainLeg:
      type: object
      description: One option contract — the call or put leg at a strike.
      properties:
        security_id:
          type: string
          description: The contract's `SECURITY_ID`; pass directly to the order endpoints.
          example: "45108"
        trading_symbol:
          type: string
          example: NIFTY-Aug2026-24450-CE
        last_price: { type: number, format: double, example: 167.9 }
        previous_close_price: { type: number, format: double, example: 274.95 }
        oi:
          type: integer
          format: int64
          description: Current open interest.
          example: 1608490
        previous_oi:
          type: integer
          format: int64
          description: Previous day's open interest; subtract to derive the OI change.
          example: 1606988
        volume: { type: integer, format: int64, example: 9079330 }
        top_bid_price: { type: number, format: double, example: 166.05 }
        top_bid_quantity: { type: integer, format: int64, example: 195 }
        top_ask_price: { type: number, format: double, example: 167.5 }
        top_ask_quantity: { type: integer, format: int64, example: 130 }
        iv:
          type: number
          format: double
          description: Implied volatility as a percentage (10.5 means 10.5%).
          example: 10.5
        greeks:
          $ref: '#/components/schemas/OptionChainGreeks'

    OptionChainStrike:
      type: object
      description: The call and put legs at a single strike price.
      properties:
        ce:
          $ref: '#/components/schemas/OptionChainLeg'
        pe:
          $ref: '#/components/schemas/OptionChainLeg'

    OptionChainResponse:
      type: object
      properties:
        status:
          type: string
          example: success
        data:
          type: object
          properties:
            underlying_ltp:
              type: number
              format: double
              description: Last traded price of the underlying.
              example: 24471.7
            expiry:
              type: string
              format: date
              example: "2026-08-18"
            strikes:
              type: object
              description: >-
                Map of strike price to its call and put legs. Keys are strike prices as strings;
                key order is not guaranteed, so sort numerically for an ordered ladder.
              additionalProperties:
                $ref: '#/components/schemas/OptionChainStrike'

externalDocs:
  description: Complete INDstocks API Documentation
  url: https://api-docs.indstocks.com
```

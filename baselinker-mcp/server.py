# server.py — BaseLinker MCP Server
import os
import asyncio
import datetime
from typing import Dict, List, Optional, Any

from fastmcp import FastMCP
import baselinker as bl

_port = int(os.environ.get("PORT", 8080))
mcp = FastMCP("BaseLinker", host="0.0.0.0", port=_port)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ts(dt: datetime.datetime) -> int:
    """Convert a datetime to a Unix timestamp (int) as expected by BaseLinker."""
    return int(dt.replace(tzinfo=datetime.timezone.utc).timestamp())


def _days_ago(n: int) -> int:
    return _ts(datetime.datetime.utcnow() - datetime.timedelta(days=n))


# ── Time ──────────────────────────────────────────────────────────────────────

@mcp.tool()
def get_current_datetime() -> Dict:
    """Returns current UTC date/time. Call this first so you know correct dates to pass to other tools."""
    now = datetime.datetime.now(datetime.timezone.utc)
    return {
        "utc_now": now.isoformat(),
        "unix_timestamp": int(now.timestamp()),
        "date": now.strftime("%Y-%m-%d"),
        "yesterday_timestamp": _days_ago(1),
        "last_7_days_timestamp": _days_ago(7),
        "last_30_days_timestamp": _days_ago(30),
    }


# ── Orders ────────────────────────────────────────────────────────────────────

@mcp.tool()
async def get_orders(
    days_back: int = 7,
    status_id: int = None,
    order_source: str = None,
    only_unpaid: bool = False,
) -> List[Dict]:
    """Get orders from the last N days. Returns key fields for each order.

    Args:
        days_back: How many days back to fetch (default 7, max practical ~30).
        status_id: Filter by a specific order status ID. Use get_order_statuses to find IDs.
        order_source: Filter by source, e.g. 'shop', 'allegro', 'amazon'. Leave empty for all.
        only_unpaid: If True, return only unpaid orders.
    """
    date_from = _days_ago(days_back)
    result = await asyncio.to_thread(
        bl.get_orders,
        date_confirmed_from=date_from,
        order_status_id=status_id,
        filter_order_source=order_source,
        only_not_paid=only_unpaid,
    )
    orders = result.get("orders", [])
    # BaseLinker returns products as a list of dicts
    def _products(o):
        raw = o.get("products") or []
        items = raw.values() if isinstance(raw, dict) else raw
        return [
            {
                "name": p.get("name"),
                "sku": p.get("sku"),
                "quantity": p.get("quantity"),
                "price_brutto": p.get("price_brutto"),
            }
            for p in items
        ]

    return [
        {
            "order_id": o.get("order_id"),
            "date_add": o.get("date_add"),
            "date_confirmed": o.get("date_confirmed"),
            "status_id": o.get("order_status_id"),
            "source": f"{o.get('order_source', '')} / {o.get('order_source_info', '')}".strip(" /"),
            "buyer_login": o.get("login"),
            "buyer_email": o.get("email"),
            "buyer_name": o.get("delivery_fullname") or o.get("invoice_fullname", ""),
            "total": o.get("payment_done"),
            "currency": o.get("currency"),
            "payment_method": o.get("payment_method"),
            "delivery_method": o.get("delivery_method"),
            "tracking_number": o.get("delivery_package_nr"),
            "want_invoice": o.get("want_invoice"),
            "products": _products(o),
            "note": o.get("admin_comments"),
        }
        for o in orders
    ]


@mcp.tool()
async def get_order_details(order_id: int) -> Dict:
    """Get full details of a single order including all products, address, payments, and notes.

    Args:
        order_id: The numeric BaseLinker order ID.
    """
    result = await asyncio.to_thread(bl.call, "getOrders", {"order_id": order_id})
    orders = result.get("orders", [])
    if not orders:
        return {"error": f"Order {order_id} not found"}
    return orders[0]


@mcp.tool()
async def get_orders_details_batch(order_ids: List[int]) -> List[Dict]:
    """Get full details for multiple orders in one shot — all fetched in parallel.

    Use this instead of calling get_order_details repeatedly. Ideal after a search
    returns a list of order IDs and you need the full data for all of them.

    Args:
        order_ids: List of numeric BaseLinker order IDs (max 20 at a time).
    """
    async def _fetch_one(oid: int) -> Dict:
        try:
            result = await asyncio.to_thread(bl.call, "getOrders", {"order_id": oid})
            orders = result.get("orders", [])
            return orders[0] if orders else {"order_id": oid, "error": "not found"}
        except Exception as e:
            return {"order_id": oid, "error": str(e)}

    return await asyncio.gather(*[_fetch_one(oid) for oid in order_ids[:20]])


@mcp.tool()
async def search_orders_by_email(email: str) -> List[Dict]:
    """Find all orders from a specific customer email address.

    Args:
        email: Customer email address to search for.
    """
    result = await asyncio.to_thread(bl.get_orders_by_email, email)
    orders = result.get("orders", [])
    return [
        {
            "order_id": o.get("order_id"),
            "date_add": o.get("date_add"),
            "status_id": o.get("order_status_id"),
            "total": o.get("payment_done"),
            "currency": o.get("currency"),
            "delivery_method": o.get("delivery_method"),
            "tracking_number": o.get("delivery_package_nr"),
        }
        for o in orders
    ]


@mcp.tool()
async def get_order_statuses() -> List[Dict]:
    """Get all order status labels and their IDs. Use these IDs in other tools."""
    result = await asyncio.to_thread(bl.get_order_status_list)
    return result.get("statuses", [])


@mcp.tool()
async def set_order_status(order_id: int, status_id: int) -> Dict:
    """Change the status of an order.

    Args:
        order_id: The numeric BaseLinker order ID.
        status_id: Target status ID. Use get_order_statuses to find available IDs.
    """
    return await asyncio.to_thread(bl.set_order_status, order_id, status_id)


@mcp.tool()
async def get_order_shipments(order_id: int) -> List[Dict]:
    """Get all shipments/packages created for a specific order, including tracking numbers.

    Args:
        order_id: The numeric BaseLinker order ID.
    """
    result = await asyncio.to_thread(bl.get_order_packages, order_id)
    return result.get("packages", [])


@mcp.tool()
async def get_recent_activity(last_log_id: int = 1) -> List[Dict]:
    """Get a stream of recent order events (new orders, status changes, etc.) from the last 3 days.

    Args:
        last_log_id: Return events with log_id greater than this value (minimum 1).
                     Use 1 to get all available events. Store the last returned log_id
                     to poll for new events incrementally.
    """
    result = await asyncio.to_thread(bl.get_journal_list, max(1, last_log_id))
    return result.get("logs", [])


@mcp.tool()
async def update_order_note(order_id: int, note: str) -> Dict:
    """Add or update the admin note on an order.

    Args:
        order_id: The numeric BaseLinker order ID.
        note: The note text to set.
    """
    return await asyncio.to_thread(bl.set_order_fields, order_id, admin_comments=note)


# ── Inventory / Products ──────────────────────────────────────────────────────

@mcp.tool()
async def get_inventories() -> List[Dict]:
    """Get all product catalogs (inventories) in your BaseLinker account.
    Each catalog has an inventory_id needed by other product tools.
    """
    result = await asyncio.to_thread(bl.get_inventories)
    return result.get("inventories", [])


@mcp.tool()
async def get_products(
    inventory_id: str,
    name_filter: str = None,
    sku_filter: str = None,
    ean_filter: str = None,
    page: int = 1,
) -> List[Dict]:
    """List products from a catalog with optional filtering.

    Args:
        inventory_id: The catalog/inventory ID. Use get_inventories to find it.
        name_filter: Optional partial name to filter by.
        sku_filter: Optional exact SKU to filter by.
        ean_filter: Optional exact EAN/barcode to filter by.
        page: Page number for pagination (100 products per page).
    """
    result = await asyncio.to_thread(
        bl.get_inventory_products_list,
        inventory_id,
        filter_name=name_filter,
        filter_sku=sku_filter,
        filter_ean=ean_filter,
        page=page,
    )
    products = result.get("products", {})
    return [
        {
            "product_id": pid,
            "name": p.get("name"),
            "sku": p.get("sku"),
            "ean": p.get("ean"),
            "price_brutto": p.get("price_brutto"),
            "category_id": p.get("category_id"),
            "is_bundle": p.get("is_bundle"),
        }
        for pid, p in products.items()
    ]


@mcp.tool()
async def get_product_details(inventory_id: str, product_ids: List[str]) -> Dict:
    """Get full product data (description, images, prices, attributes) for specific products.

    Args:
        inventory_id: The catalog/inventory ID.
        product_ids: List of product IDs to fetch (max 25 at a time).
    """
    result = await asyncio.to_thread(
        bl.get_inventory_products_data, inventory_id, product_ids[:25]
    )
    return result.get("products", {})


@mcp.tool()
async def get_stock_levels(inventory_id: str, page: int = 1) -> Dict:
    """Get stock levels for all products in a catalog.

    Args:
        inventory_id: The catalog/inventory ID.
        page: Page number (1000 products per page).
    """
    result = await asyncio.to_thread(bl.get_inventory_products_stock, inventory_id, page)
    return result.get("products", {})


@mcp.tool()
async def update_stock(
    inventory_id: str,
    product_id: str,
    warehouse_id: str,
    quantity: int,
) -> Dict:
    """Update the stock quantity of a single product in a specific warehouse.

    Args:
        inventory_id: The catalog/inventory ID.
        product_id: The product ID to update.
        warehouse_id: Warehouse ID (e.g. 'bl_1' for BaseLinker default warehouse).
        quantity: New stock quantity.
    """
    products = {product_id: {warehouse_id: quantity}}
    return await asyncio.to_thread(
        bl.update_inventory_products_stock, inventory_id, products
    )


@mcp.tool()
async def get_prices(inventory_id: str, page: int = 1) -> Dict:
    """Get prices for all products in a catalog.

    Args:
        inventory_id: The catalog/inventory ID.
        page: Page number (1000 products per page).
    """
    result = await asyncio.to_thread(bl.get_inventory_products_prices, inventory_id, page)
    return result.get("products", {})


# ── CRM / Customers ───────────────────────────────────────────────────────────

@mcp.tool()
async def search_customers(
    name: str = None,
    email: str = None,
    phone: str = None,
    page: int = 1,
) -> List[Dict]:
    """Search for CRM customers by name, email, or phone number.

    Args:
        name: Partial name to search for.
        email: Email address to search for.
        phone: Phone number to search for.
        page: Page number for pagination.
    """
    result = await asyncio.to_thread(
        bl.get_crm_clients,
        filter_name=name,
        filter_email=email,
        filter_phone=phone,
        page=page,
    )
    return result.get("clients", [])


@mcp.tool()
async def get_customer_details(crm_client_id: int) -> Dict:
    """Get full CRM profile of a customer including notes and order history.

    Args:
        crm_client_id: The CRM client ID from search_customers.
    """
    result = await asyncio.to_thread(bl.get_crm_client_data, crm_client_id)
    return result.get("client", result)


# ── Couriers ──────────────────────────────────────────────────────────────────

@mcp.tool()
async def get_couriers() -> List[Dict]:
    """List all couriers/shipping providers available in your BaseLinker account."""
    result = await asyncio.to_thread(bl.get_couriers_list)
    return result.get("couriers", [])


# ── Server entry point ────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    import uvicorn
    from starlette.applications import Starlette
    from starlette.middleware import Middleware
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import Response
    from starlette.routing import Route
    from mcp.server.sse import SseServerTransport

    parser = argparse.ArgumentParser()
    parser.add_argument("--serve", action="store_true", help="Run as HTTP/SSE server")
    args = parser.parse_args()

    if args.serve:
        from baselinker import _request_token

        def _extract_token(request) -> str:
            """Pull the BaseLinker token from whichever auth header the client sends."""
            auth = request.headers.get("Authorization", "")
            if auth.lower().startswith("bearer "):
                return auth[7:].strip()
            # MCP clients using "API Key" auth send a custom header — support both common names
            x_key = request.headers.get("X-API-Key", "")
            if x_key:
                return x_key
            x_bl = request.headers.get("X-BLToken", "")
            if x_bl:
                return x_bl
            return os.environ.get("BASELINKER_TOKEN", "")

        class AuthMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request, call_next):
                token = _extract_token(request)
                if not token:
                    return Response(
                        "Unauthorized — provide your BaseLinker API token as the Bearer token "
                        "or X-API-Key when connecting.",
                        status_code=401,
                    )
                return await call_next(request)

        sse = SseServerTransport("/messages")

        async def handle_sse(request):
            # Inject the per-connection BaseLinker token into the contextvar
            token = _extract_token(request)
            token_ctx = _request_token.set(token)
            try:
                async with sse.connect_sse(
                    request.scope, request.receive, request._send
                ) as streams:
                    await mcp._mcp_server.run(
                        streams[0], streams[1],
                        mcp._mcp_server.create_initialization_options(),
                    )
            finally:
                _request_token.reset(token_ctx)

        async def handle_messages(request):
            await sse.handle_post_message(request.scope, request.receive, request._send)

        app = Starlette(
            routes=[
                Route("/sse", endpoint=handle_sse),
                Route("/messages", endpoint=handle_messages, methods=["POST"]),
            ],
            middleware=[Middleware(AuthMiddleware)],
        )

        print(f"\nBaseLinker MCP server starting")
        print(f"  SSE endpoint : http://0.0.0.0:{_port}/sse")
        print(f"  Auth         : pass your BaseLinker token as Bearer token or X-API-Key")
        print("\nPress CTRL+C to stop\n")

        async def run():
            config = uvicorn.Config(app, host="0.0.0.0", port=_port)
            server = uvicorn.Server(config)
            await server.serve()

        asyncio.run(run())
    else:
        mcp.run()

"""BaseLinker API client.

All requests go to https://api.baselinker.com/connector.php as POST with:
  Header  : X-BLToken: <token>
  Body    : method=<METHOD>&parameters=<JSON-encoded-params>

Docs: https://api.baselinker.com/
"""
import os
import json
import httpx
from contextvars import ContextVar
from typing import Any, Dict, Optional

BASE_URL = "https://api.baselinker.com/connector.php"

# Set per-SSE-connection by server.py — holds the token from the MCP auth header
_request_token: ContextVar[Optional[str]] = ContextVar("bl_request_token", default=None)


def _get_token() -> str:
    # Per-request token injected from the MCP connection header takes priority
    token = _request_token.get()
    if not token:
        token = os.environ.get("BASELINKER_TOKEN", "")
    if not token:
        raise RuntimeError(
            "No BaseLinker token found. Pass your token as the API Key "
            "when connecting to this MCP server."
        )
    return token


def call(method: str, parameters: Optional[Dict[str, Any]] = None) -> Dict:
    """Make a synchronous call to the BaseLinker connector API."""
    token = _get_token()
    data: Dict[str, str] = {"method": method}
    if parameters:
        data["parameters"] = json.dumps(parameters)

    response = httpx.post(
        BASE_URL,
        headers={"X-BLToken": token},
        data=data,
        timeout=30,
    )
    response.raise_for_status()
    result = response.json()

    if result.get("status") != "SUCCESS":
        error_msg = result.get("error_message", result.get("status", "Unknown error"))
        error_code = result.get("error_code", "")
        raise RuntimeError(f"BaseLinker API error [{error_code}]: {error_msg}")

    return result


# ── Orders ────────────────────────────────────────────────────────────────────

def get_orders(
    date_from: Optional[int] = None,
    date_confirmed_from: Optional[int] = None,
    order_status_id: Optional[int] = None,
    filter_order_source: Optional[str] = None,
    get_unconfirmed_orders: bool = False,
    only_not_paid: bool = False,
) -> Dict:
    """Download orders (max 100 per call). Use date_confirmed_from for incremental sync."""
    params: Dict[str, Any] = {
        "get_unconfirmed_orders": get_unconfirmed_orders,
    }
    if date_from is not None:
        params["date_from"] = date_from
    if date_confirmed_from is not None:
        params["date_confirmed_from"] = date_confirmed_from
    if order_status_id is not None:
        params["status_id"] = order_status_id
    if filter_order_source:
        params["filter_order_source"] = filter_order_source
    if only_not_paid:
        params["only_not_paid"] = True
    return call("getOrders", params)


def get_order_status_list() -> Dict:
    return call("getOrderStatusList")


def set_order_status(order_id: int, status_id: int) -> Dict:
    return call("setOrderStatus", {"order_id": order_id, "status_id": status_id})


def get_order_sources() -> Dict:
    return call("getOrderSources")


def get_journal_list(last_log_id: int = 0, logs_types: Optional[list] = None, order_id: Optional[int] = None) -> Dict:
    """Returns order events from the last 3 days."""
    params: Dict[str, Any] = {"last_log_id": last_log_id}
    if logs_types:
        params["logs_types"] = logs_types
    if order_id:
        params["order_id"] = order_id
    return call("getJournalList", params)


def get_order_packages(order_id: int) -> Dict:
    return call("getOrderPackages", {"order_id": order_id})


def get_package_status_history(package_ids: list) -> Dict:
    return call("getCourierPackagesStatusHistory", {"package_ids": package_ids})


def get_order_invoices(order_id: int) -> Dict:
    return call("getInvoices", {"order_id": order_id})


def set_order_fields(order_id: int, **fields) -> Dict:
    params = {"order_id": order_id, **fields}
    return call("setOrderFields", params)


def get_orders_by_email(email: str) -> Dict:
    return call("getOrdersByEmail", {"email": email})


# ── Inventory / Products ──────────────────────────────────────────────────────

def get_inventories() -> Dict:
    return call("getInventories")


def get_inventory_categories(inventory_id: str) -> Dict:
    return call("getInventoryCategories", {"inventory_id": inventory_id})


def get_inventory_products_list(
    inventory_id: str,
    filter_category_id: Optional[str] = None,
    filter_name: Optional[str] = None,
    filter_ean: Optional[str] = None,
    filter_sku: Optional[str] = None,
    page: int = 1,
) -> Dict:
    params: Dict[str, Any] = {"inventory_id": inventory_id, "page": page}
    if filter_category_id:
        params["filter_category_id"] = filter_category_id
    if filter_name:
        params["filter_name"] = filter_name
    if filter_ean:
        params["filter_ean"] = filter_ean
    if filter_sku:
        params["filter_sku"] = filter_sku
    return call("getInventoryProductsList", params)


def get_inventory_products_data(inventory_id: str, product_ids: list) -> Dict:
    return call("getInventoryProductsData", {
        "inventory_id": inventory_id,
        "products": {str(pid): None for pid in product_ids},
    })


def get_inventory_products_stock(inventory_id: str, page: int = 1) -> Dict:
    return call("getInventoryProductsStock", {"inventory_id": inventory_id, "page": page})


def update_inventory_products_stock(inventory_id: str, products: Dict[str, Dict]) -> Dict:
    """products: {product_id: {warehouse_id: qty, ...}, ...}"""
    return call("updateInventoryProductsStock", {
        "inventory_id": inventory_id,
        "products": products,
    })


def get_inventory_products_prices(inventory_id: str, page: int = 1) -> Dict:
    return call("getInventoryProductsPrices", {"inventory_id": inventory_id, "page": page})


# ── Couriers ──────────────────────────────────────────────────────────────────

def get_couriers_list() -> Dict:
    return call("getCouriersList")


def get_package_details(courier_code: str, package_number: str) -> Dict:
    return call("getPackageDetails", {
        "courier_code": courier_code,
        "package_number": package_number,
    })


# ── CRM ───────────────────────────────────────────────────────────────────────

def get_crm_clients(
    filter_name: Optional[str] = None,
    filter_email: Optional[str] = None,
    filter_phone: Optional[str] = None,
    page: int = 1,
) -> Dict:
    params: Dict[str, Any] = {"page": page}
    if filter_name:
        params["filter_name"] = filter_name
    if filter_email:
        params["filter_email"] = filter_email
    if filter_phone:
        params["filter_phone"] = filter_phone
    return call("getCrmClients", params)


def get_crm_client_data(crm_client_id: int) -> Dict:
    return call("getCrmClientData", {"crm_client_id": crm_client_id})
